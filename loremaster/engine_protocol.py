"""Versioned, renderer-neutral Loremaster engine snapshots.

The Python parser remains the source of truth.  These frozen value objects are
the narrow boundary consumed by the Electron fixture preview and, later, a
supervised local worker connection.  No Tk widgets or mutable parser objects
cross this boundary.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
from typing import Any, Literal, TypeAlias


PROTOCOL_VERSION = 1
MAX_ACTOR_SOURCE_ROWS = 24

CombatAbilityCategory: TypeAlias = Literal[
    "melee",
    "spell",
    "dot",
    "proc",
    "damage_shield",
    "pet",
    "healing",
    "unknown",
]
_COMBAT_ABILITY_CATEGORIES = frozenset({
    "melee",
    "spell",
    "dot",
    "proc",
    "damage_shield",
    "pet",
    "healing",
    "unknown",
})


def classify_combat_ability_category(
        name: str, explicit_category: object = None,
        *, healing: bool = False) -> CombatAbilityCategory:
    """Return only categories supported by direct protocol evidence.

    Healing metrics have an unambiguous context. Damage metrics use the
    structured labels produced by the parser rather than guessing from an
    arbitrary spell or actor name. A canonical category supplied by a future
    parser is preserved, while malformed or unsupported values fail closed.
    """
    if healing:
        return "healing"
    if isinstance(explicit_category, str):
        normalized_category = explicit_category.strip().casefold()
        if normalized_category in _COMBAT_ABILITY_CATEGORIES:
            return normalized_category  # type: ignore[return-value]

    label = str(name or "").strip()
    normalized_label = label.casefold()
    if normalized_label == "melee":
        return "melee"
    if normalized_label == "spells" or normalized_label.startswith("spell: "):
        return "spell"
    if normalized_label.startswith("dot: "):
        return "dot"
    if normalized_label == "proc" or normalized_label.startswith("proc: "):
        return "proc"
    if normalized_label == "damage shield":
        return "damage_shield"
    if (normalized_label.startswith("pet (")
            and normalized_label.endswith(")")
            and len(label) > len("Pet ()")):
        return "pet"
    return "unknown"


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class CharacterView:
    name: str
    level: int
    composition: str
    zone: str


@dataclass(frozen=True)
class CombatView:
    active: bool
    auto_attack: bool
    encounter_name: str
    fight_dps: int
    session_dps: int
    personal_damage: int
    charmed_pet_damage: int
    summoned_pet_damage: int
    fight_seconds: float
    fight_damage: int
    fight_personal_damage: int
    fight_charmed_pet_damage: int
    fight_summoned_pet_damage: int
    damage_taken: int
    healing_done: int
    kills: int
    crits: int
    misses: int


@dataclass(frozen=True)
class CombatMetricView:
    name: str
    total: int
    hits: int
    maximum: int
    category: CombatAbilityCategory = "unknown"


@dataclass(frozen=True)
class CombatHealingMetricView:
    name: str
    total: int
    hits: int
    maximum: int
    overheal: int
    category: CombatAbilityCategory = "healing"


@dataclass(frozen=True)
class EncounterTimelinePointView:
    second: int
    outgoing: int
    incoming: int
    healing: int
    kills: int


@dataclass(frozen=True)
class CombatBreakdownView:
    sources: tuple[CombatMetricView, ...]
    targets: tuple[CombatMetricView, ...]
    actors: tuple[CombatMetricView, ...]


@dataclass(frozen=True)
class CombatActorView:
    name: str
    role: str
    encounter_damage: int
    encounter_dps: int
    encounter_hits: int
    encounter_maximum: int
    session_damage: int
    session_dps: int
    session_hits: int
    session_maximum: int
    # Additive protocol-v1 detail. Older consumers can ignore this field;
    # source rows are bounded when snapshots are built.
    sources: tuple[CombatMetricView, ...] = ()


@dataclass(frozen=True)
class EncounterView:
    encounter_id: str
    name: str
    active: bool
    started_at: str
    ended_at: str
    seconds: float
    damage: int
    dps: int
    personal_damage: int
    charmed_pet_damage: int
    summoned_pet_damage: int
    damage_taken: int
    healing_done: int
    heals_received: int
    kills: int
    crits: int
    misses: int
    sources: tuple[CombatMetricView, ...]
    targets: tuple[CombatMetricView, ...]
    actors: tuple[CombatActorView, ...]
    healing_sources: tuple[CombatHealingMetricView, ...]
    timeline: tuple[EncounterTimelinePointView, ...]
    zone: str = ""
    raid_tier: int | None = None
    raid_mode: str = ""
    summary_only: bool = False


@dataclass(frozen=True)
class ItemInfoView:
    title: str
    url: str
    stats: tuple[str, ...]
    notes: tuple[str, ...]
    sections: dict[str, tuple[str, ...]]
    freshness: str


@dataclass(frozen=True)
class LootEventView:
    event_id: str
    occurred_at: str
    item: str
    item_key: str
    quantity: int
    looter: str
    source: str
    zone: str
    character: str
    server: str
    encounter_id: str
    acquisition_type: str
    raid_tier: int | None
    raid_mode: str
    item_info: ItemInfoView | None


@dataclass(frozen=True)
class JournalEncounterView:
    encounter_id: str
    started_at: str
    ended_at: str
    character: str
    server: str
    name: str
    zone: str
    raid_tier: int | None
    raid_mode: str
    seconds: float
    damage: int
    dps: int
    kills: int


@dataclass(frozen=True)
class ControlTimerView:
    kind: str
    state: str
    target: str
    count: int
    spell: str
    rank: int
    landed_at: str
    safe_expires_at: str
    expires_at: str
    duration_seconds: int
    safe_remaining_seconds: float
    remaining_seconds: float
    last_tick: bool
    urgency: str
    confidence: str
    ambiguity: str


@dataclass(frozen=True)
class EngineSnapshot:
    protocol_version: int
    sequence: int
    observed_at: str
    character: CharacterView
    group_members: tuple[str, ...]
    combat: CombatView
    breakdown: CombatBreakdownView
    encounters: tuple[EncounterView, ...]
    loot: tuple[LootEventView, ...]
    loot_event_count: int
    loot_total_count: int
    loot_unique_count: int
    journal_encounters: tuple[JournalEncounterView, ...]
    controls: tuple[ControlTimerView, ...]
    hidden_control_rows: int
    control_notice_count: int
    control_ambiguity_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(), ensure_ascii=False, separators=(",", ":"),
            sort_keys=True)


@dataclass(frozen=True)
class EngineEvent:
    protocol_version: int
    sequence: int
    occurred_at: str
    event_type: str
    snapshot: EngineSnapshot

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        # JSON-facing names stay stable and idiomatic for TypeScript.
        payload["protocolVersion"] = payload.pop("protocol_version")
        payload["occurredAt"] = payload.pop("occurred_at")
        payload["eventType"] = payload.pop("event_type")
        snapshot = payload["snapshot"]
        snapshot["protocolVersion"] = snapshot.pop("protocol_version")
        snapshot["observedAt"] = snapshot.pop("observed_at")
        snapshot["groupMembers"] = snapshot.pop("group_members")
        snapshot["hiddenControlRows"] = snapshot.pop("hidden_control_rows")
        snapshot["controlNoticeCount"] = snapshot.pop("control_notice_count")
        snapshot["controlAmbiguityCount"] = snapshot.pop(
            "control_ambiguity_count")
        snapshot["lootEventCount"] = snapshot.pop("loot_event_count")
        snapshot["lootTotalCount"] = snapshot.pop("loot_total_count")
        snapshot["lootUniqueCount"] = snapshot.pop("loot_unique_count")
        snapshot["journalEncounters"] = snapshot.pop("journal_encounters")
        for loot in snapshot["loot"]:
            for old, new in (
                    ("event_id", "eventId"),
                    ("occurred_at", "occurredAt"),
                    ("item_key", "itemKey"),
                    ("encounter_id", "encounterId"),
                    ("acquisition_type", "acquisitionType"),
                    ("raid_tier", "raidTier"),
                    ("raid_mode", "raidMode"),
                    ("item_info", "itemInfo")):
                loot[new] = loot.pop(old)
        for encounter in snapshot["journalEncounters"]:
            for old, new in (
                    ("encounter_id", "encounterId"),
                    ("started_at", "startedAt"),
                    ("ended_at", "endedAt"),
                    ("raid_tier", "raidTier"),
                    ("raid_mode", "raidMode")):
                encounter[new] = encounter.pop(old)
        for control in snapshot["controls"]:
            control["landedAt"] = control.pop("landed_at")
            control["safeExpiresAt"] = control.pop("safe_expires_at")
            control["expiresAt"] = control.pop("expires_at")
            control["durationSeconds"] = control.pop("duration_seconds")
            control["safeRemainingSeconds"] = control.pop(
                "safe_remaining_seconds")
            control["remainingSeconds"] = control.pop("remaining_seconds")
            control["lastTick"] = control.pop("last_tick")
        for encounter in snapshot["encounters"]:
            for old, new in (
                    ("encounter_id", "encounterId"),
                    ("started_at", "startedAt"),
                    ("ended_at", "endedAt"),
                    ("personal_damage", "personalDamage"),
                    ("charmed_pet_damage", "charmedPetDamage"),
                    ("summoned_pet_damage", "summonedPetDamage"),
                    ("damage_taken", "damageTaken"),
                    ("healing_done", "healingDone"),
                     ("heals_received", "healsReceived"),
                    ("healing_sources", "healingSources"),
                    ("raid_tier", "raidTier"),
                    ("raid_mode", "raidMode"),
                    ("summary_only", "summaryOnly")):
                encounter[new] = encounter.pop(old)
            for actor in encounter["actors"]:
                for old, new in (
                        ("encounter_damage", "encounterDamage"),
                        ("encounter_dps", "encounterDps"),
                        ("encounter_hits", "encounterHits"),
                        ("encounter_maximum", "encounterMaximum"),
                        ("session_damage", "sessionDamage"),
                        ("session_dps", "sessionDps"),
                        ("session_hits", "sessionHits"),
                        ("session_maximum", "sessionMaximum")):
                    actor[new] = actor.pop(old)
        combat = snapshot["combat"]
        for old, new in (
                ("auto_attack", "autoAttack"),
                ("encounter_name", "encounterName"),
                ("fight_dps", "fightDps"),
                ("session_dps", "sessionDps"),
                ("personal_damage", "personalDamage"),
                ("charmed_pet_damage", "charmedPetDamage"),
                ("summoned_pet_damage", "summonedPetDamage")):
            combat[new] = combat.pop(old)
        combat["fightSeconds"] = combat.pop("fight_seconds")
        for old, new in (
                ("fight_damage", "fightDamage"),
                ("fight_personal_damage", "fightPersonalDamage"),
                ("fight_charmed_pet_damage", "fightCharmedPetDamage"),
                ("fight_summoned_pet_damage", "fightSummonedPetDamage"),
                ("damage_taken", "damageTaken"),
                ("healing_done", "healingDone")):
            combat[new] = combat.pop(old)
        return payload

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(), ensure_ascii=False, separators=(",", ":"),
            sort_keys=True)


def build_engine_snapshot(*, sequence: int, observed_at: datetime,
                          stats_snapshot: dict,
                          control_snapshot, recent_loot=(),
                          journal_encounters=(), loot_summary=None
                          ) -> EngineSnapshot:
    """Copy mutable runtime dictionaries into one frozen boundary snapshot."""

    controls = tuple(ControlTimerView(
        kind=row.control_kind,
        state=row.timer_state,
        target=row.target_name,
        count=int(row.count),
        spell=row.spell_name,
        rank=int(row.rank),
        landed_at=_timestamp(row.landed_at),
        safe_expires_at=_timestamp(row.safe_expires_at),
        expires_at=_timestamp(row.expires_at),
        duration_seconds=int(row.duration_seconds),
        safe_remaining_seconds=round(float(row.safe_remaining_seconds), 3),
        remaining_seconds=round(float(row.remaining_seconds), 3),
        last_tick=bool(row.last_tick),
        urgency=row.urgency,
        confidence=row.confidence,
        ambiguity=row.ambiguity,
    ) for row in control_snapshot.rows)
    fight = stats_snapshot.get("fight")
    if isinstance(fight, dict):
        encounter_name = str(fight.get("name") or "")
        fight_dps = int(fight.get("dps") or 0)
        fight_seconds = float(fight.get("seconds") or 0.0)
        fight_damage = int(fight.get("damage") or 0)
        fight_charmed = int(fight.get("charmed_pet_damage") or 0)
        fight_summoned = int(fight.get("summoned_pet_damage") or 0)
        damage_taken = int(fight.get("damage_taken") or 0)
        healing_done = int(fight.get("healing_done") or 0)
        kills = int(fight.get("kills") or 0)
        crits = int(fight.get("crits") or 0)
        misses = int(fight.get("misses") or 0)
    else:
        encounter_name = str(getattr(fight, "name", "") or "")
        fight_dps = int(getattr(fight, "dps", 0) or 0)
        fight_seconds = float(getattr(fight, "seconds", 0.0) or 0.0)
        fight_damage = int(getattr(fight, "damage", 0) or 0)
        fight_charmed = int(getattr(fight, "charmed_pet_damage", 0) or 0)
        fight_summoned = int(getattr(fight, "summoned_pet_damage", 0) or 0)
        damage_taken = int(getattr(fight, "damage_taken", 0) or 0)
        healing_done = int(getattr(fight, "healing_done", 0) or 0)
        kills = int(getattr(fight, "kills", 0) or 0)
        crits = int(getattr(fight, "crits", 0) or 0)
        misses = int(getattr(fight, "misses", 0) or 0)

    def metric_rows(values, *, plain_totals=False, abilities=False,
                    categories=None,
                    limit=48) -> tuple[CombatMetricView, ...]:
        rows = []
        for name, value in (values or {}).items():
            if plain_totals:
                total, hits, maximum = int(value or 0), 0, 0
            else:
                total = int(value.get("t") or 0) if isinstance(value, dict) else int(value or 0)
                hits = int(value.get("h") or 0) if isinstance(value, dict) else 0
                maximum = int(value.get("max") or 0) if isinstance(value, dict) else 0
            explicit_category = (value.get("category")
                                 if isinstance(value, dict) else None)
            if explicit_category is None and isinstance(categories, dict):
                explicit_category = categories.get(name)
            category = (classify_combat_ability_category(
                str(name), explicit_category) if abilities else "unknown")
            rows.append(CombatMetricView(
                str(name), total, hits, maximum, category))
        return tuple(sorted(
            rows, key=lambda row: (-row.total, row.name.casefold()))[:limit])

    def healing_rows(values, *, limit=32) -> tuple[CombatHealingMetricView, ...]:
        rows = []
        for name, value in (values or {}).items():
            if isinstance(value, dict):
                rows.append(CombatHealingMetricView(
                    str(name), int(value.get("t") or 0),
                    int(value.get("h") or 0), int(value.get("max") or 0),
                    int(value.get("over") or 0),
                    classify_combat_ability_category(
                        str(name), value.get("category"), healing=True)))
        return tuple(sorted(
            rows, key=lambda row: (-row.total, row.name.casefold()))[:limit])

    def value_from(source, name: str, fallback=None):
        if isinstance(source, dict):
            return source.get(name, fallback)
        return getattr(source, name, fallback)

    def metric_values(values) -> dict[str, dict[str, int]]:
        normalized: dict[str, dict[str, int]] = {}
        for name, value in (values or {}).items():
            if isinstance(value, dict):
                normalized[str(name)] = {
                    "t": int(value.get("t") or 0),
                    "h": int(value.get("h") or 0),
                    "max": int(value.get("max") or 0),
                }
            else:
                normalized[str(name)] = {"t": int(value or 0), "h": 0, "max": 0}
        return normalized

    session_seconds = max(1.0, float(stats_snapshot.get("combat_seconds") or 0.0))
    session_actor_values = metric_values(stats_snapshot.get("actor_damage"))
    session_actor_roles = {
        str(name): str(role) for name, role in
        (stats_snapshot.get("actor_roles") or {}).items()
    }

    def encounter_view(value, *, active: bool) -> EncounterView:
        start = value_from(value, "start", observed_at)
        end = value_from(value, "end", start)
        if not isinstance(start, datetime):
            try:
                start = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                start = observed_at
        if not isinstance(end, datetime):
            try:
                end = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                end = start
        seconds = max(1.0, float(value_from(value, "seconds", 0.0) or 0.0))
        damage = int(value_from(value, "damage", 0) or 0)
        charmed = int(value_from(value, "charmed_pet_damage", 0) or 0)
        summoned = int(value_from(value, "summoned_pet_damage", 0) or 0)
        actor_values = metric_values(value_from(value, "actor_damage", {}))
        actor_source_values = value_from(value, "actor_sources", {}) or {}
        actor_roles = {
            str(name): str(role) for name, role in
            (value_from(value, "actor_roles", {}) or {}).items()
        }
        actors = []
        for actor_name, row in actor_values.items():
            session_row = session_actor_values.get(actor_name, row)
            role = actor_roles.get(
                actor_name, session_actor_roles.get(actor_name, "observed"))
            actors.append(CombatActorView(
                name=actor_name,
                role=role if role in {"self", "charmed", "summoned", "group", "observed"} else "observed",
                encounter_damage=row["t"],
                encounter_dps=int(round(row["t"] / seconds)),
                encounter_hits=row["h"],
                encounter_maximum=row["max"],
                session_damage=session_row["t"],
                session_dps=int(round(session_row["t"] / session_seconds)),
                session_hits=session_row["h"],
                session_maximum=session_row["max"],
                sources=metric_rows(
                    actor_source_values.get(actor_name, {})
                    if isinstance(actor_source_values, dict) else {},
                    abilities=True, limit=MAX_ACTOR_SOURCE_ROWS),
            ))
        actors.sort(key=lambda row: (-row.encounter_damage, row.name.casefold()))
        bucket_seconds = max(
            1, int(stats_snapshot.get("timeline_bucket_seconds") or 2))
        timeline_rows = [EncounterTimelinePointView(
            second=max(0, int(bucket) * bucket_seconds),
            outgoing=int(row.get("out") or 0),
            incoming=int(row.get("in") or 0),
            healing=int(row.get("heal") or 0),
            kills=int(row.get("kills") or 0),
        ) for bucket, row in sorted(
            (value_from(value, "timeline", {}) or {}).items(),
            key=lambda item: int(item[0])) if isinstance(row, dict)]
        # Long encounters can contain hundreds of two-second samples. Keep
        # the desktop protocol bounded while preserving trace shape and totals.
        maximum_timeline_points = 180
        if len(timeline_rows) > maximum_timeline_points:
            stride = math.ceil(len(timeline_rows) / maximum_timeline_points)
            timeline = tuple(EncounterTimelinePointView(
                second=group[0].second,
                outgoing=sum(point.outgoing for point in group),
                incoming=sum(point.incoming for point in group),
                healing=sum(point.healing for point in group),
                kills=sum(point.kills for point in group),
            ) for offset in range(0, len(timeline_rows), stride)
              if (group := timeline_rows[offset:offset + stride]))
        else:
            timeline = tuple(timeline_rows)
        return EncounterView(
            encounter_id=_timestamp(start),
            name=str(value_from(value, "name", "fight") or "fight"),
            active=active,
            started_at=_timestamp(start),
            ended_at=_timestamp(end),
            seconds=round(seconds, 3),
            damage=damage,
            dps=int(round(damage / seconds)),
            personal_damage=max(0, damage - charmed - summoned),
            charmed_pet_damage=charmed,
            summoned_pet_damage=summoned,
            damage_taken=int(value_from(value, "damage_taken", 0) or 0),
            healing_done=int(value_from(value, "healing_done", 0) or 0),
            heals_received=int(value_from(value, "heals_received", 0) or 0),
            kills=int(value_from(value, "kills", 0) or 0),
            crits=int(value_from(value, "crits", 0) or 0),
            misses=int(value_from(value, "misses", 0) or 0),
            sources=metric_rows(
                value_from(value, "sources", {}), abilities=True,
                categories=value_from(value, "source_categories", {})),
            targets=metric_rows(
                value_from(value, "targets", {}), plain_totals=True,
                limit=32),
            actors=tuple(actors[:48]),
            healing_sources=healing_rows(
                value_from(value, "healing_sources", {})),
            timeline=timeline,
            zone=str(value_from(value, "zone", "") or ""),
            raid_tier=(int(tier) if (tier := value_from(
                value, "journal_raid_tier", None)) in range(5) else None),
            raid_mode=str(value_from(
                value, "journal_raid_mode", "") or ""),
        )

    fight_history = tuple(stats_snapshot.get("fights") or ())[-60:]
    encounters = tuple(
        encounter_view(row, active=bool(stats_snapshot.get("in_combat")) and index == len(fight_history) - 1)
        for index, row in enumerate(fight_history)
    )

    def item_info_view(value) -> ItemInfoView | None:
        if not isinstance(value, dict):
            return None
        stats = value.get("stats")
        if isinstance(stats, dict):
            stat_rows = tuple(
                f"{key}: {entry}" for key, entry in stats.items())
        elif isinstance(stats, (list, tuple)):
            stat_rows = tuple(str(entry) for entry in stats)
        else:
            stat_rows = ()
        notes = value.get("notes")
        if isinstance(notes, (list, tuple)):
            note_rows = tuple(str(entry) for entry in notes)
        elif notes:
            note_rows = tuple(
                row.strip() for row in str(notes).splitlines() if row.strip())
        else:
            note_rows = ()
        sections = value.get("sections")
        section_rows: dict[str, tuple[str, ...]] = {}
        if isinstance(sections, dict):
            for heading, entries in sections.items():
                if isinstance(entries, (list, tuple)):
                    section_rows[str(heading)] = tuple(
                        str(entry) for entry in entries)
                elif entries:
                    section_rows[str(heading)] = (str(entries),)
        freshness = str(value.get("freshness") or "cached")
        return ItemInfoView(
            title=str(value.get("title") or ""),
            url=str(value.get("url") or ""),
            stats=stat_rows,
            notes=note_rows,
            sections=section_rows,
            freshness=freshness,
        )

    loot = tuple(LootEventView(
        event_id=str(row.get("event_id") or ""),
        occurred_at=str(row.get("occurred_at") or ""),
        item=str(row.get("item") or ""),
        item_key=str(row.get("item_key") or ""),
        quantity=max(1, int(row.get("quantity") or 1)),
        looter=str(row.get("looter") or ""),
        source=str(row.get("source") or ""),
        zone=str(row.get("zone") or ""),
        character=str(row.get("character") or ""),
        server=str(row.get("server") or ""),
        encounter_id=str(row.get("encounter_id") or ""),
        acquisition_type=str(row.get("acquisition_type") or "loot"),
        raid_tier=(int(row["raid_tier"])
                   if row.get("raid_tier") in range(5) else None),
        raid_mode=str(row.get("raid_mode") or ""),
        item_info=item_info_view(row.get("item_info")),
    ) for row in tuple(recent_loot or ())[:100] if isinstance(row, dict))
    journal = tuple(JournalEncounterView(
        encounter_id=str(row.get("encounter_id") or ""),
        started_at=str(row.get("started_at") or ""),
        ended_at=str(row.get("ended_at") or ""),
        character=str(row.get("character") or ""),
        server=str(row.get("server") or ""),
        name=str(row.get("name") or "fight"),
        zone=str(row.get("zone") or ""),
        raid_tier=(int(row["raid_tier"])
                   if row.get("raid_tier") in range(5) else None),
        raid_mode=str(row.get("raid_mode") or ""),
        seconds=round(max(0.0, float(row.get("seconds") or 0.0)), 3),
        damage=max(0, int(row.get("damage") or 0)),
        dps=max(0, int(row.get("dps") or 0)),
        kills=max(0, int(row.get("kills") or 0)),
    ) for row in tuple(journal_encounters or ())[:60]
      if isinstance(row, dict))
    summary = loot_summary if isinstance(loot_summary, dict) else {}

    breakdown = CombatBreakdownView(
        sources=metric_rows(
            stats_snapshot.get("fight_sources"), abilities=True),
        targets=metric_rows(stats_snapshot.get("fight_targets"), plain_totals=True),
        actors=metric_rows(stats_snapshot.get("fight_actor_damage")),
    )
    personal_damage = stats_snapshot.get("personal_damage")
    if personal_damage is None:
        personal_damage = max(
            0,
            int(stats_snapshot.get("combat_damage") or 0)
            - int(stats_snapshot.get("pet_damage") or 0),
        )
    return EngineSnapshot(
        protocol_version=PROTOCOL_VERSION,
        sequence=max(0, int(sequence)),
        observed_at=_timestamp(observed_at),
        character=CharacterView(
            name=str(stats_snapshot.get("character") or "?"),
            level=int(stats_snapshot.get("level") or 0),
            composition=str(stats_snapshot.get("composition") or ""),
            zone=str(stats_snapshot.get("zone") or ""),
        ),
        group_members=tuple(str(name) for name in
                            (stats_snapshot.get("group_members") or ())),
        combat=CombatView(
            active=bool(stats_snapshot.get("in_combat", False)),
            auto_attack=bool(stats_snapshot.get("auto_attack", False)),
            encounter_name=encounter_name,
            fight_dps=fight_dps,
            session_dps=int(stats_snapshot.get("session_dps") or 0),
            personal_damage=int(personal_damage or 0),
            charmed_pet_damage=int(
                stats_snapshot.get("charmed_pet_damage") or 0),
            summoned_pet_damage=int(
                stats_snapshot.get("summoned_pet_damage") or 0),
            fight_seconds=round(fight_seconds, 3),
            fight_damage=fight_damage,
            fight_personal_damage=max(0, fight_damage - fight_charmed - fight_summoned),
            fight_charmed_pet_damage=fight_charmed,
            fight_summoned_pet_damage=fight_summoned,
            damage_taken=damage_taken,
            healing_done=healing_done,
            kills=kills,
            crits=crits,
            misses=misses,
        ),
        breakdown=breakdown,
        encounters=encounters,
        loot=loot,
        loot_event_count=max(0, int(summary.get("events") or len(loot))),
        loot_total_count=max(0, int(summary.get("quantity") or sum(
            row.quantity for row in loot))),
        loot_unique_count=max(0, int(summary.get("unique_items") or len({
            row.item_key for row in loot}))),
        journal_encounters=journal,
        controls=controls,
        hidden_control_rows=int(control_snapshot.hidden_rows),
        control_notice_count=int(control_snapshot.notice_count),
        control_ambiguity_count=int(control_snapshot.ambiguity_count),
    )


def snapshot_event(snapshot: EngineSnapshot) -> EngineEvent:
    return EngineEvent(
        protocol_version=PROTOCOL_VERSION,
        sequence=snapshot.sequence,
        occurred_at=snapshot.observed_at,
        event_type="engine.snapshot",
        snapshot=snapshot,
    )


__all__ = [
    "PROTOCOL_VERSION",
    "CombatAbilityCategory",
    "classify_combat_ability_category",
    "CharacterView",
    "CombatView",
    "CombatMetricView",
    "CombatHealingMetricView",
    "EncounterTimelinePointView",
    "CombatBreakdownView",
    "CombatActorView",
    "EncounterView",
    "ItemInfoView",
    "LootEventView",
    "JournalEncounterView",
    "ControlTimerView",
    "EngineEvent",
    "EngineSnapshot",
    "build_engine_snapshot",
    "snapshot_event",
]
