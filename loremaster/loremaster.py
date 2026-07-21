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
* Encounter Lab with current/previous/session views, actor/ability/healing
  meters, multi-mob target breakdowns, and a two-second combat timeline.
* Pet damage attribution (learns pet names from pet speech) + active pet
  count for swarm/multiclass play.
* Bard song counting (songs twisted, songs/min) — WAR/DRU/BRD approved.
* XP tracking: xp events, xp %/hr when the server logs percentages, level
  ups, and estimated time to level.
* Kills (per-creature breakdown), deaths, heals in/out, damage taken,
  loot log, coin (plat/hr), faction hits, skill-ups, fizzles/resists.
* HUD mode: a slim EQ-only overlay strip with your starred stats.
* Lore Lens: Ctrl+Shift+E reads a hovered item with on-demand Windows OCR,
  then uses safe EQL Wiki parsing, background I/O, and an offline cache.
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

from hover_ocr import HoverOcrService
from log_ingest import (
    LineBatchRecord,
    LogIngestWorker,
    StatusRecord,
    SwitchRecord,
)

from wiki_overlay import (
    DISPLAY_SECTIONS,
    EMPTY_SECTION_TEXT,
    WikiCache,
    WikiClient,
    WikiError,
    WikiItem,
    WikiLookupService,
    WikiNotFoundError,
    WikiOfflineError,
    clipboard_lookup_plan,
    extract_item_query,
    format_cache_age,
    normalize_item_name,
    parse_hotkey,
    selftest as wiki_selftest,
)

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
    r"C:\EQLegends\Logs",
    r"C:\EQLegends",
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
WIKI_CACHE_DIR = APP_DATA_DIR / "wiki_cache"

# Combat pacing constants
COMBAT_GAP = timedelta(seconds=10)
BYSTANDER_GRACE = timedelta(seconds=20)
SESSION_GAP = timedelta(minutes=60)
POLL_MS = 500
LOG_RESCAN_SECONDS = 2.0
MAX_READ_BYTES = 256 * 1024
INITIAL_BACKFILL_BYTES = 2 * 1024 * 1024
INITIAL_BACKFILL_MINUTES = 30
MAX_FIGHT_HISTORY = 500
TIMELINE_BUCKET_SECONDS = 2
MINI_BASE_WIDTH = 720
MINI_BASE_HEIGHT = 34

TS_FORMAT = "%a %b %d %H:%M:%S %Y"
_EQ_PID_CACHE = {"expires": 0.0, "ids": set()}
_INSTANCE_MUTEXES = {}


def _acquire_instance_mutex(name: str) -> bool:
    if os.name != "nt" or name in _INSTANCE_MUTEXES:
        return True
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL,
                                          wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.CreateMutexW(None, False, name)
        if not handle:
            return True  # Do not prevent launch if Windows denied the mutex.
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(handle)
            return False
        _INSTANCE_MUTEXES[name] = handle
    except (AttributeError, OSError):
        return True
    return True


def acquire_single_instance() -> bool:
    """Keep normal launches to one lightweight overlay per Windows session."""
    return _acquire_instance_mutex("Local\\SpinsLoremaster.Singleton")


def acquire_waiter_instance() -> bool:
    """Prevent duplicate invisible startup waiters without blocking manual UI."""
    return _acquire_instance_mutex("Local\\SpinsLoremaster.Waiter")


def process_ids(image_name: str) -> set[int] | None:
    """Return matching Windows process IDs, or None if enumeration failed."""
    if os.name != "nt":
        return {os.getpid()}
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
            return None
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        wanted = image_name.casefold()
        result: set[int] = set()
        try:
            found = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while found:
                if entry.szExeFile.casefold() == wanted:
                    result.add(int(entry.th32ProcessID))
                found = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)
        return result
    except Exception:
        return None


def process_is_running(image_name: str) -> bool:
    """Return True when a process exists; fail open for manual launches."""
    ids = process_ids(image_name)
    return True if ids is None else bool(ids)


