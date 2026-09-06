#!/usr/bin/env python3
"""Spin's Loremaster — the log-reading companion for Spin's UI Reloaded.

A zero-dependency (Python standard library only) EverQuest Legends session
tracker themed to match the "Vellum & Ember" skin
and shaped to dock into the reserved bottom-right zone of the 3440x1440
layout.

What it does
------------
* Tails your EverQuest Legends log file (offset-based, 500 ms polls).
* Auto-detects the active character and switches when you swap toons.
* Combat-aware DPS: fights open on your, your pet's, or a verified group
  member's first action and close after 10 s of silence; unverified bystander
  activity can never open a fight and only extends one near trusted activity.
* Encounter Lab with current/previous/session views, actor/ability/healing
  meters, multi-mob target breakdowns, and a two-second combat timeline.
* Pet damage attribution for summoned and charmed pets, plus active pet count
  for swarm/multiclass play.
* Bard song counting (songs twisted, songs/min) — WAR/DRU/BRD approved.
* XP tracking: xp events, xp %/hr when the server logs percentages, level
  ups, and estimated time to level.
* Kills (per-creature breakdown), deaths, heals in/out, damage taken,
  loot log, coin (plat/hr), faction hits, skill-ups, fizzles/resists.
* Rune Seed HUD: a tiny EQ-only combat sigil that cycles starred stats and
  unfolds into the full encounter panel on click.
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
import math
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

from charm_break import CharmBreakDetector, CharmBreakEvent
from control_snapshot import merge_control_snapshots
from hover_ocr import HoverOcrService
from log_ingest import (
    LineBatchRecord,
    LogIngestWorker,
    StatusRecord,
    SwitchRecord,
)
from mez_timer import MezTracker, format_mez_remaining
from lull_timer import LullTracker
from progression import ProgressionTracker
from sky_intel import (SOURCE_URL as SKY_SOURCE_URL, inventory_names_from_text,
                       load_bundled_catalog, write_map_marker)
from windows_hotkeys import (
    HOTKEY_RECOVERY,
    HOTKEY_WIKI,
    HotkeyBinding,
    WindowsHotkeyService,
)
from windows_tray import (
    TRAY_EXIT,
    TRAY_HIDE,
    TRAY_SHOW,
    WindowsTrayIcon,
    overlay_should_be_visible,
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
    hotkey_lookup_plan,
    normalize_item_name,
    parse_hotkey,
    selftest as wiki_selftest,
)

# ---------------------------------------------------------------------------
# Theme — matches Spin UI "Vellum & Ember" (leather, brass, ember, spirit)
# ---------------------------------------------------------------------------
THEME = {
    "bg": "#120d08",
    "panel": "#1a140d",
    "raised": "#261d12",
    "line": "#685030",
    "line_soft": "#342819",
    "gold": "#d0a254",
    "gold_bright": "#f8d68c",
    "cyan": "#7eaaf4",
    "text": "#f1e7d4",
    "dim": "#ac9a7e",
    "hp": "#de3e48",
    "mana": "#427ef4",
    "endur": "#d0a254",
    "green": "#42cf8b",
    "ember": "#f2762c",
    "parchment": "#decca2",
    "void": "#090704",
    "meter": "#2e1c10",
    "meter_edge": "#9a5a24",
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
if os.environ.get("LOREMASTER_APP_DATA_DIR"):
    APP_DATA_DIR = Path(os.environ["LOREMASTER_APP_DATA_DIR"])
elif getattr(sys, "frozen", False):
    APP_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "SpinsLoremaster"
else:
    APP_DATA_DIR = SOURCE_DIR
CONFIG_PATH = APP_DATA_DIR / "loremaster_config.json"
DATA_DIR = APP_DATA_DIR / "loremaster_data"
WIKI_CACHE_DIR = APP_DATA_DIR / "wiki_cache"
BRAND_COG_FILE = "loremaster-cog.png"


def bundled_resource_path(*parts) -> Path:
    """Resolve a source or PyInstaller one-file data asset."""
    root = Path(getattr(sys, "_MEIPASS", SOURCE_DIR))
    return root.joinpath(*parts)


def png_asset_identity(path) -> tuple[int, int, int]:
    """Return PNG width, height, and color type without runtime dependencies."""
    payload = Path(path).read_bytes()[:26]
    if (len(payload) < 26 or payload[:8] != b"\x89PNG\r\n\x1a\n"
            or payload[12:16] != b"IHDR"):
        raise ValueError("brand asset is not a valid PNG")
    width = int.from_bytes(payload[16:20], "big")
    height = int.from_bytes(payload[20:24], "big")
    return width, height, payload[25]

# Combat pacing constants
COMBAT_GAP = timedelta(seconds=10)
BYSTANDER_GRACE = timedelta(seconds=20)
CHARM_LAND_WINDOW = timedelta(seconds=12)
DIRECT_CAST_FANOUT_WINDOW = timedelta(seconds=2)
SESSION_GAP = timedelta(minutes=60)
POLL_MS = 500
LOG_RESCAN_SECONDS = 2.0
MAX_READ_BYTES = 256 * 1024
INITIAL_BACKFILL_BYTES = 2 * 1024 * 1024
INITIAL_BACKFILL_MINUTES = 30
CONTEXT_BACKFILL_BYTES = 32 * 1024 * 1024
MAX_FIGHT_HISTORY = 500
DESKTOP_FIGHT_HISTORY = 60
TIMELINE_BUCKET_SECONDS = 2
CHARM_SPELL_FAMILIES = frozenset({
    # Enchanter
    "charm", "beguile", "cajoling whispers", "allure",
    "boltran's agacerie", "dictate", "command of druzzil",
    # Druid / ranger animal charm
    "befriend animal", "charm animals", "beguile animals",
    "allure of the wild", "call of karana", "tunare's request",
    "command of tunare",
    # Necromancer undead charm
    "beguile undead", "cajole undead", "allure of death",
    "thrall of bones", "control undead",
})
# EverQuest Legends drops ten grades of potential mote, and a night of clearing
# camps buries their loot lines under everything else.  The client names the
# items in the plural ("Motes of Minor Potential") while a loot line reads
# "You have looted a <name>", so both forms have to match - as does the
# unqualified fourth grade, which carries no grade word at all.
#
# Order is the in-game tier order.  "Infinite" and "Infinitesimal" share a
# prefix, so the grade word is always matched whole against a fixed set rather
# than by prefix.  Exp per mote comes from the same table the grades do, so the
# ledger can total a session's potential without a second source of truth.
MOTE_TIERS = (
    # grade word in the item name, short label, exp per mote
    ("infinitesimal", "Infinitesimal", 1),
    ("minor", "Minor", 1),
    ("lesser", "Lesser", 2),
    ("", "Potential", 4),
    ("major", "Major", 5),
    ("greater", "Greater", 6),
    ("superior", "Superior", 7),
    ("grand", "Grand", 8),
    ("ascendant", "Ascendant", 9),
    ("infinite", "Infinite", 10),
)
MOTE_GRADES = tuple(grade for grade, _label, _exp in MOTE_TIERS)
MOTE_TIER_LABELS = tuple(label for _grade, label, _exp in MOTE_TIERS)
MOTE_TIER_EXP = tuple(exp for _grade, _label, exp in MOTE_TIERS)
MOTE_RE = re.compile(
    r"^motes?\s+of\s+(?:("
    + "|".join(re.escape(grade) for grade in MOTE_GRADES if grade)
    + r")\s+)?potential$",
    re.IGNORECASE)


def mote_tier_index(name) -> int | None:
    """Which grade an item name is, or None when it is not a potential mote."""
    match = MOTE_RE.match(str(name).strip())
    if match is None:
        return None
    return MOTE_GRADES.index((match.group(1) or "").casefold())


def mote_tier_counts(loot) -> list[int]:
    """Bucket a name -> quantity mapping into mote grades, lowest tier first."""
    counts = [0] * len(MOTE_TIERS)
    if not isinstance(loot, dict):
        return counts
    for name, quantity in loot.items():
        tier = mote_tier_index(name)
        if tier is None:
            continue
        try:
            amount = int(quantity)
        except (TypeError, ValueError):
            continue
        counts[tier] += max(0, amount)
    return counts


def mote_exp_total(counts) -> int:
    """Session potential earned, using each grade's own exp value."""
    return sum(max(0, int(n)) * exp
               for n, exp in zip(counts or (), MOTE_TIER_EXP))


def fmt_mote_tiers(counts) -> str:
    """The compact ledger readout: tier counts, lowest tier on the left.

    Ten grades would be a twenty-character cell if every one were printed, so
    the readout stops at the highest grade that has actually dropped.  Early in
    a session that is one or two numbers; it only grows when a rare grade
    earns the space.
    """
    values = [max(0, int(n)) for n in (counts or ())]
    highest = max((index for index, n in enumerate(values) if n), default=-1)
    if highest < 0:
        return "\u2014"
    return "/".join(str(n) for n in values[:highest + 1])


# Rune Seed dimensions are the inner canvas size at 100% font scaling.  The
# one-pixel Vellum frame makes the actual toplevel 94x50.  The extra width turns
# the old gem-like square into a readable capsule while remaining a tiny HUD.
RUNE_SEED_WIDTH = 92
RUNE_SEED_HEIGHT = 48
RUNE_SEED_COMBAT_LABEL = "DPS"
RUNE_SEED_ICON_SIZE = 32
MINI_BASE_WIDTH = RUNE_SEED_WIDTH + 2
MINI_MIN_WIDTH = MINI_BASE_WIDTH
# Starred cards now form a scrollable carousel.  Keeping the existing four-card
# budget preserves user configuration while rendering only one metric at once.
MINI_MAX_CELLS = 4
# Fresh installs seed only the live/session DPS face. Players can still star
# up to four ledger cards to build a carousel, but secondary metrics never
# appear until they are deliberately chosen.
DEFAULT_RUNE_SEED_CARDS = ("combat",)
LEGACY_DEFAULT_RUNE_SEED_CARDS = ("combat", "kills", "money", "motes")
RUNE_SEED_CONFIG_VERSION = 3
# Windows names the diagonal resize cursor "size_nw_se"; X11 uses
# "bottom_right_corner".
RESIZE_CURSOR = "size_nw_se" if os.name == "nt" else "bottom_right_corner"
MINI_BASE_HEIGHT = RUNE_SEED_HEIGHT + 2

# The expanded HUD is deliberately substantial: it is the high-detail state,
# not a second compact mode.  Saved custom sizes remain supported and are
# clamped into this wider, more legible range.
FULL_DEFAULT_SIZE = (550, 820)
FULL_MIN_WIDTH = 440
FULL_MIN_HEIGHT = 520
FULL_MAX_WIDTH = 820
FULL_MAX_HEIGHT = 1000
HUD_MORPH_STEPS = 16
HUD_MORPH_MS = 240
HUD_MORPH_FRAME_MS = 16
RUNE_ALERT_SECONDS = 2.0

MINI_CARD_LABELS = {
    "combat": "COMBAT",
    "kills": "SLAYING",
    "loot": "SPOILS",
    "money": "COIN",
    "progress": "PROGRESSION",
    "motes": "MOTES",
    "faction": "STANDING",
    "travels": "JOURNEY",
}
MINI_COMPACT_LABELS = {
    "progress": "XP",
    "combat": "DPS",
    "kills": "KILLS",
    "faction": "REP",
    "travels": "TRAVEL",
}

GLASS_THEME = {
    "bg": "#03080e",
    "panel": "#07131d",
    "raised": "#0b1d29",
    "line": "#2f7285",
    "line_soft": "#173746",
    "gold": "#6ec8de",
    "gold_bright": "#d9f7ff",
    "cyan": "#73dcff",
    "text": "#e8f7fb",
    "dim": "#8fb1bd",
    "hp": "#f25567",
    "mana": "#5c8fff",
    "endur": "#d2aa5e",
    "green": "#46e0ad",
    "ember": "#a980ff",
    "parchment": "#bad7df",
    "void": "#010407",
    "meter": "#0b2430",
    "meter_edge": "#3eaac2",
}


def theme_palette(name="vellum", high_contrast=False) -> dict:
    """Resolve one coherent palette; theme changes intentionally apply at launch."""
    theme = GLASS_THEME if str(name).strip().casefold() == "glass" else THEME
    palette = dict(theme)
    if high_contrast:
        palette.update(bg="#000000", panel="#0a0a0a", raised="#171717",
                       line="#74818a", line_soft="#3e474d", text="#ffffff",
                       dim="#c6cdd1", gold_bright="#ffe184", cyan="#9cc4ff")
    return palette


def compact_hud_number(value) -> str:
    """Compact a numeric HUD value without hiding meaningful precision."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "\u2014"
    magnitude = abs(number)
    if magnitude >= 1_000_000:
        text = f"{number / 1_000_000:.1f}m"
    elif magnitude >= 10_000:
        text = f"{number / 1_000:.1f}k"
    elif magnitude >= 1_000:
        text = f"{number / 1_000:.2f}k"
    else:
        text = f"{number:.0f}"
    return text.replace(".0m", "m").replace(".0k", "k")


def rune_seed_keys(starred) -> list[str]:
    """Return the stable, bounded metric carousel used by the Rune Seed."""
    if not isinstance(starred, list):
        return ["combat"]
    keys = []
    for key in starred:
        if key in MINI_CARD_LABELS and key not in keys:
            keys.append(key)
    return (keys or ["combat"])[:MINI_MAX_CELLS]


def toggle_rune_seed_star(starred, key) -> list[str]:
    """Toggle one metric while keeping the visible carousel honest.

    The Rune Seed can expose four metrics.  When a fifth is selected, the
    oldest selection is replaced instead of silently keeping an unreachable
    starred card in the expanded ledger.
    """
    keys = []
    for item in (starred if isinstance(starred, list) else []):
        if item in MINI_CARD_LABELS and item not in keys:
            keys.append(item)
    if key not in MINI_CARD_LABELS:
        return keys[:MINI_MAX_CELLS]
    if key in keys:
        if len(keys) == 1:
            return keys
        keys.remove(key)
    else:
        keys.append(key)
        if len(keys) > MINI_MAX_CELLS:
            keys = keys[-MINI_MAX_CELLS:]
    return keys


def cycle_rune_seed_index(index, direction, count) -> int:
    """Wrap a mouse-wheel carousel index in either direction."""
    try:
        size = max(1, int(count))
        current = int(index)
        step = 1 if int(direction) >= 0 else -1
    except (TypeError, ValueError):
        return 0
    return (current + step) % size


def rounded_rectangle_points(x1, y1, x2, y2, radius) -> list[float]:
    """Control points for a smooth Tk canvas capsule/rounded rectangle."""
    left, right = sorted((float(x1), float(x2)))
    top, bottom = sorted((float(y1), float(y2)))
    corner = max(0.0, min(float(radius), (right - left) / 2,
                          (bottom - top) / 2))
    return [
        left + corner, top, right - corner, top,
        right, top, right, top + corner,
        right, bottom - corner, right, bottom,
        right - corner, bottom, left + corner, bottom,
        left, bottom, left, bottom - corner,
        left, top + corner, left, top,
    ]


def rune_seed_content_layout(width, height) -> dict[str, tuple[float, ...]]:
    """Reserve non-overlapping cog and metric lanes in the compact capsule."""
    try:
        canvas_width = max(1.0, float(width))
        canvas_height = max(1.0, float(height))
    except (TypeError, ValueError):
        canvas_width, canvas_height = RUNE_SEED_WIDTH, RUNE_SEED_HEIGHT
    scale = max(0.5, canvas_height / RUNE_SEED_HEIGHT)
    center_x = 23.0 * scale
    center_y = canvas_height / 2.0
    half_icon = RUNE_SEED_ICON_SIZE / 2.0
    icon = (center_x - half_icon, center_y - half_icon,
            center_x + half_icon, center_y + half_icon)
    text_left = max(icon[2] + 5.0, 45.0 * scale)
    text = (text_left, 4.0 * scale,
            max(text_left + 1.0, canvas_width - 4.0 * scale),
            canvas_height - 4.0 * scale)
    return {"icon": icon, "text": text,
            "center": (center_x, center_y), "scale": (scale,)}


def blend_hex_color(first: str, second: str, amount: float) -> str:
    """Blend two #RRGGBB colors for small, alpha-free canvas animations."""
    try:
        ratio = max(0.0, min(1.0, float(amount)))
        start = tuple(int(first[index:index + 2], 16) for index in (1, 3, 5))
        end = tuple(int(second[index:index + 2], 16) for index in (1, 3, 5))
    except (TypeError, ValueError):
        return str(second if amount else first)
    values = [round(a + (b - a) * ratio) for a, b in zip(start, end)]
    return "#" + "".join(f"{value:02x}" for value in values)


def geometry_morph_at(start, end, progress):
    """Interpolate one clamped smoothstep HUD rectangle."""
    try:
        first = tuple(int(value) for value in start)
        last = tuple(int(value) for value in end)
        amount = max(0.0, min(1.0, float(progress)))
    except (TypeError, ValueError):
        return ()
    if len(first) != 4 or len(last) != 4:
        return ()
    eased = amount * amount * (3.0 - 2.0 * amount)
    return tuple(round(a + (b - a) * eased)
                 for a, b in zip(first, last))


def geometry_morph_frames(start, end, steps=HUD_MORPH_STEPS):
    """Return a smoothstep geometry sequence for a non-blocking HUD morph."""
    try:
        count = max(2, int(steps))
        a = tuple(int(v) for v in start)
        b = tuple(int(v) for v in end)
    except (TypeError, ValueError):
        return []
    if len(a) != 4 or len(b) != 4:
        return []
    frames = []
    for index in range(count):
        frames.append(geometry_morph_at(a, b, index / (count - 1)))
    frames[0] = a
    frames[-1] = b
    return frames


def mini_stat_label_plan(keys, available_width: int,
                         full_widths: dict[str, int],
                         compact_widths: dict[str, int],
                         divider_width: int = 9) -> dict[str, str]:
    """Choose whole mini-HUD labels without ever clipping a word in half.

    Widths include each label, its live value, and local padding.  The caller
    measures with the real Tk fonts; this pure planner makes the compact
    packing rule deterministic and independently testable.
    """
    ordered = [key for key in keys if key in MINI_CARD_LABELS]
    labels = {key: MINI_CARD_LABELS[key] for key in ordered}
    required = sum(max(0, int(full_widths.get(key, 0))) for key in ordered)
    required += max(0, len(ordered) - 1) * max(0, int(divider_width))
    if required <= max(0, int(available_width)):
        return labels
    # Compact the longest labels first so the row never clips mid-word.
    savings = sorted(
        (key for key in ordered if key in MINI_COMPACT_LABELS),
        key=lambda key: (full_widths.get(key, 0)
                         - compact_widths.get(key, full_widths.get(key, 0))),
        reverse=True,
    )
    for key in savings:
        required -= max(0, int(full_widths.get(key, 0)))
        required += max(0, int(compact_widths.get(key, full_widths.get(key, 0))))
        labels[key] = MINI_COMPACT_LABELS[key]
        if required <= max(0, int(available_width)):
            break
    return labels

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
# Legends appends Ranger-only shot outcomes after the ordinary damage sentence.
# Keep this grammar explicit so chat or unrelated parenthetical prose cannot be
# mistaken for combat evidence. ``ranged_result`` also lets the stats model
# count only variants that actually say Critical as critical hits.
RANGED_RESULT = (
    r"(?P<ranged_result> \((?:Critical|Double Bow Shot|"
    r"Strikethrough Double Bow Shot|Critical Double Bow Shot|"
    r"Strikethrough Critical Double Bow Shot)\))?"
)

LINE_RE = re.compile(
    r"^\[(?P<ts>[A-Za-z]{3} [A-Za-z]{3} +\d{1,2} \d{2}:\d{2}:\d{2} \d{4})\] (?P<msg>.*)$"
)

PATTERNS: list[tuple[str, re.Pattern]] = [
    # --- your damage ---
    ("ranged_out", re.compile(
        rf"^You shoot (?P<target>.+?) for (?P<dmg>\d+) points? of "
        rf"damage\.{RANGED_RESULT}$")),
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
    ("song_begin", re.compile(
        r"^You begin (?:to sing|singing) (?P<song>.+?)\.$")),
    ("cast_begin", re.compile(r"^You begin casting (?P<spell>.+?)\.$")),
    ("song_begin_other", re.compile(
        r"^(?P<caster>.+?) begins (?:to sing|singing) (?P<song>.+?)\.$")),
    ("cast_begin_other", re.compile(
        r"^(?P<caster>.+?) begins casting (?P<spell>.+?)\.$")),
    ("fizzle", re.compile(r"^Your (?P<spell>.+?) spell fizzles!$")),
    ("resist", re.compile(r"^Your target resisted the (?P<spell>.+?) spell\.$")),
    ("resist2", re.compile(
        r"^(?P<target>.+?) resisted your (?P<spell>.+?)!$")),
    ("interrupt", re.compile(
        r"^Your (?:(?P<spell>.+?) spell|spell) is interrupted\.$")),
    ("mez_immune", re.compile(r"^Your target cannot be mesmerized\.$")),
    ("spell_overwritten", re.compile(
        r"^Your (?P<spell>.+?) spell on (?P<target>.+?) has been overwritten\.$")),
    ("spell_fade", re.compile(
        r"^Your (?P<spell>.+?) spell has worn off(?: of (?P<target>.+?))?\.$")),
    # --- confirmed crowd-control outcomes -------------------------------
    # These lines are visible for nearby casters too. The independent mez
    # tracker accepts one only while a compatible local cast is pending.
    ("mez_landed_mesmerized", re.compile(
        r"^(?P<target>.+?) has been mesmerized\.$", re.I)),
    ("mez_landed_enthralled", re.compile(
        r"^(?P<target>.+?) has been enthralled\.$", re.I)),
    ("mez_landed_entranced", re.compile(
        r"^(?P<target>.+?) has been entranced\.$", re.I)),
    ("mez_landed_lights", re.compile(
        r"^(?P<target>.+?) gawks at the glowing lights\.$", re.I)),
    ("mez_landed_screaming", re.compile(
        r"^(?P<target>.+?) begins to scream\.$", re.I)),
    ("mez_landed_lullaby", re.compile(
        r"^(?P<target>.+?)'s head nods\.$", re.I)),
    ("mez_landed_pixie", re.compile(
        r"^(?P<target>.+?)'s eyes glaze over\.$", re.I)),
    ("mez_landed_fascinated", re.compile(
        r"^(?P<target>.+?) has been fascinated\.$", re.I)),
    ("mez_landed_glamour", re.compile(
        r"^(?P<target>.+?) has been mesmerized by the Glamour of Kintaz\.$",
        re.I)),
    ("mez_landed_rapture", re.compile(
        r"^(?P<target>.+?) (?:swoons in raptured bliss|"
        r"(?:has entered|enters) a state of rapture)\.$",
        re.I)),
    ("lull_landed", re.compile(
        r"^(?P<target>.+?) looks less aggressive\.$", re.I)),
    ("mez_awakened", re.compile(
        r"^(?P<target>.+?) has been awakened by (?P<breaker>.+?)\.$", re.I)),
    # --- xp / progression ---
    ("xp", re.compile(r"^You gain (?P<party>party )?experience!+(?: \((?P<pct>[\d.]+)%\))?.*$")),
    ("level", re.compile(r"^You have gained a level! Welcome to level (?P<level>\d+)!$")),
    ("skill", re.compile(r"^You have become better at (?P<skill>.+?)! \((?P<value>\d+)\)$")),
    ("aa", re.compile(r"^You have gained an ability point!.*$")),
    # --- loot / money ---
    # Stackables can arrive as a count rather than an article ("looted 5
    # Motes of Minor Potential"), and the old article-only form dropped those
    # lines on the floor entirely - they never reached the ledger at all.
    # Legends also has explicit terminal dispositions. Keep these ahead of the
    # ordinary form so a stored/sold acquisition produces exactly one event.
    ("loot_storage", re.compile(
        r"^(?:--)?You (?:have )?looted (?:an?|(?P<qty>\d+)) (?P<item>.+?)"
        r" from (?P<source>.+?)'s corpse and stored it in your "
        r"(?P<destination>tradeskill depot|Dragon Hoard|currency)\.?(?:--)?$", re.I)),
    ("loot_auto_sale", re.compile(
        r"^(?:--)?You (?:have )?looted (?:an?|(?P<qty>\d+)) (?P<item>.+?)"
        r" from (?P<source>.+?)'s corpse and sold it for (?P<coins>.+?)\."
        r"(?:--)?$", re.I)),
    ("loot_merge", re.compile(
        r"^You have successfully merged two items together to create a new "
        r"item:\s*(?P<item>.+?)\.?$", re.I)),
    ("loot_upgrade", re.compile(
        r"^(?:--)?You (?:have )?looted (?:an?|(?P<qty>\d+)) "
        r"(?P<input_item>.+?) from (?P<source>.+?)'s corpse to create "
        r"(?:an? )?(?P<item>.+?)\.?(?:--)?$", re.I)),
    ("loot_inventory", re.compile(
        r"^(?P<item>.+?) has been placed in your inventory!$", re.I)),
    ("loot", re.compile(
        r"^--You have looted (?:an?|(?P<qty>\d+)) (?P<item>.+?)"
        r"(?: from (?P<source>.+?)'s corpse)?\.--$")),
    ("loot2", re.compile(
        r"^--(?P<who>\S+) has looted (?:an?|(?P<qty>\d+)) (?P<item>.+?)\.--$")),
    ("money", re.compile(r"^You receive (?P<coins>.+?) (?:from the corpse|as your split)\.$")),
    ("money_sale", re.compile(r"^You receive (?P<coins>.+?) from (?P<vendor>.+?) for the (?P<item>.+?)\(s\)\.$")),
    # --- world ---
    ("auto_attack", re.compile(
        r"^Auto attack is (?P<state>on|off)\.$", re.I)),
    ("faction", re.compile(r"^Your faction standing with (?P<faction>.+?) has been adjusted by (?P<delta>-?\d+)\.$")),
    ("zone", re.compile(r"^You have entered (?P<zone>.+)\.$")),
    # EQL builds do not consistently emit a class trio.  When one does, only
    # this explicit system-style sentence is eligible for exact inference;
    # normal chat and spell names are never guessed as a composition.
    ("composition", re.compile(
        r"^(?:Your active classes are|Your active class composition is|"
        r"Your class composition is|Active classes:)\s+(?P<classes>.+?)[.!]?$",
        re.I)),
    # Group membership is explicit log evidence. It lets the desktop overlay
    # distinguish known group contributors from unrelated nearby players.
    ("group_join", re.compile(
        r"^(?P<member>[A-Za-z][A-Za-z'`-]{1,31}) (?:has joined|joins) (?:the|your) group\.$",
        re.I)),
    ("group_leave", re.compile(
        r"^(?P<member>[A-Za-z][A-Za-z'`-]{1,31}) has (?:left|been removed from) the group\.$",
        re.I)),
    ("group_clear", re.compile(
        r"^(?:Your group has been disbanded|You have left the group|"
        r"You have been removed from the group)\.?$", re.I)),
    # --- pets ---
    # Summoned pets have one-word names, while charmed creatures retain names
    # such as "A rock golem". Both ownership messages are visible only to the
    # owner, making them safe claims even in a group or a busy camp.
    ("pet_attack", re.compile(
        r"^(?P<pet>.+?) (?:tells|told) you, 'Attacking (?P<target>.+?) Master\.'$")),
    ("pet_leader", re.compile(
        r"^(?P<pet>.+?) says,? 'My leader is (?P<leader>\S+?)\.'$")),
    # Legends emits the first form when charm lands; some EQ clients use the
    # older blink emote. Neither proves ownership alone because groupmates'
    # charms are also visible, so application requires our own recent cast.
    ("pet_charm", re.compile(r"^(?P<pet>.+?) has been charmed\.$", re.I)),
    ("pet_charm", re.compile(r"^(?P<pet>.+?) blinks\.$", re.I)),
    # --- alert-worthy lines ---
    ("tell_in", re.compile(r"^(?P<sender>[A-Za-z]+) tells you, '(?P<msg>.*)'$")),
    ("summoned", re.compile(r"^You have been summoned!?$")),
    # --- bystanders (third party) ---
    ("ranged_third", re.compile(
        rf"^(?P<attacker>.+?) shoots? (?P<target>.+?) for (?P<dmg>\d+) "
        rf"points? of damage\.{RANGED_RESULT}$")),
    ("melee_third", re.compile(
        rf"^(?P<attacker>.+?) (?:{MELEE_VERBS}) (?P<target>.+?) for (?P<dmg>\d+) points? of damage\.{CRIT}$")),
    ("dot_third", re.compile(
        r"^(?P<target>.+?) has taken (?P<dmg>\d+) damage from (?P<spell>.+?) by (?P<caster>.+?)\.$")),
    ("nuke_third", re.compile(
        rf"^(?P<attacker>.+?) hit (?P<target>.+?) for (?P<dmg>\d+) points? of \w+ damage by (?P<spell>.+?)\.{CRIT}$")),
    ("miss_third", re.compile(
        r"^(?P<attacker>.+?) tries to \w+(?: on)? (?P<target>.+?), but (?P<reason>.+)!$")),
    # --- potential motes: last resort ---------------------------------------
    # Motes are a progression currency, and Legends does not always announce
    # one with the corpse-loot sentence the ledger above understands - it can
    # be a plain "You receive ..." system line with no dashes and no article.
    # This pattern is deliberately last so a real loot line is still counted as
    # loot; it only catches the forms nothing else claims. It anchors on "You"
    # plus an acquisition verb so a player typing the item name in chat is
    # never counted.
    ("mote_gain", re.compile(
        r"^(?:--)?\s*You\s+(?:have\s+|just\s+)*"
        r"(?:loot|looted|receive|receives|received|gain|gains|gained|"
        r"acquire|acquires|acquired|find|found|get|got)\s+"
        r"(?:an?|the|(?P<qty>\d+))?\s*"
        r"(?P<item>motes?\s+of\s+(?:[A-Za-z]+\s+)?potential)\b",
        re.IGNORECASE)),
]

