import importlib.util
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path


LOREMASTER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LOREMASTER_DIR))
SPEC = importlib.util.spec_from_file_location(
    "loremaster_charmed_pet_test_app", LOREMASTER_DIR / "loremaster.py")
LOREMASTER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = LOREMASTER
SPEC.loader.exec_module(LOREMASTER)

SessionStats = LOREMASTER.SessionStats
SESSION_GAP = LOREMASTER.SESSION_GAP
is_known_charm_spell = LOREMASTER.is_known_charm_spell
parse_line = LOREMASTER.parse_line

BASE = datetime(2026, 8, 1, 12, 0, 0)


def log_line(offset: int, message: str) -> str:
    stamp = BASE + timedelta(seconds=offset)
    return f"[{stamp.strftime('%a %b %d %H:%M:%S %Y')}] {message}"


def apply_line(stats: SessionStats, offset: int, message: str):
    parsed = parse_line(log_line(offset, message))
    if parsed is None:
        raise AssertionError(f"unparsed test line: {message}")
    stats.apply(*parsed)
    return parsed


class CharmedPetParserTests(unittest.TestCase):
    def test_multiword_ownership_success_and_fade_grammar(self):
        cases = (
            ("A rock golem told you, 'Attacking a rock golem Master.'",
             "pet_attack", "A rock golem"),
            ("A rock golem says 'My leader is Spin.'",
             "pet_leader", "A rock golem"),
            ("a rock golem has been charmed.",
             "pet_charm", "a rock golem"),
            ("Your Cajoling Whispers spell has worn off of a rock golem.",
             "spell_fade", None),
        )
        for message, expected_kind, expected_pet in cases:
            with self.subTest(message=message):
                parsed = parse_line(log_line(0, message))
                self.assertIsNotNone(parsed)
                self.assertEqual(parsed[1], expected_kind)
                if expected_pet is not None:
                    self.assertEqual(parsed[2]["pet"], expected_pet)

    def test_known_charm_families_accept_rank_suffixes(self):
        self.assertTrue(is_known_charm_spell("Cajoling Whispers VI"))
        self.assertTrue(is_known_charm_spell("Beguile 3"))
        self.assertTrue(is_known_charm_spell("Call of Karana"))
        self.assertFalse(is_known_charm_spell("Mesmerization V"))


