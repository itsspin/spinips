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
    "PlayerWindow":  P(1188, 770, show=1),                      # 360x193 (XML)
    "TargetWindow":  P(1916, 770, show=1),                      # 360x193 (XML)
    "PetInfoWindow": P(864, 770),                               # 311x190, Show per base
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
    "BuffWindow":                 P(3224, 8, show=1),           # 216x640 (XML)
    "BuffWindow_13":              P(3224, 8, show=0),
    "ShortDurationBuffWindow":    P(3008, 8, show=1),           # 216x324 (XML)
    "ShortDurationBuffWindow_13": P(3008, 8, show=0),
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
        "MainChat": q(8, CHAT_TOP, 700, 280, extra=CHAT_ALPHA),
        "Chat 1": q(716, CHAT_TOP, 700, 280, extra=CHAT_ALPHA),
        "Chat 2": q(1424, CHAT_TOP, 1128, 280, extra=CHAT_ALPHA),
        "Chat 3": q(8, CHAT_TOP, 700, 280, show=0, extra=CHAT_ALPHA),
        "CastSpellWnd": q(8, 521, 52, 623, show=1),
        "HotButtonWnd": q(64, 877, 94, 267),
        "HotButtonWnd11": q(162, 873, 98, 271),
        "PetInfoWindow": q(632, 770),
        "PlayerWindow": q(956, 770, show=1),
        "TargetWindow": q(1684, 770, show=1),
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
        "BuffWindow": q(2344, 8, show=1),
        "BuffWindow_13": q(2344, 8, show=0),
        "ShortDurationBuffWindow": q(2128, 8, show=1),
        "ShortDurationBuffWindow_13": q(2128, 8, show=0),
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
    # Player/Target keep their full transparent interaction and buff hosts for
    # placement math even though only the compact lower subframes are painted.
    "PlayerWindow": (360, 193), "TargetWindow": (360, 193),
    "PetInfoWindow": (311, 190), "BuffWindow": (216, 640),
    "BuffWindow_13": (216, 640), "ShortDurationBuffWindow": (216, 324),
    "ShortDurationBuffWindow_13": (216, 324), "BigBankWnd": (287, 390),
    "InventoryWindow": (780, 800), "BreathWindow": (118, 32),
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
    """Minimal overlay for the player's live layout: safe fixes only, and
    chat geometry only for the non-default presets (combat-focus keeps the
    player's own chat row exactly as they arranged it)."""
    placements = {k: dict(PLACEMENTS[k]) for k in PERSONAL_FIXES}
    if preset != DEFAULT_PRESET:
        for section, (x, w, h, y) in CHAT_PRESETS[preset].items():
            placements[section] = P(x, y, w, h, extra=CHAT_ALPHA)
    return placements


def transform(text: str, preset: str, placements: dict | None = None,
              eqmain: dict | None = None) -> str:
    sections = parse_ini(text)
    sections = apply_placements(sections, placements or preset_placements(preset), eqmain)
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
    print("layout validation: on-screen OK  no HUD overlaps OK  (all presets)")


DEFAULT_PRESET = "combat-focus"

# The player's own in-game arrangement is the source of truth for the
# personal files.  Only objectively-safe improvements are overlaid on it;
# everything they placed by hand is preserved verbatim.
PERSONAL_BASE = "layouts/spin-live/UI_Spin_qeynos_LO1.ini"
PERSONAL_FIXES = [
    "MapViewWnd",              # bigger, clearer glass map
    "TargetOfTargetWindow",    # tidy parking spot (hidden)
    "ExtendedTargetWnd",       # tidy parking spot (hidden)
    "BuffWindow", "BuffWindow_13",
    "ShortDurationBuffWindow", "ShortDurationBuffWindow_13",
]


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
