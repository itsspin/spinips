#!/usr/bin/env python3
"""Spin's Loremaster — the log-reading companion for Spin's UI Reloaded.

A zero-dependency (Python standard library only) EverQuest Legends session
tracker in the spirit of EQBuddy, themed to match the "Obsidian & Ember" skin
and shaped to dock into the reserved bottom-right zone of the 3440x1440
layout.

What it does
------------
* Tails your EverQuest Legends log file (offset-based, 500 ms polls).
* Auto-detects the active character and switches when you swap toons.
* Combat-aware DPS: fights open on your (or your pet's) first action and
  close after 10 s of silence; bystander activity only extends a fight
  within a 20 s grace window of your own last action.
* Live fight DPS, session DPS, best fight, full fight history.
* Pet damage attribution (learns pet names from pet speech) + active pet
  count for swarm/multiclass play.
* Bard song counting (songs twisted, songs/min) — WAR/DRU/BRD approved.
* XP tracking: xp events, xp %/hr when the server logs percentages, level
  ups, and estimated time to level.
* Kills (per-creature breakdown), deaths, heals in/out, damage taken,
  loot log, coin (plat/hr), faction hits, skill-ups, fizzles/resists.
* Mini mode: a slim always-on-top strip with your starred stats.
* Per-character persistence in loremaster_data/<Character>.json.

Usage
-----
    python loremaster.py               # run the overlay
    python loremaster.py --demo        # overlay fed by a synthetic fight
    python loremaster.py --selftest    # run the parser/stats test suite
    python loremaster.py --log PATH    # follow one specific log file
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Theme — matches Spin UI "Obsidian & Ember"
# ---------------------------------------------------------------------------
THEME = {
    "bg": "#0b0d12",
    "panel": "#10131b",
    "raised": "#181c27",
    "line": "#3a4152",
    "line_soft": "#262b38",
    "gold": "#c9a227",
    "gold_bright": "#e8c55c",
    "cyan": "#41c7e4",
    "text": "#e8eaf0",
    "dim": "#9aa3b5",
    "hp": "#d93a3f",
    "mana": "#3e7bfa",
    "endur": "#d9a13a",
    "green": "#3fbf6b",
}

DEFAULT_LOG_DIRS = [
    r"C:\Users\Public\Daybreak Game Company\Installed Games\EverQuest Legends\Logs",
    r"C:\Users\Public\Daybreak Game Company\Installed Games\EverQuest\Logs",
    r"C:\Program Files (x86)\Sony\EverQuest\Logs",
    str(Path.home() / "EverQuest Legends" / "Logs"),
]

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "loremaster_config.json"
DATA_DIR = APP_DIR / "loremaster_data"

# EQBuddy-proven pacing constants
COMBAT_GAP = timedelta(seconds=10)
BYSTANDER_GRACE = timedelta(seconds=20)
SESSION_GAP = timedelta(minutes=60)
POLL_MS = 500

TS_FORMAT = "%a %b %d %H:%M:%S %Y"


# ---------------------------------------------------------------------------
# Log grammar (EverQuest Legends / live-style lines)
# ---------------------------------------------------------------------------
MELEE_VERBS = (
    "slash(?:es)?|hits?|kicks?|bash(?:es)?|pierc(?:e|es)|crush(?:es)?|"
    "punch(?:es)?|backstabs?|strikes?|slams?|mauls?|gores?|bites?|claws?|"
    "smash(?:es)?|rends?|stings?|frenz(?:y|ies) on"
)
CRIT = r"(?P<crit> \((?:Critical|Crippling Blow|Lucky Critical|Finishing Blow)\))?"

LINE_RE = re.compile(
    r"^\[(?P<ts>[A-Za-z]{3} [A-Za-z]{3} +\d{1,2} \d{2}:\d{2}:\d{2} \d{4})\] (?P<msg>.*)$"
)

PATTERNS: list[tuple[str, re.Pattern]] = [
    # --- your damage ---
    ("melee_out", re.compile(
        rf"^You (?:{MELEE_VERBS}) (?P<target>.+?) for (?P<dmg>\d+) points? of damage\.{CRIT}$")),
    ("miss_out", re.compile(
        r"^You try to \w+(?: on)? (?P<target>.+?), but (?P<reason>.+)!$")),
    ("dot_out", re.compile(
        rf"^(?P<target>.+?) has taken (?P<dmg>\d+) damage from your (?P<spell>.+?)\.{CRIT}$")),
    ("nuke_out_plain", re.compile(
        rf"^You hit (?P<target>.+?) for (?P<dmg>\d+) points? of non-melee damage\.{CRIT}$")),
    ("nuke_out_school", re.compile(
        rf"^You hit (?P<target>.+?) for (?P<dmg>\d+) points? of \w+ damage by (?P<spell>.+?)\.{CRIT}$")),
    ("ds_out", re.compile(
        r"^(?P<target>.+?) is \w+ by YOUR .+? for (?P<dmg>\d+) points? of non-melee damage\.$")),
    # --- incoming ---
    ("melee_in", re.compile(
        rf"^(?P<attacker>.+?) (?:{MELEE_VERBS}) YOU for (?P<dmg>\d+) points? of damage\.{CRIT}$")),
    ("miss_in", re.compile(
        r"^(?P<attacker>.+?) tries to \w+(?: on)? YOU, but (?P<reason>.+)!$")),
    ("nuke_in", re.compile(
        r"^(?P<attacker>.+?) hit you for (?P<dmg>\d+) points? of \w+ damage by (?P<spell>.+?)\.$")),
    ("dot_in", re.compile(
        r"^You have taken (?P<dmg>\d+) damage from (?P<spell>.+?) by (?P<attacker>.+?)\.$")),
    ("nonmelee_in", re.compile(
        r"^YOU are (?P<how>.+?) for (?P<dmg>\d+) points? of non-melee damage!$")),
    # --- deaths & kills ---
    ("kill_you", re.compile(r"^You have slain (?P<target>.+)!$")),
    ("death_you", re.compile(r"^You have been slain by (?P<killer>.+)!$")),
    ("kill_other", re.compile(r"^(?P<target>.+) has been slain by (?P<killer>.+)!$")),
    # --- heals ---
    ("heal_out", re.compile(
        r"^You healed (?P<target>.+?) for (?P<amount>\d+)(?: \((?P<attempted>\d+)\))? hit points(?: by (?P<spell>.+?))?\.$")),
    ("heal_in_named", re.compile(
        r"^(?P<healer>.+?) healed you(?: over time)? for (?P<amount>\d+)(?: \((?P<attempted>\d+)\))? hit points(?: by (?P<spell>.+?))?\.$")),
    ("heal_in", re.compile(
        r"^You have been healed for (?P<amount>\d+) (?:hit )?points?(?: of damage)?\.?$")),
    # --- casting / songs ---
    ("song_begin", re.compile(r"^You begin to sing (?P<song>.+?)\.$")),
    ("cast_begin", re.compile(r"^You begin casting (?P<spell>.+?)\.$")),
    ("fizzle", re.compile(r"^Your (?P<spell>.+?) spell fizzles!$")),
    ("resist", re.compile(r"^Your target resisted the (?P<spell>.+?) spell\.$")),
    ("resist2", re.compile(r"^.+? resisted your (?P<spell>.+?)!$")),
    ("interrupt", re.compile(r"^Your spell is interrupted\.$")),
    # --- xp / progression ---
    ("xp", re.compile(r"^You gain (?P<party>party )?experience!+(?: \((?P<pct>[\d.]+)%\))?.*$")),
    ("level", re.compile(r"^You have gained a level! Welcome to level (?P<level>\d+)!$")),
    ("skill", re.compile(r"^You have become better at (?P<skill>.+?)! \((?P<value>\d+)\)$")),
    ("aa", re.compile(r"^You have gained an ability point!.*$")),
    # --- loot / money ---
    ("loot", re.compile(r"^--You have looted an? (?P<item>.+?)(?: from (?P<source>.+?)'s corpse)?\.--$")),
    ("loot2", re.compile(r"^--(?P<who>\S+) has looted an? (?P<item>.+?)\.--$")),
    ("money", re.compile(r"^You receive (?P<coins>.+?) (?:from the corpse|as your split)\.$")),
    ("money_sale", re.compile(r"^You receive (?P<coins>.+?) from (?P<vendor>.+?) for the (?P<item>.+?)\(s\)\.$")),
    # --- world ---
    ("faction", re.compile(r"^Your faction standing with (?P<faction>.+?) has been adjusted by (?P<delta>-?\d+)\.$")),
    ("zone", re.compile(r"^You have entered (?P<zone>.+)\.$")),
    # --- pets ---
    ("pet_attack", re.compile(r"^(?P<pet>\S+) (?:tells|told) you, 'Attacking (?P<target>.+?) Master\.'$")),
    ("pet_leader", re.compile(r"^(?P<pet>\S+) says,? 'My leader is (?P<leader>\S+?)\.'$")),
    # --- bystanders (third party) ---
    ("melee_third", re.compile(
        rf"^(?P<attacker>.+?) (?:{MELEE_VERBS}) (?P<target>.+?) for (?P<dmg>\d+) points? of damage\.{CRIT}$")),
    ("dot_third", re.compile(
        r"^(?P<target>.+?) has taken (?P<dmg>\d+) damage from (?P<spell>.+?) by (?P<caster>.+?)\.$")),
    ("nuke_third", re.compile(
        rf"^(?P<attacker>.+?) hit (?P<target>.+?) for (?P<dmg>\d+) points? of \w+ damage by (?P<spell>.+?)\.{CRIT}$")),
    ("miss_third", re.compile(
        r"^(?P<attacker>.+?) tries to \w+(?: on)? (?P<target>.+?), but (?P<reason>.+)!$")),
]

COIN_RE = re.compile(r"(\d+) (platinum|gold|silver|copper)")
COIN_COPPER = {"platinum": 1000, "gold": 100, "silver": 10, "copper": 1}

ZONE_FALSE_POSITIVES = ("an area", "area where", "an Arena")


def normalize_mob(name: str) -> str:
    n = re.sub(r"^(a|an|the)\s+", "", name.strip(), flags=re.I)
    return n[:1].upper() + n[1:] if n else name


def parse_coins(text: str) -> int:
    return sum(int(n) * COIN_COPPER[unit] for n, unit in COIN_RE.findall(text))


def parse_line(line: str):
    """Return (timestamp, kind, groupdict) or None."""
    m = LINE_RE.match(line)
    if not m:
        return None
    try:
        ts = datetime.strptime(re.sub(r"\s+", " ", m.group("ts")), TS_FORMAT)
    except ValueError:
        return None
    msg = m.group("msg")
    for kind, rx in PATTERNS:
        pm = rx.match(msg)
        if pm:
            return ts, kind, pm.groupdict()
    return None


# ---------------------------------------------------------------------------
# Session statistics
# ---------------------------------------------------------------------------
@dataclass
class Fight:
    start: datetime
    end: datetime
    damage: int = 0
    targets: dict = field(default_factory=lambda: defaultdict(int))

    @property
    def seconds(self) -> float:
        return max(1.0, (self.end - self.start).total_seconds())

    @property
    def dps(self) -> float:
        return self.damage / self.seconds

    @property
    def name(self) -> str:
        if not self.targets:
            return "fight"
        return max(self.targets.items(), key=lambda kv: kv[1])[0]


class SessionStats:
    def __init__(self, character: str = "?"):
        self.character = character
        self.reset()

    def reset(self):
        self.session_start: datetime | None = None
        self.last_event: datetime | None = None
        # combat
        self.fight: Fight | None = None
        self.fights: list[Fight] = []
        self.last_own_action: datetime | None = None
        self.last_combat_signal: datetime | None = None
        self.damage_by_source: dict[str, int] = defaultdict(int)
        self.melee_hits = self.melee_misses = 0
        self.crits = 0
        self.enemy_misses = 0
        # defense
        self.damage_taken = 0
        self.heals_received = 0
        # healing
        self.healing_done = 0
        self.overheal = 0
        # pets
        self.pet_names: set[str] = set()
        self.pet_last_seen: dict[str, datetime] = {}
        self.pet_damage = 0
        # kills etc.
        self.kills: dict[str, int] = defaultdict(int)
        self.deaths = 0
        # casting
        self.songs = 0
        self.casts = 0
        self.fizzles = 0
        self.resists = 0
        self.interrupts = 0
        # xp
        self.xp_events = 0
        self.xp_pct = 0.0
        self.xp_pct_known = False
        self.level: int | None = None
        self.xp_since_level = 0.0
        self.levelups = 0
        # loot / money
        self.copper = 0
        self.loot: dict[str, int] = defaultdict(int)
        self.faction: dict[str, int] = defaultdict(int)
        self.skillups: dict[str, int] = defaultdict(int)
        self.aa_points = 0
        self.zone = ""
        self.log_lines = 0

    # -- helpers ---------------------------------------------------------
    def hours(self) -> float:
        if not self.session_start or not self.last_event:
            return 0.0
        return max(1 / 3600, (self.last_event - self.session_start).total_seconds() / 3600)

    def is_pet(self, name: str) -> bool:
        return name.strip() in self.pet_names

    def _touch(self, ts: datetime):
        if self.session_start is None:
            self.session_start = ts
        elif self.last_event and ts - self.last_event > SESSION_GAP:
            level, xsl, known = self.level, self.xp_since_level, self.xp_pct_known
            pets = set(self.pet_names)
            zone = self.zone
            self.reset()
            self.session_start = ts
            self.level, self.xp_since_level, self.xp_pct_known = level, xsl, known
            self.pet_names, self.zone = pets, zone
        self.last_event = ts

    # -- combat windows --------------------------------------------------
    def _own_combat(self, ts: datetime):
        self.last_own_action = ts
        self._combat_signal(ts, own=True)

    def _combat_signal(self, ts: datetime, own: bool = False):
        if self.fight is None:
            if not own:
                return  # bystanders never open a fight
            self.fight = Fight(start=ts, end=ts)
        else:
            if not own and self.last_own_action and ts - self.last_own_action > BYSTANDER_GRACE:
                return  # too long since our own action: don't stretch the fight
            if ts - self.fight.end > COMBAT_GAP:
                self._close_fight()
                if own:
                    self.fight = Fight(start=ts, end=ts)
                return
            self.fight.end = ts
        self.last_combat_signal = ts

    def _close_fight(self):
        if self.fight and self.fight.damage > 0:
            self.fights.append(self.fight)
        self.fight = None

    def _deal(self, ts: datetime, target: str, dmg: int, source: str, crit: bool = False):
        self._own_combat(ts)
        if self.fight is None:
            self.fight = Fight(start=ts, end=ts)
        self.fight.damage += dmg
        self.fight.targets[normalize_mob(target)] += dmg
        self.damage_by_source[source] += dmg
        if crit:
            self.crits += 1

    # -- event application ----------------------------------------------
    def apply(self, ts: datetime, kind: str, g: dict):
        self._touch(ts)
        self.log_lines += 1
        crit = bool(g.get("crit"))

        if kind == "melee_out":
            self.melee_hits += 1
            self._deal(ts, g["target"], int(g["dmg"]), "Melee", crit)
        elif kind == "miss_out":
            self.melee_misses += 1
            self._own_combat(ts)
        elif kind == "dot_out":
            self._deal(ts, g["target"], int(g["dmg"]), f"DoT: {g['spell']}", crit)
        elif kind == "nuke_out_plain":
            self._deal(ts, g["target"], int(g["dmg"]), "Spells", crit)
        elif kind == "nuke_out_school":
            self._deal(ts, g["target"], int(g["dmg"]), f"Spell: {g['spell']}", crit)
        elif kind == "ds_out":
            self._deal(ts, g["target"], int(g["dmg"]), "Damage shield")

        elif kind == "melee_in":
            self.damage_taken += int(g["dmg"])
            self._own_combat(ts)
        elif kind in ("nuke_in", "dot_in", "nonmelee_in"):
            self.damage_taken += int(g["dmg"])
            self._own_combat(ts)
        elif kind == "miss_in":
            self.enemy_misses += 1
            self._own_combat(ts)

        elif kind == "kill_you":
            self.kills[normalize_mob(g["target"])] += 1
            self._own_combat(ts)
        elif kind == "death_you":
            self.deaths += 1
            self._close_fight()
        elif kind == "kill_other":
            killer = g["killer"].strip()
            if killer == self.character or self.is_pet(killer):
                self.kills[normalize_mob(g["target"])] += 1
            self._combat_signal(ts)

        elif kind == "heal_out":
            amt = int(g["amount"])
            self.healing_done += amt
            if g.get("attempted"):
                self.overheal += max(0, int(g["attempted"]) - amt)
        elif kind in ("heal_in", "heal_in_named"):
            self.heals_received += int(g["amount"])

        elif kind == "song_begin":
            self.songs += 1
        elif kind == "cast_begin":
            self.casts += 1
        elif kind == "fizzle":
            self.fizzles += 1
        elif kind in ("resist", "resist2"):
            self.resists += 1
        elif kind == "interrupt":
            self.interrupts += 1

        elif kind == "xp":
            self.xp_events += 1
            if g.get("pct"):
                pct = float(g["pct"])
                self.xp_pct += pct
                self.xp_since_level = min(100.0, self.xp_since_level + pct)
                self.xp_pct_known = True
        elif kind == "level":
            self.levelups += 1
            self.level = int(g["level"])
            self.xp_since_level = 0.0
        elif kind == "skill":
            self.skillups[g["skill"]] = int(g["value"])
        elif kind == "aa":
            self.aa_points += 1

        elif kind in ("loot", "loot2"):
            who = g.get("who")
            if who is None or who == self.character:
                self.loot[g["item"]] += 1
        elif kind in ("money", "money_sale"):
            self.copper += parse_coins(g["coins"])

        elif kind == "faction":
            self.faction[g["faction"]] += int(g["delta"])
        elif kind == "zone":
            if not any(fp in g["zone"] for fp in ZONE_FALSE_POSITIVES):
                self.zone = g["zone"]

        elif kind == "pet_attack":
            self._register_pet(g["pet"], ts)
            self._own_combat(ts)
        elif kind == "pet_leader":
            if g["leader"] == self.character or self.character == "?":
                self._register_pet(g["pet"], ts)

        elif kind in ("melee_third", "nuke_third", "dot_third"):
            attacker = (g.get("attacker") or g.get("caster") or "").strip()
            dmg = int(g["dmg"])
            if attacker and self.is_pet(attacker):
                self.pet_last_seen[attacker] = ts
                self.pet_damage += dmg
                self._deal(ts, g["target"], dmg, f"Pet ({attacker})")
            else:
                self._combat_signal(ts)
        elif kind == "miss_third":
            attacker = (g.get("attacker") or "").strip()
            if attacker and self.is_pet(attacker):
                self._own_combat(ts)
            else:
                self._combat_signal(ts)

    def _register_pet(self, pet: str, ts: datetime):
        pet = pet.strip()
        self.pet_names.add(pet)
        self.pet_last_seen[pet] = ts

    # -- snapshot for the UI ---------------------------------------------
    def snapshot(self, now: datetime | None = None) -> dict:
        now = now or self.last_event or datetime.now()
        live = None
        pending: list[Fight] = []
        if self.fight is not None:
            ref = self.last_combat_signal or self.fight.end
            if now - ref <= COMBAT_GAP + timedelta(seconds=2):
                live = self.fight
            elif self.fight.damage > 0:
                pending = [self.fight]  # idle long enough: treat as closed
        closed = self.fights + pending
        closed_damage = sum(f.damage for f in closed)
        closed_seconds = sum(f.seconds for f in closed)
        if live:
            closed_damage += live.damage
            closed_seconds += live.seconds
        session_dps = closed_damage / closed_seconds if closed_seconds else 0.0
        current_dps = live.dps if live else 0.0
        hours = self.hours()
        xp_hr = self.xp_pct / hours if hours and self.xp_pct_known else 0.0
        if xp_hr > 0.05:
            hours_to_level = max(0.0, 100.0 - min(self.xp_since_level, 100.0)) / xp_hr
        else:
            hours_to_level = None
        active_pets = [p for p, t in self.pet_last_seen.items()
                       if (now - t) <= timedelta(seconds=60)]
        best = max(closed + ([live] if live else []),
                   key=lambda f: f.dps, default=None)
        songs_min = self.songs / (hours * 60) if hours else 0.0
        return {
            "character": self.character,
            "zone": self.zone,
            "level": self.level,
            "session_dps": session_dps,
            "current_dps": current_dps,
            "in_combat": live is not None,
            "combat_damage": closed_damage,
            "combat_seconds": closed_seconds,
            "best_fight": best,
            "fights": (closed + ([live] if live else []))[-10:],
            "damage_by_source": dict(self.damage_by_source),
            "pet_damage": self.pet_damage,
            "active_pets": active_pets,
            "pet_names": sorted(self.pet_names),
            "kills": sum(self.kills.values()),
            "kill_breakdown": dict(self.kills),
            "deaths": self.deaths,
            "melee_hits": self.melee_hits,
            "melee_misses": self.melee_misses,
            "crits": self.crits,
            "enemy_misses": self.enemy_misses,
            "damage_taken": self.damage_taken,
            "healing_done": self.healing_done,
            "heals_received": self.heals_received,
            "hps": self.healing_done / (closed_seconds or 1) if closed_seconds else 0.0,
            "songs": self.songs,
            "songs_min": songs_min,
            "casts": self.casts,
            "fizzles": self.fizzles,
            "resists": self.resists,
            "xp_events": self.xp_events,
            "xp_pct": self.xp_pct,
            "xp_pct_known": self.xp_pct_known,
            "xp_hr": xp_hr,
            "xp_since_level": self.xp_since_level,
            "hours_to_level": hours_to_level,
            "plat": self.copper / 1000.0,
            "plat_hr": (self.copper / 1000.0) / hours if hours else 0.0,
            "loot": dict(self.loot),
            "hours": hours,
        }


# ---------------------------------------------------------------------------
# Log watching / character auto-detection
# ---------------------------------------------------------------------------
LOG_NAME_RE = re.compile(r"^eqlog_(?P<char>[A-Za-z]+)_(?P<server>[A-Za-z0-9.]+)\.txt$")


class LogWatcher:
    """Offset-based tail of the most recently active eqlog file."""

    def __init__(self, log_dir: str | None, explicit_log: str | None = None):
        self.log_dir = Path(log_dir) if log_dir else None
        self.explicit = Path(explicit_log) if explicit_log else None
        self.path: Path | None = None
        self.offset = 0
        self.character = "?"
        self.server = "?"

    @staticmethod
    def discover_dir() -> str | None:
        for d in DEFAULT_LOG_DIRS:
            if Path(d).is_dir():
                return d
        return None

    def _pick(self) -> Path | None:
        if self.explicit:
            return self.explicit if self.explicit.exists() else None
        if not self.log_dir or not self.log_dir.is_dir():
            return None
        best, best_m = None, -1.0
        for p in self.log_dir.glob("eqlog_*.txt"):
            m = p.stat().st_mtime
            if m > best_m:
                best, best_m = p, m
        return best

    def poll(self) -> tuple[list[str], bool]:
        """Return (new_lines, switched_character)."""
        target = self._pick()
        switched = False
        if target is None:
            return [], False
        if self.path != target:
            self.path = target
            m = LOG_NAME_RE.match(target.name)
            if m:
                self.character, self.server = m.group("char"), m.group("server")
            self.offset = target.stat().st_size  # start at live tail
            switched = True
            return [], switched
        try:
            size = self.path.stat().st_size
            if size < self.offset:  # rotated/truncated
                self.offset = 0
            if size == self.offset:
                return [], False
            with self.path.open("r", encoding="latin-1", errors="replace") as fh:
                fh.seek(self.offset)
                chunk = fh.read()
                self.offset = fh.tell()
            lines = chunk.splitlines()
            return lines, False
        except OSError:
            return [], False


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def load_config() -> dict:
    cfg = {
        "log_dir": None,
        "mini_mode": False,
        "opacity": 0.94,
        "position": None,
        "mini_position": None,
        "starred": ["session_dps", "xp_hr", "hours_to_level", "kills"],
    }
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text()))
    except (OSError, ValueError):
        pass
    if not cfg.get("log_dir"):
        cfg["log_dir"] = LogWatcher.discover_dir()
    return cfg


def save_config(cfg: dict) -> None:
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    except OSError:
        pass


def load_character_state(char: str) -> dict:
    try:
        return json.loads((DATA_DIR / f"{char}.json").read_text())
    except (OSError, ValueError):
        return {}


def save_character_state(char: str, stats: SessionStats) -> None:
    if char in ("?", ""):
        return
    try:
        DATA_DIR.mkdir(exist_ok=True)
        prior = load_character_state(char)
        snap = stats.snapshot()
        lifetime = prior.get("lifetime", {})
        session_key = stats.session_start.isoformat() if stats.session_start else "unknown"
        if prior.get("last_session_key") != session_key:
            for k in ("kills", "deaths", "xp_pct", "plat", "songs", "casts"):
                lifetime[k] = lifetime.get(k, 0) + (
                    snap[k] if isinstance(snap.get(k), (int, float)) else 0)
        state = {
            "character": char,
            "level": snap["level"],
            "xp_since_level": snap["xp_since_level"],
            "pet_names": snap["pet_names"],
            "zone": snap["zone"],
            "last_session_key": session_key,
            "lifetime": lifetime,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
        }
        (DATA_DIR / f"{char}.json").write_text(json.dumps(state, indent=2))
    except OSError:
        pass


def restore_character_state(stats: SessionStats) -> None:
    st = load_character_state(stats.character)
    if not st:
        return
    stats.level = st.get("level", stats.level)
    stats.xp_since_level = float(st.get("xp_since_level", 0.0))
    if stats.xp_since_level:
        stats.xp_pct_known = True
    for p in st.get("pet_names", []):
        stats.pet_names.add(p)
    stats.zone = st.get("zone", stats.zone)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def fmt_num(v: float) -> str:
    if v >= 1_000_000:
        return f"{v / 1_000_000:.2f}M"
    if v >= 10_000:
        return f"{v / 1000:.1f}k"
    if v >= 1000:
        return f"{v:,.0f}"
    return f"{v:.0f}"


def fmt_dur(seconds: float) -> str:
    s = int(seconds)
    if s >= 3600:
        return f"{s // 3600}h{(s % 3600) // 60:02d}m"
    if s >= 60:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s}s"


def fmt_eta(hours: float | None) -> str:
    if hours is None:
        return "—"
    if hours > 99:
        return ">99h"
    return fmt_dur(hours * 3600)


STAT_DEFS: dict[str, tuple[str, str]] = {
    # key -> (label, how to format from snapshot)
    "session_dps": ("DPS (session)", "dps"),
    "current_dps": ("DPS (fight)", "dps"),
    "kills": ("Kills", "int"),
    "deaths": ("Deaths", "int"),
    "xp_hr": ("XP %/hr", "pct"),
    "hours_to_level": ("Time to level", "eta"),
    "plat_hr": ("Plat/hr", "plat"),
    "hps": ("HPS", "dps"),
    "damage_taken": ("Dmg taken", "num"),
    "songs_min": ("Songs/min", "rate"),
    "active_pets": ("Pets active", "len"),
    "crits": ("Crits", "int"),
}


def stat_value(snap: dict, key: str) -> str:
    kind = STAT_DEFS[key][1]
    v = snap.get(key)
    if kind == "dps":
        return fmt_num(v or 0)
    if kind == "int":
        return str(int(v or 0))
    if kind == "pct":
        return f"{v:.1f}%" if snap.get("xp_pct_known") else "—"
    if kind == "eta":
        return fmt_eta(v)
    if kind == "plat":
        return f"{v:.1f}p"
    if kind == "num":
        return fmt_num(v or 0)
    if kind == "rate":
        return f"{v:.1f}"
    if kind == "len":
        return str(len(v or []))
    return str(v)


# ---------------------------------------------------------------------------
# Demo feed — a synthetic WAR/DRU/BRD session for testing without EQ
# ---------------------------------------------------------------------------
class DemoFeed:
    SCRIPT_INTERVAL = 0.7

    def __init__(self):
        self.t = datetime.now()
        self.i = 0
        self.pet_announced = False

    def lines(self) -> list[str]:
        import random
        self.i += 1
        self.t = datetime.now()
        stamp = self.t.strftime(TS_FORMAT.replace("%d", "%d"))
        out = []

        def emit(msg):
            out.append(f"[{stamp}] {msg}")

        r = random.random()
        if not self.pet_announced:
            emit("Gann tells you, 'Attacking a froglok shin knight Master.'")
            self.pet_announced = True
        if r < 0.45:
            emit(f"You slash a froglok shin knight for {random.randint(80, 340)} points of damage.")
            if random.random() < 0.2:
                emit(f"You slash a froglok shin knight for {random.randint(400, 900)} points of damage. (Critical)")
        elif r < 0.6:
            emit(f"Gann hits a froglok shin knight for {random.randint(40, 160)} points of damage.")
        elif r < 0.72:
            emit(f"You hit a froglok shin knight for {random.randint(150, 600)} points of magic damage by Careless Lightning.")
        elif r < 0.8:
            emit("You begin to sing Chant of Battle.")
        elif r < 0.88:
            emit(f"A froglok shin knight hits YOU for {random.randint(30, 180)} points of damage.")
        else:
            emit("You have slain a froglok shin knight!")
            emit("You gain party experience!! (0.42%)")
            emit("You receive 2 platinum, 4 gold from the corpse.")
            if random.random() < 0.3:
                emit("--You have looted a Froglok Fine Mesh from a froglok shin knight's corpse.--")
        return out


# ---------------------------------------------------------------------------
# Tk overlay
# ---------------------------------------------------------------------------
def run_gui(args):
    try:
        import tkinter as tk
    except ImportError:
        print("Spin\'s Loremaster needs Python's tkinter module (bundled with the")
        print("standard python.org Windows installer).")
        return 1

    cfg = load_config()
    if args.log_dir:
        cfg["log_dir"] = args.log_dir
    watcher = LogWatcher(cfg.get("log_dir"), args.log)
    stats = SessionStats()
    demo = DemoFeed() if args.demo else None
    if demo:
        stats.character = "Spin"
        watcher.character = "Spin"
        restore_character_state(stats)

    T = THEME
    root = tk.Tk()
    root.title("Spin\'s Loremaster")
    root.configure(bg=T["bg"])
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    try:
        root.attributes("-alpha", cfg.get("opacity", 0.94))
    except tk.TclError:
        pass

    state = {"mini": bool(cfg.get("mini_mode")), "last_save": time.time()}

    # ---- window drag + position persistence ----
    drag = {"x": 0, "y": 0}

    def start_drag(e):
        drag["x"], drag["y"] = e.x, e.y

    def do_drag(e):
        x = root.winfo_x() + e.x - drag["x"]
        y = root.winfo_y() + e.y - drag["y"]
        root.geometry(f"+{x}+{y}")

    def end_drag(_e):
        key = "mini_position" if state["mini"] else "position"
        cfg[key] = [root.winfo_x(), root.winfo_y()]
        save_config(cfg)

    root.bind("<Button-1>", start_drag)
    root.bind("<B1-Motion>", do_drag)
    root.bind("<ButtonRelease-1>", end_drag)

    FONT = ("Segoe UI", 10)
    FONT_S = ("Segoe UI", 8)
    FONT_B = ("Segoe UI Semibold", 10)
    FONT_BIG = ("Segoe UI Semibold", 17)
    FONT_MED = ("Segoe UI Semibold", 12)

    outer = tk.Frame(root, bg=T["gold"], padx=1, pady=1)   # 1px ember frame
    outer.pack(fill="both", expand=True)
    body = tk.Frame(outer, bg=T["bg"])
    body.pack(fill="both", expand=True)

    widgets: dict[str, tk.Widget] = {}

    def L(parent, text="", fg=None, font=FONT, bg=None, anchor="w", **kw):
        return tk.Label(parent, text=text, fg=fg or T["text"], bg=bg or parent["bg"],
                        font=font, anchor=anchor, **kw)

    def build_full():
        for w in body.winfo_children():
            w.destroy()
        head = tk.Frame(body, bg=T["panel"])
        head.pack(fill="x")
        widgets["title"] = L(head, "LOREMASTER", fg=T["gold_bright"], font=FONT_B, bg=T["panel"])
        widgets["title"].pack(side="left", padx=8, pady=4)
        widgets["zone"] = L(head, "", fg=T["dim"], font=FONT_S, bg=T["panel"])
        widgets["zone"].pack(side="left", padx=4)
        for txt, cmd in (("—", toggle_mini), ("↺", do_reset), ("✕", do_quit)):
            b = tk.Label(head, text=txt, fg=T["dim"], bg=T["panel"], font=FONT_B, cursor="hand2")
            b.pack(side="right", padx=6)
            b.bind("<Button-1>", lambda _e, c=cmd: c())

        dps_row = tk.Frame(body, bg=T["bg"])
        dps_row.pack(fill="x", padx=8, pady=(6, 2))
        for key, label, color in (
            ("current_dps", "FIGHT DPS", T["gold_bright"]),
            ("session_dps", "SESSION", T["text"]),
            ("best_dps", "BEST FIGHT", T["cyan"]),
        ):
            cell = tk.Frame(dps_row, bg=T["bg"])
            cell.pack(side="left", expand=True, fill="x")
            widgets[key] = L(cell, "0", fg=color, font=FONT_BIG, anchor="center")
            widgets[key].pack(fill="x")
            L(cell, label, fg=T["dim"], font=FONT_S, anchor="center").pack(fill="x")

        widgets["combat_bar"] = tk.Canvas(body, height=3, bg=T["line_soft"],
                                          highlightthickness=0)
        widgets["combat_bar"].pack(fill="x", padx=8, pady=3)

        grid = tk.Frame(body, bg=T["bg"])
        grid.pack(fill="x", padx=8)
        keys = ["kills", "xp_hr", "hours_to_level", "plat_hr",
                "hps", "damage_taken", "songs_min", "active_pets",
                "crits", "deaths"]
        for i, key in enumerate(keys):
            cell = tk.Frame(grid, bg=T["panel"], padx=6, pady=3,
                            highlightbackground=T["line_soft"], highlightthickness=1)
            cell.grid(row=i // 5, column=i % 5, sticky="nsew", padx=2, pady=2)
            grid.columnconfigure(i % 5, weight=1)
            star = "★" if key in cfg["starred"] else "☆"
            lab = L(cell, f"{star} {STAT_DEFS[key][0]}", fg=T["dim"], font=FONT_S, bg=T["panel"])
            lab.pack(fill="x")
            val = L(cell, "—", fg=T["text"], font=FONT_MED, bg=T["panel"])
            val.pack(fill="x")
            widgets[f"stat:{key}"] = val
            widgets[f"statlab:{key}"] = lab
            for w in (cell, lab, val):
                w.bind("<Button-3>", lambda _e, k=key: toggle_star(k))

        L(body, "RECENT FIGHTS", fg=T["gold"], font=FONT_S).pack(fill="x", padx=10, pady=(6, 0))
        widgets["fights"] = tk.Frame(body, bg=T["bg"])
        widgets["fights"].pack(fill="x", padx=8, pady=(0, 2))

        L(body, "DAMAGE BY SOURCE", fg=T["gold"], font=FONT_S).pack(fill="x", padx=10)
        widgets["sources"] = tk.Frame(body, bg=T["bg"])
        widgets["sources"].pack(fill="x", padx=8, pady=(0, 6))

        widgets["status"] = L(body, "Loremaster awaits your log…", fg=T["dim"], font=FONT_S)
        widgets["status"].pack(fill="x", padx=8, pady=(0, 5))
        pos = cfg.get("position")
        root.geometry(f"430x430+{pos[0]}+{pos[1]}" if pos else "430x430+2492+1156")

    def build_mini():
        for w in body.winfo_children():
            w.destroy()
        strip = tk.Frame(body, bg=T["bg"])
        strip.pack(fill="both", expand=True, padx=6, pady=3)
        widgets["mini_cells"] = strip
        b = tk.Label(strip, text="▣", fg=T["dim"], bg=T["bg"], font=FONT_B, cursor="hand2")
        b.pack(side="right", padx=2)
        b.bind("<Button-1>", lambda _e: toggle_mini())
        pos = cfg.get("mini_position")
        root.geometry(f"+{pos[0]}+{pos[1]}" if pos else "+2492+1396")

    def toggle_mini():
        state["mini"] = not state["mini"]
        cfg["mini_mode"] = state["mini"]
        save_config(cfg)
        (build_mini if state["mini"] else build_full)()

    def toggle_star(key):
        if key in cfg["starred"]:
            cfg["starred"].remove(key)
        else:
            cfg["starred"].append(key)
        save_config(cfg)
        lab = widgets.get(f"statlab:{key}")
        if lab:
            star = "★" if key in cfg["starred"] else "☆"
            lab.configure(text=f"{star} {STAT_DEFS[key][0]}")

    def do_reset():
        stats.reset()

    def do_quit():
        save_character_state(stats.character, stats)
        save_config(cfg)
        root.destroy()

    # ---- periodic update ----
    def tick():
        switched = False
        if demo:
            lines = demo.lines()
        else:
            lines, switched = watcher.poll()
            if switched:
                save_character_state(stats.character, stats)
                stats.__init__(watcher.character)
                stats.character = watcher.character
                restore_character_state(stats)
        for raw in lines:
            parsed = parse_line(raw)
            if parsed:
                ts, kind, groups = parsed
                stats.character = watcher.character if not demo else stats.character
                stats.apply(ts, kind, groups)
        refresh()
        if time.time() - state["last_save"] > 30:
            save_character_state(stats.character, stats)
            state["last_save"] = time.time()
        root.after(POLL_MS, tick)

    def refresh():
        snap = stats.snapshot(datetime.now())
        if state["mini"]:
            strip = widgets.get("mini_cells")
            if not strip:
                return
            for w in list(strip.winfo_children())[:-1]:
                w.destroy()
            head = f"{snap['character']}"
            tk.Label(strip, text=head, fg=THEME["gold_bright"], bg=THEME["bg"],
                     font=FONT_B).pack(side="left", padx=(2, 8))
            shown = [k for k in cfg["starred"] if k in STAT_DEFS] or ["session_dps"]
            for k in shown:
                tk.Label(strip, text=f"{STAT_DEFS[k][0]}:", fg=THEME["dim"],
                         bg=THEME["bg"], font=FONT_S).pack(side="left")
                tk.Label(strip, text=stat_value(snap, k), fg=THEME["text"],
                         bg=THEME["bg"], font=FONT_B).pack(side="left", padx=(2, 8))
            if snap["in_combat"]:
                tk.Label(strip, text=f"⚔ {fmt_num(snap['current_dps'])}",
                         fg=THEME["gold_bright"], bg=THEME["bg"], font=FONT_B
                         ).pack(side="left", padx=4)
            return

        title = snap["character"]
        if snap["level"]:
            title += f"  ·  {snap['level']}"
        widgets["title"].configure(text=f"LOREMASTER  —  {title}")
        widgets["zone"].configure(text=snap["zone"])
        widgets["current_dps"].configure(
            text=fmt_num(snap["current_dps"]),
            fg=THEME["gold_bright"] if snap["in_combat"] else THEME["dim"])
        widgets["session_dps"].configure(text=fmt_num(snap["session_dps"]))
        best = snap["best_fight"]
        widgets["best_dps"].configure(text=fmt_num(best.dps) if best else "0")
        bar = widgets["combat_bar"]
        bar.delete("all")
        bar.configure(bg=THEME["gold"] if snap["in_combat"] else THEME["line_soft"])
        for key in ("kills", "xp_hr", "hours_to_level", "plat_hr", "hps",
                    "damage_taken", "songs_min", "active_pets", "crits", "deaths"):
            widgets[f"stat:{key}"].configure(text=stat_value(snap, key))

        rows = widgets["fights"]
        for w in rows.winfo_children():
            w.destroy()
        for f in reversed(snap["fights"][-6:]):
            r = tk.Frame(rows, bg=THEME["bg"])
            r.pack(fill="x")
            nm = f.name if len(f.name) < 30 else f.name[:29] + "…"
            L(r, nm, fg=THEME["text"], font=FONT_S).pack(side="left")
            L(r, f"{fmt_num(f.dps)} dps", fg=THEME["gold_bright"], font=FONT_S,
              anchor="e").pack(side="right")
            L(r, f"{fmt_num(f.damage)} in {fmt_dur(f.seconds)}", fg=THEME["dim"],
              font=FONT_S, anchor="e").pack(side="right", padx=8)

        src = widgets["sources"]
        for w in src.winfo_children():
            w.destroy()
        total = sum(snap["damage_by_source"].values()) or 1
        top = sorted(snap["damage_by_source"].items(), key=lambda kv: -kv[1])[:5]
        for name, dmg in top:
            r = tk.Frame(src, bg=THEME["bg"])
            r.pack(fill="x")
            L(r, name, fg=THEME["text"], font=FONT_S).pack(side="left")
            L(r, f"{fmt_num(dmg)}  ({dmg * 100 // total}%)", fg=THEME["dim"],
              font=FONT_S, anchor="e").pack(side="right")
            c = tk.Canvas(r, width=90, height=6, bg=THEME["line_soft"], highlightthickness=0)
            c.pack(side="right", padx=6, pady=3)
            c.create_rectangle(0, 0, 90 * dmg / total, 6, fill=THEME["cyan"], width=0)

        if demo:
            src_txt = "demo mode — synthetic fight"
        elif watcher.path:
            src_txt = f"tailing {watcher.path.name}"
        else:
            src_txt = "no eqlog found — set log_dir in loremaster_config.json and /log on in game"
        widgets["status"].configure(text=src_txt)

    (build_mini if state["mini"] else build_full)()
    tick()
    root.mainloop()
    return 0


# ---------------------------------------------------------------------------
# Self-test — parser + stats engine, no GUI required
# ---------------------------------------------------------------------------
def selftest() -> int:
    now = datetime(2026, 7, 19, 20, 0, 0)

    def line(offset_s: int, msg: str) -> str:
        t = now + timedelta(seconds=offset_s)
        return f"[{t.strftime(TS_FORMAT)}] {msg}"

    stats = SessionStats("Spin")
    feed = [
        line(0, "Gann tells you, 'Attacking a froglok shin knight Master.'"),
        line(1, "You slash a froglok shin knight for 100 points of damage."),
        line(2, "You slash a froglok shin knight for 300 points of damage. (Critical)"),
        line(3, "Gann hits a froglok shin knight for 50 points of damage."),
        line(4, "You hit a froglok shin knight for 250 points of magic damage by Careless Lightning."),
        line(5, "A froglok shin knight has taken 75 damage from your Flame Lick."),
        line(6, "A froglok shin knight hits YOU for 60 points of damage."),
        line(7, "You try to slash a froglok shin knight, but miss!"),
        line(8, "You begin to sing Chant of Battle."),
        line(9, "You have slain a froglok shin knight!"),
        line(9, "You gain party experience!! (0.50%)"),
        line(9, "You receive 2 platinum, 4 gold from the corpse."),
        line(9, "--You have looted a Froglok Fine Mesh from a froglok shin knight's corpse.--"),
        # 30s of nothing -> fight closes at 10s gap
        line(40, "You healed Grimlord for 500 (650) hit points by Light Healing."),
        line(41, "Nexus slashes a gnoll for 40 points of damage."),  # bystander, no fight
        line(60, "You slash a gnoll pup for 120 points of damage."),
        line(62, "You have slain a gnoll pup!"),
        line(62, "You gain experience!! (0.25%)"),
        line(63, "You have gained a level! Welcome to level 40!"),
        line(64, "Your faction standing with Sabertooths of Blackburrow has been adjusted by -5."),
        line(65, "You have become better at Dodge! (58)"),
        line(66, "You have entered Blackburrow."),
    ]
    for raw in feed:
        parsed = parse_line(raw)
        assert parsed, f"unparsed: {raw}"
        ts, kind, g = parsed
        stats.apply(ts, kind, g)

    snap = stats.snapshot(now + timedelta(seconds=80))

    # fight 1: you 100+300+250+75 = 725, pet 50 → 775 dmg over 1..9 = 8s? (start 1 end 9)
    assert len(snap["fights"]) == 2, snap["fights"]
    f1, f2 = snap["fights"]
    assert f1.damage == 775, f1.damage
    assert f1.name == "Froglok shin knight", f1.name
    assert f2.damage == 120
    assert snap["kills"] == 2
    assert snap["kill_breakdown"]["Froglok shin knight"] == 1
    assert snap["crits"] == 1
    assert snap["melee_hits"] == 3 and snap["melee_misses"] == 1
    assert snap["damage_taken"] == 60
    assert snap["pet_damage"] == 50
    assert "Pet (Gann)" in snap["damage_by_source"]
    assert snap["songs"] == 1
    assert snap["healing_done"] == 500 and stats.overheal == 150
    assert abs(snap["plat"] - 2.4) < 1e-9, snap["plat"]
    assert snap["loot"]["Froglok Fine Mesh"] == 1
    assert snap["xp_events"] == 2
    assert abs(snap["xp_pct"] - 0.75) < 1e-9
    assert stats.level == 40 and stats.xp_since_level == 0.0
    assert stats.faction["Sabertooths of Blackburrow"] == -5
    assert stats.skillups["Dodge"] == 58
    assert stats.zone == "Blackburrow"
    assert snap["session_dps"] > 0

    # ETL math: fabricate xp rate
    s2 = SessionStats("Spin")
    for i in range(10):
        p = parse_line(line(i * 60, "You gain experience!! (1.00%)"))
        s2.apply(p[0], p[1], p[2])
    snap2 = s2.snapshot(now + timedelta(seconds=600))
    assert snap2["xp_pct_known"] and snap2["xp_hr"] > 0
    assert snap2["hours_to_level"] is not None
    expected = (100 - 10.0) / snap2["xp_hr"]
    assert abs(snap2["hours_to_level"] - expected) < 1e-6

    # bystanders never OPEN a fight
    s3 = SessionStats("Spin")
    p = parse_line(line(0, "Nexus slashes a gnoll for 40 points of damage."))
    s3.apply(*p)
    assert s3.fight is None
    # …but extend one within grace
    p = parse_line(line(1, "You slash a gnoll for 10 points of damage."))
    s3.apply(*p)
    p = parse_line(line(8, "Nexus slashes a gnoll for 40 points of damage."))
    s3.apply(*p)
    assert s3.fight is not None and s3.fight.end.second == 8

    # coin parsing
    assert parse_coins("2 platinum, 4 gold, 3 silver, 9 copper") == 2439

    # session rollover after 60+ min idle
    s4 = SessionStats("Spin")
    p = parse_line(line(0, "You slash a rat for 5 points of damage."))
    s4.apply(*p)
    p = parse_line(line(4000, "You slash a rat for 5 points of damage."))
    s4.apply(*p)
    assert s4.session_start == now + timedelta(seconds=4000)

    # pet leader registration
    s5 = SessionStats("Spin")
    p = parse_line(line(0, "Gkzzallk says 'My leader is Spin.'"))
    s5.apply(*p)
    assert "Gkzzallk" in s5.pet_names

    print("Loremaster selftest: ALL PASS")
    print(f"  patterns: {len(PATTERNS)}  |  fight1 dps {f1.dps:.0f}  |  "
          f"session dps {snap['session_dps']:.0f}  |  ETL {fmt_eta(snap2['hours_to_level'])}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Spin\'s Loremaster — log parser & session tracker for Spin\'s UI Reloaded")
    ap.add_argument("--demo", action="store_true", help="run with a synthetic combat feed")
    ap.add_argument("--selftest", action="store_true", help="run parser/stats tests and exit")
    ap.add_argument("--log", help="tail one specific eqlog file")
    ap.add_argument("--log-dir", help="EverQuest Legends Logs directory")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    return run_gui(args)


if __name__ == "__main__":
    raise SystemExit(main())
