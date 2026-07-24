#!/usr/bin/env python3
"""SpinUI resolution-aware layout generator.

Produces screenshot-matched defaults plus every resolution/chat profile:
  * spinui_reloaded/default1080.ini, default1440.ini, default4k.ini
  * layouts/profiles/<resolution>/<preset>/UI_Spin_qeynos_LO1.ini
  * UI_Spin_qeynos_LO1.ini — 3440x1440 Combat Focus compatibility alias

Both are derived from the shipped default1440.ini / the player's uploaded
UI file, so every key the client expects stays present; only geometry,
visibility and the chat routing are rewritten.

Every profile preserves native control sizes and the submitted 3440x1440
hierarchy while recalculating chat widths, docks, and combat geometry.

The script validates: every placed window fully on-screen, and no two
default-visible windows overlap.

Run from repo root:  python3 tools/generate_spinui_layout.py
"""

from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKIN = REPO / "spinui_reloaded"
SCREEN_W, SCREEN_H = 3440, 1440

CHAT_TOP = 1146          # live screenshot: 1146..1426, 14px bottom margin
DOCK_X = 2492            # bottom-right utility dock (bags / SpinBuddy)


@dataclass(frozen=True)
class ResolutionProfile:
    key: str
    width: int
    height: int
    label: str


DEFAULT_RESOLUTION_PROFILE = "3440x1440"
RESOLUTION_PROFILES: dict[str, ResolutionProfile] = {
    "1920x1080": ResolutionProfile(
        "1920x1080", 1920, 1080, "1920×1080 · Full HD"),
    "2048x1080": ResolutionProfile(
        "2048x1080", 2048, 1080, "2048×1080 · Wide Full HD"),
    "2560x1080": ResolutionProfile(
        "2560x1080", 2560, 1080, "2560×1080 · Ultrawide"),
    "2560x1440": ResolutionProfile(
        "2560x1440", 2560, 1440, "2560×1440 · QHD"),
    "3440x1440": ResolutionProfile(
        "3440x1440", 3440, 1440, "3440×1440 · Ultrawide QHD"),
    "3840x1600": ResolutionProfile(
        "3840x1600", 3840, 1600, "3840×1600 · Ultrawide"),
    "3840x2160": ResolutionProfile(
        "3840x2160", 3840, 2160, "3840×2160 · 4K"),
}


def recommended_profile(width: int, height: int) -> ResolutionProfile:
    """Return the closest deliberate profile, prioritizing aspect and height."""
    exact = RESOLUTION_PROFILES.get(f"{width}x{height}")
    if exact is not None:
        return exact
    aspect = width / max(1, height)

    def score(profile: ResolutionProfile) -> float:
        profile_aspect = profile.width / profile.height
        return (
            abs(aspect - profile_aspect) * 3
            + abs(height - profile.height) / max(height, profile.height) * 2
            + abs(width - profile.width) / max(width, profile.width)
        )

    return min(RESOLUTION_PROFILES.values(), key=score)


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
    # Matches the submitted live screenshot: Main and Social are compact,
    # while Combat receives the larger pane.
    "combat-focus": {
        "MainChat": (8, 700, 280, CHAT_TOP),
        "Chat 1": (713, 700, 280, CHAT_TOP),
        "Chat 2": (1421, 1060, 280, CHAT_TOP),
    },
    # Social pane dominates; combat stays readable.
    "social-focus": {
        "MainChat": (8, 620, 280, CHAT_TOP),
        "Chat 1": (633, 1120, 280, CHAT_TOP),
        "Chat 2": (1761, 720, 280, CHAT_TOP),
    },
    # Hybrid: big main + social, small self-combat ticker bottom-aligned.
    "hybrid": {
        "MainChat": (8, 820, 280, CHAT_TOP),
        "Chat 1": (833, 1000, 280, CHAT_TOP),
        "Chat 2": (1841, 640, 200, CHAT_TOP + 80),
    },
}

CHAT_ALPHA = {"Alpha": "235", "FadeToAlpha": "150", "Fades": "1"}