# Landing prose is not a spell identity: Mesmerize, Mesmerization, and Dazzle
# deliberately share the same line. Compatibility is checked against the
# pending local cast so another player's visible mez cannot create a timer.
MEZ_LANDING_COMPATIBILITY = {
    "mez_landed_mesmerized": frozenset({
        "Mesmerize", "Mesmerization", "Dazzle",
    }),
    "mez_landed_enthralled": frozenset({"Enthrall"}),
    "mez_landed_entranced": frozenset({"Entrance"}),
    "mez_landed_lights": frozenset({"Entrancing Lights"}),
    "mez_landed_screaming": frozenset({"Screaming Terror"}),
    "mez_landed_lullaby": frozenset({"Kelin's Lucid Lullaby"}),
    "mez_landed_pixie": frozenset({
        "Crission's Pixie Strike", "Sionachie's Dreams",
    }),
    "mez_landed_fascinated": frozenset({"Fascination"}),
    "mez_landed_glamour": frozenset({"Glamour of Kintaz"}),
    "mez_landed_rapture": frozenset({"Rapture"}),
}
MEZ_ONLY_KINDS = frozenset((
    *MEZ_LANDING_COMPATIBILITY,
    "cast_begin_other", "song_begin_other", "mez_immune",
    "spell_overwritten", "mez_awakened",
))
LULL_LANDING_COMPATIBILITY = frozenset({
    "Pacify", "Calm", "Lull", "Soothe", "Calm Animal", "Pacification",
})
CONTROL_ONLY_KINDS = frozenset((*MEZ_ONLY_KINDS, "lull_landed"))


def observe_mez_log_event(tracker: MezTracker, ts: datetime,
                          kind: str, groups: dict) -> None:
    """Feed one parsed line to the independent crowd-control state machine."""
    if kind == "cast_begin":
        # Every own cast replaces stale correlation, including a non-mez cast.
        tracker.begin_cast(groups.get("spell", ""), ts)
    elif kind == "song_begin":
        tracker.begin_cast(groups.get("song", ""), ts)
    elif kind == "cast_begin_other":
        tracker.observe_nearby_cast(groups.get("spell", ""), ts)
    elif kind == "song_begin_other":
        tracker.observe_nearby_cast(groups.get("song", ""), ts)
    elif kind == "fizzle":
        tracker.observe_fizzle()
    elif kind == "interrupt":
        tracker.observe_interrupt()
    elif kind in ("resist", "resist2"):
        tracker.observe_resist(groups.get("spell"), ts)
    elif kind == "mez_immune":
        tracker.observe_resist(occurred_at=ts)
    elif kind in MEZ_LANDING_COMPATIBILITY:
        pending = tracker.pending
        if (pending is not None
                and pending.resolved.name in MEZ_LANDING_COMPATIBILITY[kind]):
            tracker.observe_landing(groups.get("target", ""), ts)
        else:
            tracker.observe_unattributed_landing(ts)
    elif kind == "spell_fade":
        tracker.observe_fade(groups.get("target"), ts, groups.get("spell"))
    elif kind == "spell_overwritten":
        # A nearby caster now owns the effect and its duration is unknowable
        # from our log.  Remove our row rather than offering false confidence.
        tracker.observe_overwrite(
            groups.get("target"), ts, groups.get("spell"))
    elif kind == "mez_awakened":
        tracker.observe_damage(groups.get("target", ""), ts)
    elif kind in {
            "melee_out", "ranged_out", "dot_out", "nuke_out_plain",
            "nuke_out_school", "ds_out", "melee_third", "ranged_third",
            "dot_third", "nuke_third"}:
        tracker.observe_damage(groups.get("target", ""), ts)
        if kind in {"melee_third", "ranged_third", "nuke_third"}:
            tracker.observe_damage(groups.get("attacker", ""), ts)
    elif kind in {"melee_in", "miss_in", "nuke_in", "miss_third"}:
        # A tracked actor attacking anyone is definitive evidence it is awake,
        # even when the swing misses and no damage line can break the row.
        tracker.observe_damage(groups.get("attacker", ""), ts)
    elif kind in ("kill_you", "kill_other"):
        tracker.observe_kill(groups.get("target", ""), ts)
    elif kind == "death_you":
        tracker.clear()
    elif kind == "zone":
        zone = groups.get("zone", "")
        if is_real_zone_transition(zone):
            tracker.clear()


def observe_lull_log_event(tracker: LullTracker, ts: datetime,
                           kind: str, groups: dict,
                           caster_level: int | None = None) -> None:
    """Feed one parsed line to the independent lull evidence state machine."""

    if kind == "cast_begin":
        tracker.begin_cast(groups.get("spell", ""), ts, caster_level)
    elif kind == "song_begin":
        tracker.begin_cast(groups.get("song", ""), ts, caster_level)
    elif kind == "cast_begin_other":
        tracker.observe_nearby_cast(
            groups.get("spell", ""), ts, caster_level)
    elif kind == "song_begin_other":
        tracker.observe_nearby_cast(
            groups.get("song", ""), ts, caster_level)
    elif kind == "fizzle":
        tracker.observe_fizzle(ts)
    elif kind == "interrupt":
        tracker.observe_interrupt(ts)
    elif kind in ("resist", "resist2"):
        tracker.observe_resist(ts, groups.get("spell"))
    elif kind == "lull_landed":
        pending = tracker.pending
        if (pending is not None
                and pending.resolved.name in LULL_LANDING_COMPATIBILITY):
            tracker.observe_landing(groups.get("target", ""), ts)
        else:
            tracker.observe_unattributed_landing(ts)
    elif kind == "spell_fade":
        tracker.observe_fade(groups.get("target"), ts, groups.get("spell"))
    elif kind == "spell_overwritten":
        tracker.observe_overwrite(
            groups.get("target"), ts, groups.get("spell"))
    elif kind in {
            "melee_out", "ranged_out", "dot_out", "nuke_out_plain",
            "nuke_out_school", "ds_out", "melee_third", "ranged_third",
            "dot_third", "nuke_third"}:
        tracker.observe_damage(groups.get("target", ""), ts)
        if kind in {"melee_third", "ranged_third", "nuke_third"}:
            tracker.observe_damage(groups.get("attacker", ""), ts)
    elif kind in {"melee_in", "miss_in", "nuke_in", "miss_third"}:
        tracker.observe_damage(groups.get("attacker", ""), ts)
    elif kind in ("kill_you", "kill_other"):
        tracker.observe_kill(groups.get("target", ""), ts)
    elif kind == "death_you":
        tracker.clear()
    elif kind == "zone":
        if is_real_zone_transition(groups.get("zone", "")):
            tracker.clear()


def apply_log_models(stats, tracker: MezTracker, ts: datetime, kind: str,
                     groups: dict, *, count_lifetime: bool = True,
                     lull_tracker: LullTracker | None = None,
                     caster_level: int | None = None):
    """Apply a parsed line without letting timer-only prose alter DPS state."""
    charm_break_events = ()
    if kind not in CONTROL_ONLY_KINDS:
        charm_break_events = stats.apply(
            ts, kind, groups, count_lifetime=count_lifetime)
    observe_mez_log_event(tracker, ts, kind, groups)
    if lull_tracker is not None:
        observe_lull_log_event(
            lull_tracker, ts, kind, groups, caster_level)
    return charm_break_events

COIN_RE = re.compile(r"(\d+) (platinum|gold|silver|copper)")
COIN_COPPER = {"platinum": 1000, "gold": 100, "silver": 10, "copper": 1}

ZONE_FALSE_POSITIVES = ("an area", "area where", "an Arena")


def is_real_zone_transition(zone_name: str) -> bool:
    """Reject environmental prose that shares EQ's zone-message prefix."""

    folded = (zone_name or "").casefold()
    return not any(false_positive.casefold() in folded
                   for false_positive in ZONE_FALSE_POSITIVES)

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


def pet_identity(name: str) -> str:
    """Canonical key shared by pet chatter and sentence-cased combat lines."""
    return (name or "").strip().casefold()


def normalize_pet_name(name: str) -> str:
    """Normalize sentence casing without discarding a creature's article."""
    value = (name or "").strip()
    return value[:1].upper() + value[1:] if value else value


def looks_like_charmed_actor(name: str) -> bool:
    value = (name or "").strip()
    return bool(re.match(r"^(?:a|an|the)\s+", value, re.I)
                or not looks_like_player_actor(value))


def is_known_charm_spell(name: str) -> bool:
    """Recognize charm families while accepting Legends rank suffixes."""
    value = re.sub(r"\s+(?:[IVXLCDM]+|\d+)$", "", name.strip(), flags=re.I)
    return value.casefold() in CHARM_SPELL_FAMILIES


PLAYER_ACTOR_RE = re.compile(r"^[A-Z][A-Za-z'`-]{1,31}$")


def looks_like_player_actor(name: str) -> bool:
    """Conservatively identify player/pet-style names in third-party lines."""
    return bool(PLAYER_ACTOR_RE.fullmatch((name or "").strip()))


def parse_coins(text: str) -> int:
    return sum(int(n) * COIN_COPPER[unit] for n, unit in COIN_RE.findall(text))


def _quantity(groups) -> int:
    """A loot line's stack count, defaulting to the single-item article form."""
    try:
        amount = int(groups.get("qty") or 1)
    except (TypeError, ValueError):
        return 1
    return max(1, amount)


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
    zone: str = ""
    # Assigned once by the desktop worker from the immutable start evidence
    # and initial title. It is never recomputed as later targets evolve.
    journal_id: str = ""
    journal_raid_tier: int | None = None
    journal_raid_mode: str = ""
    composition: str = ""
    composition_source: str = "unset"
    damage: int = 0
    # Text logs do not carry actor IDs. When a charmed creature attacks an
    # identically named NPC, the direction of each line cannot be proven; we
    # still include the damage, but surface this subtotal as an estimate.
    ambiguous_pet_damage: int = 0
    charmed_pet_damage: int = 0
    summoned_pet_damage: int = 0
    targets: dict = field(default_factory=lambda: defaultdict(int))
    sources: dict = field(default_factory=lambda: defaultdict(
        lambda: {"t": 0, "h": 0, "max": 0}))
    source_categories: dict[str, str] = field(default_factory=dict)
    healing_sources: dict = field(default_factory=lambda: defaultdict(
        lambda: {"t": 0, "h": 0, "max": 0, "over": 0}))
    actor_damage: dict = field(default_factory=lambda: defaultdict(
        lambda: {"t": 0, "h": 0, "max": 0}))
    # Per-contributor sources power the desktop drill-down without changing
    # the conservative actor totals above. Categories are attached only when
    # the log grammar proves them (for example melee, ranged, or a DoT).
    actor_sources: dict = field(default_factory=lambda: defaultdict(
        lambda: defaultdict(
            lambda: {"t": 0, "h": 0, "max": 0, "category": "unknown"})))
    actor_roles: dict = field(default_factory=dict)
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
        self.last_group_action: datetime | None = None
        self.last_support_action: datetime | None = None
        self.last_combat_signal: datetime | None = None
        self.damage_by_source: dict[str, dict] = defaultdict(
            lambda: {"t": 0, "h": 0, "max": 0})
        self.healing_by_source: dict[str, dict] = defaultdict(
            lambda: {"t": 0, "h": 0, "max": 0, "over": 0})
        self.actor_damage: dict[str, dict] = defaultdict(
            lambda: {"t": 0, "h": 0, "max": 0})
        self.actor_roles: dict[str, str] = {}
        self.group_members: set[str] = set()
        self.actor_healing: dict[str, dict] = defaultdict(
            lambda: {"t": 0, "h": 0, "max": 0})
        self.melee_hits = self.melee_misses = 0
        self.crits = 0
        self.enemy_misses = 0
        self.auto_attack = False
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
        # Charm aliases are intentionally ephemeral. Unlike summoned-pet
        # names, a creature name can also belong to unrelated NPCs later.
        self.charmed_pet_names: set[str] = set()
        self.charm_breaks = CharmBreakDetector()
        self.charm_spells: set[str] = set()
        self.pending_cast: tuple[str, datetime] | None = None
        # One named direct cast may emit a damage result for several targets.
        # Keep only the short landing fan-out, separate from charm ownership.
        self.recent_direct_damage_cast: tuple[str, datetime] | None = None
        self.pet_damage = 0
        self.charmed_pet_damage = 0
        self.summoned_pet_damage = 0
        self.ambiguous_pet_damage = 0
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
        self.progression = ProgressionTracker()
        self.xp_pct = 0.0
        self.xp_pct_known = False
        self.level: int | None = None
        self.xp_since_level = 0.0
        self.levelups = 0
        # loot / money
        self.copper = 0
        self.loot: dict[str, int] = defaultdict(int)
        # Motes are counted here rather than derived from the loot ledger,
        # because not every way a mote arrives produces a loot line.
        self.motes: list[int] = [0] * len(MOTE_TIERS)
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
        key = pet_identity(name)
        return any(pet_identity(pet) == key for pet in self.pet_names)

    def is_charmed_pet(self, name: str) -> bool:
        key = pet_identity(name)
        return any(pet_identity(pet) == key for pet in self.charmed_pet_names)

    def _pet_display_name(self, name: str) -> str | None:
        key = pet_identity(name)
        return next((pet for pet in self.pet_names
                     if pet_identity(pet) == key), None)

    def _drop_pet(self, name: str) -> None:
        key = pet_identity(name)
        self.charm_breaks.clear_silently(name)
        self.pet_names = {pet for pet in self.pet_names
                          if pet_identity(pet) != key}
        self.charmed_pet_names = {
            pet for pet in self.charmed_pet_names
            if pet_identity(pet) != key
        }
        for pet in list(self.pet_last_seen):
            if pet_identity(pet) == key:
                del self.pet_last_seen[pet]

    def _drop_charmed_pets(self) -> None:
        for pet in list(self.charmed_pet_names):
            self._drop_pet(pet)
        # Defensive synchronization if ownership state was learned before a
        # display alias was populated. Lifecycle cleanup never means "break".
        self.charm_breaks.clear_silently()

    def persistent_pet_names(self) -> list[str]:
        """Only stable summoned-pet identities may survive an app restart."""
        charm_keys = {pet_identity(pet) for pet in self.charmed_pet_names}
        return sorted(pet for pet in self.pet_names
                      if pet_identity(pet) not in charm_keys)

    def _touch(self, ts: datetime):
        if self.session_start is None:
            self.session_start = ts
        elif self.session_gap and self.last_event and ts - self.last_event > self.session_gap:
            level, xsl, known = self.level, self.xp_since_level, self.xp_pct_known
            pets = set(self.persistent_pet_names())
            group_members = set(self.group_members)
            zone = self.zone
            self.reset()
            self.session_start = ts
            self.level, self.xp_since_level, self.xp_pct_known = level, xsl, known
            self.pet_names, self.zone = pets, zone
            self.group_members = group_members
        self.last_event = ts

    def set_composition(self, composition, *, source: str = "manual",
                        retag_active: bool = True) -> str:
        """Set the exact active EQL class trio and optionally retag combat."""
        canonical = normalize_composition(composition)
        if self.composition and canonical != self.composition:
            # A new loadout can have a different level/XP requirement.
            self.progression = ProgressionTracker()
        self.composition = canonical
        self.composition_source = source if canonical else "unset"
        if retag_active and getattr(self, "fight", None) is not None:
            self.fight.composition = canonical
            self.fight.composition_source = self.composition_source
        return canonical

    def _new_fight(self, ts: datetime) -> Fight:
        return Fight(start=ts, end=ts, zone=self.zone,
                     composition=self.composition,
                     composition_source=self.composition_source)

    # -- combat windows --------------------------------------------------
    def _own_combat(self, ts: datetime):
        self.last_own_action = ts
        self._combat_signal(ts, own=True)

    def _combat_signal(self, ts: datetime, own: bool = False,
                       group: bool = False):
        if group:
            self.last_group_action = ts
        trusted = own or group
        if self.fight is None:
            if not trusted:
                return  # bystanders never open a fight
            self.fight = self._new_fight(ts)
        else:
            trusted_times = tuple(
                stamp for stamp in (
                    self.last_own_action, self.last_group_action)
                if stamp is not None)
            if (not trusted and (
                    not trusted_times
                    or ts - max(trusted_times) > BYSTANDER_GRACE)):
                return  # unrelated activity cannot stretch the encounter
            if ts - self.fight.end > COMBAT_GAP:
                self._close_fight()
                if trusted:
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

    def _record_actor_damage(self, actor: str, dmg: int,
                             role: str = "observed", *,
                             source: str = "Unattributed damage",
                             category: str = "unknown"):
        if self.fight is None:
            return
        self._add_metric(self.fight.actor_damage, actor, dmg)
        self._add_metric(self.actor_damage, actor, dmg)
        actor_source = self.fight.actor_sources[actor][source]
        actor_source["t"] += dmg
        actor_source["h"] += 1
        actor_source["max"] = max(actor_source["max"], dmg)
        if category != "unknown":
            actor_source["category"] = category
        # Preserve ownership with the fight. Charmed aliases are deliberately
        # removed when charm breaks, so classifying historical rows from the
        # current pet-name set would silently turn old pet damage into an
        # unrelated observed actor.
        self.fight.actor_roles[actor] = role
        self.actor_roles[actor] = role

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
              crit: bool = False, actor: str | None = None,
              actor_role: str | None = None, category: str = "unknown",
              actor_source: str | None = None,
              actor_source_category: str | None = None):
        self._own_combat(ts)
        if self.fight is None:
            self.fight = self._new_fight(ts)
        self._record_actor_damage(
            actor or self.character or "You", dmg,
            actor_role or ("self" if actor is None else "observed"),
            source=actor_source or source,
            category=(actor_source_category
                      if actor_source_category is not None else category))
        self.fight.damage += dmg
        normalized_target = normalize_mob(target)
        self.fight.targets[normalized_target] += dmg
        self.fight.observed_targets[normalized_target] += dmg
        self.fight.add_timeline(ts, "out", dmg)
        fight_src = self.fight.sources[source]
        if category != "unknown":
            self.fight.source_categories[source] = category
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

    def _direct_cast_result(self, ts: datetime,
                            spell: str | None = None) -> str | None:
        """Return a proven direct-cast name for one bounded result line."""
        if self.pending_cast:
            cast_spell, cast_at = self.pending_cast
            if ((spell is None or cast_spell.casefold() == spell.casefold())
                    and timedelta(0) <= ts - cast_at <= CHARM_LAND_WINDOW):
                self.pending_cast = None
                self.recent_direct_damage_cast = (cast_spell, ts)
                return cast_spell
        if self.recent_direct_damage_cast:
            cast_spell, landed_at = self.recent_direct_damage_cast
            if ((spell is None or cast_spell.casefold() == spell.casefold())
                    and timedelta(0) <= ts - landed_at
                    <= DIRECT_CAST_FANOUT_WINDOW):
                return cast_spell
        return None

    def _observe_actor_damage(self, ts: datetime, actor: str, target: str,
                              dmg: int, *, source: str,
                              category: str = "unknown"):
        """Add a visible player/pet contributor without polluting self DPS."""
        actor = actor.strip()
        if not looks_like_player_actor(actor):
            return
        group_names = {member.casefold() for member in self.group_members}
        is_group_member = actor.casefold() in group_names
        self._combat_signal(ts, group=is_group_member)
        if self.fight is None:
            return
        known_targets = {name.casefold() for name in self.fight.targets}
        if normalize_mob(actor).casefold() in known_targets:
            return
        role = "group" if is_group_member else "observed"
        self._record_actor_damage(
            actor, dmg, role, source=source, category=category)
        self.fight.observed_targets[normalize_mob(target)] += dmg
        self.fight.add_timeline(ts, "out", dmg)

    @staticmethod
    def _third_party_damage_source(kind: str, groups: dict) -> tuple[str, str]:
        """Return only the source/category directly proven by a combat line."""
        if kind == "melee_third":
            return "Melee", "melee"
        if kind == "ranged_third":
            result = (groups.get("ranged_result") or "").casefold()
            return ("Double Bow Shot" if "double bow shot" in result
                    else "Ranged"), "melee"
        if kind == "dot_third":
            spell = (groups.get("spell") or "Unknown DoT").strip()
            return f"DoT: {spell}", "dot"
        spell = (groups.get("spell") or "Unknown source").strip()
        # Third-party direct-damage lines do not reveal whether the named
        # effect was a cast spell, item click, or proc. Retain the useful
        # label while deliberately leaving its category unknown.
        return f"Non-melee: {spell}", "unknown"

    def _count_motes(self, g: dict) -> None:
        """Add one acquisition line's motes to the session tally."""
        tier = mote_tier_index(g.get("item", ""))
        if tier is not None:
            self.motes[tier] += _quantity(g)

    # -- event application ----------------------------------------------
    def apply(self, ts: datetime, kind: str, g: dict, *, count_lifetime: bool = True
              ) -> tuple[CharmBreakEvent, ...]:
        """Apply one parsed log event and return any proven charm breaks.

        The tuple belongs only to this input line. Callers can immediately
        turn it into banners/sounds without polling mutable model state.
        """
        charm_break_events: list[CharmBreakEvent] = []
        if self.fight:
            ref = self.last_combat_signal or self.fight.end
            if ts - ref > COMBAT_GAP:
                self._close_fight()
        self._touch(ts)
        self.progression.observe(ts, kind, g)
        self.log_lines += 1
        crit = (bool(g.get("crit"))
                or "critical" in (g.get("ranged_result") or "").casefold())

        if kind == "auto_attack":
            self.auto_attack = g.get("state", "").casefold() == "on"
        elif kind == "melee_out":
            self.melee_hits += 1
            self._deal(ts, g["target"], int(g["dmg"]), "Melee", crit,
                       category="melee")
        elif kind == "ranged_out":
            self.melee_hits += 1
            result = (g.get("ranged_result") or "").casefold()
            source = ("Double Bow Shot"
                      if "double bow shot" in result else "Ranged")
            self._deal(ts, g["target"], int(g["dmg"]), source, crit,
                       category="melee")
        elif kind == "miss_out":
            self.melee_misses += 1
            self._own_combat(ts)
            if self.fight:
                self.fight.misses += 1
        elif kind == "dot_out":
            self._deal(ts, g["target"], int(g["dmg"]), f"DoT: {g['spell']}",
                       crit, category="dot")
        elif kind == "nuke_out_plain":
            # This line carries no spell identity. It may be a spell, item
            # proc, or another non-melee source. Attribute it only when an
            # immediately preceding local cast supplies direct evidence.
            cast_spell = self._direct_cast_result(ts)
            self._deal(
                ts, g["target"], int(g["dmg"]),
                f"Spell: {cast_spell}" if cast_spell
                else "Unattributed non-melee",
                crit, category="spell" if cast_spell else "unknown")
        elif kind == "nuke_out_school":
            spell = g["spell"]
            category = ("spell" if self._direct_cast_result(ts, spell)
                        else "proc")
            self._deal(ts, g["target"], int(g["dmg"]),
                       f"{'Spell' if category == 'spell' else 'Proc'}: {spell}",
                       crit, category=category)
        elif kind == "ds_out":
            self._deal(ts, g["target"], int(g["dmg"]), "Damage shield",
                       category="damage_shield")

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
            self.auto_attack = False
            self.pending_cast = None
            self.recent_direct_damage_cast = None
            self._drop_charmed_pets()
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
            raw_target = g["target"].strip()
            mob = normalize_mob(raw_target)
            # "X has been slain by Y!" also fires when a groupmate or pet is
            # the one who died. A player-style victim killed by a mob-article
            # actor is an ally death, not a slain enemy: counting it would
            # name the dead ally in kill totals, the encounter title, and the
            # persisted SLAYING records.
            ally_death = (
                (self.is_pet(raw_target) or looks_like_player_actor(raw_target))
                and killer.split(" ", 1)[0].lower() in {"a", "an", "the"}
            )
            self._combat_signal(ts)
            if ally_death:
                self._feed(ts, "ally_death", 0, raw_target)
                # A differently named NPC killing the controlled creature is
                # the only useful death signal the text log can provide. If
                # both names are identical, keep ownership until charm fade:
                # the line could instead describe an enemy of the same type.
                if (self.is_charmed_pet(raw_target)
                        and pet_identity(raw_target) != pet_identity(killer)):
                    self._drop_pet(raw_target)
            else:
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
            self.last_support_action = ts
        elif kind == "cast_begin":
            self.casts += 1
            self.last_support_action = ts
            self.pending_cast = (g["spell"], ts)
            self.recent_direct_damage_cast = None
        elif kind == "fizzle":
            self.fizzles += 1
            self.pending_cast = None
            self.recent_direct_damage_cast = None
        elif kind in ("resist", "resist2"):
            self.resists += 1
            if self.pending_cast:
                cast_spell, _cast_at = self.pending_cast
                if cast_spell.casefold() == (g.get("spell") or "").casefold():
                    # An area spell can be resisted by one target and still
                    # land on another. Retain only its brief result fan-out.
                    self.recent_direct_damage_cast = (cast_spell, ts)
            self.pending_cast = None
        elif kind == "interrupt":
            self.interrupts += 1
            self.pending_cast = None
            self.recent_direct_damage_cast = None
        elif kind == "spell_fade":
            target = (g.get("target") or "").strip()
            charm_faded = (g["spell"].casefold() in self.charm_spells
                           or is_known_charm_spell(g["spell"]))
            if charm_faded:
                event = self.charm_breaks.observe_fade(
                    spell_name=g["spell"], occurred_at=ts,
                    target_name=target or None, is_charm_spell=True)
                if event is not None:
                    charm_break_events.append(event)
                if target and self.is_charmed_pet(target):
                    self._drop_pet(target)
                elif not target:
                    # Some clients omit the target from worn-off messages.
                    # The ownership model permits only one active charm, so a
                    # recognized targetless fade safely releases it.
                    self._drop_charmed_pets()

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

        elif kind in ("loot", "loot2", "loot_storage", "loot_auto_sale",
                      "loot_merge", "loot_upgrade", "loot_inventory"):
            who = g.get("who")
            if who is None or who == self.character:
                self.loot[g["item"]] += _quantity(g)
                self._count_motes(g)
            if kind == "loot_auto_sale" and g.get("coins"):
                self.copper += parse_coins(g["coins"])
        elif kind == "mote_gain":
            # Only reached by acquisition sentences no loot pattern claimed, so
            # a mote is never counted twice for one line.
            self._count_motes(g)
        elif kind in ("money", "money_sale"):
            coins = parse_coins(g["coins"])
            self.copper += coins

        elif kind == "faction":
            self.faction[g["faction"]] += int(g["delta"])
        elif kind == "zone":
            if is_real_zone_transition(g["zone"]):
                self.auto_attack = False
                # Charm never crosses a zone boundary. Keeping an NPC alias
                # here would turn a future same-named mob into personal DPS.
                self.pending_cast = None
                self.recent_direct_damage_cast = None
                self._drop_charmed_pets()
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
        elif kind == "group_join":
            member = (g.get("member") or "").strip()
            if member and member.casefold() != self.character.casefold():
                self.group_members.add(member)
        elif kind == "group_leave":
            member = (g.get("member") or "").strip().casefold()
            self.group_members = {
                existing for existing in self.group_members
                if existing.casefold() != member
            }
        elif kind == "group_clear":
            self.group_members.clear()

        elif kind == "tell_in":
            self.tells += 1
        elif kind == "summoned":
            pass  # alert layer handles it
        elif kind == "pet_attack":
            pet = g["pet"]
            charmed = (self.is_charmed_pet(pet)
                       or looks_like_charmed_actor(pet))
            self._register_pet(pet, ts, charmed=charmed)
            self._own_combat(ts)
        elif kind == "pet_leader":
            if (g["leader"].casefold() == self.character.casefold()
                    or self.character == "?"):
                pet = g["pet"]
                charmed = (self.is_charmed_pet(pet)
                           or looks_like_charmed_actor(pet))
                self._register_pet(pet, ts, charmed=charmed)
        elif kind == "pet_charm":
            # Everyone nearby sees this success line. Claim it only when it
            # follows *our* cast; another Enchanter's charm must stay observed
            # NPC activity rather than inflate our personal DPS.
            if self.pending_cast:
                spell, cast_at = self.pending_cast
                if (timedelta(0) <= ts - cast_at <= CHARM_LAND_WINDOW
                        and is_known_charm_spell(spell)):
                    self.charm_spells.add(spell.casefold())
                    self._register_pet(
                        g["pet"], ts, charmed=True, charm_spell=spell)
                self.pending_cast = None

        elif kind in (
                "melee_third", "ranged_third", "nuke_third", "dot_third"):
            attacker = (g.get("attacker") or g.get("caster") or "").strip()
            dmg = int(g["dmg"])
            actor_source, actor_category = self._third_party_damage_source(
                kind, g)
            if attacker and self.is_pet(attacker):
                pet = self._pet_display_name(attacker) or normalize_mob(attacker)
                is_charmed = self.is_charmed_pet(attacker)
                self.pet_last_seen[pet] = ts
                self.pet_damage += dmg
                self._deal(ts, g["target"], dmg, f"Pet ({pet})",
                           actor=f"{pet} (pet)",
                           actor_role="charmed" if is_charmed else "summoned",
                           category="pet", actor_source=actor_source,
                           actor_source_category=actor_category)
                if is_charmed:
                    self.charmed_pet_damage += dmg
                    if self.fight:
                        self.fight.charmed_pet_damage += dmg
                else:
                    self.summoned_pet_damage += dmg
                    if self.fight:
                        self.fight.summoned_pet_damage += dmg
                if (is_charmed
                        and pet_identity(attacker) == pet_identity(g["target"])):
                    self.ambiguous_pet_damage += dmg
                    if self.fight:
                        self.fight.ambiguous_pet_damage += dmg
                self.melee_hits += 0  # pet swings tracked via source hits
            else:
                self._observe_actor_damage(
                    ts, attacker, g["target"], dmg,
                    source=actor_source, category=actor_category)
        elif kind == "miss_third":
            attacker = (g.get("attacker") or "").strip()
            if attacker and self.is_pet(attacker):
                pet = self._pet_display_name(attacker) or normalize_mob(attacker)
                self.pet_last_seen[pet] = ts
                self._own_combat(ts)
            else:
                self._combat_signal(ts)

        return tuple(charm_break_events)

    def _register_pet(self, pet: str, ts: datetime, *, charmed: bool = False,
                      charm_spell: str = ""):
        pet = normalize_pet_name(pet)
        key = pet_identity(pet)
        if charmed:
            # One controlled creature at a time: a new charm claim invalidates
            # the previous NPC alias but leaves summoned/swarm names intact.
            for old in list(self.charmed_pet_names):
                if pet_identity(old) != key:
                    self._drop_pet(old)
        display = self._pet_display_name(pet) or pet
        self.pet_names.add(display)
        self.pet_last_seen[display] = ts
        if charmed:
            self.charmed_pet_names.add(display)
            self.charm_breaks.claim(display, ts, charm_spell)

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
        history = self.fights[-DESKTOP_FIGHT_HISTORY:] + pending
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
            "auto_attack": self.auto_attack,
            "combat_damage": closed_damage,
            "combat_seconds": closed_seconds,
            # Export attribution totals explicitly for renderer-neutral
            # consumers.  The legacy Tk UI could read these attributes from
            # ``SessionStats`` directly; the Electron worker cannot and must
            # never infer a charmed-pet split from display rows.
            "personal_damage": max(0, closed_damage - self.pet_damage),
            "pet_damage": self.pet_damage,
            "charmed_pet_damage": self.charmed_pet_damage,
            "summoned_pet_damage": self.summoned_pet_damage,
            "ambiguous_pet_damage": self.ambiguous_pet_damage,
            "best_fight": best,
            "fight": shown_fight,
            "fight_sources": ({k: {
                **dict(v),
                "category": shown_fight.source_categories.get(k, "unknown"),
            } for k, v in shown_fight.sources.items()}
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
            "fight_actor_sources": ({
                actor: {source: dict(metric)
                        for source, metric in sources.items()}
                for actor, sources in shown_fight.actor_sources.items()
            } if shown_fight else {}),
            "fight_actor_roles": (
                dict(shown_fight.actor_roles) if shown_fight else {}),
            "fight_actor_healing": (
                {k: dict(v) for k, v in shown_fight.actor_healing.items()}
                if shown_fight else {}),
            "fights": (history + ([live] if live else []))[-DESKTOP_FIGHT_HISTORY:],
            "timeline_bucket_seconds": TIMELINE_BUCKET_SECONDS,
            "damage_by_source": {k: dict(v) for k, v in self.damage_by_source.items()},
            "healing_by_source": {k: dict(v) for k, v in self.healing_by_source.items()},
            "actor_damage": {k: dict(v) for k, v in self.actor_damage.items()},
            "actor_roles": dict(self.actor_roles),
            "group_members": sorted(self.group_members, key=str.casefold),
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
            "charmed_pet_damage": self.charmed_pet_damage,
            "summoned_pet_damage": self.summoned_pet_damage,
            "ambiguous_pet_damage": self.ambiguous_pet_damage,
            "active_pets": active_pets,
            "pet_names": sorted(self.pet_names),
            "charmed_pet_names": sorted(self.charmed_pet_names),
            "summoned_pet_names": sorted(
                pet for pet in self.pet_names if not self.is_charmed_pet(pet)),
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
            "motes": list(self.motes),
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

    def recent_context(self) -> dict[str, object]:
        """Recover zone, class trio, and group roster without replaying combat.

        The normal warm start intentionally reads only thirty minutes of log
        activity. A player can remain in one zone much longer, so identity
        context gets one separate bounded reverse scan when a log is attached.
        """
        if self.path is None:
            return {
                "zone": "", "zone_observed_at": None,
                "zone_evidence": "", "composition": "",
                "group_members": [],
            }
        try:
            size = self.path.stat().st_size
            start = max(0, size - CONTEXT_BACKFILL_BYTES)
            with self.path.open("rb") as handle:
                handle.seek(start)
                data = handle.read()
        except OSError:
            return {
                "zone": "", "zone_observed_at": None,
                "zone_evidence": "", "composition": "",
                "group_members": [],
            }
        if start and b"\n" in data:
            data = data[data.index(b"\n") + 1:]
        zone = ""
        zone_observed_at = None
        zone_evidence = ""
        composition = ""
        group_members: dict[str, str] = {}
        for raw in data.splitlines():
            folded = raw.lower()
            if not any(marker in folded for marker in (
                    b"you have entered", b"active class", b"group")):
                continue
            decoded = raw.decode("latin-1", errors="replace")
            parsed = parse_line(decoded)
            if parsed is None:
                continue
            _stamp, kind, groups = parsed
            if kind == "zone":
                candidate = groups.get("zone", "")
                if is_real_zone_transition(candidate):
                    zone = candidate
                    zone_observed_at = _stamp
                    zone_evidence = (decoded.split("] ", 1)[1]
                                     if "] " in decoded else decoded)
            elif kind == "composition":
                try:
                    composition = normalize_composition(
                        groups.get("classes", ""))
                except ValueError:
                    pass
            elif kind == "group_join":
                member = (groups.get("member") or "").strip()
                if member:
                    group_members[member.casefold()] = member
            elif kind == "group_leave":
                group_members.pop(
                    (groups.get("member") or "").strip().casefold(), None)
            elif kind == "group_clear":
                group_members.clear()
        return {
            "zone": zone,
            "zone_observed_at": zone_observed_at,
            "zone_evidence": zone_evidence,
            "composition": composition,
            "group_members": sorted(group_members.values(), key=str.casefold),
        }

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
def check_alerts(kind: str, g: dict, raw_msg: str, character: str, cfg: dict,
                 charm_break_events: tuple[CharmBreakEvent, ...] = ()):
    """Return a list of (severity, text) alerts for one parsed event.
    severity: 'danger' (red), 'warn' (gold), 'info' (cyan)."""
    if not cfg.get("alerts_enabled", False):
        return []
    out = []
    if cfg.get("alert_charm_break", True):
        for event in charm_break_events:
            pet = (event.pet_name or "CHARMED PET").upper()
            out.append(("danger", f"CHARM BROKE — {pet}"))
    if kind == "tell_in" and cfg.get("alert_tells", True):
        out.append(("info", f"TELL \u2014 {g['sender']}: {g['msg'][:60]}"))
    elif kind == "summoned" and cfg.get("alert_summon", True):
        out.append(("danger", "YOU HAVE BEEN SUMMONED"))
    elif kind == "death_you" and cfg.get("alert_death", True):
        out.append(("danger", f"YOU DIED \u2014 {g.get('killer', '?')}"))
    elif (kind in ("melee_in", "nuke_in", "dot_in", "nonmelee_in")
            and cfg.get("alert_big_hit", True)):
        dmg = int(g.get("dmg", 0))
        if dmg >= int(cfg.get("big_hit_threshold", 800)):
            out.append(("warn", f"BIG HIT \u2014 {dmg}"))
    if (cfg.get("alert_name_called", True)
            and character and character != "?" and raw_msg):
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


def invalid_custom_alert_patterns(rules) -> list[str]:
    """Return each custom alert pattern that is broken regex (never matches)."""
    bad = []
    for rule in rules if isinstance(rules, list) else []:
        pattern = rule.get("pattern", "") if isinstance(rule, dict) else ""
        try:
            re.compile(pattern or "$^")
        except re.error:
            bad.append(str(pattern))
    return bad


def fight_toasts_active(cfg: dict) -> bool:
    """Fight-end toasts honor both the master switch and their own toggle."""
    return bool(cfg.get("alerts_enabled", False) and cfg.get("fight_toasts", True))


def load_config() -> dict:
    cfg = {
        "log_dir": None,
        "ui_theme": "vellum",
        "mini_mode": True,
        "opacity": 1.0,
        "ui_rendering_version": 3,
        "position": None,
        "mini_position": None,
        "panel_size": list(FULL_DEFAULT_SIZE),
        "mini_stat_index": 0,
        "locked": False,
        # Full-panel summary can collapse without changing the user's saved
        # window size, turning the reclaimed height into ledger viewport.
        "summary_collapsed": False,
        "starred": ["session_dps", "xp_hr", "hours_to_level", "kills"],
        # DPS is the only first-run Rune Seed face. Additional metrics appear
        # only after the player stars them in the expanded ledger.
        "starred_cards": list(DEFAULT_RUNE_SEED_CARDS),
        "hud_cards_version": RUNE_SEED_CONFIG_VERSION,
        # Banners are opt-in: quiet by default, one switch in Settings.
        "alerts_enabled": False,
        "alert_sound": True,
        "alert_seconds": 4,
        "big_hit_threshold": 800,
        "alert_position": None,
        # Compact banners normally follow the Rune Seed. Players can pin
        # their preferred side without giving up monitor-edge clamping.
        "mini_alert_anchor": "auto",
        "fight_toasts": True,
        # Per-trigger switches for the built-in alert banners.
        "alert_tells": True,
        "alert_summon": True,
        "alert_death": True,
        "alert_charm_break": True,
        "alert_big_hit": True,
        "alert_name_called": True,
        # Mez timers are a separate, always-honest control surface. Sound is
        # opt-in so enabling the visual does not make a previously quiet HUD
        # noisy; the warning fires once as the guaranteed-safe window closes.
        "mez_timers_enabled": True,
        "mez_timer_sound": False,
        "mez_warning_seconds": 10,
        "lull_timers_enabled": True,
        "lull_timer_sound": False,
        "lull_warning_seconds": 12,
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
        # Plane of Sky intelligence is local and opt-in. Only matching turn-in
        # names from an imported inventory output are persisted.
        "sky_intel_enabled": False,
        "sky_owned_items": [],
        "sky_inventory_path": "",
        "sky_target_reward": [],
        "split_charmed_pet_dps": False,
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
    # Gear Compare was removed. Drop its retired preferences so the next
    # normal config save also cleans up installations that tried the feature.
    for retired_key in ("compare_enabled", "compare_hotkey",
                        "compare_hotkey_customized", "compare_position"):
        cfg.pop(retired_key, None)
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
    # Rendering v3 replaces the legacy 400x480 detail panel with the wider
    # Rune Seed expansion.  Only migrate the untouched old default; a size the
    # player deliberately chose is preserved and clamped at render time.
    if rendering_version < 3:
        try:
            old_size = [int(v) for v in cfg.get("panel_size", [])[:2]]
        except (TypeError, ValueError):
            old_size = []
        if old_size == [400, 480]:
            cfg["panel_size"] = list(FULL_DEFAULT_SIZE)
    cfg["ui_rendering_version"] = 3
    # One-time seed migrations are applied exactly once so a deliberate later
    # choice is never undone: v1 added motes, v2 removed the overflowing
    # PROGRESSION default, and v3 makes DPS the sole seeded metric. The v3
    # migration changes only the exact untouched v2 wheel; custom orders and
    # selections remain intact.
    try:
        hud_cards_version = int(loaded.get("hud_cards_version", 0) or 0)
    except (TypeError, ValueError):
        hud_cards_version = 0
    loaded_starred = loaded.get("starred_cards")
    starred = cfg.get("starred_cards")
    if isinstance(starred, list) and isinstance(loaded_starred, list):
        if hud_cards_version < 1 and "motes" not in starred:
            starred.append("motes")
        if hud_cards_version < 2 and "progress" in starred:
            starred.remove("progress")
        if (hud_cards_version < RUNE_SEED_CONFIG_VERSION
                and starred == list(LEGACY_DEFAULT_RUNE_SEED_CARDS)):
            starred = list(DEFAULT_RUNE_SEED_CARDS)
            cfg["mini_stat_index"] = 0
    # The legacy strip could leave more than four flags in the config even
    # though only four were visible. Rune Seed has one honest four-item wheel,
    # so normalize old/hand-edited lists instead of hiding unreachable stars.
    cfg["starred_cards"] = rune_seed_keys(starred)
    cfg["hud_cards_version"] = RUNE_SEED_CONFIG_VERSION
    cfg["mini_alert_anchor"] = normalize_alert_anchor(
        cfg.get("mini_alert_anchor", "auto"))
    cfg["ui_theme"] = ("glass" if str(cfg.get("ui_theme", "vellum")).casefold()
                       == "glass" else "vellum")
    if not isinstance(cfg.get("sky_owned_items"), list):
        cfg["sky_owned_items"] = []
    target_reward = cfg.get("sky_target_reward")
    if not isinstance(target_reward, list) or len(target_reward) != 3:
        cfg["sky_target_reward"] = []
    # Broken custom alert regexes are skipped silently per log line; warn
    # exactly once here so the config author can find and fix them.
    bad_patterns = invalid_custom_alert_patterns(cfg.get("custom_alerts", []))
    if bad_patterns:
        preview = ", ".join(repr(p) for p in bad_patterns[:3])
        print(f"Loremaster: ignoring {len(bad_patterns)} invalid custom alert "
              f"pattern(s): {preview}")
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
            # Charmed NPC aliases never survive a restart; the same creature
            # name may be an unrelated enemy in the next session.
            "pet_names": stats.persistent_pet_names(),
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
        # Older saves only contained one-word summoned pet names because the
        # former parser could not learn multi-word charm pets at all.
        stats.pet_names.add(normalize_pet_name(str(p)))
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
# Overlay geometry & status helpers (pure where possible, for the test suite)
# ---------------------------------------------------------------------------
def desktop_bounds(root) -> tuple[int, int, int, int]:
    """Return the full virtual desktop in this process's (logical) pixels."""
    if os.name == "nt":
        try:
            import ctypes
            user32 = ctypes.windll.user32
            return (user32.GetSystemMetrics(76), user32.GetSystemMetrics(77),
                    user32.GetSystemMetrics(78), user32.GetSystemMetrics(79))
        except (AttributeError, OSError):
            pass
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def monitor_work_area(root, rect=None) -> tuple[int, int, int, int]:
    """Return the nearest monitor's taskbar-safe work area in Tk pixels."""
    fallback = desktop_bounds(root)
    if os.name != "nt":
        return fallback
    try:
        import ctypes
        from ctypes import wintypes

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG),
            ]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                ("rcWork", RECT), ("dwFlags", wintypes.DWORD),
            ]

        user32 = ctypes.windll.user32
        if rect is not None:
            x, y, width, height = (int(value) for value in rect)
            native_rect = RECT(x, y, x + max(1, width), y + max(1, height))
            user32.MonitorFromRect.argtypes = [ctypes.POINTER(RECT), wintypes.DWORD]
            user32.MonitorFromRect.restype = wintypes.HANDLE
            monitor = user32.MonitorFromRect(ctypes.byref(native_rect), 2)
        else:
            user32.GetParent.argtypes = [wintypes.HWND]
            user32.GetParent.restype = wintypes.HWND
            hwnd = int(root.winfo_id())
            parent = int(user32.GetParent(wintypes.HWND(hwnd)) or 0)
            user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
            user32.MonitorFromWindow.restype = wintypes.HANDLE
            monitor = user32.MonitorFromWindow(
                wintypes.HWND(parent or hwnd), 2)
        if not monitor:
            return fallback
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE,
                                          ctypes.POINTER(MONITORINFO)]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return fallback
        work = info.rcWork
        return (work.left, work.top, work.right - work.left,
                work.bottom - work.top)
    except (AttributeError, OSError, TypeError, ValueError):
        return fallback