class CharmedPetAttributionTests(unittest.TestCase):
    def test_exact_rock_golem_replay_adds_charm_damage(self):
        stats = SessionStats("Spin")
        lines = (
            "A rock golem told you, 'Attacking a rock golem Master.'",
            # A same-named enemy can hit the player immediately after a valid
            # ownership line. It must not revoke the charmed-pet identity.
            "A rock golem hits YOU for 40 points of damage.",
            "A rock golem slashes a rock golem for 91 points of damage.",
            "A rock golem slashes a rock golem for 123 points of damage.",
            "You try to smite a rock golem, but miss!",
            "You bash a rock golem for 2 points of damage.",
            "You slash a rock golem for 36 points of damage.",
            "You slash a rock golem for 79 points of damage.",
        )
        for offset, message in enumerate(lines):
            apply_line(stats, offset, message)

        snap = stats.snapshot(BASE + timedelta(seconds=len(lines)))
        fight = snap["fight"]
        self.assertIsNotNone(fight)
        self.assertTrue(stats.is_charmed_pet("a rock golem"))
        self.assertEqual(fight.damage, 331)
        self.assertEqual(snap["pet_damage"], 214)
        self.assertEqual(snap["ambiguous_pet_damage"], 214)
        self.assertEqual(fight.ambiguous_pet_damage, 214)
        self.assertEqual(fight.sources["Pet (A rock golem)"]["t"], 214)
        self.assertEqual(fight.actor_damage["A rock golem (pet)"]["t"], 214)
        self.assertEqual(fight.actor_roles["A rock golem (pet)"], "charmed")
        self.assertEqual(snap["damage_taken"], 40)

    def test_unowned_creature_damage_never_inflates_personal_dps(self):
        stats = SessionStats("Spin")
        lines = (
            "You bash a rock golem for 2 points of damage.",
            "A rock golem slashes a rock golem for 91 points of damage.",
            "A rock golem slashes a rock golem for 123 points of damage.",
            "You slash a rock golem for 36 points of damage.",
            "You slash a rock golem for 79 points of damage.",
        )
        for offset, message in enumerate(lines):
            apply_line(stats, offset, message)

        snap = stats.snapshot(BASE + timedelta(seconds=len(lines)))
        self.assertEqual(snap["fight"].damage, 117)
        self.assertEqual(snap["pet_damage"], 0)
        self.assertEqual(snap["ambiguous_pet_damage"], 0)
        self.assertNotIn("Pet (A rock golem)", snap["damage_by_source"])

    def test_owned_charm_can_open_a_solo_fight(self):
        stats = SessionStats("Spin")
        apply_line(stats, 0, "You begin casting Cajoling Whispers VI.")
        apply_line(stats, 2, "a rock golem has been charmed.")
        self.assertIsNone(stats.fight)
        apply_line(stats, 4,
                   "a rock golem slashes a goblin warrior for 80 points of damage.")

        self.assertIsNotNone(stats.fight)
        self.assertEqual(stats.fight.damage, 80)
        self.assertEqual(stats.pet_damage, 80)
        self.assertEqual(stats.fight.ambiguous_pet_damage, 0)

    def test_pet_spell_dot_and_case_variants_are_attributed(self):
        stats = SessionStats("Spin")
        apply_line(stats, 0, "You begin casting Cajoling Whispers VI.")
        apply_line(stats, 1, "a rock golem has been charmed.")
        apply_line(stats, 2,
                   "a rock golem hit a goblin for 50 points of magic damage by Stone Bite.")
        apply_line(stats, 3,
                   "A goblin has taken 30 damage from Burning by A rock golem.")

        self.assertEqual(stats.pet_damage, 80)
        self.assertEqual(stats.charmed_pet_damage, 80)
        self.assertEqual(stats.summoned_pet_damage, 0)
        self.assertEqual(stats.fight.damage, 80)
        self.assertEqual(stats.fight.charmed_pet_damage, 80)
        self.assertEqual(stats.fight.sources["Pet (A rock golem)"]["h"], 2)
        pet_sources = stats.fight.actor_sources["A rock golem (pet)"]
        self.assertEqual(pet_sources["Non-melee: Stone Bite"], {
            "t": 50, "h": 1, "max": 50, "category": "unknown",
        })
        self.assertEqual(pet_sources["DoT: Burning"], {
            "t": 30, "h": 1, "max": 30, "category": "dot",
        })

    def test_summoned_and_charmed_pet_damage_have_separate_totals(self):
        stats = SessionStats("Spin")
        apply_line(stats, 0, "Gann says 'My leader is Spin.'")
        apply_line(stats, 1, "Gann slashes a goblin for 40 points of damage.")
        apply_line(stats, 2, "You begin casting Charm.")
        apply_line(stats, 3, "a rock golem has been charmed.")
        apply_line(stats, 4, "A rock golem slashes a goblin for 60 points of damage.")
        snap = stats.snapshot(BASE + timedelta(seconds=5))
        self.assertEqual(snap["pet_damage"], 100)
        self.assertEqual(snap["summoned_pet_damage"], 40)
        self.assertEqual(snap["charmed_pet_damage"], 60)
        self.assertEqual(stats.fight.summoned_pet_damage, 40)
        self.assertEqual(stats.fight.charmed_pet_damage, 60)
        self.assertEqual(stats.fight.actor_roles["Gann (pet)"], "summoned")
        self.assertEqual(stats.fight.actor_roles["A rock golem (pet)"], "charmed")
        self.assertEqual(stats.fight.actor_sources["Gann (pet)"]["Melee"], {
            "t": 40, "h": 1, "max": 40, "category": "melee",
        })
        self.assertEqual(
            stats.fight.actor_sources["A rock golem (pet)"]["Melee"],
            {"t": 60, "h": 1, "max": 60, "category": "melee"},
        )


