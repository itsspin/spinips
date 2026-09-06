"""End-to-end parser/model seams for Loremaster's mez timer overlay."""

import importlib.util
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path


LOREMASTER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LOREMASTER_DIR))
SPEC = importlib.util.spec_from_file_location(
    "loremaster_mez_integration_app", LOREMASTER_DIR / "loremaster.py")
LOREMASTER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = LOREMASTER
SPEC.loader.exec_module(LOREMASTER)

BASE = datetime(2026, 8, 3, 12, 0, 0)


def parsed(message, offset=0):
    stamp = (BASE + timedelta(seconds=offset)).strftime(LOREMASTER.TS_FORMAT)
    event = LOREMASTER.parse_line(f"[{stamp}] {message}")
    if event is None:
        raise AssertionError(f"line did not parse: {message}")
    return event


class MezLogGrammarTests(unittest.TestCase):
    def test_real_eql_cast_interrupt_resist_and_fade_forms(self):
        cases = (
            ("You begin singing Kelin's Lucid Lullaby.", "song_begin"),
            ("Hingle begins casting Mesmerization V.", "cast_begin_other"),
            ("Ihopeyoumiss begins singing Kelin's Lucid Lullaby.",
             "song_begin_other"),
            ("Your Mesmerize spell is interrupted.", "interrupt"),
            ("Your spell is interrupted.", "interrupt"),
            ("A gnoll bouncer resisted your Mesmerization V!", "resist2"),
            ("Your target cannot be mesmerized.", "mez_immune"),
            ("Your Mesmerization spell on a soul carrier has been overwritten.",
             "spell_overwritten"),
            ("orc legionnaire has been mesmerized.",
             "mez_landed_mesmerized"),
            ("an elemental crusader has been entranced.",
             "mez_landed_entranced"),
            ("an abhorrent swoons in raptured bliss.",
             "mez_landed_rapture"),
            ("orc legionnaire has been awakened by Spin.", "mez_awakened"),
            ("Your Mesmerization spell has worn off of a thought spoiler.",
             "spell_fade"),
        )
        for message, expected_kind in cases:
            with self.subTest(message=message):
                self.assertEqual(parsed(message)[1], expected_kind)

    def test_named_interrupt_preserves_the_spell_name(self):
        _ts, _kind, groups = parsed("Your Mesmerize spell is interrupted.")
        self.assertEqual(groups["spell"], "Mesmerize")


