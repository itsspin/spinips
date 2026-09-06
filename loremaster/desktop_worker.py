#!/usr/bin/env python3
"""Headless Loremaster engine for the Electron desktop application.

stdin accepts one JSON object per line. stdout emits JSON events only; human
diagnostics go to stderr.  This intentionally small boundary lets Electron own
windows, settings and animation while the proven parser remains authoritative.
"""

from __future__ import annotations

import json
import importlib.util
import os
from pathlib import Path
import queue
import sys
import threading
import time
from datetime import datetime, timedelta

from adventure_journal import (AdventureJournal, encounter_identity,
                               evidence_hash)
from control_snapshot import merge_control_snapshots
from engine_protocol import build_engine_snapshot, snapshot_event
try:
    # Script/PyInstaller import used by the production worker.
    from loremaster import (LogWatcher, SessionStats, apply_log_models,
                            check_alerts, normalize_composition, parse_line)
except (ImportError, AttributeError):
    # ``python -m unittest loremaster.tests...`` reserves ``loremaster`` as a
    # namespace package. Load the runtime file under a private name so worker
    # tests exercise the exact same implementation without import ambiguity.
    runtime_path = Path(__file__).resolve().with_name("loremaster.py")
    runtime_spec = importlib.util.spec_from_file_location(
        "_loremaster_runtime", runtime_path)
    if runtime_spec is None or runtime_spec.loader is None:
        raise ImportError(f"Could not load Loremaster runtime at {runtime_path}")
    runtime_module = importlib.util.module_from_spec(runtime_spec)
    sys.modules[runtime_spec.name] = runtime_module
    runtime_spec.loader.exec_module(runtime_module)
    LogWatcher = runtime_module.LogWatcher
    SessionStats = runtime_module.SessionStats
    apply_log_models = runtime_module.apply_log_models
    check_alerts = runtime_module.check_alerts
    parse_line = runtime_module.parse_line
    normalize_composition = runtime_module.normalize_composition
from lull_timer import LullTracker
from mez_timer import MezTracker
from raid_context import RaidContextTracker
from weekly_tracker import DIFFICULTIES, WeeklyBossTracker


SNAPSHOT_INTERVAL_SECONDS = 0.25
POLL_INTERVAL_SECONDS = 0.08
SUPPORT_CREDIT_WINDOW = timedelta(seconds=120)

# JSONL is an explicit UTF-8 protocol even on Windows systems whose console
# code page is still cp1252. Electron decodes the worker stream as UTF-8.
if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8", errors="strict")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="strict")


