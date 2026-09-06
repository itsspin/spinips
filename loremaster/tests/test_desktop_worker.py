import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


LOREMASTER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LOREMASTER_DIR))

from desktop_worker import HeadlessEngine  # noqa: E402


class DesktopWorkerTests(unittest.TestCase):
    def test_progression_evidence_reaches_desktop(self):
        with tempfile.TemporaryDirectory() as root:
            engine = HeadlessEngine(data_dir=root)
            try:
                engine.process_line("[Tue Aug 25 12:00:00 2026] You have gained a level! Welcome to level 40!")
                engine.process_line("[Tue Aug 25 12:00:30 2026] You gain experience! (2.5%)")
                engine.process_line("[Tue Aug 25 12:01:00 2026] You have gained an ability point!")
                view = engine.snapshot_event(datetime(2026, 8, 25, 12, 2))["snapshot"]["progression"]
                self.assertEqual(view["percentPerHour"], 75)
                self.assertEqual(view["currentPercent"], 2.5)
                self.assertEqual(view["level"], 40)
                self.assertEqual(view["aaEarned"], 1)
                engine.stats.set_composition("PAL/MNK/ENC")
                engine.stats.set_composition("WAR/CLR/MAG")
                self.assertIsNone(engine.stats.progression.snapshot(datetime(2026, 8, 25, 12, 3))["currentPercent"])
            finally:
                engine.close()

    def test_loot_chronicle_captures_safe_formats_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as root:
            lines = (
                "[Thu Aug 13 18:00:00 2026] You have entered Nagafen's Lair - Solo 3 (Fused).",
                "[Thu Aug 13 18:00:01 2026] --You have looted a Cloak of Flames +4 from Lord Nagafen's corpse.--",
                "[Thu Aug 13 18:00:02 2026] You looted a Crystallized Sulfur from Lord Nagafen's corpse and stored it in your Dragon Hoard",
                "[Thu Aug 13 18:00:03 2026] You have successfully merged two items together to create a new item: Prismatic Shield +2",
                "[Thu Aug 13 18:00:04 2026] Ruby has been placed in your inventory!",
                "[Thu Aug 13 18:00:04 2026] Ruby has been placed in your inventory!",
                "[Thu Aug 13 18:00:05 2026] You looted a Mote of Minor Potential from Lord Nagafen's corpse and stored it in your currency",
                "[Thu Aug 13 18:00:06 2026] You looted a Torch from Lord Nagafen's corpse to create a Torch +1",
            )
            engine = HeadlessEngine(data_dir=root)
            try:
                engine.stats.character = "Spin"
                for line in lines:
                    self.assertTrue(engine.process_line(line))
                snapshot = engine.snapshot_event(
                    datetime(2026, 8, 13, 18, 0, 4))["snapshot"]
            finally:
                engine.close()
            self.assertEqual(snapshot["lootTotalCount"], 7)
            self.assertEqual(snapshot["lootUniqueCount"], 6)
            self.assertIn("merged", {
                row["acquisitionType"] for row in snapshot["loot"]})
            cloak = next(row for row in snapshot["loot"]
                         if row["item"].startswith("Cloak of Flames"))
            self.assertEqual(cloak["source"], "Lord Nagafen")
            self.assertEqual(cloak["raidTier"], 3)
            upgraded = next(row for row in snapshot["loot"]
                            if row["item"] == "Torch +1")
            self.assertEqual(upgraded["source"], "Lord Nagafen")
            self.assertEqual(upgraded["acquisitionType"], "upgraded-loot")
            stored_currency = next(row for row in snapshot["loot"]
                                   if row["item"].startswith("Mote of Minor"))
            self.assertEqual(stored_currency["acquisitionType"],
                             "stored-currency")
            reopened = HeadlessEngine(data_dir=root)
            try:
                reopened.stats.character = "Spin"
                # A warm log replay produces the same structured ordinal IDs;
                # it must never duplicate the durable ledger.
                for line in lines:
                    self.assertTrue(reopened.process_line(line))
                replayed = reopened.snapshot_event(
                    datetime(2026, 8, 13, 18, 1, 0))["snapshot"]
            finally:
                reopened.close()
            self.assertEqual(replayed["lootTotalCount"], 7)
            self.assertEqual(len(replayed["loot"]), 7)

    def test_loot_and_completed_fight_share_immutable_encounter_identity(self):
        with tempfile.TemporaryDirectory() as root:
            engine = HeadlessEngine(data_dir=root)
            try:
                engine.stats.character = "Spin"
                for line in (
                    "[Thu Aug 13 18:00:00 2026] You slash a bat for 20 points of damage.",
                    "[Thu Aug 13 18:00:01 2026] --You have looted a Bat Wing from a bat's corpse.--",
                    "[Thu Aug 13 18:00:02 2026] You slash a spider for 30 points of damage.",
                ):
                    self.assertTrue(engine.process_line(line))
                snapshot = engine.snapshot_event(
                    datetime(2026, 8, 13, 18, 0, 20))["snapshot"]
            finally:
                engine.close()
            self.assertEqual(len(snapshot["journalEncounters"]), 1)
            self.assertEqual(snapshot["loot"][0]["encounterId"],
                             snapshot["journalEncounters"][0]["encounterId"])
            self.assertIn("+1 more", snapshot["journalEncounters"][0]["name"])

    def test_loot_query_and_item_cache_bridge_use_the_durable_journal(self):
        with tempfile.TemporaryDirectory() as root:
            engine = HeadlessEngine(data_dir=root)
            try:
                engine.stats.character = "Spin"
                self.assertTrue(engine.process_line(
                    "[Thu Aug 13 18:00:01 2026] --You have looted a Cloak of Flames +4 from Lord Nagafen's corpse.--"))
                self.assertTrue(engine.cache_item({
                    "title": "Cloak of Flames",
                    "url": "https://eqlwiki.com/Cloak_of_Flames",
                    "stats": ["AC: 10"], "notes": [],
                    "sections": {"Drops From": ["Lord Nagafen"]},
                }))
                result = engine.query_loot({
                    "scope": "known", "query": "cloak",
                    "offset": 0, "limit": 50,
                })
            finally:
                engine.close()
            self.assertEqual(result["total"], 1)
            self.assertFalse(result["hasMore"])
            self.assertEqual(result["rows"][0]["itemInfo"]["stats"],
                             ["AC: 10"])
            self.assertIn("freshness", result["rows"][0]["itemInfo"])

    def test_log_attach_recovers_zone_and_composition_beyond_combat_warm_start(self):
        with tempfile.TemporaryDirectory() as root:
            log = Path(root) / "eqlog_Spin_qeynos.txt"
            log.write_text(
                "[Sat Aug 08 10:00:00 2026] You have entered The Plane of Fear.\n"
                "[Sat Aug 08 10:00:01 2026] Your active classes are PAL / MNK / ENC.\n"
                "[Sat Aug 08 10:00:02 2026] AromeK has joined the group.\n"
                "[Sat Aug 08 17:00:00 2026] Auto attack is off.\n",
                encoding="latin-1")
            engine = HeadlessEngine(log_path=str(log), data_dir=root)
            try:
                _parsed, switched = engine.poll()
                event = engine.snapshot_event(datetime(2026, 8, 8, 17, 0, 1))
            finally:
                engine.close()
            self.assertTrue(switched)
            self.assertEqual(event["snapshot"]["character"]["zone"],
                             "The Plane of Fear")
            self.assertEqual(event["snapshot"]["character"]["composition"],
                             "PAL / MNK / ENC")
            self.assertEqual(event["snapshot"]["groupMembers"], ("AromeK",))

    def test_explicit_group_members_are_distinct_from_nearby_actors(self):
        with tempfile.TemporaryDirectory() as root:
            engine = HeadlessEngine(data_dir=root)
            try:
                engine.stats.character = "Spin"
                for line in (
                    "[Sat Aug 08 17:00:00 2026] AromeK has joined the group.",
                    "[Sat Aug 08 17:00:01 2026] You slash a rock golem for 100 points of damage.",
                    "[Sat Aug 08 17:00:02 2026] AromeK slashes a rock golem for 70 points of damage.",
                    "[Sat Aug 08 17:00:03 2026] Stranger slashes a rock golem for 40 points of damage.",
                ):
                    self.assertTrue(engine.process_line(line))
                event = engine.snapshot_event(datetime(2026, 8, 8, 17, 0, 4))
            finally:
                engine.close()
            actors = {row["name"]: row for row in
                      event["snapshot"]["encounters"][-1]["actors"]}
            self.assertEqual(actors["AromeK"]["role"], "group")
            self.assertEqual(actors["Stranger"]["role"], "observed")
            self.assertEqual(event["snapshot"]["groupMembers"], ("AromeK",))
            self.assertEqual(event["snapshot"]["combat"]["fightDamage"], 100)

    def test_manual_composition_setting_is_validated_and_applied(self):
        with tempfile.TemporaryDirectory() as root:
            engine = HeadlessEngine(data_dir=root)
            try:
                self.assertTrue(engine.set_composition("pal/mnk/enc"))
                self.assertFalse(engine.set_composition("pal/enc"))
                event = engine.snapshot_event(datetime(2026, 8, 8, 17, 0, 0))
            finally:
                engine.close()
            self.assertEqual(event["snapshot"]["character"]["composition"],
                             "PAL / MNK / ENC")

    def test_auto_attack_state_crosses_desktop_boundary_exactly(self):
        with tempfile.TemporaryDirectory() as root:
            engine = HeadlessEngine(data_dir=root)
            try:
                self.assertTrue(engine.process_line(
                    "[Fri Aug 07 19:59:58 2026] Auto attack is on."))
                enabled = engine.snapshot_event(
                    datetime(2026, 8, 7, 19, 59, 59))
                self.assertTrue(engine.process_line(
                    "[Fri Aug 07 20:00:00 2026] Auto attack is off."))
                disabled = engine.snapshot_event(
                    datetime(2026, 8, 7, 20, 0, 1))
            finally:
                engine.close()
            self.assertTrue(enabled["snapshot"]["combat"]["autoAttack"])
            self.assertFalse(disabled["snapshot"]["combat"]["autoAttack"])

    def test_live_snapshot_preserves_damage_and_control_parity(self):
        with tempfile.TemporaryDirectory() as root:
            engine = HeadlessEngine(data_dir=root)
            try:
                self.assertTrue(engine.set_raid_difficulty(2))
                lines = [
                    "[Fri Aug 07 20:00:00 2026] You begin casting Mesmerize.",
                    "[Fri Aug 07 20:00:02 2026] a soul carrier has been mesmerized.",
                    "[Fri Aug 07 20:00:03 2026] You slash Lord Nagafen for 100 points of damage.",
                    "[Fri Aug 07 20:00:04 2026] You have slain Lord Nagafen!",
                ]
                for line in lines:
                    self.assertTrue(engine.process_line(line))
                event = engine.snapshot_event(datetime(2026, 8, 7, 20, 0, 5))
            finally:
                engine.close()
            self.assertEqual(event["eventType"], "engine.snapshot")
            self.assertEqual(event["snapshot"]["combat"]["personalDamage"], 100)
            self.assertEqual(event["snapshot"]["controls"][0]["kind"], "mez")
            weekly = event["snapshot"]["weekly"]
            self.assertEqual(weekly["completedCount"], 1)
            self.assertNotIn("altZLockouts", weekly)
            self.assertNotIn("altZScan", weekly)
            nagafen = next(row for row in weekly["raids"]
                           if row["target"] == "Lord Nagafen")
            self.assertTrue(nagafen["difficulties"][2])

    def test_controls_survive_other_target_encounter_transitions_until_expiry(self):
        with tempfile.TemporaryDirectory() as root:
            engine = HeadlessEngine(data_dir=root)
            base = datetime(2026, 8, 29, 12, 0, 0)

            def apply(message, offset):
                stamp = (base + timedelta(seconds=offset)).strftime(
                    "%a %b %d %H:%M:%S %Y")
                self.assertTrue(engine.process_line(f"[{stamp}] {message}"))

            def control_identity(snapshot):
                return {
                    row["kind"]: (
                        row["target"], row["landedAt"],
                        row["safeExpiresAt"], row["expiresAt"],
                    )
                    for row in snapshot["controls"]
                    if row["state"] == "active"
                }

            try:
                engine.stats.character = "Spin"
                engine.stats.level = 50
                apply("You begin casting Dazzle.", 0)
                apply("a thought spoiler has been mesmerized.", 1)
                apply("You begin casting Calm.", 2)
                apply("a soul carrier looks less aggressive.", 4)
                initial = engine.snapshot_event(base + timedelta(seconds=4))["snapshot"]
                original = control_identity(initial)
                self.assertEqual(set(original), {"mez", "lull"})

                for offset, message in (
                    (5, "You begin casting Flame of Light."),
                    (6, "You hit a froglok for 15 points of magic damage by Flame of Light."),
                    (7, "Auto attack is on."),
                    (8, "You slash a froglok for 20 points of damage."),
                    (9, "You have slain a froglok!"),
                ):
                    apply(message, offset)
                    snapshot = engine.snapshot_event(
                        base + timedelta(seconds=offset))["snapshot"]
                    self.assertEqual(control_identity(snapshot), original)

                idle = engine.snapshot_event(base + timedelta(seconds=23))["snapshot"]
                self.assertFalse(idle["combat"]["active"])
                self.assertEqual(control_identity(idle), original)

                apply("You shoot a goblin for 10 points of damage.", 24)
                next_fight = engine.snapshot_event(base + timedelta(seconds=24))["snapshot"]
                self.assertTrue(next_fight["combat"]["active"])
                self.assertEqual(len(next_fight["encounters"]), 2)
                self.assertEqual(control_identity(next_fight), original)

                lull_expiry = engine.lull.snapshot(
                    base + timedelta(seconds=24)).rows[0].expires_at
                mez_expiry = engine.mez.snapshot(
                    base + timedelta(seconds=24)).rows[0].expires_at
                mez_prune_at = mez_expiry + timedelta(
                    seconds=engine.mez.expiry_grace_seconds)
                before_lull_expiry = engine.snapshot_event(
                    lull_expiry - timedelta(milliseconds=1))["snapshot"]
                self.assertEqual(control_identity(before_lull_expiry), original)
                after_lull_expiry = engine.snapshot_event(lull_expiry)["snapshot"]
                self.assertEqual(control_identity(after_lull_expiry), {
                    "mez": original["mez"],
                })
                before_mez_expiry = engine.snapshot_event(
                    mez_prune_at - timedelta(milliseconds=1))["snapshot"]
                self.assertEqual(control_identity(before_mez_expiry), {
                    "mez": original["mez"],
                })
                after_mez_expiry = engine.snapshot_event(mez_prune_at)["snapshot"]
                self.assertEqual(control_identity(after_mez_expiry), {})
            finally:
                engine.close()

    def test_log_instance_context_overrides_manual_fallback_and_records_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            engine = HeadlessEngine(data_dir=root)
            try:
                engine.stats.character = "Spin"
                self.assertTrue(engine.set_raid_difficulty(4))
                for line in (
                    "[Fri Aug 07 20:00:00 2026] You have entered The Plane of Fear - Group 1 (Awakened).",
                    "[Fri Aug 07 20:00:01 2026] You slash Cazic-Thule for 100 points of damage.",
                    "[Fri Aug 07 20:00:02 2026] Cazic-Thule has been slain by AromeK!",
                ):
                    self.assertTrue(engine.process_line(line))
                event = engine.snapshot_event(datetime(2026, 8, 7, 20, 0, 3))
            finally:
                engine.close()
            weekly = event["snapshot"]["weekly"]
            self.assertEqual(weekly["activeDifficulty"], 1)
            self.assertEqual(weekly["configuredDifficulty"], 4)
            self.assertEqual(weekly["difficultySource"], "log-zone")
            self.assertEqual(weekly["raidContext"]["instanceName"],
                             "The Plane of Fear - Group 1 (Awakened)")
            self.assertEqual(weekly["raidContext"]["mode"], "Group")
            self.assertEqual(weekly["pendingRaidTarget"], "")
            cazic = next(row for row in weekly["raids"]
                         if row["target"] == "Cazic-Thule")
            self.assertEqual(cazic["difficulties"],
                             [False, True, False, False, False])
            kill = weekly["kills"][0]
            self.assertEqual(kill["difficulty_source"], "log-zone")
            self.assertEqual(kill["instance_mode"], "Group")
            self.assertEqual(kill["instance_label"], "Awakened")
            self.assertIn("You have entered The Plane of Fear", kill["evidence"])

    def test_environmental_zone_prose_cannot_erase_log_instance_context(self):
        with tempfile.TemporaryDirectory() as root:
            engine = HeadlessEngine(data_dir=root)
            try:
                engine.stats.character = "Spin"
                for line in (
                    "[Fri Aug 07 20:00:00 2026] You have entered Nagafen's Lair - Solo 4 (Refined).",
                    "[Fri Aug 07 20:00:01 2026] You have entered an area where levitation effects do not function.",
                    "[Fri Aug 07 20:00:02 2026] You have slain Lord Nagafen!",
                ):
                    self.assertTrue(engine.process_line(line))
                event = engine.snapshot_event(datetime(2026, 8, 7, 20, 0, 3))
            finally:
                engine.close()
            weekly = event["snapshot"]["weekly"]
            self.assertEqual(weekly["activeDifficulty"], 4)
            nagafen = next(row for row in weekly["raids"]
                           if row["target"] == "Lord Nagafen")
            self.assertTrue(nagafen["difficulties"][4])

    def test_suffix_only_instance_context_credits_d0(self):
        with tempfile.TemporaryDirectory() as root:
            engine = HeadlessEngine(data_dir=root)
            try:
                engine.stats.character = "Spin"
                for line in (
                    "[Fri Aug 07 20:00:00 2026] You have entered The Plane of Fear - Group.",
                    "[Fri Aug 07 20:00:01 2026] You slash Cazic-Thule for 100 points of damage.",
                    "[Fri Aug 07 20:00:02 2026] You have slain Cazic-Thule!",
                ):
                    self.assertTrue(engine.process_line(line))
                weekly = engine.snapshot_event(
                    datetime(2026, 8, 7, 20, 0, 3))["snapshot"]["weekly"]
            finally:
                engine.close()
            self.assertEqual(weekly["activeDifficulty"], 0)
            self.assertEqual(weekly["raidContext"]["label"], "Normal")
            cazic = next(row for row in weekly["raids"]
                         if row["target"] == "Cazic-Thule")
            self.assertEqual(cazic["difficulties"],
                             [True, False, False, False, False])

    def test_plain_zone_clears_log_context_and_restores_manual_fallback(self):
        with tempfile.TemporaryDirectory() as root:
            engine = HeadlessEngine(data_dir=root)
            try:
                engine.stats.character = "Spin"
                self.assertTrue(engine.set_raid_difficulty(3))
                for line in (
                    "[Fri Aug 07 20:00:00 2026] You have entered The Plane of Fear - Group 1 (Awakened).",
                    "[Fri Aug 07 20:00:01 2026] You have entered Permafrost Keep.",
                    "[Fri Aug 07 20:00:02 2026] You have slain Lady Vox!",
                ):
                    self.assertTrue(engine.process_line(line))
                event = engine.snapshot_event(datetime(2026, 8, 7, 20, 0, 3))
            finally:
                engine.close()
            weekly = event["snapshot"]["weekly"]
            self.assertIsNone(weekly["raidContext"])
            self.assertEqual(weekly["activeDifficulty"], 3)
            self.assertEqual(weekly["difficultySource"], "manual")
            vox = next(row for row in weekly["raids"]
                       if row["target"] == "Lady Vox")
            self.assertTrue(vox["difficulties"][3])

    def test_log_warm_start_recovers_instance_context_without_combat_replay(self):
        with tempfile.TemporaryDirectory() as root:
            log = Path(root) / "eqlog_Spin_qeynos.txt"
            log.write_text(
                "[Sat Aug 08 10:00:00 2026] You have entered Nagafen's Lair - Solo 2 (Adaptive).\n"
                "[Sat Aug 08 17:00:00 2026] Auto attack is off.\n",
                encoding="latin-1")
            engine = HeadlessEngine(log_path=str(log), data_dir=root)
            try:
                _parsed, switched = engine.poll()
                event = engine.snapshot_event(datetime(2026, 8, 8, 17, 0, 1))
            finally:
                engine.close()
            self.assertTrue(switched)
            context = event["snapshot"]["weekly"]["raidContext"]
            self.assertEqual(context["difficulty"], 2)
            self.assertEqual(context["label"], "Adaptive")
            self.assertTrue(context["observedAt"].endswith("Z"))
            self.assertEqual(
                context["evidence"],
                "You have entered Nagafen's Lair - Solo 2 (Adaptive).")

    def test_selected_missing_directory_reports_searching(self):
        with tempfile.TemporaryDirectory() as root:
            engine = HeadlessEngine(log_path=str(Path(root) / "missing"), data_dir=root)
            try:
                health = engine.health()
            finally:
                engine.close()
            self.assertEqual(health["state"], "searching")
            self.assertIn("selected location", health["detail"])

    def test_proven_charm_break_crosses_desktop_boundary_once(self):
        with tempfile.TemporaryDirectory() as root:
            engine = HeadlessEngine(data_dir=root)
            try:
                for line in (
                    "[Fri Aug 07 20:00:00 2026] You begin casting Charm.",
                    "[Fri Aug 07 20:00:01 2026] a rock golem has been charmed.",
                    "[Fri Aug 07 20:00:03 2026] Your Charm spell has worn off of a rock golem.",
                ):
                    engine.process_line(line)
                first = engine.snapshot_event(datetime(2026, 8, 7, 20, 0, 4))
                second = engine.snapshot_event(datetime(2026, 8, 7, 20, 0, 9))
            finally:
                engine.close()
            self.assertEqual(first["snapshot"]["alerts"][0]["kind"], "charmBreak")
            self.assertEqual(first["snapshot"]["alerts"][0]["target"], "A rock golem")
            self.assertEqual(second["snapshot"]["alerts"], [])

    def test_alert_preferences_control_general_log_alerts(self):
        with tempfile.TemporaryDirectory() as root:
            engine = HeadlessEngine(data_dir=root)
            try:
                engine.stats.character = "Spin"
                engine.set_alert_config({
                    "alertsEnabled": True, "alertTells": True,
                    "alertSeconds": 7})
                engine.process_line(
                    "[Fri Aug 07 20:00:00 2026] Friend tells you, 'Ready?'")
                event = engine.snapshot_event(datetime(2026, 8, 7, 20, 0, 1))
                engine.alerts.clear()
                engine.set_alert_config({"alertsEnabled": False})
                engine.process_line(
                    "[Fri Aug 07 20:00:02 2026] Friend tells you, 'Again?'")
                muted = engine.snapshot_event(datetime(2026, 8, 7, 20, 0, 3))
            finally:
                engine.close()
            self.assertEqual(event["snapshot"]["alerts"][0]["kind"], "tell_in")
            self.assertEqual(event["snapshot"]["alerts"][0]["severity"], "info")
            self.assertEqual(muted["snapshot"]["alerts"], [])

    def test_nearby_players_boss_kill_does_not_credit_weekly_ledger(self):
        with tempfile.TemporaryDirectory() as root:
            engine = HeadlessEngine(data_dir=root)
            try:
                engine.process_line(
                    "[Fri Aug 07 20:00:00 2026] Lord Nagafen has been slain by Stranger!")
                event = engine.snapshot_event(datetime(2026, 8, 7, 20, 0, 1))
            finally:
                engine.close()
            self.assertEqual(event["snapshot"]["weekly"]["completedCount"], 0)

    def test_group_killing_blow_prompts_when_self_engaged_the_raid_boss(self):
        with tempfile.TemporaryDirectory() as root:
            engine = HeadlessEngine(data_dir=root)
            try:
                engine.process_line(
                    "[Fri Aug 07 20:00:00 2026] You slash Cazic-Thule for 100 points of damage.")
                engine.process_line(
                    "[Fri Aug 07 20:00:01 2026] Cazic-Thule has been slain by AromeK!")
                event = engine.snapshot_event(datetime(2026, 8, 7, 20, 0, 2))
            finally:
                engine.close()
            weekly = event["snapshot"]["weekly"]
            self.assertEqual(weekly["pendingRaidTarget"], "Cazic-Thule")
            self.assertEqual(weekly["completedCount"], 0)

    def test_verified_group_instance_credits_support_participation(self):
        with tempfile.TemporaryDirectory() as root:
            engine = HeadlessEngine(data_dir=root)
            try:
                engine.stats.character = "Spin"
                for line in (
                    "[Fri Aug 07 20:00:00 2026] You have entered The Plane of Fear - Group 1 (Awakened).",
                    "[Fri Aug 07 20:00:01 2026] AromeK has joined the group.",
                    "[Fri Aug 07 20:00:02 2026] You healed AromeK for 100 hit points by Superior Healing.",
                    "[Fri Aug 07 20:00:03 2026] Cazic-Thule has been slain by AromeK!",
                ):
                    self.assertTrue(engine.process_line(line))
                weekly = engine.snapshot_event(
                    datetime(2026, 8, 7, 20, 0, 4))["snapshot"]["weekly"]
            finally:
                engine.close()
            cazic = next(row for row in weekly["raids"]
                         if row["target"] == "Cazic-Thule")
            self.assertTrue(cazic["difficulties"][1])
            self.assertEqual(weekly["kills"][0]["difficulty_source"],
                             "log-zone")

    def test_verified_group_instance_credits_recent_control_support(self):
        with tempfile.TemporaryDirectory() as root:
            engine = HeadlessEngine(data_dir=root)
            try:
                engine.stats.character = "Spin"
                for line in (
                    "[Fri Aug 07 20:00:00 2026] You have entered The Plane of Fear - Group 1 (Awakened).",
                    "[Fri Aug 07 20:00:01 2026] AromeK has joined the group.",
                    "[Fri Aug 07 20:00:02 2026] You begin casting Mesmerize.",
                    "[Fri Aug 07 20:00:03 2026] a fright has been mesmerized.",
                    "[Fri Aug 07 20:00:20 2026] Cazic-Thule has been slain by AromeK!",
                ):
                    self.assertTrue(engine.process_line(line))
                weekly = engine.snapshot_event(
                    datetime(2026, 8, 7, 20, 0, 21))["snapshot"]["weekly"]
            finally:
                engine.close()
            cazic = next(row for row in weekly["raids"]
                         if row["target"] == "Cazic-Thule")
            self.assertTrue(cazic["difficulties"][1])

    def test_verified_group_instance_requires_recent_participation(self):
        with tempfile.TemporaryDirectory() as root:
            engine = HeadlessEngine(data_dir=root)
            try:
                engine.stats.character = "Spin"
                for line in (
                    "[Fri Aug 07 20:00:00 2026] You have entered The Plane of Fear - Group 1 (Awakened).",
                    "[Fri Aug 07 20:00:01 2026] AromeK has joined the group.",
                    "[Fri Aug 07 20:03:00 2026] Cazic-Thule has been slain by AromeK!",
                ):
                    self.assertTrue(engine.process_line(line))
                weekly = engine.snapshot_event(
                    datetime(2026, 8, 7, 20, 3, 1))["snapshot"]["weekly"]
            finally:
                engine.close()
            self.assertEqual(weekly["completedCount"], 0)

    def test_open_world_support_line_remains_too_ambiguous_for_credit(self):
        with tempfile.TemporaryDirectory() as root:
            engine = HeadlessEngine(data_dir=root)
            try:
                engine.stats.character = "Spin"
                self.assertTrue(engine.set_raid_difficulty(1))
                for line in (
                    "[Fri Aug 07 20:00:00 2026] You have entered The Plane of Fear.",
                    "[Fri Aug 07 20:00:01 2026] AromeK has joined the group.",
                    "[Fri Aug 07 20:00:02 2026] You healed AromeK for 100 hit points by Superior Healing.",
                    "[Fri Aug 07 20:00:03 2026] Cazic-Thule has been slain by AromeK!",
                ):
                    self.assertTrue(engine.process_line(line))
                weekly = engine.snapshot_event(
                    datetime(2026, 8, 7, 20, 0, 4))["snapshot"]["weekly"]
            finally:
                engine.close()
            self.assertEqual(weekly["completedCount"], 0)

    def test_boss_kill_waits_for_explicit_difficulty(self):
        with tempfile.TemporaryDirectory() as root:
            engine = HeadlessEngine(data_dir=root)
            try:
                engine.process_line(
                    "[Fri Aug 07 20:00:00 2026] You have slain Lady Vox!")
                pending = engine.snapshot_event(datetime(2026, 8, 7, 20, 0, 1))
                self.assertEqual(
                    pending["snapshot"]["weekly"]["pendingRaidTarget"], "Lady Vox")
                self.assertEqual(
                    pending["snapshot"]["weekly"]["completedCount"], 0)
                self.assertTrue(engine.set_raid_difficulty(4))
                credited = engine.snapshot_event(datetime(2026, 8, 7, 20, 0, 2))
            finally:
                engine.close()
            self.assertEqual(credited["snapshot"]["weekly"]["completedCount"], 1)

    def test_manual_confirmation_preserves_original_kill_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            engine = HeadlessEngine(data_dir=root)
            try:
                engine.stats.character = "Spin"
                engine.process_line(
                    "[Mon Aug 10 07:59:58 2026] You have entered Permafrost Keep.")
                engine.process_line(
                    "[Mon Aug 10 07:59:59 2026] You have slain Lady Vox!")
                engine.process_line(
                    "[Tue Aug 11 09:00:00 2026] You have entered The Nexus.")
                engine.stats.character = "Other"
                self.assertTrue(engine.set_raid_difficulty(4))
                kill = engine.weekly._kills[0]
            finally:
                engine.close()
            self.assertEqual(kill.character, "Spin")
            self.assertEqual(kill.zone, "Permafrost Keep")
            expected_stamp = datetime(2026, 8, 10, 7, 59, 59).astimezone(
                timezone.utc).isoformat(timespec="seconds").replace(
                    "+00:00", "Z")
            self.assertEqual(kill.killed_at, expected_stamp)
            self.assertEqual(kill.evidence, "You have slain Lady Vox!")

    def test_journal_write_failure_does_not_stop_combat_parser(self):
        with tempfile.TemporaryDirectory() as root:
            engine = HeadlessEngine(data_dir=root)
            try:
                engine.stats.character = "Spin"
                assert engine.journal is not None
                engine.journal._connection.execute("DROP TABLE loot_events")
                self.assertTrue(engine.process_line(
                    "[Fri Aug 07 20:00:00 2026] --You have looted a Ruby from a dragon's corpse.--"))
                self.assertIsNone(engine.journal)
                self.assertTrue(engine.process_line(
                    "[Fri Aug 07 20:00:01 2026] You slash a dragon for 100 points of damage."))
                snapshot = engine.snapshot_event(
                    datetime(2026, 8, 7, 20, 0, 2))["snapshot"]
            finally:
                engine.close()
            self.assertEqual(snapshot["combat"]["fightDamage"], 100)
            self.assertEqual(snapshot["loot"], ())


if __name__ == "__main__":
    unittest.main()