def fit_panel_size_to_bounds(requested_size, scale, bounds, margin=8):
    """Fit the expanded HUD to a work area, even when scaled minima cannot."""
    try:
        requested_width, requested_height = (
            int(requested_size[0]), int(requested_size[1]))
    except (TypeError, ValueError, IndexError):
        requested_width, requested_height = FULL_DEFAULT_SIZE
    try:
        value_scale = max(1.0, min(1.40, float(scale)))
    except (TypeError, ValueError):
        value_scale = 1.0
    _vx, _vy, work_width, work_height = (int(value) for value in bounds)
    available_width = max(1, work_width - max(0, int(margin)) * 2)
    available_height = max(1, work_height - max(0, int(margin)) * 2)
    ideal_width = max(
        int(FULL_MIN_WIDTH * value_scale),
        min(FULL_MAX_WIDTH, requested_width),
    )
    ideal_height = max(
        int(FULL_MIN_HEIGHT * value_scale),
        min(FULL_MAX_HEIGHT, requested_height),
    )
    return min(ideal_width, available_width), min(ideal_height, available_height)


def adjacent_window_position(root_rect, window_size, bounds, gap=16):
    """Place a secondary surface beside its owner inside one monitor."""
    rx, ry, rw, _rh = (int(value) for value in root_rect)
    width, height = (max(1, int(value)) for value in window_size)
    bx, by, bw, bh = (int(value) for value in bounds)
    x = rx - width - int(gap)
    if x < bx:
        x = rx + rw + int(gap)
    x = max(bx + 8, min(x, bx + bw - width - 8))
    y = max(by + 8, min(ry, by + bh - height - 8))
    return x, y


def place_native_toplevel_beside(root, win) -> bool:
    """Place a realized Windows Toplevel without mixing Tk/Win32 DPI units."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class RECT(ctypes.Structure):
            _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                        ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                        ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]

        user32 = ctypes.windll.user32
        user32.GetParent.argtypes = [wintypes.HWND]
        user32.GetParent.restype = wintypes.HWND
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.MonitorFromWindow.restype = wintypes.HANDLE
        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE,
                                          ctypes.POINTER(MONITORINFO)]
        user32.GetMonitorInfoW.restype = wintypes.BOOL

        def wrapper(widget):
            child = wintypes.HWND(int(widget.winfo_id()))
            return user32.GetParent(child) or child

        root_handle, win_handle = wrapper(root), wrapper(win)
        root_native, win_native = RECT(), RECT()
        if not user32.GetWindowRect(root_handle, ctypes.byref(root_native)):
            return False
        if not user32.GetWindowRect(win_handle, ctypes.byref(win_native)):
            return False
        monitor = user32.MonitorFromWindow(root_handle, 2)
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return False
        root_rect = (root_native.left, root_native.top,
                     root_native.right - root_native.left,
                     root_native.bottom - root_native.top)
        window_size = (win_native.right - win_native.left,
                       win_native.bottom - win_native.top)
        work = info.rcWork
        bounds = (work.left, work.top, work.right - work.left,
                  work.bottom - work.top)
        x, y = adjacent_window_position(root_rect, window_size, bounds)
        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND,
                                       ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.SetWindowPos.restype = wintypes.BOOL
        return bool(user32.SetWindowPos(
            win_handle, None, x, y, 0, 0,
            0x0001 | 0x0010 | 0x0040))  # NOSIZE | NOACTIVATE | SHOWWINDOW
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def clamp_alert_position(pos, width, height, bounds, default_x, default_y):
    """Keep a remembered banner fully reachable on the virtual desktop."""
    try:
        x, y = int(pos[0]), int(pos[1])
    except (TypeError, ValueError, IndexError, KeyError):
        x, y = int(default_x), int(default_y)
    vx, vy, vw, vh = bounds
    x = max(vx, min(x, vx + max(0, vw - width)))
    y = max(vy, min(y, vy + max(0, vh - height)))
    return x, y


ALERT_ANCHORS = ("auto", "right", "left", "above", "below")


def normalize_alert_anchor(value) -> str:
    """Return a supported compact-banner anchor, safe for old configs."""
    candidate = str(value or "").strip().casefold()
    return candidate if candidate in ALERT_ANCHORS else "auto"


def alert_banner_position(root_rect, banner_size, bounds, *, mini_mode,
                          saved_position=None, stack_index=0, gap=10,
                          anchor="auto"):
    """Place compact alerts beside the Rune Seed; expanded alerts stay saved."""
    try:
        root_x, root_y, root_width, root_height = (
            int(value) for value in root_rect)
        width, height = (int(value) for value in banner_size)
        vx, vy, vw, vh = (int(value) for value in bounds)
        index = max(0, int(stack_index))
    except (TypeError, ValueError):
        return 0, 0
    anchor_mode = normalize_alert_anchor(anchor)
    resolved_anchor = anchor_mode
    if mini_mode:
        right_x = root_x + root_width + int(gap)
        left_x = root_x - width - int(gap)
        centered_x = root_x + (root_width - width) // 2
        centered_y = root_y + (root_height - height) // 2
        candidates = {
            "right": (right_x, centered_y),
            "left": (left_x, centered_y),
            "above": (centered_x, root_y - height - int(gap)),
            "below": (centered_x, root_y + root_height + int(gap)),
        }
        orders = {
            "auto": ("right", "left", "above", "below"),
            "right": ("right", "left", "above", "below"),
            "left": ("left", "right", "above", "below"),
            "above": ("above", "below", "right", "left"),
            "below": ("below", "above", "right", "left"),
        }
        order = orders[anchor_mode]
        resolved_anchor = order[0]
        default_x, default_y = candidates[resolved_anchor]
        for candidate_anchor in order:
            candidate_x, candidate_y = candidates[candidate_anchor]
            if (vx <= candidate_x and candidate_x + width <= vx + vw
                    and vy <= candidate_y
                    and candidate_y + height <= vy + vh):
                resolved_anchor = candidate_anchor
                default_x, default_y = candidate_x, candidate_y
                break
        # A legacy/expanded banner coordinate must never detach compact alerts
        # from the Rune Seed.
        position = (default_x, default_y)
    else:
        default_x = vx + max(0, (vw - width) // 2)
        default_y = vy + 64
        position = saved_position if saved_position else (default_x, default_y)
    x, y = clamp_alert_position(
        position, width, height, bounds, default_x, default_y)
    if index:
        spacing = height + 6
        below_y = y + index * spacing
        above_y = y - index * spacing
        if mini_mode and resolved_anchor == "above":
            stacked_y = above_y
        elif mini_mode and resolved_anchor == "below":
            stacked_y = below_y
        else:
            stacked_y = below_y if below_y + height <= vy + vh else above_y
        x, y = clamp_alert_position(
            (x, stacked_y), width, height, bounds, x, y)
    return x, y


def signed_window_position(x, y) -> str:
    """Tk geometry coordinates, including valid signs on negative monitors."""
    return f"{int(x):+d}{int(y):+d}"


def native_window_position_plan(rect=None, *, show=False):
    """Build one SetWindowPos operation without losing a queued Tk move.

    A withdrawn Tk window may not have applied its requested coordinates when
    Windows first creates the native wrapper. Initial mapping therefore moves
    and sizes that wrapper atomically; later z-order syncs leave its rectangle
    untouched.
    """
    flags = 0x0010  # SWP_NOACTIVATE
    if rect is None:
        if show:
            raise ValueError("an initial native show requires a rectangle")
        x = y = width = height = 0
        flags |= 0x0001 | 0x0002  # SWP_NOSIZE | SWP_NOMOVE
    else:
        try:
            x, y, width, height = (int(value) for value in rect)
        except (TypeError, ValueError):
            if show:
                raise ValueError(
                    "an initial native show requires a valid rectangle")
            x = y = width = height = 0
            flags |= 0x0001 | 0x0002
        else:
            width, height = max(1, width), max(1, height)
    if show:
        flags |= 0x0040 | 0x0020  # SWP_SHOWWINDOW | SWP_FRAMECHANGED
    return x, y, width, height, flags


def rescale_capture_anchor(cursor_x, cursor_y, physical_bounds, tk_bounds):
    """Map a DPI-aware physical cursor onto Tk's logical virtual desktop.

    Hover OCR captures run PER_MONITOR_AWARE_V2 and report physical pixels,
    while the DPI-unaware Tk process positions windows in logical pixels.
    """
    px, py, pw, ph = physical_bounds
    tx, ty, tw, th = tk_bounds
    if pw <= 0 or ph <= 0 or tw <= 0 or th <= 0:
        return int(cursor_x), int(cursor_y)
    x = tx + (int(cursor_x) - px) * tw / pw
    y = ty + (int(cursor_y) - py) * th / ph
    return int(round(x)), int(round(y))


def wiki_status_label(item) -> str:
    """Three honest freshness states: stale cache, valid cache, live fetch."""
    if item.stale:
        return f"STALE CACHE {format_cache_age(item).upper()}"
    if item.cached:
        return f"CACHED {format_cache_age(item).upper()}"
    return "LIVE"


def summary_toggle_label(collapsed: bool) -> str:
    """Compact affordance for hiding or restoring the full-panel summary."""
    return "SHOW TOP ▾" if collapsed else "TOP ▴"


def apply_summary_visibility(summary, restore, ledger, collapsed: bool) -> None:
    """Show or hide the packed summary without rebuilding or resizing UI."""
    if summary is None or restore is None or ledger is None:
        return
    if collapsed:
        if summary.winfo_manager():
            summary.pack_forget()
        if not restore.winfo_manager():
            restore.pack(fill="x", before=ledger)
    else:
        if restore.winfo_manager():
            restore.pack_forget()
        if not summary.winfo_manager():
            summary.pack(fill="x", before=ledger)


# ---------------------------------------------------------------------------
# Alert banners — frameless EQ-overlay toasts, seed-adjacent when compact
# ---------------------------------------------------------------------------
class AlertManager:
    COLORS = {
        "danger": ("#de3e48", "#160b08", "#f2762c"),
        "warn": ("#d0a254", "#181108", "#f8d68c"),
        "info": ("#7eaaf4", "#0e1118", "#7eaaf4"),
    }
    LABELS = {"danger": "DANGER", "warn": "WARNING", "info": "NOTICE"}

    def __init__(self, tk_module, root, cfg):
        self.tk = tk_module
        self.root = root
        self.cfg = cfg
        self.active = []
        self._callbacks = {}
        self.on_show = None

    def _schedule(self, win, delay, callback):
        """Track every toast callback so eviction and shutdown are clean."""
        if win not in self._callbacks:
            return None
        token = None

        def run():
            callbacks = self._callbacks.get(win)
            if callbacks is not None:
                callbacks.discard(token)
            try:
                if win.winfo_exists():
                    callback()
            except Exception:
                pass

        try:
            token = win.after(max(0, int(delay)), run)
            self._callbacks[win].add(token)
            return token
        except Exception:
            return None

    def _cancel_and_destroy(self, win):
        for token in tuple(self._callbacks.pop(win, ())):
            try:
                win.after_cancel(token)
            except Exception:
                pass
        if win in self.active:
            self.active.remove(win)
        try:
            win.destroy()
        except Exception:
            pass

    @staticmethod
    def _round_window(win, width, height, radius=11):
        """Clip a Windows toast to real rounded corners without alpha blur."""
        if os.name != "nt":
            return
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            gdi32 = ctypes.windll.gdi32
            user32.GetParent.argtypes = [wintypes.HWND]
            user32.GetParent.restype = wintypes.HWND
            user32.SetWindowRgn.argtypes = [
                wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
            user32.SetWindowRgn.restype = ctypes.c_int
            gdi32.CreateRoundRectRgn.argtypes = [
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int]
            gdi32.CreateRoundRectRgn.restype = wintypes.HRGN
            gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
            hwnd = int(win.winfo_id())
            parent = int(user32.GetParent(wintypes.HWND(hwnd)) or 0)
            handle = wintypes.HWND(parent or hwnd)
            region = gdi32.CreateRoundRectRgn(
                0, 0, int(width) + 1, int(height) + 1,
                int(radius) * 2, int(radius) * 2)
            if not region:
                return
            if not user32.SetWindowRgn(handle, region, True):
                gdi32.DeleteObject(region)
        except (AttributeError, OSError, TypeError, ValueError):
            pass

    @staticmethod
    def _set_native_topmost(win, floating, *, show=False, rect=None):
        """Set toast z-order without activation; optionally map it visibly."""
        if os.name != "nt":
            try:
                if rect is not None:
                    x, y, width, height, _flags = native_window_position_plan(
                        rect, show=show)
                    win.geometry(
                        f"{width}x{height}{signed_window_position(x, y)}")
                win.attributes("-topmost", bool(floating))
                if show:
                    win.deiconify()
                return True
            except Exception:
                return False
        try:
            import ctypes
            from ctypes import wintypes
            handle_value = int(getattr(win, "_lore_native_handle", 0) or 0)
            if not handle_value:
                return False
            user32 = ctypes.windll.user32
            user32.SetWindowPos.argtypes = [
                wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, ctypes.c_uint]
            user32.SetWindowPos.restype = wintypes.BOOL
            user32.IsWindowVisible.argtypes = [wintypes.HWND]
            user32.IsWindowVisible.restype = wintypes.BOOL
            insert_after = wintypes.HWND(-1 if floating else -2)
            x, y, width, height, flags = native_window_position_plan(
                rect, show=show)
            if not user32.SetWindowPos(
                    wintypes.HWND(handle_value), insert_after,
                    x, y, width, height, flags):
                return False
            return (not show or bool(user32.IsWindowVisible(
                wintypes.HWND(handle_value))))
        except (AttributeError, OSError, TypeError, ValueError):
            return False

    def _show_nonactivating(self, win, floating, rect=None):
        """Show an informational toast without taking keyboard focus from EQ."""
        if os.name != "nt":
            return self._set_native_topmost(
                win, floating, show=True, rect=rect)
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            user32.GetParent.argtypes = [wintypes.HWND]
            user32.GetParent.restype = wintypes.HWND
            user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.GetWindowLongW.restype = ctypes.c_long
            user32.SetWindowLongW.argtypes = [
                wintypes.HWND, ctypes.c_int, ctypes.c_long]
            user32.SetWindowLongW.restype = ctypes.c_long
            user32.SetWindowPos.argtypes = [
                wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, ctypes.c_uint]
            user32.SetWindowPos.restype = wintypes.BOOL
            hwnd = int(win.winfo_id())
            parent = int(user32.GetParent(wintypes.HWND(hwnd)) or 0)
            handle = wintypes.HWND(parent or hwnd)
            style = int(user32.GetWindowLongW(handle, -20))
            # TOOLWINDOW | TRANSPARENT | NOACTIVATE; remove APPWINDOW.
            style = (style | 0x00000080 | 0x00000020 | 0x08000000) & ~0x00040000
            user32.SetWindowLongW(handle, -20, style)
            user32.SetWindowPos(
                handle, wintypes.HWND(0), 0, 0, 0, 0,
                0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)
            win._lore_native_handle = int(handle.value or 0)
            return self._set_native_topmost(
                win, floating, show=True, rect=rect)
        except (AttributeError, OSError, TypeError, ValueError):
            return False

    def _animate_icon(self, win, icon, edge, bright, step=0):
        """One short fixed-geometry flare; never a distracting loop."""
        if self.cfg.get("reduced_motion", False) or step >= 12:
            try:
                icon.itemconfigure("alert_ring", outline=edge, width=1)
                icon.itemconfigure("alert_sweep", state="hidden")
            except Exception:
                pass
            return
        wave = math.sin(math.pi * min(1.0, step / 11))
        try:
            icon.itemconfigure(
                "alert_ring", outline=blend_hex_color(edge, bright, wave),
                width=1 + round(wave * 2))
            icon.itemconfigure(
                "alert_sweep", state="normal", outline=bright,
                start=90 - step * 30)
        except Exception:
            return
        self._schedule(
            win, 50,
            lambda: self._animate_icon(win, icon, edge, bright, step + 1))

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
            self._cancel_and_destroy(self.active[0])
        edge, body, label_color = self.COLORS.get(
            severity, self.COLORS["info"])
        win = tk.Toplevel(self.root)
        win.withdraw()
        win.overrideredirect(True)
        floating = foreground_is_everquest_or_loremaster(
            self.root.winfo_id())
        win.attributes("-topmost", floating)
        self._callbacks[win] = set()
        outer = tk.Frame(win, bg=edge, padx=1, pady=1)
        outer.pack()
        inner = tk.Frame(outer, bg=body)
        inner.pack()
        icon = tk.Canvas(
            inner, width=32, height=34, bg=body, highlightthickness=0, bd=0)
        icon.pack(side="left", padx=(9, 2), pady=5)
        icon.create_oval(
            5, 6, 27, 28, fill="", outline=edge, width=1,
            tags="alert_ring")
        icon.create_arc(
            3, 4, 29, 30, start=90, extent=76, style="arc",
            outline=label_color, width=2, tags="alert_sweep")
        icon.create_text(
            16, 17, text="!" if severity != "info" else "\u2022",
            fill=label_color, font=("Segoe UI Semibold", 13),
            tags="alert_glyph")
        copy = tk.Frame(inner, bg=body, padx=9, pady=7)
        copy.pack(side="left")
        is_charm_break = str(text_msg).upper().startswith("CHARM BROKE")
        heading = ("CHARM CONTROL LOST" if is_charm_break else
                   f"{self.LABELS.get(severity, 'NOTICE')} ALERT")
        tk.Label(
            copy, text=heading,
            fg=label_color, bg=body, font=("Georgia", 8, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(copy, text=text_msg, fg="#f1e7d4", bg=body,
                 font=("Segoe UI Semibold", 12), anchor="w").pack(fill="x")
        win.update_idletasks()
        width, height = win.winfo_width(), win.winfo_height()
        bounds = monitor_work_area(self.root, (
            self.root.winfo_x(), self.root.winfo_y(),
            self.root.winfo_width(), self.root.winfo_height()))
        ax, ay = alert_banner_position(
            (self.root.winfo_x(), self.root.winfo_y(),
             self.root.winfo_width(), self.root.winfo_height()),
            (width, height), bounds,
            mini_mode=bool(self.cfg.get("mini_mode", False)),
            saved_position=self.cfg.get("alert_position"),
            stack_index=len(self.active),
            anchor=self.cfg.get("mini_alert_anchor", "auto"),
        )
        native_rect = (ax, ay, width, height)
        win.geometry(f"{width}x{height}{signed_window_position(ax, ay)}")
        # Flush Tk's requested rectangle before realizing the native wrapper.
        # The native show below repeats the exact rectangle atomically because
        # Windows can otherwise map a withdrawn Toplevel at (0, 0).
        win.update_idletasks()
        self._round_window(win, width, height)
        # The compact seed and sound are independent fallbacks: a transient
        # native-window failure must never suppress the actual danger signal.
        if callable(self.on_show):
            try:
                self.on_show(severity, text_msg)
            except Exception:
                pass
        self._beep(severity)
        if not self._show_nonactivating(win, floating, rect=native_rect):
            self._cancel_and_destroy(win)
            return
        self.active.append(win)
        self._animate_icon(win, icon, edge, label_color)
        ttl = int(self.cfg.get("alert_seconds", 4) * 1000)
        self._schedule(win, ttl, lambda: self._dismiss(win))

    def sync_topmost(self, floating: bool) -> None:
        """Keep every live banner in the same EQ-only z-order policy."""
        for win in list(self.active):
            try:
                if win.winfo_exists():
                    self._set_native_topmost(win, floating)
                else:
                    self.active.remove(win)
            except Exception:
                if win in self.active:
                    self.active.remove(win)

    def occupied_rects(self) -> tuple[tuple[int, int, int, int], ...]:
        """Return live banner rectangles for shared overlay placement."""
        rects = []
        for win in list(self.active):
            try:
                if not win.winfo_exists() or not win.winfo_viewable():
                    continue
                win.update_idletasks()
                rects.append((
                    win.winfo_x(), win.winfo_y(),
                    win.winfo_width(), win.winfo_height(),
                ))
            except Exception:
                continue
        return tuple(rects)

    def clear(self) -> None:
        for win in list(self.active):
            self._cancel_and_destroy(win)

    def _dismiss(self, win, step=0):
        """Fade without blocking Tk's parser/UI update loop."""
        if self.cfg.get("reduced_motion", False):
            self._cancel_and_destroy(win)
            return
        try:
            if step < 8:
                self._set_native_topmost(
                    win, foreground_is_everquest_or_loremaster(
                        self.root.winfo_id()))
                win.attributes("-alpha", 1.0 - step / 8)
                self._schedule(win, 20, lambda: self._dismiss(win, step + 1))
                return
        except Exception:
            pass
        self._cancel_and_destroy(win)


