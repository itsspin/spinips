#!/usr/bin/env python3
"""SpinUI resolution-aware layout generator.

Produces two files from pixel-exact placement tables:
  * spinui_reloaded/default1440.ini  — safe standard 2560x1440 default
  * UI_Spin_qeynos_LO1.ini           — drop-in personal layout for Spin @ qeynos

Both are derived from the shipped default1440.ini / the player's uploaded
UI file, so every key the client expects stays present; only geometry,
visibility and the chat routing are rewritten.

The optional personal layout remains pixel-perfect at 3440x1440.  The skin's
generic default1440.ini is separately authored for 2560x1440 so a standard
1440p player never inherits off-screen ultrawide coordinates.

The script validates: every placed window fully on-screen, and no two
default-visible windows overlap.

Run from repo root:  python3 tools/generate_spinui_layout.py
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKIN = REPO / "spinui_reloaded"
SCREEN_W, SCREEN_H = 3440, 1440

CHAT_TOP = 1152          # chat row: 1152..1432, 8px bottom margin
DOCK_X = 2492            # bottom-right utility dock (bags / SpinBuddy)


def pct_x(px: float) -> str:
    return f"{px / SCREEN_W * 100:.6f}%"


def pct_y(px: float) -> str:
    return f"{px / SCREEN_H * 100:.6f}%"


# ---------------------------------------------------------------------------
# Placement table: section -> dict of INI keys to force.
# x/y are left/top pixels; w/h set Width/Height keys when given.
# "show" sets Show=; omitted -> leave whatever the base file has.
# ---------------------------------------------------------------------------

def P(x, y, w=None, h=None, show=None, extra=None):
    d = {"XRef": "left", "YRef": "top", "XPos": pct_x(x), "YPos": pct_y(y)}
    if w is not None:
        d["Width"] = str(w)
    if h is not None:
        d["Height"] = str(h)
    if show is not None:
        d["Show"] = str(show)
    if extra:
        d.update(extra)
    d["_rect"] = (x, y, w, h)
    return d


def P_for(screen_w, screen_h, x, y, w=None, h=None, show=None, extra=None):
    """Placement encoded for a specific resolution without mutating globals."""
    d = {"XRef": "left", "YRef": "top",
         "XPos": f"{x / screen_w * 100:.6f}%",
         "YPos": f"{y / screen_h * 100:.6f}%"}
    if w is not None:
        d["Width"] = str(w)
    if h is not None:
        d["Height"] = str(h)
    if show is not None:
        d["Show"] = str(show)
    if extra:
        d.update(extra)
    d["_rect"] = (x, y, w, h)
    return d


# Chat-row geometry per preset: (name, x, width, height, y) for the three
# visible containers.  MainChat = Main Chat, Chat 1 = Social, Chat 2 = Combat.
CHAT_PRESETS = {
    # Three equal panes — a symmetrical chat row (8px gaps, flush to the dock).
    "combat-focus": {
        "MainChat": (8, 820, 280, CHAT_TOP),
        "Chat 1": (836, 820, 280, CHAT_TOP),
        "Chat 2": (1664, 820, 280, CHAT_TOP),
    },
    # Social pane dominates; combat stays readable.
    "social-focus": {
        "MainChat": (8, 800, 280, CHAT_TOP),
        "Chat 1": (816, 1000, 280, CHAT_TOP),
        "Chat 2": (1824, 660, 280, CHAT_TOP),
    },
    # Hybrid: big main + social, small self-combat ticker bottom-aligned.
    "hybrid": {
        "MainChat": (8, 900, 280, CHAT_TOP),
        "Chat 1": (916, 1000, 280, CHAT_TOP),
        "Chat 2": (1924, 560, 200, 1232),
    },
}

CHAT_ALPHA = {"Alpha": "235", "FadeToAlpha": "150", "Fades": "1"}

PLACEMENTS: dict[str, dict] = {
    # --- chat row (geometry filled in per preset) ---------------------------
    "MainChat": P(8, CHAT_TOP, 700, 280, extra=CHAT_ALPHA),
    "Chat 1":  P(716, CHAT_TOP, 700, 280, extra=CHAT_ALPHA),
    "Chat 2":  P(1424, CHAT_TOP, 1060, 280, extra=CHAT_ALPHA),
    # Legacy fourth container is explicitly parked and hidden; the active
    # three-pane ChatManager ends at x=2484, leaving the bag dock unobstructed.
    "Chat 3":  P(2492, CHAT_TOP, 700, 280, show=0, extra=CHAT_ALPHA),

    # --- left column: spell gems + vertical hotbars -------------------------
    "CastSpellWnd":   P(8, 521, 52, 623, show=1),
    "HotButtonWnd":   P(64, 877, 94, 267),
    "HotButtonWnd11": P(162, 873, 98, 271),

    # --- center combat cluster (above chat) ---------------------------------
    # Player and Target plates share the hotbar block's outer edges, so the
    # pair is perfectly centered over the rows beneath (block midpoint 1720).
    "PlayerWindow":  P(1188, 770, 360, 193, show=1),
    "TargetWindow":  P(1892, 770, 360, 193, show=1),
    # The expanded 356x255 pet plate sits on the same 8px grid as the combat
    # cluster. It clears PlayerWindow on the right and the utility hotbars
    # below, while remaining visible beside the open inventory window.
    "PetInfoWindow": P(824, 710, 356, 255),                     # Show per base
    "PetInfoWindow_1": P(824, 710, 356, 255, show=0),
    "PetInfoWindow_2": P(824, 710, 356, 255, show=0),
    # Right-buff variant is wider; align its right edge with the base plate.
    "PetInfoWindow_3": P(720, 710, 460, 255, show=0),
    "StanceWnd":     P(1188, 970, 440, 56, show=1),
    "CastingWindow": P(1636, 978, 380, 36, show=1),
    "AggroMeterWnd": P(2032, 974, 220, 48),
    "HotButtonWnd4": P(1188, 1032, 528, 56),
    "HotButtonWnd5": P(1724, 1032, 528, 56),
    "HotButtonWnd2": P(1188, 1092, 528, 56),
    "HotButtonWnd3": P(1724, 1092, 528, 56),

    # --- utility hotbars flanking the cluster -------------------------------
    "HotButtonWnd8": P(652, 972, 528, 56),
    "HotButtonWnd7": P(652, 1032, 528, 56),
    "HotButtonWnd6": P(652, 1092, 528, 56),
    "HotButtonWnd9": P(2260, 1092, 528, 56),
    "HotButtonWnd10": P(2260, 1032, 528, 56),

    # --- right column: buffs / songs / group --------------------------------
    # July Legends-native effect rows: icon + readable name, backed by the
    # current 500..529 / 600..614 EQType bindings. Older menu variants remain
    # parked but hidden because they predate the Legends row schema.
    "BuffWindow":                 P(3224, 8, 216, 640, show=1),
    "BuffWindow_13":              P(3224, 8, 216, 640, show=0),
    "ShortDurationBuffWindow":    P(3008, 8, 216, 324, show=1),
    "ShortDurationBuffWindow_13": P(3008, 8, 216, 324, show=0),
    "GroupWindow":             P(3210, 728, show=1),
    "ExtendedTargetWnd":       P(3024, 728, 178, 300),          # Show per base
    # Map: translucent glass, top-right but clear of buffs/songs, so it can
    # stay open while running without hiding the HUD or the world.
    "MapViewWnd":              P(2280, 8, 720, 600,
                                 extra={"Alpha": "235", "FadeToAlpha": "160",
                                        "Fades": "1"}),
    "TargetOfTargetWindow":    P(2296, 616, 240, 53),

    # --- top center / left utility ------------------------------------------
    "CompassWindow": P(1490, 8),
    "TrackingWnd":   P(8, 120, 340, 390),                       # druid/bard tracking

    # --- openable windows ---------------------------------------------------
    # Inventory clears TrackingWnd horizontally and parks 8px above the pet.
    # The bank suite forms a separate center-left workspace beside it.
    "InventoryWindow": P(400, 2),
    "BigBankWnd":      P(1088, 300),
    "BreathWindow":    P(1661, 700),
}

# EQMainWnd: proven right/bottom anchoring from the shipped defaults.
EQMAIN = {"XRef": "right", "YRef": "bottom", "XPos": pct_x(8), "YPos": pct_y(4), "Show": "1"}

# Inventory bags: one tight row in the bottom-right dock.
for i in range(1, 9):
    PLACEMENTS[f"BagInv{i}"] = P(2500 + (i - 1) * 100, 1160, 96, 194)

# Bank bags: 8x2 grid right of the bank window, clear of inventory, the map,
# and the combat cluster.
for i in range(1, 17):
    col, row = (i - 1) % 8, (i - 1) // 8
    PLACEMENTS[f"BagBank{i}"] = P(1384 + col * 100, 300 + row * 204, 96, 194)


def standard_1440_placements() -> dict[str, dict]:
    """Conservative default for 2560x1440 displays.

    It retains the same combat hierarchy as the ultrawide composition but
    pulls the center cluster left, docks buffs against the real right edge,
    and narrows the final utility hotbars so every default-visible window has
    a deliberate non-overlapping home.
    """
    sw, sh = 2560, 1440

    def q(x, y, w=None, h=None, show=None, extra=None):
        return P_for(sw, sh, x, y, w, h, show, extra)

    placements = {
        "MainChat": q(8, CHAT_TOP, 842, 280, extra=CHAT_ALPHA),
        "Chat 1": q(858, CHAT_TOP, 842, 280, extra=CHAT_ALPHA),
        "Chat 2": q(1708, CHAT_TOP, 842, 280, extra=CHAT_ALPHA),
        "Chat 3": q(8, CHAT_TOP, 842, 280, show=0, extra=CHAT_ALPHA),
        "CastSpellWnd": q(8, 521, 52, 623, show=1),
        "HotButtonWnd": q(64, 877, 94, 267),
        "HotButtonWnd11": q(162, 873, 98, 271),
        # Preserve the old plate's right edge while accommodating the taller
        # expanded pet layout without touching PlayerWindow or HotButtonWnd8.
        "PetInfoWindow": q(592, 710, 356, 255),
        "PetInfoWindow_1": q(592, 710, 356, 255, show=0),
        "PetInfoWindow_2": q(592, 710, 356, 255, show=0),
        "PetInfoWindow_3": q(488, 710, 460, 255, show=0),
        # Plates share the hotbar block's outer edges (midpoint 1488).
        "PlayerWindow": q(956, 770, 360, 193, show=1),
        "TargetWindow": q(1660, 770, 360, 193, show=1),
        "StanceWnd": q(956, 970, 440, 56, show=1),
        "CastingWindow": q(1404, 978, 380, 36, show=1),
        "AggroMeterWnd": q(1800, 974, 220, 48),
        "HotButtonWnd4": q(956, 1032, 528, 56),
        "HotButtonWnd5": q(1492, 1032, 528, 56),
        "HotButtonWnd2": q(956, 1092, 528, 56),
        "HotButtonWnd3": q(1492, 1092, 528, 56),
        "HotButtonWnd8": q(420, 972, 528, 56),
        "HotButtonWnd7": q(420, 1032, 528, 56),
        "HotButtonWnd6": q(420, 1092, 528, 56),
        "HotButtonWnd10": q(2028, 1032, 266, 56),
        "HotButtonWnd9": q(2028, 1092, 266, 56),
        "BuffWindow": q(2344, 8, 216, 640, show=1),
        "BuffWindow_13": q(2344, 8, 216, 640, show=0),
        "ShortDurationBuffWindow": q(2128, 8, 216, 324, show=1),
        "ShortDurationBuffWindow_13": q(2128, 8, 216, 324, show=0),
        "GroupWindow": q(2330, 728, show=1),
        "ExtendedTargetWnd": q(2144, 728, 178, 300),
        "MapViewWnd": q(1400, 8, 720, 600,
                        extra={"Alpha": "235", "FadeToAlpha": "160", "Fades": "1"}),
        "TargetOfTargetWindow": q(1904, 616, 240, 53),
        "CompassWindow": q(1050, 8),
        "TrackingWnd": q(8, 120, 340, 390),
        "InventoryWindow": q(300, 140),
        "BigBankWnd": q(900, 330),
        "BreathWindow": q(1221, 700),
    }
    for i in range(1, 9):
        col, row = (i - 1) % 4, (i - 1) // 4
        placements[f"BagInv{i}"] = q(1740 + col * 100, 740 + row * 204, 96, 194)
    for i in range(1, 17):
        col, row = (i - 1) % 8, (i - 1) // 8
        placements[f"BagBank{i}"] = q(1330 + col * 100, 330 + row * 204, 96, 194)
    return placements

def standard_2160_placements() -> dict[str, dict]:
    """Deliberate 3840x2160 (4K) composition.

    Same combat hierarchy as the ultrawide layout: a symmetrical three-pane
    chat row with a bottom-right utility dock, the center cluster perfectly
    centered on x=1920 with the player/target plates sharing the hotbar
    block's outer edges, buffs/songs flush to the real right edge, and the
    glass map clear of both.  Windows keep their native pixel sizes, so on a
    4K panel the HUD reads denser; chat gets the largest client font (see
    CHAT_FONT_2160) to stay comfortably readable.
    """
    sw, sh = 3840, 2160
    chat_top = 1852          # chat row: 1852..2152, 8px bottom margin

    def q(x, y, w=None, h=None, show=None, extra=None):
        return P_for(sw, sh, x, y, w, h, show, extra)

    placements = {
        # symmetrical chat row (three 953px panes), dock at 2892..3832
        "MainChat": q(8, chat_top, 953, 300, extra=CHAT_ALPHA),
        "Chat 1": q(969, chat_top, 953, 300, extra=CHAT_ALPHA),
        "Chat 2": q(1930, chat_top, 953, 300, extra=CHAT_ALPHA),
        "Chat 3": q(8, chat_top, 953, 300, show=0, extra=CHAT_ALPHA),
        "CastSpellWnd": q(8, 1221, 52, 623, show=1),
        "HotButtonWnd": q(64, 1577, 94, 267),
        "HotButtonWnd11": q(162, 1573, 98, 271),
        # Preserve the old plate's right edge while accommodating the taller
        # expanded pet layout without touching PlayerWindow or HotButtonWnd8.
        "PetInfoWindow": q(1024, 1410, 356, 255),
        "PetInfoWindow_1": q(1024, 1410, 356, 255, show=0),
        "PetInfoWindow_2": q(1024, 1410, 356, 255, show=0),
        "PetInfoWindow_3": q(920, 1410, 460, 255, show=0),
        # cluster centered on 1920: block 1388..2452, plates on its edges
        "PlayerWindow": q(1388, 1470, 360, 193, show=1),
        "TargetWindow": q(2092, 1470, 360, 193, show=1),
        "StanceWnd": q(1388, 1670, 440, 56, show=1),
        "CastingWindow": q(1836, 1678, 380, 36, show=1),
        "AggroMeterWnd": q(2232, 1674, 220, 48),
        "HotButtonWnd4": q(1388, 1732, 528, 56),
        "HotButtonWnd5": q(1924, 1732, 528, 56),
        "HotButtonWnd2": q(1388, 1792, 528, 56),
        "HotButtonWnd3": q(1924, 1792, 528, 56),
        "HotButtonWnd8": q(852, 1670, 528, 56),
        "HotButtonWnd7": q(852, 1732, 528, 56),
        "HotButtonWnd6": q(852, 1792, 528, 56),
        "HotButtonWnd9": q(2460, 1792, 528, 56),
        "HotButtonWnd10": q(2460, 1732, 528, 56),
        "BuffWindow": q(3624, 8, 216, 640, show=1),
        "BuffWindow_13": q(3624, 8, 216, 640, show=0),
        "ShortDurationBuffWindow": q(3408, 8, 216, 324, show=1),
        "ShortDurationBuffWindow_13": q(3408, 8, 216, 324, show=0),
        "GroupWindow": q(3610, 1440, show=1),
        "ExtendedTargetWnd": q(3424, 1440, 178, 300),
        "MapViewWnd": q(2680, 8, 720, 600,
                        extra={"Alpha": "235", "FadeToAlpha": "160", "Fades": "1"}),
        "TargetOfTargetWindow": q(2696, 620, 240, 53),
        "CompassWindow": q(1690, 8),
        "TrackingWnd": q(8, 120, 340, 390),
        "InventoryWindow": q(560, 240),
        "BigBankWnd": q(1100, 430),
        "BreathWindow": q(1861, 1050),
    }
    for i in range(1, 9):
        placements[f"BagInv{i}"] = q(2900 + (i - 1) * 100, 1860, 96, 194)
    for i in range(1, 17):
        col, row = (i - 1) % 8, (i - 1) // 8
        placements[f"BagBank{i}"] = q(1530 + col * 100, 430 + row * 204, 96, 194)
    return placements


# Chat font per generated default: 5 reads well at 1440p pixel densities; a
# 4K panel renders the same pixel sizes ~1.5x smaller, so the generated 4K
# default uses the client's largest chat font.
CHAT_FONT_1440 = 5
CHAT_FONT_2160 = 6

# Windows whose Show flag we force on (quality-of-life for a WAR/DRU/BRD).
FORCE_SHOW_NOTES = {
    "BuffWindow": "buff icons visible (ALT+B toggles)",
    "ShortDurationBuffWindow": "song window visible — bard multiclass",
    "CastingWindow": "centered cast bar",
}

# Sections that exist in the defaults but not in the player's file get copied
# from the regenerated default file.
COPY_IF_MISSING = [
    "CastingWindow", "CompassWindow", "AggroMeterWnd", "TargetOfTargetWindow",
    "BreathWindow",
] + [f"BagInv{i}" for i in range(1, 9)] + [f"BagBank{i}" for i in range(1, 17)]


# ---------------------------------------------------------------------------
# Ordered INI parsing that preserves unknown keys and section order
# ---------------------------------------------------------------------------

def parse_ini(text: str):
    sections: list[tuple[str | None, list[list[str]]]] = []
    cur_name, cur_lines = None, []
    for raw in text.splitlines():
        line = raw.rstrip("\r\n")
        if line.startswith("[") and line.endswith("]"):
            sections.append((cur_name, cur_lines))
            cur_name, cur_lines = line[1:-1], []
        else:
            cur_lines.append(line)
    sections.append((cur_name, cur_lines))
    return sections


def set_key(lines: list[str], key: str, value: str) -> None:
    for i, line in enumerate(lines):
        if line.split("=", 1)[0] == key:
            lines[i] = f"{key}={value}"
            return
    # insert before trailing blank lines
    idx = len(lines)
    while idx > 0 and lines[idx - 1] == "":
        idx -= 1
    lines.insert(idx, f"{key}={value}")


def apply_placements(sections, placements, eqmain=None):
    eqmain = eqmain or EQMAIN
    known = {name for name, _ in sections if name}
    for name, lines in sections:
        if name in placements:
            for k, v in placements[name].items():
                if k == "_rect":
                    continue
                set_key(lines, k, v)
        if name == "EQMainWnd":
            for k, v in eqmain.items():
                set_key(lines, k, v)
    # append brand-new sections that the base file lacked entirely
    for name, spec in placements.items():
        if name not in known:
            lines = ["INIVersion=1"]
            for k, v in spec.items():
                if k != "_rect":
                    set_key(lines, k, v)
            sections.append((name, lines))
    return sections


# ---------------------------------------------------------------------------
# Chat manager: 3 visible windows — Main Chat / Combat / Social
# ---------------------------------------------------------------------------

def chat_window_block(idx, container, tab, name, container_name=None,
                      chat_channel="-1", font=CHAT_FONT_1440):
    lines = [
        f"ChatWindow{idx}_ContainerIndex={container}",
        f"ChatWindow{idx}_ContainerTabIndex={tab}",
    ]
    if container_name is not None:
        lines.append(f"ChatWindow{idx}_ContainerName={container_name}")
    lines += [
        f"ChatWindow{idx}_LanguageId=0",
        f"ChatWindow{idx}_DefaultChannel=8",
        f"ChatWindow{idx}_ChatChannel={chat_channel}",
        f"ChatWindow{idx}_TellTarget=",
        f"ChatWindow{idx}_Scrollbar=1",
        f"ChatWindow{idx}_FontStyle={font}",
        f"ChatWindow{idx}_Name={name}",
        f"ChatWindow{idx}_Highlight=1",
        f"ChatWindow{idx}_HighlightColor=-65536",
        f"ChatWindow{idx}_TimestampFormat=0",
        f"ChatWindow{idx}_TimestampMatchChatColor=1",
        f"ChatWindow{idx}_TimestampColor.red=255",
        f"ChatWindow{idx}_TimestampColor.green=255",
        f"ChatWindow{idx}_TimestampColor.blue=255",
    ]
    return lines


# Filters routed to the Social window (window index 2).  These four indices
# have been stable in the client since 2002: 0=Say 1=Tell 2=Group 3=Guild.
SOCIAL_FILTERS = {0: "Say", 1: "Tell", 2: "Group", 3: "Guild"}


def rebuild_chat_manager(lines: list[str], font: int = CHAT_FONT_1440) -> list[str]:
    # keep the existing ChannelMap / HitMode values as the routing base
    channel_map: dict[int, str] = {}
    hit_modes: list[str] = []
    for line in lines:
        if line.startswith("ChannelMap"):
            k, v = line.split("=", 1)
            channel_map[int(k[len("ChannelMap"):])] = v
        elif line.startswith("HitMode"):
            hit_modes.append(line)
    for idx in SOCIAL_FILTERS:
        channel_map[idx] = "2"

    out = [
        "NumWindows=3",
        "NumContainers=3",
        "LockedActiveWindow=-1",
    ]
    out += chat_window_block(0, 0, 0, "Main Chat", "Main Chat",
                             chat_channel="0", font=font)
    out += chat_window_block(1, 2, 0, "Combat", "Combat", font=font)
    out += chat_window_block(2, 1, 0, "Social", "Social", font=font)
    out += [f"ChannelMap{i}={channel_map[i]}" for i in sorted(channel_map)]
    out += hit_modes if hit_modes else [f"HitMode{i}=0" for i in range(8)]
    return out


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

# XML-fixed sizes for windows the INI cannot size (from the window XMLs).
XML_SIZES = {
    # Player/Target keep their full transparent interaction and buff hosts for
    # placement math even though only the compact lower subframes are painted.
    "PlayerWindow": (360, 193), "TargetWindow": (360, 193),
    "PetInfoWindow": (356, 255), "BuffWindow": (216, 640),
    "PetInfoWindow_1": (356, 255), "PetInfoWindow_2": (356, 255),
    "PetInfoWindow_3": (460, 255),
    "BuffWindow_13": (216, 640), "ShortDurationBuffWindow": (216, 324),
    "ShortDurationBuffWindow_13": (216, 324), "BigBankWnd": (287, 390),
    "InventoryWindow": (660, 700), "BreathWindow": (118, 32),
    "GroupWindow": (230, 204),   # four-player Legends group = three companion rows
    "CompassWindow": (460, 36),
}

# Windows visible in the default HUD (Show=1 or always-on) → overlap-checked.
VISIBLE = [
    "MainChat", "Chat 1", "Chat 2",
    "CastSpellWnd", "HotButtonWnd", "HotButtonWnd11",
    "PlayerWindow", "TargetWindow", "StanceWnd", "CastingWindow",
    "HotButtonWnd2", "HotButtonWnd3", "HotButtonWnd4", "HotButtonWnd5",
    "HotButtonWnd6", "HotButtonWnd7", "HotButtonWnd8", "HotButtonWnd9",
    "HotButtonWnd10", "GroupWindow", "BuffWindow", "ShortDurationBuffWindow",
]

# PetInfoWindow is character/state dependent, so its Show flag is preserved.
# Validate its active state independently to guarantee that summoning a pet
# cannot introduce a collision into the otherwise default-visible HUD.
OPTIONAL_VISIBLE = {
    "pet-active": ["PetInfoWindow"],
    "pet-layout-1": ["PetInfoWindow_1"],
    "pet-layout-2": ["PetInfoWindow_2"],
    "pet-right-buffs": ["PetInfoWindow_3"],
}

# Common 3440x1440 toggle/open combinations. These windows are not all shown
# at login, but they are routinely used together and should still compose as
# one intentional workspace when opened.
OPTIONAL_VISIBLE_3440 = {
    "inventory-pet-tracking": [
        "InventoryWindow", "PetInfoWindow", "TrackingWnd",
    ],
    "inventory-pet-right-buffs": [
        "InventoryWindow", "PetInfoWindow_3", "TrackingWnd",
    ],
    "banking": [
        "InventoryWindow", "BigBankWnd",
        *[f"BagBank{i}" for i in range(1, 17)],
    ],
    "right-utilities": [
        "MapViewWnd", "TargetOfTargetWindow", "ExtendedTargetWnd",
    ],
    "inventory-bags": [f"BagInv{i}" for i in range(1, 9)],
}


def rect_of(name):
    x, y, w, h = PLACEMENTS[name]["_rect"]
    if w is None or h is None:
        w, h = XML_SIZES[name]
    return (x, y, x + w, y + h)


def validate_profile(placements, screen_w, screen_h) -> list[str]:
    def profile_rect(name):
        x, y, w, h = placements[name]["_rect"]
        if w is None or h is None:
            w, h = XML_SIZES[name]
        return x, y, x + w, y + h

    problems = []
    for name in placements:
        x0, y0, x1, y1 = profile_rect(name)
        if x0 < 0 or y0 < 0 or x1 > screen_w or y1 > screen_h:
            problems.append(f"OFF-SCREEN {name}: {(x0, y0, x1, y1)}")
    visible = [name for name in VISIBLE if name in placements]
    for i, a in enumerate(visible):
        ax0, ay0, ax1, ay1 = profile_rect(a)
        for b in visible[i + 1:]:
            bx0, by0, bx1, by1 = profile_rect(b)
            if ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1:
                problems.append(f"OVERLAP {a} x {b}")
    optional_states = dict(OPTIONAL_VISIBLE)
    if (screen_w, screen_h) == (SCREEN_W, SCREEN_H):
        optional_states.update(OPTIONAL_VISIBLE_3440)
    for state, optional in optional_states.items():
        state_visible = visible + [name for name in optional if name in placements]
        for i, a in enumerate(state_visible):
            ax0, ay0, ax1, ay1 = profile_rect(a)
            for b in state_visible[i + 1:]:
                # Default/default pairs were already checked above.
                if a in visible and b in visible:
                    continue
                bx0, by0, bx1, by1 = profile_rect(b)
                if ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1:
                    problems.append(f"OVERLAP [{state}] {a} x {b}")
    return problems


def validate() -> list[str]:
    problems = []
    for name in PLACEMENTS:
        x0, y0, x1, y1 = rect_of(name)
        if x0 < 0 or y0 < 0 or x1 > SCREEN_W or y1 > SCREEN_H:
            problems.append(f"OFF-SCREEN {name}: {(x0, y0, x1, y1)}")
    for i, a in enumerate(VISIBLE):
        ax0, ay0, ax1, ay1 = rect_of(a)
        for b in VISIBLE[i + 1:]:
            bx0, by0, bx1, by1 = rect_of(b)
            if ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1:
                problems.append(f"OVERLAP {a} x {b}")
    optional_states = dict(OPTIONAL_VISIBLE)
    optional_states.update(OPTIONAL_VISIBLE_3440)
    for state, optional in optional_states.items():
        state_visible = VISIBLE + optional
        for i, a in enumerate(state_visible):
            ax0, ay0, ax1, ay1 = rect_of(a)
            for b in state_visible[i + 1:]:
                # Default/default pairs were already checked above.
                if a in VISIBLE and b in VISIBLE:
                    continue
                bx0, by0, bx1, by1 = rect_of(b)
                if ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1:
                    problems.append(f"OVERLAP [{state}] {a} x {b}")
    return problems


# ---------------------------------------------------------------------------

def emit(sections) -> str:
    out = []
    for name, lines in sections:
        if name is not None:
            out.append(f"[{name}]")
        out.extend(lines)
    # normalise trailing whitespace: exactly one newline at EOF
    text = "\n".join(out)
    return text.rstrip("\n") + "\n"


def preset_placements(preset: str) -> dict[str, dict]:
    placements = {k: dict(v) for k, v in PLACEMENTS.items()}
    for section, (x, w, h, y) in CHAT_PRESETS[preset].items():
        placements[section] = P(x, y, w, h, extra=CHAT_ALPHA)
    return placements


def personal_placements(preset: str) -> dict[str, dict]:
    """Return the complete, validated 3440x1440 placement for a preset.

    The live profile remains the source for client-specific settings, but its
    older hand-positioned geometry is intentionally replaced. Applying the
    same table that validate_all_presets() checks prevents generated personal
    profiles from silently drifting away from the documented composition.
    """
    return preset_placements(preset)


def transform(text: str, preset: str, placements: dict | None = None,
              eqmain: dict | None = None,
              chat_font: int = CHAT_FONT_1440) -> str:
    sections = parse_ini(text)
    sections = apply_placements(sections, placements or preset_placements(preset), eqmain)
    for name, lines in sections:
        if name == "ChatManager":
            lines[:] = rebuild_chat_manager(lines, chat_font)
        elif name == "Main":
            set_key(lines, "UISkin", "spinui_reloaded")
    out = emit(sections)
    # carry sections the base file lacked over from the placement table is
    # already handled by apply_placements; nothing else to do here.
    return out


def merge_missing(personal: str, default_text: str) -> str:
    have = {name for name, _ in parse_ini(personal) if name}
    default_secs = {name: lines for name, lines in parse_ini(default_text) if name}
    extra = []
    for name in COPY_IF_MISSING:
        if name not in have and name in default_secs:
            extra.append(f"[{name}]")
            extra.extend(default_secs[name])
    if extra:
        personal = personal.rstrip("\n") + "\n" + "\n".join(extra) + "\n"
    return personal


def validate_all_presets() -> None:
    problems = []
    base_placements = {k: dict(v) for k, v in PLACEMENTS.items()}
    try:
        for preset in CHAT_PRESETS:
            placements = preset_placements(preset)
            PLACEMENTS.update(placements)  # rect_of reads PLACEMENTS
            problems += [f"[{preset}] {p}" for p in validate()]
    finally:
        # Validation must not leak the final preset's chat geometry into the
        # subsequent generation pass.
        PLACEMENTS.clear()
        PLACEMENTS.update(base_placements)
    if problems:
        for p in problems:
            print("LAYOUT ERROR:", p)
        raise SystemExit(1)
    print("layout validation: on-screen OK  no HUD overlaps OK  (all presets)")


DEFAULT_PRESET = "combat-focus"

# The player's own in-game file supplies client-specific settings and unknown
# future sections. Geometry and visibility are supplied by the complete,
# validated 3440x1440 preset table above.
PERSONAL_BASE = "layouts/spin-live/UI_Spin_qeynos_LO1.ini"


def main():
    validate_all_presets()

    import shutil

    default_src = _pristine_default1440()
    standard = standard_1440_placements()
    standard_problems = validate_profile(standard, 2560, 1440)
    if standard_problems:
        for problem in standard_problems:
            print("LAYOUT ERROR: [2560x1440]", problem)
        raise SystemExit(1)
    standard_eqmain = {
        "XRef": "right", "YRef": "bottom",
        "XPos": f"{8 / 2560 * 100:.6f}%",
        "YPos": f"{4 / 1440 * 100:.6f}%", "Show": "1",
    }
    new_default = transform(default_src, DEFAULT_PRESET, standard, standard_eqmain)
    (SKIN / "default1440.ini").write_text(new_default)
    print("wrote spinui_reloaded/default1440.ini  (safe 2560x1440 default)")
    print("layout validation: 2560x1440 on-screen OK  no HUD overlaps OK")

    four_k = standard_2160_placements()
    four_k_problems = validate_profile(four_k, 3840, 2160)
    if four_k_problems:
        for problem in four_k_problems:
            print("LAYOUT ERROR: [3840x2160]", problem)
        raise SystemExit(1)
    four_k_eqmain = {
        "XRef": "right", "YRef": "bottom",
        "XPos": f"{8 / 3840 * 100:.6f}%",
        "YPos": f"{4 / 2160 * 100:.6f}%", "Show": "1",
    }
    new_4k = transform(_pristine_default("default4k.ini"), DEFAULT_PRESET,
                       four_k, four_k_eqmain, chat_font=CHAT_FONT_2160)
    (SKIN / "default4k.ini").write_text(new_4k)
    print("wrote spinui_reloaded/default4k.ini  (deliberate 3840x2160 default)")
    print("layout validation: 3840x2160 on-screen OK  no HUD overlaps OK")

    base = (REPO / PERSONAL_BASE).read_text()
    for preset in CHAT_PRESETS:
        out_dir = REPO / "layouts" / preset
        out_dir.mkdir(parents=True, exist_ok=True)
        personal = merge_missing(
            transform(base, preset, personal_placements(preset)), new_default)
        (out_dir / "UI_Spin_qeynos_LO1.ini").write_text(personal)
        print(f"wrote layouts/{preset}/UI_Spin_qeynos_LO1.ini")

    shutil.copyfile(
        REPO / "layouts" / DEFAULT_PRESET / "UI_Spin_qeynos_LO1.ini",
        REPO / "UI_Spin_qeynos_LO1.ini",
    )
    print("wrote UI_Spin_qeynos_LO1.ini  (=%s)" % DEFAULT_PRESET)


def _pristine_default(name: str) -> str:
    """Read a stock default INI from git so reruns stay idempotent."""
    import subprocess
    out = subprocess.run(
        ["git", "-C", str(REPO), "show", f"0eac353:default_modern/{name}"],
        capture_output=True,
    )
    if out.returncode == 0 and out.stdout:
        return out.stdout.decode("utf-8", errors="replace")
    return (SKIN / name).read_text()


def _pristine_default1440() -> str:
    return _pristine_default("default1440.ini")


if __name__ == "__main__":
    main()
