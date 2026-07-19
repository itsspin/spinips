#!/usr/bin/env python3
"""Spin's Loremaster — the log-reading companion for Spin's UI Reloaded.

A zero-dependency (Python standard library only) EverQuest Legends session
tracker themed to match the "Obsidian, Venom & Ember" skin
and shaped to dock into the reserved bottom-right zone of the 3440x1440
layout.

What it does
------------
* Tails your EverQuest Legends log file (offset-based, 500 ms polls).
* Auto-detects the active character and switches when you swap toons.
* Combat-aware DPS: fights open on your (or your pet's) first action and
  close after 10 s of silence; bystander activity only extends a fight
  within a 20 s grace window of your own last action.
* Live fight DPS, session DPS, best fight, and rolling encounter history.
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
    python loremaster.py --wait-for-eq # stay hidden until eqgame.exe starts
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

# In a windowed EXE (pyinstaller --windowed) there is no console; print()
# would explode on stdout=None.  Route to devnull so --selftest still runs.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Theme — matches Spin UI "Obsidian, Venom & Ember"
# ---------------------------------------------------------------------------
THEME = {
    "bg": "#090c11",
    "panel": "#10161d",
    "raised": "#17222a",
    "line": "#303f4e",
    "line_soft": "#1c2631",
    "gold": "#db9e2a",
    "gold_bright": "#facd5f",
    "cyan": "#34dabe",
    "text": "#eef2f3",
    "dim": "#92a1a9",
    "hp": "#de3e48",
    "mana": "#427ef4",
    "endur": "#db9e2a",
    "green": "#42cf8b",
    "ember": "#e5642d",
    "parchment": "#d8c89a",
    "void": "#040609",
    "meter": "#12302f",
    "meter_edge": "#1e7468",
}

# EverQuest writes eqlog_<Character>_<server>.txt (any character, any
# server) into the Logs folder inside the game directory; some installs
# write to the game root instead.  Every candidate is scanned and the most
# recently written log wins, so all players are covered automatically.
DEFAULT_LOG_DIRS = [
    r"C:\Users\Public\Daybreak Game Company\Installed Games\EverQuest Legends\Logs",
    r"C:\Users\Public\Daybreak Game Company\Installed Games\EverQuest Legends",
    r"C:\Users\Public\Daybreak Game Company\Installed Games\EverQuest",
    r"C:\Users\Public\Daybreak Game Company\Installed Games\EverQuest\Logs",
    r"C:\Program Files (x86)\Sony\EverQuest",
    r"C:\Program Files (x86)\Steam\steamapps\common\EverQuest\Logs",
    r"C:\Program Files (x86)\Steam\steamapps\common\EverQuest",
    str(Path.home() / "EverQuest Legends"),
]

SOURCE_DIR = Path(__file__).resolve().parent
if getattr(sys, "frozen", False):
    APP_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "SpinsLoremaster"
else:
    APP_DATA_DIR = SOURCE_DIR
CONFIG_PATH = APP_DATA_DIR / "loremaster_config.json"
DATA_DIR = APP_DATA_DIR / "loremaster_data"

# Combat pacing constants
COMBAT_GAP = timedelta(seconds=10)
BYSTANDER_GRACE = timedelta(seconds=20)
SESSION_GAP = timedelta(minutes=60)
POLL_MS = 500
LOG_RESCAN_SECONDS = 2.0
MAX_READ_BYTES = 256 * 1024
MAX_FIGHT_HISTORY = 500

TS_FORMAT = "%a %b %d %H:%M:%S %Y"


def process_is_running(image_name: str) -> bool:
    """Return True when a Windows process exists without spawning tasklist."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        kernel32.Process32FirstW.restype = wintypes.BOOL
        kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        kernel32.Process32NextW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        invalid = wintypes.HANDLE(-1).value
        if snapshot == invalid:
            return True  # fail open: never strand a legitimate manual launch
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        wanted = image_name.casefold()
        try:
            found = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while found:
                if entry.szExeFile.casefold() == wanted:
                    return True
                found = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)
        return False
    except Exception:
        return True


def wait_for_everquest() -> None:
    """Use a near-zero-cost process snapshot every two seconds until EQ runs."""
    while not process_is_running("eqgame.exe"):
        time.sleep(2.0)