class HeadlessEngine:
    """One deterministic parser session with replaceable log input."""

    def __init__(self, *, log_path: str = "", data_dir: str | Path | None = None,
                 now: datetime | None = None) -> None:
        self.data_dir = Path(data_dir or os.environ.get(
            "LOREMASTER_APP_DATA_DIR", Path.cwd()))
        self.weekly = WeeklyBossTracker(
            storage_path=self.data_dir / "weekly_boss_kills.json")
        self.journal: AdventureJournal | None = None
        self.journal_error = ""
        try:
            self.journal = AdventureJournal(
                self.data_dir / "adventure_journal.sqlite3")
            if not self.journal.available:
                self.journal_error = self.journal.last_error
        except Exception as error:
            # Combat parsing is the primary feature. A read-only directory,
            # newer journal schema, or damaged local history must never stop
            # DPS/CC/alert processing.
            self.journal_error = f"{type(error).__name__}: {error}"
        self._journal_cache_dirty = True
        self._recent_loot: list[dict] = []
        self._recent_journal_encounters: list[dict] = []
        self._loot_summary: dict[str, int] = {
            "events": 0, "quantity": 0, "unique_items": 0}
        self._persisted_encounter_ids: set[str] = set()
        self._loot_evidence_counts: dict[str, int] = {}
        self._loot_evidence_stamp = ""
        self.raid_context = RaidContextTracker()
        self.raid_difficulty: int | None = None
        self.configured_composition = ""
        self.pending_raid_target = ""
        self.pending_raid_seconds = 0.0
        self.pending_raid_occurred_at: datetime | None = None
        self.pending_raid_zone = ""
        self.pending_raid_character = ""
        self.pending_raid_evidence = ""
        self.sequence = 0
        self.stats = SessionStats()
        self.mez = MezTracker()
        self.lull = LullTracker()
        self.alerts: list[dict] = []
        self.alert_config = {
            "alerts_enabled": True,
            "alert_sound": True,
            "alert_seconds": 5,
            "alert_charm_break": True,
            "alert_tells": True,
            "alert_summon": True,
            "alert_death": True,
            "alert_big_hit": True,
            "alert_name_called": True,
            "big_hit_threshold": 800,
            "mez_timers_enabled": True,
            "mez_warning_seconds": 10,
            "lull_timers_enabled": True,
            "lull_warning_seconds": 12,
            "custom_alerts": [],
        }
        self.watcher: LogWatcher
        self.configured_path = ""
        self.set_log_path(log_path)
        self.last_observed_at = now or datetime.now()
        if self.journal is not None and not self.journal.available:
            self.journal.close()
            self.journal = None

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.astimezone()

    def _disable_journal(self, error: BaseException | str) -> None:
        self.journal_error = (str(error) if isinstance(error, str) else
                              f"{type(error).__name__}: {error}")
        journal, self.journal = self.journal, None
        if journal is not None:
            try:
                journal.close()
            except Exception:
                pass
        self._recent_loot = []
        self._recent_journal_encounters = []
        self._loot_summary = {
            "events": 0, "quantity": 0, "unique_items": 0}
        self._journal_cache_dirty = False

    def _clear_pending_raid_kill(self) -> None:
        self.pending_raid_target = ""
        self.pending_raid_seconds = 0.0
        self.pending_raid_occurred_at = None
        self.pending_raid_zone = ""
        self.pending_raid_character = ""
        self.pending_raid_evidence = ""

    def _ensure_fight_journal_identity(self, fight) -> str:
        encounter_id = str(getattr(fight, "journal_id", "") or "")
        if encounter_id:
            return encounter_id
        encounter_id = encounter_identity(
            character=self.stats.character, server=self.watcher.server,
            started_at=fight.start,
            # Capture the initial title once. It improves deterministic
            # uniqueness for fights that begin in the same log second, while
            # the stored ID remains unchanged as later targets evolve.
            name=str(getattr(fight, "name", "fight") or "fight"))
        fight.journal_id = encounter_id
        tier, mode = self._raid_context_values()
        fight.journal_raid_tier = tier
        fight.journal_raid_mode = mode
        return encounter_id

    def set_log_path(self, value: str) -> None:
        old = getattr(self, "watcher", None)
        if old is not None:
            old.close()
        cleaned = str(value or "").strip()
        candidate = Path(cleaned) if cleaned else None
        explicit = str(candidate) if candidate and candidate.suffix.casefold() == ".txt" else None
        directory = str(candidate) if candidate and explicit is None else None
        self.watcher = LogWatcher(directory, explicit)
        self.configured_path = cleaned
        self.raid_context.clear()
        # A confirmation belongs to the log/character that produced it. Do
        # not let a path switch apply an old pending kill to another log.
        self._clear_pending_raid_kill()

    def reset(self) -> None:
        character = self.stats.character
        composition = self.stats.composition
        self.stats = SessionStats(character, composition=composition)
        self.mez.clear()
        self.lull.clear()
        self.alerts.clear()
        self._persisted_encounter_ids.clear()
        self._clear_pending_raid_kill()

    def set_alert_config(self, value) -> None:
        if not isinstance(value, dict):
            return
        mapping = {
            "alertsEnabled": "alerts_enabled",
            "alertSound": "alert_sound",
            "alertSeconds": "alert_seconds",
            "alertCharmBreak": "alert_charm_break",
            "alertTells": "alert_tells",
            "alertSummon": "alert_summon",
            "alertDeath": "alert_death",
            "alertBigHit": "alert_big_hit",
            "alertNameCalled": "alert_name_called",
            "bigHitThreshold": "big_hit_threshold",
            "mezTimersEnabled": "mez_timers_enabled",
            "mezWarningSeconds": "mez_warning_seconds",
            "lullTimersEnabled": "lull_timers_enabled",
            "lullWarningSeconds": "lull_warning_seconds",
        }
        for source, target in mapping.items():
            if source in value:
                self.alert_config[target] = value[source]
        for key, low, high in (
                ("alert_seconds", 1, 15),
                ("big_hit_threshold", 1, 999999),
                ("mez_warning_seconds", 3, 30),
                ("lull_warning_seconds", 3, 30)):
            try:
                self.alert_config[key] = max(
                    low, min(high, int(self.alert_config[key])))
            except (TypeError, ValueError):
                pass

    def set_raid_difficulty(self, value: int | None) -> bool:
        if value is not None and value not in DIFFICULTIES:
            return False
        self.raid_difficulty = value
        if value is not None and self.pending_raid_target:
            self.weekly.observe_kill(
                self.pending_raid_occurred_at or self.last_observed_at,
                self.pending_raid_target,
                zone=self.pending_raid_zone,
                character=self.pending_raid_character,
                difficulty=value, duration_seconds=self.pending_raid_seconds,
                difficulty_source="manual-confirmation",
                evidence=self.pending_raid_evidence)
            self._clear_pending_raid_kill()
        return True

    def set_composition(self, value: str) -> bool:
        cleaned = str(value or "").strip()
        if not cleaned:
            self.configured_composition = ""
            if self.stats.composition_source == "desktop setting":
                self.stats.composition = ""
                self.stats.composition_source = "unset"
            return True
        try:
            canonical = normalize_composition(cleaned)
        except ValueError:
            return False
        self.configured_composition = canonical
        self.stats.set_composition(canonical, source="desktop setting")
        return True

    def set_raid_completion(self, target: str, difficulty: int,
                            completed: bool) -> bool:
        return self.weekly.set_completion(
            self.last_observed_at, target, difficulty,
            character=self.stats.character, completed=completed)

    def _effective_raid_difficulty(self) -> int | None:
        context = self.raid_context.active
        return context.difficulty if context is not None else self.raid_difficulty

    def _switch_character(self) -> None:
        context = self.watcher.recent_context()
        composition = str(self.configured_composition
                          or context.get("composition", ""))
        self.stats = SessionStats(
            self.watcher.character or "?", composition=composition)
        self.stats.zone = str(context.get("zone", ""))
        self.stats.group_members = set(context.get("group_members", ()))
        if self.stats.zone:
            self.stats.zones.append(self.stats.zone)
            self.raid_context.observe_zone(
                self.stats.zone,
                occurred_at=context.get("zone_observed_at"),
                evidence=str(context.get("zone_evidence") or
                             f"You have entered {self.stats.zone}."))
        else:
            self.raid_context.clear()
        self.mez.clear()
        self.lull.clear()
        self._journal_cache_dirty = True
        self._persisted_encounter_ids.clear()
        self._loot_evidence_counts.clear()
        self._loot_evidence_stamp = ""
        self._clear_pending_raid_kill()

    def _raid_context_values(self) -> tuple[int | None, str]:
        """Read automatic or manual raid context without coupling trackers."""
        context = self.raid_context.active
        return (
            self._effective_raid_difficulty(),
            str(context.mode if context is not None else ""),
        )

    def _encounter_id_near(self, occurred_at: datetime) -> str:
        fight = self.stats.fight
        if fight is None and self.stats.fights:
            recent = self.stats.fights[-1]
            if abs((occurred_at - recent.end).total_seconds()) <= 120:
                fight = recent
        if fight is None:
            return ""
        return self._ensure_fight_journal_identity(fight)

    def _observe_loot(self, *, occurred_at: datetime, kind: str,
                      groups: dict, evidence: str) -> None:
        if kind not in {"loot", "loot2", "loot_storage", "loot_auto_sale",
                        "loot_merge", "loot_upgrade", "loot_inventory"}:
            return
        item = str(groups.get("item") or "").strip()
        if not item:
            return
        try:
            quantity = max(1, int(groups.get("qty") or 1))
        except (TypeError, ValueError):
            quantity = 1
        looter = str(groups.get("who") or self.stats.character or "You")
        acquisition_type = {
            "loot": "corpse",
            "loot2": "observed-group",
            "loot_storage": "stored-" + str(
                groups.get("destination") or "storage").casefold().replace(" ", "-"),
            "loot_auto_sale": "auto-sold",
            "loot_merge": "merged",
            "loot_upgrade": "upgraded-loot",
            "loot_inventory": "inventory-placement",
        }[kind]
        occurred_stamp = occurred_at.isoformat()
        if occurred_stamp != self._loot_evidence_stamp:
            # EQ logs are chronological. Only identical loot messages within
            # one timestamp need ordinals, so never retain an unbounded map for
            # a long-running session.
            self._loot_evidence_counts.clear()
            self._loot_evidence_stamp = occurred_stamp
        evidence_key = evidence_hash(
            occurred_stamp, self.stats.character.casefold(),
            self.watcher.server.casefold(), evidence)
        ordinal = self._loot_evidence_counts.get(evidence_key, 0) + 1
        self._loot_evidence_counts[evidence_key] = ordinal
        event_id = "loot-" + evidence_hash(evidence_key, ordinal)[:24]
        tier, mode = self._raid_context_values()
        if self.journal is None:
            return
        try:
            write = self.journal.record_loot(
                occurred_at=occurred_at, item=item, quantity=quantity,
                looter=looter, source=str(groups.get("source") or ""),
                zone=self.stats.zone, character=self.stats.character,
                server=self.watcher.server,
                encounter_id=self._encounter_id_near(occurred_at),
                acquisition_type=acquisition_type, raid_tier=tier,
                raid_mode=mode, evidence=evidence, event_id=event_id)
        except Exception as error:
            self._disable_journal(error)
            return
        if not self.journal.available:
            self._disable_journal(self.journal.last_error or
                                  "Adventure journal became unavailable")
            return
        if write.inserted:
            self._journal_cache_dirty = True

    def _persist_completed_encounters(self, stats_snapshot: dict) -> None:
        if self.journal is None:
            return
        fights = tuple(stats_snapshot.get("fights") or ())
        if not fights:
            return
        active_index = (len(fights) - 1 if stats_snapshot.get("in_combat")
                        else -1)
        wrote = False
        visible_ids: set[str] = set()
        for index, fight in enumerate(fights):
            if index == active_index:
                continue
            start = getattr(fight, "start", None)
            end = getattr(fight, "end", start)
            if not isinstance(start, datetime):
                continue
            name = str(getattr(fight, "name", "fight") or "fight")
            seconds = max(1.0, float(getattr(fight, "seconds", 0.0) or 0.0))
            damage = max(0, int(getattr(fight, "damage", 0) or 0))
            encounter_id = self._ensure_fight_journal_identity(fight)
            visible_ids.add(encounter_id)
            if encounter_id in self._persisted_encounter_ids:
                continue
            tier = getattr(fight, "journal_raid_tier", None)
            mode = str(getattr(fight, "journal_raid_mode", "") or "")
            zone = str(getattr(fight, "zone", "") or "")
            details = {
                "composition": str(getattr(fight, "composition", "") or ""),
                "personalDamage": max(
                    0, damage
                    - int(getattr(fight, "charmed_pet_damage", 0) or 0)
                    - int(getattr(fight, "summoned_pet_damage", 0) or 0)),
                "charmedPetDamage": int(
                    getattr(fight, "charmed_pet_damage", 0) or 0),
                "summonedPetDamage": int(
                    getattr(fight, "summoned_pet_damage", 0) or 0),
                "damageTaken": int(getattr(fight, "damage_taken", 0) or 0),
                "healingDone": int(getattr(fight, "healing_done", 0) or 0),
                "healsReceived": int(getattr(fight, "heals_received", 0) or 0),
                "crits": int(getattr(fight, "crits", 0) or 0),
                "misses": int(getattr(fight, "misses", 0) or 0),
            }
            try:
                write = self.journal.record_encounter(
                    encounter_id=encounter_id, started_at=start, ended_at=end,
                    name=name, character=self.stats.character,
                    server=self.watcher.server, zone=zone,
                    raid_tier=tier, raid_mode=mode, seconds=seconds,
                    damage=damage, dps=int(round(damage / seconds)),
                    kills=int(getattr(fight, "kills", 0) or 0), details=details)
            except Exception as error:
                self._disable_journal(error)
                return
            if not self.journal.available:
                self._disable_journal(self.journal.last_error or
                                      "Adventure journal became unavailable")
                return
            self._persisted_encounter_ids.add(encounter_id)
            wrote = wrote or write.inserted
        if wrote:
            self._journal_cache_dirty = True
        self._persisted_encounter_ids.intersection_update(visible_ids)

    def _journal_snapshot(self) -> tuple[list[dict], list[dict], dict[str, int]]:
        if self.journal is None:
            return (self._recent_loot, self._recent_journal_encounters,
                    self._loot_summary)
        if self._journal_cache_dirty:
            character = (self.stats.character or "").strip()
            if character == "?":
                character = ""
            try:
                self._recent_loot = self.journal.recent_loot(
                    limit=100, character=character)
                self._recent_journal_encounters = self.journal.recent_encounters(
                    limit=60, character=character)
                self._loot_summary = self.journal.loot_summary(
                    character=character)
            except Exception as error:
                self._disable_journal(error)
                return (self._recent_loot, self._recent_journal_encounters,
                        self._loot_summary)
            if not self.journal.available:
                self._disable_journal(self.journal.last_error or
                                      "Adventure journal became unavailable")
                return (self._recent_loot, self._recent_journal_encounters,
                        self._loot_summary)
            self._journal_cache_dirty = False
        return (self._recent_loot, self._recent_journal_encounters,
                self._loot_summary)

    def query_loot(self, filters: dict) -> dict:
        if self.journal is None:
            return {"rows": [], "total": 0, "offset": 0, "hasMore": False}
        character = (self.stats.character or "").strip()
        if character == "?":
            character = ""
        try:
            result = self.journal.query_loot(
                query=str(filters.get("query") or ""),
                zone=str(filters.get("zone") or ""),
                raid_tier=filters.get("raidTier", "all"),
                scope=str(filters.get("scope") or "all"),
                character=character,
                offset=int(filters.get("offset") or 0),
                limit=int(filters.get("limit") or 100))
        except Exception as error:
            self._disable_journal(error)
            return {"rows": [], "total": 0, "offset": 0, "hasMore": False}
        if not self.journal.available:
            self._disable_journal(self.journal.last_error or
                                  "Adventure journal became unavailable")
            return {"rows": [], "total": 0, "offset": 0, "hasMore": False}
        rows = []
        for source in result.get("rows", ()):
            row = dict(source)
            for old, new in (
                    ("event_id", "eventId"),
                    ("occurred_at", "occurredAt"),
                    ("item_key", "itemKey"),
                    ("encounter_id", "encounterId"),
                    ("acquisition_type", "acquisitionType"),
                    ("raid_tier", "raidTier"),
                    ("raid_mode", "raidMode"),
                    ("item_info", "itemInfo")):
                row[new] = row.pop(old, None)
            info = row.get("itemInfo")
            if isinstance(info, dict):
                info = dict(info)
                info.pop("fresh_until", None)
                info.pop("fetched_at", None)
                info["freshness"] = "cached"
                info.pop("source", None)
                row["itemInfo"] = info
            rows.append(row)
        return {
            "rows": rows,
            "total": max(0, int(result.get("total") or 0)),
            "offset": max(0, int(result.get("offset") or 0)),
            "hasMore": bool(result.get("has_more")),
        }

    def cache_item(self, value: dict) -> bool:
        title = str(value.get("title") or "").strip()
        if not title:
            return False
        if self.journal is None:
            return False
        now = datetime.now().astimezone()
        try:
            self.journal.put_item_cache(
                title=title, url=str(value.get("url") or ""),
                stats=value.get("stats"), notes=value.get("notes"),
                sections=value.get("sections"), source="EQL Wiki",
                fetched_at=now, fresh_until=now + timedelta(days=7))
        except Exception as error:
            self._disable_journal(error)
            return False
        if not self.journal.available:
            self._disable_journal(self.journal.last_error or
                                  "Adventure journal became unavailable")
            return False
        self._journal_cache_dirty = True
        return True

    def process_line(self, line: str) -> bool:
        parsed = parse_line(line)
        if parsed is None:
            return False
        occurred_at, kind, groups = parsed
        self.last_observed_at = occurred_at
        charm_breaks = apply_log_models(
            self.stats, self.mez, occurred_at, kind, groups,
            lull_tracker=self.lull, caster_level=self.stats.level)
        # Bind immutable identity and context at encounter creation. A later
        # target discovery or zone transition must not rewrite journal links.
        if self.stats.fight is not None:
            self._ensure_fight_journal_identity(self.stats.fight)
        raw_message = line.split("] ", 1)[1] if "] " in line else line
        self._observe_loot(
            occurred_at=occurred_at, kind=kind, groups=groups,
            evidence=raw_message)
        if kind == "zone":
            self.raid_context.observe_zone(
                groups.get("zone", ""), occurred_at=occurred_at,
                evidence=raw_message)
        triggered = check_alerts(
            kind, groups, raw_message, self.stats.character,
            self.alert_config, charm_breaks)
        for offset, (severity, message) in enumerate(triggered):
            alert_kind = "charmBreak" if message.startswith("CHARM BROKE") else kind
            title, _, target = message.partition(" — ")
            if alert_kind == "charmBreak" and charm_breaks:
                target = charm_breaks[0].pet_name
            event_key = getattr(charm_breaks[0], "event_id", "") if charm_breaks else ""
            alert_id = (f"{alert_kind}-{event_key}" if event_key else
                        f"{alert_kind}-{occurred_at.timestamp():.3f}-{offset}")
            self.alerts.append({
                "id": alert_id,
                "kind": alert_kind,
                "severity": severity,
                "title": title,
                "target": target,
                "occurredAt": occurred_at.isoformat(timespec="milliseconds"),
                "expiresAt": (occurred_at + timedelta(
                    seconds=self.alert_config["alert_seconds"])).isoformat(
                        timespec="milliseconds"),
            })
        # A raid's killing blow is commonly attributed to a groupmate. Credit
        # it only when ownership is direct (self/pet) or this encounter
        # contains proven self/pet damage against the same tracked boss. That
        # captures real group clears without marking unrelated nearby kills.
        target = groups.get("target", "")
        raid_target = self.weekly.match_target(target)
        engaged_raid_target = bool(
            kind == "kill_other" and raid_target and self.stats.fight and
            any(
                damage > 0 and self.weekly.match_target(fight_target) == raid_target
                for fight_target, damage in self.stats.fight.targets.items()
            )
        )
        context = self.raid_context.active
        group_names = {
            member.casefold() for member in self.stats.group_members}
        support_times = tuple(
            stamp for stamp in (
                self.stats.last_own_action,
                self.stats.last_support_action,
            ) if stamp is not None)
        last_support = max(support_times) if support_times else None
        recent_support = bool(
            last_support is not None
            and timedelta(0) <= occurred_at - last_support
            <= SUPPORT_CREDIT_WINDOW)
        support_raid_credit = bool(
            kind == "kill_other" and raid_target and context is not None
            and context.mode.casefold() == "group" and recent_support
            and str(groups.get("killer") or "").casefold() in group_names)
        weekly_credit = (kind == "kill_you" or (
            kind == "kill_other" and (
                self.stats.is_pet(groups.get("killer", "")) or
                engaged_raid_target or support_raid_credit)))
        if weekly_credit:
            if raid_target:
                difficulty = self._effective_raid_difficulty()
                if difficulty is None:
                    self.pending_raid_target = raid_target.name
                    self.pending_raid_seconds = (
                        self.stats.fight.seconds if self.stats.fight else 0.0)
                    self.pending_raid_occurred_at = occurred_at
                    self.pending_raid_zone = self.stats.zone
                    self.pending_raid_character = self.stats.character
                    self.pending_raid_evidence = raw_message
                else:
                    self.weekly.observe_kill(
                        occurred_at, raid_target.name,
                        zone=(context.zone if context is not None
                              else self.stats.zone),
                        character=self.stats.character,
                        difficulty=difficulty,
                        duration_seconds=(self.stats.fight.seconds
                                          if self.stats.fight else 0.0),
                        **(context.kill_evidence() if context is not None else {
                            "difficulty_source": "manual",
                            "evidence": raw_message,
                        }))
        return True

    def poll(self) -> tuple[int, bool]:
        lines, switched = self.watcher.poll()
        if switched:
            self._switch_character()
        parsed = sum(1 for line in lines if self.process_line(line))
        return parsed, switched

    def health(self) -> dict:
        active = self.watcher.path
        if active is not None:
            state = "live"
            detail = f"Reading {active.name}"
        elif self.configured_path:
            state = "searching"
            detail = "Waiting for an EverQuest log in the selected location"
        else:
            state = "searching"
            detail = "Searching common EverQuest Legends log locations"
        return {
            "state": state,
            "detail": detail,
            "configuredPath": self.configured_path,
            "activeLogPath": str(active) if active else "",
            "character": self.stats.character,
            "server": self.watcher.server,
        }

    def snapshot_event(self, now: datetime | None = None) -> dict:
        observed_at = now or datetime.now()
        stats = self.stats.snapshot(observed_at)
        self._persist_completed_encounters(stats)
        recent_loot, journal_encounters, loot_summary = self._journal_snapshot()
        mez_snapshot = self.mez.snapshot(
            observed_at,
            warning_seconds=self.alert_config["mez_warning_seconds"])
        lull_snapshot = self.lull.snapshot(
            observed_at,
            warning_seconds=self.alert_config["lull_warning_seconds"])
        controls = merge_control_snapshots(
            mez_snapshot, lull_snapshot, limit=6,
            include_mez=bool(self.alert_config.get("mez_timers_enabled", True)),
            include_lull=bool(self.alert_config.get("lull_timers_enabled", True)))
        self.sequence += 1
        snapshot = build_engine_snapshot(
            sequence=self.sequence, observed_at=observed_at,
            stats_snapshot=stats, control_snapshot=controls,
            recent_loot=recent_loot,
            journal_encounters=journal_encounters,
            loot_summary=loot_summary)
        event = snapshot_event(snapshot).to_dict()
        event["snapshot"]["progression"] = self.stats.progression.snapshot(observed_at)
        weekly_character = (self.stats.character or "").strip()
        weekly = self.weekly.snapshot(
            observed_at,
            character="" if weekly_character in ("", "?") else weekly_character)
        active_difficulty = self._effective_raid_difficulty()
        context = self.raid_context.active
        weekly["activeDifficulty"] = active_difficulty
        weekly["configuredDifficulty"] = self.raid_difficulty
        weekly["difficultySource"] = (
            "log-zone" if context is not None else
            "manual" if self.raid_difficulty is not None else "unknown")
        weekly["raidContext"] = self.raid_context.snapshot()
        weekly["pendingRaidTarget"] = self.pending_raid_target
        event["snapshot"]["weekly"] = weekly
        self.alerts = [alert for alert in self.alerts
                       if self._aware(datetime.fromisoformat(alert["expiresAt"]))
                       > self._aware(observed_at)]
        event["snapshot"]["alerts"] = list(self.alerts)
        return event

    def close(self) -> None:
        self.watcher.close()
        if self.journal is not None:
            self.journal.close()


