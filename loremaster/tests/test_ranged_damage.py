import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


LOREMASTER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LOREMASTER_DIR))
SPEC = importlib.util.spec_from_file_location(
    "loremaster_ranged_damage_test_app", LOREMASTER_DIR / "loremaster.py")
LOREMASTER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = LOREMASTER
SPEC.loader.exec_module(LOREMASTER)

from engine_protocol import build_engine_snapshot


class RangerRangedDamageTests(unittest.TestCase):
    def test_ranger_shot_variants_contribute_ranged_damage_and_crits(self):
        stats = LOREMASTER.SessionStats("Archer")
        lines = (
            "[Mon Aug 24 15:08:26 2026] You shoot Innoruuk`s Chosen for 146 points of damage.",
            "[Mon Aug 24 15:08:28 2026] You shoot Innoruuk`s Chosen for 130 points of damage. (Critical)",
            "[Mon Aug 24 15:08:30 2026] You shoot Lady Vox for 616 points of damage. (Double Bow Shot)",
            "[Mon Aug 24 15:08:32 2026] You shoot Lady Vox for 204 points of damage. (Strikethrough Double Bow Shot)",
            "[Mon Aug 24 15:08:34 2026] You shoot Lady Vox for 1055 points of damage. (Critical Double Bow Shot)",
            "[Mon Aug 24 15:08:36 2026] You shoot Innoruuk`s Chosen for 1055 points of damage. (Strikethrough Critical Double Bow Shot)",
        )

        parsed_lines = []
        for line in lines:
            parsed = LOREMASTER.parse_line(line)
            self.assertIsNotNone(parsed, line)
            self.assertEqual(parsed[1], "ranged_out")
            parsed_lines.append(parsed)
            stats.apply(*parsed)

        self.assertEqual(
            [bool(parsed[2].get("ranged_result")) for parsed in parsed_lines],
            [False, True, True, True, True, True],
        )
        self.assertIsNotNone(stats.fight)
        self.assertEqual(stats.fight.damage, 3206)
        self.assertEqual(stats.fight.sources["Ranged"], {
            "t": 276, "h": 2, "max": 146,
        })
        self.assertEqual(stats.fight.sources["Double Bow Shot"], {
            "t": 2930, "h": 4, "max": 1055,
        })
        self.assertEqual(stats.fight.source_categories["Ranged"], "melee")
        self.assertEqual(
            stats.fight.source_categories["Double Bow Shot"], "melee")
        self.assertEqual(stats.fight.crits, 3)
        self.assertEqual(stats.crits, 3)
        self.assertEqual(stats.melee_hits, 6)

        snapshot = stats.snapshot(parsed_lines[-1][0])
        self.assertEqual(snapshot["combat_damage"], 3206)
        self.assertEqual(snapshot["fight_sources"]["Ranged"]["t"], 276)
        self.assertEqual(snapshot["fight_sources"]["Ranged"]["h"], 2)
        self.assertEqual(
            snapshot["fight_sources"]["Ranged"]["category"], "melee")
        self.assertEqual(
            snapshot["fight_sources"]["Double Bow Shot"]["t"], 2930)

    def test_failed_shots_keep_combat_active_without_inventing_damage(self):
        stats = LOREMASTER.SessionStats("Archer")
        lines = (
            "[Mon Aug 24 00:26:22 2026] You try to shoot Lady Vox, but miss!",
            "[Mon Aug 24 00:26:29 2026] You try to shoot Lady Vox, but Lady Vox blocks!",
            "[Mon Aug 24 00:26:31 2026] You try to shoot Lady Vox, but Lady Vox dodges!",
            "[Mon Aug 24 00:26:33 2026] You try to shoot Lady Vox, but Lady Vox's magical skin absorbs the blow!",
        )

        for line in lines:
            parsed = LOREMASTER.parse_line(line)
            self.assertIsNotNone(parsed, line)
            self.assertEqual(parsed[1], "miss_out")
            self.assertEqual(parsed[2]["target"], "Lady Vox")
            stats.apply(*parsed)

        self.assertIsNotNone(stats.fight)
        self.assertEqual(stats.fight.damage, 0)
        self.assertEqual(stats.fight.misses, 4)
        self.assertEqual(stats.melee_misses, 4)
        self.assertEqual(dict(stats.fight.sources), {})
        self.assertEqual(dict(stats.damage_by_source), {})
        self.assertEqual(stats.last_own_action, stats.last_event)

    def test_group_ranger_shots_reach_the_group_actor_meter(self):
        stats = LOREMASTER.SessionStats("Archer")
        lines = (
            "[Mon Aug 24 15:08:20 2026] Huntress has joined the group.",
            "[Mon Aug 24 15:08:21 2026] Huntress shoots Lady Vox for 425 points of damage. (Critical Double Bow Shot)",
        )

        parsed_lines = [LOREMASTER.parse_line(line) for line in lines]
        self.assertTrue(all(parsed_lines))
        self.assertEqual(
            [parsed[1] for parsed in parsed_lines],
            ["group_join", "ranged_third"],
        )
        for parsed in parsed_lines:
            stats.apply(*parsed)

        self.assertIsNotNone(stats.fight)
        self.assertEqual(stats.fight.damage, 0)
        self.assertEqual(stats.fight.actor_damage["Huntress"], {
            "t": 425, "h": 1, "max": 425,
        })
        self.assertEqual(stats.fight.actor_roles["Huntress"], "group")
        snapshot = stats.snapshot(parsed_lines[-1][0])
        self.assertEqual(snapshot["fight_actor_damage"]["Huntress"]["t"], 425)
        self.assertEqual(snapshot["fight_actor_roles"]["Huntress"], "group")

        engine_snapshot = build_engine_snapshot(
            sequence=1,
            observed_at=parsed_lines[-1][0],
            stats_snapshot=snapshot,
            control_snapshot=SimpleNamespace(
                rows=(), hidden_rows=0, notice_count=0, ambiguity_count=0),
        )
        huntress = next(
            actor for actor in engine_snapshot.encounters[-1].actors
            if actor.name == "Huntress")
        self.assertEqual(huntress.role, "group")
        self.assertEqual(huntress.encounter_damage, 425)

        stranger = LOREMASTER.SessionStats("Archer")
        stranger_shot = LOREMASTER.parse_line(
            "[Mon Aug 24 15:08:22 2026] Stranger shoots Lady Vox for 900 "
            "points of damage. (Critical Double Bow Shot)")
        self.assertIsNotNone(stranger_shot)
        stranger.apply(*stranger_shot)
        self.assertIsNone(stranger.fight)
        self.assertEqual(dict(stranger.actor_damage), {})

    def test_ranged_damage_retires_mez_and_lull_timers(self):
        stats = LOREMASTER.SessionStats("Archer")
        stats.level = 50
        mez = LOREMASTER.MezTracker()
        lull = LOREMASTER.LullTracker()
        lines = (
            "[Mon Aug 24 15:08:20 2026] You begin casting Mesmerize.",
            "[Mon Aug 24 15:08:21 2026] Lady Vox has been mesmerized.",
            "[Mon Aug 24 15:08:22 2026] You shoot Lady Vox for 100 points of damage.",
            "[Mon Aug 24 15:08:23 2026] You begin casting Calm.",
            "[Mon Aug 24 15:08:24 2026] Lady Vox looks less aggressive.",
            "[Mon Aug 24 15:08:25 2026] Huntress shoots Lady Vox for 425 points of damage. (Double Bow Shot)",
        )

        for index, line in enumerate(lines):
            parsed = LOREMASTER.parse_line(line)
            self.assertIsNotNone(parsed, line)
            LOREMASTER.apply_log_models(
                stats, mez, *parsed,
                lull_tracker=lull, caster_level=stats.level)
            if index == 1:
                self.assertEqual(mez.snapshot(parsed[0]).active_count, 1)
            elif index == 2:
                self.assertEqual(mez.snapshot(parsed[0]).active_count, 0)
            elif index == 4:
                self.assertEqual(lull.snapshot(parsed[0]).active_count, 1)
            elif index == 5:
                self.assertEqual(lull.snapshot(parsed[0]).active_count, 0)


if __name__ == "__main__":
    unittest.main()