def foreground_is_everquest_or_loremaster(window_handle: int) -> bool:
    """Float over EQ/Loremaster, but drop below unrelated foreground apps."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        foreground = user32.GetForegroundWindow()
        if not foreground:
            return False
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(foreground, ctypes.byref(pid))
        now = time.monotonic()
        if now >= _EQ_PID_CACHE["expires"]:
            _EQ_PID_CACHE["ids"] = process_ids("eqgame.exe") or set()
            _EQ_PID_CACHE["expires"] = now + 2.0
        eq_pids = _EQ_PID_CACHE["ids"]
        return (int(foreground) == int(window_handle) or int(pid.value) == os.getpid()
                or int(pid.value) in eq_pids)
    except Exception:
        return False


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
        r"^You try to \w+(?: on)? (?P<target>.+?), but (?P<reason>.+?)!(?: \([^)]+\))?$")),
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
        r"^(?P<attacker>.+?) tries to \w+(?: on)? YOU, but (?P<reason>.+?)!(?: \([^)]+\))?$")),
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
    # EQL builds do not consistently emit a class trio.  When one does, only
    # this explicit system-style sentence is eligible for exact inference;
    # normal chat and spell names are never guessed as a composition.
    ("composition", re.compile(
        r"^(?:Your active classes are|Your active class composition is|"
        r"Your class composition is|Active classes:)\s+(?P<classes>.+?)[.!]?$",
        re.I)),
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

# EverQuest Legends characters carry three active classes.  Keep this list
# deliberately closed so a stray chat line can never silently mislabel an
# encounter.  Class order is preserved because players commonly identify a
# build by its primary/secondary/tertiary ordering.
EQL_CLASS_NAMES = {
    "WAR": "Warrior", "CLR": "Cleric", "PAL": "Paladin",
    "RNG": "Ranger", "SHD": "Shadow Knight", "DRU": "Druid",
    "MNK": "Monk", "BRD": "Bard", "ROG": "Rogue", "SHM": "Shaman",
    "NEC": "Necromancer", "WIZ": "Wizard", "MAG": "Magician",
    "ENC": "Enchanter", "BST": "Beastlord", "BER": "Berserker",
}
EQL_CLASS_ALIASES = {
    **{abbr.casefold(): abbr for abbr in EQL_CLASS_NAMES},
    **{name.casefold(): abbr for abbr, name in EQL_CLASS_NAMES.items()},
    "shadowknight": "SHD", "shadow knight": "SHD", "sk": "SHD",
    "cler": "CLR", "cleric": "CLR", "pally": "PAL", "ranger": "RNG",
    "druid": "DRU", "monk": "MNK", "bard": "BRD", "rogue": "ROG",
    "sham": "SHM", "shaman": "SHM", "necro": "NEC",
    "necromancer": "NEC", "wizard": "WIZ", "mage": "MAG",
    "magician": "MAG", "enchanter": "ENC", "chanter": "ENC",
    "beastlord": "BST", "beast": "BST", "berserker": "BER",
    "zerker": "BER", "warrior": "WAR",
}


def normalize_composition(value) -> str:
    """Return a canonical three-class EQL loadout or raise ValueError.

    Accepted examples include ``WAR/BRD/DRU`` and
    ``Warrior, Bard, and Druid``.  Requiring exactly three distinct known
    classes is important: composition data is only useful when it is exact.
    """
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        raw_parts = [str(part).strip() for part in value]
    else:
        text = str(value).strip()
        if not text:
            return ""
        text = re.sub(r"\s+and\s+", "/", text, flags=re.I)
        raw_parts = [part.strip() for part in re.split(r"\s*[/,+]\s*", text)]
        if len(raw_parts) == 1:
            # Space-separated abbreviations are convenient, but full class
            # names containing spaces still need an explicit slash/comma.
            words = text.split()
            if len(words) == 3:
                raw_parts = words
    parts = [part for part in raw_parts if part]
    if len(parts) != 3:
        raise ValueError("Enter exactly three classes, for example WAR / BRD / DRU.")
    canonical = []
    for part in parts:
        abbr = EQL_CLASS_ALIASES.get(part.casefold())
        if not abbr:
            raise ValueError(f"Unknown EQL class '{part}'. Use class abbreviations like WAR or DRU.")
        canonical.append(abbr)
    if len(set(canonical)) != 3:
        raise ValueError("A loadout must contain three different classes.")
    return " / ".join(canonical)


COMPOSITION_MESSAGE_RE = re.compile(
    r"^(?:Your active classes are|Your active class composition is|"
    r"Your class composition is|Active classes:)\s+(?P<classes>.+?)[.!]?$",
    re.I,
)


def infer_composition_from_message(message: str) -> str:
    """Infer only from an explicit three-class system-style announcement."""
    match = COMPOSITION_MESSAGE_RE.fullmatch((message or "").strip())
    if not match:
        return ""
    try:
        return normalize_composition(match.group("classes"))
    except ValueError:
        return ""


def composition_comparisons(fights, selected, mode="same") -> list:
    """Return preceding encounters matching a same/other/all loadout filter."""
    if selected is None or mode not in {"same", "other", "all"}:
        return []
    try:
        selected_index = next(i for i, fight in enumerate(fights) if fight is selected)
    except StopIteration:
        return []
    current = selected.composition
    matches = []
    for fight in fights[:selected_index]:
        if mode == "same" and (not current or fight.composition != current):
            continue
        if mode == "other" and (not current or not fight.composition
                                or fight.composition == current):
            continue
        matches.append(fight)
    return matches


def summarize_compositions(fights) -> list[dict]:
    """Build small rolling loadout summaries without retaining extra state."""
    grouped: dict[str, list] = defaultdict(list)
    for fight in fights:
        grouped[fight.composition or "UNSET"].append(fight)
    summaries = []
    for composition, rows in grouped.items():
        summaries.append({
            "composition": composition,
            "fights": len(rows),
            "average_dps": sum(fight.dps for fight in rows) / len(rows),
            "best_dps": max(fight.dps for fight in rows),
            "damage": sum(fight.damage for fight in rows),
        })
    return sorted(summaries, key=lambda row: (-row["average_dps"], row["composition"]))


def normalize_mob(name: str) -> str:
    n = re.sub(r"^(a|an|the)\s+", "", name.strip(), flags=re.I)
    return n[:1].upper() + n[1:] if n else name


PLAYER_ACTOR_RE = re.compile(r"^[A-Z][A-Za-z'`-]{1,31}$")


def looks_like_player_actor(name: str) -> bool:
    """Conservatively identify player/pet-style names in third-party lines."""
    return bool(PLAYER_ACTOR_RE.fullmatch((name or "").strip()))


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
    composition: str = ""
    composition_source: str = "unset"
    damage: int = 0
    targets: dict = field(default_factory=lambda: defaultdict(int))
    sources: dict = field(default_factory=lambda: defaultdict(
        lambda: {"t": 0, "h": 0, "max": 0}))
    healing_sources: dict = field(default_factory=lambda: defaultdict(
        lambda: {"t": 0, "h": 0, "max": 0, "over": 0}))
    actor_damage: dict = field(default_factory=lambda: defaultdict(
        lambda: {"t": 0, "h": 0, "max": 0}))
    actor_healing: dict = field(default_factory=lambda: defaultdict(
        lambda: {"t": 0, "h": 0, "max": 0}))
    # `targets` is the player's attributed damage. `observed_targets` adds
    # actors visible in the local log without ever changing personal DPS.
    observed_targets: dict = field(default_factory=lambda: defaultdict(int))
    timeline: dict = field(default_factory=lambda: defaultdict(
        lambda: {"out": 0, "in": 0, "heal": 0, "kills": 0}))
    kills: int = 0
    kill_targets: dict = field(default_factory=lambda: defaultdict(int))
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
        target_map = dict(self.observed_targets or self.targets)
        for killed_name in self.kill_targets:
            target_map.setdefault(killed_name, 0)
        if not target_map:
            return "fight"
        primary = max(target_map.items(), key=lambda kv: kv[1])[0]
        if self.kills > 1:
            return f"{self.kills} enemies"
        if len(target_map) > 1:
            return f"{primary} +{len(target_map) - 1} more"
        return primary

    def add_timeline(self, ts: datetime, metric: str, amount: int = 0):
        """Record a bounded two-second encounter bucket for the Lab view."""
        elapsed = max(0.0, (ts - self.start).total_seconds())
        bucket = min(899, int(elapsed // TIMELINE_BUCKET_SECONDS))
        row = self.timeline[bucket]
        if metric in row:
            row[metric] += amount


class SessionStats:
    def __init__(self, character: str = "?", session_gap: timedelta | None = None,
                 composition: str = ""):
        self.character = character
        self.session_gap = session_gap
        self.lifetime = new_lifetime_stats()
        self.composition = ""
        self.composition_source = "unset"
        if composition:
            self.set_composition(composition)
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
        self.healing_by_source: dict[str, dict] = defaultdict(
            lambda: {"t": 0, "h": 0, "max": 0, "over": 0})
        self.actor_damage: dict[str, dict] = defaultdict(
            lambda: {"t": 0, "h": 0, "max": 0})
        self.actor_healing: dict[str, dict] = defaultdict(
            lambda: {"t": 0, "h": 0, "max": 0})
        self.melee_hits = self.melee_misses = 0
        self.crits = 0
        self.enemy_misses = 0
        # defense
        self.damage_taken = 0
        self.heals_received = 0
        self.combat_feed = deque(maxlen=80)
        self.last_death_recap: list[tuple[datetime, str, int, str]] = []
        self.last_death_at: datetime | None = None
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

    def set_composition(self, composition, *, source: str = "manual",
                        retag_active: bool = True) -> str:
        """Set the exact active EQL class trio and optionally retag combat."""
        canonical = normalize_composition(composition)
        self.composition = canonical
        self.composition_source = source if canonical else "unset"
        if retag_active and getattr(self, "fight", None) is not None:
            self.fight.composition = canonical
            self.fight.composition_source = self.composition_source
        return canonical

    def _new_fight(self, ts: datetime) -> Fight:
        return Fight(start=ts, end=ts, composition=self.composition,
                     composition_source=self.composition_source)

    # -- combat windows --------------------------------------------------
    def _own_combat(self, ts: datetime):
        self.last_own_action = ts
        self._combat_signal(ts, own=True)

    def _combat_signal(self, ts: datetime, own: bool = False):
        if self.fight is None:
            if not own:
                return  # bystanders never open a fight
            self.fight = self._new_fight(ts)
        else:
            if not own and self.last_own_action and ts - self.last_own_action > BYSTANDER_GRACE:
                return  # too long since our own action: don't stretch the fight
            if ts - self.fight.end > COMBAT_GAP:
                self._close_fight()
                if own:
                    self.fight = self._new_fight(ts)
                return
            self.fight.end = ts
        self.last_combat_signal = ts

    def _close_fight(self):
        if self.fight and (self.fight.damage > 0 or self.fight.healing_done > 0
                           or self.fight.actor_damage):
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

    @staticmethod
    def _add_metric(bucket: dict, key: str, amount: int, *, overheal: int = 0):
        row = bucket[key]
        row["t"] += amount
        row["h"] += 1
        row["max"] = max(row["max"], amount)
        if "over" in row:
            row["over"] += overheal

    def _record_actor_damage(self, actor: str, dmg: int):
        if self.fight is None:
            return
        self._add_metric(self.fight.actor_damage, actor, dmg)
        self._add_metric(self.actor_damage, actor, dmg)

    def _record_actor_healing(self, actor: str, amount: int):
        if self.fight is not None:
            self._add_metric(self.fight.actor_healing, actor, amount)
        self._add_metric(self.actor_healing, actor, amount)

    def _observe_actor_healing(self, ts: datetime, actor: str, amount: int):
        """Record a named healer only when they are part of our live encounter."""
        actor = actor.strip()
        self._combat_signal(ts)
        if self.fight is not None and looks_like_player_actor(actor):
            self._record_actor_healing(actor, amount)

    def _feed(self, ts: datetime, kind: str, amount: int, label: str):
        """Keep a tiny bounded stream for the most recent death recap."""
        self.combat_feed.append((ts, kind, amount, label))

    def _deal(self, ts: datetime, target: str, dmg: int, source: str,
              crit: bool = False, actor: str | None = None):
        self._own_combat(ts)
        if self.fight is None:
            self.fight = self._new_fight(ts)
        self._record_actor_damage(actor or self.character or "You", dmg)
        self.fight.damage += dmg
        normalized_target = normalize_mob(target)
        self.fight.targets[normalized_target] += dmg
        self.fight.observed_targets[normalized_target] += dmg
        self.fight.add_timeline(ts, "out", dmg)
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

    def _observe_actor_damage(self, ts: datetime, actor: str, target: str, dmg: int):
        """Add a visible player/pet contributor without polluting self DPS."""
        actor = actor.strip()
        self._combat_signal(ts)
        if self.fight is None or not looks_like_player_actor(actor):
            return
        known_targets = {name.casefold() for name in self.fight.targets}
        if normalize_mob(actor).casefold() in known_targets:
            return
        self._record_actor_damage(actor, dmg)
        self.fight.observed_targets[normalize_mob(target)] += dmg
        self.fight.add_timeline(ts, "out", dmg)

    # -- event application ----------------------------------------------
    def apply(self, ts: datetime, kind: str, g: dict, *, count_lifetime: bool = True):
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
            attacker = normalize_mob(g["attacker"])
            self.damage_taken += dmg
            atk = self.damage_taken_by[attacker]
            atk["t"] += dmg
            atk["h"] += 1
            self._feed(ts, "damage", dmg, attacker)
            self._own_combat(ts)
            if self.fight:
                self.fight.damage_taken += dmg
                self.fight.add_timeline(ts, "in", dmg)
        elif kind in ("nuke_in", "dot_in", "nonmelee_in"):
            dmg = int(g["dmg"])
            self.damage_taken += dmg
            who = g.get("attacker")
            if who:
                who = normalize_mob(who)
                atk = self.damage_taken_by[who]
                atk["t"] += dmg
                atk["h"] += 1
            source = g.get("spell") or g.get("how") or who or "Non-melee damage"
            self._feed(ts, "damage", dmg, source)
            self._own_combat(ts)
            if self.fight:
                self.fight.damage_taken += dmg
                self.fight.add_timeline(ts, "in", dmg)
        elif kind == "miss_in":
            self.enemy_misses += 1
            self._feed(ts, "avoid", 0, normalize_mob(g["attacker"]))
            self._own_combat(ts)

        elif kind == "kill_you":
            mob = normalize_mob(g["target"])
            self.kills[mob] += 1
            if count_lifetime:
                self._lifetime_inc("kills")
                self._lifetime_named("kill_breakdown", mob)
            self._own_combat(ts)
            if self.fight:
                self.fight.kills += 1
                self.fight.kill_targets[mob] += 1
                self.fight.add_timeline(ts, "kills", 1)
        elif kind == "death_you":
            self.deaths += 1
            if count_lifetime:
                self._lifetime_inc("deaths")
            self._feed(ts, "death", 0, normalize_mob(g["killer"]))
            cutoff = ts - timedelta(seconds=20)
            self.last_death_recap = [event for event in self.combat_feed
                                     if event[0] >= cutoff]
            self.last_death_at = ts
            self._close_fight()
        elif kind == "kill_other":
            killer = g["killer"].strip()
            mob = normalize_mob(g["target"])
            self._combat_signal(ts)
            if self.fight:
                self.fight.kills += 1
                self.fight.kill_targets[mob] += 1
                self.fight.add_timeline(ts, "kills", 1)
            if killer == self.character or self.is_pet(killer):
                self.kills[mob] += 1
                if count_lifetime:
                    self._lifetime_inc("kills")
                    self._lifetime_named("kill_breakdown", mob)
            else:
                self.group_kills[mob] += 1
                if count_lifetime:
                    self._lifetime_inc("group_kills")
                    self._lifetime_named("group_kill_breakdown", mob)

        elif kind == "heal_out":
            amt = int(g["amount"])
            attempted = int(g.get("attempted") or amt)
            overheal = max(0, attempted - amt)
            spell = (g.get("spell") or "Direct healing").strip()
            self._own_combat(ts)
            self.healing_done += amt
            if self.fight:
                self.fight.healing_done += amt
                self.fight.add_timeline(ts, "heal", amt)
                self._add_metric(self.fight.healing_sources, spell, amt,
                                 overheal=overheal)
            self._add_metric(self.healing_by_source, spell, amt, overheal=overheal)
            self._record_actor_healing(self.character or "You", amt)
            self.overheal += overheal
        elif kind in ("heal_in", "heal_in_named"):
            amt = int(g["amount"])
            self.heals_received += amt
            healer = (g.get("healer") or "Unknown healer").strip()
            spell = (g.get("spell") or healer).strip()
            self._feed(ts, "heal", amt, spell)
            if kind == "heal_in_named":
                self._observe_actor_healing(ts, healer, amt)
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
        elif kind == "composition":
            # Parsing is intentionally strict.  Unknown or partial class text
            # cannot overwrite the player's explicit selector.
            try:
                self.set_composition(g.get("classes", ""), source="exact log")
            except ValueError:
                pass

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
                self._deal(ts, g["target"], dmg, f"Pet ({attacker})",
                           actor=f"{attacker} (pet)")
                self.melee_hits += 0  # pet swings tracked via source hits
            else:
                self._observe_actor_damage(ts, attacker, g["target"], dmg)
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
            elif (self.fight.damage > 0 or self.fight.healing_done > 0
                  or self.fight.actor_damage):
                pending = [self.fight]  # idle long enough: treat as closed
        history = self.fights[-30:] + pending
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
            "composition": self.composition,
            "composition_source": self.composition_source,
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
            "fight_observed_targets": (
                dict(shown_fight.observed_targets) if shown_fight else {}),
            "fight_timeline": ({int(k): dict(v) for k, v in shown_fight.timeline.items()}
                               if shown_fight else {}),
            "fight_healing_sources": (
                {k: dict(v) for k, v in shown_fight.healing_sources.items()}
                if shown_fight else {}),
            "fight_actor_damage": (
                {k: dict(v) for k, v in shown_fight.actor_damage.items()}
                if shown_fight else {}),
            "fight_actor_healing": (
                {k: dict(v) for k, v in shown_fight.actor_healing.items()}
                if shown_fight else {}),
            "fights": (history + ([live] if live else []))[-30:],
            "damage_by_source": {k: dict(v) for k, v in self.damage_by_source.items()},
            "healing_by_source": {k: dict(v) for k, v in self.healing_by_source.items()},
            "actor_damage": {k: dict(v) for k, v in self.actor_damage.items()},
            "actor_healing": {k: dict(v) for k, v in self.actor_healing.items()},
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
            "last_death_recap": list(self.last_death_recap),
            "last_death_at": self.last_death_at,
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

    @staticmethod
    def _recent_offset(path: Path, now: datetime | None = None) -> int:
        """Find a bounded, line-aligned offset for recent-session warm start."""
        try:
            size = path.stat().st_size
            start = max(0, size - INITIAL_BACKFILL_BYTES)
            with path.open("rb") as fh:
                fh.seek(start)
                data = fh.read()
        except OSError:
            return 0
        base = start
        if start and b"\n" in data:
            cut = data.index(b"\n") + 1
            base += cut
            data = data[cut:]
        cutoff = (now or datetime.now()) - timedelta(minutes=INITIAL_BACKFILL_MINUTES)
        cursor = 0
        for raw in data.splitlines(keepends=True):
            line = raw.rstrip(b"\r\n").decode("latin-1", errors="replace")
            match = LINE_RE.match(line)
            if match:
                try:
                    stamp = datetime.strptime(re.sub(r"\s+", " ", match.group("ts")), TS_FORMAT)
                except ValueError:
                    stamp = None
                if stamp is not None and stamp >= cutoff:
                    return base + cursor
            cursor += len(raw)
        return size

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
            # Warm-start the current play session instead of silently losing a
            # fight that began moments before Loremaster launched.
            self.offset = self._recent_offset(target)
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
        "mini_mode": True,
        "opacity": 1.0,
        "ui_rendering_version": 2,
        "position": None,
        "mini_position": None,
        "panel_size": [400, 480],
        "locked": False,
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
        # Exact EQL three-class identity.  Profiles are keyed by character so
        # swapping eqlog files restores the correct loadout without guessing.
        "composition": "",
        "composition_profiles": {},
        # Wiki lookup is explicit and injection-free. Hover OCR captures one
        # bounded screen region on demand and never reads eqgame memory.
        "wiki_enabled": True,
        "wiki_network_enabled": True,
        "wiki_hotkey": "Ctrl+Shift+E",
        "wiki_hotkey_customized": False,
        "wiki_hover_ocr_enabled": True,
        "wiki_cache_ttl_hours": 168,
        "wiki_request_timeout_seconds": 6,
        "wiki_position": None,
        "wiki_last_query": "",
        # Accessibility preferences are conservative and backward compatible.
        "font_scale": 1.0,
        "high_contrast": False,
        "reduced_motion": False,
    }
    loaded = {}
    try:
        decoded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        # A truncated file is already handled below; valid JSON with the
        # wrong top-level shape (null, list, scalar) must be just as harmless.
        if isinstance(decoded, dict):
            loaded = decoded
            cfg.update(loaded)
    except (OSError, ValueError):
        pass
    # Migrate the old untouched Alt+E default once, while preserving every
    # other custom binding (including an explicitly re-selected Alt+E).
    legacy = re.sub(r"\s+", "", str(cfg.get("wiki_hotkey", ""))).casefold()
    if legacy == "alt+e" and not loaded.get("wiki_hotkey_customized", False):
        cfg["wiki_hotkey"] = "Ctrl+Shift+E"
    # The former 0.94 default forced Windows layered-window composition and
    # softened every glyph. Migrate only that legacy default; deliberate
    # advanced opacity values remain supported.
    try:
        rendering_version = int(loaded.get("ui_rendering_version", 0) or 0)
    except (TypeError, ValueError):
        rendering_version = 0
    if rendering_version < 2:
        try:
            if abs(float(cfg.get("opacity", 1.0)) - 0.94) < 0.0001:
                cfg["opacity"] = 1.0
        except (TypeError, ValueError):
            cfg["opacity"] = 1.0
    cfg["ui_rendering_version"] = 2
    return cfg


def write_json_atomic(path: Path, data: dict) -> None:
    """Replace a small JSON state file without exposing a partial write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(path.suffix + ".tmp")
    try:
        staged.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def save_config(cfg: dict) -> None:
    try:
        write_json_atomic(CONFIG_PATH, cfg)
    except OSError:
        pass


def configured_composition(cfg: dict, character: str = "") -> str:
    """Read a valid per-character loadout, then the backward-safe default."""
    profiles = cfg.get("composition_profiles", {})
    candidate = profiles.get(character, "") if isinstance(profiles, dict) else ""
    candidate = candidate or cfg.get("composition", "")
    try:
        return normalize_composition(candidate)
    except ValueError:
        return ""


def remember_composition(cfg: dict, character: str, composition) -> str:
    """Persist a canonical loadout as both current and character profile."""
    canonical = normalize_composition(composition)
    cfg["composition"] = canonical
    profiles = cfg.setdefault("composition_profiles", {})
    if not isinstance(profiles, dict):
        profiles = {}
        cfg["composition_profiles"] = profiles
    if character and character != "?":
        profiles[character] = canonical
    return canonical


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
            "composition": stats.composition,
            "last_session_key": session_key,
            "lifetime": normalize_lifetime(stats.lifetime),
            "saved_at": datetime.now().isoformat(timespec="seconds"),
        }
        write_json_atomic(DATA_DIR / f"{char}.json", state)
    except OSError:
        pass