def new_lifetime_stats() -> dict:
    """Small, record-worthy totals that survive session resets."""
    return {
        "kills": 0,
        "kill_breakdown": {},
        "group_kills": 0,
        "group_kill_breakdown": {},
        "deaths": 0,
        "best_dps": 0.0,
        "best_fight": "",
    }


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
    # --- alert-worthy lines ---
    ("tell_in", re.compile(r"^(?P<sender>[A-Za-z]+) tells you, '(?P<msg>.*)'$")),
    ("summoned", re.compile(r"^You have been summoned!?$")),
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
    sources: dict = field(default_factory=lambda: defaultdict(
        lambda: {"t": 0, "h": 0, "max": 0}))
    damage_taken: int = 0
    healing_done: int = 0
    heals_received: int = 0
    crits: int = 0
    misses: int = 0

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
    def __init__(self, character: str = "?", session_gap: timedelta | None = None):
        self.character = character
        self.session_gap = session_gap
        self.lifetime = new_lifetime_stats()
        self.reset()

    def reset(self):
        self.session_start: datetime | None = None
        self.last_event: datetime | None = None
        # combat
        self.fight: Fight | None = None
        self.fights: list[Fight] = []
        self.closed_damage = 0
        self.closed_seconds = 0.0
        self.best_fight: Fight | None = None
        self.last_own_action: datetime | None = None
        self.last_combat_signal: datetime | None = None
        self.damage_by_source: dict[str, dict] = defaultdict(
            lambda: {"t": 0, "h": 0, "max": 0})
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
        self.max_hit: tuple[int, str, str] | None = None   # (dmg, source, target)
        self.damage_taken_by: dict[str, dict] = defaultdict(lambda: {"t": 0, "h": 0})
        self.group_kills: dict[str, int] = defaultdict(int)
        self.zones: list[str] = []
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
        self.tells = 0

    def _lifetime_inc(self, key: str, amount: int | float = 1):
        self.lifetime[key] = self.lifetime.get(key, 0) + amount

    def _lifetime_named(self, key: str, name: str, amount: int = 1):
        values = self.lifetime.setdefault(key, {})
        values[name] = values.get(name, 0) + amount

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
        elif self.session_gap and self.last_event and ts - self.last_event > self.session_gap:
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
        if self.fight and (self.fight.damage > 0 or self.fight.healing_done > 0):
            self.fights.append(self.fight)
            if len(self.fights) > MAX_FIGHT_HISTORY:
                del self.fights[:100]
            self.closed_damage += self.fight.damage
            self.closed_seconds += self.fight.seconds
            if self.best_fight is None or self.fight.dps > self.best_fight.dps:
                self.best_fight = self.fight
            if self.fight.dps > float(self.lifetime.get("best_dps", 0.0)):
                self.lifetime["best_dps"] = self.fight.dps
                self.lifetime["best_fight"] = self.fight.name
        self.fight = None

    def finalize_idle(self, now: datetime | None = None):
        """Close a quiet fight promptly so the UI and lifetime totals settle."""
        if self.fight is None:
            return
        now = now or datetime.now()
        ref = self.last_combat_signal or self.fight.end
        if now - ref > COMBAT_GAP:
            self._close_fight()

    def _deal(self, ts: datetime, target: str, dmg: int, source: str, crit: bool = False):
        self._own_combat(ts)
        if self.fight is None:
            self.fight = Fight(start=ts, end=ts)
        self.fight.damage += dmg
        self.fight.targets[normalize_mob(target)] += dmg
        fight_src = self.fight.sources[source]
        fight_src["t"] += dmg
        fight_src["h"] += 1
        fight_src["max"] = max(fight_src["max"], dmg)
        if crit:
            self.fight.crits += 1
        src = self.damage_by_source[source]
        src["t"] += dmg
        src["h"] += 1
        src["max"] = max(src["max"], dmg)
        if self.max_hit is None or dmg > self.max_hit[0]:
            self.max_hit = (dmg, source, normalize_mob(target))
        if crit:
            self.crits += 1

    # -- event application ----------------------------------------------
    def apply(self, ts: datetime, kind: str, g: dict):
        if self.fight:
            ref = self.last_combat_signal or self.fight.end
            if ts - ref > COMBAT_GAP:
                self._close_fight()
        self._touch(ts)
        self.log_lines += 1
        crit = bool(g.get("crit"))

        if kind == "melee_out":
            self.melee_hits += 1
            self._deal(ts, g["target"], int(g["dmg"]), "Melee", crit)
        elif kind == "miss_out":
            self.melee_misses += 1
            self._own_combat(ts)
            if self.fight:
                self.fight.misses += 1
        elif kind == "dot_out":
            self._deal(ts, g["target"], int(g["dmg"]), f"DoT: {g['spell']}", crit)
        elif kind == "nuke_out_plain":
            self._deal(ts, g["target"], int(g["dmg"]), "Spells", crit)
        elif kind == "nuke_out_school":
            self._deal(ts, g["target"], int(g["dmg"]), f"Spell: {g['spell']}", crit)
        elif kind == "ds_out":
            self._deal(ts, g["target"], int(g["dmg"]), "Damage shield")

        elif kind == "melee_in":
            dmg = int(g["dmg"])
            self.damage_taken += dmg
            atk = self.damage_taken_by[normalize_mob(g["attacker"])]
            atk["t"] += dmg
            atk["h"] += 1
            self._own_combat(ts)
            if self.fight:
                self.fight.damage_taken += dmg
        elif kind in ("nuke_in", "dot_in", "nonmelee_in"):
            dmg = int(g["dmg"])
            self.damage_taken += dmg
            who = g.get("attacker")
            if who:
                atk = self.damage_taken_by[normalize_mob(who)]
                atk["t"] += dmg
                atk["h"] += 1
            self._own_combat(ts)
            if self.fight:
                self.fight.damage_taken += dmg
        elif kind == "miss_in":
            self.enemy_misses += 1
            self._own_combat(ts)

        elif kind == "kill_you":
            mob = normalize_mob(g["target"])
            self.kills[mob] += 1
            self._lifetime_inc("kills")
            self._lifetime_named("kill_breakdown", mob)
            self._own_combat(ts)
        elif kind == "death_you":
            self.deaths += 1
            self._lifetime_inc("deaths")
            self._close_fight()
        elif kind == "kill_other":
            killer = g["killer"].strip()
            mob = normalize_mob(g["target"])
            if killer == self.character or self.is_pet(killer):
                self.kills[mob] += 1
                self._lifetime_inc("kills")
                self._lifetime_named("kill_breakdown", mob)
            else:
                self.group_kills[mob] += 1
                self._lifetime_inc("group_kills")
                self._lifetime_named("group_kill_breakdown", mob)
            self._combat_signal(ts)

        elif kind == "heal_out":
            amt = int(g["amount"])
            self._own_combat(ts)
            self.healing_done += amt
            if self.fight:
                self.fight.healing_done += amt
            if g.get("attempted"):
                self.overheal += max(0, int(g["attempted"]) - amt)
        elif kind in ("heal_in", "heal_in_named"):
            amt = int(g["amount"])
            self.heals_received += amt
            if self.fight:
                self.fight.heals_received += amt

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
            coins = parse_coins(g["coins"])
            self.copper += coins

        elif kind == "faction":
            self.faction[g["faction"]] += int(g["delta"])
        elif kind == "zone":
            if not any(fp in g["zone"] for fp in ZONE_FALSE_POSITIVES):
                self.zone = g["zone"]
                if not self.zones or self.zones[-1] != g["zone"]:
                    self.zones.append(g["zone"])

        elif kind == "tell_in":
            self.tells += 1
        elif kind == "summoned":
            pass  # alert layer handles it
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
                self.melee_hits += 0  # pet swings tracked via source hits
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
            elif self.fight.damage > 0 or self.fight.healing_done > 0:
                pending = [self.fight]  # idle long enough: treat as closed
        history = self.fights[-10:] + pending
        closed_damage = self.closed_damage + sum(f.damage for f in pending)
        closed_seconds = self.closed_seconds + sum(f.seconds for f in pending)
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
        candidates = ([self.best_fight] if self.best_fight else []) + pending + ([live] if live else [])
        best = max(candidates, key=lambda f: f.dps, default=None)
        shown_fight = live or (pending[-1] if pending else (self.fights[-1] if self.fights else None))
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
            "fight": shown_fight,
            "fight_sources": ({k: dict(v) for k, v in shown_fight.sources.items()}
                              if shown_fight else {}),
            "fight_targets": dict(shown_fight.targets) if shown_fight else {},
            "fights": (history + ([live] if live else []))[-10:],
            "damage_by_source": {k: dict(v) for k, v in self.damage_by_source.items()},
            "damage_taken_by": {k: dict(v) for k, v in self.damage_taken_by.items()},
            "group_kills": dict(self.group_kills),
            "zones": list(self.zones),
            "max_hit": self.max_hit,
            "melee_dealt": self.damage_by_source.get("Melee", {"t": 0})["t"],
            "spell_dealt": sum(v["t"] for k, v in self.damage_by_source.items()
                               if k.startswith(("Spell", "DoT")) or k == "Spells"),
            "accuracy": (100.0 * self.melee_hits / (self.melee_hits + self.melee_misses)
                         if (self.melee_hits + self.melee_misses) else None),
            "session_start": self.session_start,
            "copper": self.copper,
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
            "lifetime": self.lifetime,
        }


# ---------------------------------------------------------------------------
# Log watching / character auto-detection
# ---------------------------------------------------------------------------
LOG_NAME_RE = re.compile(r"^eqlog_(?P<char>[A-Za-z]+)_(?P<server>[A-Za-z0-9.]+)\.txt$")


