"""End-to-end log grammar and model seams for lull tracking."""

import importlib.util
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path


LOREMASTER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LOREMASTER_DIR))
SPEC = importlib.util.spec_from_file_location(
    "loremaster_lull_integration_app", LOREMASTER_DIR / "loremaster.py")
LOREMASTER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = LOREMASTER
SPEC.loader.exec_module(LOREMASTER)

BASE = datetime(2026, 8, 6, 19, 0, 0)


def parsed(message, offset=0):
    stamp = (BASE + timedelta(seconds=offset)).strftime(LOREMASTER.TS_FORMAT)
    event = LOREMASTER.parse_line(f"[{stamp}] {message}")
    if event is None:
        raise AssertionError(f"line did not parse: {message}")
    return event


class LullLogGrammarTests(unittest.TestCase):
    def test_real_eql_landing_form(self):
        _ts, kind, groups = parsed("a soul carrier looks less aggressive.")
        self.assertEqual(kind, "lull_landed")
        self.assertEqual(groups["target"], "a soul carrier")


class LullParserModelTests(unittest.TestCase):
    def setUp(self):
        self.stats = LOREMASTER.SessionStats("Spin")
        self.stats.level = 50
        self.mez = LOREMASTER.MezTracker()
        self.lull = LOREMASTER.LullTracker()

    def apply(self, message, offset=0):
        ts, kind, groups = parsed(message, offset)
        LOREMASTER.apply_log_models(
            self.stats, self.mez, ts, kind, groups,
            lull_tracker=self.lull, caster_level=self.stats.level)
        return ts, kind, groups

    def test_confirmed_local_lull_starts_timer(self):
        self.apply("You begin casting Calm.")
        self.apply("a soul carrier looks less aggressive.", 2)
        snapshot = self.lull.snapshot(BASE + timedelta(seconds=3))
        self.assertEqual(snapshot.active_count, 1)
        self.assertEqual(snapshot.rows[0].target_name, "a soul carrier")

    def test_cast_without_result_and_resist_never_start_timer(self):
        self.apply("You begin casting Calm.")
        self.assertEqual(self.lull.snapshot(BASE).active_count, 0)
        self.apply("a soul carrier resisted your Calm!", 2)
        snapshot = self.lull.snapshot(BASE + timedelta(seconds=2))
        self.assertEqual(snapshot.active_count, 0)
        self.assertEqual(snapshot.notices[-1].status, "failed")

    def test_nearby_lull_ownership_is_not_guessed(self):
        self.apply("Hingle begins casting Calm.")
        self.apply("You begin casting Calm.", 1)
        self.apply("a soul carrier looks less aggressive.", 2)
        snapshot = self.lull.snapshot(BASE + timedelta(seconds=2))
        self.assertEqual(snapshot.active_count, 0)
        self.assertTrue(any(notice.status == "ambiguous"
                            for notice in snapshot.notices))

    def test_damage_death_zone_and_character_reset_paths_clear(self):
        self.apply("You begin casting Calm.")
        self.apply("a soul carrier looks less aggressive.", 2)
        self.apply("You slash a soul carrier for 1 point of damage.", 3)
        self.assertEqual(self.lull.snapshot(
            BASE + timedelta(seconds=3)).active_count, 0)

        self.apply("You begin casting Calm.", 4)
        self.apply("a soul carrier looks less aggressive.", 6)
        self.apply("You have been slain by a soul carrier!", 7)
        self.assertEqual(self.lull.snapshot(
            BASE + timedelta(seconds=7)).active_count, 0)

        self.apply("You begin casting Calm.", 8)
        self.apply("a soul carrier looks less aggressive.", 10)
        self.apply("You have entered The Plane of Sky.", 11)
        self.assertEqual(self.lull.snapshot(
            BASE + timedelta(seconds=11)).active_count, 0)

    def test_environment_zone_prose_does_not_clear(self):
        self.apply("You begin casting Calm.")
        self.apply("a soul carrier looks less aggressive.", 2)
        self.apply(
            "You have entered an area where levitation effects do not function.",
            3,
        )
        self.assertEqual(self.lull.snapshot(
            BASE + timedelta(seconds=3)).active_count, 1)

    def test_engaging_a_different_target_preserves_the_confirmed_lull(self):
        self.apply("You begin casting Calm.")
        self.apply("a soul carrier looks less aggressive.", 2)
        original = self.lull.snapshot(BASE + timedelta(seconds=2)).rows[0]
        unrelated_events = (
            "You begin casting Flame of Light.",
            "You hit a froglok for 15 points of magic damage by Flame of Light.",
            "Auto attack is on.",
            "You shoot a froglok for 20 points of damage.",
            "a froglok hits YOU for 3 points of damage.",
            "a froglok tries to slash YOU, but misses!",
            "Hingle slashes a froglok for 9 points of damage.",
            "You have slain a froglok!",
            "Your Calm spell has worn off of a froglok.",
            "Your Calm spell on a froglok has been overwritten.",
        )
        for offset, message in enumerate(unrelated_events, 3):
            with self.subTest(message=message):
                self.apply(message, offset)
                snapshot = self.lull.snapshot(BASE + timedelta(seconds=offset))
                self.assertEqual(snapshot.active_count, 1)
                row = snapshot.rows[0]
                self.assertEqual(row.target_name, "a soul carrier")
                self.assertEqual(row.landed_at, original.landed_at)
                self.assertEqual(row.safe_expires_at, original.safe_expires_at)
                self.assertEqual(row.expires_at, original.expires_at)

        self.apply("a soul carrier tries to slash YOU, but misses!", 13)
        self.assertFalse(self.lull.snapshot(BASE + timedelta(seconds=13)).rows)

    def test_lull_result_prose_does_not_change_session_stats(self):
        before = self.stats.snapshot(BASE)
        self.apply("a soul carrier looks less aggressive.", 1)
        after = self.stats.snapshot(BASE + timedelta(seconds=1))
        self.assertEqual(before["combat_damage"], after["combat_damage"])
        self.assertEqual(before["session_dps"], after["session_dps"])


if __name__ == "__main__":
    unittest.main()
