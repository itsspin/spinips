#!/usr/bin/env python3
"""Spin UI — 3440x1440 layout generator.

Produces two files from pixel-exact placement tables:
  * default_modern/default1440.ini   — the skin's default layout at 1440p
  * UI_Spin_qeynos_LO1.ini           — drop-in personal layout for Spin @ qeynos

Both are derived from the shipped default1440.ini / the player's uploaded
UI file, so every key the client expects stays present; only geometry,
visibility and the chat routing are rewritten.

Anchoring: every placed window uses XRef=left / YRef=top with percentages
computed against 3440x1440, which the client resolves back to exact pixels
at that resolution.  EQMainWnd keeps its proven right/bottom anchoring.

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


# Chat-row geometry per preset: (name, x, width, height, y) for the three
# visible containers.  MainChat = Main Chat, Chat 1 = Social, Chat 2 = Combat.
CHAT_PRESETS = {
    # Combat window gets the widest pane.
    "combat-focus": {
        "MainChat": (8, 700, 280, CHAT_TOP),
        "Chat 1": (716, 700, 280, CHAT_TOP),
        "Chat 2": (1424, 1060, 280, CHAT_TOP),
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
    "Chat 3":  P(2492, CHAT_TOP, 700, 280, extra=CHAT_ALPHA),

    # --- left column: spell gems + vertical hotbars -------------------------
    "CastSpellWnd":   P(8, 521, 52, 623, show=1),
    "HotButtonWnd":   P(64, 877, 94, 267),
    "HotButtonWnd11": P(162, 873, 98, 271),

    # --- center combat cluster (above chat) ---------------------------------
    "PlayerWindow":  P(1188, 780, show=1),                      # 336x193 (XML)
    "TargetWindow":  P(1916, 780, show=1),                      # 336x193 (XML)
    "PetInfoWindow": P(864, 780),                               # 311x190, Show per base
    "StanceWnd":     P(1188, 980, 440, 44, show=1),
    "CastingWindow": P(1636, 980, 380, 36, show=1),
    "AggroMeterWnd": P(2032, 976, 220, 48),
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
    # Style: LEFT-anchored static list, no numbering (variant 13) — icons sit
    # beside names with no floating number rail.  Base variants stay hidden
    # but keep the same position in case the player switches styles in-game.
    "BuffWindow":                 P(3232, 8, show=0),           # 200x712 (XML)
    "BuffWindow_13":              P(3232, 8, show=1),
    "ShortDurationBuffWindow":    P(3024, 8, show=0),           # 200x367 (XML)
    "ShortDurationBuffWindow_13": P(3024, 8, show=1),
    "GroupWindow":             P(3202, 728, show=1),
    "ExtendedTargetWnd":       P(3024, 728, 170, 300),          # Show per base
    # Map: translucent glass, top-right but clear of buffs/songs, so it can
    # stay open while running without hiding the HUD or the world.
    "MapViewWnd":              P(2296, 8, 720, 600,
                                 extra={"Alpha": "235", "FadeToAlpha": "160",
                                        "Fades": "1"}),
    "TargetOfTargetWindow":    P(2296, 616, 232, 100),

    # --- top center / left utility ------------------------------------------
    "CompassWindow": P(1490, 8),
    "TrackingWnd":   P(8, 120, 340, 390),                       # druid/bard tracking

    # --- openable windows ---------------------------------------------------
    "InventoryWindow": P(420, 140),
    "BigBankWnd":      P(1000, 330),
    "BreathWindow":    P(1661, 700),
}

# EQMainWnd: proven right/bottom anchoring from the shipped defaults.
EQMAIN = {"XRef": "right", "YRef": "bottom", "XPos": pct_x(8), "YPos": pct_y(4), "Show": "1"}

# Inventory bags: one tight row in the bottom-right dock.
for i in range(1, 9):
    PLACEMENTS[f"BagInv{i}"] = P(2500 + (i - 1) * 100, 1160, 96, 194)

# Bank bags: 8x2 grid right of the bank window, clear of the combat cluster.
for i in range(1, 17):
    col, row = (i - 1) % 8, (i - 1) // 8
    PLACEMENTS[f"BagBank{i}"] = P(1330 + col * 100, 330 + row * 204, 96, 194)

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


def apply_placements(sections, placements):
    known = {name for name, _ in sections if name}
    for name, lines in sections:
        if name in placements:
            for k, v in placements[name].items():
                if k == "_rect":
                    continue
                set_key(lines, k, v)
        if name == "EQMainWnd":
            for k, v in EQMAIN.items():
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
                      chat_channel="-1"):
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
        f"ChatWindow{idx}_FontStyle=5",
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


def rebuild_chat_manager(lines: list[str]) -> list[str]:
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
    out += chat_window_block(0, 0, 0, "Main Chat", "Main Chat", chat_channel="0")
    out += chat_window_block(1, 2, 0, "Combat", "Combat")
    out += chat_window_block(2, 1, 0, "Social", "Social")
    out += [f"ChannelMap{i}={channel_map[i]}" for i in sorted(channel_map)]
    out += hit_modes if hit_modes else [f"HitMode{i}=0" for i in range(8)]
    return out


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

# XML-fixed sizes for windows the INI cannot size (from the window XMLs).
XML_SIZES = {
    "PlayerWindow": (336, 193), "TargetWindow": (336, 193),
    "PetInfoWindow": (311, 190), "BuffWindow": (200, 712),
    "BuffWindow_13": (200, 712), "ShortDurationBuffWindow": (200, 367),
    "ShortDurationBuffWindow_13": (200, 367), "BigBankWnd": (287, 390),
    "InventoryWindow": (720, 800), "BreathWindow": (118, 32),
    "GroupWindow": (230, 430),   # grows downward; reserve
    "CompassWindow": (460, 36),
}

# Windows visible in the default HUD (Show=1 or always-on) → overlap-checked.
VISIBLE = [
    "MainChat", "Chat 1", "Chat 2",
    "CastSpellWnd", "HotButtonWnd", "HotButtonWnd11",
    "PlayerWindow", "TargetWindow", "StanceWnd", "CastingWindow",
    "HotButtonWnd2", "HotButtonWnd3", "HotButtonWnd4", "HotButtonWnd5",
    "HotButtonWnd6", "HotButtonWnd7", "HotButtonWnd8", "HotButtonWnd9",
    "HotButtonWnd10", "BuffWindow_13", "ShortDurationBuffWindow_13", "GroupWindow",
]


def rect_of(name):
    x, y, w, h = PLACEMENTS[name]["_rect"]
    if w is None or h is None:
        w, h = XML_SIZES[name]
    return (x, y, x + w, y + h)


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


def transform(text: str, preset: str) -> str:
    sections = parse_ini(text)
    sections = apply_placements(sections, preset_placements(preset))
    for name, lines in sections:
        if name == "ChatManager":
            lines[:] = rebuild_chat_manager(lines)
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
    base_rects = {k: v["_rect"] for k, v in PLACEMENTS.items()}
    for preset in CHAT_PRESETS:
        placements = preset_placements(preset)
        PLACEMENTS.update(placements)  # rect_of reads PLACEMENTS
        problems += [f"[{preset}] {p}" for p in validate()]
    # restore
    for k, r in base_rects.items():
        PLACEMENTS[k]["_rect"] = r
    if problems:
        for p in problems:
            print("LAYOUT ERROR:", p)
        raise SystemExit(1)
    print("layout validation: on-screen ✓  no HUD overlaps ✓  (all presets)")


DEFAULT_PRESET = "combat-focus"


def main():
    validate_all_presets()

    import shutil

    default_src = _pristine_default1440()
    new_default = transform(default_src, DEFAULT_PRESET)
    (SKIN / "default1440.ini").write_text(new_default)
    print("wrote spinui_reloaded/default1440.ini  (%s)" % DEFAULT_PRESET)

    original = (REPO / "layouts" / "original" / "UI_Spin_qeynos_LO1.ini").read_text()
    for preset in CHAT_PRESETS:
        out_dir = REPO / "layouts" / preset
        out_dir.mkdir(parents=True, exist_ok=True)
        personal = merge_missing(transform(original, preset), new_default)
        (out_dir / "UI_Spin_qeynos_LO1.ini").write_text(personal)
        print(f"wrote layouts/{preset}/UI_Spin_qeynos_LO1.ini")

    shutil.copyfile(
        REPO / "layouts" / DEFAULT_PRESET / "UI_Spin_qeynos_LO1.ini",
        REPO / "UI_Spin_qeynos_LO1.ini",
    )
    print("wrote UI_Spin_qeynos_LO1.ini  (=%s)" % DEFAULT_PRESET)


def _pristine_default1440() -> str:
    """Read the stock default1440.ini from git so reruns stay idempotent."""
    import subprocess
    out = subprocess.run(
        ["git", "-C", str(REPO), "show", "0eac353:default_modern/default1440.ini"],
        capture_output=True,
    )
    if out.returncode == 0 and out.stdout:
        return out.stdout.decode("utf-8", errors="replace")
    return (SKIN / "default1440.ini").read_text()


if __name__ == "__main__":
    main()