class LogWatcher:
    """Offset-based tail of the most recently active eqlog file."""

    def __init__(self, log_dir: str | None, explicit_log: str | None = None):
        # a configured dir is searched together with its Logs/ subfolder;
        # otherwise every existing default candidate is scanned.
        if log_dir:
            self.log_dirs = [Path(log_dir), Path(log_dir) / "Logs"]
        else:
            self.log_dirs = [Path(d) for d in DEFAULT_LOG_DIRS]
        # Keep not-yet-created Logs folders in the scan set: `/log on` may
        # create one after Loremaster has already launched.
        self.log_dirs = list(dict.fromkeys(self.log_dirs))
        self.explicit = Path(explicit_log) if explicit_log else None
        self.path: Path | None = None
        self.offset = 0
        self.character = "?"
        self.server = "?"
        self._fh = None
        self._partial = b""
        self._next_scan = 0.0

    def _pick(self) -> Path | None:
        if self.explicit:
            return self.explicit if self.explicit.exists() else None
        best, best_m = None, -1.0
        for d in self.log_dirs:
            for p in d.glob("eqlog_*.txt"):
                try:
                    m = p.stat().st_mtime
                except OSError:
                    continue
                if m > best_m:
                    best, best_m = p, m
        return best

    def poll(self) -> tuple[list[str], bool]:
        """Return (new_lines, switched_character)."""
        now = time.monotonic()
        target = self.path
        if self.explicit or self.path is None or now >= self._next_scan:
            target = self._pick()
            self._next_scan = now + LOG_RESCAN_SECONDS
        switched = False
        if target is None:
            return [], False
        if self.path != target:
            self.close()
            self.path = target
            m = LOG_NAME_RE.match(target.name)
            if m:
                self.character, self.server = m.group("char"), m.group("server")
            self.offset = target.stat().st_size  # start at live tail
            try:
                self._fh = target.open("rb")
                self._fh.seek(self.offset)
            except OSError:
                self._fh = None
            switched = True
            return [], switched
        try:
            size = self.path.stat().st_size
            if size < self.offset:  # rotated/truncated
                self.offset = 0
                self._partial = b""
                self.close(keep_path=True)
            if size == self.offset:
                return [], False
            if self._fh is None:
                self._fh = self.path.open("rb")
            self._fh.seek(self.offset)
            chunk = self._fh.read(min(MAX_READ_BYTES, size - self.offset))
            self.offset = self._fh.tell()
            data = self._partial + chunk
            parts = data.splitlines(keepends=True)
            self._partial = b""
            if parts and not parts[-1].endswith(b"\n"):
                self._partial = parts.pop()
            lines = [line.rstrip(b"\r\n").decode("latin-1", errors="replace") for line in parts]
            return lines, False
        except OSError:
            self.close(keep_path=True)
            return [], False

    def close(self, keep_path: bool = False):
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
        self._fh = None
        self._partial = b""
        if not keep_path:
            self.path = None


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Alerts — DBM/WeakAuras-style banners driven by log lines
# ---------------------------------------------------------------------------
def check_alerts(kind: str, g: dict, raw_msg: str, character: str, cfg: dict):
    """Return a list of (severity, text) alerts for one parsed event.
    severity: 'danger' (red), 'warn' (gold), 'info' (cyan)."""
    if not cfg.get("alerts_enabled", True):
        return []
    out = []
    if kind == "tell_in":
        out.append(("info", f"TELL \u2014 {g['sender']}: {g['msg'][:60]}"))
    elif kind == "summoned":
        out.append(("danger", "YOU HAVE BEEN SUMMONED"))
    elif kind == "death_you":
        out.append(("danger", f"YOU DIED \u2014 {g.get('killer', '?')}"))
    elif kind in ("melee_in", "nuke_in", "dot_in", "nonmelee_in"):
        dmg = int(g.get("dmg", 0))
        if dmg >= int(cfg.get("big_hit_threshold", 800)):
            out.append(("warn", f"BIG HIT \u2014 {dmg}"))
    if character and character != "?" and raw_msg:
        m = re.match(r"^(?P<who>[A-Za-z]+) tells the (?:group|raid|guild), '(?P<what>.*)'$", raw_msg)
        if m and character.lower() in m.group("what").lower() and m.group("who") != character:
            out.append(("warn", f"{m.group('who').upper()} CALLED YOU \u2014 {m.group('what')[:60]}"))
    for rule in cfg.get("custom_alerts", []):
        try:
            if re.search(rule.get("pattern", "$^"), raw_msg or ""):
                out.append((rule.get("severity", "info"), rule.get("text", raw_msg)[:80]))
        except re.error:
            continue
    return out


def load_config() -> dict:
    cfg = {
        "log_dir": None,
        "mini_mode": False,
        "opacity": 0.94,
        "position": None,
        "mini_position": None,
        "starred": ["session_dps", "xp_hr", "hours_to_level", "kills"],
        "starred_cards": ["combat", "kills", "money", "progress"],
        "alerts_enabled": True,
        "alert_sound": True,
        "alert_seconds": 4,
        "big_hit_threshold": 800,
        "alert_position": None,
        "fight_toasts": True,
        "auto_reset_minutes": 0,
        "custom_alerts": [],
    }
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text()))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg: dict) -> None:
    try:
        APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    except OSError:
        pass


def load_character_state(char: str) -> dict:
    try:
        return json.loads((DATA_DIR / f"{char}.json").read_text())
    except (OSError, ValueError):
        return {}


def normalize_lifetime(raw: dict | None) -> dict:
    """Merge persisted totals with the current schema (including v1 saves)."""
    totals = new_lifetime_stats()
    if not isinstance(raw, dict):
        return totals
    for key, default in totals.items():
        value = raw.get(key)
        if isinstance(default, dict):
            if isinstance(value, dict):
                totals[key] = {str(k): int(v) for k, v in value.items()
                               if isinstance(v, (int, float))}
        elif isinstance(value, (int, float)):
            totals[key] = value
        elif isinstance(default, str) and isinstance(value, str):
            totals[key] = value
    return totals