class MezParserModelTests(unittest.TestCase):
    def setUp(self):
        self.stats = LOREMASTER.SessionStats("Spin")
        self.tracker = LOREMASTER.MezTracker()

    def apply(self, message, offset=0):
        ts, kind, groups = parsed(message, offset)
        LOREMASTER.apply_log_models(
            self.stats, self.tracker, ts, kind, groups)
        return ts, kind, groups

    def test_ranked_ae_resists_do_not_close_the_success_batch(self):
        self.apply("You begin casting Mesmerization V.")
        self.apply("A gnoll bouncer resisted your Mesmerization V!", 1)
        self.apply("a thought spoiler has been mesmerized.", 2)
        self.apply("a thought spoiler has been mesmerized.", 2)
        snap = self.tracker.snapshot(BASE + timedelta(seconds=3))
        self.assertEqual(snap.active_count, 2)
        self.assertEqual(snap.rows[0].count, 2)
        self.assertEqual(snap.rows[0].rank, 5)
        self.assertEqual(snap.rows[0].safe_remaining_seconds, 35)

    def test_overlapping_groupmate_ae_batch_is_not_counted_as_ours(self):
        # Replays the ordering observed in the current EQL log: Hingle starts
        # first, their actorless result batch lands after our cast begins, then
        # an explicit local resist precedes our own successful result batch.
        self.apply("Hingle begins casting Mesmerization V.")
        self.apply("You begin casting Mesmerization V.", 1)
        self.apply("an essence carrier has been mesmerized.", 1)
        self.apply("an essence carrier has been mesmerized.", 1)
        self.apply("a soul carrier has been mesmerized.", 1)
        self.assertEqual(
            self.tracker.snapshot(BASE + timedelta(seconds=1)).active_count, 0)

        self.apply("Overseer of Air resisted your Mesmerization V!", 2)
        self.apply("an essence carrier has been mesmerized.", 2)
        self.apply("an essence carrier has been mesmerized.", 2)
        self.apply("a soul carrier has been mesmerized.", 2)
        snapshot = self.tracker.snapshot(BASE + timedelta(seconds=2))
        self.assertEqual(snapshot.active_count, 3)
        self.assertEqual(
            {row.target_name: row.count for row in snapshot.rows},
            {"an essence carrier": 2, "a soul carrier": 1},
        )

    def test_nearby_batch_before_our_cast_does_not_shadow_our_landing(self):
        self.apply("Hingle begins casting Mesmerization V.")
        self.apply("an essence carrier has been mesmerized.", 1)
        self.apply("You begin casting Mesmerization V.", 2)
        self.apply("a soul carrier has been mesmerized.", 3)
        self.assertEqual(
            self.tracker.snapshot(BASE + timedelta(seconds=3)).active_count, 1)

    def test_shared_landing_family_is_quarantined_across_spell_names(self):
        self.apply("Hingle begins casting Dazzle.")
        self.apply("You begin casting Mesmerize.", 1)
        self.apply("a thought spoiler has been mesmerized.", 1)
        self.assertFalse(
            self.tracker.snapshot(BASE + timedelta(seconds=1)).rows)
        self.apply("a thought spoiler has been mesmerized.", 2)
        self.assertEqual(
            self.tracker.snapshot(BASE + timedelta(seconds=2)).active_count, 1)

    def test_later_nearby_same_family_closes_ambiguous_local_pending(self):
        self.apply("You begin casting Mesmerization V.")
        self.apply("Hingle begins casting Dazzle.", 1)
        self.apply("a thought spoiler has been mesmerized.", 2)
        self.assertFalse(
            self.tracker.snapshot(BASE + timedelta(seconds=2)).rows)

    def test_later_nearby_cast_cannot_extend_an_accepted_local_ae_batch(self):
        self.apply("You begin casting Mesmerization V.")
        self.apply("first target has been mesmerized.", 1)
        self.apply("Hingle begins casting Dazzle.", 1)
        self.apply("nearby target has been mesmerized.", 2)
        snapshot = self.tracker.snapshot(BASE + timedelta(seconds=2))
        self.assertEqual(snapshot.active_count, 1)
        self.assertEqual(snapshot.rows[0].target_name, "first target")

    def test_groupmate_overwrite_retires_our_unknown_duration(self):
        self.apply("You begin casting Mesmerization V.")
        self.apply("an essence carrier has been mesmerized.", 1)
        self.apply("Hingle begins casting Mesmerization V.", 2)
        self.apply(
            "Your Mesmerization spell on an essence carrier has been overwritten.",
            3,
        )
        self.assertFalse(
            self.tracker.snapshot(BASE + timedelta(seconds=3)).rows)

    def test_real_wake_chain_removes_only_one_same_named_instance(self):
        self.apply("You begin casting Mesmerization.")
        for _ in range(3):
            self.apply("Bonefire has been mesmerized.", 1)
        self.apply("Your Mesmerization spell has worn off of Bonefire.", 2)
        self.apply("Bonefire has been awakened by Ihopeyoumiss.", 2)
        self.apply("Ihopeyoumiss slashes Bonefire for 38 points of damage.", 2)
        self.assertEqual(
            self.tracker.snapshot(BASE + timedelta(seconds=2)).active_count, 2)

    def test_zone_like_environment_message_does_not_clear_timers(self):
        self.apply("You begin casting Mesmerize.")
        self.apply("a thought spoiler has been mesmerized.", 1)
        self.apply(
            "You have entered an area where levitation effects do not function.",
            2,
        )
        self.assertEqual(
            self.tracker.snapshot(BASE + timedelta(seconds=2)).active_count, 1)

    def test_cannot_be_mesmerized_cancels_single_target_pending(self):
        self.apply("You begin casting Mesmerize.")
        self.apply("Your target cannot be mesmerized.", 1)
        self.apply("a groupmate target has been mesmerized.", 2)
        self.assertFalse(
            self.tracker.snapshot(BASE + timedelta(seconds=2)).rows)

    def test_a_landing_phrase_must_match_the_pending_spell_family(self):
        self.apply("You begin casting Entrance.")
        self.apply("a thought spoiler has been mesmerized.", 1)
        self.assertFalse(self.tracker.snapshot(BASE + timedelta(seconds=1)).rows)
        self.apply("a thought spoiler has been entranced.", 2)
        self.assertEqual(
            self.tracker.snapshot(BASE + timedelta(seconds=3)).active_count, 1)

    def test_groupmate_landing_without_our_pending_cast_is_ignored(self):
        self.apply("a thought spoiler has been mesmerized.")
        self.assertFalse(self.tracker.snapshot(BASE).rows)

    def test_named_interrupt_prevents_a_false_timer(self):
        self.apply("You begin casting Mesmerize.")
        self.apply("Your Mesmerize spell is interrupted.", 1)
        self.apply("a thought spoiler has been mesmerized.", 2)
        self.assertFalse(self.tracker.snapshot(BASE + timedelta(seconds=2)).rows)

    def test_ranked_cast_matches_an_unranked_fade(self):
        self.apply("You begin casting Mesmerization V.")
        self.apply("a thought spoiler has been mesmerized.", 1)
        self.apply(
            "Your Mesmerization spell has worn off of a thought spoiler.", 20)
        self.assertFalse(self.tracker.snapshot(BASE + timedelta(seconds=20)).rows)

    def test_targetless_fade_retires_a_matching_timer(self):
        self.apply("You begin casting Mesmerize.")
        self.apply("a thought spoiler has been mesmerized.", 1)
        self.apply("Your Mesmerize spell has worn off.", 2)
        self.assertFalse(self.tracker.snapshot(BASE + timedelta(seconds=2)).rows)

    def test_rapture_uses_the_current_client_landing_text(self):
        self.apply("You begin casting Rapture.")
        self.apply("an abhorrent swoons in raptured bliss.", 2)
        snapshot = self.tracker.snapshot(BASE + timedelta(seconds=3))
        self.assertEqual(snapshot.active_count, 1)
        self.assertEqual(snapshot.rows[0].spell_name, "Rapture")

    def test_late_compatible_bystander_landing_is_not_attributed_to_us(self):
        self.apply("You begin casting Mesmerize.")
        self.apply("a thought spoiler has been mesmerized.", 5)
        self.assertFalse(self.tracker.snapshot(BASE + timedelta(seconds=5)).rows)

    def test_damage_break_removes_the_timer_immediately(self):
        self.apply("You begin casting Mesmerize.")
        self.apply("a thought spoiler has been mesmerized.", 1)
        self.apply("You slash a thought spoiler for 12 points of damage.", 2)
        self.assertFalse(self.tracker.snapshot(BASE + timedelta(seconds=2)).rows)

    def test_engaging_a_different_target_preserves_the_confirmed_mez(self):
        self.apply("You begin casting Dazzle.")
        self.apply("a thought spoiler has been mesmerized.", 1)
        original = self.tracker.snapshot(BASE + timedelta(seconds=1)).rows[0]
        unrelated_events = (
            "You begin casting Flame of Light.",
            "You hit a froglok for 15 points of magic damage by Flame of Light.",
            "Auto attack is on.",
            "You slash a froglok for 20 points of damage.",
            "a froglok hits YOU for 3 points of damage.",
            "a froglok tries to slash YOU, but misses!",
            "Hingle slashes a froglok for 9 points of damage.",
            "You have slain a froglok!",
            "Your Dazzle spell has worn off of a froglok.",
            "Your Dazzle spell on a froglok has been overwritten.",
        )
        for offset, message in enumerate(unrelated_events, 2):
            with self.subTest(message=message):
                self.apply(message, offset)
                snapshot = self.tracker.snapshot(BASE + timedelta(seconds=offset))
                self.assertEqual(snapshot.active_count, 1)
                row = snapshot.rows[0]
                self.assertEqual(row.target_name, "a thought spoiler")
                self.assertEqual(row.landed_at, original.landed_at)
                self.assertEqual(row.safe_expires_at, original.safe_expires_at)
                self.assertEqual(row.expires_at, original.expires_at)

        self.apply("You slash a thought spoiler for 1 point of damage.", 12)
        self.assertFalse(self.tracker.snapshot(BASE + timedelta(seconds=12)).rows)

    def test_tracked_attacker_acting_retires_the_timer_even_on_a_miss(self):
        self.apply("You begin casting Mesmerize.")
        self.apply("a thought spoiler has been mesmerized.", 1)
        self.apply("a thought spoiler tries to slash YOU, but misses!", 2)
        self.assertFalse(self.tracker.snapshot(BASE + timedelta(seconds=2)).rows)

    def test_timer_only_lines_do_not_change_dps_or_session_truth(self):
        baseline = LOREMASTER.SessionStats("Spin")
        with_timer = LOREMASTER.SessionStats("Spin")
        tracker_a = LOREMASTER.MezTracker()
        tracker_b = LOREMASTER.MezTracker()
        common = (
            parsed("You begin casting Mesmerize."),
            parsed("You slash a froglok for 100 points of damage.", 2),
        )
        for ts, kind, groups in common:
            LOREMASTER.apply_log_models(baseline, tracker_a, ts, kind, groups)
            LOREMASTER.apply_log_models(with_timer, tracker_b, ts, kind, groups)
        ts, kind, groups = parsed("a thought spoiler has been mesmerized.", 1)
        LOREMASTER.apply_log_models(with_timer, tracker_b, ts, kind, groups)
        for name in ("casts", "log_lines", "session_start"):
            self.assertEqual(getattr(baseline, name), getattr(with_timer, name))
        baseline_snap = baseline.snapshot(BASE + timedelta(seconds=3))
        timer_snap = with_timer.snapshot(BASE + timedelta(seconds=3))
        for name in ("combat_damage", "session_dps"):
            self.assertEqual(baseline_snap[name], timer_snap[name])