def _rects_overlap(first, second, gap=0):
    ax, ay, aw, ah = first
    bx, by, bw, bh = second
    return not (
        ax + aw + gap <= bx or bx + bw + gap <= ax
        or ay + ah + gap <= by or by + bh + gap <= ay)


def mez_overlay_position(root_geometry, overlay_size, bounds, gap=10,
                         occupied_rects=()):
    """Place mez rows near the HUD without covering transient alerts."""
    try:
        root_x, root_y, root_width, root_height = (
            int(value) for value in root_geometry)
        width, height = (int(value) for value in overlay_size)
        vx, vy, vw, vh = (int(value) for value in bounds)
    except (TypeError, ValueError):
        return 0, 0
    right_x = root_x + root_width + int(gap)
    left_x = root_x - width - int(gap)
    desired_x = right_x
    if desired_x + width > vx + vw:
        desired_x = left_x
    desired_y = root_y + max(0, min(72, (root_height - height) // 2))
    base = clamp_alert_position(
        (desired_x, desired_y), width, height, bounds,
        desired_x, desired_y)
    normalized = []
    for rect in occupied_rects or ():
        try:
            normalized.append(tuple(int(value) for value in rect))
        except (TypeError, ValueError):
            continue
    overlay_rect = (base[0], base[1], width, height)
    if not any(_rects_overlap(overlay_rect, rect, gap=4)
               for rect in normalized):
        return base

    same_side = base[0]
    opposite = left_x if desired_x == right_x else right_x
    blockers = [rect for rect in normalized
                if _rects_overlap(overlay_rect, rect, gap=4)]
    below = max(rect[1] + rect[3] for rect in blockers) + 8
    above = min(rect[1] for rect in blockers) - height - 8
    candidates = (
        (same_side, below), (same_side, above),
        (opposite, desired_y), (opposite, below), (opposite, above),
    )
    for candidate in candidates:
        x, y = clamp_alert_position(
            candidate, width, height, bounds, candidate[0], candidate[1])
        candidate_rect = (x, y, width, height)
        if not any(_rects_overlap(candidate_rect, rect, gap=4)
                   for rect in normalized):
            return x, y
    return base


def mez_spell_label(spell_name: str, rank: int) -> str:
    """Compact spell/rank copy for one timer row."""
    roman = ("", "I", "II", "III", "IV", "V", "VI", "VII", "VIII",
             "IX", "X")
    try:
        value = max(0, int(rank))
    except (TypeError, ValueError):
        value = 0
    suffix = roman[value] if value < len(roman) else f"R{value}"
    return f"{spell_name} {suffix}".rstrip()


def mez_meter_edge(width, remaining_seconds, duration_seconds,
                   last_tick=False) -> int:
    """Return a clamped, monotonically shrinking timer-meter edge."""
    try:
        pixel_width = max(1, int(width))
        remaining = max(0.0, float(remaining_seconds))
        duration = max(0.0, float(duration_seconds))
    except (TypeError, ValueError):
        return 0
    fraction = remaining / duration if duration > 0 and not last_tick else 0.0
    return max(0, min(pixel_width, round(pixel_width * fraction)))


def mez_motion_mix(now, entered_at, urgency_changed_at, urgency,
                   last_tick=False, reduced_motion=False) -> tuple[float, float]:
    """Return bounded glow and one-shot sheen strengths for a timer row."""
    if reduced_motion:
        return 0.0, 0.0
    try:
        moment = float(now)
        landing_age = max(0.0, moment - float(entered_at))
        urgency_age = max(0.0, moment - float(urgency_changed_at))
    except (TypeError, ValueError):
        return 0.0, 0.0
    landing = max(0.0, 1.0 - landing_age / 0.36)
    transition = (max(0.0, 1.0 - urgency_age / 0.32)
                  if urgency in {"warning", "critical"} or last_tick else 0.0)
    critical = bool(last_tick or urgency == "critical")
    breath = (((math.sin(moment * math.tau / 1.45) + 1.0) / 2) * 0.18
              if critical else 0.0)
    glow = max(landing * 0.72, transition * 0.42, breath)
    return min(0.72, glow), min(1.0, max(landing, transition))


class MezTimerOverlay:
    """Persistent, non-activating mez/lull control surface."""

    MAX_ROWS = 4
    WIDTH = 306
    ROW_HEIGHT = 43

    def __init__(self, tk_module, root, cfg, theme, font_scale=1.0):
        self.tk = tk_module
        self.root = root
        self.cfg = cfg
        self.theme = theme
        self.scale = max(0.85, min(1.40, float(font_scale)))
        self.win = None
        self.header_count = None
        self.row_container = None
        self.rows = []
        self.visible = False
        self.floating = None
        self.native_handle = None
        self._animation_after = None
        self._last_rect = None
        self._rounded_size = None

    def _font(self, family, size, *styles):
        return (family, max(7, round(size * self.scale)), *styles)

    def _ensure_window(self):
        if self.win is not None:
            try:
                if self.win.winfo_exists():
                    return
            except Exception:
                pass
        tk = self.tk
        t = self.theme
        win = tk.Toplevel(self.root)
        win.withdraw()
        win.overrideredirect(True)
        win.configure(bg=t["gold"])
        win.attributes("-topmost", False)
        self.win = win
        self.visible = False

        shell = tk.Frame(win, bg=t["gold"], padx=1, pady=1)
        shell.pack(fill="both", expand=True)
        inner = tk.Frame(shell, bg=t["bg"])
        inner.pack(fill="both", expand=True)
        tk.Frame(inner, bg=t["cyan"], height=2).pack(fill="x")
        header = tk.Frame(inner, bg=t["panel"], width=round(self.WIDTH * self.scale),
                          height=round(27 * self.scale))
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(
            header, text="CONTROL  ·  MEZ + LULL", fg=t["cyan"], bg=t["panel"],
            font=self._font("Georgia", 8, "bold"), anchor="w",
        ).pack(side="left", padx=(10, 4), fill="y")
        self.header_count = tk.Label(
            header, text="", fg=t["dim"], bg=t["panel"],
            font=self._font("Segoe UI", 8), anchor="e",
        )
        self.header_count.pack(side="right", padx=(4, 10), fill="y")
        self.row_container = tk.Frame(inner, bg=t["bg"])
        self.row_container.pack(fill="both", expand=True)
        self.rows = [self._create_row() for _ in range(self.MAX_ROWS)]

    def _create_row(self):
        tk = self.tk
        t = self.theme
        width = round(self.WIDTH * self.scale)
        height = round(self.ROW_HEIGHT * self.scale)
        row = tk.Frame(self.row_container, bg=t["bg"], width=width, height=height)
        row.pack_propagate(False)
        accent = tk.Frame(row, bg=t["cyan"], width=max(2, round(2 * self.scale)))
        accent.pack(side="left", fill="y")
        copy = tk.Frame(row, bg=t["bg"])
        copy.pack(side="left", fill="both", expand=True, padx=(8, 4), pady=(4, 2))
        target = tk.Label(
            copy, text="", fg=t["parchment"], bg=t["bg"],
            font=self._font("Segoe UI Semibold", 10), anchor="w",
        )
        target.pack(fill="x")
        spell = tk.Label(
            copy, text="", fg=t["dim"], bg=t["bg"],
            font=self._font("Segoe UI", 7), anchor="w",
        )
        spell.pack(fill="x")
        timing = tk.Frame(row, bg=t["bg"], width=round(72 * self.scale))
        timing.pack(side="right", fill="y", padx=(2, 8), pady=(3, 2))
        timing.pack_propagate(False)
        countdown = tk.Label(
            timing, text="", fg=t["cyan"], bg=t["bg"],
            font=self._font("Segoe UI Semibold", 11), anchor="e",
        )
        countdown.pack(fill="x")
        phase = tk.Label(
            timing, text="SAFE", fg=t["dim"], bg=t["bg"],
            font=self._font("Georgia", 7, "bold"), anchor="e",
        )
        phase.pack(fill="x")
        meter = tk.Canvas(
            row, height=max(3, round(3 * self.scale)), bg=t["line_soft"],
            highlightthickness=0, bd=0,
        )
        meter.place(x=max(2, round(2 * self.scale)), rely=1.0,
                    relwidth=1.0, anchor="sw")
        fill = meter.create_rectangle(0, 0, 0, 3, fill=t["cyan"], outline="")
        sheen = meter.create_rectangle(
            0, 0, 0, 3, fill=t["gold_bright"], outline="", state="hidden")
        return {
            "frame": row, "accent": accent, "target": target, "spell": spell,
            "countdown": countdown, "phase": phase, "meter": meter,
            "fill": fill, "sheen": sheen, "identity": None,
            "entered_at": 0.0, "edge": 0, "color": t["cyan"],
            "urgency": "safe", "urgency_changed_at": 0.0,
            "last_tick": False, "meter_width": 1,
            "duration": 0.0, "deadline_mono": 0.0,
        }

    def _settle_rows(self):
        """Restore stable pigment and remove any cached transient sheen."""
        for widget_row in self.rows:
            try:
                color = widget_row["color"]
                widget_row["accent"].configure(bg=color)
                widget_row["countdown"].configure(fg=color)
                widget_row["meter"].itemconfigure(
                    widget_row["sheen"], state="hidden")
            except Exception:
                pass

    def _stop_animation(self, *, settle=False):
        pending = self._animation_after
        self._animation_after = None
        if pending is not None:
            try:
                self.root.after_cancel(pending)
            except Exception:
                pass
        if settle:
            self._settle_rows()

    def _start_animation(self):
        if (self.cfg.get("reduced_motion", False) or not self.visible
                or self._animation_after is not None):
            return
        self._animation_after = self.root.after(50, self._animation_frame)

    def _animation_frame(self):
        """Animate cached pigment and meter items; never relayout the window."""
        self._animation_after = None
        if (not self.visible or self.win is None
                or self.cfg.get("reduced_motion", False)):
            self._stop_animation(settle=True)
            return
        now = time.monotonic()
        needs_more = False
        for widget_row in self.rows:
            try:
                if not widget_row["frame"].winfo_manager():
                    continue
                strength, sheen_strength = mez_motion_mix(
                    now, widget_row["entered_at"],
                    widget_row["urgency_changed_at"],
                    widget_row["urgency"],
                    last_tick=widget_row["last_tick"])
                color = widget_row["color"]
                bright = blend_hex_color(
                    color, self.theme["gold_bright"], strength)
                widget_row["accent"].configure(bg=bright)
                widget_row["countdown"].configure(fg=bright)
                meter = widget_row["meter"]
                remaining = max(0.0, widget_row["deadline_mono"] - now)
                edge = mez_meter_edge(
                    widget_row["meter_width"], remaining,
                    widget_row["duration"], widget_row["last_tick"])
                if edge != widget_row["edge"]:
                    widget_row["edge"] = edge
                    meter.coords(widget_row["fill"], 0, 0, edge, 3)
                    meter.itemconfigure(
                        widget_row["fill"],
                        state="normal" if edge else "hidden")
                if edge > 3 and sheen_strength > 0.02:
                    tip = max(0, edge - max(2, round(5 * self.scale)))
                    meter.coords(widget_row["sheen"], tip, 0, edge, 3)
                    meter.itemconfigure(
                        widget_row["sheen"], fill=bright, state="normal")
                else:
                    meter.itemconfigure(widget_row["sheen"], state="hidden")
                needs_more = bool(
                    needs_more or remaining > 0.0 or sheen_strength > 0.0
                    or widget_row["last_tick"]
                    or widget_row["urgency"] == "critical")
            except Exception:
                continue
        if needs_more and self.visible:
            self._animation_after = self.root.after(50, self._animation_frame)

    def _apply_nonactivating_style(self):
        """Keep the persistent timer from stealing clicks or keyboard focus."""
        if self.win is None:
            return False
        if os.name != "nt":
            return True
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            user32.GetParent.argtypes = [wintypes.HWND]
            user32.GetParent.restype = wintypes.HWND
            user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.GetWindowLongW.restype = ctypes.c_long
            user32.SetWindowLongW.argtypes = [
                wintypes.HWND, ctypes.c_int, ctypes.c_long]
            user32.SetWindowLongW.restype = ctypes.c_long
            user32.SetWindowPos.argtypes = [
                wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, ctypes.c_uint]
            user32.SetWindowPos.restype = wintypes.BOOL
            hwnd = int(self.win.winfo_id())
            parent = int(user32.GetParent(wintypes.HWND(hwnd)) or 0)
            handle = wintypes.HWND(parent or hwnd)
            style = int(user32.GetWindowLongW(handle, -20))
            # TOOLWINDOW | TRANSPARENT | NOACTIVATE; remove APPWINDOW.
            style = (style | 0x00000080 | 0x00000020 | 0x08000000) & ~0x00040000
            user32.SetWindowLongW(handle, -20, style)
            # SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE |
            # SWP_FRAMECHANGED makes the extended style effective while the
            # Tk toplevel is still withdrawn.
            user32.SetWindowPos(
                handle, wintypes.HWND(0), 0, 0, 0, 0,
                0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)
            self.native_handle = int(handle.value or 0)
            return bool(self.native_handle)
        except (AttributeError, OSError, TypeError, ValueError):
            self.native_handle = None
            return False

    def _place_nonactivating(self, rect, *, show=False):
        """Move/size—and optionally reveal—the timer in one native call."""
        if self.win is None:
            return False
        if os.name != "nt":
            try:
                x, y, width, height, _flags = native_window_position_plan(
                    rect, show=show)
                self.win.geometry(
                    f"{width}x{height}{signed_window_position(x, y)}")
                if show:
                    self.win.deiconify()
                return True
            except Exception:
                return False
        try:
            import ctypes
            from ctypes import wintypes
            if not self.native_handle:
                return False
            user32 = ctypes.windll.user32
            user32.SetWindowPos.argtypes = [
                wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, ctypes.c_uint]
            user32.SetWindowPos.restype = wintypes.BOOL
            user32.IsWindowVisible.argtypes = [wintypes.HWND]
            user32.IsWindowVisible.restype = wintypes.BOOL
            x, y, width, height, flags = native_window_position_plan(
                rect, show=show)
            flags |= 0x0004  # SWP_NOZORDER
            if not user32.SetWindowPos(
                    wintypes.HWND(self.native_handle), wintypes.HWND(0),
                    x, y, width, height, flags):
                return False
            return (not show or bool(user32.IsWindowVisible(
                wintypes.HWND(self.native_handle))))
        except (AttributeError, OSError, TypeError, ValueError):
            return False

    def _show_nonactivating(self, rect):
        """Show the final timer rectangle without focus or an origin flash."""
        if self.win is None:
            return False
        self.win.update_idletasks()
        if not self._apply_nonactivating_style():
            return False
        try:
            _x, _y, width, height = (int(value) for value in rect)
            if self._rounded_size != (width, height):
                AlertManager._round_window(
                    self.win, width, height, radius=10)
                self._rounded_size = (width, height)
        except (TypeError, ValueError):
            return False
        return self._place_nonactivating(rect, show=True)

    def _color(self, row):
        t = self.theme
        if row.timer_state == "failed":
            return t["ember"]
        if row.timer_state in {"ambiguous", "unconfirmed"}:
            return t["gold_bright"]
        if row.last_tick or row.urgency == "critical":
            return t["ember"]
        if row.urgency == "warning":
            return t["gold_bright"]
        return t["cyan"]

    def render(self, snapshot, *, enabled=True, hud_visible=True,
               occupied_rects=()):
        if not enabled or not hud_visible or not snapshot.rows:
            self.hide()
            return
        self._ensure_window()
        t = self.theme
        visible_rows = tuple(snapshot.rows[:self.MAX_ROWS])
        total_copy = f"{snapshot.active_count} TRACKED"
        if snapshot.notice_count:
            total_copy += f"  ·  {snapshot.notice_count} HONEST UNKNOWN"
        if snapshot.hidden_rows:
            total_copy += f"  ·  +{snapshot.hidden_rows}"
        self.header_count.configure(text=total_copy)
        now_mono = time.monotonic()
        pending_meter_rows = []
        for index, widget_row in enumerate(self.rows):
            if index >= len(visible_rows):
                if widget_row["frame"].winfo_manager():
                    widget_row["frame"].pack_forget()
                continue
            if not widget_row["frame"].winfo_manager():
                widget_row["frame"].pack(fill="x")
            row = visible_rows[index]
            color = self._color(row)
            identity = (
                row.target_name.casefold(), row.spell_name.casefold(),
                row.rank, row.landed_at, row.count, row.control_kind,
                row.timer_state, row.confidence, row.ambiguity)
            if identity != widget_row["identity"]:
                widget_row["identity"] = identity
                widget_row["entered_at"] = now_mono
                widget_row["urgency_changed_at"] = now_mono
            if (row.urgency != widget_row["urgency"]
                    or bool(row.last_tick) != widget_row["last_tick"]):
                widget_row["urgency_changed_at"] = now_mono
            widget_row["color"] = color
            widget_row["urgency"] = row.urgency
            widget_row["last_tick"] = row.last_tick
            widget_row["duration"] = max(0.0, float(row.duration_seconds))
            widget_row["deadline_mono"] = (
                now_mono + max(0.0, float(row.safe_remaining_seconds)))
            target = row.target_name
            if row.timer_state != "active":
                target = f"?  {target}"
            elif row.count > 1:
                target += f"  ×{row.count} · EARLIEST"
            if len(target) > 34:
                target = target[:33].rstrip() + "…"
            widget_row["target"].configure(text=target)
            spell_copy = (
                f"{row.control_kind.upper()} · "
                f"{mez_spell_label(row.spell_name, row.rank)}"
            )
            if row.timer_state == "active":
                if row.confidence not in {"confirmed", "exact"}:
                    spell_copy += f" · {row.confidence.upper()}"
                elif row.control_kind == "lull":
                    spell_copy += " · EXACT"
            elif row.ambiguity:
                spell_copy += f" · {row.ambiguity}"
            if len(spell_copy) > 62:
                spell_copy = spell_copy[:61].rstrip() + "…"
            widget_row["spell"].configure(text=spell_copy)
            if row.timer_state == "active":
                widget_row["countdown"].configure(
                    text=format_mez_remaining(
                        row.safe_remaining_seconds, last_tick=row.last_tick),
                    fg=color,
                )
                if row.last_tick:
                    phase_copy = "!! LAST TICK"
                elif row.urgency == "critical":
                    phase_copy = "!! CRITICAL"
                elif row.urgency == "warning":
                    phase_copy = "! WARNING"
                else:
                    phase_copy = "✓ SAFE"
                widget_row["phase"].configure(
                    text=phase_copy,
                    fg=color if row.urgency != "safe" or row.last_tick
                    else t["dim"],
                )
                meter = widget_row["meter"]
                if not meter.winfo_manager():
                    meter.place(x=max(2, round(2 * self.scale)), rely=1.0,
                                relwidth=1.0, anchor="sw")
                pending_meter_rows.append((widget_row, row, color))
            else:
                widget_row["countdown"].configure(text="—", fg=color)
                widget_row["phase"].configure(
                    text=row.timer_state.upper(), fg=color)
                widget_row["meter"].place_forget()
            widget_row["accent"].configure(bg=color)

        # One layout pass establishes every packed row and meter width. The
        # former per-row flushes were the timer overlay's largest jitter cost.
        self.win.update_idletasks()
        for widget_row, row, color in pending_meter_rows:
            meter = widget_row["meter"]
            meter_width = max(1, meter.winfo_width())
            widget_row["meter_width"] = meter_width
            edge = mez_meter_edge(
                meter_width, row.safe_remaining_seconds,
                row.duration_seconds, row.last_tick)
            widget_row["edge"] = edge
            meter.coords(widget_row["fill"], 0, 0, edge, 3)
            meter.itemconfigure(
                widget_row["fill"], fill=color,
                state="normal" if edge else "hidden")
        width, height = self.win.winfo_width(), self.win.winfo_height()
        x, y = mez_overlay_position(
            (self.root.winfo_x(), self.root.winfo_y(),
             self.root.winfo_width(), self.root.winfo_height()),
            (width, height), monitor_work_area(self.root, (
                self.root.winfo_x(), self.root.winfo_y(),
                self.root.winfo_width(), self.root.winfo_height())),
            occupied_rects=occupied_rects,
        )
        rect = (x, y, width, height)
        if not self.visible:
            if self._show_nonactivating(rect):
                self.visible = True
                self._last_rect = rect
            else:
                self.hide()
                return
        elif rect != self._last_rect:
            if self._place_nonactivating(rect):
                self._last_rect = rect
                if self._rounded_size != (width, height):
                    AlertManager._round_window(
                        self.win, width, height, radius=10)
                    self._rounded_size = (width, height)
        if self.cfg.get("reduced_motion", False):
            self._stop_animation(settle=True)
            return
        self._start_animation()

    def warning_sound(self, events, *, enabled=None):
        if enabled is None:
            enabled = self.cfg.get("mez_timer_sound", False)
        if not events or not enabled:
            return
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            try:
                self.root.bell()
            except Exception:
                pass

    def sync_topmost(self, floating):
        if self.win is None:
            return
        floating = bool(floating)
        if self.floating == floating:
            return
        try:
            if os.name == "nt":
                if not self._apply_nonactivating_style():
                    self.hide()
                    return
                import ctypes
                from ctypes import wintypes
                user32 = ctypes.windll.user32
                insert_after = wintypes.HWND(-1 if floating else -2)
                # SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
                user32.SetWindowPos(
                    wintypes.HWND(self.native_handle), insert_after,
                    0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
            else:
                self.win.attributes("-topmost", floating)
            self.floating = floating
        except Exception:
            pass

    def hide(self):
        self._stop_animation(settle=True)
        if self.win is not None and self.visible:
            try:
                self.win.withdraw()
            except Exception:
                pass
        self.visible = False
        self._last_rect = None
        self.floating = None

    def destroy(self):
        self._stop_animation(settle=True)
        if self.win is not None:
            try:
                self.win.destroy()
            except Exception:
                pass
        self.win = None
        self.rows = []
        self.visible = False
        self.floating = None
        self.native_handle = None
        self._last_rect = None
        self._rounded_size = None


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
    mez_tracker = MezTracker()
    lull_tracker = LullTracker()
    demo = DemoFeed() if args.demo else None
    if demo:
        stats.character = "Spin"
        watcher.character = "Spin"
        stats.set_composition(configured_composition(cfg, "Spin") or
                              "WAR / BRD / DRU", source="demo")

    T = theme_palette(cfg.get("ui_theme", "vellum"),
                      cfg.get("high_contrast", False))
    try:
        sky_catalog = load_bundled_catalog(bundled_resource_path())
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        sky_catalog = None
    root = tk.Tk()
    root.title("Loremaster")
    root.configure(bg=T["bg"])
    root.overrideredirect(not args.windowed)
    root.attributes("-topmost", False)
    try:
        opacity = max(0.75, min(1.0, float(cfg.get("opacity", 1.0))))
        if opacity < 0.999:
            root.attributes("-alpha", opacity)
    except (tk.TclError, TypeError, ValueError):
        pass

    # Load the generated SpinUI cog once and retain a root-owned reference.
    # Rebuilt compact/full canvases reuse this exact 32 px RGBA asset, avoiding
    # runtime resampling and Tk image garbage collection.
    brand_images = {}
    try:
        brand_images["cog"] = tk.PhotoImage(
            master=root,
            file=str(bundled_resource_path("assets", BRAND_COG_FILE)),
        )
    except (OSError, tk.TclError):
        brand_images["cog"] = None
    root._lore_brand_images = brand_images

    try:
        mini_stat_index = int(cfg.get("mini_stat_index", 0) or 0)
    except (TypeError, ValueError):
        mini_stat_index = 0
    state = {"mini": bool(cfg.get("mini_mode")), "last_save": time.time(),
             "last_render": 0.0, "next_demo": 0.0, "closing": False,
             "ingest_error": "", "ingest_error_until": 0.0,
             "mini_stat_index": mini_stat_index,
             "mini_alert": None, "mini_save_after": None,
             "seed_motion_after": None,
             "morph_after": None, "morphing": False,
             "fights_seen": 0, "expanded": {"combat"}, "scope": "fight",
             "lab_view": "overview", "compare_filter": "same",
             "lifetime_cutoff": None, "selected_fight": None,
             "summary_collapsed": bool(cfg.get("summary_collapsed", False)),
             "locked": bool(cfg.get("locked", False)), "click_through": False,
             "hidden_to_tray": False, "manual_show": False}
    alerts = AlertManager(tk, root, cfg)
    tray = WindowsTrayIcon("Loremaster")

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
    try:
        wiki_modifiers, wiki_virtual_key, wiki_canonical = parse_hotkey(
            cfg.get("wiki_hotkey", "Ctrl+Shift+E"))
    except ValueError:
        wiki_modifiers, wiki_virtual_key, wiki_canonical = parse_hotkey(
            "Ctrl+Shift+E")
        cfg["wiki_hotkey"] = wiki_canonical
    hotkey_service = WindowsHotkeyService(
        HotkeyBinding(
            HOTKEY_RECOVERY, hotkey["id"],
            0x0001 | 0x0002 | 0x4000, ord("L"), "Ctrl+Alt+L"),
        HotkeyBinding(
            HOTKEY_WIKI, hotkey["wiki_id"],
            wiki_modifiers, wiki_virtual_key, wiki_canonical),
    )

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

    def sync_hotkey_status():
        recovery_status = hotkey_service.status(HOTKEY_RECOVERY)
        wiki_status = hotkey_service.status(HOTKEY_WIKI)
        hotkey["registered"] = bool(recovery_status.registered)
        hotkey["wiki_registered"] = bool(wiki_status.registered)
        hotkey["wiki_error"] = wiki_status.error
        cfg["wiki_hotkey"] = wiki_status.binding.label

    def install_recovery_hotkey():
        hotkey_service.start(timeout=1.0)
        sync_hotkey_status()

    def install_wiki_hotkey():
        # Both bindings are owned for the lifetime of the native service. The
        # Tk thread only mirrors status and filters whether an action may run.
        sync_hotkey_status()

    def remove_wiki_hotkey():
        # Intentional no-op: unregistering on foreground changes created the
        # exact race this native owner exists to remove.
        sync_hotkey_status()

    def reinstall_wiki_hotkey(canonical=None):
        try:
            modifiers, virtual_key, label = parse_hotkey(
                canonical or cfg.get("wiki_hotkey", "Ctrl+Shift+E"))
        except ValueError as exc:
            hotkey["wiki_error"] = str(exc)
            return None
        result = hotkey_service.rebind_wiki(
            modifiers, virtual_key, label, timeout=1.0)
        sync_hotkey_status()
        if not result.success:
            hotkey["wiki_error"] = result.status.error
        return result

    def poll_recovery_hotkey():
        if state["closing"]:
            return
        try:
            sync_hotkey_status()
            for command in hotkey_service.poll(limit=8):
                if command == HOTKEY_RECOVERY and state["click_through"]:
                    toggle_click_through()
                elif (command == HOTKEY_WIKI
                      and foreground_is_everquest_or_loremaster(root.winfo_id())):
                    open_wiki_from_hotkey()
        except (AttributeError, OSError, KeyError):
            pass
        finally:
            if not state["closing"]:
                root.after(100, poll_recovery_hotkey)

    def remove_recovery_hotkey():
        hotkey_service.close(timeout=1.0)
        hotkey["registered"] = False
        hotkey["wiki_registered"] = False

    def wiki_hotkey_presentation():
        sync_hotkey_status()
        shortcut = str(cfg.get("wiki_hotkey", "Ctrl+Shift+E")).upper()
        if not cfg.get("wiki_enabled", True):
            return shortcut, "DISABLED", T["dim"]
        if hotkey["wiki_registered"]:
            return shortcut, "READY", T["cyan"]
        return shortcut, "CONFLICT", T["hp"]

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
                       or cursor in {"hand2", "size_nw_se",
                                     "bottom_right_corner"}
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
        return desktop_bounds(root)

    def clamped_position(pos, width, height, default_x, default_y):
        """Keep a remembered overlay reachable after resolution/monitor changes."""
        try:
            x, y = int(pos[0]), int(pos[1])
        except (TypeError, ValueError, IndexError, KeyError):
            x, y = int(default_x), int(default_y)
        vx, vy, vw, vh = monitor_work_area(root, (x, y, width, height))
        x = max(vx, min(x, vx + max(0, vw - width)))
        y = max(vy, min(y, vy + max(0, vh - height)))
        return x, y

    def place_window(width, height, pos, default_x, default_y):
        x, y = clamped_position(pos, width, height, default_x, default_y)
        root.geometry(f"{width}x{height}{x:+d}{y:+d}")
        return x, y

    def set_capsule_window_region(enabled, width=0, height=0):
        """Give compact mode real rounded corners without layered alpha."""
        # The hidden --windowed QA/development mode keeps its native title bar;
        # clipping that decorated frame would hide the seed being inspected.
        if os.name != "nt" or args.windowed:
            return
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            gdi32 = ctypes.windll.gdi32
            user32.GetParent.argtypes = [wintypes.HWND]
            user32.GetParent.restype = wintypes.HWND
            user32.SetWindowRgn.argtypes = [
                wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
            user32.SetWindowRgn.restype = ctypes.c_int
            hwnd = int(root.winfo_id())
            parent = int(user32.GetParent(wintypes.HWND(hwnd)) or 0)
            handle = wintypes.HWND(parent or hwnd)
            if not enabled:
                user32.SetWindowRgn(handle, wintypes.HRGN(0), True)
                return
            gdi32.CreateRoundRectRgn.argtypes = [
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int]
            gdi32.CreateRoundRectRgn.restype = wintypes.HRGN
            gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
            region = gdi32.CreateRoundRectRgn(
                0, 0, int(width) + 1, int(height) + 1,
                max(16, round(int(height) * 0.58)),
                max(16, round(int(height) * 0.58)))
            if region and not user32.SetWindowRgn(handle, region, True):
                gdi32.DeleteObject(region)
        except (AttributeError, OSError, TypeError, ValueError):
            pass

    def target_geometry_for_mode(mini):
        """Resolve a saved, desktop-clamped geometry without showing it."""
        scale = max(1.0, font_scale)
        if mini:
            width = int(round(RUNE_SEED_WIDTH * scale)) + 2
            height = int(round(RUNE_SEED_HEIGHT * scale)) + 2
            default_x = max(8, root.winfo_screenwidth() - width - 12)
            default_y = max(8, root.winfo_screenheight() - height - 284)
            pos = cfg.get("mini_position")
        else:
            panel_size = cfg.get("panel_size") or list(FULL_DEFAULT_SIZE)
            try:
                requested_width = int(panel_size[0])
                requested_height = int(panel_size[1])
            except (TypeError, ValueError, IndexError):
                requested_width, requested_height = FULL_DEFAULT_SIZE
            raw_pos = cfg.get("position")
            try:
                probe_x, probe_y = int(raw_pos[0]), int(raw_pos[1])
            except (TypeError, ValueError, IndexError):
                probe_x = max(8, root.winfo_screenwidth() - requested_width - 24)
                probe_y = max(8, root.winfo_screenheight() - requested_height - 300)
            work_area = monitor_work_area(
                root, (probe_x, probe_y, requested_width, requested_height))
            width, height = fit_panel_size_to_bounds(
                (requested_width, requested_height), scale, work_area)
            work_x, work_y, work_width, work_height = work_area
            default_x = work_x + max(8, work_width - width - 24)
            default_y = work_y + max(8, work_height - height - 300)
            pos = cfg.get("position")
        x, y = clamped_position(pos, width, height, default_x, default_y)
        return width, height, x, y

    def animate_geometry_transition(start, target, on_complete=None,
                                    on_frame=None):
        """Time-sample a smooth HUD morph without accumulating callback lag."""
        pending = state.get("morph_after")
        if pending is not None:
            try:
                root.after_cancel(pending)
            except tk.TclError:
                pass
        state["morph_after"] = None
        first = geometry_morph_at(start, target, 0.0)
        last = geometry_morph_at(start, target, 1.0)
        if (cfg.get("reduced_motion", False) or not first
                or first == last):
            width, height, x, y = last or target
            root.geometry(f"{width}x{height}{x:+d}{y:+d}")
            if callable(on_frame):
                on_frame(1.0, (width, height, x, y))
            if on_complete is not None:
                on_complete()
            return
        started = time.monotonic()
        duration = max(0.001, HUD_MORPH_MS / 1000.0)
        last_rect = None

        def show_frame():
            nonlocal last_rect
            if state["closing"]:
                return
            frame_started = time.monotonic()
            progress = min(1.0, (frame_started - started) / duration)
            rect = geometry_morph_at(start, target, progress)
            if rect and rect != last_rect:
                width, height, x, y = rect
                root.geometry(f"{width}x{height}{x:+d}{y:+d}")
                last_rect = rect
            if callable(on_frame):
                on_frame(progress, rect)
            if progress < 1.0:
                spent_ms = round((time.monotonic() - frame_started) * 1000)
                delay = max(1, HUD_MORPH_FRAME_MS - spent_ms)
                state["morph_after"] = root.after(delay, show_frame)
            else:
                state["morph_after"] = None
                if on_complete is not None:
                    # Let the target rectangle paint once before constructing
                    # the detail tree; this removes the old end-of-morph hitch.
                    root.after_idle(on_complete)

        show_frame()

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
        minimum_width = int(FULL_MIN_WIDTH * max(1.0, font_scale))
        minimum_height = int(FULL_MIN_HEIGHT * max(1.0, font_scale))
        bounds = monitor_work_area(root, (
            root.winfo_x(), root.winfo_y(), root.winfo_width(),
            root.winfo_height()))
        vx, vy, vw, vh = bounds
        available_width = max(1, vx + vw - root.winfo_x() - 8)
        available_height = max(1, vy + vh - root.winfo_y() - 8)
        maximum_width = min(FULL_MAX_WIDTH, available_width)
        maximum_height = min(FULL_MAX_HEIGHT, available_height)
        effective_min_width = min(minimum_width, maximum_width)
        effective_min_height = min(minimum_height, maximum_height)
        width = max(effective_min_width, min(
            maximum_width, resize["w"] + e.x_root - resize["x"]))
        height = max(effective_min_height, min(
            maximum_height, resize["h"] + e.y_root - resize["y"]))
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
    mez_overlay = MezTimerOverlay(tk, root, cfg, T, font_scale)

    def hide_child_overlay_on_unmap(event):
        if event.widget is root:
            mez_overlay.hide()

    root.bind("<Unmap>", hide_child_overlay_on_unmap, add="+")

    def fs(size):
        return max(8, int(round(size * font_scale)))

    FONT = ("Segoe UI", fs(11))
    FONT_S = ("Segoe UI", fs(9))
    FONT_B = ("Segoe UI Semibold", fs(11))
    FONT_BIG = ("Segoe UI Semibold", fs(19))
    FONT_MED = ("Segoe UI Semibold", fs(13))
    FONT_HERO = ("Segoe UI Semibold", fs(30))
    FONT_METRIC = ("Segoe UI Semibold", fs(15))
    FONT_SEED = ("Segoe UI Semibold", fs(14))
    FONT_SEED_LABEL = (
        "Segoe UI Semibold", max(7, int(round(7 * font_scale))))
    FONT_TITLE = ("Georgia", fs(11), "bold")
    FONT_RUNE = ("Georgia", fs(8), "bold")
    FONT_RUNE_S = ("Georgia", fs(7), "bold")

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

    def _wiki_cursor_position(width=392, height=560, anchor=None):
        if anchor is not None:
            px, py = int(anchor[0]), int(anchor[1])
        else:
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

    def _wiki_render_sky_context(item_name) -> bool:
        """Append deterministic local quest context even if the wiki misses."""
        if (not cfg.get("sky_intel_enabled", False) or sky_catalog is None):
            return False
        matches = sky_catalog.item_matches(item_name)
        if not matches:
            return False
        _wiki_text("\nPLANE OF SKY JOURNEY\n", tag="heading")
        for row in matches[:8]:
            _wiki_text(f"  • {row.reward} ({row.class_name})\n", tag="bullet")
            _wiki_text(f"    Farm: {row.source}\n", tag="zone")
            _wiki_text(f"    Turn in to: {row.npc}\n", tag="muted")
        return True

    def _wiki_render_prompt(message=None):
        _wiki_clear()
        _wiki_text("ITEM LORE AT A GLANCE\n", tag="hero")
        shortcut = str(cfg.get("wiki_hotkey", "Ctrl+Shift+E"))
        _wiki_text((message or
                    f"Hover an EQ item and press {shortcut}. A copied item "
                    "link or typed search remains available.\n\n"),
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
        _wiki_render_sky_context(item.title)
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
        if item.stale:
            state_color = T["ember"]
        elif item.cached:
            state_color = T["dim"]
        else:
            state_color = T["green"]
        wiki_ui["status"].configure(
            text=f"EQL WIKI  •  {wiki_status_label(item)}", fg=state_color)
        wiki_ui["open"].configure(state="normal", fg=T["cyan"])

    def _wiki_render_error(error, query):
        wiki_ui["item"] = None
        _wiki_clear()
        local_match = False
        if isinstance(error, WikiNotFoundError):
            _wiki_text("NO EXACT MATCH\n", tag="error")
            _wiki_text(f'No exact item page was found for "{query}".\n\n', tag="body")
            local_match = _wiki_render_sky_context(query)
            if error.suggestions:
                _wiki_text("POSSIBLE PAGES\n", tag="heading")
                for suggestion in error.suggestions:
                    _wiki_text("  • " + suggestion + "\n", tag="bullet")
                _wiki_text("\nType a suggested title above and press Enter.\n", tag="muted")
            status = ("LOCAL SKY MATCH  •  WIKI MISS" if local_match else
                      "EQL WIKI  •  NO EXACT MATCH")
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
        wiki_ui["status"].configure(text=status,
                                    fg=T["green"] if local_match else T["hp"])
        wiki_ui["open"].configure(state="disabled", fg=T["line"])

    def wiki_lookup(query=None, source="search"):
        if not cfg.get("wiki_enabled", True):
            open_settings()
            return
        if query is None and wiki_ui.get("entry"):
            query = wiki_ui["entry"].get()
        query = normalize_item_name(query or "")
        if not query:
            shortcut = str(cfg.get("wiki_hotkey", "Ctrl+Shift+E"))
            _wiki_render_prompt(
                f"Type an item name above, or hover it and press {shortcut}.\n\n")
            # Never yank keyboard focus away from a foreground EverQuest.
            if not _wiki_eq_is_foreground():
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
        # Closing the lens also dismisses the active intent. A late OCR/wiki
        # result must never reopen a window the player deliberately closed.
        wiki_ui["ocr_request_id"] = 0
        wiki_ui["request_id"] = 0
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

        head = tk.Frame(shell, bg=T["panel"], cursor="fleur")
        head.pack(fill="x")
        tk.Frame(head, bg=T["cyan"], width=3).pack(side="left", fill="y")
        lens_title = L(head, "LORE LENS", fg=T["gold_bright"], font=FONT_TITLE,
                       bg=T["panel"], cursor="fleur")
        lens_title.pack(side="left", padx=9, pady=7)

        wiki_drag = {"x": 0, "y": 0}

        def start_wiki_drag(event):
            wiki_drag["x"] = event.x_root - win.winfo_x()
            wiki_drag["y"] = event.y_root - win.winfo_y()
            wiki_drag["origin"] = (win.winfo_x(), win.winfo_y())

        def move_wiki(event):
            width, height = win.winfo_width(), win.winfo_height()
            desired_x = event.x_root - wiki_drag["x"]
            desired_y = event.y_root - wiki_drag["y"]
            x, y = clamped_position(
                [desired_x, desired_y], width, height, desired_x, desired_y)
            win.geometry(f"{x:+d}{y:+d}")

        def end_wiki_drag(_event):
            # A plain click on the header is not a move: pinning the window
            # then would silently disable follow-the-cursor placement with no
            # way back. Only an actual drag saves a pinned position.
            if wiki_drag.get("origin") == (win.winfo_x(), win.winfo_y()):
                return
            x, y = clamped_position(
                [win.winfo_x(), win.winfo_y()], win.winfo_width(),
                win.winfo_height(), win.winfo_x(), win.winfo_y())
            cfg["wiki_position"] = [x, y]
            save_config(cfg)

        for drag_target in (head, lens_title):
            drag_target.bind("<Button-1>", start_wiki_drag)
            drag_target.bind("<B1-Motion>", move_wiki)
            drag_target.bind("<ButtonRelease-1>", end_wiki_drag)
        close = tk.Label(head, text="X", fg=T["dim"], bg=T["panel"],
                         font=FONT_B, cursor="hand2", padx=8)
        close.pack(side="right", fill="y")
        close.bind("<Button-1>", lambda _e: _wiki_close())
        hotkey_badge = tk.Label(
            head, text=str(cfg.get("wiki_hotkey", "Ctrl+Shift+E")).upper(),
            fg=T["cyan"], bg=T["panel"], font=FONT_RUNE, padx=4)
        hotkey_badge.pack(side="right", fill="y")

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
                       open=open_button, hotkey=hotkey_badge)
        _wiki_render_prompt()
        return win

    def _wiki_show_window(anchor=None):
        win = wiki_ui.get("win") or _wiki_make_window()
        badge = wiki_ui.get("hotkey")
        if badge:
            _set_text(badge, str(cfg.get("wiki_hotkey", "Ctrl+Shift+E")).upper())
        width, height = 392, 560
        # A player who dragged the lens pinned it; otherwise follow the cursor.
        pinned = cfg.get("wiki_position")
        if pinned:
            x, y = clamped_position(
                pinned, width, height,
                *_wiki_cursor_position(width, height, anchor))
        else:
            x, y = _wiki_cursor_position(width, height, anchor)
        win.geometry(f"{width}x{height}{x:+d}{y:+d}")
        win.deiconify()
        win.lift()
        return win

    def _wiki_plaintext_fallback(clipboard, message="", anchor=None):
        _wiki_show_window(anchor)
        query, _source, _auto = clipboard_lookup_plan(clipboard)
        last = query or normalize_item_name(cfg.get("wiki_last_query", ""))
        wiki_ui["entry"].delete(0, "end")
        if last:
            wiki_ui["entry"].insert(0, last)
        _wiki_render_prompt(message or (
            "No hovered item title was recognized. Review the prefilled name "
            "or type one, then press Enter.\n\n"))
        # Never yank keyboard focus away from a foreground EverQuest.
        if not _wiki_eq_is_foreground():
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
            open_settings()
            return
        sync_hotkey_status()
        if _event is not None and not hotkey["wiki_registered"]:
            _wiki_plaintext_fallback(
                "", "HOTKEY CONFLICT\n\n" + (hotkey["wiki_error"] or
                "Windows could not reserve the configured Lore Lens shortcut.")
                + "\nOpen Settings and choose another modified shortcut.\n\n")
            return
        try:
            clipboard = root.clipboard_get()
        except (tk.TclError, TypeError):
            clipboard = ""
        action, query, source, auto_lookup = hotkey_lookup_plan(
            clipboard,
            eq_foreground=_wiki_eq_is_foreground(),
            hover_scan_enabled=bool(cfg.get("wiki_hover_ocr_enabled", True)),
        )
        if action == "hover":
            # submit() captures the physical cursor synchronously, before
            # PowerShell starts and before Lore Lens can cover the tooltip.
            wiki_ui["ocr_clipboard"] = clipboard
            try:
                wiki_ui["ocr_request_id"] = hover_ocr_service.submit()
                _wiki_show_window()
                _wiki_render_prompt(
                    "READING HOVERED ITEM…\n\n"
                    "Lore Lens is recognizing the EQ tooltip and validating "
                    "the item against EQL Wiki.")
            except RuntimeError as exc:
                _wiki_plaintext_fallback(clipboard, str(exc) + "\n\n")
        elif action == "clipboard" and query and auto_lookup:
            _wiki_show_window()
            wiki_lookup(query, source)
        else:
            _wiki_plaintext_fallback(clipboard)

    def current_sky_owned_items() -> list[str]:
        owned = [str(name) for name in cfg.get("sky_owned_items", [])]
        for item_name, count in stats.loot.items():
            owned.extend([item_name] * max(0, int(count)))
        return owned

    def open_sky_planner(parent=None, initial_query="", on_enable=None):
        if sky_catalog is None:
            return
        existing = widgets.get("sky_planner_window")
        if existing:
            try:
                existing.deiconify()
                existing.lift()
                return
            except tk.TclError:
                pass
        planner = tk.Toplevel(parent or root)
        widgets["sky_planner_window"] = planner
        planner.title("Loremaster — Plane of Sky Planner")
        planner.configure(bg=T["line"])
        planner.geometry("760x590")
        planner.minsize(640, 470)
        shell = tk.Frame(planner, bg=T["bg"], padx=14, pady=12)
        shell.pack(fill="both", expand=True, padx=1, pady=1)
        L(shell, "PLANE OF SKY · TARGET FARM", fg=T["gold_bright"],
          font=FONT_TITLE).pack(anchor="w")
        L(shell, "Search a reward, turn-in, quest NPC, or drop source. Inventory "
          "imports and this session's loot stay on this PC.", fg=T["dim"],
          font=FONT_S, wraplength=700, justify="left").pack(fill="x", pady=(2, 9))
        query = tk.StringVar(value=initial_query)
        search = tk.Entry(shell, textvariable=query, bg=T["void"], fg=T["text"],
                          insertbackground=T["cyan"], relief="flat", font=FONT_B)
        search.pack(fill="x", ipady=6)
        body = tk.Frame(shell, bg=T["bg"])
        body.pack(fill="both", expand=True, pady=(9, 8))
        results = tk.Listbox(body, bg=T["panel"], fg=T["text"],
                             selectbackground=T["line"], selectforeground=T["gold_bright"],
                             relief="flat", exportselection=False, font=FONT, width=36)
        results.pack(side="left", fill="both", expand=False)
        detail = tk.Text(body, bg=T["panel"], fg=T["text"], relief="flat",
                         wrap="word", font=FONT, padx=12, pady=10,
                         insertbackground=T["cyan"], state="disabled")
        detail.pack(side="left", fill="both", expand=True, padx=(8, 0))
        result_keys: list[tuple[str, str, str]] = []

        def render_results(*_args):
            result_keys[:] = sky_catalog.search_rewards(query.get())[:300]
            results.delete(0, "end")
            for class_name, _npc, reward in result_keys:
                results.insert("end", f"{class_name.upper():<10}  {reward}")
            if result_keys:
                results.selection_set(0)
                show_selected()
            else:
                set_detail("No matching Sky reward, turn-in, NPC, or source.")

        def set_detail(value):
            detail.configure(state="normal")
            detail.delete("1.0", "end")
            detail.insert("1.0", value)
            detail.configure(state="disabled")

        def selected_key():
            selection = results.curselection()
            return result_keys[selection[0]] if selection else None

        def show_selected(_event=None):
            key = selected_key()
            if key is None:
                return
            plan = sky_catalog.plan(key, current_sky_owned_items())
            lines = [f"{plan.reward}\n{plan.class_name} · turn in to {plan.npc}\n"]
            for row in plan.required:
                have = row in plan.owned
                lines.append(f"{'✓ OWNED' if have else '○ NEED'}  {row.quest_item}\n"
                             f"         Farm: {row.source}")
            if any(row.quest_item.casefold().startswith("wind rune")
                   for row in plan.missing):
                lines.append("\nWind runes in the currency tab are not present in "
                             "EQ's inventory output; confirm those manually.")
            lines.append("\nCOMPLETE" if plan.complete else
                         f"\n{len(plan.owned)}/{len(plan.required)} turn-ins ready")
            set_detail("\n\n".join(lines))

        def set_target():
            key = selected_key()
            if key is None:
                return
            cfg["sky_target_reward"] = list(key)
            cfg["sky_intel_enabled"] = True
            if callable(on_enable):
                on_enable()
            save_config(cfg)
            show_selected()
            refresh(force_detail=True)

        planner_status = L(shell, "", fg=T["dim"], font=FONT_S,
                           wraplength=700, justify="left")

        def mark_target_on_map():
            key = selected_key()
            if key is None:
                return
            plan = sky_catalog.plan(key, current_sky_owned_items())
            source = plan.sources[0] if plan.sources else (
                plan.required[0].source if plan.required else "")
            configured_text = str(cfg.get("log_dir") or "").strip()
            if not configured_text:
                planner_status.configure(
                    text="Choose the EverQuest folder in Settings before exporting a map target.",
                    fg=T["hp"])
                return
            configured = Path(configured_text)
            if configured.name.casefold() == "logs":
                configured = configured.parent
            maps_dir = configured / "maps"
            if not maps_dir.parent.is_dir():
                planner_status.configure(
                    text="Choose the EverQuest folder in Settings before exporting a map target.",
                    fg=T["hp"])
                return
            try:
                path = write_map_marker(maps_dir, plan.reward, source)
            except (OSError, ValueError) as exc:
                planner_status.configure(text=f"Map target unavailable: {exc}", fg=T["hp"])
                return
            planner_status.configure(
                text=f"Map target written to {path.name} · Plane of Sky layer 3.",
                fg=T["green"])

        query.trace_add("write", render_results)
        results.bind("<<ListboxSelect>>", show_selected)
        actions = tk.Frame(shell, bg=T["bg"])
        actions.pack(fill="x")
        tk.Button(actions, text="SET AS JOURNEY TARGET", command=set_target,
                  bg=T["raised"], fg=T["gold_bright"], relief="flat",
                  font=FONT_B, padx=12, pady=5).pack(side="left")
        tk.Button(actions, text="MARK EQ MAP", command=mark_target_on_map,
                  bg=T["panel"], fg=T["green"], relief="flat",
                  font=FONT_S, padx=12, pady=5).pack(side="left", padx=(6, 0))
        tk.Button(actions, text="SOURCE GUIDE", command=lambda: __import__(
            "webbrowser").open(SKY_SOURCE_URL), bg=T["panel"], fg=T["cyan"],
                  relief="flat", font=FONT_S, padx=12, pady=5).pack(side="left", padx=6)
        tk.Button(actions, text="CLOSE", command=planner.destroy,
                  bg=T["panel"], fg=T["dim"], relief="flat",
                  font=FONT_S, padx=12, pady=5).pack(side="right")
        planner_status.pack(fill="x", pady=(5, 0))
        planner.protocol("WM_DELETE_WINDOW", lambda: (widgets.__setitem__(
            "sky_planner_window", None), planner.destroy()))
        search.bind("<Return>", lambda _event: render_results())
        render_results()
        search.focus_set()

    def open_settings(_event=None):
        # Settings opens beside the HUD and may occupy the timer's anchor side.
        # Keep the control stack quiet until configuration closes.
        mez_overlay.hide()
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

        settings_viewport = tk.Frame(shell, bg=T["bg"])
        settings_viewport.pack(fill="both", expand=True)
        settings_canvas = tk.Canvas(
            settings_viewport, bg=T["bg"], highlightthickness=0, bd=0)
        settings_canvas.pack(side="left", fill="both", expand=True)
        settings_scroll = tk.Scrollbar(
            settings_viewport, orient="vertical",
            command=settings_canvas.yview, bg=T["raised"],
            activebackground=T["panel"], troughcolor=T["void"],
            relief="flat", bd=0, highlightthickness=0)
        settings_canvas.configure(yscrollcommand=settings_scroll.set)
        settings_content = tk.Frame(settings_canvas, bg=T["bg"])
        settings_content_window = settings_canvas.create_window(
            (0, 0), window=settings_content, anchor="nw")

        def sync_settings_scrollregion(_event=None):
            settings_canvas.configure(scrollregion=settings_canvas.bbox("all"))

        def fit_settings_content(event):
            settings_canvas.itemconfigure(
                settings_content_window, width=max(1, event.width))

        def scroll_settings(event):
            delta = int(getattr(event, "delta", 0) or 0)
            if delta:
                settings_canvas.yview_scroll(-1 if delta > 0 else 1, "units")
            return "break"

        settings_content.bind("<Configure>", sync_settings_scrollregion)
        settings_canvas.bind("<Configure>", fit_settings_content)
        settings_canvas.bind("<MouseWheel>", scroll_settings)
        settings_content.bind("<MouseWheel>", scroll_settings)

        columns = tk.Frame(settings_content, bg=T["bg"], padx=16, pady=14)
        columns.pack(fill="both", expand=True)
        frame = tk.Frame(columns, bg=T["bg"])
        frame.pack(side="left", fill="both", expand=True)
        tk.Frame(columns, bg=T["line_soft"], width=1).pack(
            side="left", fill="y", padx=14)
        alerts_frame = tk.Frame(columns, bg=T["bg"])
        alerts_frame.pack(side="left", fill="both", expand=True)
        L(frame, "LORE LENS ITEM LOOKUP", fg=T["cyan"], font=FONT_B).pack(
            fill="x")
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
        theme_var = tk.StringVar(value=cfg.get("ui_theme", "vellum"))
        split_pet_var = tk.BooleanVar(value=bool(
            cfg.get("split_charmed_pet_dps", False)))
        sky_enabled_var = tk.BooleanVar(value=bool(
            cfg.get("sky_intel_enabled", False)))

        def check(text_value, variable, parent=None):
            c = tk.Checkbutton(parent or frame, text=text_value, variable=variable,
                               bg=T["bg"], fg=T["text"], selectcolor=T["raised"],
                               activebackground=T["bg"], activeforeground=T["gold_bright"],
                               font=FONT_S, anchor="w")
            c.pack(fill="x", pady=1)
            return c

        check("Enable Lore Lens item lookup", enabled_var)
        check("Scan hovered tooltip on hotkey (Windows OCR, on demand)", hover_ocr_var)
        check("Allow network lookups (cached pages still work when off)", network_var)

        def unpin_lore_lens():
            cfg["wiki_position"] = None
            save_config(cfg)
            status.configure(
                text="Lore Lens unpinned; it opens beside the cursor again.",
                fg=T["green"])

        tk.Button(frame, text="UNPIN LORE LENS (follow cursor)",
                  command=unpin_lore_lens, bg=T["panel"], fg=T["dim"],
                  activebackground=T["raised"], relief="flat", font=FONT_RUNE,
                  padx=10, pady=4).pack(anchor="w", pady=(6, 0))
        row = tk.Frame(frame, bg=T["bg"])
        row.pack(fill="x", pady=(8, 4))
        L(row, "EQ-only global hotkey", fg=T["gold"], font=FONT_S).pack(side="left")
        hotkey_entry = tk.Entry(row, width=16, bg=T["void"], fg=T["text"],
                                insertbackground=T["cyan"], relief="flat", font=FONT)
        hotkey_entry.pack(side="right", ipady=3)
        hotkey_entry.insert(0, cfg.get("wiki_hotkey", "Ctrl+Shift+E"))
        status = L(frame, "", fg=T["dim"], font=FONT_S, wraplength=410)
        status.pack(fill="x", pady=(0, 8))
        _shortcut, current_state, current_color = wiki_hotkey_presentation()
        if current_state == "READY":
            status.configure(
                text=f"{_shortcut} READY — owned by Loremaster's native hotkey service.",
                fg=current_color)
        elif current_state == "DISABLED":
            status.configure(text=f"{_shortcut} is reserved; Lore Lens is disabled.",
                             fg=current_color)
        else:
            status.configure(
                text="CONFLICT — " + (hotkey.get("wiki_error") or
                                      "Windows could not reserve this shortcut."),
                fg=current_color)
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

        # ---- Installation, appearance, and local journey intelligence ---
        tk.Frame(frame, bg=T["line_soft"], height=1).pack(
            fill="x", pady=(12, 10))
        L(frame, "EVERQUEST & APPEARANCE", fg=T["cyan"], font=FONT_B).pack(fill="x")
        log_path_label = L(
            frame, cfg.get("log_dir") or "Automatic: newest EverQuest log",
            fg=T["dim"], font=FONT_S, wraplength=410, justify="left")
        log_path_label.pack(fill="x", pady=(2, 5))

        def change_eq_from_settings():
            selected = choose_log_dir()
            if selected:
                log_path_label.configure(text=selected, fg=T["green"])

        tk.Button(frame, text="CHANGE EVERQUEST FOLDER", command=change_eq_from_settings,
                  bg=T["panel"], fg=T["gold_bright"], relief="flat",
                  font=FONT_RUNE, padx=10, pady=4).pack(anchor="w")
        L(frame, "LOREMASTER THEME · applies next launch", fg=T["gold"],
          font=FONT_S).pack(fill="x", pady=(9, 3))
        theme_row = tk.Frame(frame, bg=T["line_soft"], padx=1, pady=1)
        theme_row.pack(fill="x")
        for value, label in (("vellum", "VELLUM & EMBER"),
                             ("glass", "MIDNIGHT FROST GLASS")):
            tk.Radiobutton(
                theme_row, text=label, variable=theme_var, value=value,
                indicatoron=False, relief="flat", bd=0, bg=T["panel"],
                fg=T["dim"], selectcolor=T["raised"],
                activebackground=T["raised"], activeforeground=T["gold_bright"],
                font=FONT_RUNE, padx=5, pady=4).pack(
                    side="left", fill="x", expand=True, padx=(0, 1))
        check("Split self and charmed-pet DPS in combat details", split_pet_var)

        tk.Frame(frame, bg=T["line_soft"], height=1).pack(
            fill="x", pady=(12, 10))
        L(frame, "PLANE OF SKY JOURNEY INTELLIGENCE", fg=T["cyan"],
          font=FONT_B).pack(fill="x")
        L(frame, "Optional local matching for looted turn-ins, owned inventory "
          "pieces, reward completion, and the bosses or islands still needed.",
          fg=T["dim"], font=FONT_S, wraplength=410, justify="left").pack(
              fill="x", pady=(2, 6))
        check("Enable automatic Sky quest recognition", sky_enabled_var)
        sky_status = L(frame, "", fg=T["dim"], font=FONT_S, wraplength=410,
                       justify="left")

        def import_sky_inventory():
            if sky_catalog is None:
                sky_status.configure(text="Sky quest data is unavailable.", fg=T["hp"])
                return
            from tkinter import filedialog
            selected = filedialog.askopenfilename(
                parent=win, title="Import EverQuest inventory.txt",
                filetypes=(("EverQuest inventory", "*.txt"), ("Text files", "*.txt")))
            if not selected:
                return
            try:
                names = inventory_names_from_text(
                    Path(selected).read_text(encoding="utf-8", errors="replace"))
                matching = [name for name in names if sky_catalog.item_matches(name)]
            except OSError as exc:
                sky_status.configure(text=f"Could not read inventory: {exc}", fg=T["hp"])
                return
            cfg["sky_owned_items"] = matching
            cfg["sky_inventory_path"] = selected
            cfg["sky_intel_enabled"] = True
            sky_enabled_var.set(True)
            save_config(cfg)
            sky_status.configure(
                text=f"Imported {len(names)} rows · {len(matching)} Sky turn-ins found.",
                fg=T["green"])
            refresh(force_detail=True)

        sky_actions = tk.Frame(frame, bg=T["bg"])
        sky_actions.pack(fill="x", pady=(5, 0))
        tk.Button(sky_actions, text="IMPORT INVENTORY.TXT",
                  command=import_sky_inventory, bg=T["panel"], fg=T["gold_bright"],
                  relief="flat", font=FONT_RUNE, padx=8, pady=4).pack(side="left")
        tk.Button(sky_actions, text="OPEN TARGET PLANNER",
                  command=lambda: open_sky_planner(
                      win, on_enable=lambda: sky_enabled_var.set(True)),
                  bg=T["raised"], fg=T["cyan"],
                  relief="flat", font=FONT_RUNE, padx=8, pady=4).pack(side="left", padx=5)
        sky_status.pack(fill="x", pady=(4, 0))

        # ---- Crowd-control timers -------------------------------------
        tk.Frame(frame, bg=T["line_soft"], height=1).pack(
            fill="x", pady=(12, 10))
        L(frame, "CROWD CONTROL TIMERS", fg=T["cyan"], font=FONT_B).pack(
            fill="x")
        L(frame,
          "Confirmed own-cast landings only. Same-named creatures group into "
          "one conservative row; LAST TICK accounts for EQ's hidden server-tick phase.",
          fg=T["dim"], font=FONT_S, justify="left", wraplength=410).pack(
              fill="x", pady=(2, 7))
        mez_enabled_var = tk.BooleanVar(value=bool(
            cfg.get("mez_timers_enabled", True)))
        mez_sound_var = tk.BooleanVar(value=bool(
            cfg.get("mez_timer_sound", False)))
        lull_enabled_var = tk.BooleanVar(value=bool(
            cfg.get("lull_timers_enabled", True)))
        lull_sound_var = tk.BooleanVar(value=bool(
            cfg.get("lull_timer_sound", False)))
        check("Show mez timers beside the HUD", mez_enabled_var)
        check("Sound once as a mez safe window closes", mez_sound_var)
        check("Show confirmed lull timers and honest unknown results",
              lull_enabled_var)
        check("Sound once as a lull safe window closes", lull_sound_var)
        mez_warning_row = tk.Frame(frame, bg=T["bg"])
        mez_warning_row.pack(fill="x", pady=(6, 2))
        L(mez_warning_row, "Warning threshold (3-30 seconds)",
          fg=T["gold"], font=FONT_S).pack(side="left")
        mez_warning_entry = tk.Entry(
            mez_warning_row, width=8, bg=T["void"], fg=T["text"],
            insertbackground=T["cyan"], relief="flat", font=FONT)
        mez_warning_entry.pack(side="right", ipady=3)
        mez_warning_entry.insert(0, str(cfg.get("mez_warning_seconds", 10)))
        lull_warning_row = tk.Frame(frame, bg=T["bg"])
        lull_warning_row.pack(fill="x", pady=(4, 2))
        L(lull_warning_row, "Lull warning threshold (3-30 seconds)",
          fg=T["gold"], font=FONT_S).pack(side="left")
        lull_warning_entry = tk.Entry(
            lull_warning_row, width=8, bg=T["void"], fg=T["text"],
            insertbackground=T["cyan"], relief="flat", font=FONT)
        lull_warning_entry.pack(side="right", ipady=3)
        lull_warning_entry.insert(0, str(cfg.get("lull_warning_seconds", 12)))

        # ---- Alerts & notifications ----------------------------------
        L(alerts_frame, "ALERTS & NOTIFICATIONS", fg=T["cyan"],
          font=FONT_B).pack(fill="x")
        bad_patterns = invalid_custom_alert_patterns(cfg.get("custom_alerts", []))
        alerts_blurb = ("DBM-style banners driven by your own log lines. "
                        "Pick which triggers fire and how long banners stay up.")
        if bad_patterns:
            alerts_blurb += (f" NOTE: {len(bad_patterns)} custom alert "
                             "pattern(s) in the config file are invalid regex "
                             "and are ignored.")
        L(alerts_frame, alerts_blurb, fg=T["dim"], font=FONT_S, justify="left",
          wraplength=410).pack(fill="x", pady=(2, 10))

        alerts_enabled_var = tk.BooleanVar(value=bool(cfg.get("alerts_enabled", False)))
        sound_var = tk.BooleanVar(value=bool(cfg.get("alert_sound", True)))
        toast_var = tk.BooleanVar(value=bool(cfg.get("fight_toasts", True)))
        tells_var = tk.BooleanVar(value=bool(cfg.get("alert_tells", True)))
        summon_var = tk.BooleanVar(value=bool(cfg.get("alert_summon", True)))
        death_var = tk.BooleanVar(value=bool(cfg.get("alert_death", True)))
        charm_break_var = tk.BooleanVar(value=bool(
            cfg.get("alert_charm_break", True)))
        big_hit_var = tk.BooleanVar(value=bool(cfg.get("alert_big_hit", True)))
        name_called_var = tk.BooleanVar(value=bool(
            cfg.get("alert_name_called", True)))

        check("Enable alert banners (off by default)", alerts_enabled_var, alerts_frame)
        check("Play alert sound", sound_var, alerts_frame)
        check("Fight-end toast", toast_var, alerts_frame)
        check("Incoming tells", tells_var, alerts_frame)
        check("You are summoned", summon_var, alerts_frame)
        check("You die", death_var, alerts_frame)
        check("Charmed pet breaks", charm_break_var, alerts_frame)
        check("Big hits on you", big_hit_var, alerts_frame)
        check("Your name is called in chat", name_called_var, alerts_frame)

        alert_anchor_var = tk.StringVar(value=normalize_alert_anchor(
            cfg.get("mini_alert_anchor", "auto")))
        L(alerts_frame, "RUNE SEED ALERT PLACEMENT", fg=T["gold"],
          font=FONT_S).pack(fill="x", pady=(9, 3))
        anchor_row = tk.Frame(alerts_frame, bg=T["line_soft"], padx=1, pady=1)
        anchor_row.pack(fill="x")
        for anchor_value in ALERT_ANCHORS:
            tk.Radiobutton(
                anchor_row, text=anchor_value.upper(),
                variable=alert_anchor_var, value=anchor_value,
                indicatoron=False, relief="flat", bd=0,
                bg=T["panel"], fg=T["dim"], selectcolor=T["raised"],
                activebackground=T["raised"],
                activeforeground=T["gold_bright"],
                font=FONT_RUNE, padx=3, pady=3,
            ).pack(side="left", fill="x", expand=True, padx=(0, 1))
        L(alerts_frame,
          "Auto chooses the clearest side. Every choice remains edge-safe.",
          fg=T["dim"], font=FONT_S).pack(fill="x", pady=(3, 0))

        threshold_row = tk.Frame(alerts_frame, bg=T["bg"])
        threshold_row.pack(fill="x", pady=(8, 2))
        L(threshold_row, "Big-hit threshold", fg=T["gold"],
          font=FONT_S).pack(side="left")
        threshold_entry = tk.Entry(threshold_row, width=8, bg=T["void"],
                                   fg=T["text"], insertbackground=T["cyan"],
                                   relief="flat", font=FONT)
        threshold_entry.pack(side="right", ipady=3)
        threshold_entry.insert(0, str(cfg.get("big_hit_threshold", 800)))

        seconds_row = tk.Frame(alerts_frame, bg=T["bg"])
        seconds_row.pack(fill="x", pady=(4, 2))
        L(seconds_row, "Banner seconds (1-15)", fg=T["gold"],
          font=FONT_S).pack(side="left")
        seconds_entry = tk.Entry(seconds_row, width=8, bg=T["void"],
                                 fg=T["text"], insertbackground=T["cyan"],
                                 relief="flat", font=FONT)
        seconds_entry.pack(side="right", ipady=3)
        seconds_entry.insert(0, str(cfg.get("alert_seconds", 4)))

        alert_status = L(alerts_frame, "", fg=T["dim"], font=FONT_S,
                         wraplength=410)
        alert_status.pack(fill="x", pady=(6, 0))

        def test_alert():
            # Preview with the on-screen sound/duration/placement choices,
            # without requiring a save first.
            previous = {"alert_sound": cfg.get("alert_sound", True),
                        "alert_seconds": cfg.get("alert_seconds", 4),
                        "mini_alert_anchor": cfg.get(
                            "mini_alert_anchor", "auto")}
            cfg["alert_sound"] = bool(sound_var.get())
            cfg["mini_alert_anchor"] = normalize_alert_anchor(
                alert_anchor_var.get())
            try:
                cfg["alert_seconds"] = max(1, min(15, int(seconds_entry.get())))
            except (TypeError, ValueError):
                pass
            try:
                alerts.show("info", "TEST ALERT — this is how alerts look")
            finally:
                cfg.update(previous)

        def reset_banner_position():
            cfg["alert_position"] = None
            save_config(cfg)
            alert_status.configure(
                text=("Expanded banner reset to top-center. Compact alerts "
                      "use the selected Rune Seed side."),
                fg=T["green"])

        alert_actions = tk.Frame(alerts_frame, bg=T["bg"])
        alert_actions.pack(fill="x", pady=(8, 0))
        tk.Button(alert_actions, text="TEST ALERT", command=test_alert,
                  bg=T["raised"], fg=T["cyan"], activebackground=T["panel"],
                  relief="flat", font=FONT_RUNE, padx=10,
                  pady=4).pack(side="left")
        tk.Button(alert_actions, text="RESET BANNER POSITION",
                  command=reset_banner_position, bg=T["panel"], fg=T["dim"],
                  activebackground=T["raised"], relief="flat", font=FONT_RUNE,
                  padx=10, pady=4).pack(side="left", padx=6)

        actions = tk.Frame(shell, bg=T["bg"])
        actions.pack(fill="x", padx=16, pady=(0, 14))

        def save_settings():
            try:
                _mods, _key, canonical = parse_hotkey(hotkey_entry.get())
                scale_value = max(0.85, min(1.40, float(scale_entry.get())))
            except (ValueError, TypeError) as exc:
                status.configure(text=str(exc), fg=T["hp"])
                return
            # Invalid alert numbers keep their prior saved values.
            try:
                threshold_value = int(str(threshold_entry.get()).strip())
                if threshold_value <= 0:
                    raise ValueError
            except (ValueError, TypeError):
                threshold_value = int(cfg.get("big_hit_threshold", 800))
            try:
                seconds_value = int(str(seconds_entry.get()).strip())
            except (ValueError, TypeError):
                seconds_value = int(cfg.get("alert_seconds", 4))
            seconds_value = max(1, min(15, seconds_value))
            try:
                mez_warning_value = int(str(mez_warning_entry.get()).strip())
            except (ValueError, TypeError):
                mez_warning_value = int(cfg.get("mez_warning_seconds", 10))
            mez_warning_value = max(3, min(30, mez_warning_value))
            try:
                lull_warning_value = int(
                    str(lull_warning_entry.get()).strip())
            except (ValueError, TypeError):
                lull_warning_value = int(
                    cfg.get("lull_warning_seconds", 12))
            lull_warning_value = max(3, min(30, lull_warning_value))
            active_before = hotkey_service.status(HOTKEY_WIKI)
            rebind = None
            if (canonical != active_before.binding.label
                    or (bool(enabled_var.get()) and not active_before.registered)):
                rebind = reinstall_wiki_hotkey(canonical)
            sync_hotkey_status()
            active_after = hotkey_service.status(HOTKEY_WIKI)
            if rebind is not None and not rebind.success:
                hotkey_entry.delete(0, "end")
                hotkey_entry.insert(0, active_after.binding.label)

            cfg.update(wiki_enabled=bool(enabled_var.get()),
                       wiki_network_enabled=bool(network_var.get()),
                       wiki_hover_ocr_enabled=bool(hover_ocr_var.get()),
                       wiki_hotkey=active_after.binding.label,
                       wiki_hotkey_customized=True,
                       high_contrast=bool(contrast_var.get()),
                       ui_theme=("glass" if theme_var.get() == "glass" else "vellum"),
                       split_charmed_pet_dps=bool(split_pet_var.get()),
                       sky_intel_enabled=bool(sky_enabled_var.get()),
                       reduced_motion=bool(motion_var.get()), font_scale=scale_value,
                       alerts_enabled=bool(alerts_enabled_var.get()),
                       alert_sound=bool(sound_var.get()),
                       fight_toasts=bool(toast_var.get()),
                       alert_tells=bool(tells_var.get()),
                       alert_summon=bool(summon_var.get()),
                       alert_death=bool(death_var.get()),
                       alert_charm_break=bool(charm_break_var.get()),
                       alert_big_hit=bool(big_hit_var.get()),
                       alert_name_called=bool(name_called_var.get()),
                       mez_timers_enabled=bool(mez_enabled_var.get()),
                       mez_timer_sound=bool(mez_sound_var.get()),
                       mez_warning_seconds=mez_warning_value,
                       lull_timers_enabled=bool(lull_enabled_var.get()),
                       lull_timer_sound=bool(lull_sound_var.get()),
                       lull_warning_seconds=lull_warning_value,
                       big_hit_threshold=threshold_value,
                       alert_seconds=seconds_value,
                       mini_alert_anchor=normalize_alert_anchor(
                           alert_anchor_var.get()))
            wiki_client.network_enabled = cfg["wiki_network_enabled"]
            save_config(cfg)
            threshold_entry.delete(0, "end")
            threshold_entry.insert(0, str(cfg["big_hit_threshold"]))
            seconds_entry.delete(0, "end")
            seconds_entry.insert(0, str(cfg["alert_seconds"]))
            mez_warning_entry.delete(0, "end")
            mez_warning_entry.insert(0, str(cfg["mez_warning_seconds"]))
            lull_warning_entry.delete(0, "end")
            lull_warning_entry.insert(0, str(cfg["lull_warning_seconds"]))
            if not (cfg["mez_timers_enabled"]
                    or cfg["lull_timers_enabled"]):
                mez_overlay.hide()
            refresh(force_detail=True)
            if cfg["wiki_enabled"] and not hotkey["wiki_registered"]:
                status.configure(text="CONFLICT — " + (hotkey["wiki_error"] or
                                 "Hotkey could not be reserved."),
                                 fg=T["hp"])
                return
            if rebind is not None and not rebind.success:
                status.configure(text="CONFLICT — " + rebind.status.error +
                                 f". Kept {active_after.binding.label} active.",
                                 fg=T["hp"])
                return
            message = (f"Saved. {active_after.binding.label} READY."
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
        win.bind("<MouseWheel>", scroll_settings)
        win.update_idletasks()
        # Use the realized HUD's native window to select the monitor. A logical
        # Tk rectangle passed to MonitorFromRect can be interpreted as physical
        # pixels on mixed-DPI desktops and select the adjacent monitor.
        work_x, work_y, work_width, work_height = monitor_work_area(root)
        # Tk's primary-screen dimensions are already in the exact coordinate
        # space used by ``geometry``. Prefer them when the HUD is visibly on
        # that screen; Win32 monitor bounds may be physical on a DPI-unaware Tk
        # process and can otherwise permit an off-screen settings window.
        primary_width = max(1, root.winfo_screenwidth())
        primary_height = max(1, root.winfo_screenheight())
        if (0 <= root.winfo_x() < primary_width
                and 0 <= root.winfo_y() < primary_height):
            work_x, work_y = 0, 0
            work_width, work_height = primary_width, primary_height
        header_height = max(1, header.winfo_reqheight())
        actions_height = max(1, actions.winfo_reqheight())
        available_content_height = max(
            1, work_height - header_height - actions_height - 20)
        content_height = min(
            max(180, settings_content.winfo_reqheight()),
            available_content_height)
        content_width = max(1, min(
            max(520, settings_content.winfo_reqwidth()), work_width - 28))
        settings_canvas.configure(width=content_width, height=content_height)
        if settings_content.winfo_reqheight() > content_height:
            settings_scroll.pack(side="right", fill="y")
        sync_settings_scrollregion()
        win.update_idletasks()
        x = root.winfo_x() - win.winfo_width() - 16
        y = root.winfo_y()
        # Reuse the work area resolved above. Re-querying with a not-yet-shown
        # Toplevel can mix Tk logical pixels with Win32 physical pixels on a
        # scaled ultrawide and leave the lower half off-screen.
        x = max(work_x + 8, min(x, work_x + work_width - win.winfo_width() - 8))
        y = max(work_y + 8, min(y, work_y + work_height - win.winfo_height() - 8))
        win.geometry(f"{x:+d}{y:+d}")
        win.deiconify()
        # Windows may realize a withdrawn override-redirect Toplevel at its
        # parent's old origin. Repeat the exact rectangle after mapping so the
        # compositor cannot discard the taskbar-safe placement request.
        win.geometry(f"{win.winfo_width()}x{win.winfo_height()}{x:+d}{y:+d}")
        win.update_idletasks()
        place_native_toplevel_beside(root, win)
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

    def _capture_anchor(capture):
        """Convert a physical OCR cursor to Tk's logical pixels when possible."""
        physical = (
            int(getattr(capture, "virtual_left", 0) or 0),
            int(getattr(capture, "virtual_top", 0) or 0),
            int(getattr(capture, "virtual_width", 0) or 0),
            int(getattr(capture, "virtual_height", 0) or 0),
        )
        if physical[2] <= 0 or physical[3] <= 0:
            # Older captures/mocks lack the DPI-aware desktop; keep raw pixels.
            return capture.cursor_x, capture.cursor_y
        return rescale_capture_anchor(
            capture.cursor_x, capture.cursor_y, physical,
            virtual_desktop_bounds())

    def poll_hover_ocr_results():
        if state["closing"]:
            return
        try:
            for result in hover_ocr_service.poll():
                if result.request_id != wiki_ui.get("ocr_request_id"):
                    continue  # a newer hotkey press owns the user's intent
                clipboard = wiki_ui.get("ocr_clipboard", "")
                anchor = None
                if result.capture is not None:
                    anchor = _capture_anchor(result.capture)
                if result.candidates:
                    _wiki_show_window(anchor)
                    wiki_lookup_candidates(result.candidates, "hover scan")
                else:
                    detail = result.error.strip() if result.error else (
                        "Windows OCR did not find a likely item title.")
                    fallback_query, fallback_source, fallback_auto = (
                        clipboard_lookup_plan(clipboard))
                    if fallback_query and fallback_auto:
                        _wiki_show_window(anchor)
                        wiki_lookup(fallback_query, fallback_source)
                    else:
                        _wiki_plaintext_fallback(
                            clipboard, detail + "\n\n", anchor)
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
        ("motes", "MOTES"),
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

    def rune_seed_canvas(parent, width=RUNE_SEED_WIDTH,
                         height=RUNE_SEED_HEIGHT, bg=None):
        """Create the cog-led Rune Seed; refresh only changes cached items."""
        canvas = tk.Canvas(
            parent, width=width, height=height, bg=bg or T["bg"],
            highlightthickness=0, bd=0,
        )
        scale = max(0.5, height / RUNE_SEED_HEIGHT)
        canvas.create_polygon(
            rounded_rectangle_points(
                2 * scale, 3 * scale, width - 1 * scale,
                height - 1 * scale, 13 * scale),
            smooth=True, splinesteps=24, fill=T["void"], outline="",
            tags="seed_shadow",
        )
        points = rounded_rectangle_points(
            1 * scale, 1 * scale, width - 2 * scale,
            height - 3 * scale, 13 * scale)
        canvas.create_polygon(
            points, smooth=True, splinesteps=24, fill="", outline=T["line_soft"],
            width=2, tags="seed_glow",
        )
        canvas.create_polygon(
            points, smooth=True, splinesteps=24, fill=T["panel"],
            outline=T["line_soft"], width=1.4, tags="seed_body",
        )
        canvas.create_line(
            16 * scale, 3 * scale, width - 14 * scale, 3 * scale,
            fill=T["line_soft"], width=max(1, round(scale)),
            tags="seed_highlight")
        canvas.create_line(
            13 * scale, 3 * scale, 23 * scale, 3 * scale,
            fill=T["cyan"], width=max(1, round(scale)), state="hidden",
            tags="seed_sheen")
        layout = rune_seed_content_layout(width, height)
        icon_left, icon_top, icon_right, icon_bottom = layout["icon"]
        center_x, center_y = layout["center"]
        canvas.create_oval(
            icon_left - 2, icon_top - 2, icon_right + 2, icon_bottom + 2,
            fill="", outline=T["line_soft"], width=1,
            tags="seed_icon_halo")
        cog = brand_images.get("cog")
        if cog is not None:
            canvas.create_image(
                center_x, center_y, image=cog, anchor="center",
                tags="seed_brand")
            canvas._lore_brand_image = cog
        else:
            # Graceful source-development fallback; packaged selftest requires
            # the generated asset, so release builds can never ship this path.
            canvas.create_oval(
                icon_left + 3, icon_top + 3, icon_right - 3, icon_bottom - 3,
                fill=T["void"], outline=T["gold"], width=2,
                tags="seed_brand")
            canvas.create_line(
                center_x, icon_top + 4, center_x, icon_bottom - 4,
                fill=T["gold_bright"], width=2, tags="seed_brand")
            canvas.create_line(
                icon_left + 4, center_y, icon_right - 4, center_y,
                fill=T["gold_bright"], width=2, tags="seed_brand")
        text_left, _text_top, text_right, _text_bottom = layout["text"]
        text_x = (text_left + text_right) / 2
        canvas.create_text(
            text_x, 16 * scale, text="\u2014", fill=T["text"],
            font=FONT_SEED, anchor="center", tags="seed_value",
        )
        canvas.create_text(
            text_x, 31 * scale, text=RUNE_SEED_COMBAT_LABEL,
            fill=T["gold_bright"],
            font=FONT_SEED_LABEL, anchor="center", tags="seed_label",
        )
        page_y = 41.5 * scale
        page_start = text_x - 7.5 * scale
        for index in range(MINI_MAX_CELLS):
            x = page_start + index * 5 * scale
            canvas.create_oval(
                x - scale, page_y - scale, x + scale, page_y + scale,
                fill=T["line_soft"], outline="", tags=f"seed_page_{index}")
        canvas._lore_seed_state = None
        canvas._lore_seed_scale = scale
        canvas._lore_seed_motion = {}
        return canvas

    def paint_rune_seed(canvas, value="", label="", health="READY",
                        health_color=None, alert=None, metric_index=0,
                        metric_count=1, in_combat=False):
        """Paint a cached Rune Seed state without recreating canvas items."""
        severity = (alert or {}).get("severity") if alert else ""
        if severity == "danger":
            left, right, edge, shown = T["hp"], T["ember"], T["hp"], "!"
        elif severity == "warn":
            left = right = edge = T["gold_bright"]
            shown = "!"
        elif severity == "info":
            left, right, edge, shown = T["cyan"], T["green"], T["cyan"], value
        else:
            left = T["cyan"] if health in {"LIVE", "DEMO"} else T["gold"]
            right = health_color or T["dim"]
            edge = T["line_soft"] if health != "STALE" else T["ember"]
            shown = value
        alert_text = str((alert or {}).get("text", "")).upper()
        shown_label = ("CHARM" if "CHARM BROKE" in alert_text
                       else "ALERT") if severity else label
        try:
            count = max(1, min(MINI_MAX_CELLS, int(metric_count)))
            selected = int(metric_index) % count
        except (TypeError, ValueError):
            count, selected = 1, 0
        alert_started = float((alert or {}).get("started", 0.0) or 0.0)
        draw_state = (
            shown, shown_label, health, left, right, edge, severity,
            selected, count, bool(in_combat), alert_started)
        if getattr(canvas, "_lore_seed_state", None) == draw_state:
            return
        old_state = getattr(canvas, "_lore_seed_state", None)
        canvas._lore_seed_state = draw_state
        if (old_state is None or old_state[1] != draw_state[1]
                or old_state[6] != draw_state[6]):
            canvas._lore_seed_change_at = time.monotonic()
        canvas.itemconfigure("seed_body", outline=edge)
        canvas.itemconfigure("seed_glow", outline=edge)
        canvas.itemconfigure(
            "seed_highlight", fill=blend_hex_color(T["line_soft"], edge, 0.35))
        canvas.itemconfigure("seed_icon_halo", outline=edge)
        canvas.itemconfigure(
            "seed_value", text=shown if not label else (shown or "\u2014"))
        canvas.itemconfigure(
            "seed_label", text=shown_label,
            fill=T["ember"] if severity else T["gold_bright"])
        for index in range(MINI_MAX_CELLS):
            canvas.itemconfigure(
                f"seed_page_{index}",
                fill=T["gold_bright"] if index == selected else T["line_soft"],
                state="normal" if index < count else "hidden")
        previous_motion = getattr(canvas, "_lore_seed_motion", {}) or {}
        previous_origin = float(previous_motion.get("origin", time.monotonic()))
        if severity:
            origin = float((alert or {}).get("started", time.monotonic()))
        elif in_combat and previous_motion.get("in_combat"):
            origin = previous_origin
        else:
            origin = time.monotonic()
        canvas._lore_seed_motion = {
            "left": left, "right": right, "edge": edge,
            "severity": severity, "alert": alert,
            "in_combat": bool(in_combat), "origin": origin,
        }

    def render_rune_seed_motion(canvas, now=None, settled=False):
        """Animate existing seed items only; return whether another frame helps."""
        motion = getattr(canvas, "_lore_seed_motion", {}) or {}
        now = time.monotonic() if now is None else float(now)
        scale = getattr(canvas, "_lore_seed_scale", 1.0)
        left = motion.get("left", T["gold"])
        right = motion.get("right", T["dim"])
        edge = motion.get("edge", T["line_soft"])
        alert = motion.get("alert") or {}
        severity = motion.get("severity", "")
        in_combat = bool(motion.get("in_combat"))
        origin = float(motion.get("origin", now))
        elapsed = max(0.0, now - origin)
        active_alert = bool(
            severity and now < float(alert.get("until", now + 0.1)))
        animated = not settled and not cfg.get("reduced_motion", False)
        pulse = 0.0
        if animated and active_alert and elapsed < 0.95:
            pulse = ((math.sin(elapsed * math.tau * 2.2) + 1.0) / 2
                     * (1.0 - elapsed / 0.95))
        glow = blend_hex_color(
            edge, T["gold_bright"], min(0.8, pulse))
        canvas.itemconfigure(
            "seed_glow", outline=glow, width=max(1, round(1 + pulse * 2)))
        canvas.itemconfigure(
            "seed_icon_halo", outline=blend_hex_color(
                T["line_soft"], edge, 0.42 + 0.48 * pulse),
            width=max(1, round(1 + pulse * 1.5)))
        change_at = float(getattr(canvas, "_lore_seed_change_at", 0.0))
        reveal = 1.0 if not animated else min(1.0, (now - change_at) / 0.22)
        canvas.itemconfigure(
            "seed_value", fill=blend_hex_color(T["dim"], T["text"], reveal))
        label_target = T["ember"] if severity else T["gold_bright"]
        canvas.itemconfigure(
            "seed_label", fill=blend_hex_color(T["line"], label_target, reveal))
        if animated and in_combat and not active_alert:
            width = max(1, canvas.winfo_width())
            travel = (elapsed % 2.8) / 2.8
            start = 13 * scale + travel * max(1, width - 38 * scale)
            canvas.coords(
                "seed_sheen", start, 3 * scale,
                min(width - 12 * scale, start + 10 * scale), 3 * scale)
            canvas.itemconfigure(
                "seed_sheen", state="normal",
                fill=blend_hex_color(T["line_soft"], T["cyan"], 0.42))
        else:
            canvas.itemconfigure("seed_sheen", state="hidden")
        changing = now - change_at < 0.22
        return bool(animated and (in_combat or active_alert or changing))

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
            if key == "motes":
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
        if key == "motes":
            return fmt_mote_tiers(snap["motes"])
        if key == "progress":
            if snap["xp_pct_known"]:
                # Time to level is the number that decides whether to keep the
                # camp, so the expanded ledger keeps it in the headline value.
                parts = [f"{snap['xp_pct']:.1f}% xp"]
                if stats.levelups:
                    parts.append(f"+{stats.levelups} lvl")
                if snap["hours_to_level"] is not None:
                    parts.append(f"{fmt_eta(snap['hours_to_level'])} to lvl")
                return " · ".join(parts)
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

    def rune_seed_metric(snap, key):
        """One compact, honest metric for the in-game Rune Seed."""
        if key == "combat":
            value = snap["current_dps"] if snap["in_combat"] else snap["session_dps"]
            return compact_hud_number(value), RUNE_SEED_COMBAT_LABEL
        if key == "kills":
            return compact_hud_number(snap["kills"]), "KILLS"
        if key == "loot":
            return compact_hud_number(sum(snap["loot"].values())), "SPOILS"
        if key == "money":
            copper = max(0, int(snap["copper"]))
            if copper >= 1000:
                return f"{copper / 1000:.1f}p".replace(".0p", "p"), "COIN"
            if copper >= 100:
                return f"{copper // 100}g", "COIN"
            return f"{copper // 10}s", "COIN"
        if key == "progress":
            if snap["xp_pct_known"]:
                return f"{snap['xp_pct']:.1f}%", "XP"
            return compact_hud_number(snap["xp_events"]), "XP"
        if key == "motes":
            return compact_hud_number(sum(snap["motes"])), "MOTES"
        if key == "faction":
            return compact_hud_number(len(stats.faction)), "REP"
        if key == "travels":
            return compact_hud_number(snap["deaths"]), "JOURNEY"
        return "\u2014", MINI_COMPACT_LABELS.get(key, "STAT")

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
            out.append(("line", f"{fmt_num(fight.damage)} personal damage · "
                                f"{fmt_num(fight.dps)} dps · {fight.crits} crits · "
                                f"{fight.misses} misses", ""))
            if cfg.get("split_charmed_pet_dps", False):
                self_damage = max(0, fight.damage - fight.charmed_pet_damage
                                  - fight.summoned_pet_damage)
                out.append(("head", "Attributed personal DPS", "damage · dps"))
                out.append(("row", "Self",
                            f"{fmt_num(self_damage)} · {fmt_num(self_damage / fight.seconds)}/s"))
                out.append(("row", "Charmed pet",
                            f"{fmt_num(fight.charmed_pet_damage)} · "
                            f"{fmt_num(fight.charmed_pet_damage / fight.seconds)}/s"))
                if fight.summoned_pet_damage:
                    out.append(("row", "Summoned pet",
                                f"{fmt_num(fight.summoned_pet_damage)} · "
                                f"{fmt_num(fight.summoned_pet_damage / fight.seconds)}/s"))
            if fight.ambiguous_pet_damage:
                out.append(("line", "Charm estimate: "
                            f"{fmt_num(fight.ambiguous_pet_damage)} same-name damage "
                            "included; the EQ log has no actor IDs.", ""))
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
                    out.append(("row", old.name,
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
            if cfg.get("split_charmed_pet_dps", False):
                self_damage = max(0, snap["combat_damage"] - snap["pet_damage"])
                seconds = max(1.0, snap["combat_seconds"])
                out.append(("head", "Attributed session DPS", "damage · dps"))
                out.append(("row", "Self",
                            f"{fmt_num(self_damage)} · {fmt_num(self_damage / seconds)}/s"))
                out.append(("row", "Charmed pet",
                            f"{fmt_num(snap['charmed_pet_damage'])} · "
                            f"{fmt_num(snap['charmed_pet_damage'] / seconds)}/s"))
                if snap["summoned_pet_damage"]:
                    out.append(("row", "Summoned pet",
                                f"{fmt_num(snap['summoned_pet_damage'])} · "
                                f"{fmt_num(snap['summoned_pet_damage'] / seconds)}/s"))
            if snap["ambiguous_pet_damage"]:
                out.append(("line", "Charm estimate: "
                            f"{fmt_num(snap['ambiguous_pet_damage'])} same-name damage "
                            "included; the EQ log has no actor IDs.", ""))
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
            if cfg.get("sky_intel_enabled", False) and sky_catalog is not None:
                matches = []
                for name, _count in rows:
                    for quest_row in sky_catalog.item_matches(name):
                        matches.append((name, quest_row))
                if matches:
                    out.append(("head", "Plane of Sky quest matches", "reward · class"))
                    for name, quest_row in matches[:8]:
                        out.append(("row", name,
                                    f"{quest_row.reward} · {quest_row.class_name}"))
                        out.append(("line", f"Farm: {quest_row.source} · "
                                            f"turn in to {quest_row.npc}", ""))
        elif key == "motes":
            counts = snap["motes"]
            for label, exp, count in zip(MOTE_TIER_LABELS, MOTE_TIER_EXP,
                                         counts):
                out.append(("row", f"{label} · {exp} xp", str(count)))
            out.append(("row", "Motes this session", str(sum(counts))))
            out.append(("row", "Potential earned", f"{mote_exp_total(counts)} xp"))
            if not any(counts):
                out.append(("line", "No potential motes have dropped yet. "
                                    "Counts are what this session looted, not "
                                    "what your bags hold. The ledger lists the "
                                    "grades in this order, lowest first.", ""))
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
            if cfg.get("sky_intel_enabled", False) and sky_catalog is not None:
                discoveries = []
                for item_name in reversed(list(snap["loot"])):
                    for quest_row in sky_catalog.item_matches(item_name):
                        discoveries.append((item_name, quest_row))
                if discoveries:
                    out.append(("head", "Recent Sky discoveries", "quest use"))
                    for item_name, quest_row in discoveries[:5]:
                        out.append(("row", item_name,
                                    f"{quest_row.reward} · {quest_row.class_name}"))
            if (cfg.get("sky_intel_enabled", False) and sky_catalog is not None
                    and len(cfg.get("sky_target_reward", [])) == 3):
                plan = sky_catalog.plan(tuple(cfg["sky_target_reward"]),
                                        current_sky_owned_items())
                out.append(("head", "Plane of Sky target", plan.class_name))
                out.append(("row", plan.reward,
                            "READY" if plan.complete else
                            f"{len(plan.owned)}/{len(plan.required)} turn-ins"))
                for source in plan.sources[:6]:
                    out.append(("row", "Focus", source))
                for missing in plan.missing[:6]:
                    out.append(("line", f"Need {missing.quest_item}", ""))
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
                    elif event_kind == "ally_death":
                        out.append(("row", f"{when} · {label} died", ""))
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
        if view not in {"overview", "damage", "healing", "targets", "timeline"}:
            view = "overview"
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
        starred = toggle_rune_seed_star(cfg.get("starred_cards"), key)
        cfg["starred_cards"] = starred
        state["mini_stat_index"] %= max(1, len(rune_seed_keys(starred)))
        cfg["mini_stat_index"] = state["mini_stat_index"]
        save_config(cfg)
        refresh(force_detail=True)

    def toggle_alert_flag(key):
        """Persist a quick alert toggle exposed on the expanded HUD."""
        if key not in {"alerts_enabled", "alert_charm_break",
                       "alert_tells", "alert_big_hit"}:
            return
        cfg[key] = not bool(cfg.get(key, True))
        if key == "alerts_enabled" and not cfg[key]:
            alerts.clear()
        save_config(cfg)
        refresh(force_detail=True)

    def toggle_summary():
        """Give the full-height ledger the space occupied by its top summary."""
        state["summary_collapsed"] = not state["summary_collapsed"]
        cfg["summary_collapsed"] = state["summary_collapsed"]
        summary = widgets.get("top_summary")
        restore = widgets.get("summary_restore")
        ledger = widgets.get("ledger_wrap")
        apply_summary_visibility(
            summary, restore, ledger, state["summary_collapsed"])
        toggle = widgets.get("summary_toggle")
        if toggle:
            _set_text(toggle, summary_toggle_label(False))
        restore_label = widgets.get("summary_restore_label")
        if restore_label:
            _set_text(restore_label, summary_toggle_label(True))
        save_config(cfg)
        refresh(force_detail=True)

    def stop_seed_motion():
        pending = state.get("seed_motion_after")
        state["seed_motion_after"] = None
        if pending is not None:
            try:
                root.after_cancel(pending)
            except tk.TclError:
                pass

    def seed_motion_frame():
        state["seed_motion_after"] = None
        if (state["closing"] or not state["mini"] or state.get("morphing")
                or cfg.get("reduced_motion", False)):
            return
        seed = widgets.get("mini_seed")
        try:
            if seed and seed.winfo_exists() and render_rune_seed_motion(seed):
                state["seed_motion_after"] = root.after(80, seed_motion_frame)
        except tk.TclError:
            state["seed_motion_after"] = None

    def ensure_seed_motion():
        seed = widgets.get("mini_seed")
        if not seed:
            return
        if cfg.get("reduced_motion", False):
            stop_seed_motion()
            try:
                render_rune_seed_motion(seed, settled=True)
            except tk.TclError:
                pass
            return
        if state.get("seed_motion_after") is None:
            try:
                if render_rune_seed_motion(seed):
                    state["seed_motion_after"] = root.after(
                        80, seed_motion_frame)
            except tk.TclError:
                pass

    def build_full():
        stop_seed_motion()
        set_capsule_window_region(False)
        clear_scroll_bindings()
        for w in body.winfo_children():
            w.destroy()
        for key in ("composition", "compare_nav", "lab_compare",
                    "compare_same", "compare_other", "compare_all"):
            widgets.pop(key, None)
        card_widgets.clear()
        head = tk.Frame(body, bg=T["raised"])
        head.pack(fill="x")
        logo = tk.Canvas(
            head, width=38, height=38, bg=T["raised"],
            highlightthickness=0, bd=0)
        logo.pack(side="left", padx=(12, 8), pady=7)
        cog = brand_images.get("cog")
        if cog is not None:
            logo.create_image(19, 19, image=cog, anchor="center")
            logo._lore_brand_image = cog
        else:
            logo.create_oval(4, 4, 34, 34, fill=T["void"],
                             outline=T["gold"], width=2)
            logo.create_line(19, 7, 19, 31, fill=T["gold_bright"], width=2)
            logo.create_line(7, 19, 31, 19, fill=T["gold_bright"], width=2)
        title_stack = tk.Frame(head, bg=T["raised"])
        title_stack.pack(side="left", fill="y", pady=(8, 5))
        widgets["title"] = L(
            title_stack, "LOREMASTER", fg=T["parchment"],
            font=FONT_TITLE, bg=T["raised"],
        )
        widgets["title"].pack(anchor="w")
        widgets["dot"] = L(
            title_stack, "\u25cf LIVE", fg=T["green"], font=FONT_S,
            bg=T["raised"],
        )
        widgets["dot"].pack(anchor="w")
        for txt, cmd in (("\u2715", do_quit), ("SEED", toggle_mini),
                         ("LORE", open_wiki_from_hotkey), ("RESET", do_reset)):
            button = tk.Label(
                head, text=txt, fg=T["dim"], bg=T["raised"],
                font=FONT_RUNE, cursor="hand2", padx=6, pady=5,
            )
            button.pack(side="right", padx=(0, 3), pady=8)
            button.bind("<Button-1>", lambda _e, command=cmd: command())
        tk.Frame(body, bg=T["line_soft"], height=1).pack(fill="x")

        top_summary = tk.Frame(body, bg=T["bg"])
        top_summary.pack(fill="x")
        widgets["top_summary"] = top_summary

        identity = tk.Frame(top_summary, bg=T["bg"])
        identity.pack(fill="x", padx=10, pady=(8, 0))
        widgets["who"] = L(identity, "", fg=T["parchment"], font=FONT_RUNE)
        widgets["who"].pack(side="left")
        widgets["session"] = L(identity, "", fg=T["dim"], font=FONT_S, anchor="e")
        widgets["session"].pack(side="right")
        sub = tk.Frame(top_summary, bg=T["bg"])
        sub.pack(fill="x", padx=10)
        widgets["zone"] = L(sub, "", fg=T["text"], font=FONT_S)
        wiki_hotkey_label, wiki_state_label, wiki_state_color = (
            wiki_hotkey_presentation())
        widgets["lore_shortcut"] = tk.Label(
            sub, text=(f"LORE LENS  •  {wiki_hotkey_label}  •  "
                       f"{wiki_state_label}"), fg=wiki_state_color, bg=T["raised"],
            font=FONT_RUNE, cursor="hand2", padx=6, pady=2, anchor="e")
        widgets["lore_shortcut"].pack(side="right")
        widgets["lore_shortcut"].bind("<Button-1>", open_wiki_from_hotkey)
        widgets["summary_toggle"] = tk.Label(
            sub, text=summary_toggle_label(False), fg=T["dim"], bg=T["raised"],
            font=FONT_RUNE, cursor="hand2", padx=5, pady=2)
        widgets["summary_toggle"].pack(side="right", padx=(0, 4))
        widgets["summary_toggle"].bind("<Button-1>", lambda _e: toggle_summary())
        # Pack fixed actions before the variable-length zone so TOP always
        # remains reachable at the panel's 360px minimum width.
        widgets["zone"].pack(side="left", pady=1)

        scopes = tk.Frame(
            top_summary, bg=T["void"],
            highlightbackground=T["line_soft"], highlightthickness=1,
        )
        scopes.pack(fill="x", padx=10, pady=(7, 2))
        for scope, label in (("fight", "ENCOUNTER"),
                             ("session", "SESSION"),
                             ("records", "RECORDS")):
            tab = tk.Label(scopes, text=label, fg=T["dim"], bg=T["void"],
                           font=FONT_RUNE, cursor="hand2", pady=5)
            tab.pack(side="left", expand=True, fill="x", padx=2, pady=2)
            tab.bind("<Button-1>", lambda _e, s=scope: set_scope(s))
            widgets[f"scope_{scope}"] = tab

        encounter = tk.Frame(top_summary, bg=T["bg"])
        encounter.pack(fill="x", padx=10, pady=(3, 0))
        widgets["encounter_nav"] = encounter
        for name, label, direction in (("encounter_prev", "‹ PREVIOUS", -1),
                                       ("encounter_live", "CURRENT", 0),
                                       ("encounter_next", "NEXT ›", 1)):
            b = tk.Label(encounter, text=label, fg=T["cyan"], bg=T["raised"],
                         font=FONT_RUNE_S, cursor="hand2", padx=8, pady=3)
            b.pack(side="left" if direction < 1 else "right")
            b.bind("<Button-1>", lambda _e, d=direction: browse_fight(d))
            widgets[name] = b
        widgets["encounter_label"] = L(encounter, "AWAITING ENCOUNTER", fg=T["dim"],
                                         font=FONT_RUNE, anchor="center")
        widgets["encounter_label"].pack(side="left", fill="x", expand=True)

        # The high-detail state opens with one dominant encounter number and
        # keeps context secondary.  Grid weights preserve that hierarchy as
        # the user resizes the panel.
        hero = tk.Frame(
            top_summary, bg=T["raised"], highlightbackground=T["line_soft"],
            highlightthickness=1,
        )
        hero.pack(fill="x", padx=10, pady=(4, 3))
        widgets["hero"] = hero
        metric_row = tk.Frame(hero, bg=T["raised"])
        metric_row.pack(fill="x", padx=10, pady=(8, 3))
        metric_row.grid_columnconfigure(0, weight=5)
        metric_row.grid_columnconfigure(1, weight=2)
        metric_row.grid_columnconfigure(2, weight=2)
        for column, (key, label, color) in enumerate((
                ("current_dps", "FIGHT DPS", T["gold_bright"]),
                ("session_dps", "SESSION", T["parchment"]),
                ("best_dps", "BEST", T["parchment"]))):
            cell = tk.Frame(metric_row, bg=T["raised"])
            cell.grid(row=0, column=column, sticky="nsew", padx=(0, 6))
            widgets[key] = L(
                cell, "0", fg=color,
                font=FONT_HERO if key == "current_dps" else FONT_METRIC,
                bg=T["raised"], anchor="w",
            )
            widgets[key].pack(fill="x")
            widgets[f"{key}_label"] = L(
                cell, label, fg=T["dim"], font=FONT_RUNE_S,
                bg=T["raised"], anchor="w",
            )
            widgets[f"{key}_label"].pack(fill="x")
        meter = tk.Canvas(
            hero, height=12, bg=T["raised"], highlightthickness=0, bd=0,
        )
        meter._lore_pct = 0.0
        meter._lore_draw_state = None
        meter.pack(fill="x", padx=10, pady=(2, 8))
        meter.bind("<Configure>", lambda _e: _draw_hero_meter(meter))
        widgets["hero_meter"] = meter

        quick_metrics = tk.Frame(top_summary, bg=T["bg"])
        quick_metrics.pack(fill="x", padx=10, pady=(1, 3))
        widgets["quick_metrics"] = quick_metrics
        for index, (key, label) in enumerate((
                ("damage", "DAMAGE"), ("taken", "TAKEN"),
                ("healing", "HEALING"), ("enemies", "ENEMIES"))):
            cell = tk.Frame(
                quick_metrics, bg=T["void"],
                highlightbackground=T["line_soft"], highlightthickness=1,
            )
            cell.pack(side="left", fill="both", expand=True,
                      padx=(0, 3 if index < 3 else 0))
            L(cell, label, fg=T["dim"], font=FONT_RUNE_S,
              bg=T["void"]).pack(anchor="w", padx=7, pady=(5, 0))
            widgets[f"quick_{key}"] = L(
                cell, "0", fg=T["text"], font=FONT_METRIC, bg=T["void"],
            )
            widgets[f"quick_{key}"].pack(anchor="w", padx=7, pady=(0, 5))

        lab_nav = tk.Frame(top_summary, bg=T["bg"])
        lab_nav.pack(fill="x", padx=10, pady=(4, 2))
        widgets["lab_nav"] = lab_nav
        for view, label in (("overview", "OVERVIEW"), ("damage", "DAMAGE"),
                            ("healing", "HEALING"), ("targets", "TARGETS"),
                            ("timeline", "TIMELINE")):
            tab = tk.Label(
                lab_nav, text=label, fg=T["dim"], bg=T["void"],
                font=FONT_RUNE_S, cursor="hand2", pady=4,
                highlightbackground=T["line_soft"], highlightthickness=1,
            )
            tab.pack(side="left", expand=True, fill="x", padx=(0, 2))
            tab.bind("<Button-1>", lambda _e, v=view: set_lab_view(v))
            widgets[f"lab_{view}"] = tab

        summary_restore = tk.Frame(body, bg=T["bg"])
        widgets["summary_restore"] = summary_restore
        widgets["summary_restore_label"] = tk.Label(
            summary_restore, text=summary_toggle_label(True), fg=T["cyan"],
            bg=T["raised"], font=FONT_RUNE, cursor="hand2", pady=3)
        widgets["summary_restore_label"].pack(fill="x", padx=10, pady=(3, 2))
        widgets["summary_restore_label"].bind(
            "<Button-1>", lambda _e: toggle_summary())

        # scrollable ledger
        wrap = tk.Frame(body, bg=T["bg"])
        wrap.pack(fill="both", expand=True, padx=10, pady=(2, 5))
        widgets["ledger_wrap"] = wrap
        apply_summary_visibility(
            top_summary, summary_restore, wrap, state["summary_collapsed"])
        canvas = tk.Canvas(wrap, bg=T["bg"], highlightthickness=0, width=520)
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
            sect = tk.Frame(
                inner, bg=T["void"], highlightbackground=T["line_soft"],
                highlightthickness=1,
            )
            sect.pack(fill="x", pady=(4, 0), padx=1)
            row = tk.Frame(sect, bg=T["void"], cursor="hand2")
            row.pack(fill="x", padx=9, pady=5)
            hb = hex_bullet(row, size=12, bg=T["void"])
            hb.pack(side="left", pady=2)
            nm = L(row, label, fg=T["gold"], font=FONT_RUNE, bg=T["void"])
            nm.pack(side="left", padx=(7, 0))
            chev = L(row, "\u25b8", fg=T["dim"], font=FONT_S, bg=T["void"])
            chev.pack(side="right")
            star = L(row, "\u2726", fg=T["line"], font=FONT_S, bg=T["void"], cursor="hand2")
            star.pack(side="right", padx=6)
            val = L(row, "\u2014", fg=T["text"], font=FONT_B, bg=T["void"], anchor="e")
            val.pack(side="right", padx=(0, 4), fill="x", expand=True)
            # the gold rule under each section header — equipment-screen DNA
            rl = tk.Frame(sect, bg=T["line_soft"], height=1)
            rl.pack(fill="x")
            detail = tk.Frame(sect, bg=T["bg"])
            for w in (row, hb, nm, val, chev):
                w.bind("<Button-1>", lambda _e, k=key: toggle_card(k))
            star.bind("<Button-1>", lambda _e, k=key: toggle_card_star(k))
            card_widgets[key] = {"value": val, "star": star, "chev": chev,
                                 "name": nm, "hex": hb, "rule": rl,
                                 "detail": detail, "detail_signature": None,
                                 "detail_controls": []}

        # Alerts stay in reach in the expanded state.  The master switch and
        # three frequent triggers are true toggles; the gear opens the complete
        # settings surface for thresholds, sounds, duration, and custom rules.
        alert_rail = tk.Frame(
            body, bg=T["raised"], highlightbackground=T["meter_edge"],
            highlightthickness=1,
        )
        alert_rail.pack(fill="x", padx=10, pady=(0, 6))
        alert_copy = tk.Frame(alert_rail, bg=T["raised"], cursor="hand2")
        alert_copy.pack(side="left", padx=10, pady=7)
        widgets["alert_master"] = L(
            alert_copy, "\u25cf ALERTS OFF", fg=T["dim"], font=FONT_RUNE,
            bg=T["raised"], cursor="hand2",
        )
        widgets["alert_master"].pack(anchor="w")
        widgets["alert_summary"] = L(
            alert_copy, "click to arm", fg=T["dim"], font=FONT_RUNE_S,
            bg=T["raised"], cursor="hand2",
        )
        widgets["alert_summary"].pack(anchor="w")
        for control in (alert_copy, widgets["alert_master"],
                        widgets["alert_summary"]):
            control.bind(
                "<Button-1>", lambda _e: toggle_alert_flag("alerts_enabled"))

        gear = tk.Label(
            alert_rail, text="\u2699", fg=T["dim"], bg=T["void"],
            font=FONT_B, cursor="hand2", padx=9, pady=5,
            highlightbackground=T["line_soft"], highlightthickness=1,
        )
        gear.pack(side="right", padx=(2, 8), pady=8)
        gear.bind("<Button-1>", open_settings)
        more = tk.Label(
            alert_rail, text="+", fg=T["dim"], bg=T["void"],
            font=FONT_B, cursor="hand2", padx=9, pady=5,
            highlightbackground=T["line_soft"], highlightthickness=1,
        )
        more.pack(side="right", padx=2, pady=8)
        more.bind("<Button-1>", open_settings)
        for key, label in (("alert_big_hit", "BIG HIT"),
                           ("alert_tells", "TELL"),
                           ("alert_charm_break", "CHARM")):
            chip = tk.Label(
                alert_rail, text=label, fg=T["dim"], bg=T["void"],
                font=FONT_RUNE_S, cursor="hand2", padx=9, pady=5,
                highlightbackground=T["line_soft"], highlightthickness=1,
            )
            chip.pack(side="right", padx=2, pady=8)
            chip.bind("<Button-1>", lambda _e, flag=key: toggle_alert_flag(flag))
            widgets[key] = chip

        footer = tk.Frame(body, bg=T["panel"])
        footer.pack(fill="x")
        widgets["status"] = L(footer, "Loremaster awaits your log\u2026",
                              fg=T["dim"], font=FONT_S, bg=T["panel"])
        widgets["status"].pack(fill="x", padx=10, pady=(5, 2))
        footer_actions = tk.Frame(footer, bg=T["panel"])
        footer_actions.pack(fill="x", padx=(9, 4), pady=(0, 4))
        grip = tk.Label(footer_actions, text="\u2198", fg=T["cyan"], bg=T["panel"],
                        font=FONT_B)
        try:
            grip.configure(cursor=RESIZE_CURSOR)
        except tk.TclError:
            pass
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
        width, height, x, y = target_geometry_for_mode(False)
        root.geometry(f"{width}x{height}{x:+d}{y:+d}")
        refresh(force_detail=True)

    def build_mini():
        stop_seed_motion()
        clear_scroll_bindings()
        for w in body.winfo_children():
            w.destroy()
        card_widgets.clear()
        scale = max(1.0, font_scale)
        seed_width = int(round(RUNE_SEED_WIDTH * scale))
        seed_height = int(round(RUNE_SEED_HEIGHT * scale))
        mini_width = seed_width + 2
        mini_height = seed_height + 2
        seed = rune_seed_canvas(
            body, width=seed_width, height=seed_height, bg=T["bg"])
        seed.pack(fill="both", expand=True)
        widgets["mini_seed"] = seed
        widgets["mini_width"] = mini_width

        def cycle_seed(direction):
            keys = rune_seed_keys(cfg.get("starred_cards"))
            state["mini_stat_index"] = cycle_rune_seed_index(
                state.get("mini_stat_index", 0), direction, len(keys))
            cfg["mini_stat_index"] = state["mini_stat_index"]
            pending = state.get("mini_save_after")
            if pending is not None:
                try:
                    root.after_cancel(pending)
                except tk.TclError:
                    pass

            def persist_seed_index():
                state["mini_save_after"] = None
                save_config(cfg)

            state["mini_save_after"] = root.after(250, persist_seed_index)
            refresh()
            return "break"

        def wheel(event):
            delta = int(getattr(event, "delta", 0) or 0)
            return cycle_seed(1 if delta < 0 else -1)

        # A click unfolds the seed; the same surface remains draggable when
        # unlocked.  Motion is coalesced to ~60 Hz so a window drag cannot
        # starve the parser/UI loop.
        seed_drag = {"mouse": (0, 0), "origin": (0, 0), "moved": False,
                     "pending": None, "after": None}

        def flush_seed_drag():
            seed_drag["after"] = None
            pending = seed_drag.get("pending")
            seed_drag["pending"] = None
            if pending is not None:
                root.geometry(f"{pending[0]:+d}{pending[1]:+d}")

        def seed_press(event):
            seed_drag["mouse"] = (event.x_root, event.y_root)
            seed_drag["origin"] = (root.winfo_x(), root.winfo_y())
            seed_drag["moved"] = False
            seed_drag["pending"] = None
            return "break"

        def seed_motion(event):
            if state["locked"] or state["click_through"]:
                return "break"
            dx = event.x_root - seed_drag["mouse"][0]
            dy = event.y_root - seed_drag["mouse"][1]
            if abs(dx) + abs(dy) < 4 and not seed_drag["moved"]:
                return "break"
            seed_drag["moved"] = True
            x, y = clamped_position(
                (seed_drag["origin"][0] + dx, seed_drag["origin"][1] + dy),
                mini_width, mini_height, root.winfo_x(), root.winfo_y())
            seed_drag["pending"] = (x, y)
            if seed_drag["after"] is None:
                seed_drag["after"] = root.after(16, flush_seed_drag)
            return "break"

        def seed_release(_event):
            pending = seed_drag.get("after")
            if pending is not None:
                try:
                    root.after_cancel(pending)
                except tk.TclError:
                    pass
                seed_drag["after"] = None
            flush_seed_drag()
            if seed_drag["moved"]:
                cfg["mini_position"] = [root.winfo_x(), root.winfo_y()]
                save_config(cfg)
            else:
                toggle_mini()
            return "break"

        seed.configure(cursor="hand2" if state["locked"] else "fleur")
        seed.bind("<Button-1>", seed_press)
        seed.bind("<B1-Motion>", seed_motion)
        seed.bind("<ButtonRelease-1>", seed_release)
        seed.bind("<MouseWheel>", wheel)
        seed.bind("<Button-4>", lambda _e: cycle_seed(-1))
        seed.bind("<Button-5>", lambda _e: cycle_seed(1))
        seed.bind("<Button-2>", lambda _e: (toggle_lock(), "break")[1])
        seed.bind("<Button-3>", open_settings)

        _width, _height, x, y = target_geometry_for_mode(True)
        root.geometry(f"{mini_width}x{mini_height}{x:+d}{y:+d}")
        root.update_idletasks()
        set_capsule_window_region(True, mini_width, mini_height)
        refresh()

    def show_morph_bridge(target_mini):
        """Show one cached, configure-light brand bridge during the morph."""
        stop_seed_motion()
        # The timer tracker continues running, but its independent window is
        # hidden until the destination HUD can place it correctly.
        mez_overlay.hide()
        set_capsule_window_region(False)
        clear_scroll_bindings()
        for child in body.winfo_children():
            child.destroy()
        card_widgets.clear()
        bridge = tk.Canvas(
            body, bg=T["bg"], highlightthickness=0, bd=0)
        bridge.pack(fill="both", expand=True)
        bridge.create_oval(
            0, 0, 1, 1, fill=T["void"], outline=T["line_soft"], width=1,
            tags="bridge_halo")
        bridge.create_arc(
            0, 0, 1, 1, start=90, extent=88, style="arc",
            outline=T["gold_bright"], width=2, tags="bridge_sweep")
        cog = brand_images.get("cog")
        if cog is not None:
            bridge.create_image(0, 0, image=cog, anchor="center",
                                tags="bridge_brand")
            bridge._lore_brand_image = cog
        else:
            bridge.create_oval(0, 0, 1, 1, fill=T["void"],
                               outline=T["gold"], width=2,
                               tags="bridge_brand")
        bridge.create_text(
            0, 0, text="LOREMASTER", fill=T["parchment"],
            font=FONT_TITLE, tags="bridge_title")
        bridge.create_text(
            0, 0,
            text="RETURNING TO RUNE" if target_mini else "OPENING LEDGER",
            fill=T["dim"], font=FONT_RUNE_S, tags="bridge_direction")
        bridge.create_line(
            0, 0, 1, 0, fill=T["line_soft"], width=2,
            tags="bridge_rail")
        bridge.create_line(
            0, 0, 1, 0, fill=T["cyan"], width=2,
            tags="bridge_progress")
        visual = {"width": 1, "height": 1, "progress": 0.0}

        def paint_bridge(event=None):
            if event is not None:
                visual["width"] = max(1, int(event.width))
                visual["height"] = max(1, int(event.height))
            width = visual["width"]
            height = visual["height"]
            progress = visual["progress"]
            cx, cy = width // 2, height // 2
            radius = max(18, min(23, min(width, height) // 3))
            bridge.coords(
                "bridge_halo", cx - radius, cy - radius,
                cx + radius, cy + radius)
            bridge.coords(
                "bridge_sweep", cx - radius, cy - radius,
                cx + radius, cy + radius)
            bridge.itemconfigure(
                "bridge_sweep", start=90 - progress * 300,
                outline=blend_hex_color(T["gold"], T["cyan"], progress))
            if cog is not None:
                bridge.coords("bridge_brand", cx, cy)
            else:
                bridge.coords(
                    "bridge_brand", cx - 15, cy - 15, cx + 15, cy + 15)
            expanded_copy = width >= 190 and height >= 110
            copy_state = "normal" if expanded_copy else "hidden"
            bridge.coords("bridge_title", cx, cy + radius + 23)
            bridge.coords("bridge_direction", cx, cy + radius + 41)
            bridge.itemconfigure("bridge_title", state=copy_state)
            bridge.itemconfigure("bridge_direction", state=copy_state)
            rail_half = max(25, min(112, (width - 48) // 2))
            rail_y = cy + radius + 59
            bridge.coords(
                "bridge_rail", cx - rail_half, rail_y,
                cx + rail_half, rail_y)
            bridge.coords(
                "bridge_progress", cx - rail_half, rail_y,
                cx - rail_half + rail_half * 2 * progress, rail_y)
            bridge.itemconfigure("bridge_rail", state=copy_state)
            bridge.itemconfigure("bridge_progress", state=copy_state)

        bridge.bind("<Configure>", paint_bridge)
        paint_bridge()

        def update_progress(progress, _rect):
            visual["progress"] = max(0.0, min(1.0, float(progress)))
            paint_bridge()

        return update_progress

    def toggle_mini():
        if state.get("morphing"):
            return
        start = (root.winfo_width(), root.winfo_height(),
                 root.winfo_x(), root.winfo_y())
        pending = state.get("mini_save_after")
        if pending is not None:
            try:
                root.after_cancel(pending)
            except tk.TclError:
                pass
            state["mini_save_after"] = None
        target_mini = not state["mini"]
        target = target_geometry_for_mode(target_mini)

        swapped = {"done": False}

        def swap_layout():
            if swapped["done"] or state["closing"]:
                return
            swapped["done"] = True
            width, height, x, y = target
            root.geometry(f"{width}x{height}{x:+d}{y:+d}")
            state["mini"] = target_mini
            cfg["mini_mode"] = target_mini
            (build_mini if target_mini else build_full)()

        def finish_transition():
            if state["closing"]:
                return
            state["morph_after"] = None
            state["morphing"] = False
            # Disk I/O is intentionally off the visual critical path.
            root.after(
                100, lambda: None if state["closing"] else save_config(cfg))

        state["morphing"] = True
        # Resizing a fully populated Tk tree on every frame causes Windows to
        # repaint hundreds of child widgets and produces the visible jitter the
        # old morph occasionally showed. Fade the existing surface, perform one
        # atomic geometry/layout swap near-transparent, then reveal it. This is
        # both smoother and substantially cheaper than animated reflow.
        if cfg.get("reduced_motion", False):
            swap_layout()
            finish_transition()
            return
        try:
            normal_alpha = max(0.75, min(1.0, float(cfg.get("opacity", 1.0))))
        except (TypeError, ValueError):
            normal_alpha = 1.0
        fade_steps = (0.82, 0.56, 0.30, 0.10)
        reveal_steps = (0.22, 0.40, 0.60, 0.78, 0.91, 1.0)

        def reveal(index=0):
            if state["closing"]:
                return
            if index >= len(reveal_steps):
                try:
                    root.attributes("-alpha", normal_alpha)
                except tk.TclError:
                    pass
                finish_transition()
                return
            try:
                root.attributes("-alpha", normal_alpha * reveal_steps[index])
            except tk.TclError:
                swap_layout()
                finish_transition()
                return
            state["morph_after"] = root.after(16, reveal, index + 1)

        def fade(index=0):
            if state["closing"]:
                return
            if index >= len(fade_steps):
                swap_layout()
                state["morph_after"] = root.after(10, reveal)
                return
            try:
                root.attributes("-alpha", normal_alpha * fade_steps[index])
            except tk.TclError:
                swap_layout()
                finish_transition()
                return
            state["morph_after"] = root.after(14, fade, index + 1)

        fade()

    def do_reset():
        stats.reset()
        mez_tracker.clear()
        lull_tracker.clear()
        mez_overlay.hide()
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
            return None
        cfg["log_dir"] = str(Path(selected))
        save_config(cfg)
        if ingest_worker is not None:
            ingest_worker.close(timeout=0.75)
        else:
            watcher.close()
        watcher = LogWatcher(cfg["log_dir"], args.log)
        ingest_worker = LogIngestWorker(watcher)
        ingest_pending.clear()
        mez_tracker.clear()
        lull_tracker.clear()
        mez_overlay.hide()
        if widgets.get("status"):
            widgets["status"].configure(text="searching for the newest eqlog…")
        state["fights_seen"] = 0
        return cfg["log_dir"]

    def hide_to_tray():
        """Hide the HUD without stopping log tracking or Lore Lens hotkeys."""
        if state["closing"]:
            return
        if state["click_through"]:
            state["click_through"] = False
            _apply_click_through()
        state["hidden_to_tray"] = True
        state["manual_show"] = False
        _wiki_close()
        settings_window = widgets.get("settings_window")
        if settings_window:
            try:
                settings_window.withdraw()
            except tk.TclError:
                pass
        alerts.clear()
        mez_overlay.hide()
        try:
            root.attributes("-topmost", False)
            root.withdraw()
        except tk.TclError:
            pass
        z_order["window_hidden"] = True
        z_order["floating"] = False

    def show_from_tray():
        """Explicit recovery action; safe for withdrawn or minimized roots."""
        if state["closing"]:
            return
        state["hidden_to_tray"] = False
        if args.wait_for_eq and not z_order.get("eq_running", True):
            state["manual_show"] = True
        try:
            root.deiconify()
            root.state("normal")
            root.lift()
            root.focus_force()
        except tk.TclError:
            return
        z_order["window_hidden"] = False
        z_order["floating"] = None

    def do_quit():
        if state["closing"]:
            return
        state["closing"] = True
        for callback_key in (
                "morph_after", "mini_save_after", "seed_motion_after"):
            pending = state.get(callback_key)
            if pending is not None:
                try:
                    root.after_cancel(pending)
                except tk.TclError:
                    pass
                state[callback_key] = None
        tray.close(timeout=1.0)
        if state["click_through"]:
            state["click_through"] = False
            _apply_click_through()
        remove_recovery_hotkey()
        hover_ocr_service.close()
        wiki_service.close()
        mez_overlay.destroy()
        if not demo:
            save_character_state(stats.character, stats)
        save_config(cfg)
        if ingest_worker is not None:
            ingest_worker.close(timeout=0.75)
        else:
            watcher.close()
        root.destroy()

    def poll_tray_commands():
        if state["closing"]:
            return
        for command in tray.poll(limit=8):
            if command == TRAY_SHOW:
                show_from_tray()
            elif command == TRAY_HIDE:
                hide_to_tray()
            elif command == TRAY_EXIT:
                do_quit()
                return
        if not state["closing"]:
            root.after(80, poll_tray_commands)

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
        mez_tracker.clear()
        lull_tracker.clear()
        mez_overlay.hide()
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
        charm_break_events = ()
        if parsed:
            ts, kind, groups = parsed
            cutoff = state.get("lifetime_cutoff")
            charm_break_events = apply_log_models(
                stats, mez_tracker, ts, kind, groups,
                count_lifetime=(cutoff is None or ts > cutoff),
                lull_tracker=lull_tracker,
                caster_level=stats.level)
            if kind == "composition" and stats.composition:
                remember_composition(cfg, stats.character, stats.composition)
                save_config(cfg)
        for severity, text_msg in check_alerts(
                kind, groups, raw_msg, stats.character, cfg,
                charm_break_events):
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

            done = len(stats.fights)
            if (fight_toasts_active(cfg) and done > state["fights_seen"]
                    and stats.fights):
                f = stats.fights[-1]
                alerts.show("info", f"{f.name}  \u2014  {fmt_num(f.dps)} dps  "
                            f"({fmt_num(f.damage)} in {fmt_dur(f.seconds)})")
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

    def _draw_hero_meter(canvas):
        """Update the two cached hero-meter rectangles in place."""
        width = max(1, canvas.winfo_width())
        pct = max(0.0, min(1.0, float(getattr(canvas, "_lore_pct", 0.0))))
        fill_width = round(width * pct)
        draw_state = (width, round(pct, 4))
        if getattr(canvas, "_lore_draw_state", None) == draw_state:
            return
        canvas._lore_draw_state = draw_state
        items = getattr(canvas, "_lore_items", None)
        if not items:
            track = canvas.create_rectangle(
                0, 3, width, 9, fill=T["meter"], outline=T["meter_edge"],
            )
            fill = canvas.create_rectangle(
                0, 3, fill_width, 9, fill=T["cyan"], outline="",
                state="normal" if fill_width else "hidden",
            )
            canvas._lore_items = (track, fill)
        else:
            track, fill = items
            canvas.coords(track, 0, 3, width, 9)
            canvas.coords(fill, 0, 3, fill_width, 9)
            canvas.itemconfigure(fill, state="normal" if fill_width else "hidden")

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
        # The short Rune Seed morph temporarily replaces the widget tree with
        # a transition canvas.  Let the completed target build own the next
        # refresh instead of touching widgets that were just destroyed.
        if state.get("morphing"):
            return
        now = datetime.now()
        snap = stats.snapshot(now)
        warning_seconds = config_number("mez_warning_seconds", 10, 3, 30)
        lull_warning_seconds = config_number(
            "lull_warning_seconds", 12, 3, 30)
        mez_snapshot = mez_tracker.snapshot(
            now, limit=None,
            warning_seconds=warning_seconds,
            critical_seconds=min(5.0, warning_seconds),
        )
        lull_snapshot = lull_tracker.snapshot(
            now, limit=None,
            warning_seconds=lull_warning_seconds,
            critical_seconds=min(5.0, lull_warning_seconds),
        )
        mez_enabled = bool(cfg.get("mez_timers_enabled", True))
        lull_enabled = bool(cfg.get("lull_timers_enabled", True))
        control_snapshot = merge_control_snapshots(
            mez_snapshot, lull_snapshot,
            limit=MezTimerOverlay.MAX_ROWS,
            include_mez=mez_enabled,
            include_lull=lull_enabled,
        )
        timer_enabled = mez_enabled or lull_enabled
        settings_window = widgets.get("settings_window")
        lore_window = wiki_ui.get("win")
        try:
            interactive_window_visible = bool(
                (settings_window and settings_window.winfo_viewable())
                or (lore_window and lore_window.winfo_viewable()))
            root_visible = bool(
                root.winfo_viewable() and root.state() == "normal")
        except tk.TclError:
            interactive_window_visible = False
            root_visible = False
        hud_visible = not (
            state["hidden_to_tray"] or z_order.get("window_hidden")
            or interactive_window_visible
        ) and root_visible
        mez_overlay.render(
            control_snapshot, enabled=timer_enabled, hud_visible=hud_visible,
            occupied_rects=alerts.occupied_rects())
        mez_overlay.warning_sound(mez_tracker.pop_warning_events(
            now, threshold_seconds=warning_seconds,
            enabled=(mez_enabled and bool(cfg.get("mez_timer_sound", False))),
        ), enabled=(mez_enabled and bool(cfg.get("mez_timer_sound", False))))
        mez_overlay.warning_sound(lull_tracker.pop_warning_events(
            now, threshold_seconds=lull_warning_seconds,
            enabled=(lull_enabled and bool(cfg.get("lull_timer_sound", False))),
        ), enabled=(lull_enabled and bool(cfg.get("lull_timer_sound", False))))
        if state["mini"]:
            seed = widgets.get("mini_seed")
            if not seed:
                return
            keys = rune_seed_keys(cfg.get("starred_cards"))
            index = state.get("mini_stat_index", 0) % len(keys)
            state["mini_stat_index"] = index
            value, label = rune_seed_metric(snap, keys[index])
            health, color = ("DEMO", T["green"]) if demo else log_health()
            alert = state.get("mini_alert")
            if alert and time.monotonic() >= alert.get("until", 0.0):
                state["mini_alert"] = None
                alert = None
            paint_rune_seed(
                seed, value, label, health, color, alert,
                metric_index=index, metric_count=len(keys),
                in_combat=snap["in_combat"])
            ensure_seed_motion()
            try:
                seed.configure(cursor="hand2" if state["locked"] else "fleur")
            except tk.TclError:
                pass
            return

        title = snap["character"].upper()
        if snap.get("level"):
            title += f"  ·  {snap['level']}"
        if snap.get("composition"):
            title += f"  ·  {snap['composition']}"
        if watcher.server != "?":
            title += f"  ·  {watcher.server.upper()}"
        _set_text(widgets["who"], title)
        health, health_color = ("DEMO", T["green"]) if demo else log_health()
        _set_widget(widgets["dot"], text=f"\u25cf {health}", fg=health_color)
        _set_text(widgets["zone"], snap["zone"] or "\u2014")
        lore_shortcut = widgets.get("lore_shortcut")
        if lore_shortcut:
            shortcut, hotkey_state, hotkey_color = wiki_hotkey_presentation()
            _set_widget(
                lore_shortcut,
                text=f"LORE LENS  \u2022  {shortcut}  \u2022  {hotkey_state}",
                fg=hotkey_color)
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
        if nav:
            if state["scope"] == "fight":
                if not nav.winfo_manager():
                    nav.pack(fill="x", padx=10, pady=(2, 0), before=widgets["hero"])
                if lab_nav and not lab_nav.winfo_manager():
                    lab_nav.pack(
                        fill="x", padx=10, pady=(4, 2),
                        after=widgets["quick_metrics"])
            else:
                if nav.winfo_manager():
                    nav.pack_forget()
                if lab_nav and lab_nav.winfo_manager():
                    lab_nav.pack_forget()
        if state["scope"] == "fight":
            for view in ("overview", "damage", "healing", "targets", "timeline"):
                tab = widgets.get(f"lab_{view}")
                if tab:
                    active = state.get("lab_view") == view
                    _set_widget(tab, bg=T["raised"] if active else T["void"],
                                fg=T["cyan"] if active else T["dim"])
        lock = widgets.get("lock")
        if lock:
            _set_widget(lock, text="MOVE" if state["locked"] else "LOCK",
                        fg=T["gold_bright"] if state["locked"] else T["dim"])
        grip = widgets.get("grip")
        if grip:
            try:
                _set_widget(
                    grip, fg=T["line"] if state["locked"] else T["cyan"],
                    cursor="arrow" if state["locked"] else RESIZE_CURSOR)
            except tk.TclError:
                _set_widget(grip, fg=T["line"] if state["locked"] else T["cyan"])
        pass_button = widgets.get("pass")
        if pass_button:
            if state["click_through"]:
                _set_widget(pass_button, text="PASS ON", fg=T["gold_bright"])
            else:
                _set_widget(pass_button, text="CLICK-THRU", fg=(T["cyan"]
                            if hotkey["registered"] else T["line"]))
        alert_master = widgets.get("alert_master")
        if alert_master:
            master_on = bool(cfg.get("alerts_enabled", False))
            trigger_keys = (
                "alert_charm_break", "alert_tells", "alert_summon",
                "alert_death", "alert_big_hit", "alert_name_called",
            )
            armed = sum(bool(cfg.get(key, True)) for key in trigger_keys)
            _set_widget(
                alert_master,
                text=f"\u25cf ALERTS {'ON' if master_on else 'OFF'}",
                fg=T["green"] if master_on else T["dim"],
            )
            _set_text(
                widgets["alert_summary"],
                f"{armed} armed \u00b7 click to {'disarm' if master_on else 'enable'}",
            )
            for key in ("alert_charm_break", "alert_tells", "alert_big_hit"):
                chip = widgets.get(key)
                if not chip:
                    continue
                enabled = bool(cfg.get(key, True))
                _set_widget(
                    chip,
                    fg=(T["gold_bright"] if enabled and master_on
                        else T["parchment"] if enabled else T["line"]),
                    bg=T["raised"] if enabled and master_on else T["void"],
                    highlightbackground=(T["meter_edge"] if enabled
                                         else T["line_soft"]),
                )
        hero_primary = 0.0
        hero_best = 0.0
        if state["scope"] == "records":
            life = snap["lifetime"]
            hero_primary = float(life["best_dps"] or 0)
            hero_best = hero_primary
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
            hero_primary = float(fight_dps or 0)
            _set_widget(
                widgets["current_dps"],
                text=fmt_num(fight_dps),
                fg=T["gold_bright"] if shown_live else T["dim"])
            _set_text(widgets["session_dps"], fmt_num(snap["session_dps"]))
            best = snap["best_fight"]
            hero_best = float(best.dps if best else 0)
            _set_text(widgets["best_dps"], fmt_num(best.dps) if best else "0")
            for key, label in (("current_dps", "FIGHT DPS"),
                               ("session_dps", "SESSION"),
                               ("best_dps", "BEST")):
                _set_text(widgets[f"{key}_label"], label)
        hero_meter = widgets.get("hero_meter")
        if hero_meter:
            hero_meter._lore_pct = (hero_primary / hero_best
                                    if hero_best > 0 else 0.0)
            _draw_hero_meter(hero_meter)

        quick_fight = displayed_fight(snap) if state["scope"] == "fight" else None
        if quick_fight:
            target_types = (set(quick_fight.observed_targets)
                            | set(quick_fight.kill_targets))
            quick_values = {
                "damage": quick_fight.damage,
                "taken": quick_fight.damage_taken,
                "healing": quick_fight.healing_done,
                "enemies": quick_fight.kills or len(target_types),
            }
        else:
            quick_values = {
                "damage": snap["combat_damage"],
                "taken": snap["damage_taken"],
                "healing": snap["healing_done"],
                "enemies": snap["kills"] + sum(snap["group_kills"].values()),
            }
        for key, value in quick_values.items():
            label = widgets.get(f"quick_{key}")
            if label:
                _set_text(label, compact_hud_number(value))
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
               "eq_running": True, "window_hidden": False}

    def sync_z_order():
        if state["closing"]:
            return
        try:
            now = time.monotonic()
            if args.wait_for_eq and now >= z_order["eq_check_at"]:
                z_order["eq_running"] = process_is_running("eqgame.exe")
                z_order["eq_check_at"] = now + 1.0
                if z_order["eq_running"]:
                    state["manual_show"] = False
            visible = overlay_should_be_visible(
                hidden_to_tray=bool(state["hidden_to_tray"]),
                wait_for_eq=bool(args.wait_for_eq),
                eq_running=bool(z_order["eq_running"]),
                manual_show=bool(state["manual_show"]),
            )
            if not visible:
                if state["click_through"]:
                    state["click_through"] = False
                    _apply_click_through()
                if not z_order["window_hidden"]:
                    _wiki_close()
                    settings_window = widgets.get("settings_window")
                    if settings_window:
                        try:
                            settings_window.withdraw()
                        except tk.TclError:
                            pass
                    alerts.clear()
                    mez_overlay.hide()
                    root.withdraw()
                    z_order["window_hidden"] = True
                floating = False
            else:
                if z_order["window_hidden"]:
                    root.deiconify()
                    z_order["window_hidden"] = False
                floating = foreground_is_everquest_or_loremaster(root.winfo_id())

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
            mez_overlay.sync_topmost(floating)
        finally:
            if not state["closing"]:
                root.after(250, sync_z_order)

    def note_hud_alert(severity, text_msg):
        """Ignite the Rune Seed briefly while the adjacent toast is visible."""
        state["mini_alert"] = {
            "severity": severity,
            "text": text_msg,
            "started": time.monotonic(),
            "until": time.monotonic() + RUNE_ALERT_SECONDS,
        }
        if state["mini"] and widgets.get("mini_seed"):
            refresh()

    alerts.on_show = note_hud_alert

    install_recovery_hotkey()
    install_wiki_hotkey()
    root.protocol("WM_DELETE_WINDOW", do_quit)
    (build_mini if state["mini"] else build_full)()
    if cfg.get("wiki_enabled", True) and not hotkey["wiki_registered"]:
        alerts.show(
            "warn", "LORE LENS HOTKEY CONFLICT — OPEN SETTINGS TO REBIND")
    tray.start(timeout=0.75)
    try:
        sync_z_order()
        poll_recovery_hotkey()
        poll_tray_commands()
        poll_hover_ocr_results()
        poll_wiki_results()
        tick()
        root.mainloop()
    finally:
        hotkey_service.close(timeout=1.0)
        tray.close(timeout=1.0)
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
        line(9, "--You have looted a Motes of Infinitesimal Potential from a "
                "froglok shin knight's corpse.--"),
        line(9, "--You have looted a Motes of Major Potential.--"),
        # A stack, and a system line with no dashes and no article: neither
        # reached the ledger before, so neither reached the tracker.
        line(9, "--You have looted 4 Motes of Minor Potential.--"),
        line(9, "You receive a Mote of Major Potential."),
        line(9, "You have gained 3 Motes of Greater Potential."),
        # Someone else naming the item in chat is never a mote you looted.
        line(9, "Aria tells you, 'You looted a Mote of Infinite Potential'"),
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
    # End to end: a real loot line reaches the mote tracker through the same
    # ledger SPOILS reads, so the two can never disagree.
    # Every acquisition shape lands in the session tally exactly once, chat
    # does not, and a stack counts its whole stack.
    assert snap["motes"] == [1, 4, 0, 0, 2, 3, 0, 0, 0, 0]
    assert fmt_mote_tiers(snap["motes"]) == "1/4/0/0/2/3"
    assert mote_exp_total(snap["motes"]) == 1 + 4 + 10 + 18
    # The ledger itself now records the stack count rather than one item.
    assert snap["loot"]["Motes of Minor Potential"] == 4
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
    # Rune Seed: the widened capsule reserves separate cog and metric lanes.
    assert RUNE_SEED_WIDTH == 92 and RUNE_SEED_HEIGHT == 48
    assert MINI_BASE_WIDTH == 94 and MINI_BASE_HEIGHT == 50
    assert MINI_MIN_WIDTH == MINI_BASE_WIDTH
    assert png_asset_identity(
        bundled_resource_path("assets", BRAND_COG_FILE)) == (32, 32, 6)
    seed_layout = rune_seed_content_layout(
        RUNE_SEED_WIDTH, RUNE_SEED_HEIGHT)
    assert seed_layout["icon"][2] < seed_layout["text"][0]
    assert len(geometry_morph_frames(
        (MINI_BASE_WIDTH, MINI_BASE_HEIGHT, 0, 0),
        FULL_DEFAULT_SIZE + (0, 0))) == HUD_MORPH_STEPS
    # SETTINGS belongs to the expanded footer and the seed's right click.
    assert "MOTES" in MINI_CARD_LABELS.values()
    assert MINI_MAX_CELLS == 4
    assert DEFAULT_RUNE_SEED_CARDS == ("combat",)
    assert RUNE_SEED_CONFIG_VERSION == 3
    # PROGRESSION remains available in the expanded ledger and can be starred
    # into the one-metric Rune Seed carousel.
    assert "progress" in MINI_CARD_LABELS and "motes" in MINI_CARD_LABELS

    # Potential-mote tracking across all ten grades: plural item names, the
    # unqualified fourth grade, and casing all bucket into in-game tier order.
    assert len(MOTE_TIERS) == 10
    assert len(MOTE_GRADES) == len(MOTE_TIER_LABELS) == len(MOTE_TIER_EXP) == 10
    assert mote_tier_counts({
        "Motes of Infinitesimal Potential": 27,
        "Mote of Minor Potential": 32,
        "motes of lesser potential": 3,
        "Motes of Potential": 2,
        "Mote of Major Potential": 1,
        "Mote of Greater Potential": 6,
        "Mote of Superior Potential": 7,
        "Mote of Grand Potential": 8,
        "Mote of Ascendant Potential": 9,
        "Mote of Infinite Potential": 10,
        "Froglok Fine Mesh": 4,
    }) == [27, 32, 3, 2, 1, 6, 7, 8, 9, 10]
    # "Infinite" and "Infinitesimal" share a prefix and must never merge.
    assert mote_tier_counts({"Mote of Infinitesimal Potential": 1})[0] == 1
    assert mote_tier_counts({"Mote of Infinite Potential": 1})[9] == 1
    # The compact ledger readout stops at the highest grade that dropped.
    assert fmt_mote_tiers([27, 32, 3, 2, 1] + [0] * 5) == "27/32/3/2/1"
    assert fmt_mote_tiers([5] + [0] * 9) == "5"
    assert fmt_mote_tiers([0] * 9 + [1]) == "0/0/0/0/0/0/0/0/0/1"
    assert fmt_mote_tiers([0] * 10) == "\u2014"
    assert fmt_mote_tiers(None) == "\u2014"
    assert mote_tier_counts({}) == [0] * 10
    assert mote_tier_counts(None) == [0] * 10
    # Each grade carries its own exp value.
    assert mote_exp_total([1] * 10) == sum((1, 1, 2, 4, 5, 6, 7, 8, 9, 10))
    assert mote_exp_total([0] * 10) == 0
    # Near-misses must not be swept into a tier.
    assert mote_tier_counts({
        "Mote of Potential Greatness": 5,
        "Shard of Minor Potential": 5,
        "Motes of Major Potentials": 5,
        "Mote of Supreme Potential": 5,
    }) == [0] * 10
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

    # Multi-word Enchanter charms are owned through pet chatter, matched
    # case-insensitively, and counted as personal pet damage. Same-name combat
    # is included but explicitly tracked as an estimate because logs have no
    # actor IDs to distinguish the two rock golems.
    charm = SessionStats("Spin")
    for raw in (
        line(0, "A rock golem told you, 'Attacking a rock golem Master.'"),
        line(1, "A rock golem hits YOU for 40 points of damage."),
        line(2, "A rock golem slashes a rock golem for 91 points of damage."),
        line(3, "A rock golem slashes a rock golem for 123 points of damage."),
        line(4, "You bash a rock golem for 2 points of damage."),
        line(5, "You slash a rock golem for 36 points of damage."),
        line(6, "You slash a rock golem for 79 points of damage."),
    ):
        charm.apply(*parse_line(raw))
    charm_snap = charm.snapshot(now + timedelta(seconds=6))
    assert charm_snap["combat_damage"] == 331
    assert charm_snap["pet_damage"] == 214
    assert charm_snap["ambiguous_pet_damage"] == 214
    assert charm.is_charmed_pet("a rock golem")
    charm.apply(*parse_line(line(
        7, "Your Cajoling Whispers spell has worn off of a rock golem.")))
    assert not charm.is_pet("a rock golem")

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