def save_character_state(char: str, stats: SessionStats) -> None:
    if char in ("?", ""):
        return
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        snap = stats.snapshot()
        session_key = stats.session_start.isoformat() if stats.session_start else "unknown"
        state = {
            "character": char,
            "level": snap["level"],
            "xp_since_level": snap["xp_since_level"],
            "pet_names": snap["pet_names"],
            "zone": snap["zone"],
            "last_session_key": session_key,
            "lifetime": normalize_lifetime(stats.lifetime),
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
    stats.lifetime = normalize_lifetime(st.get("lifetime"))


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


def fmt_coins(copper: int) -> str:
    p, rem = divmod(int(copper), 1000)
    g, rem = divmod(rem, 100)
    sv, c = divmod(rem, 10)
    parts = []
    if p: parts.append(f"{p}p")
    if g: parts.append(f"{g}g")
    if sv: parts.append(f"{sv}s")
    if c or not parts: parts.append(f"{c}c")
    return " ".join(parts)


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
        elif r < 0.9:
            emit("Stuka tells you, 'port up when you are ready'")
        elif r < 0.93:
            emit("Grimlord tells the group, 'Spin get over here!'")
        else:
            emit("You have slain a froglok shin knight!")
            emit("You gain party experience!! (0.42%)")
            emit("You receive 2 platinum, 4 gold from the corpse.")
            if random.random() < 0.3:
                emit("--You have looted a Froglok Fine Mesh from a froglok shin knight's corpse.--")
        return out


# ---------------------------------------------------------------------------
# Alert banners — frameless topmost strips, center-top, auto-fading
# ---------------------------------------------------------------------------
class AlertManager:
    COLORS = {"danger": ("#d93a3f", "#1a0d0e"), "warn": ("#c9a227", "#14110a"),
              "info": ("#41c7e4", "#0a1214")}

    def __init__(self, tk_module, root, cfg):
        self.tk = tk_module
        self.root = root
        self.cfg = cfg
        self.active = []

    def _beep(self, severity):
        if not self.cfg.get("alert_sound", True):
            return
        try:
            import winsound
            winsound.MessageBeep(
                winsound.MB_ICONHAND if severity == "danger" else winsound.MB_ICONASTERISK)
        except Exception:
            try:
                self.root.bell()
            except Exception:
                pass

    def show(self, severity, text_msg):
        tk = self.tk
        if len(self.active) >= 3:
            old_win = self.active.pop(0)
            try:
                old_win.destroy()
            except Exception:
                pass
        edge, body = self.COLORS.get(severity, self.COLORS["info"])
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        outer = tk.Frame(win, bg=edge, padx=2, pady=2)
        outer.pack()
        inner = tk.Frame(outer, bg=body, padx=18, pady=8)
        inner.pack()
        tk.Label(inner, text=text_msg, fg="#e8eaf0", bg=body,
                 font=("Segoe UI Semibold", 14)).pack()
        win.update_idletasks()
        pos = self.cfg.get("alert_position")
        if pos:
            ax, ay = pos
        else:
            ax = (win.winfo_screenwidth() - win.winfo_width()) // 2
            ay = 64
        win.geometry(f"+{ax}+{ay + len(self.active) * 56}")
        self.active.append(win)
        self._beep(severity)
        ttl = int(self.cfg.get("alert_seconds", 4) * 1000)
        win.after(ttl, lambda: self._dismiss(win))

    def _dismiss(self, win):
        if win in self.active:
            self.active.remove(win)
        try:
            for step in range(8):
                win.attributes("-alpha", 1.0 - step / 8)
                win.update()
                time.sleep(0.02)
        except Exception:
            pass
        try:
            win.destroy()
        except Exception:
            pass


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
    save_config(cfg)  # materialize defaults on first run; packaged builds use LocalAppData
    watcher = LogWatcher(cfg.get("log_dir"), args.log)
    reset_minutes = float(cfg.get("auto_reset_minutes", 0) or 0)
    session_gap = timedelta(minutes=reset_minutes) if reset_minutes > 0 else None
    stats = SessionStats(session_gap=session_gap)
    demo = DemoFeed() if args.demo else None
    if demo:
        stats.character = "Spin"
        watcher.character = "Spin"

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

    state = {"mini": bool(cfg.get("mini_mode")), "last_save": time.time(),
             "fights_seen": 0, "expanded": {"combat"}, "scope": "fight"}
    alerts = AlertManager(tk, root, cfg)

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

    FONT = ("Segoe UI", 11)
    FONT_S = ("Segoe UI", 9)
    FONT_B = ("Segoe UI Semibold", 11)
    FONT_BIG = ("Segoe UI Semibold", 19)
    FONT_MED = ("Segoe UI Semibold", 13)
    FONT_TITLE = ("Georgia", 11, "bold")
    FONT_RUNE = ("Georgia", 8, "bold")

    outer = tk.Frame(root, bg=T["gold"], padx=1, pady=1)   # 1px ember frame
    outer.pack(fill="both", expand=True)
    body = tk.Frame(outer, bg=T["bg"])
    body.pack(fill="both", expand=True)

    widgets: dict[str, tk.Widget] = {}

    def L(parent, text="", fg=None, font=FONT, bg=None, anchor="w", **kw):
        return tk.Label(parent, text=text, fg=fg or T["text"], bg=bg or parent["bg"],
                        font=font, anchor=anchor, **kw)

    # Loremaster's own voice: gold-ruled ledger sections (the equipment
    # screen's typography), hex bullets from the Spin UI crest language,
    # and an ember hero band up top.  Interaction stays glance -> expand.
    CARDS = [
        ("combat", "COMBAT"),
        ("kills", "SLAYING"),
        ("loot", "SPOILS"),
        ("money", "COIN"),
        ("progress", "PROGRESSION"),
        ("faction", "STANDING"),
        ("travels", "JOURNEY"),
    ]
    card_widgets: dict[str, dict] = {}

    def hex_bullet(parent, size=14, color=None, bg=None):
        c = tk.Canvas(parent, width=size, height=size, bg=bg or T["bg"],
                      highlightthickness=0)
        r = size / 2 - 1
        cx = cy = size / 2
        import math as _m
        pts = []
        for i in range(6):
            a = _m.radians(90 + i * 60)
            pts += [cx + r * _m.cos(a), cy + r * _m.sin(a)]
        c.create_polygon(pts, outline=color or T["gold"], fill="", width=1.2)
        return c

    def card_value(snap, key):
        if not state["mini"] and state["scope"] == "fight" and key == "combat":
            fight = snap["fight"]
            return f"{fmt_num(fight.dps)} dps" if fight else "awaiting combat"
        if not state["mini"] and state["scope"] == "records":
            life = snap["lifetime"]
            if key == "combat":
                return f"{fmt_num(life['best_dps'])} record dps"
            if key == "kills":
                extra = life.get("group_kills", 0)
                return f"{life['kills']} (+{extra})" if extra else str(life["kills"])
            if key == "loot":
                return "session only"
            if key == "money":
                return "session only"
            if key == "progress":
                return "session only"
            if key == "faction":
                return "session only"
            if key == "travels":
                return f"{life['deaths']} death" + ("s" if life["deaths"] != 1 else "")
            return ""
        if key == "combat":
            if snap["in_combat"]:
                return f"{fmt_num(snap['current_dps'])} dps \u2694"
            return f"{fmt_num(snap['session_dps'])} dps"
        if key == "kills":
            extra = sum(snap["group_kills"].values())
            return f"{snap['kills']} (+{extra})" if extra else f"{snap['kills']}"
        if key == "loot":
            n = sum(snap["loot"].values())
            return f"{n} item" + ("s" if n != 1 else "")
        if key == "money":
            return fmt_coins(snap["copper"])
        if key == "progress":
            if snap["xp_pct_known"]:
                return f"{snap['xp_pct']:.1f}% xp" + (f", +{stats.levelups} lvl" if stats.levelups else "")
            return f"{snap['xp_events']} xp gains"
        if key == "faction":
            return f"{len(stats.faction)} factions"
        if key == "travels":
            return f"{snap['deaths']} death" + ("s" if snap["deaths"] != 1 else "")
        return ""

    def card_detail(snap, key):
        """Return visual ledger rows; meter kinds embed a 0..1 bar share."""
        out = []
        if state["scope"] == "fight" and key == "combat":
            fight = snap["fight"]
            if not fight:
                return [("line", "Your next encounter will be recorded here in real time.", "")]
            status = "LIVE ENCOUNTER" if snap["in_combat"] else "LAST ENCOUNTER"
            out.append(("head", f"{status} · {fight.name}", fmt_dur(fight.seconds)))
            out.append(("line", f"{fmt_num(fight.damage)} damage · {fmt_num(fight.dps)} dps · "
                                f"{fight.crits} crits · {fight.misses} misses", ""))
            if fight.damage_taken or fight.healing_done or fight.heals_received:
                out.append(("line", f"Taken {fmt_num(fight.damage_taken)} · healed {fmt_num(fight.healing_done)} "
                                    f"· received {fmt_num(fight.heals_received)}", ""))
            sources = sorted(snap["fight_sources"].items(), key=lambda kv: -kv[1]["t"])
            if sources:
                out.append(("head", "Damage by ability", "total · share · dps"))
                for name, value in sources[:12]:
                    share = 100.0 * value["t"] / max(1, fight.damage)
                    out.append((f"meter:{share / 100.0:.4f}", name,
                                f"{fmt_num(value['t'])} · {share:.0f}% · {fmt_num(value['t'] / fight.seconds)}/s"))
                    out.append(("line", f"{value['h']} hits · avg {value['t'] / max(1, value['h']):.1f} "
                                        f"· max {fmt_num(value.get('max', 0))}", ""))
            targets = sorted(snap["fight_targets"].items(), key=lambda kv: -kv[1])
            if targets:
                out.append(("head", "Damage by target", "total · share"))
                for name, total in targets[:8]:
                    out.append((f"meter:{total / max(1, fight.damage):.4f}", name,
                                f"{fmt_num(total)} · {100.0 * total / max(1, fight.damage):.0f}%"))
            recent = [f for f in snap["fights"] if f is not fight][-6:]
            if recent:
                out.append(("head", "Recent encounters", "damage · dps"))
                for old in reversed(recent):
                    out.append(("row", old.name,
                                f"{fmt_num(old.damage)} · {fmt_num(old.dps)}/s"))
            return out
        if state["scope"] == "records":
            life = snap["lifetime"]
            if key == "combat":
                out.append(("row", "Best fight", f"{fmt_num(life['best_dps'])} dps"))
                if life.get("best_fight"):
                    out.append(("line", f"Record set against {life['best_fight']}", ""))
            elif key == "kills":
                rows = sorted(life["kill_breakdown"].items(), key=lambda kv: (-kv[1], kv[0]))
                for name, n in rows[:12]:
                    out.append(("row", name, f"\u00d7{n}"))
                if len(rows) > 12:
                    out.append(("line", f"\u2026and {len(rows) - 12} more creatures", ""))
                if life.get("group_kills"):
                    out.append(("head", "Witnessed group slayings", str(life["group_kills"])))
            elif key == "loot":
                out.append(("line", "Spoils reset with the live session.", ""))
            elif key == "money":
                out.append(("line", "Coin and plat/hour reset with the live session.", ""))
            elif key == "progress":
                out.append(("line", "XP rates, levels, AA, and casts are session stats.", ""))
            elif key == "faction":
                out.append(("line", "Faction standing remains a live-session ledger.", ""))
            elif key == "travels":
                out.append(("row", "Deaths recorded", str(life["deaths"])))
            return out
        if key == "combat":
            acc = f" \u00b7 {snap['accuracy']:.0f}% accuracy" if snap["accuracy"] is not None else ""
            out.append(("line", f"Dealt {fmt_num(snap['combat_damage'])} "
                                f"({fmt_num(snap['melee_dealt'])} melee / {fmt_num(snap['spell_dealt'])} spell)"
                                f" \u00b7 {snap['crits']} crits{acc}", ""))
            if snap["max_hit"]:
                d, src, tgt = snap["max_hit"]
                out.append(("line", f"Biggest hit: {fmt_num(d)} ({src} on {tgt})", ""))
            out.append(("line", f"Taken {fmt_num(snap['damage_taken'])} \u00b7 avoided {snap['enemy_misses']} attacks", ""))
            out.append(("line", f"Healing done {fmt_num(snap['healing_done'])} \u00b7 received {fmt_num(snap['heals_received'])}", ""))
            out.append(("line", f"Fizzles {snap['fizzles']} \u00b7 resists {snap['resists']}", ""))
            srcs = sorted(snap["damage_by_source"].items(), key=lambda kv: -kv[1]["t"])
            if srcs:
                out.append(("head", "Session damage by ability", "total · share · dps"))
                for name, v in srcs[:8]:
                    avg = v["t"] / max(1, v["h"])
                    share = 100.0 * v["t"] / max(1, snap["combat_damage"])
                    out.append((f"meter:{share / 100.0:.4f}", name,
                                f"{fmt_num(v['t'])} · {share:.0f}% · "
                                f"{fmt_num(v['t'] / max(1, snap['combat_seconds']))}/s"))
                    out.append(("line", f"{v['h']} hits · avg {avg:.1f} · max {fmt_num(v.get('max', 0))}", ""))
                if len(srcs) > 8:
                    out.append(("line", f"\u2026and {len(srcs) - 8} more", ""))
            taken = sorted(snap["damage_taken_by"].items(), key=lambda kv: -kv[1]["t"])
            if taken:
                out.append(("head", "Damage taken from", ""))
                for name, v in taken[:6]:
                    avg = v["t"] / max(1, v["h"])
                    out.append(("row", name, f"{fmt_num(v['t'])} \u00b7 {v['h']} hits \u00b7 avg {avg:.1f}"))
            if snap["fights"]:
                out.append(("head", "Recent encounters", "damage · dps · time"))
                for fight in reversed(snap["fights"][-8:]):
                    out.append(("row", fight.name,
                                f"{fmt_num(fight.damage)} · {fmt_num(fight.dps)}/s · {fmt_dur(fight.seconds)}"))
        elif key == "kills":
            rows = sorted(snap["kill_breakdown"].items(), key=lambda kv: -kv[1])
            for name, n in rows[:10]:
                out.append(("row", name, f"\u00d7{n}"))
            if len(rows) > 10:
                out.append(("line", f"\u2026and {len(rows) - 10} more", ""))
            grp = sorted(snap["group_kills"].items(), key=lambda kv: -kv[1])
            if grp:
                out.append(("head", "Group kills", ""))
                for name, n in grp[:5]:
                    out.append(("row", name, f"\u00d7{n}"))
        elif key == "loot":
            rows = sorted(snap["loot"].items(), key=lambda kv: -kv[1])
            for name, n in rows[:12]:
                out.append(("row", name, f"\u00d7{n}" if n > 1 else ""))
            if not rows:
                out.append(("line", "Nothing looted yet", ""))
        elif key == "money":
            out.append(("row", "Total", fmt_coins(snap["copper"])))
            out.append(("row", "Plat / hour", f"{snap['plat_hr']:.1f}p"))
        elif key == "progress":
            out.append(("row", "XP rate", f"{snap['xp_hr']:.1f}%/hr" if snap["xp_pct_known"] else "\u2014"))
            out.append(("row", "Time to level", fmt_eta(snap["hours_to_level"])))
            out.append(("row", "Into level", f"{snap['xp_since_level']:.1f}%" if snap["xp_pct_known"] else "\u2014"))
            out.append(("row", "Levels this session", str(stats.levelups)))
            out.append(("row", "AA points", str(stats.aa_points)))
            out.append(("row", "Songs twisted", f"{snap['songs']} ({snap['songs_min']:.1f}/min)"))
        elif key == "faction":
            rows = sorted(stats.faction.items(), key=lambda kv: kv[1])
            for name, d in rows[:10]:
                out.append(("row", name, f"{d:+d}"))
            if not rows:
                out.append(("line", "No faction hits yet", ""))
        elif key == "travels":
            out.append(("row", "Deaths", str(snap["deaths"])))
            zs = snap["zones"][-6:]
            if zs:
                out.append(("head", "Zones visited", ""))
                for z in zs:
                    out.append(("row", z, ""))
        return out

    def set_scope(scope):
        if scope == state["scope"]:
            return
        state["scope"] = scope
        for cw in card_widgets.values():
            cw["detail_signature"] = None
        refresh(force_detail=True)

    def toggle_card(key):
        if key in state["expanded"]:
            state["expanded"].discard(key)
        else:
            state["expanded"].add(key)
        refresh(force_detail=True)

    def toggle_card_star(key):
        starred = cfg.setdefault("starred_cards", [])
        if key in starred:
            starred.remove(key)
        else:
            starred.append(key)
        save_config(cfg)
        refresh(force_detail=True)

    def build_full():
        for w in body.winfo_children():
            w.destroy()
        card_widgets.clear()
        head = tk.Frame(body, bg=T["panel"])
        head.pack(fill="x")
        hx = hex_bullet(head, size=20, color=T["gold_bright"], bg=T["panel"])
        hx.pack(side="left", padx=(9, 5), pady=5)
        widgets["title"] = L(head, "SPIN'S LOREMASTER", fg=T["gold_bright"],
                             font=FONT_TITLE, bg=T["panel"])
        widgets["title"].pack(side="left")
        for txt, cmd in (("\u2715", do_quit), ("\u2014", toggle_mini), ("\u21ba", do_reset)):
            b = tk.Label(head, text=txt, fg=T["dim"], bg=T["panel"], font=FONT_B, cursor="hand2")
            b.pack(side="right", padx=5)
            b.bind("<Button-1>", lambda _e, c=cmd: c())
        widgets["dot"] = L(head, "\u25cf", fg=T["green"], font=FONT_S, bg=T["panel"])
        widgets["dot"].pack(side="right", padx=2)
        tk.Frame(body, bg=T["ember"], height=2).pack(fill="x")

        identity = tk.Frame(body, bg=T["bg"])
        identity.pack(fill="x", padx=10, pady=(4, 0))
        widgets["who"] = L(identity, "", fg=T["parchment"], font=FONT_RUNE)
        widgets["who"].pack(side="left")
        widgets["session"] = L(identity, "", fg=T["dim"], font=FONT_S, anchor="e")
        widgets["session"].pack(side="right")
        sub = tk.Frame(body, bg=T["bg"])
        sub.pack(fill="x", padx=10)
        widgets["zone"] = L(sub, "", fg=T["text"], font=FONT_S)
        widgets["zone"].pack(side="left", pady=1)
        L(sub, "THE ADVENTURER'S CHRONICLE", fg=T["line"], font=FONT_RUNE,
          anchor="e").pack(side="right")

        scopes = tk.Frame(body, bg=T["void"])
        scopes.pack(fill="x", padx=10, pady=(4, 1))
        for scope, label in (("fight", "FIGHT"),
                             ("session", "SESSION"),
                             ("records", "RECORDS")):
            tab = tk.Label(scopes, text=label, fg=T["dim"], bg=T["void"],
                           font=FONT_RUNE, cursor="hand2", pady=3)
            tab.pack(side="left", expand=True, fill="x")
            tab.bind("<Button-1>", lambda _e, s=scope: set_scope(s))
            widgets[f"scope_{scope}"] = tab

        # Ember hero band: live combat at a glance, or the permanent chronicle.
        hero = tk.Frame(body, bg=T["raised"])
        hero.pack(fill="x", padx=10, pady=(3, 2))
        tk.Frame(hero, bg=T["cyan"], width=3).pack(side="left", fill="y")
        for key, label, color in (("current_dps", "FIGHT DPS", T["gold_bright"]),
                                  ("session_dps", "SESSION", T["text"]),
                                  ("best_dps", "BEST", T["cyan"])):
            if key != "current_dps":
                tk.Frame(hero, bg=T["line"], width=1).pack(side="left", fill="y", pady=7)
            cell = tk.Frame(hero, bg=T["raised"])
            cell.pack(side="left", expand=True, fill="both", pady=3)
            widgets[key] = L(cell, "0", fg=color, font=FONT_BIG,
                             bg=T["raised"], anchor="center")
            widgets[key].pack(fill="x")
            widgets[f"{key}_label"] = L(cell, label, fg=T["dim"], font=FONT_RUNE,
                                         bg=T["raised"], anchor="center")
            widgets[f"{key}_label"].pack(fill="x")
        rule = tk.Frame(body, bg=T["gold"], height=2)
        rule.pack(fill="x", padx=10, pady=(3, 4))

        # scrollable ledger
        wrap = tk.Frame(body, bg=T["bg"])
        wrap.pack(fill="both", expand=True, padx=6, pady=(0, 4))
        canvas = tk.Canvas(wrap, bg=T["bg"], highlightthickness=0, width=396)
        vsb = tk.Scrollbar(wrap, orient="vertical", command=canvas.yview,
                           troughcolor=T["bg"], bg=T["raised"], width=8)
        inner = tk.Frame(canvas, bg=T["bg"])
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(inner_id, width=e.width))
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"))
        canvas.bind_all("<Button-4>", lambda _e: canvas.yview_scroll(-1, "units"))
        canvas.bind_all("<Button-5>", lambda _e: canvas.yview_scroll(1, "units"))

        for key, label in CARDS:
            sect = tk.Frame(inner, bg=T["bg"])
            sect.pack(fill="x", pady=(5, 0))
            row = tk.Frame(sect, bg=T["bg"], cursor="hand2")
            row.pack(fill="x", padx=4)
            hb = hex_bullet(row, size=12)
            hb.pack(side="left", pady=2)
            nm = L(row, label, fg=T["gold"], font=FONT_S, bg=T["bg"])
            nm.pack(side="left", padx=(6, 0))
            chev = L(row, "\u25b8", fg=T["dim"], font=FONT_S, bg=T["bg"])
            chev.pack(side="right")
            star = L(row, "\u2726", fg=T["line"], font=FONT_S, bg=T["bg"], cursor="hand2")
            star.pack(side="right", padx=6)
            val = L(row, "\u2014", fg=T["text"], font=FONT_B, bg=T["bg"], anchor="e")
            val.pack(side="right", padx=(0, 4), fill="x", expand=True)
            # the gold rule under each section header — equipment-screen DNA
            rl = tk.Frame(sect, bg=T["gold"], height=1)
            rl.pack(fill="x", padx=4, pady=(1, 0))
            rl2 = tk.Frame(sect, bg="#050609", height=1)
            rl2.pack(fill="x", padx=4)
            detail = tk.Frame(sect, bg=T["bg"])
            for w in (row, hb, nm, val, chev):
                w.bind("<Button-1>", lambda _e, k=key: toggle_card(k))
            star.bind("<Button-1>", lambda _e, k=key: toggle_card_star(k))
            card_widgets[key] = {"value": val, "star": star, "chev": chev,
                                 "name": nm, "hex": hb, "rule": rl,
                                 "detail": detail, "detail_signature": None}
        footer = tk.Frame(body, bg=T["panel"])
        footer.pack(fill="x")
        widgets["status"] = L(footer, "Loremaster awaits your log\u2026",
                              fg=T["dim"], font=FONT_S, bg=T["panel"])
        widgets["status"].pack(side="left", fill="x", expand=True, padx=(10, 4), pady=5)
        widgets["locate"] = tk.Label(
            footer, text="LOCATE LOG", fg=T["cyan"], bg=T["raised"],
            font=FONT_RUNE, cursor="hand2", padx=7, pady=3)
        widgets["locate"].pack(side="right", padx=(0, 6), pady=3)
        widgets["locate"].bind("<Button-1>", choose_log_dir)
        pos = cfg.get("position")
        root.geometry(f"430x560+{pos[0]}+{pos[1]}" if pos else "430x560+2782+586")

    def build_mini():
        for w in body.winfo_children():
            w.destroy()
        card_widgets.clear()
        strip = tk.Frame(body, bg=T["bg"])
        strip.pack(fill="both", expand=True)
        cap = tk.Frame(strip, bg=T["gold"], width=3)
        cap.pack(side="left", fill="y")
        cells = tk.Frame(strip, bg=T["bg"])
        cells.pack(side="left", fill="both", expand=True, padx=4, pady=3)
        widgets["mini_cells"] = cells
        widgets["mini_items"] = {}
        names = dict(CARDS)
        starred = [k for k in (cfg.get("starred_cards") or ["combat"]) if k in names]
        for i, key in enumerate(starred):
            if i:
                tk.Frame(cells, bg=T["gold"], width=1, height=14).pack(
                    side="left", padx=6, pady=2)
            tk.Label(cells, text=names[key], fg=T["gold"], bg=T["bg"],
                     font=FONT_RUNE).pack(side="left")
            value = tk.Label(cells, text="—", fg=T["text"], bg=T["bg"], font=FONT_B)
            value.pack(side="left", padx=(4, 0))
            widgets["mini_items"][key] = value
        locate = tk.Label(strip, text="LOG", fg=T["gold_bright"], bg=T["raised"],
                          font=FONT_RUNE, cursor="hand2", padx=4)
        locate.pack(side="right", padx=(2, 0), pady=3)
        locate.bind("<Button-1>", choose_log_dir)
        b = tk.Label(strip, text="\u25a3", fg=T["dim"], bg=T["bg"], font=FONT_B, cursor="hand2")
        b.pack(side="right", padx=4)
        b.bind("<Button-1>", lambda _e: toggle_mini())
        pos = cfg.get("mini_position")
        root.geometry(f"+{pos[0]}+{pos[1]}" if pos else "+2792+1118")

    def toggle_mini():
        state["mini"] = not state["mini"]
        cfg["mini_mode"] = state["mini"]
        save_config(cfg)
        (build_mini if state["mini"] else build_full)()

    def do_reset():
        stats.reset()
        state["fights_seen"] = 0

    def choose_log_dir(_event=None):
        nonlocal watcher
        from tkinter import filedialog
        initial = cfg.get("log_dir")
        if not initial or not Path(initial).is_dir():
            initial = str(Path.home())
        selected = filedialog.askdirectory(
            parent=root,
            title="Choose the EverQuest folder or its Logs folder",
            initialdir=initial,
            mustexist=True,
        )
        if not selected:
            return
        cfg["log_dir"] = str(Path(selected))
        save_config(cfg)
        watcher.close()
        watcher = LogWatcher(cfg["log_dir"], args.log)
        if widgets.get("status"):
            widgets["status"].configure(text="searching for the newest eqlog…")
        state["fights_seen"] = 0

    def do_quit():
        if not demo:
            save_character_state(stats.character, stats)
        save_config(cfg)
        watcher.close()
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
                stats.__init__(watcher.character, session_gap=session_gap)
                stats.character = watcher.character
                restore_character_state(stats)
                state["fights_seen"] = 0
        for raw in lines:
            raw_msg = raw.split("] ", 1)[1] if "] " in raw else raw
            parsed = parse_line(raw)
            kind, groups = "", {}
            if parsed:
                ts, kind, groups = parsed
                stats.character = watcher.character if not demo else stats.character
                stats.apply(ts, kind, groups)
            for severity, text_msg in check_alerts(kind, groups, raw_msg,
                                                   stats.character, cfg):
                alerts.show(severity, text_msg)
        stats.finalize_idle(datetime.now())
        if cfg.get("fight_toasts", True):
            done = len(stats.fights)
            if done > state["fights_seen"] and stats.fights:
                f = stats.fights[-1]
                alerts.show("info", f"{f.name}  \u2014  {fmt_num(f.dps)} dps  ({fmt_num(f.damage)} in {fmt_dur(f.seconds)})")
            state["fights_seen"] = done
        refresh()
        if not demo and time.time() - state["last_save"] > 30:
            save_character_state(stats.character, stats)
            state["last_save"] = time.time()
        root.after(POLL_MS, tick)

    def refresh(force_detail=False):
        snap = stats.snapshot(datetime.now())
        if state["mini"]:
            items = widgets.get("mini_items")
            if not items:
                return
            for key, label in items.items():
                value = card_value(snap, key)
                if label.cget("text") != value:
                    label.configure(text=value)
            return

        title = snap["character"]
        if watcher.server != "?":
            title += f" ({watcher.server})"
        widgets["who"].configure(text=title)
        widgets["dot"].configure(fg=THEME["green"] if (demo or watcher.path) else THEME["dim"])
        widgets["zone"].configure(text=snap["zone"] or "\u2014")
        if snap["session_start"]:
            dur = fmt_dur(snap["hours"] * 3600)
            since = snap["session_start"].strftime("%I:%M %p").lstrip("0")
            widgets["session"].configure(text=f"session {dur} (since {since})")
        for scope in ("fight", "session", "records"):
            active = state["scope"] == scope
            widgets[f"scope_{scope}"].configure(
                bg=THEME["raised"] if active else THEME["void"],
                fg=THEME["cyan"] if active else THEME["dim"])
        if state["scope"] == "records":
            life = snap["lifetime"]
            widgets["current_dps"].configure(text=fmt_num(life["kills"]), fg=THEME["gold_bright"])
            widgets["session_dps"].configure(text=fmt_num(len(life["kill_breakdown"])))
            widgets["best_dps"].configure(text=fmt_num(life["best_dps"]))
            for key, label in (("current_dps", "NPC KILLS"),
                               ("session_dps", "CREATURE TYPES"),
                               ("best_dps", "RECORD DPS")):
                widgets[f"{key}_label"].configure(text=label)
        else:
            shown = snap["fight"]
            fight_dps = snap["current_dps"] if snap["in_combat"] else (shown.dps if shown else 0)
            widgets["current_dps"].configure(
                text=fmt_num(fight_dps),
                fg=THEME["gold_bright"] if snap["in_combat"] else THEME["dim"])
            widgets["session_dps"].configure(text=fmt_num(snap["session_dps"]))
            best = snap["best_fight"]
            widgets["best_dps"].configure(text=fmt_num(best.dps) if best else "0")
            for key, label in (("current_dps", "FIGHT DPS"),
                               ("session_dps", "SESSION"),
                               ("best_dps", "BEST")):
                widgets[f"{key}_label"].configure(text=label)
        starred = cfg.get("starred_cards", [])
        for key, _label in CARDS:
            cw = card_widgets.get(key)
            if not cw:
                continue
            cw["value"].configure(text=card_value(snap, key))
            cw["star"].configure(text="\u2726" if key in starred else "\u25c7",
                                 fg=THEME["gold_bright"] if key in starred else THEME["line"])
            expanded = key in state["expanded"]
            accent = THEME["cyan"] if expanded else THEME["line_soft"]
            if state["scope"] == "records" and key in ("kills", "travels"):
                accent = THEME["gold"]
            cw["name"].configure(fg=accent if expanded else THEME["dim"])
            cw["rule"].configure(bg=accent)
            cw["hex"].itemconfigure("all", outline=accent)
            cw["chev"].configure(text="\u25be" if expanded else "\u25b8")
            if expanded:
                rows = card_detail(snap, key)
                signature = tuple(rows)
                if (force_detail or not cw["detail"].winfo_children()
                        or signature != cw["detail_signature"]):
                    for w in cw["detail"].winfo_children():
                        w.destroy()
                    for kind, left, right in rows:
                        r = tk.Frame(cw["detail"], bg=THEME["bg"])
                        r.pack(fill="x", padx=14, pady=0)
                        if kind.startswith("meter:"):
                            proportion = max(0.0, min(1.0, float(kind.split(":", 1)[1])))
                            meter = tk.Canvas(r, height=19, bg=THEME["bg"], highlightthickness=0)
                            meter.pack(fill="x")

                            def draw_meter(_event=None, canvas=meter, pct=proportion,
                                           lhs=left, rhs=right):
                                width = max(1, canvas.winfo_width())
                                canvas.delete("all")
                                canvas.create_rectangle(0, 2, max(2, int(width * pct)), 17,
                                                        fill=THEME["meter"], outline="")
                                canvas.create_line(0, 2, max(2, int(width * pct)), 2,
                                                   fill=THEME["meter_edge"])
                                canvas.create_text(3, 10, text=lhs, fill=THEME["text"],
                                                   font=FONT_S, anchor="w")
                                canvas.create_text(width - 3, 10, text=rhs,
                                                   fill=THEME["gold_bright"],
                                                   font=FONT_S, anchor="e")

                            meter.bind("<Configure>", draw_meter)
                        elif kind == "head":
                            tk.Label(r, text=left, fg=THEME["gold"], bg=THEME["bg"],
                                     font=FONT_S, anchor="w").pack(side="left", pady=(4, 1))
                            if right:
                                tk.Label(r, text=right, fg=THEME["gold_bright"], bg=THEME["bg"],
                                         font=FONT_S, anchor="e").pack(side="right", pady=(4, 1))
                        elif kind == "line":
                            tk.Label(r, text=left, fg=THEME["dim"], bg=THEME["bg"],
                                     font=FONT_S, anchor="w", justify="left"
                                     ).pack(side="left")
                        else:
                            tk.Label(r, text=left, fg=THEME["text"], bg=THEME["bg"],
                                     font=FONT_S, anchor="w").pack(side="left")
                            tk.Label(r, text=right, fg=THEME["gold_bright"], bg=THEME["bg"],
                                     font=FONT_S, anchor="e").pack(side="right")
                    cw["detail_signature"] = signature
                cw["detail"].pack(fill="x", pady=(0, 4))
            else:
                cw["detail"].pack_forget()

        if demo:
            src_txt = "demo mode \u2014 synthetic fight"
        elif watcher.path:
            src_txt = f"tailing {watcher.path.name}"
        else:
            src_txt = "no eqlog found \u2014 /log on, then click LOCATE LOG"
        widgets["status"].configure(text=src_txt)
        widgets["locate"].configure(text="CHANGE" if watcher.path else "LOCATE LOG")

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

    stats.finalize_idle(now + timedelta(seconds=80))
    snap = stats.snapshot(now + timedelta(seconds=80))

    # Two damage encounters plus a healing encounter are retained separately.
    assert len(snap["fights"]) == 3, snap["fights"]
    f1, heal_fight, f2 = snap["fights"]
    assert f1.damage == 775, f1.damage
    assert f1.name == "Froglok shin knight", f1.name
    assert heal_fight.healing_done == 500 and heal_fight.damage == 0
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
    assert snap["lifetime"]["kills"] == 2
    assert snap["lifetime"]["best_dps"] > 0
    assert f1.sources["Melee"] == {"t": 400, "h": 2, "max": 300}
    assert f1.sources["Pet (Gann)"]["t"] == 50
    assert f1.damage_taken == 60 and f1.crits == 1 and f1.misses == 1

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
    s4 = SessionStats("Spin", session_gap=SESSION_GAP)
    p = parse_line(line(0, "You slash a rat for 5 points of damage."))
    s4.apply(*p)
    p = parse_line(line(4000, "You slash a rat for 5 points of damage."))
    s4.apply(*p)
    assert s4.session_start == now + timedelta(seconds=4000)
    s4_manual = SessionStats("Spin")
    p = parse_line(line(0, "You slash a rat for 5 points of damage."))
    s4_manual.apply(*p)
    p = parse_line(line(4000, "You slash a rat for 5 points of damage."))
    s4_manual.apply(*p)
    assert s4_manual.session_start == now  # default session lasts until reset/exit
    assert set(snap["lifetime"]) == {
        "kills", "kill_breakdown", "group_kills", "group_kill_breakdown",
        "deaths", "best_dps", "best_fight",
    }

    # pet leader registration
    s5 = SessionStats("Spin")
    p = parse_line(line(0, "Gkzzallk says 'My leader is Spin.'"))
    s5.apply(*p)
    assert "Gkzzallk" in s5.pet_names

    # enriched engine fields (card detail views)
    assert snap["damage_by_source"]["Pet (Gann)"]["t"] == 50
    assert snap["damage_by_source"]["Melee"]["h"] == 3
    assert snap["max_hit"][0] == 300 and snap["max_hit"][1] == "Melee"
    assert snap["damage_taken_by"]["Froglok shin knight"] == {"t": 60, "h": 1}
    assert snap["zones"] == ["Blackburrow"]
    assert fmt_coins(2439) == "2p 4g 3s 9c" and fmt_coins(0) == "0c" and fmt_coins(1000) == "1p"
    s6 = SessionStats("Spin")
    pp = parse_line(line(0, "A gnoll has been slain by Grimlord!"))
    s6.apply(*pp)
    assert s6.group_kills["Gnoll"] == 1 and s6.kills == {}

    # alert engine
    cfg = {"alerts_enabled": True, "big_hit_threshold": 500,
           "custom_alerts": [{"pattern": "begins to cast a spell", "text": "MOB CASTING", "severity": "warn"}]}
    p = parse_line(line(0, "Stuka tells you, 'any chance of a port?'"))
    assert p and p[1] == "tell_in", p
    a = check_alerts(p[1], p[2], "Stuka tells you, 'any chance of a port?'", "Spin", cfg)
    assert a and a[0][0] == "info" and "Stuka" in a[0][1]
    p = parse_line(line(0, "Gann tells you, 'Attacking a froglok shin knight Master.'"))
    assert p and p[1] == "pet_attack", "tell_in must not swallow pet lines"
    p = parse_line(line(0, "You have been summoned!"))
    assert p and p[1] == "summoned"
    assert check_alerts("summoned", {}, "", "Spin", cfg)[0][0] == "danger"
    p = parse_line(line(0, "A froglok shin knight hits YOU for 900 points of damage."))
    a = check_alerts(p[1], p[2], "", "Spin", cfg)
    assert any("BIG HIT" in t for _sev, t in a)
    a = check_alerts("", {}, "Grimlord tells the group, 'Spin to the east wall'", "Spin", cfg)
    assert any("CALLED YOU" in t for _sev, t in a)
    a = check_alerts("", {}, "A froglok king begins to cast a spell.", "Spin", cfg)
    assert any(t == "MOB CASTING" for _sev, t in a)
    assert check_alerts("summoned", {}, "", "Spin", {"alerts_enabled": False}) == []

    # log discovery: newest eqlog wins across EQ root + Logs subfolder
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "Logs").mkdir()
        older = root / "Logs" / "eqlog_Alt_qeynos.txt"
        newer = root / "eqlog_Spin_qeynos.txt"
        older.write_text("x")
        newer.write_text("x")
        os.utime(older, (1000, 1000))
        os.utime(newer, (2000, 2000))
        w = LogWatcher(str(root))
        assert w._pick() == newer, w._pick()
        os.utime(older, (3000, 3000))
        assert w._pick() == older
        # The tailer preserves an unfinished write and emits it only once the
        # terminating newline arrives.
        w = LogWatcher(None, str(newer))
        assert w.poll() == ([], True)
        with newer.open("ab") as fh:
            fh.write(b"[Sun Jul 19 20:00:00 2026] You have slain a")
        assert w.poll() == ([], False)
        with newer.open("ab") as fh:
            fh.write(b" rat!\r\n")
        lines, switched = w.poll()
        assert not switched and lines == ["[Sun Jul 19 20:00:00 2026] You have slain a rat!"]
        w.close()
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
    ap.add_argument("--wait-for-eq", action="store_true",
                    help="remain hidden and idle until eqgame.exe is running")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.wait_for_eq and not args.demo:
        wait_for_everquest()
    return run_gui(args)


if __name__ == "__main__":
    raise SystemExit(main())