class MezOverlayHelperTests(unittest.TestCase):
    def test_overlay_flips_to_the_left_at_the_desktop_edge(self):
        self.assertEqual(
            LOREMASTER.mez_overlay_position(
                (940, 100, 58, 46), (300, 120), (0, 0, 1000, 800)),
            (630, 100),
        )

    def test_overlay_moves_below_a_seed_adjacent_alert(self):
        self.assertEqual(
            LOREMASTER.mez_overlay_position(
                (100, 100, 58, 46), (300, 120), (0, 0, 1000, 800),
                occupied_rects=((168, 100, 250, 50),)),
            (168, 158),
        )

    def test_overlay_moves_above_an_alert_near_the_bottom_edge(self):
        self.assertEqual(
            LOREMASTER.mez_overlay_position(
                (100, 690, 58, 46), (300, 120), (0, 0, 1000, 800),
                occupied_rects=((168, 680, 250, 60),)),
            (168, 552),
        )

    def test_spell_label_uses_rank_roman_numerals(self):
        self.assertEqual(
            LOREMASTER.mez_spell_label("Mesmerization", 5),
            "Mesmerization V",
        )

    def test_meter_edge_is_clamped_and_decreases_smoothly(self):
        edges = [LOREMASTER.mez_meter_edge(300, remaining, 60)
                 for remaining in (90, 60, 45, 30, 15, 0)]
        self.assertEqual(edges, [300, 300, 225, 150, 75, 0])
        self.assertTrue(all(first >= second
                            for first, second in zip(edges, edges[1:])))
        self.assertEqual(LOREMASTER.mez_meter_edge(300, 30, 60, True), 0)

    def test_mez_motion_is_bounded_and_reduced_motion_is_settled(self):
        self.assertEqual(LOREMASTER.mez_motion_mix(
            10, 9.9, 9.9, "critical", reduced_motion=True), (0.0, 0.0))
        landing = LOREMASTER.mez_motion_mix(10, 9.9, 0, "safe")
        settled = LOREMASTER.mez_motion_mix(10, 8, 8, "warning")
        critical = LOREMASTER.mez_motion_mix(10, 8, 8, "critical")
        self.assertGreater(landing[1], settled[1])
        self.assertEqual(settled, (0.0, 0.0))
        self.assertGreaterEqual(critical[0], 0.0)
        self.assertLessEqual(critical[0], 0.72)

    def test_native_timer_show_uses_atomic_rectangle_not_showwindow(self):
        source = (LOREMASTER_DIR / "loremaster.py").read_text(encoding="utf-8")
        class_source = source[source.index("class MezTimerOverlay"):
                              source.index("# Tk overlay")]
        self.assertIn("native_window_position_plan", class_source)
        self.assertIn("_show_nonactivating(self, rect)", class_source)
        self.assertNotIn("ShowWindow", class_source)


if __name__ == "__main__":
    unittest.main()