class JsonLineWorker:
    def __init__(self) -> None:
        self.commands: queue.Queue[dict] = queue.Queue()
        self.stopping = False
        self.engine = HeadlessEngine(
            data_dir=os.environ.get("LOREMASTER_APP_DATA_DIR"))
        self._reader = threading.Thread(
            target=self._read_commands, name="LoremasterDesktopCommands",
            daemon=True)

    @staticmethod
    def emit(payload: dict) -> None:
        sys.stdout.write(json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        sys.stdout.flush()

    def _read_commands(self) -> None:
        for raw in sys.stdin:
            try:
                value = json.loads(raw)
                if isinstance(value, dict):
                    self.commands.put(value)
            except json.JSONDecodeError as exc:
                print(f"invalid desktop command: {exc}", file=sys.stderr)
        self.commands.put({"type": "engine.shutdown"})

    def _handle(self, command: dict) -> None:
        kind = command.get("type")
        if kind == "engine.initialize":
            if not self.engine.set_composition(str(command.get("composition") or "")):
                raise ValueError("composition must contain exactly three valid classes")
            self.engine.set_log_path(str(command.get("logPath") or ""))
            self.engine.set_raid_difficulty(command.get("raidDifficulty"))
            self.engine.set_alert_config(command.get("alertConfig"))
        elif kind == "engine.set-log-path":
            self.engine.set_log_path(str(command.get("logPath") or ""))
        elif kind == "engine.set-raid-difficulty":
            if not self.engine.set_raid_difficulty(command.get("raidDifficulty")):
                raise ValueError("raidDifficulty must be null or an integer from 0 to 4")
        elif kind == "engine.set-alert-config":
            self.engine.set_alert_config(command.get("alertConfig"))
        elif kind == "engine.xp-checkpoint":
            self.engine.stats.progression.checkpoint(
                datetime.now(), command.get("level"), command.get("percent"))
        elif kind == "engine.set-composition":
            if not self.engine.set_composition(str(command.get("composition") or "")):
                raise ValueError("composition must contain exactly three valid classes")
        elif kind == "engine.set-raid-completion":
            self.engine.set_raid_completion(
                str(command.get("target") or ""),
                int(command.get("difficulty", -1)),
                bool(command.get("completed")))
        elif kind == "engine.query-loot":
            filters = command.get("filters")
            self.emit({
                "protocolVersion": 1,
                "eventType": "engine.loot-query",
                "requestId": str(command.get("requestId") or ""),
                "result": self.engine.query_loot(
                    filters if isinstance(filters, dict) else {}),
            })
        elif kind == "engine.cache-item":
            item = command.get("item")
            if isinstance(item, dict):
                self.engine.cache_item(item)
        elif kind == "engine.reset":
            self.engine.reset()
        elif kind == "engine.shutdown":
            self.stopping = True
        else:
            self.emit({
                "protocolVersion": 1,
                "eventType": "engine.error",
                "recoverable": True,
                "message": f"Unsupported command: {kind}",
            })

    def run(self) -> int:
        self._reader.start()
        self.emit({
            "protocolVersion": 1,
            "eventType": "engine.ready",
            "pid": os.getpid(),
            "health": self.engine.health(),
        })
        next_snapshot = 0.0
        last_health = ""
        try:
            while not self.stopping:
                while True:
                    try:
                        self._handle(self.commands.get_nowait())
                    except queue.Empty:
                        break
                self.engine.poll()
                health = self.engine.health()
                encoded_health = json.dumps(health, sort_keys=True)
                if encoded_health != last_health:
                    self.emit({
                        "protocolVersion": 1,
                        "eventType": "engine.health",
                        "health": health,
                    })
                    last_health = encoded_health
                monotonic_now = time.monotonic()
                if monotonic_now >= next_snapshot:
                    self.emit(self.engine.snapshot_event())
                    next_snapshot = monotonic_now + SNAPSHOT_INTERVAL_SECONDS
                time.sleep(POLL_INTERVAL_SECONDS)
        except Exception as exc:
            self.emit({
                "protocolVersion": 1,
                "eventType": "engine.error",
                "recoverable": False,
                "message": f"{type(exc).__name__}: {exc}",
            })
            return 1
        finally:
            self.engine.close()
        return 0


def main() -> int:
    return JsonLineWorker().run()


if __name__ == "__main__":
    raise SystemExit(main())
