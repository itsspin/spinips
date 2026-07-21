import sys
import unittest
import importlib.util
from datetime import datetime, timedelta
from pathlib import Path


LOREMASTER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LOREMASTER_DIR))
SPEC = importlib.util.spec_from_file_location(
    "loremaster_composition_test_app", LOREMASTER_DIR / "loremaster.py")
LOREMASTER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = LOREMASTER
SPEC.loader.exec_module(LOREMASTER)

Fight = LOREMASTER.Fight
SessionStats = LOREMASTER.SessionStats
composition_comparisons = LOREMASTER.composition_comparisons
configured_composition = LOREMASTER.configured_composition
infer_composition_from_message = LOREMASTER.infer_composition_from_message
normalize_composition = LOREMASTER.normalize_composition
parse_line = LOREMASTER.parse_line
remember_composition = LOREMASTER.remember_composition
summarize_compositions = LOREMASTER.summarize_compositions


class CompositionIdentityTests(unittest.TestCase):
    def test_normalizes_names_abbreviations_and_order(self):
        self.assertEqual(normalize_composition("war/brd/dru"), "WAR / BRD / DRU")
        self.assertEqual(normalize_composition("Warrior, Bard, and Druid"),
                         "WAR / BRD / DRU")
        self.assertEqual(normalize_composition(["NEC", "DRU", "WAR"]),
                         "NEC / DRU / WAR")
        with self.assertRaises(ValueError):
            normalize_composition("WAR / DRU")
        with self.assertRaises(ValueError):
            normalize_composition("WAR / WAR / DRU")
        with self.assertRaises(ValueError):
            normalize_composition("WAR / BOG / DRU")

    def test_inference_accepts_only_explicit_exact_announcements(self):
        self.assertEqual(
            infer_composition_from_message(
                "Your active classes are Warrior, Bard, and Druid."),
            "WAR / BRD / DRU",
        )
        self.assertEqual(infer_composition_from_message("Active classes: WAR/NEC/DRU"),
                         "WAR / NEC / DRU")
        self.assertEqual(infer_composition_from_message(
            "Spin tells the group, 'WAR BRD DRU'"), "")
        self.assertEqual(infer_composition_from_message(
            "Your active classes are WAR and DRU."), "")

        parsed = parse_line(
            "[Mon Jul 20 12:00:00 2026] Your active classes are WAR / BRD / DRU."
        )
        self.assertIsNotNone(parsed)
        stats = SessionStats()
        stats.apply(*parsed)
        self.assertEqual(stats.composition, "WAR / BRD / DRU")
        self.assertEqual(stats.composition_source, "exact log")

    def test_per_character_profile_falls_back_safely(self):
        cfg = {"composition": "WAR/BRD/DRU", "composition_profiles": {}}
        remember_composition(cfg, "Spin", "WAR / NEC / DRU")
        self.assertEqual(configured_composition(cfg, "Spin"), "WAR / NEC / DRU")
        self.assertEqual(configured_composition(cfg, "SomeoneElse"), "WAR / NEC / DRU")
        cfg["composition_profiles"]["Spin"] = "not a loadout"
        self.assertEqual(configured_composition(cfg, "Spin"), "")


class CompositionEncounterTests(unittest.TestCase):
    def test_multi_mob_pull_stays_one_tagged_fight(self):
        start = datetime(2026, 7, 20, 12, 0, 0)
        stats = SessionStats("Spin", composition="WAR / BRD / DRU")
        stats.apply(start, "melee_out", {"target": "a shaman", "dmg": "100"})
        stats.apply(start + timedelta(seconds=1), "kill_you", {"target": "a shaman"})
        stats.apply(start + timedelta(seconds=2), "melee_out",
                    {"target": "a warrior", "dmg": "150"})
        stats.apply(start + timedelta(seconds=3), "kill_you", {"target": "a warrior"})
        stats.finalize_idle(start + timedelta(seconds=14))

        self.assertEqual(len(stats.fights), 1)
        fight = stats.fights[0]
        self.assertEqual(fight.composition, "WAR / BRD / DRU")
        self.assertEqual(fight.kills, 2)
        self.assertEqual(fight.name, "2 enemies")
        self.assertEqual(set(fight.targets), {"Shaman", "Warrior"})

        stats.set_composition("WAR / NEC / DRU")
        stats.apply(start + timedelta(seconds=20), "melee_out",
                    {"target": "a skeleton", "dmg": "200"})
        self.assertEqual(stats.fight.composition, "WAR / NEC / DRU")
        self.assertEqual(stats.fights[0].composition, "WAR / BRD / DRU")

    def test_manual_correction_retags_only_active_encounter(self):
        start = datetime(2026, 7, 20, 12, 0, 0)
        stats = SessionStats("Spin", composition="WAR / BRD / DRU")
        stats.apply(start, "melee_out", {"target": "a rat", "dmg": "10"})
        stats.set_composition("WAR / NEC / DRU", source="manual")
        self.assertEqual(stats.fight.composition, "WAR / NEC / DRU")
        self.assertEqual(stats.fight.composition_source, "manual")

    @staticmethod
    def fight(at, damage, seconds, composition, name):
        fight = Fight(at, at + timedelta(seconds=seconds), composition=composition)
        fight.damage = damage
        fight.targets[name] = damage
        return fight

    def test_same_other_all_filters_and_deltas_are_deterministic(self):
        at = datetime(2026, 7, 20, 12, 0, 0)
        first = self.fight(at, 1000, 10, "WAR / BRD / DRU", "Shaman")
        other = self.fight(at + timedelta(minutes=1), 1800, 10,
                           "WAR / NEC / DRU", "Warrior")
        same = self.fight(at + timedelta(minutes=2), 1400, 10,
                          "WAR / BRD / DRU", "Shaman")
        selected = self.fight(at + timedelta(minutes=3), 2400, 10,
                              "WAR / BRD / DRU", "Dragon")
        fights = [first, other, same, selected]

        self.assertEqual(composition_comparisons(fights, selected, "same"),
                         [first, same])
        self.assertEqual(composition_comparisons(fights, selected, "other"), [other])
        self.assertEqual(composition_comparisons(fights, selected, "all"),
                         [first, other, same])
        self.assertEqual(selected.dps - other.dps, 60)
        self.assertEqual(selected.damage - other.damage, 600)

        summaries = summarize_compositions(fights)
        bard = next(row for row in summaries
                    if row["composition"] == "WAR / BRD / DRU")
        self.assertEqual(bard["fights"], 3)
        self.assertAlmostEqual(bard["average_dps"], 160)
        self.assertEqual(bard["best_dps"], 240)
        self.assertEqual(bard["damage"], 4800)


if __name__ == "__main__":
    unittest.main()