class CharmedPetOwnershipSafetyTests(unittest.TestCase):
    def test_groupmate_and_unrelated_cast_lines_do_not_claim_a_pet(self):
        stats = SessionStats("Spin")
        apply_line(stats, 0, "a ratman warrior has been charmed.")
        apply_line(stats, 1, "You begin casting Greater Healing.")
        apply_line(stats, 2, "a rock golem has been charmed.")
        apply_line(stats, 3, "A froglok says 'My leader is Aria.'")

        self.assertEqual(stats.pet_names, set())
        self.assertEqual(stats.charmed_pet_names, set())

    def test_fade_zone_death_and_rollover_release_only_charms(self):
        stats = SessionStats("Spin")
        apply_line(stats, 0, "Gann says 'My leader is Spin.'")
        apply_line(stats, 1, "You begin casting Cajoling Whispers VI.")
        apply_line(stats, 2, "a rock golem has been charmed.")
        self.assertEqual(stats.persistent_pet_names(), ["Gann"])

        # An unrelated spell fading from the same target is not charm break.
        apply_line(stats, 3,
                   "Your Mesmerization V spell has worn off of a rock golem.")
        self.assertTrue(stats.is_charmed_pet("A rock golem"))
        apply_line(stats, 4,
                   "Your Cajoling Whispers spell has worn off of a rock golem.")
        self.assertFalse(stats.is_pet("A rock golem"))
        self.assertTrue(stats.is_pet("gann"))

        apply_line(stats, 5, "You begin casting Charm.")
        apply_line(stats, 6, "a rat has been charmed.")
        apply_line(stats, 7, "Your Charm spell has worn off.")
        self.assertFalse(stats.is_pet("a rat"))

        apply_line(stats, 8, "You begin casting Charm.")
        apply_line(stats, 9, "a goblin warrior has been charmed.")
        apply_line(stats, 10, "You begin casting Charm.")
        apply_line(stats, 11, "You have entered Blackburrow.")
        self.assertFalse(stats.is_pet("a goblin warrior"))
        self.assertTrue(stats.is_pet("Gann"))
        self.assertIsNone(stats.pending_cast)

        apply_line(stats, 12, "You begin casting Beguile.")
        apply_line(stats, 13, "an orc centurion has been charmed.")
        apply_line(stats, 14, "You begin casting Beguile.")
        apply_line(stats, 15, "You have been slain by an orc centurion!")
        self.assertFalse(stats.is_pet("an orc centurion"))
        self.assertTrue(stats.is_pet("Gann"))
        self.assertIsNone(stats.pending_cast)

        rollover = SessionStats("Spin", session_gap=SESSION_GAP)
        apply_line(rollover, 0, "Gann says 'My leader is Spin.'")
        apply_line(rollover, 1, "You begin casting Charm.")
        apply_line(rollover, 2, "a rat has been charmed.")
        apply_line(rollover, 4000, "You slash a beetle for 5 points of damage.")
        self.assertTrue(rollover.is_pet("Gann"))
        self.assertFalse(rollover.is_pet("a rat"))

    def test_a_new_charm_replaces_the_old_alias(self):
        stats = SessionStats("Spin")
        apply_line(stats, 0, "You begin casting Charm.")
        apply_line(stats, 1, "a rock golem has been charmed.")
        apply_line(stats, 2, "You begin casting Beguile.")
        apply_line(stats, 3, "a ratman warrior has been charmed.")

        self.assertFalse(stats.is_pet("a rock golem"))
        self.assertTrue(stats.is_charmed_pet("A ratman warrior"))
        self.assertEqual(stats.persistent_pet_names(), [])

    def test_differently_named_killer_releases_charm_but_same_name_is_ambiguous(self):
        ambiguous = SessionStats("Spin")
        apply_line(ambiguous, 0, "You begin casting Charm.")
        apply_line(ambiguous, 1, "a rat has been charmed.")
        apply_line(ambiguous, 2, "A rat has been slain by a rat!")
        self.assertTrue(ambiguous.is_charmed_pet("a rat"))

        confirmed = SessionStats("Spin")
        apply_line(confirmed, 0, "You begin casting Charm.")
        apply_line(confirmed, 1, "a rat has been charmed.")
        apply_line(confirmed, 2, "A rat has been slain by a goblin!")
        self.assertFalse(confirmed.is_pet("a rat"))
        apply_line(confirmed, 3,
                   "A rat slashes a goblin for 25 points of damage.")
        self.assertEqual(confirmed.pet_damage, 0)


if __name__ == "__main__":
    unittest.main()