def restore_character_state(stats: SessionStats) -> datetime | None:
    st = load_character_state(stats.character)
    if not st:
        return None
    stats.level = st.get("level", stats.level)
    stats.xp_since_level = float(st.get("xp_since_level", 0.0))
    if stats.xp_since_level:
        stats.xp_pct_known = True
    for p in st.get("pet_names", []):
        stats.pet_names.add(p)
    stats.zone = st.get("zone", stats.zone)
    try:
        if st.get("composition"):
            stats.set_composition(st["composition"], source="saved", retag_active=False)
    except ValueError:
        pass
    stats.lifetime = normalize_lifetime(st.get("lifetime"))
    try:
        return datetime.fromisoformat(st["saved_at"])
    except (KeyError, TypeError, ValueError):
        return None


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
# Alert banners — frameless EQ-overlay strips, center-top, auto-fading
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
        win.withdraw()
        win.overrideredirect(True)
        win.attributes("-topmost", foreground_is_everquest_or_loremaster(
            self.root.winfo_id()))
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
        win.deiconify()
        self.active.append(win)
        self._beep(severity)
        ttl = int(self.cfg.get("alert_seconds", 4) * 1000)
        win.after(ttl, lambda: self._dismiss(win))

    def sync_topmost(self, floating: bool) -> None:
        """Keep every live banner in the same EQ-only z-order policy."""
        for win in list(self.active):
            try:
                if win.winfo_exists():
                    win.attributes("-topmost", floating)
                else:
                    self.active.remove(win)
            except Exception:
                if win in self.active:
                    self.active.remove(win)

    def clear(self) -> None:
        for win in list(self.active):
            try:
                win.destroy()
            except Exception:
                pass
        self.active.clear()

    def _dismiss(self, win, step=0):
        """Fade without blocking Tk's parser/UI update loop."""
        if step == 0 and win in self.active:
            self.active.remove(win)
        if self.cfg.get("reduced_motion", False):
            try:
                win.destroy()
            except Exception:
                pass
            return
        try:
            if step < 8:
                win.attributes("-topmost", foreground_is_everquest_or_loremaster(
                    self.root.winfo_id()))
                win.attributes("-alpha", 1.0 - step / 8)
                win.after(20, lambda: self._dismiss(win, step + 1))
                return
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
    stats = SessionStats(session_gap=session_gap,
                         composition=configured_composition(cfg))
    demo = DemoFeed() if args.demo else None
    if demo:
        stats.character = "Spin"
        watcher.character = "Spin"
        stats.set_composition(configured_composition(cfg, "Spin") or
                              "WAR / BRD / DRU", source="demo")

    T = dict(THEME)
    if cfg.get("high_contrast", False):
        T.update(bg="#000000", panel="#0a0a0a", raised="#171717",
                 line="#74818a", line_soft="#3e474d", text="#ffffff",
                 dim="#c6cdd1", gold_bright="#ffe184", cyan="#5cffe2")
    root = tk.Tk()
    root.title("Spin\'s Loremaster")
    root.configure(bg=T["bg"])
    root.overrideredirect(not args.windowed)
    root.attributes("-topmost", False)
    try:
        opacity = max(0.75, min(1.0, float(cfg.get("opacity", 1.0))))
        if opacity < 0.999:
            root.attributes("-alpha", opacity)
    except (tk.TclError, TypeError, ValueError):
        pass

    state = {"mini": bool(cfg.get("mini_mode")), "last_save": time.time(),
             "last_render": 0.0, "next_demo": 0.0, "closing": False,
             "ingest_error": "", "ingest_error_until": 0.0,
             "fights_seen": 0, "expanded": {"combat"}, "scope": "fight",
             "lab_view": "overview", "compare_filter": "same",
             "lifetime_cutoff": None, "selected_fight": None,
             "locked": bool(cfg.get("locked", False)), "click_through": False}
    alerts = AlertManager(tk, root, cfg)

    def config_number(key, default, low, high):
        try:
            return max(low, min(high, float(cfg.get(key, default))))
        except (TypeError, ValueError):
            return float(default)

    wiki_cache = WikiCache(
        WIKI_CACHE_DIR,
        ttl_seconds=config_number("wiki_cache_ttl_hours", 168, 0, 24 * 365) * 3600,
    )
    wiki_client = WikiClient(
        wiki_cache,
        timeout=config_number("wiki_request_timeout_seconds", 6, 1, 20),
        network_enabled=bool(cfg.get("wiki_network_enabled", True)),
    )
    wiki_service = WikiLookupService(wiki_client)
    hover_ocr_service = HoverOcrService()
    ingest_worker = None if demo else LogIngestWorker(watcher)
    ingest_pending = deque()

    # Click-through is deliberately never persisted.  It can only be enabled
    # when the process owns Ctrl+Alt+L, which always restores interaction.
    hotkey = {"registered": False, "id": 0x534C,
              "wiki_registered": False, "wiki_id": 0x5345,
              "wiki_error": ""}

    def _window_handle():
        if os.name != "nt":
            return None
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            user32.GetParent.argtypes = [wintypes.HWND]
            user32.GetParent.restype = wintypes.HWND
            hwnd = int(root.winfo_id())
            parent = int(user32.GetParent(wintypes.HWND(hwnd)) or 0)
            return parent or hwnd
        except (AttributeError, OSError, tk.TclError, ValueError):
            return None

    def _apply_click_through():
        hwnd = _window_handle()
        if hwnd is None:
            return False
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.GetWindowLongW.restype = ctypes.c_long
            user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
            user32.SetWindowLongW.restype = ctypes.c_long
            user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND,
                                            ctypes.c_int, ctypes.c_int,
                                            ctypes.c_int, ctypes.c_int, wintypes.UINT]
            user32.SetWindowPos.restype = wintypes.BOOL
            handle = wintypes.HWND(hwnd)
            style = user32.GetWindowLongW(handle, -20)  # GWL_EXSTYLE
            if state["click_through"]:
                style |= 0x20  # WS_EX_TRANSPARENT
            else:
                style &= ~0x20
            user32.SetWindowLongW(handle, -20, style)
            user32.SetWindowPos(handle, None, 0, 0, 0, 0,
                                0x0001 | 0x0002 | 0x0004 | 0x0020)
            return True
        except (AttributeError, OSError):
            return False

    def toggle_lock():
        state["locked"] = not state["locked"]
        cfg["locked"] = state["locked"]
        save_config(cfg)
        refresh(force_detail=True)

    def toggle_click_through():
        if not state["click_through"] and not hotkey["registered"]:
            alerts.show("warn", "CLICK-THROUGH UNAVAILABLE — Ctrl+Alt+L could not be reserved")
            return
        state["click_through"] = not state["click_through"]
        if not _apply_click_through():
            state["click_through"] = False
            alerts.show("warn", "CLICK-THROUGH COULD NOT BE APPLIED")
            return
        if state["click_through"]:
            alerts.show("info", "CLICK-THROUGH ON — PRESS CTRL+ALT+L TO RESTORE MOUSE")
        else:
            try:
                root.lift()
            except tk.TclError:
                pass
            alerts.show("info", "MOUSE CONTROL RESTORED")
        refresh(force_detail=True)

    def install_recovery_hotkey():
        if os.name != "nt":
            return
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int,
                                               wintypes.UINT, wintypes.UINT]
            user32.RegisterHotKey.restype = wintypes.BOOL
            # MOD_ALT | MOD_CONTROL | MOD_NOREPEAT, virtual key L
            hotkey["registered"] = bool(user32.RegisterHotKey(
                None, hotkey["id"], 0x0001 | 0x0002 | 0x4000, ord("L")))
        except (AttributeError, OSError):
            hotkey["registered"] = False

    def install_wiki_hotkey():
        hotkey["wiki_registered"] = False
        hotkey["wiki_error"] = ""
        if os.name != "nt" or not cfg.get("wiki_enabled", True):
            return
        try:
            import ctypes
            from ctypes import wintypes
            mods, virtual_key, canonical = parse_hotkey(
                cfg.get("wiki_hotkey", "Ctrl+Shift+E"))
            cfg["wiki_hotkey"] = canonical
            user32 = ctypes.windll.user32
            user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int,
                                               wintypes.UINT, wintypes.UINT]
            user32.RegisterHotKey.restype = wintypes.BOOL
            hotkey["wiki_registered"] = bool(user32.RegisterHotKey(
                None, hotkey["wiki_id"], mods, virtual_key))
            if not hotkey["wiki_registered"]:
                hotkey["wiki_error"] = f"{canonical} is already in use"
        except (AttributeError, OSError, ValueError) as exc:
            hotkey["wiki_error"] = str(exc)

    def remove_wiki_hotkey():
        if hotkey["wiki_registered"] and os.name == "nt":
            try:
                import ctypes
                ctypes.windll.user32.UnregisterHotKey(None, hotkey["wiki_id"])
            except (AttributeError, OSError):
                pass
        hotkey["wiki_registered"] = False

    def reinstall_wiki_hotkey():
        remove_wiki_hotkey()
        install_wiki_hotkey()

    def poll_recovery_hotkey():
        if state["closing"]:
            return
        try:
            if ((hotkey["registered"] or hotkey["wiki_registered"])
                    and os.name == "nt"):
                import ctypes
                from ctypes import wintypes
                msg = wintypes.MSG()
                while ctypes.windll.user32.PeekMessageW(
                        ctypes.byref(msg), None, 0x0312, 0x0312, 0x0001):
                    if int(msg.wParam) == hotkey["id"] and state["click_through"]:
                        toggle_click_through()
                    elif (int(msg.wParam) == hotkey["wiki_id"]
                          and foreground_is_everquest_or_loremaster(root.winfo_id())):
                        open_wiki_from_hotkey()
        except (AttributeError, OSError):
            pass
        finally:
            if not state["closing"]:
                root.after(100, poll_recovery_hotkey)

    def remove_recovery_hotkey():
        if hotkey["registered"] and os.name == "nt":
            try:
                import ctypes
                ctypes.windll.user32.UnregisterHotKey(None, hotkey["id"])
            except (AttributeError, OSError):
                pass
        hotkey["registered"] = False
        remove_wiki_hotkey()

    # ---- window drag + position persistence ----
    drag = {"x": 0, "y": 0, "active": False,
            "pending": None, "after_id": None}

    def flush_drag():
        drag["after_id"] = None
        pending = drag.get("pending")
        drag["pending"] = None
        if pending is not None:
            root.geometry(f"{pending[0]:+d}{pending[1]:+d}")

    def start_drag(e):
        if state["locked"] or state["click_through"]:
            drag["active"] = False
            return
        try:
            cursor = str(e.widget.cget("cursor"))
            widget_class = e.widget.winfo_class()
            has_click_handler = bool(e.widget.bind("<Button-1>"))
        except (AttributeError, tk.TclError):
            # Mode switches destroy the clicked control before the toplevel's
            # bindtag runs; that click must never become a window drag.
            drag["active"] = False
            return
        interactive = (has_click_handler
                       or cursor in {"hand2", "size_nw_se"}
                       or widget_class in {
                           "Scrollbar", "Button", "TButton", "Entry",
                           "TEntry", "TCombobox",
                       })
        drag["active"] = not interactive
        if not drag["active"]:
            return
        if drag.get("after_id") is not None:
            try:
                root.after_cancel(drag["after_id"])
            except tk.TclError:
                pass
        drag["after_id"] = None
        drag["pending"] = None
        drag["x"], drag["y"] = e.x, e.y

    def do_drag(e):
        if not drag["active"]:
            return
        x = root.winfo_x() + e.x - drag["x"]
        y = root.winfo_y() + e.y - drag["y"]
        drag["pending"] = (x, y)
        if drag.get("after_id") is None:
            drag["after_id"] = root.after(16, flush_drag)

    def end_drag(_e):
        if not drag["active"]:
            return
        if drag.get("after_id") is not None:
            try:
                root.after_cancel(drag["after_id"])
            except tk.TclError:
                pass
            drag["after_id"] = None
        flush_drag()
        drag["active"] = False
        root.update_idletasks()
        width, height = root.winfo_width(), root.winfo_height()
        x, y = clamped_position(
            [root.winfo_x(), root.winfo_y()], width, height,
            root.winfo_x(), root.winfo_y())
        root.geometry(f"{width}x{height}{x:+d}{y:+d}")
        key = "mini_position" if state["mini"] else "position"
        cfg[key] = [x, y]
        save_config(cfg)

    resize = {"x": 0, "y": 0, "w": 0, "h": 0, "active": False,
              "pending": None, "after_id": None}

    def flush_resize():
        resize["after_id"] = None
        pending = resize.get("pending")
        resize["pending"] = None
        if pending is not None:
            root.geometry(f"{pending[0]}x{pending[1]}")

    def virtual_desktop_bounds():
        """Return the complete Windows desktop, including left-side monitors."""
        if os.name == "nt":
            try:
                import ctypes
                user32 = ctypes.windll.user32
                return (user32.GetSystemMetrics(76), user32.GetSystemMetrics(77),
                        user32.GetSystemMetrics(78), user32.GetSystemMetrics(79))
            except (AttributeError, OSError):
                pass
        return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()

    def clamped_position(pos, width, height, default_x, default_y):
        """Keep a remembered overlay reachable after resolution/monitor changes."""
        try:
            x, y = int(pos[0]), int(pos[1])
        except (TypeError, ValueError, IndexError, KeyError):
            x, y = int(default_x), int(default_y)
        vx, vy, vw, vh = virtual_desktop_bounds()
        x = max(vx, min(x, vx + max(0, vw - width)))
        y = max(vy, min(y, vy + max(0, vh - height)))
        return x, y

    def place_window(width, height, pos, default_x, default_y):
        x, y = clamped_position(pos, width, height, default_x, default_y)
        root.geometry(f"{width}x{height}{x:+d}{y:+d}")
        return x, y

    def start_resize(e):
        if state["locked"] or state["click_through"]:
            return "break"
        if resize.get("after_id") is not None:
            try:
                root.after_cancel(resize["after_id"])
            except tk.TclError:
                pass
        resize["after_id"] = None
        resize["pending"] = None
        resize["active"] = True
        resize.update(x=e.x_root, y=e.y_root, w=root.winfo_width(), h=root.winfo_height())
        return "break"

    def do_resize(e):
        if (state["locked"] or state["click_through"]
                or not resize.get("active")):
            return "break"
        minimum_width = int(360 * max(1.0, font_scale))
        minimum_height = int(360 * max(1.0, font_scale))
        width = max(minimum_width, min(760, resize["w"] + e.x_root - resize["x"]))
        height = max(minimum_height, min(940, resize["h"] + e.y_root - resize["y"]))
        resize["pending"] = (width, height)
        if resize.get("after_id") is None:
            resize["after_id"] = root.after(16, flush_resize)
        return "break"

    def end_resize(_e):
        if not resize.get("active"):
            return "break"
        if resize.get("after_id") is not None:
            try:
                root.after_cancel(resize["after_id"])
            except tk.TclError:
                pass
            resize["after_id"] = None
        flush_resize()
        resize["active"] = False
        root.update_idletasks()
        width, height = root.winfo_width(), root.winfo_height()
        x, y = clamped_position(
            [root.winfo_x(), root.winfo_y()], width, height,
            root.winfo_x(), root.winfo_y())
        root.geometry(f"{width}x{height}{x:+d}{y:+d}")
        cfg["position"] = [x, y]
        cfg["panel_size"] = [width, height]
        save_config(cfg)
        return "break"

    root.bind("<Button-1>", start_drag)
    root.bind("<B1-Motion>", do_drag)
    root.bind("<ButtonRelease-1>", end_drag)

    try:
        font_scale = max(0.85, min(1.40, float(cfg.get("font_scale", 1.0))))
    except (TypeError, ValueError):
        font_scale = 1.0

    def fs(size):
        return max(8, int(round(size * font_scale)))

    FONT = ("Segoe UI", fs(11))
    FONT_S = ("Segoe UI", fs(9))
    FONT_B = ("Segoe UI Semibold", fs(11))
    FONT_BIG = ("Segoe UI Semibold", fs(19))
    FONT_MED = ("Segoe UI Semibold", fs(13))
    FONT_TITLE = ("Georgia", fs(11), "bold")
    FONT_RUNE = ("Georgia", fs(8), "bold")

    outer = tk.Frame(root, bg=T["gold"], padx=1, pady=1)   # 1px ember frame
    outer.pack(fill="both", expand=True)
    body = tk.Frame(outer, bg=T["bg"])
    body.pack(fill="both", expand=True)

    widgets: dict[str, tk.Widget] = {}

    def L(parent, text="", fg=None, font=FONT, bg=None, anchor="w", **kw):
        return tk.Label(parent, text=text, fg=fg or T["text"], bg=bg or parent["bg"],
                        font=font, anchor=anchor, **kw)

    # ---- EQL Wiki item overlay ---------------------------------------
    # EQ's tooltip is not a native text control. Ctrl+Shift+E therefore takes
    # one bounded screen capture around the cursor and lets Windows OCR it;
    # the capture happens before this window appears. Nothing touches eqgame.
    wiki_ui = {"win": None, "entry": None, "content": None, "status": None,
               "open": None, "item": None, "request_id": 0, "source": "",
               "ocr_request_id": 0, "ocr_clipboard": ""}

    def _wiki_cursor_position(width=392, height=560):
        try:
            px, py = root.winfo_pointerx(), root.winfo_pointery()
        except tk.TclError:
            px, py = root.winfo_x(), root.winfo_y()
        vx, vy, vw, vh = virtual_desktop_bounds()
        # Prefer the left side of the cursor so the native item display to the
        # right stays readable. Fall right only when the left edge is crowded.
        x = px - width - 28
        if x < vx + 8:
            x = px + 36
        x = max(vx + 8, min(x, vx + max(8, vw - width - 8)))
        y = py - 110
        y = max(vy + 8, min(y, vy + max(8, vh - height - 8)))
        return x, y

    def _wiki_text(*parts, tag=None):
        text_widget = wiki_ui.get("content")
        if not text_widget:
            return
        text_widget.insert("end", "".join(str(part) for part in parts), tag)

    def _wiki_clear():
        text_widget = wiki_ui.get("content")
        if text_widget:
            text_widget.configure(state="normal")
            text_widget.delete("1.0", "end")

    def _wiki_finish():
        text_widget = wiki_ui.get("content")
        if text_widget:
            text_widget.configure(state="disabled")

    def _wiki_render_prompt(message=None):
        _wiki_clear()
        _wiki_text("ITEM LORE AT A GLANCE\n", tag="hero")
        _wiki_text((message or
                    "Hover an EQ item and press Ctrl+Shift+E. A copied item "
                    "link/name or typed search remains available.\n\n"),
                   tag="body")
        _wiki_text("SAFE HOVER SCAN\n", tag="heading")
        _wiki_text("Only the cursor region is captured, only when you press "
                   "the hotkey, using Windows OCR. Loremaster never injects "
                   "into or reads eqgame memory.\n", tag="muted")
        _wiki_finish()
        wiki_ui["status"].configure(text="EQL WIKI  •  READY", fg=T["cyan"])
        wiki_ui["open"].configure(state="disabled", fg=T["line"])

    def _wiki_render_loading(query, source):
        _wiki_clear()
        _wiki_text("CONSULTING THE ARCHIVES\n", tag="hero")
        _wiki_text(query + "\n\n", tag="title")
        _wiki_text("Looking up the exact item page in a background worker. "
                   "Combat and log reading continue uninterrupted.\n", tag="muted")
        _wiki_finish()
        source_label = source.upper() if source else "SEARCH"
        wiki_ui["status"].configure(
            text=f"EQL WIKI  •  LOADING  •  {source_label}", fg=T["gold_bright"])
        wiki_ui["open"].configure(state="disabled", fg=T["line"])

    def _wiki_render_item(item: WikiItem):
        wiki_ui["item"] = item
        _wiki_clear()
        _wiki_text(item.title + "\n", tag="hero")
        if item.notes:
            for line in item.notes[:5]:
                _wiki_text(line + "\n", tag="body")
            _wiki_text("\n")
        if item.stats:
            _wiki_text("ITEM PROFILE\n", tag="heading")
            for line in item.stats[:40]:
                upper = line.upper()
                tag = "magic" if any(flag in upper for flag in (
                    "MAGIC ITEM", "LORE ITEM", "NO DROP", "ATTUNABLE")) else "stat"
                _wiki_text(line + "\n", tag=tag)
        for section in DISPLAY_SECTIONS:
            _wiki_text("\n" + section.upper() + "\n", tag="heading")
            rows = item.sections.get(section) or []
            if not rows:
                _wiki_text(EMPTY_SECTION_TEXT[section] + "\n", tag="muted")
                continue
            for line in rows[:22]:
                stripped = line.lstrip()
                if stripped.startswith("•"):
                    indent = "    " if line.startswith("  ") else "  "
                    _wiki_text(indent + stripped + "\n", tag="bullet")
                else:
                    _wiki_text(line + "\n", tag="zone")
            if len(rows) > 22:
                _wiki_text(f"  ...and {len(rows) - 22} more entries\n", tag="muted")
        _wiki_finish()
        state_label = "STALE CACHE" if item.stale else "CACHED"
        wiki_ui["status"].configure(
            text=f"EQL WIKI  •  {state_label} {format_cache_age(item).upper()}",
            fg=T["ember"] if item.stale else T["dim"])
        wiki_ui["open"].configure(state="normal", fg=T["cyan"])

    def _wiki_render_error(error, query):
        wiki_ui["item"] = None
        _wiki_clear()
        if isinstance(error, WikiNotFoundError):
            _wiki_text("NO EXACT MATCH\n", tag="error")
            _wiki_text(f'No exact item page was found for "{query}".\n\n', tag="body")
            if error.suggestions:
                _wiki_text("POSSIBLE PAGES\n", tag="heading")
                for suggestion in error.suggestions:
                    _wiki_text("  • " + suggestion + "\n", tag="bullet")
                _wiki_text("\nType a suggested title above and press Enter.\n", tag="muted")
            status = "EQL WIKI  •  NO EXACT MATCH"
        elif isinstance(error, WikiOfflineError):
            _wiki_text("ARCHIVES OFFLINE\n", tag="error")
            _wiki_text(str(error) + "\n\n", tag="body")
            _wiki_text("Cached items remain available. Check the network setting "
                       "or try again later.\n", tag="muted")
            status = "EQL WIKI  •  OFFLINE"
        else:
            _wiki_text("LOOKUP COULD NOT COMPLETE\n", tag="error")
            _wiki_text(str(error)[:300] + "\n", tag="body")
            status = "EQL WIKI  •  ERROR"
        _wiki_finish()
        wiki_ui["status"].configure(text=status, fg=T["hp"])
        wiki_ui["open"].configure(state="disabled", fg=T["line"])

    def wiki_lookup(query=None, source="search"):
        if not cfg.get("wiki_enabled", True):
            open_settings()
            return
        if query is None and wiki_ui.get("entry"):
            query = wiki_ui["entry"].get()
        query = normalize_item_name(query or "")
        if not query:
            _wiki_render_prompt(
                "Type an item name above, or hover it and press Ctrl+Shift+E.\n\n")
            try:
                wiki_ui["entry"].focus_force()
            except tk.TclError:
                pass
            return
        wiki_ui["entry"].delete(0, "end")
        wiki_ui["entry"].insert(0, query)
        cfg["wiki_last_query"] = query
        wiki_ui["source"] = source
        wiki_ui["request_id"] = wiki_service.submit(query)
        _wiki_render_loading(query, source)

    def wiki_lookup_candidates(candidates, source="hover scan"):
        candidates = [normalize_item_name(value) for value in candidates]
        candidates = [value for value in candidates if value]
        if not candidates:
            return False
        wiki_ui["entry"].delete(0, "end")
        wiki_ui["entry"].insert(0, candidates[0])
        wiki_ui["source"] = source
        wiki_ui["request_id"] = wiki_service.submit_candidates(candidates)
        _wiki_render_loading(candidates[0], source)
        return True

    def _wiki_open_page():
        item = wiki_ui.get("item")
        if not item:
            return
        try:
            import webbrowser
            webbrowser.open(item.url, new=2)
        except Exception:
            alerts.show("warn", "COULD NOT OPEN THE EQL WIKI PAGE")

    def _wiki_close():
        win = wiki_ui.get("win")
        if win:
            try:
                win.withdraw()
            except tk.TclError:
                pass

    def _wiki_make_window():
        win = tk.Toplevel(root)
        win.withdraw()
        win.overrideredirect(True)
        win.configure(bg=T["gold"])
        win.attributes("-topmost", foreground_is_everquest_or_loremaster(root.winfo_id()))
        shell = tk.Frame(win, bg=T["bg"], padx=1, pady=1)
        shell.pack(fill="both", expand=True, padx=1, pady=1)

        head = tk.Frame(shell, bg=T["panel"])
        head.pack(fill="x")
        tk.Frame(head, bg=T["cyan"], width=3).pack(side="left", fill="y")
        L(head, "LORE LENS", fg=T["gold_bright"], font=FONT_TITLE,
          bg=T["panel"]).pack(side="left", padx=9, pady=7)
        close = tk.Label(head, text="X", fg=T["dim"], bg=T["panel"],
                         font=FONT_B, cursor="hand2", padx=8)
        close.pack(side="right", fill="y")
        close.bind("<Button-1>", lambda _e: _wiki_close())
        settings = tk.Label(head, text="SETTINGS", fg=T["dim"], bg=T["panel"],
                            font=FONT_RUNE, cursor="hand2", padx=6)
        settings.pack(side="right", fill="y")
        settings.bind("<Button-1>", lambda _e: open_settings())

        search = tk.Frame(shell, bg=T["raised"])
        search.pack(fill="x", padx=8, pady=(8, 5))
        entry = tk.Entry(search, bg=T["void"], fg=T["text"], insertbackground=T["cyan"],
                         relief="flat", font=FONT, highlightthickness=1,
                         highlightbackground=T["line"], highlightcolor=T["cyan"])
        entry.pack(side="left", fill="x", expand=True, padx=(1, 5), ipady=4)
        entry.bind("<Return>", lambda _e: wiki_lookup(source="typed search"))
        go = tk.Label(search, text="SEARCH", fg=T["gold_bright"], bg=T["panel"],
                      font=FONT_RUNE, cursor="hand2", padx=10, pady=6)
        go.pack(side="right")
        go.bind("<Button-1>", lambda _e: wiki_lookup(source="typed search"))

        text_wrap = tk.Frame(shell, bg=T["bg"])
        text_wrap.pack(fill="both", expand=True, padx=9)
        content = tk.Text(text_wrap, bg=T["bg"], fg=T["text"], relief="flat",
                          bd=0, highlightthickness=0, wrap="word", font=FONT_S,
                          cursor="arrow", padx=4, pady=5, spacing1=1, spacing3=1)
        scroll = tk.Scrollbar(text_wrap, orient="vertical", command=content.yview,
                              bg=T["raised"], troughcolor=T["bg"], width=8)
        content.configure(yscrollcommand=scroll.set)
        content.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        content.tag_configure("hero", foreground=T["gold_bright"], font=FONT_MED,
                              spacing3=3)
        content.tag_configure("title", foreground=T["text"], font=FONT_B)
        content.tag_configure("body", foreground=T["text"], font=FONT_S)
        content.tag_configure("heading", foreground=T["gold"], font=FONT_RUNE,
                              spacing1=5, spacing3=2)
        content.tag_configure("stat", foreground=T["text"], font=FONT_S)
        content.tag_configure("magic", foreground=T["cyan"], font=FONT_B)
        content.tag_configure("zone", foreground=T["gold_bright"], font=FONT_B)
        content.tag_configure("bullet", foreground=T["text"], font=FONT_S)
        content.tag_configure("muted", foreground=T["dim"], font=FONT_S)
        content.tag_configure("error", foreground=T["hp"], font=FONT_MED)

        footer = tk.Frame(shell, bg=T["panel"])
        footer.pack(fill="x", padx=0, pady=(5, 0))
        status = L(footer, "EQL WIKI  •  READY", fg=T["dim"], font=FONT_RUNE,
                   bg=T["panel"])
        status.pack(side="left", fill="x", expand=True, padx=8, pady=6)
        open_button = tk.Button(
            footer, text="OPEN FULL WIKI PAGE  ↗", command=_wiki_open_page,
            bg=T["raised"], fg=T["line"], activebackground=T["panel"],
            activeforeground=T["gold_bright"], relief="flat", bd=0,
            font=FONT_RUNE, cursor="hand2", state="disabled", padx=7, pady=4)
        open_button.pack(side="right", padx=5, pady=3)
        L(shell, "ON-DEMAND SCREEN OCR  •  NO EQ INJECTION",
          fg=T["line"], font=FONT_RUNE, anchor="center").pack(fill="x", pady=(2, 5))
        win.bind("<Escape>", lambda _e: _wiki_close())
        wiki_ui.update(win=win, entry=entry, content=content, status=status,
                       open=open_button)
        _wiki_render_prompt()
        return win

    def _wiki_show_window():
        win = wiki_ui.get("win") or _wiki_make_window()
        width, height = 392, 560
        x, y = _wiki_cursor_position(width, height)
        win.geometry(f"{width}x{height}{x:+d}{y:+d}")
        win.deiconify()
        win.lift()
        return win

    def _wiki_plaintext_fallback(clipboard, message=""):
        _wiki_show_window()
        query, _source, _auto = clipboard_lookup_plan(clipboard)
        last = query or normalize_item_name(cfg.get("wiki_last_query", ""))
        wiki_ui["entry"].delete(0, "end")
        if last:
            wiki_ui["entry"].insert(0, last)
        _wiki_render_prompt(message or (
            "No hovered item title was recognized. Review the prefilled name "
            "or type one, then press Enter.\n\n"))
        try:
            wiki_ui["entry"].focus_force()
            wiki_ui["entry"].selection_range(0, "end")
        except tk.TclError:
            pass

    def _wiki_eq_is_foreground():
        if os.name != "nt":
            return False
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.GetForegroundWindow.restype = wintypes.HWND
            foreground = user32.GetForegroundWindow()
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(foreground, ctypes.byref(pid))
            return int(pid.value) in (process_ids("eqgame.exe") or set())
        except Exception:
            return False

    def open_wiki_from_hotkey(_event=None):
        if not cfg.get("wiki_enabled", True):
            return
        try:
            clipboard = root.clipboard_get()
        except (tk.TclError, TypeError):
            clipboard = ""
        query, source, auto_lookup = clipboard_lookup_plan(clipboard)
        if query and auto_lookup:
            _wiki_show_window()
            wiki_lookup(query, source)
        elif cfg.get("wiki_hover_ocr_enabled", True) and _wiki_eq_is_foreground():
            # submit() captures the physical cursor synchronously, before
            # PowerShell starts and before Lore Lens can cover the tooltip.
            wiki_ui["ocr_clipboard"] = clipboard
            try:
                wiki_ui["ocr_request_id"] = hover_ocr_service.submit()
            except RuntimeError as exc:
                _wiki_plaintext_fallback(clipboard, str(exc) + "\n\n")
        else:
            _wiki_plaintext_fallback(clipboard)

    def open_settings(_event=None):
        existing = widgets.get("settings_window")
        if existing:
            try:
                existing.deiconify()
                existing.lift()
                return
            except tk.TclError:
                pass
        win = tk.Toplevel(root)
        widgets["settings_window"] = win
        win.withdraw()
        win.title("Loremaster Settings")
        win.configure(bg=T["gold"])
        win.resizable(False, False)
        win.overrideredirect(True)
        win.attributes("-topmost", foreground_is_everquest_or_loremaster(root.winfo_id()))

        shell = tk.Frame(win, bg=T["bg"])
        shell.pack(fill="both", expand=True, padx=1, pady=1)
        tk.Frame(shell, bg=T["cyan"], height=3).pack(fill="x")
        header = tk.Frame(shell, bg=T["panel"], cursor="fleur")
        header.pack(fill="x")
        settings_title = L(
            header, "LOREMASTER SETTINGS", fg=T["gold_bright"],
            font=FONT_TITLE, bg=T["panel"], cursor="fleur")
        settings_title.pack(side="left", padx=(12, 7), pady=8)
        settings_subtitle = L(
            header, "CONFIGURATION & ACCESSIBILITY", fg=T["dim"],
            font=FONT_RUNE, bg=T["panel"], cursor="fleur")
        settings_subtitle.pack(side="left", pady=(10, 7))

        def close_settings(_event=None):
            widgets["settings_window"] = None
            try:
                win.destroy()
            except tk.TclError:
                pass

        close_label = tk.Label(
            header, text="X", fg=T["dim"], bg=T["panel"], font=FONT_B,
            cursor="hand2", padx=10, pady=6)
        close_label.pack(side="right")
        close_label.bind("<Button-1>", close_settings)

        settings_drag = {"x": 0, "y": 0}

        def start_settings_drag(event):
            settings_drag["x"] = event.x_root - win.winfo_x()
            settings_drag["y"] = event.y_root - win.winfo_y()

        def move_settings(event):
            width, height = win.winfo_width(), win.winfo_height()
            desired_x = event.x_root - settings_drag["x"]
            desired_y = event.y_root - settings_drag["y"]
            x, y = clamped_position(
                [desired_x, desired_y], width, height, desired_x, desired_y)
            win.geometry(f"{x:+d}{y:+d}")

        for drag_target in (header, settings_title, settings_subtitle):
            drag_target.bind("<Button-1>", start_settings_drag)
            drag_target.bind("<B1-Motion>", move_settings)

        frame = tk.Frame(shell, bg=T["bg"], padx=16, pady=14)
        frame.pack(fill="both", expand=True)
        L(frame, "LOADOUT IDENTITY", fg=T["gold"], font=FONT_B).pack(
            fill="x", pady=(0, 1))
        L(frame, "Tag every encounter with this character's exact three-class build. "
          "Loremaster will not guess from spells or combat messages.",
          fg=T["dim"], font=FONT_S, justify="left", wraplength=410).pack(
              fill="x", pady=(0, 5))
        composition_row = tk.Frame(frame, bg=T["raised"], padx=8, pady=6)
        composition_row.pack(fill="x", pady=(0, 9))
        L(composition_row, "ACTIVE", fg=T["cyan"], font=FONT_RUNE,
          bg=T["raised"]).pack(side="left")
        composition_entry = tk.Entry(
            composition_row, width=24, bg=T["void"], fg=T["gold_bright"],
            insertbackground=T["cyan"], relief="flat", font=FONT_B,
            justify="center")
        composition_entry.pack(side="right", ipady=4)
        composition_entry.insert(0, stats.composition)
        composition_status = L(
            frame, "Example: WAR / BRD / DRU  •  saved per character",
            fg=T["dim"], font=FONT_RUNE)
        composition_status.pack(fill="x", pady=(0, 10))
        L(frame, "Lore Lens item lookup", fg=T["cyan"], font=FONT_B).pack(fill="x")
        L(frame, "Hover an item in EQ and use the global key. Loremaster captures "
          "one bounded cursor region for Windows OCR, never eqgame memory.",
          fg=T["dim"], font=FONT_S, justify="left", wraplength=410).pack(
              fill="x", pady=(2, 10))

        enabled_var = tk.BooleanVar(value=bool(cfg.get("wiki_enabled", True)))
        network_var = tk.BooleanVar(value=bool(cfg.get("wiki_network_enabled", True)))
        hover_ocr_var = tk.BooleanVar(value=bool(
            cfg.get("wiki_hover_ocr_enabled", True)))
        contrast_var = tk.BooleanVar(value=bool(cfg.get("high_contrast", False)))
        motion_var = tk.BooleanVar(value=bool(cfg.get("reduced_motion", False)))

        def check(text_value, variable):
            c = tk.Checkbutton(frame, text=text_value, variable=variable,
                               bg=T["bg"], fg=T["text"], selectcolor=T["raised"],
                               activebackground=T["bg"], activeforeground=T["gold_bright"],
                               font=FONT_S, anchor="w")
            c.pack(fill="x", pady=1)
            return c

        check("Enable Lore Lens item lookup", enabled_var)
        check("Scan hovered tooltip on hotkey (Windows OCR, on demand)", hover_ocr_var)
        check("Allow network lookups (cached pages still work when off)", network_var)
        row = tk.Frame(frame, bg=T["bg"])
        row.pack(fill="x", pady=(8, 4))
        L(row, "EQ-only global hotkey", fg=T["gold"], font=FONT_S).pack(side="left")
        hotkey_entry = tk.Entry(row, width=16, bg=T["void"], fg=T["text"],
                                insertbackground=T["cyan"], relief="flat", font=FONT)
        hotkey_entry.pack(side="right", ipady=3)
        hotkey_entry.insert(0, cfg.get("wiki_hotkey", "Ctrl+Shift+E"))
        status = L(frame, "", fg=T["dim"], font=FONT_S, wraplength=410)
        status.pack(fill="x", pady=(0, 8))
        if hotkey.get("wiki_error"):
            status.configure(text="Current hotkey: " + hotkey["wiki_error"], fg=T["ember"])
        check("High-contrast palette (applies next launch)", contrast_var)
        check("Reduced motion", motion_var)
        scale_row = tk.Frame(frame, bg=T["bg"])
        scale_row.pack(fill="x", pady=(7, 2))
        L(scale_row, "Text scale (0.85-1.40; next launch)", fg=T["gold"],
          font=FONT_S).pack(side="left")
        scale_entry = tk.Entry(scale_row, width=8, bg=T["void"], fg=T["text"],
                               insertbackground=T["cyan"], relief="flat", font=FONT)
        scale_entry.pack(side="right", ipady=3)
        scale_entry.insert(0, str(cfg.get("font_scale", 1.0)))

        actions = tk.Frame(frame, bg=T["bg"])
        actions.pack(fill="x", pady=(12, 0))

        def save_settings():
            try:
                _mods, _key, canonical = parse_hotkey(hotkey_entry.get())
                scale_value = max(0.85, min(1.40, float(scale_entry.get())))
                canonical_composition = normalize_composition(composition_entry.get())
            except (ValueError, TypeError) as exc:
                status.configure(text=str(exc), fg=T["hp"])
                return
            remember_composition(cfg, stats.character, canonical_composition)
            stats.set_composition(canonical_composition, source="manual")
            cfg.update(wiki_enabled=bool(enabled_var.get()),
                       wiki_network_enabled=bool(network_var.get()),
                       wiki_hover_ocr_enabled=bool(hover_ocr_var.get()),
                       wiki_hotkey=canonical, wiki_hotkey_customized=True,
                       high_contrast=bool(contrast_var.get()),
                       reduced_motion=bool(motion_var.get()), font_scale=scale_value)
            wiki_client.network_enabled = cfg["wiki_network_enabled"]
            save_config(cfg)
            composition_entry.delete(0, "end")
            composition_entry.insert(0, canonical_composition)
            composition_status.configure(
                text=(f"{canonical_composition} is tagging new encounters"
                      if canonical_composition else
                      "Loadout cleared; encounters will show UNSET until selected."),
                fg=T["green"] if canonical_composition else T["ember"])
            refresh(force_detail=True)
            reinstall_wiki_hotkey()
            if cfg["wiki_enabled"] and not hotkey["wiki_registered"]:
                status.configure(text=hotkey["wiki_error"] or "Hotkey could not be reserved.",
                                 fg=T["hp"])
                return
            message = (f"Saved. {canonical} is ready while EQ/Loremaster is active."
                       if cfg["wiki_enabled"] else "Saved. Lore Lens is disabled.")
            status.configure(text=message, fg=T["green"])

        tk.Button(actions, text="SAVE", command=save_settings, bg=T["raised"],
                  fg=T["gold_bright"], activebackground=T["panel"], relief="flat",
                  font=FONT_B, padx=16, pady=5).pack(side="right")
        tk.Button(actions, text="CLOSE", command=close_settings, bg=T["panel"],
                  fg=T["dim"], activebackground=T["raised"], relief="flat",
                  font=FONT_S, padx=12, pady=5).pack(side="right", padx=6)
        win.protocol("WM_DELETE_WINDOW", close_settings)
        win.bind("<Escape>", close_settings)
        win.update_idletasks()
        x = root.winfo_x() - win.winfo_width() - 16
        y = root.winfo_y()
        x, y = clamped_position([x, y], win.winfo_width(), win.winfo_height(), x, y)
        win.geometry(f"{x:+d}{y:+d}")
        win.deiconify()
        win.lift()

    def poll_wiki_results():
        if state["closing"]:
            return
        try:
            for result in wiki_service.poll():
                if result.request_id != wiki_ui.get("request_id"):
                    continue
                if result.item is not None:
                    cfg["wiki_last_query"] = result.item.title
                    wiki_ui["entry"].delete(0, "end")
                    wiki_ui["entry"].insert(0, result.item.title)
                    _wiki_render_item(result.item)
                else:
                    _wiki_render_error(result.error or WikiError("Unknown lookup error"),
                                       result.query)
        finally:
            if not state["closing"]:
                root.after(80, poll_wiki_results)

    def poll_hover_ocr_results():
        if state["closing"]:
            return
        try:
            for result in hover_ocr_service.poll():
                if result.request_id != wiki_ui.get("ocr_request_id"):
                    continue  # a newer hotkey press owns the user's intent
                clipboard = wiki_ui.get("ocr_clipboard", "")
                if result.candidates:
                    _wiki_show_window()
                    wiki_lookup_candidates(result.candidates, "hover scan")
                else:
                    detail = result.error.strip() if result.error else (
                        "Windows OCR did not find a likely item title.")
                    _wiki_plaintext_fallback(clipboard, detail + "\n\n")
        finally:
            if not state["closing"]:
                root.after(50, poll_hover_ocr_results)

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
    scroll_bindings: dict[str, str] = {}

    def clear_scroll_bindings():
        for event_name, binding_id in list(scroll_bindings.items()):
            try:
                root.unbind(event_name, binding_id)
            except tk.TclError:
                pass
        scroll_bindings.clear()

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

    def displayed_fight(snap):
        selected = state.get("selected_fight")
        if selected is not None:
            if any(f is selected for f in snap["fights"]):
                return selected
            state["selected_fight"] = None
        return snap["fight"]

    def fight_is_live(snap, fight):
        return bool(snap["in_combat"] and fight is stats.fight)

    def browse_fight(direction):
        snap = stats.snapshot(datetime.now())
        fights = snap["fights"]
        if not fights:
            return
        current = displayed_fight(snap)
        index = next((i for i, f in enumerate(fights) if f is current), len(fights) - 1)
        if direction < 0:
            state["selected_fight"] = fights[max(0, index - 1)]
        elif direction > 0:
            next_index = min(len(fights) - 1, index + 1)
            state["selected_fight"] = None if next_index == len(fights) - 1 else fights[next_index]
        else:
            state["selected_fight"] = None
        for cw in card_widgets.values():
            cw["detail_signature"] = None
        refresh(force_detail=True)

    def card_value(snap, key):
        if not state["mini"] and state["scope"] == "fight" and key == "combat":
            fight = displayed_fight(snap)
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
            if snap["xp_events"]:
                return f"{snap['xp_events']} xp gain" + ("s" if snap["xp_events"] != 1 else "")
            if stats.skillups:
                count = len(stats.skillups)
                return f"{count} skillup" + ("s" if count != 1 else "")
            if stats.aa_points:
                return f"+{stats.aa_points} AA"
            return "awaiting gains"
        if key == "faction":
            return f"{len(stats.faction)} factions"
        if key == "travels":
            return f"{snap['deaths']} death" + ("s" if snap["deaths"] != 1 else "")
        return ""

    def card_detail(snap, key):
        """Return visual ledger rows; meter kinds embed a 0..1 bar share."""
        out = []
        if state["scope"] == "fight" and key == "combat":
            fight = displayed_fight(snap)
            if not fight:
                return [("line", "Your next encounter will be recorded here in real time.", "")]
            view = state.get("lab_view", "overview")
            status = "LIVE ENCOUNTER" if fight_is_live(snap, fight) else "ENCOUNTER"
            target_types = set(fight.observed_targets) | set(fight.kill_targets)
            out.append(("head", f"{status} · {fight.name}", fmt_dur(fight.seconds)))
            loadout = fight.composition or "UNSET"
            source = (f" · {fight.composition_source}"
                      if fight.composition_source not in {"", "unset"} else "")
            out.append(("line", f"Loadout {loadout}{source}", ""))
            out.append(("line", f"{fmt_num(fight.damage)} personal damage · "
                                f"{fmt_num(fight.dps)} dps · {fight.crits} crits · "
                                f"{fight.misses} misses", ""))
            if fight.kills or len(target_types) > 1:
                kill_text = (f"{fight.kills} enem{'y' if fight.kills == 1 else 'ies'} slain"
                             if fight.kills else "pull in progress")
                out.append(("line", f"{kill_text} · {len(target_types)} target "
                                    f"type{'s' if len(target_types) != 1 else ''}", ""))
            if fight.damage_taken or fight.healing_done or fight.heals_received:
                out.append(("line", f"Taken {fmt_num(fight.damage_taken)} · healed {fmt_num(fight.healing_done)} "
                                    f"· received {fmt_num(fight.heals_received)}", ""))

            recent = [f for f in snap["fights"] if f is not fight]
            if view == "overview" and recent:
                previous = recent[-1]
                delta = fight.dps - previous.dps
                direction = "+" if delta >= 0 else ""
                out.append(("head", "Compared with previous", previous.name))
                out.append(("line", f"{direction}{fmt_num(delta)} dps · "
                                    f"previous {fmt_num(previous.dps)} dps", ""))

            if view == "compare":
                mode = state.get("compare_filter", "same")
                candidates = composition_comparisons(snap["fights"], fight, mode)
                mode_label = {"same": "same loadout", "other": "other loadouts",
                              "all": "all earlier fights"}[mode]
                out.append(("head", f"Baselines · {mode_label}",
                            "selected minus baseline"))
                if not fight.composition and mode in {"same", "other"}:
                    out.append(("line", "Set the selected encounter's loadout to compare "
                                        "compositions accurately.", ""))
                elif not candidates:
                    out.append(("line", "No earlier encounter matches this filter yet.", ""))
                for old in reversed(candidates[-8:]):
                    dps_delta = fight.dps - old.dps
                    damage_delta = fight.damage - old.damage
                    out.append(("row", f"{old.name} · {old.composition or 'UNSET'}",
                                f"{dps_delta:+,.0f} dps · {damage_delta:+,.0f} dmg"))
                summaries = summarize_compositions(snap["fights"])
                if summaries:
                    out.append(("head", "Rolling loadout summary", "fights · avg · best"))
                    for summary in summaries:
                        out.append(("row", summary["composition"],
                                    f"{summary['fights']} · "
                                    f"{fmt_num(summary['average_dps'])}/s · "
                                    f"{fmt_num(summary['best_dps'])}/s"))
                out.append(("line", "Only the rolling encounter list is compared; multi-mob "
                                    "pulls remain one fight until combat ends.", ""))
                return out
            actors = sorted(fight.actor_damage.items(), key=lambda kv: -kv[1]["t"])
            actor_total = sum(value["t"] for _name, value in actors)
            if actors and view in ("overview", "damage"):
                out.append(("head", "Observed encounter actors", "damage · share · dps"))
                for name, value in actors[:12]:
                    share = value["t"] / max(1, actor_total)
                    out.append((f"meter:{share:.4f}", name,
                                f"{fmt_num(value['t'])} · {share * 100:.0f}% · "
                                f"{fmt_num(value['t'] / fight.seconds)}/s"))
                out.append(("line", "Actors visible in your EQ log; not a guaranteed group roster.", ""))
            sources = sorted(fight.sources.items(), key=lambda kv: -kv[1]["t"])
            if sources and view == "damage":
                out.append(("head", "Damage by ability", "total · share · dps"))
                for name, value in sources[:12]:
                    share = 100.0 * value["t"] / max(1, fight.damage)
                    out.append((f"meter:{share / 100.0:.4f}", name,
                                f"{fmt_num(value['t'])} · {share:.0f}% · {fmt_num(value['t'] / fight.seconds)}/s"))
                    out.append(("line", f"{value['h']} hits · avg {value['t'] / max(1, value['h']):.1f} "
                                        f"· max {fmt_num(value.get('max', 0))}", ""))
            heals = sorted(fight.healing_sources.items(), key=lambda kv: -kv[1]["t"])
            if heals and view == "healing":
                out.append(("head", "Healing by spell", "effective · overheal"))
                healing_total = sum(value["t"] for _name, value in heals)
                for name, value in heals[:10]:
                    out.append((f"meter:{value['t'] / max(1, healing_total):.4f}", name,
                                f"{fmt_num(value['t'])} · {fmt_num(value.get('over', 0))} over"))
            healers = sorted(fight.actor_healing.items(), key=lambda kv: -kv[1]["t"])
            healer_total = sum(value["t"] for _name, value in healers)
            if healers and view == "healing":
                out.append(("head", "Observed healing actors", "effective · share"))
                for name, value in healers[:10]:
                    share = value["t"] / max(1, healer_total)
                    out.append((f"meter:{share:.4f}", name,
                                f"{fmt_num(value['t'])} · {share * 100:.0f}%"))
            if view == "healing" and not heals and not healers:
                out.append(("line", "No outgoing healing was visible in this encounter.", ""))

            target_totals = dict(fight.observed_targets)
            for killed_name in fight.kill_targets:
                target_totals.setdefault(killed_name, 0)
            targets = sorted(target_totals.items(), key=lambda kv: (-kv[1], kv[0]))
            if targets and view == "targets":
                observed_total = sum(total for _name, total in targets)
                out.append(("head", "Multi-mob target breakdown", "visible dmg · kills"))
                for name, total in targets[:20]:
                    kills = int(fight.kill_targets.get(name, 0))
                    suffix = f" · {kills} slain" if kills else ""
                    out.append((f"meter:{total / max(1, observed_total):.4f}", name,
                                f"{fmt_num(total)}{suffix}"))
                out.append(("line", "Repeated enemies collapse by creature type; the slain "
                                    "count preserves the full pull size.", ""))

            if view == "timeline":
                buckets = sorted(fight.timeline.items())
                if buckets:
                    peak = max(max(row["out"], row["in"], row["heal"])
                               for _bucket, row in buckets)
                    out.append(("head", f"{TIMELINE_BUCKET_SECONDS}-second timeline",
                                "visible out / personal in / own heal"))
                    for bucket, values in buckets[-40:]:
                        elapsed = bucket * TIMELINE_BUCKET_SECONDS
                        right = (f"{fmt_num(values['out'])} / {fmt_num(values['in'])} / "
                                 f"{fmt_num(values['heal'])}")
                        if values["kills"]:
                            right += f" · {values['kills']} slain"
                        peak_value = max(values["out"], values["in"], values["heal"])
                        out.append((f"meter:{peak_value / max(1, peak):.4f}",
                                    f"+{elapsed:02d}s", right))
                else:
                    out.append(("line", "No timeline events were recorded.", ""))

            if view == "overview" and recent:
                out.append(("head", "Recent encounters", "damage · dps · time"))
                for old in reversed(recent[-8:]):
                    out.append(("row", f"{old.name} · {old.composition or 'UNSET'}",
                                f"{fmt_num(old.damage)} · {fmt_num(old.dps)}/s · "
                                f"{fmt_dur(old.seconds)}"))
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
            actors = sorted(snap["actor_damage"].items(), key=lambda kv: -kv[1]["t"])
            actor_total = sum(value["t"] for _name, value in actors)
            if actors:
                out.append(("head", "Observed session actors", "damage · share"))
                for name, value in actors[:10]:
                    share = value["t"] / max(1, actor_total)
                    out.append((f"meter:{share:.4f}", name,
                                f"{fmt_num(value['t'])} · {share * 100:.0f}%"))
                out.append(("line", "Built only from actors visible in your local EQ log.", ""))
            heals = sorted(snap["healing_by_source"].items(), key=lambda kv: -kv[1]["t"])
            if heals:
                out.append(("head", "Session healing by spell", "effective · overheal"))
                total_healing = sum(value["t"] for _name, value in heals)
                for name, value in heals[:8]:
                    out.append((f"meter:{value['t'] / max(1, total_healing):.4f}", name,
                                f"{fmt_num(value['t'])} · {fmt_num(value.get('over', 0))} over"))
            healers = sorted(snap["actor_healing"].items(), key=lambda kv: -kv[1]["t"])
            healer_total = sum(value["t"] for _name, value in healers)
            if healers:
                out.append(("head", "Observed session healing actors", "effective · share"))
                for name, value in healers[:8]:
                    share = value["t"] / max(1, healer_total)
                    out.append((f"meter:{share:.4f}", name,
                                f"{fmt_num(value['t'])} · {share * 100:.0f}%"))
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
            if stats.skillups:
                out.append(("head", "Skill improvements", str(len(stats.skillups))))
                for name, value in sorted(stats.skillups.items())[:12]:
                    out.append(("row", name, str(value)))
        elif key == "faction":
            rows = sorted(stats.faction.items(), key=lambda kv: kv[1])
            for name, d in rows[:10]:
                out.append(("row", name, f"{d:+d}"))
            if not rows:
                out.append(("line", "No faction hits yet", ""))
        elif key == "travels":
            out.append(("row", "Deaths", str(snap["deaths"])))
            recap = snap.get("last_death_recap") or []
            death_at = snap.get("last_death_at")
            if recap and death_at:
                out.append(("head", "Last death · final 20 seconds", ""))
                for ts, event_kind, amount, label in recap[-12:]:
                    seconds_before = max(0.0, (death_at - ts).total_seconds())
                    when = "death" if event_kind == "death" else f"-{seconds_before:.0f}s"
                    if event_kind == "damage":
                        out.append(("row", f"{when} · {label}", f"-{fmt_num(amount)}"))
                    elif event_kind == "heal":
                        out.append(("row", f"{when} · {label}", f"+{fmt_num(amount)}"))
                    elif event_kind == "avoid":
                        out.append(("row", f"{when} · avoided {label}", ""))
                    else:
                        out.append(("row", f"Slain by {label}", ""))
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

    def set_lab_view(view):
        if view == state.get("lab_view"):
            return
        state["lab_view"] = view
        combat = card_widgets.get("combat")
        if combat:
            combat["detail_signature"] = None
        refresh(force_detail=True)

    def set_compare_filter(mode):
        if mode not in {"same", "other", "all"} or mode == state.get("compare_filter"):
            return
        state["compare_filter"] = mode
        combat = card_widgets.get("combat")
        if combat:
            combat["detail_signature"] = None
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
        clear_scroll_bindings()
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
        for txt, cmd in (("\u2715", do_quit), ("HUD", toggle_mini),
                         ("LORE", open_wiki_from_hotkey), ("RESET", do_reset)):
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
        widgets["composition"] = tk.Label(
            sub, text="LOADOUT  •  SET", fg=T["gold_bright"], bg=T["raised"],
            font=FONT_RUNE, cursor="hand2", padx=6, pady=2, anchor="e")
        widgets["composition"].pack(side="right")
        widgets["composition"].bind("<Button-1>", open_settings)

        scopes = tk.Frame(body, bg=T["void"])
        scopes.pack(fill="x", padx=10, pady=(4, 1))
        for scope, label in (("fight", "ENCOUNTER"),
                             ("session", "SESSION"),
                             ("records", "RECORDS")):
            tab = tk.Label(scopes, text=label, fg=T["dim"], bg=T["void"],
                           font=FONT_RUNE, cursor="hand2", pady=3)
            tab.pack(side="left", expand=True, fill="x")
            tab.bind("<Button-1>", lambda _e, s=scope: set_scope(s))
            widgets[f"scope_{scope}"] = tab

        encounter = tk.Frame(body, bg=T["bg"])
        encounter.pack(fill="x", padx=10, pady=(2, 0))
        widgets["encounter_nav"] = encounter
        for name, label, direction in (("encounter_prev", "‹ PREVIOUS", -1),
                                       ("encounter_live", "CURRENT", 0),
                                       ("encounter_next", "NEXT ›", 1)):
            b = tk.Label(encounter, text=label, fg=T["cyan"], bg=T["raised"],
                         font=FONT_RUNE, cursor="hand2", padx=6, pady=2)
            b.pack(side="left" if direction < 1 else "right")
            b.bind("<Button-1>", lambda _e, d=direction: browse_fight(d))
            widgets[name] = b
        widgets["encounter_label"] = L(encounter, "AWAITING ENCOUNTER", fg=T["dim"],
                                         font=FONT_RUNE, anchor="center")
        widgets["encounter_label"].pack(side="left", fill="x", expand=True)

        lab_nav = tk.Frame(body, bg=T["bg"])
        lab_nav.pack(fill="x", padx=10, pady=(3, 0))
        widgets["lab_nav"] = lab_nav
        for view, label in (("overview", "OVERVIEW"), ("damage", "DAMAGE"),
                            ("healing", "HEALING"), ("targets", "TARGETS"),
                            ("timeline", "TIMELINE"), ("compare", "COMPARE")):
            tab = tk.Label(lab_nav, text=label, fg=T["dim"], bg=T["void"],
                           font=FONT_RUNE, cursor="hand2", pady=3)
            tab.pack(side="left", expand=True, fill="x", padx=(0, 1))
            tab.bind("<Button-1>", lambda _e, v=view: set_lab_view(v))
            widgets[f"lab_{view}"] = tab

        compare_nav = tk.Frame(body, bg=T["bg"])
        widgets["compare_nav"] = compare_nav
        L(compare_nav, "BASELINE", fg=T["gold"], font=FONT_RUNE).pack(
            side="left", padx=(1, 6))
        for compare_mode, label in (("same", "SAME LOADOUT"),
                                    ("other", "OTHER LOADOUTS"),
                                    ("all", "ALL FIGHTS")):
            tab = tk.Label(compare_nav, text=label, fg=T["dim"], bg=T["void"],
                           font=FONT_RUNE, cursor="hand2", padx=4, pady=2)
            tab.pack(side="left", expand=True, fill="x", padx=(0, 1))
            tab.bind("<Button-1>",
                     lambda _e, m=compare_mode: set_compare_filter(m))
            widgets[f"compare_{compare_mode}"] = tab

        # Ember hero band: live combat at a glance, or the permanent chronicle.
        hero = tk.Frame(body, bg=T["raised"])
        hero.pack(fill="x", padx=10, pady=(3, 2))
        widgets["hero"] = hero
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

        def scroll_wheel(event):
            delta = int(event.delta)
            if delta:
                units = max(1, abs(delta) // 120)
                canvas.yview_scroll(-units if delta > 0 else units, "units")
            return "break"

        def scroll_linux(units):
            def handler(_event):
                canvas.yview_scroll(units, "units")
                return "break"
            return handler

        # Bind to this toplevel's bindtag, not Tk's global ``all`` bindtag.
        # Lore Lens and Settings are separate toplevels and therefore cannot
        # accidentally scroll the encounter ledger beneath themselves.
        scroll_bindings["<MouseWheel>"] = root.bind(
            "<MouseWheel>", scroll_wheel, add="+")
        scroll_bindings["<Button-4>"] = root.bind(
            "<Button-4>", scroll_linux(-1), add="+")
        scroll_bindings["<Button-5>"] = root.bind(
            "<Button-5>", scroll_linux(1), add="+")

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
                                 "detail": detail, "detail_signature": None,
                                 "detail_controls": []}
        footer = tk.Frame(body, bg=T["panel"])
        footer.pack(fill="x")
        widgets["status"] = L(footer, "Loremaster awaits your log\u2026",
                              fg=T["dim"], font=FONT_S, bg=T["panel"])
        widgets["status"].pack(fill="x", padx=10, pady=(5, 2))
        footer_actions = tk.Frame(footer, bg=T["panel"])
        footer_actions.pack(fill="x", padx=(9, 4), pady=(0, 4))
        grip = tk.Label(footer_actions, text="\u2198", fg=T["cyan"], bg=T["panel"],
                        font=FONT_B, cursor="size_nw_se")
        widgets["grip"] = grip
        grip.pack(side="right", padx=(4, 1), pady=3)
        grip.bind("<Button-1>", start_resize)
        grip.bind("<B1-Motion>", do_resize)
        grip.bind("<ButtonRelease-1>", end_resize)
        widgets["locate"] = tk.Label(
            footer_actions, text="LOCATE LOG", fg=T["cyan"], bg=T["raised"],
            font=FONT_RUNE, cursor="hand2", padx=7, pady=3)
        widgets["locate"].pack(side="right", padx=(0, 4), pady=3)
        widgets["locate"].bind("<Button-1>", choose_log_dir)
        widgets["pass"] = tk.Label(
            footer_actions, text="CLICK-THRU", fg=T["dim"], bg=T["raised"],
            font=FONT_RUNE, cursor="hand2", padx=7, pady=3)
        widgets["pass"].pack(side="right", padx=(0, 4), pady=3)
        widgets["pass"].bind("<Button-1>", lambda _e: toggle_click_through())
        widgets["settings"] = tk.Label(
            footer_actions, text="SETTINGS", fg=T["dim"], bg=T["raised"],
            font=FONT_RUNE, cursor="hand2", padx=7, pady=3)
        widgets["settings"].pack(side="right", padx=(0, 4), pady=3)
        widgets["settings"].bind("<Button-1>", open_settings)
        widgets["lock"] = tk.Label(
            footer_actions, text="LOCK", fg=T["dim"], bg=T["raised"],
            font=FONT_RUNE, cursor="hand2", padx=7, pady=3)
        widgets["lock"].pack(side="right", padx=(0, 4), pady=3)
        widgets["lock"].bind("<Button-1>", lambda _e: toggle_lock())
        pos = cfg.get("position")
        panel_size = cfg.get("panel_size") or [400, 480]
        minimum_width = int(360 * max(1.0, font_scale))
        minimum_height = int(360 * max(1.0, font_scale))
        width = max(minimum_width, min(760, int(panel_size[0])))
        height = max(minimum_height, min(940, int(panel_size[1])))
        default_x = max(8, root.winfo_screenwidth() - width - 24)
        default_y = max(8, root.winfo_screenheight() - height - 300)
        place_window(width, height, pos, default_x, default_y)

    def build_mini():
        clear_scroll_bindings()
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
        details = tk.Label(strip, text="DETAILS", fg=T["cyan"], bg=T["raised"],
                           font=FONT_RUNE, cursor="hand2", padx=6)
        details.pack(side="right", padx=(3, 3), pady=3)
        details.bind("<Button-1>", lambda _e: toggle_mini())
        widgets["mini_lock"] = tk.Label(
            strip, text="LOCK", fg=T["dim"], bg=T["raised"],
            font=FONT_RUNE, cursor="hand2", padx=5)
        widgets["mini_lock"].pack(side="right", pady=3)
        widgets["mini_lock"].bind("<Button-1>", lambda _e: toggle_lock())
        widgets["mini_log"] = tk.Label(
            strip, text="\u25cf", fg=T["dim"], bg=T["bg"], font=FONT_S,
            cursor="hand2", padx=3)
        widgets["mini_log"].pack(side="right", pady=3)
        widgets["mini_log"].bind("<Button-1>", choose_log_dir)
        mini_composition = (stats.composition.replace(" / ", "/")
                            if stats.composition else "SET LOADOUT")
        widgets["mini_wiki"] = tk.Label(
            strip, text=f"{mini_composition}  ·  LORE",
            fg=T["cyan"], bg=T["raised"],
            font=FONT_RUNE, cursor="hand2", padx=6)
        widgets["mini_wiki"].pack(side="right", padx=(2, 1), pady=3)
        widgets["mini_wiki"].bind("<Button-1>", open_wiki_from_hotkey)
        pos = cfg.get("mini_position")
        # Four default ledger cards plus log health, Lore Lens, lock, and
        # details controls need 720 px at the standard font size.  Keeping an
        # explicit width also makes the companion reservation deterministic
        # for installer-provided layouts instead of clipping right-side tools.
        mini_width = min(1000, int(MINI_BASE_WIDTH * max(1.0, font_scale)))
        mini_height = min(48, int(MINI_BASE_HEIGHT * max(1.0, font_scale)))
        default_x = max(8, root.winfo_screenwidth() - mini_width - 12)
        default_y = max(8, root.winfo_screenheight() - mini_height - 284)
        place_window(mini_width, mini_height, pos, default_x, default_y)

    def toggle_mini():
        state["mini"] = not state["mini"]
        cfg["mini_mode"] = state["mini"]
        save_config(cfg)
        (build_mini if state["mini"] else build_full)()

    def do_reset():
        stats.reset()
        state["fights_seen"] = 0
        state["selected_fight"] = None

    def choose_log_dir(_event=None):
        nonlocal watcher, ingest_worker
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
        if ingest_worker is not None:
            ingest_worker.close(timeout=0.75)
        else:
            watcher.close()
        watcher = LogWatcher(cfg["log_dir"], args.log)
        ingest_worker = LogIngestWorker(watcher)
        ingest_pending.clear()
        if widgets.get("status"):
            widgets["status"].configure(text="searching for the newest eqlog…")
        state["fights_seen"] = 0

    def do_quit():
        state["closing"] = True
        if state["click_through"]:
            state["click_through"] = False
            _apply_click_through()
        remove_recovery_hotkey()
        hover_ocr_service.close()
        wiki_service.close()
        if not demo:
            save_character_state(stats.character, stats)
        save_config(cfg)
        if ingest_worker is not None:
            ingest_worker.close(timeout=0.75)
        else:
            watcher.close()
        root.destroy()

    # ---- periodic update ----
    def _queue_ingest_records():
        """Move a bounded number of worker records into one ordered UI deque."""
        if ingest_worker is None or len(ingest_pending) >= 2048:
            return
        for record in ingest_worker.drain(max_records=16):
            if isinstance(record, LineBatchRecord):
                ingest_pending.extend(("line", raw) for raw in record.lines)
            elif isinstance(record, SwitchRecord):
                ingest_pending.append(("switch", record))
            elif isinstance(record, StatusRecord):
                ingest_pending.append(("status", record))

    def _apply_character_switch(record):
        save_character_state(stats.character, stats)
        character = record.character or "?"
        stats.__init__(character, session_gap=session_gap,
                       composition=configured_composition(cfg, character))
        stats.character = character
        state["lifetime_cutoff"] = restore_character_state(stats)
        if stats.composition:
            remember_composition(cfg, stats.character, stats.composition)
        state["fights_seen"] = 0
        state["selected_fight"] = None

    def _apply_log_line(raw):
        raw_msg = raw.split("] ", 1)[1] if "] " in raw else raw
        parsed = parse_line(raw)
        kind, groups = "", {}
        if parsed:
            ts, kind, groups = parsed
            cutoff = state.get("lifetime_cutoff")
            stats.apply(ts, kind, groups,
                        count_lifetime=(cutoff is None or ts > cutoff))
            if kind == "composition" and stats.composition:
                remember_composition(cfg, stats.character, stats.composition)
                save_config(cfg)
        for severity, text_msg in check_alerts(
                kind, groups, raw_msg, stats.character, cfg):
            alerts.show(severity, text_msg)

    def tick():
        if state["closing"]:
            return
        next_delay = 50
        try:
            if demo:
                now_mono = time.monotonic()
                if now_mono >= state["next_demo"]:
                    ingest_pending.extend(("line", raw) for raw in demo.lines())
                    state["next_demo"] = now_mono + POLL_MS / 1000.0
            else:
                _queue_ingest_records()

            deadline = time.perf_counter() + 0.008
            while ingest_pending and time.perf_counter() < deadline:
                record_type, payload = ingest_pending.popleft()
                if record_type == "line":
                    _apply_log_line(payload)
                elif record_type == "switch":
                    _apply_character_switch(payload)
                else:
                    state["ingest_error"] = (
                        f"log {payload.operation}: {payload.message}"[:180])
                    state["ingest_error_until"] = time.time() + 6.0

            worker_pending = ingest_worker.pending_count if ingest_worker else 0
            if not ingest_pending and not worker_pending:
                stats.finalize_idle(datetime.now())
            else:
                next_delay = 16

            if cfg.get("fight_toasts", True):
                done = len(stats.fights)
                if done > state["fights_seen"] and stats.fights:
                    f = stats.fights[-1]
                    profile = f"  \u00b7  {f.composition}" if f.composition else ""
                    alerts.show("info", f"{f.name}  \u2014  {fmt_num(f.dps)} dps  "
                                f"({fmt_num(f.damage)} in {fmt_dur(f.seconds)}){profile}")
                state["fights_seen"] = done

            now_mono = time.monotonic()
            if now_mono - state["last_render"] >= 0.25:
                refresh()
                state["last_render"] = now_mono
            if not demo and time.time() - state["last_save"] > 30:
                save_character_state(stats.character, stats)
                state["last_save"] = time.time()
        except Exception as exc:
            # A malformed line or transient widget error must never kill the
            # recurring ingest loop. Surface it briefly and keep draining.
            state["ingest_error"] = f"runtime: {type(exc).__name__}: {exc}"[:180]
            state["ingest_error_until"] = time.time() + 6.0
        finally:
            if not state["closing"]:
                root.after(next_delay, tick)

    def log_health():
        if not watcher.path:
            return "NO LOG", T["hp"]
        try:
            age = max(0.0, time.time() - watcher.path.stat().st_mtime)
        except OSError:
            return "NO LOG", T["hp"]
        if age <= 10:
            return "LIVE", T["green"]
        if age <= 120:
            return "READY", T["cyan"]
        return "STALE", T["ember"]

    def _detail_kind(kind):
        return "meter" if kind.startswith("meter:") else kind

    def _set_widget(widget, **options):
        """Configure only changed Tk options to avoid redundant repaints."""
        changed = {}
        for key, value in options.items():
            try:
                current = widget.cget(key)
            except tk.TclError:
                current = object()
            if str(current) != str(value):
                changed[key] = value
        if changed:
            widget.configure(**changed)

    def _set_text(label, value):
        _set_widget(label, text=value)

    def _draw_detail_meter(canvas):
        width = max(1, canvas.winfo_width())
        pct = canvas._lore_pct
        left = canvas._lore_left
        right = canvas._lore_right
        draw_state = (width, pct, left, right)
        if getattr(canvas, "_lore_draw_state", None) == draw_state:
            return
        canvas._lore_draw_state = draw_state
        canvas.delete("all")
        edge = max(2, int(width * pct))
        canvas.create_rectangle(0, 2, edge, 17, fill=T["meter"], outline="")
        canvas.create_line(0, 2, edge, 2, fill=T["meter_edge"])
        canvas.create_text(3, 10, text=left, fill=T["text"],
                           font=FONT_S, anchor="w")
        canvas.create_text(width - 3, 10, text=right, fill=T["gold_bright"],
                           font=FONT_S, anchor="e")

    def _new_detail_control(cw, base_kind):
        row = tk.Frame(cw["detail"], bg=T["bg"])
        row.pack(fill="x", padx=14, pady=0)
        control = {"kind": base_kind, "row": row}
        if base_kind == "meter":
            meter = tk.Canvas(row, height=19, bg=T["bg"], highlightthickness=0)
            meter._lore_pct = 0.0
            meter._lore_left = ""
            meter._lore_right = ""
            meter.pack(fill="x")
            meter.bind("<Configure>", lambda _e, canvas=meter: _draw_detail_meter(canvas))
            control["meter"] = meter
        elif base_kind == "head":
            left_label = tk.Label(row, fg=T["gold"], bg=T["bg"],
                                  font=FONT_S, anchor="w")
            left_label.pack(side="left", pady=(4, 1))
            right_label = tk.Label(row, fg=T["gold_bright"], bg=T["bg"],
                                   font=FONT_S, anchor="e")
            right_label.pack(side="right", pady=(4, 1))
            control.update(left=left_label, right=right_label)
        elif base_kind == "line":
            left_label = tk.Label(row, fg=T["dim"], bg=T["bg"],
                                  font=FONT_S, anchor="w", justify="left")
            left_label.pack(side="left")
            control["left"] = left_label
        else:
            left_label = tk.Label(row, fg=T["text"], bg=T["bg"],
                                  font=FONT_S, anchor="w")
            left_label.pack(side="left")
            right_label = tk.Label(row, fg=T["gold_bright"], bg=T["bg"],
                                   font=FONT_S, anchor="e")
            right_label.pack(side="right")
            control.update(left=left_label, right=right_label)
        return control

    def _build_detail_controls(cw, rows):
        """Reconcile the changing row tail instead of destroying the card."""
        wanted = tuple(_detail_kind(kind) for kind, _left, _right in rows)
        controls = list(cw.get("detail_controls", []))
        prefix = 0
        while (prefix < len(wanted) and prefix < len(controls)
               and controls[prefix]["kind"] == wanted[prefix]):
            prefix += 1
        for control in controls[prefix:]:
            control["row"].destroy()
        controls = controls[:prefix]
        for base_kind in wanted[prefix:]:
            controls.append(_new_detail_control(cw, base_kind))
        cw["detail_controls"] = controls
        cw["detail_signature"] = wanted

    def _update_detail_controls(cw, rows):
        signature = tuple(_detail_kind(kind) for kind, _left, _right in rows)
        if signature != cw.get("detail_signature"):
            _build_detail_controls(cw, rows)
        for control, (kind, left, right) in zip(cw["detail_controls"], rows):
            if control["kind"] == "meter":
                meter = control["meter"]
                meter._lore_pct = max(0.0, min(1.0, float(kind.split(":", 1)[1])))
                meter._lore_left = left
                meter._lore_right = right
                _draw_detail_meter(meter)
            else:
                _set_text(control["left"], left)
                if "right" in control:
                    _set_text(control["right"], right)

    def refresh(force_detail=False):
        snap = stats.snapshot(datetime.now())
        if state["mini"]:
            items = widgets.get("mini_items")
            if not items:
                return
            for key, label in items.items():
                value = card_value(snap, key)
                _set_text(label, value)
            health, color = log_health()
            mini_log = widgets.get("mini_log")
            if mini_log:
                _set_widget(mini_log, text=f"\u25cf {health}", fg=color)
            mini_lock = widgets.get("mini_lock")
            if mini_lock:
                _set_widget(
                    mini_lock,
                    text="MOVE" if state["locked"] else "LOCK",
                    fg=T["gold_bright"] if state["locked"] else T["dim"])
            mini_wiki = widgets.get("mini_wiki")
            if mini_wiki:
                compact = (snap.get("composition") or "SET LOADOUT").replace(" / ", "/")
                _set_text(mini_wiki, f"{compact}  \u00b7  LORE")
                profile_color = (T["cyan"] if snap.get("composition")
                                 else T["ember"])
                _set_widget(mini_wiki, fg=profile_color)
            return

        title = snap["character"]
        if watcher.server != "?":
            title += f" ({watcher.server})"
        _set_text(widgets["who"], title)
        health, health_color = ("DEMO", T["green"]) if demo else log_health()
        _set_widget(widgets["dot"], fg=health_color)
        _set_text(widgets["zone"], snap["zone"] or "\u2014")
        composition = snap.get("composition") or "SET LOADOUT"
        _set_text(widgets["composition"], f"LOADOUT  \u2022  {composition}")
        profile_color = (T["gold_bright"] if snap.get("composition")
                         else T["ember"])
        _set_widget(widgets["composition"], fg=profile_color)
        session_text = "session \u2014"
        if snap["session_start"]:
            dur = fmt_dur(snap["hours"] * 3600)
            since = snap["session_start"].strftime("%I:%M %p").lstrip("0")
            session_text = f"session {dur} (since {since})"
        _set_text(widgets["session"], session_text)
        for scope in ("fight", "session", "records"):
            active = state["scope"] == scope
            _set_widget(
                widgets[f"scope_{scope}"],
                bg=T["raised"] if active else T["void"],
                fg=T["cyan"] if active else T["dim"])
        nav = widgets.get("encounter_nav")
        lab_nav = widgets.get("lab_nav")
        compare_nav = widgets.get("compare_nav")
        if nav:
            if state["scope"] == "fight":
                if not nav.winfo_manager():
                    nav.pack(fill="x", padx=10, pady=(2, 0), before=widgets["hero"])
                if lab_nav and not lab_nav.winfo_manager():
                    lab_nav.pack(fill="x", padx=10, pady=(3, 0), before=widgets["hero"])
                if compare_nav:
                    if state.get("lab_view") == "compare":
                        if not compare_nav.winfo_manager():
                            compare_nav.pack(fill="x", padx=10, pady=(2, 0),
                                             before=widgets["hero"])
                    else:
                        if compare_nav.winfo_manager():
                            compare_nav.pack_forget()
            else:
                if nav.winfo_manager():
                    nav.pack_forget()
                if lab_nav and lab_nav.winfo_manager():
                    lab_nav.pack_forget()
                if compare_nav and compare_nav.winfo_manager():
                    compare_nav.pack_forget()
        if state["scope"] == "fight":
            for view in ("overview", "damage", "healing", "targets", "timeline", "compare"):
                tab = widgets.get(f"lab_{view}")
                if tab:
                    active = state.get("lab_view") == view
                    _set_widget(tab, bg=T["raised"] if active else T["void"],
                                fg=T["cyan"] if active else T["dim"])
            for mode in ("same", "other", "all"):
                tab = widgets.get(f"compare_{mode}")
                if tab:
                    active = state.get("compare_filter") == mode
                    _set_widget(tab, bg=T["raised"] if active else T["void"],
                                fg=T["gold_bright"] if active else T["dim"])
        lock = widgets.get("lock")
        if lock:
            _set_widget(lock, text="MOVE" if state["locked"] else "LOCK",
                        fg=T["gold_bright"] if state["locked"] else T["dim"])
        grip = widgets.get("grip")
        if grip:
            _set_widget(grip, fg=T["line"] if state["locked"] else T["cyan"],
                        cursor="arrow" if state["locked"] else "size_nw_se")
        pass_button = widgets.get("pass")
        if pass_button:
            if state["click_through"]:
                _set_widget(pass_button, text="PASS ON", fg=T["gold_bright"])
            else:
                _set_widget(pass_button, text="CLICK-THRU", fg=(T["cyan"]
                            if hotkey["registered"] else T["line"]))
        if state["scope"] == "records":
            life = snap["lifetime"]
            _set_widget(widgets["current_dps"], text=fmt_num(life["kills"]),
                        fg=T["gold_bright"])
            _set_text(widgets["session_dps"], fmt_num(len(life["kill_breakdown"])))
            _set_text(widgets["best_dps"], fmt_num(life["best_dps"]))
            for key, label in (("current_dps", "NPC KILLS"),
                               ("session_dps", "CREATURE TYPES"),
                               ("best_dps", "RECORD DPS")):
                _set_text(widgets[f"{key}_label"], label)
        else:
            shown = displayed_fight(snap) if state["scope"] == "fight" else snap["fight"]
            shown_live = fight_is_live(snap, shown)
            fight_dps = snap["current_dps"] if shown_live else (shown.dps if shown else 0)
            _set_widget(
                widgets["current_dps"],
                text=fmt_num(fight_dps),
                fg=T["gold_bright"] if shown_live else T["dim"])
            _set_text(widgets["session_dps"], fmt_num(snap["session_dps"]))
            best = snap["best_fight"]
            _set_text(widgets["best_dps"], fmt_num(best.dps) if best else "0")
            for key, label in (("current_dps", "FIGHT DPS"),
                               ("session_dps", "SESSION"),
                               ("best_dps", "BEST")):
                _set_text(widgets[f"{key}_label"], label)
        if state["scope"] == "fight":
            fights = snap["fights"]
            shown = displayed_fight(snap)
            index = next((i for i, f in enumerate(fights) if f is shown), -1)
            label = "AWAITING ENCOUNTER"
            if shown:
                encounter_name = shown.name.upper()
                if len(encounter_name) > 20:
                    encounter_name = encounter_name[:19].rstrip() + "…"
                prefix = "" if fight_is_live(snap, shown) else f"{index + 1}/{len(fights)} · "
                label = f"{prefix}{encounter_name}"
            _set_text(widgets["encounter_label"], label)
            _set_widget(widgets["encounter_prev"],
                        fg=T["cyan"] if index > 0 else T["line"])
            _set_widget(widgets["encounter_next"],
                        fg=T["cyan"] if 0 <= index < len(fights) - 1 else T["line"])
            _set_widget(widgets["encounter_live"],
                        fg=T["gold_bright"] if state["selected_fight"] is None else T["cyan"])
        starred = cfg.get("starred_cards", [])
        for key, _label in CARDS:
            cw = card_widgets.get(key)
            if not cw:
                continue
            _set_text(cw["value"], card_value(snap, key))
            _set_widget(cw["star"], text="\u2726" if key in starred else "\u25c7",
                        fg=T["gold_bright"] if key in starred else T["line"])
            expanded = key in state["expanded"]
            accent = T["cyan"] if expanded else T["line_soft"]
            if state["scope"] == "records" and key in ("kills", "travels"):
                accent = T["gold"]
            _set_widget(cw["name"], fg=accent if expanded else T["dim"])
            _set_widget(cw["rule"], bg=accent)
            if cw.get("hex_accent") != accent:
                cw["hex"].itemconfigure("all", outline=accent)
                cw["hex_accent"] = accent
            _set_text(cw["chev"], "\u25be" if expanded else "\u25b8")
            if expanded:
                rows = card_detail(snap, key)
                _update_detail_controls(cw, rows)
                if not cw["detail"].winfo_manager():
                    cw["detail"].pack(fill="x", pady=(0, 4))
            else:
                if cw["detail"].winfo_manager():
                    cw["detail"].pack_forget()

        if demo:
            src_txt = "demo mode \u2014 synthetic fight"
        elif watcher.path:
            src_txt = f"{health.lower()} \u00b7 {watcher.path.name}"
        else:
            src_txt = "no log \u00b7 /log on, then LOCATE LOG"
        if state["click_through"]:
            src_txt = "CLICK-THROUGH ON  \u00b7  CTRL+ALT+L RESTORES MOUSE"
            health_color = T["gold_bright"]
        elif state["ingest_error"] and time.time() < state["ingest_error_until"]:
            src_txt = state["ingest_error"]
            health_color = T["hp"]
        _set_widget(widgets["status"], text=src_txt, fg=health_color)
        _set_text(widgets["locate"], "CHANGE" if watcher.path else "LOCATE LOG")

    z_order = {"floating": None, "eq_check_at": 0.0,
               "eq_running": True, "hidden_for_eq": False}

    def sync_z_order():
        if state["closing"]:
            return
        try:
            now = time.monotonic()
            if args.wait_for_eq and now >= z_order["eq_check_at"]:
                z_order["eq_running"] = process_is_running("eqgame.exe")
                z_order["eq_check_at"] = now + 1.0
            if args.wait_for_eq and not z_order["eq_running"]:
                if state["click_through"]:
                    state["click_through"] = False
                    _apply_click_through()
                remove_wiki_hotkey()
                if not z_order["hidden_for_eq"]:
                    _wiki_close()
                    settings_window = widgets.get("settings_window")
                    if settings_window:
                        try:
                            settings_window.withdraw()
                        except tk.TclError:
                            pass
                    alerts.clear()
                    root.withdraw()
                    z_order["hidden_for_eq"] = True
                floating = False
            else:
                if z_order["hidden_for_eq"]:
                    root.deiconify()
                    z_order["hidden_for_eq"] = False
                floating = foreground_is_everquest_or_loremaster(root.winfo_id())
                if (floating and cfg.get("wiki_enabled", True)
                        and not hotkey["wiki_registered"]):
                    install_wiki_hotkey()
                elif not floating and hotkey["wiki_registered"]:
                    remove_wiki_hotkey()

            if floating != z_order["floating"]:
                try:
                    root.attributes("-topmost", floating)
                    if floating:
                        root.lift()
                except tk.TclError:
                    pass
                for extra in (wiki_ui.get("win"), widgets.get("settings_window")):
                    if extra:
                        try:
                            extra.attributes("-topmost", floating)
                        except tk.TclError:
                            pass
                z_order["floating"] = floating
            alerts.sync_topmost(floating)
        finally:
            if not state["closing"]:
                root.after(250, sync_z_order)

    install_recovery_hotkey()
    install_wiki_hotkey()
    root.protocol("WM_DELETE_WINDOW", do_quit)
    (build_mini if state["mini"] else build_full)()
    sync_z_order()
    poll_recovery_hotkey()
    poll_hover_ocr_results()
    poll_wiki_results()
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
    assert f1.actor_damage["Spin"]["t"] == 725
    assert f1.actor_damage["Gann (pet)"]["t"] == 50
    assert f1.damage_taken == 60 and f1.crits == 1 and f1.misses == 1
    assert heal_fight.healing_sources["Light Healing"] == {
        "t": 500, "h": 1, "max": 500, "over": 150,
    }
    assert snap["healing_by_source"]["Light Healing"]["over"] == 150

    # Exact Legends combat grammar captured from a live rock-dervish session.
    # These lines must light up COMBAT and SLAYING even if Loremaster discovers
    # the log after the first swing has already been written.
    live = SessionStats("Spin")
    rock_lines = [
        line(0, "A rock dervish is pierced by YOUR thorns for 4 points of non-melee damage."),
        line(1, "A rock dervish hits YOU for 1 point of damage."),
        line(2, "A rock dervish has taken 16 damage from your Denon's Disruptive Discord."),
        line(3, "A rock dervish tries to hit YOU, but misses! (Riposte)"),
        line(4, "You slash a rock dervish for 54 points of damage."),
        line(5, "You slash a rock dervish for 20 points of damage."),
        line(6, "You slash a rock dervish for 33 points of damage."),
        line(7, "You slash a rock dervish for 29 points of damage."),
        line(8, "You receive 4 silver from the corpse."),
        line(8, "You have slain a rock dervish!"),
    ]
    for raw in rock_lines:
        parsed = parse_line(raw)
        assert parsed, f"unparsed live line: {raw}"
        live.apply(*parsed)
    rock = live.snapshot(now + timedelta(seconds=8))
    assert rock["current_dps"] > 0 and rock["combat_damage"] == 156, rock
    assert rock["kills"] == 1 and rock["enemy_misses"] == 1, rock
    assert rock["copper"] == 40, rock
    replay = SessionStats("Spin")
    parsed = parse_line(line(0, "You have slain a rock dervish!"))
    replay.apply(*parsed, count_lifetime=False)
    assert replay.kills["Rock dervish"] == 1 and replay.lifetime["kills"] == 0

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
    assert s3.fight.actor_damage["Nexus"]["t"] == 40
    assert s3.actor_damage["Spin"]["t"] == 10

    # Named healing and incoming damage form an honest 20-second death recap.
    death = SessionStats("Spin")
    for raw in (
        line(0, "You slash a spectre for 25 points of damage."),
        line(2, "A spectre hits YOU for 80 points of damage."),
        line(4, "Aria healed you for 30 hit points by Superior Healing."),
        line(7, "You have been slain by a spectre!"),
    ):
        death.apply(*parse_line(raw))
    ds = death.snapshot(now + timedelta(seconds=7))
    assert ds["last_death_at"] == now + timedelta(seconds=7)
    assert [event[1] for event in ds["last_death_recap"]] == [
        "damage", "heal", "death",
    ]
    assert death.actor_healing["Aria"]["t"] == 30

    # One uninterrupted multi-mob pull remains one encounter. Repeated mob
    # names collapse into target types, while individual slay lines retain the
    # real seven-enemy pull count.
    pull = SessionStats("Spin")
    offset = 0
    for mob, count in (("a goblin shaman", 3), ("a goblin warrior", 4)):
        for _ in range(count):
            pull.apply(*parse_line(line(offset, f"You slash {mob} for 25 points of damage.")))
            pull.apply(*parse_line(line(offset + 1, f"You have slain {mob}!")))
            offset += 2
    pull.finalize_idle(now + timedelta(seconds=offset + 20))
    pull_snap = pull.snapshot(now + timedelta(seconds=offset + 20))
    assert len(pull_snap["fights"]) == 1
    pull_fight = pull_snap["fights"][0]
    assert pull_fight.kills == 7 and len(pull_fight.targets) == 2
    assert pull_fight.kill_targets == {
        "Goblin shaman": 3, "Goblin warrior": 4,
    }
    assert pull_fight.name == "7 enemies"
    assert sum(pull_fight.observed_targets.values()) == 175
    assert sum(row["kills"] for row in pull_fight.timeline.values()) == 7
    assert s3.fight.observed_targets["Gnoll"] == 50

    kill_only = SessionStats("Spin")
    kill_only.apply(*parse_line(line(0, "You slash a goblin for 10 points of damage.")))
    kill_only.apply(*parse_line(line(1, "An orc has been slain by Aria!")))
    assert set(kill_only.fight.observed_targets) | set(kill_only.fight.kill_targets) == {
        "Goblin", "Orc",
    }
    assert kill_only.fight.name == "Goblin +1 more"

    # Lore Lens is deterministic and never requires the live wiki in tests.
    assert parse_hotkey("ctrl+shift+e") == (0x4006, ord("E"), "Ctrl+Shift+E")
    assert extract_item_query("https://eqlwiki.com/Cloak_of_Flames")[0] == (
        "Cloak of Flames")
    assert extract_item_query("[Cloak of Flames +4]")[0] == "Cloak of Flames"
    assert r"C:\EQLegends\Logs" in DEFAULT_LOG_DIRS
    assert MINI_BASE_WIDTH == 720 and MINI_BASE_HEIGHT == 34
    wiki_selftest()

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
        atomic_state = root / "state.json"
        write_json_atomic(atomic_state, {"ok": True})
        assert json.loads(atomic_state.read_text(encoding="utf-8")) == {"ok": True}
        assert not atomic_state.with_suffix(".json.tmp").exists()
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

        # A newly discovered log replays only the recent, bounded session tail
        # on its next poll instead of discarding combat already in progress.
        recent = root / "eqlog_Spin_qeynos.txt"
        recent.write_text(
            line(-3600, "You slash an old rat for 1 point of damage.") + "\n" +
            line(-10, "You slash a rock dervish for 33 points of damage.") + "\n" +
            line(-9, "You have slain a rock dervish!") + "\n",
            encoding="latin-1",
        )
        w = LogWatcher(None, str(recent))
        # _recent_offset accepts an explicit clock, keeping this test stable.
        offset = w._recent_offset(recent, now=now)
        with recent.open("rb") as fh:
            fh.seek(offset)
            warmed = fh.read().decode("latin-1")
        assert "old rat" not in warmed and "rock dervish" in warmed, warmed
        w.close()
    print("Loremaster selftest: ALL PASS")
    print(f"  patterns: {len(PATTERNS)}  |  fight1 dps {f1.dps:.0f}  |  "
          f"session dps {snap['session_dps']:.0f}  |  ETL {fmt_eta(snap2['hours_to_level'])}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Spin\'s Loremaster — log parser & session tracker for Spin\'s UI Reloaded")
    ap.add_argument("--demo", action="store_true", help="run with a synthetic combat feed")
    ap.add_argument("--windowed", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--selftest", action="store_true", help="run parser/stats tests and exit")
    ap.add_argument("--log", help="tail one specific eqlog file")
    ap.add_argument("--log-dir", help="EverQuest Legends Logs directory")
    ap.add_argument("--wait-for-eq", action="store_true",
                    help="remain hidden and idle until eqgame.exe is running")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.wait_for_eq and not args.demo:
        if not acquire_waiter_instance():
            return 0
        wait_for_everquest()
    # A startup waiter acquires only after EQ appears. That lets a deliberate
    # desktop launch open immediately; when EQ starts, the waiter sees the
    # existing overlay and exits instead of creating a duplicate.
    if not args.demo and not acquire_single_instance():
        return 0
    return run_gui(args)


if __name__ == "__main__":
    raise SystemExit(main())