PLACEMENTS: dict[str, dict] = {
    # --- chat row (geometry filled in per preset) ---------------------------
    "MainChat": P(8, CHAT_TOP, 700, 280, extra=CHAT_ALPHA),
    "Chat 1":  P(713, CHAT_TOP, 700, 280, extra=CHAT_ALPHA),
    "Chat 2":  P(1421, CHAT_TOP, 1060, 280, extra=CHAT_ALPHA),
    # Legacy fourth container is explicitly parked and hidden; the active
    # three-pane ChatManager ends at x=2484, leaving the bag dock unobstructed.
    "Chat 3":  P(2486, CHAT_TOP, 700, 280, show=0, extra=CHAT_ALPHA),

    # --- screenshot-faithful horizontal spell and two 6x2 hotbars -----------
    "CastSpellWnd":   P(1410, 1094, 595, 48, show=1),
    "HotButtonWnd":   P(1132, 1048, 272, 93, show=1),
    "HotButtonWnd11": P(162, 873, 98, 271, show=0),

    # --- center combat cluster (above chat) ---------------------------------
    # Player and Target plates share the hotbar block's outer edges, so the
    # pair is perfectly centered over the rows beneath (block midpoint 1720).
    "PlayerWindow":  P(1170, 771, 360, 193, show=1),
    "TargetWindow":  P(1898, 771, 360, 193, show=1),
    # Every pet layout preserves the historical x=1180 right edge and y=965
    # bottom baseline: 8px before PlayerWindow and 7px above the hotbar row.
    # The fixed default spends ultrawide width on a full-capacity effect rail
    # instead of stacking a dark tray overhead.
    "PetInfoWindow": P(851, 582, 513, 181, show=0),
    "PetInfoWindow_1": P(1008, 558, 356, 209, show=0),
    "PetInfoWindow_2": P(1008, 558, 356, 209, show=0),
    "PetInfoWindow_3": P(923, 586, 441, 181, show=0),
    "StanceWnd":     P(1489, 1027, 440, 66, show=1),
    "CastingWindow": P(1557, 966, 310, 46, show=1),
    "AggroMeterWnd": P(2032, 976, 220, 48, show=0),
    "HotButtonWnd4": P(1188, 1032, 528, 56, show=0),
    "HotButtonWnd5": P(1724, 1032, 528, 56, show=0),
    "HotButtonWnd2": P(2009, 1048, 272, 93, show=1),
    "HotButtonWnd3": P(1724, 1092, 528, 56, show=0),

    # --- utility hotbars flanking the cluster -------------------------------
    "HotButtonWnd8": P(652, 972, 528, 56, show=0),
    "HotButtonWnd7": P(652, 1032, 528, 56, show=0),
    "HotButtonWnd6": P(652, 1092, 528, 56, show=0),
    "HotButtonWnd9": P(2260, 1092, 528, 56, show=0),
    "HotButtonWnd10": P(2260, 1032, 528, 56, show=0),

    # --- right column: buffs / songs / group --------------------------------
    # July Legends-native effect rows: icon + readable name, backed by the
    # current 500..529 / 600..614 EQType bindings. Older menu variants remain
    # parked but hidden because they predate the Legends row schema.
    "BuffWindow":                 P(3224, 8, 216, 640, show=1),
    "BuffWindow_13":              P(3224, 8, 216, 640, show=0),
    "ShortDurationBuffWindow":    P(3008, 8, 216, 324, show=1),
    "ShortDurationBuffWindow_13": P(3008, 8, 216, 324, show=0),
    "GroupWindow":             P(3133, 656, show=1),
    "ExtendedTargetWnd":       P(2946, 656, 170, 300, show=0),
    # Map: translucent glass, top-right but clear of buffs/songs, so it can
    # stay open while running without hiding the HUD or the world.
    "MapViewWnd":              P(2360, 8, 640, 520,
                                 extra={"Alpha": "235", "FadeToAlpha": "160",
                                        "Fades": "1"}),
    "TargetOfTargetWindow":    P(2360, 536, 232, 100),

    # --- top center / left utility ------------------------------------------
    "CompassWindow": P(1490, 8),
    "TrackingWnd":   P(8, 120, 340, 390),                       # druid/bard tracking

    # --- openable windows ---------------------------------------------------
    # Inventory clears TrackingWnd horizontally and parks 8px above the pet.
    # The bank suite forms a separate center-left workspace beside it.
    "InventoryWindow": P(175, 203),
    "BigBankWnd":      P(1000, 330),
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
    PLACEMENTS[f"BagBank{i}"] = P(1330 + col * 100, 330 + row * 204, 96, 194)


CHAT_WEIGHTS = {
    "combat-focus": (700, 700, 1060),
    "social-focus": (620, 1120, 720),
    "hybrid": (820, 1000, 640),
}


def _adaptive_chat_geometry(profile: ResolutionProfile, preset: str):
    sw, sh = profile.width, profile.height
    chat_height = 200 if sh <= 1080 else 300 if sh >= 2000 else 280
    chat_top = sh - chat_height - (14 if sh == 1440 else 8)
    aspect = sw / sh
    # Reserve the lower-right command strip even on 16:9 screens. Ultrawide
    # profiles expand that dock for bags and utilities, matching the live HUD.
    dock_width = 400
    if sw >= 3000:
        dock_width = 951
    elif aspect >= 2.2:
        dock_width = round(sw * 0.275)
    usable = sw - 16 - dock_width
    content = usable - 16
    weights = CHAT_WEIGHTS[preset]
    total = sum(weights)
    widths = [
        round(content * weights[0] / total),
        round(content * weights[1] / total),
    ]
    widths.append(content - sum(widths))
    third_height = 160 if preset == "hybrid" and sh <= 1080 else (
        200 if preset == "hybrid" else chat_height)
    x0 = 8
    x1 = x0 + widths[0] + 8
    x2 = x1 + widths[1] + 8
    return {
        "MainChat": (x0, widths[0], chat_height, chat_top),
        "Chat 1": (x1, widths[1], chat_height, chat_top),
        "Chat 2": (
            x2, widths[2], third_height,
            chat_top + chat_height - third_height,
        ),
    }


def adaptive_placements(profile: ResolutionProfile, preset: str) -> dict[str, dict]:
    """Preserve the 3440 live composition at another supported resolution."""
    if profile.key == DEFAULT_RESOLUTION_PROFILE:
        return preset_placements(preset)
    if preset not in CHAT_PRESETS:
        raise ValueError(f"unknown chat preset: {preset}")
    sw, sh = profile.width, profile.height

    def q(x, y, w=None, h=None, show=None, extra=None):
        return P_for(sw, sh, x, y, w, h, show, extra)

    chat = _adaptive_chat_geometry(profile, preset)
    chat_top = chat["MainChat"][3]
    center = min(sw // 2, sw - 978)
    player_x = center - 550
    target_x = center + 178
    player_y = chat_top - 375
    group_y = max(656, chat_top - 502)
    inventory_x = max(8, round(sw * 0.0509))
    inventory_y = min(round(sh * 0.141), chat_top - 676)
    pet_x = min(sw - 521, inventory_x + 676)
    pet_y = max(8, player_y - 189)
    bank_y = max(80, min(round(sh * 0.23), chat_top - 406))

    placements = {
        "Chat 3": q(8, chat_top, chat["MainChat"][1],
                    chat["MainChat"][2], show=0, extra=CHAT_ALPHA),
        "CastSpellWnd": q(center - 310, chat_top - 52, 595, 48, show=1),
        "HotButtonWnd": q(center - 588, chat_top - 98, 272, 93, show=1),
        "HotButtonWnd2": q(center + 289, chat_top - 98, 272, 93, show=1),
        "HotButtonWnd11": q(64, max(8, chat_top - 273), 98, 271, show=0),
        "PlayerWindow": q(player_x, player_y, 360, 193, show=1),
        "TargetWindow": q(target_x, player_y, 360, 193, show=1),
        "PetInfoWindow": q(pet_x, pet_y, 513, 181, show=0),
        "PetInfoWindow_1": q(pet_x + 157, pet_y - 24,
                             356, 209, show=0),
        "PetInfoWindow_2": q(pet_x + 157, pet_y - 24,
                             356, 209, show=0),
        "PetInfoWindow_3": q(pet_x + 72, pet_y + 4,
                             441, 181, show=0),
        "StanceWnd": q(center - 231, chat_top - 119, 440, 66, show=1),
        "CastingWindow": q(center - 163, chat_top - 180, 310, 46, show=1),
        "AggroMeterWnd": q(center + 312, chat_top - 170, 220, 48, show=0),
        "HotButtonWnd4": q(max(8, center - 532), chat_top - 114,
                           528, 56, show=0),
        "HotButtonWnd5": q(min(sw - 536, center + 4), chat_top - 114,
                           528, 56, show=0),
        "HotButtonWnd3": q(min(sw - 536, center + 4), chat_top - 56,
                           528, 56, show=0),
        "HotButtonWnd8": q(max(8, center - 1068), chat_top - 174,
                           528, 56, show=0),
        "HotButtonWnd7": q(max(8, center - 1068), chat_top - 114,
                           528, 56, show=0),
        "HotButtonWnd6": q(max(8, center - 1068), chat_top - 56,
                           528, 56, show=0),
        "HotButtonWnd10": q(min(sw - 536, center + 540), chat_top - 114,
                            528, 56, show=0),
        "HotButtonWnd9": q(min(sw - 536, center + 540), chat_top - 56,
                           528, 56, show=0),
        "BuffWindow": q(sw - 216, 8, 216, 640, show=1),
        "BuffWindow_13": q(sw - 216, 8, 216, 640, show=0),
        "ShortDurationBuffWindow": q(sw - 432, 8, 216, 324, show=1),
        "ShortDurationBuffWindow_13": q(sw - 432, 8, 216, 324, show=0),
        "GroupWindow": q(sw - 307, group_y, show=1),
        "ExtendedTargetWnd": q(sw - 494, group_y, 170, 300, show=0),
        "MapViewWnd": q(max(8, sw - 1080), 8, 640, 520,
                        extra={"Alpha": "235", "FadeToAlpha": "160", "Fades": "1"}),
        "TargetOfTargetWindow": q(max(8, sw - 1080), 536, 232, 100),
        "CompassWindow": q(center - 230, 8),
        "TrackingWnd": q(8, 120, 340, 390),
        "InventoryWindow": q(inventory_x, inventory_y),
        "BigBankWnd": q(max(8, center - 720), bank_y),
        "BreathWindow": q(center - 59, max(8, player_y - 71)),
    }
    for name, (x, width, height, y) in chat.items():
        placements[name] = q(x, y, width, height, extra=CHAT_ALPHA)

    if sw >= 3000 or sw / sh >= 2.2:
        bag_x, bag_y, columns = sw - 940, chat_top + 14, 8
    else:
        bag_x, bag_y, columns = min(sw - 404, inventory_x + 668), inventory_y, 4
    for i in range(1, 9):
        col, row = (i - 1) % columns, (i - 1) // columns
        placements[f"BagInv{i}"] = q(
            bag_x + col * 100, bag_y + row * 204, 96, 194)

    bank_bag_x = max(8, min(sw - 804, center - 400))
    for i in range(1, 17):
        col, row = (i - 1) % 8, (i - 1) // 8
        placements[f"BagBank{i}"] = q(
            bank_bag_x + col * 100, bank_y + row * 204, 96, 194)
    return placements


def profile_placements(profile_key: str, preset: str) -> dict[str, dict]:
    try:
        profile = RESOLUTION_PROFILES[profile_key]
    except KeyError as exc:
        raise ValueError(f"unknown resolution profile: {profile_key}") from exc
    return adaptive_placements(profile, preset)


def profile_eqmain(profile_key: str) -> dict[str, str]:
    try:
        profile = RESOLUTION_PROFILES[profile_key]
    except KeyError as exc:
        raise ValueError(f"unknown resolution profile: {profile_key}") from exc
    return {
        "XRef": "right",
        "YRef": "bottom",
        "XPos": f"{8 / profile.width * 100:.6f}%",
        "YPos": f"{4 / profile.height * 100:.6f}%",
        "Show": "1",
    }


def standard_1440_placements() -> dict[str, dict]:
    return profile_placements("2560x1440", DEFAULT_PRESET)


def standard_2160_placements() -> dict[str, dict]:
    return profile_placements("3840x2160", DEFAULT_PRESET)


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
    "PetInfoWindow": (513, 181), "BuffWindow": (216, 640),
    "PetInfoWindow_1": (356, 209), "PetInfoWindow_2": (356, 209),
    "PetInfoWindow_3": (441, 181),
    "BuffWindow_13": (216, 640), "ShortDurationBuffWindow": (216, 324),
    "ShortDurationBuffWindow_13": (216, 324), "BigBankWnd": (287, 390),
    "InventoryWindow": (660, 668), "BreathWindow": (118, 32),
    "GroupWindow": (230, 204),   # four-player Legends group = three companion rows
    "CompassWindow": (460, 36),
}

# Windows visible in the default HUD (Show=1 or always-on) → overlap-checked.
VISIBLE = [
    "MainChat", "Chat 1", "Chat 2",
    "CastSpellWnd", "HotButtonWnd",
    "PlayerWindow", "TargetWindow", "StanceWnd", "CastingWindow",
    "HotButtonWnd2", "GroupWindow", "BuffWindow", "ShortDurationBuffWindow",
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
              chat_font: int = CHAT_FONT_1440,
              skin_name: str = "spinui_reloaded",
              rebuild_chat: bool = True) -> str:
    sections = parse_ini(text)
    sections = apply_placements(sections, placements or preset_placements(preset), eqmain)
    for name, lines in sections:
        if name == "ChatManager" and rebuild_chat:
            lines[:] = rebuild_chat_manager(lines, chat_font)
        elif name == "Main":
            set_key(lines, "UISkin", skin_name)
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
    for profile in RESOLUTION_PROFILES.values():
        for preset in CHAT_PRESETS:
            placements = profile_placements(profile.key, preset)
            problems += [
                f"[{profile.key}/{preset}] {problem}"
                for problem in validate_profile(
                    placements, profile.width, profile.height)
            ]
    if problems:
        for p in problems:
            print("LAYOUT ERROR:", p)
        raise SystemExit(1)
    print(
        "layout validation: on-screen OK  no HUD overlaps OK  "
        f"({len(RESOLUTION_PROFILES)} resolutions × {len(CHAT_PRESETS)} presets)"
    )


DEFAULT_PRESET = "combat-focus"

# The player's own in-game file supplies client-specific settings and unknown
# future sections. Geometry and visibility are supplied by the complete,
# validated 3440x1440 preset table above.
PERSONAL_BASE = "layouts/spin-live/UI_Spin_qeynos_LO1.ini"


def main():
    validate_all_presets()

    import shutil

    generated_defaults: dict[str, str] = {}
    default_targets = {
        "1920x1080": "default1080.ini",
        "2560x1440": "default1440.ini",
        "3840x2160": "default4k.ini",
    }
    for profile_key, filename in default_targets.items():
        profile = RESOLUTION_PROFILES[profile_key]
        placements = profile_placements(profile_key, DEFAULT_PRESET)
        rendered = transform(
            _pristine_default(filename),
            DEFAULT_PRESET,
            placements,
            profile_eqmain(profile_key),
            chat_font=(
                CHAT_FONT_2160 if profile.height >= 2000 else CHAT_FONT_1440),
        )
        (SKIN / filename).write_text(rendered)
        generated_defaults[profile_key] = rendered
        print(
            f"wrote spinui_reloaded/{filename}  "
            f"({profile.label} screenshot-matched default)"
        )

    base = (REPO / PERSONAL_BASE).read_text()
    for profile in RESOLUTION_PROFILES.values():
        if profile.height <= 1080:
            default_text = generated_defaults["1920x1080"]
        elif profile.height >= 2000:
            default_text = generated_defaults["3840x2160"]
        else:
            default_text = generated_defaults["2560x1440"]
        for preset in CHAT_PRESETS:
            personal = merge_missing(
                transform(
                    base,
                    preset,
                    profile_placements(profile.key, preset),
                    profile_eqmain(profile.key),
                    chat_font=(
                        CHAT_FONT_2160
                        if profile.height >= 2000 else CHAT_FONT_1440),
                ),
                default_text,
            )
            profile_dir = REPO / "layouts" / "profiles" / profile.key / preset
            profile_dir.mkdir(parents=True, exist_ok=True)
            (profile_dir / "UI_Spin_qeynos_LO1.ini").write_text(personal)
            print(
                f"wrote layouts/profiles/{profile.key}/{preset}/"
                "UI_Spin_qeynos_LO1.ini"
            )
            if profile.key == DEFAULT_RESOLUTION_PROFILE:
                compatibility_dir = REPO / "layouts" / preset
                compatibility_dir.mkdir(parents=True, exist_ok=True)
                (compatibility_dir / "UI_Spin_qeynos_LO1.ini").write_text(personal)

    shutil.copyfile(
        REPO / "layouts" / "profiles" / DEFAULT_RESOLUTION_PROFILE
        / DEFAULT_PRESET / "UI_Spin_qeynos_LO1.ini",
        REPO / "UI_Spin_qeynos_LO1.ini",
    )
    print(
        "wrote UI_Spin_qeynos_LO1.ini  "
        f"(={DEFAULT_RESOLUTION_PROFILE}/{DEFAULT_PRESET})"
    )


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
