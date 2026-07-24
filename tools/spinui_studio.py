#!/usr/bin/env python3
"""Offline visual editor and custom-theme builder for SpinUI.

SpinUI Studio edits the same pixel geometry that is written to EverQuest's
character INI and previews it with the repository's real window textures.
Live names, gauges, buffs, inventory contents, and chat lines are deterministic
sample data because only eqgame.exe can supply runtime state.

Run from a source checkout:
    python tools/spinui_studio.py

Useful non-GUI checks:
    python tools/spinui_studio.py --selftest
    python tools/spinui_studio.py --render-preview studio-preview.png
"""

from __future__ import annotations

import argparse
import configparser
import json
import os
import queue
import re
import shutil
import sys
import tempfile
import threading
import traceback
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFilter


TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import generate_spinui_layout as layout  # noqa: E402
import generate_spinui_textures as texture_builder  # noqa: E402
import render_preview as preview  # noqa: E402
from spinui_theme import (DEFAULT_ACCENTS, accent_palette, hex_from_rgb,  # noqa: E402
                          palette_from_hex)


APP_NAME = "SpinUI Studio"
PROJECT_SCHEMA = 2
DEFAULT_SCREEN = (3440, 1440)
# Every supported canvas maps to an audited placement table in
# generate_spinui_layout, so the offline composition is the same geometry the
# release ships for that game resolution.
RESOLUTIONS = {
    (3440, 1440): "3440 × 1440 · ultrawide",
    (2560, 1440): "2560 × 1440 · standard",
    (3840, 2160): "3840 × 2160 · 4K",
}
RESOLUTION_BY_LABEL = {label: size for size, label in RESOLUTIONS.items()}
# Presentation keys the audited tables force onto fresh presets. When the
# base INI is the player's own imported file these stay untouched so exported
# transparency/fade choices survive exactly.
STYLE_KEYS = ("Alpha", "FadeToAlpha", "Fades")
DEFAULT_SKIN_NAME = "spinui_custom"
DEFAULT_INI_NAME = "UI_Spin_qeynos_LO1.ini"
CUSTOM_PRESET_LABEL = "custom / imported INI"
LOAD_PRESERVE = "Preserve current INI"
LOAD_SHOW = "Show when UI loads"
LOAD_HIDE = "Hide when UI loads"

BG = "#090c11"
PANEL = "#10161d"
RAISED = "#17222a"
LINE = "#303f4e"
GOLD = "#db9e2a"
GOLD_BRIGHT = "#facd5f"
CYAN = "#34dabe"
TEXT = "#eef2f3"
DIM = "#92a1a9"
EMBER = "#e5642d"


def release_root() -> Path:
    """Return the unpacked release folder or source checkout."""
    if getattr(sys, "frozen", False):
        executable_root = Path(sys.executable).resolve().parent
        # A packaged release keeps the assets beside the executable. Local
        # PyInstaller builds live in source_root/dist, and CI smoke tests run
        # them with source_root as the working directory.
        for candidate in (
                executable_root, executable_root.parent, Path.cwd().resolve()):
            if (candidate / "spinui_reloaded" / "EQUI.xml").is_file():
                return candidate
        return executable_root
    return TOOLS.parent


def safe_skin_name(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{2,47}", value):
        raise ValueError(
            "Skin name must be 3–48 characters, begin with a letter, and use "
            "letters, numbers, underscores, or hyphens."
        )
    return value


def safe_ini_name(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"UI_[A-Za-z]{1,32}_[a-z]{2,24}_LO[1-9][0-9]?\.ini", value):
        raise ValueError(
            "INI name must look like UI_Character_server_LO1.ini using letters only "
            "for the character and server."
        )
    return value


def discover_character_inis(extra_roots: Iterable[Path] = ()) -> list[Path]:
    """Find live character UI files without walking unrelated user folders."""
    candidates = [
        Path(r"C:\EQLegends"),
        Path(r"C:\Users\Public\Daybreak Game Company\Installed Games\EverQuest Legends"),
        Path(r"C:\Program Files (x86)\Steam\steamapps\common\EverQuest"),
        *extra_roots,
    ]
    seen: set[Path] = set()
    found: list[Path] = []
    pattern = re.compile(
        r"UI_[A-Za-z]{1,32}_[a-z]{2,24}_LO[1-9][0-9]?\.ini")
    for root in candidates:
        try:
            resolved = root.resolve()
        except OSError:
            continue
        if resolved in seen or not (resolved / "eqgame.exe").is_file():
            continue
        seen.add(resolved)
        try:
            for path in resolved.glob("UI_*_*_LO*.ini"):
                if path.is_file() and pattern.fullmatch(path.name):
                    found.append(path)
        except OSError:
            continue
    return sorted(
        found,
        key=lambda path: path.stat().st_mtime if path.exists() else 0,
        reverse=True,
    )


def write_crash_log(message: str) -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()))
    folder = root / "SpinUIStudio"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "spinui-studio.log"
    stamp = datetime.now().isoformat(timespec="seconds")
    with path.open("a", encoding="utf-8") as stream:
        stream.write(f"\n[{stamp}] {message.rstrip()}\n")
    return path


def percent(value: float, total: int) -> str:
    return f"{value / total * 100:.6f}%"


def parse_percent(value: str) -> float:
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)%", value.strip()):
        raise ValueError(f"invalid percentage: {value!r}")
    return float(value.strip()[:-1]) / 100.0


def reference_pixel(ref: str, fraction: float, total: int, size: int) -> int:
    """Resolve the three EQ INI anchors into a top/left pixel coordinate."""
    ref = ref.casefold()
    if ref in {"left", "top"}:
        return round(fraction * total)
    if ref in {"right", "bottom"}:
        return round(total - fraction * total - size)
    if ref == "center":
        # EverQuest measures center-relative percentages across one half of
        # the viewport, then anchors the window by its own center. Using the
        # full viewport here shifts imported center-anchored windows by
        # hundreds of pixels at 3440x1440.
        return round(total / 2 + fraction * (total / 2) - size / 2)
    raise ValueError(f"unsupported INI reference: {ref}")


def _included_xml_files(skin: Path) -> list[Path]:
    """The XML files a skin's EQUI.xml manifest actually loads.

    Restricting the scan to the manifest mirrors the client and keeps
    shipped-but-unincluded variant files (EQUI_PetInfoWindow1.xml and
    friends) from shadowing the live window definitions. A skin without a
    readable manifest degrades to scanning every XML file.
    """
    everything = sorted(skin.glob("*.xml"))
    try:
        root = ET.parse(skin / "EQUI.xml").getroot()
    except (ET.ParseError, OSError):
        return everything
    by_folded = {path.name.casefold(): path for path in everything}
    included = []
    for node in root.iter("Include"):
        if not node.text or not node.text.strip():
            continue
        name = Path(node.text.strip().replace("\\", "/")).name.casefold()
        if name in by_folded:
            included.append(by_folded[name])
    return included or everything


def read_skin_geometry(
        skin: Path, targets: Iterable[str]) -> tuple[dict[str, tuple[int, int]], int]:
    """Read declared window sizes from any EverQuest UI folder's SIDL XML.

    Only files included by the skin's EQUI.xml are read, matching what the
    client loads. INI variant sections (PetInfoWindow_1, BuffWindow_13, …)
    map onto their base XML window. Returns matched sizes keyed by the
    requested target names plus a count of XML files that could not be
    parsed — third-party downloads occasionally ship malformed or
    oddly-encoded files, and one bad file must not reject the whole skin.
    """
    wanted: dict[str, list[str]] = {}
    for name in targets:
        wanted.setdefault(re.sub(r"_\d+$", "", name), []).append(name)
    sizes: dict[str, tuple[int, int]] = {}
    unreadable = 0
    for path in _included_xml_files(skin):
        try:
            root = ET.parse(path).getroot()
        except (ET.ParseError, OSError):
            unreadable += 1
            continue
        for screen in root.iter("Screen"):
            item = screen.get("item")
            if item not in wanted:
                continue
            size = screen.find("Size")
            if size is None:
                continue
            try:
                width = int(float((size.findtext("CX") or "").strip()))
                height = int(float((size.findtext("CY") or "").strip()))
            except ValueError:
                continue
            if width > 0 and height > 0:
                for target in wanted[item]:
                    sizes[target] = (width, height)
    return sizes, unreadable


@dataclass
class WindowState:
    name: str
    x: int
    y: int
    width: int
    height: int
    visible: bool
    resizable: bool
    show_on_load: bool | None = None

    def rect(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.x + self.width, self.y + self.height


RESIZABLE = {
    "MainChat", "Chat 1", "Chat 2", "Chat 3",
    "MapViewWnd", "TrackingWnd", "ExtendedTargetWnd",
    "BuffWindow", "BuffWindow_13",
    "ShortDurationBuffWindow", "ShortDurationBuffWindow_13",
    "PetInfoWindow_1", "PetInfoWindow_2", "PetInfoWindow_3",
    "BigBankWnd", "CastSpellWnd", "StanceWnd", "CastingWindow",
    "AggroMeterWnd",
    "HotButtonWnd", "HotButtonWnd2", "HotButtonWnd3", "HotButtonWnd4",
    "HotButtonWnd5", "HotButtonWnd6", "HotButtonWnd7", "HotButtonWnd8",
    "HotButtonWnd9", "HotButtonWnd10", "HotButtonWnd11",
}

MINIMUMS = {
    "MainChat": (360, 160), "Chat 1": (360, 160), "Chat 2": (360, 160),
    "Chat 3": (360, 160), "MapViewWnd": (360, 280),
    "TrackingWnd": (260, 220), "ExtendedTargetWnd": (150, 180),
    "BuffWindow": (160, 160), "ShortDurationBuffWindow": (160, 120),
    "PetInfoWindow_1": (356, 209), "PetInfoWindow_2": (356, 209),
    "PetInfoWindow_3": (441, 181), "BigBankWnd": (287, 280),
    "CastSpellWnd": (48, 48), "StanceWnd": (320, 48),
    "CastingWindow": (220, 28), "AggroMeterWnd": (160, 36),
}
for _hotbar_name in (
        "HotButtonWnd", "HotButtonWnd2", "HotButtonWnd3", "HotButtonWnd4",
        "HotButtonWnd5", "HotButtonWnd6", "HotButtonWnd7", "HotButtonWnd8",
        "HotButtonWnd9", "HotButtonWnd10", "HotButtonWnd11"):
    MINIMUMS[_hotbar_name] = (48, 48)

FALLBACK_SIZES = {
    "EQMainWnd": (392, 36),
    "TargetOfTargetWindow": (240, 53),
    "StanceWnd": (440, 56),
    "CastingWindow": (380, 36),
    "AggroMeterWnd": (220, 48),
}

# Windows the client re-sizes at runtime (menu-bar style, compass strip,
# per-member group growth). Their SIDL <Size> is only an initial hint, so a
# downloaded UI's declared value would misrepresent the in-game footprint;
# they keep the curated sizes instead.
CLIENT_RUNTIME_SIZED = {"EQMainWnd", "CompassWindow", "GroupWindow"}

# EverQuest omits Show= for a few persistent windows. Optional windows also
# commonly omit it when closed, so inheriting the Studio preset's visibility
# produces a misleading preview (most visibly, hotbars 3-11). Keep this list
# deliberately narrow and let explicit Show= values remain authoritative.
IMPLICIT_IMPORT_VISIBLE = {
    "MainChat",
    "Chat 1",
    "Chat 2",
    "HotButtonWnd",
}


class StudioModel:
    def __init__(self, root: Path | None = None, *,
                 resolution: tuple[int, int] = DEFAULT_SCREEN,
                 preset: str = layout.DEFAULT_PRESET):
        self.root = (root or release_root()).resolve()
        self.source_skin = self.root / "spinui_reloaded"
        if not (self.source_skin / "EQUI.xml").is_file():
            raise FileNotFoundError(
                f"{APP_NAME} needs the release's spinui_reloaded folder beside "
                f"it: {self.source_skin}\n\nUnpack the complete "
                "SpinUI-Studio.zip (or run the executable from inside an "
                "unpacked SpinUI release folder) instead of moving "
                "SpinUIStudio.exe out on its own."
            )
        self.screen_width, self.screen_height = resolution
        self.preset = preset
        self.skin_name = DEFAULT_SKIN_NAME
        self.ini_name = DEFAULT_INI_NAME
        self.accent_hex = {
            "venom": hex_from_rgb(DEFAULT_ACCENTS["CYAN"]),
            "gold": hex_from_rgb(DEFAULT_ACCENTS["GOLD"]),
            "ember": hex_from_rgb(DEFAULT_ACCENTS["EMBER"]),
        }
        self.palette = palette_from_hex(self.accent_hex)
        self.windows: dict[str, WindowState] = {}
        self.base_ini_text: str | None = None
        self.preserve_imported_chat = False
        # A downloaded third-party UI adopted for window geometry. SpinUI
        # remains the preview art and theme-build source; the custom skin
        # supplies each fixed window's true footprint and the UISkin= target.
        self.custom_skin: Path | None = None
        self.custom_skin_sizes: dict[str, tuple[int, int]] = {}
        self.reset_preset(preset)

    def is_ultrawide(self) -> bool:
        return (self.screen_width, self.screen_height) == DEFAULT_SCREEN

    def base_placements(self) -> dict[str, dict]:
        """Audited placement table for the current game resolution.

        Chat presets only rearrange the 3440x1440 chat row; the 2560x1440 and
        3840x2160 canvases use their separately-authored release tables.
        """
        size = (self.screen_width, self.screen_height)
        if size == (2560, 1440):
            return layout.standard_1440_placements()
        if size == (3840, 2160):
            return layout.standard_2160_placements()
        return layout.preset_placements(self.preset)

    def chat_font(self) -> int:
        return (layout.CHAT_FONT_2160 if self.screen_height >= 2160
                else layout.CHAT_FONT_1440)

    def set_resolution(self, width: int, height: int) -> None:
        if (width, height) not in RESOLUTIONS:
            supported = ", ".join(
                f"{w}x{h}" for w, h in RESOLUTIONS)
            raise ValueError(
                f"unsupported resolution {width}x{height}; "
                f"supported: {supported}")
        self.screen_width, self.screen_height = width, height
        self.reset_preset(self.preset)

    def reset_preset(self, preset: str) -> None:
        if preset not in layout.CHAT_PRESETS:
            raise ValueError(f"unknown layout preset: {preset}")
        self.preset = preset
        # An imported INI stays the key/value base (client settings survive);
        # only its chat layout and geometry are replaced by the preset table.
        self.preserve_imported_chat = False
        specs = self.placement_table = self.base_placements()
        windows: dict[str, WindowState] = {}
        for name, spec in specs.items():
            x, y, width, height = spec["_rect"]
            if width is None or height is None:
                fallback = layout.XML_SIZES.get(name) or FALLBACK_SIZES.get(name)
                if fallback is None:
                    continue
                width, height = fallback
            show = spec.get("Show")
            visible = show == "1" if show is not None else name in layout.VISIBLE
            windows[name] = WindowState(
                name, round(x), round(y), int(width), int(height),
                visible, name in RESIZABLE,
                None if show is None else show == "1",
            )
        # EQMain is generated separately from PLACEMENTS. The shipped defaults
        # anchor it 8px from the right and 4px from the bottom edge.
        eq_width, eq_height = FALLBACK_SIZES["EQMainWnd"]
        windows["EQMainWnd"] = WindowState(
            "EQMainWnd",
            self.screen_width - 8 - eq_width,
            self.screen_height - 4 - eq_height,
            eq_width, eq_height, True, False, True,
        )
        self.windows = windows
        if self.custom_skin is not None:
            self._apply_custom_sizes()

    def _default_fixed_size(self, name: str) -> tuple[int, int] | None:
        spec = self.placement_table.get(name)
        if spec is not None:
            _x, _y, width, height = spec["_rect"]
            if width is not None and height is not None:
                return int(width), int(height)
        return layout.XML_SIZES.get(name) or FALLBACK_SIZES.get(name)

    def _apply_custom_sizes(self) -> None:
        for name, state in self.windows.items():
            if state.resizable:
                continue
            size = self.custom_skin_sizes.get(name)
            if size is None:
                continue
            state.width, state.height = size
            self.move(name, state.x, state.y)

    def use_skin(self, path: Path) -> str:
        """Adopt a downloaded UI folder's declared window geometry.

        Client-fixed windows take that skin's true footprints and exports
        target its UISkin= name. Preview chrome stays SpinUI placeholder art;
        the third-party skin's own textures are never rendered or rebuilt.
        """
        path = path.resolve()
        if not (path / "EQUI.xml").is_file():
            raise ValueError(
                f"{path} does not look like an EverQuest UI folder — it has "
                "no EQUI.xml. Choose the skin folder itself (the one you "
                "would copy into uifiles\\).")
        if path == self.source_skin:
            self.custom_skin = None
            self.custom_skin_sizes = {}
            for name, state in self.windows.items():
                if state.resizable:
                    continue
                size = self._default_fixed_size(name)
                if size is not None:
                    state.width, state.height = size
                    self.move(name, state.x, state.y)
            return "Restored the bundled SpinUI window geometry."
        fixed = [name for name, state in self.windows.items()
                 if not state.resizable and name not in CLIENT_RUNTIME_SIZED]
        sizes, unreadable = read_skin_geometry(path, fixed)
        if not sizes:
            raise ValueError(
                f"No matching window definitions were found in {path}. "
                "Studio looks for standard EverQuest window XML "
                "(EQUI_PlayerWindow.xml and friends).")
        self.custom_skin = path
        self.custom_skin_sizes = sizes
        self._apply_custom_sizes()
        note = (
            f"Using window geometry from {path.name}: {len(sizes)} windows "
            "matched · preview shows SpinUI placeholder art")
        try:
            self.skin_name = safe_skin_name(path.name)
            note += f" · exports set UISkin={self.skin_name}"
        except ValueError:
            note += (
                " · folder name is not usable as UISkin=, set the skin "
                "folder field manually")
        if unreadable:
            note += f" · {unreadable} unreadable XML file(s) skipped"
        return note

    def set_accent(self, role: str, value: str) -> None:
        if role not in self.accent_hex:
            raise ValueError(f"unknown accent role: {role}")
        updated = dict(self.accent_hex)
        updated[role] = value.lower()
        self.palette = palette_from_hex(updated)
        self.accent_hex = updated

    def move(self, name: str, x: int, y: int) -> None:
        state = self.windows[name]
        state.x = max(0, min(int(x), self.screen_width - state.width))
        state.y = max(0, min(int(y), self.screen_height - state.height))

    def resize(self, name: str, width: int, height: int) -> None:
        state = self.windows[name]
        if not state.resizable:
            raise ValueError(f"{name} has a client-fixed XML size")
        min_width, min_height = MINIMUMS.get(name, (80, 50))
        state.width = max(min_width, min(int(width), self.screen_width - state.x))
        state.height = max(min_height, min(int(height), self.screen_height - state.y))

    def set_visible(self, name: str, visible: bool) -> None:
        self.windows[name].visible = bool(visible)

    def set_show_on_load(self, name: str, value: bool | None) -> None:
        self.windows[name].show_on_load = value

    def ordered_names(self) -> list[str]:
        preferred = list(self.placement_table)
        if "EQMainWnd" not in preferred:
            preferred.append("EQMainWnd")
        return [name for name in preferred if name in self.windows]

    def visible_names(self) -> list[str]:
        return [name for name in self.ordered_names() if self.windows[name].visible]

    def validation(self) -> list[str]:
        problems: list[str] = []
        visible = self.visible_names()
        for name in visible:
            state = self.windows[name]
            if (state.x < 0 or state.y < 0
                    or state.x + state.width > self.screen_width
                    or state.y + state.height > self.screen_height):
                problems.append(f"{name} is off-screen")
        for index, first_name in enumerate(visible):
            first = self.windows[first_name].rect()
            for second_name in visible[index + 1:]:
                second = self.windows[second_name].rect()
                if (first[0] < second[2] and second[0] < first[2]
                        and first[1] < second[3] and second[1] < first[3]):
                    problems.append(f"{first_name} overlaps {second_name}")
        return problems

    def placement_specs(self) -> tuple[dict[str, dict], dict[str, str]]:
        specs: dict[str, dict] = {}
        for name, source in self.placement_table.items():
            state = self.windows[name]
            spec = {key: value for key, value in source.items() if key != "_rect"}
            if self.preserve_imported_chat:
                # The base INI is the player's own file: keep their exact
                # transparency and fade choices instead of the preset styling.
                for key in STYLE_KEYS:
                    spec.pop(key, None)
            spec.update({
                "XRef": "left", "YRef": "top",
                "XPos": percent(state.x, self.screen_width),
                "YPos": percent(state.y, self.screen_height),
                "Width": str(state.width), "Height": str(state.height),
                "_rect": (state.x, state.y, state.width, state.height),
            })
            if state.show_on_load is None:
                spec.pop("Show", None)
            else:
                spec["Show"] = "1" if state.show_on_load else "0"
            specs[name] = spec
        eq = self.windows["EQMainWnd"]
        eqmain = {
            "XRef": "left", "YRef": "top",
            "XPos": percent(eq.x, self.screen_width),
            "YPos": percent(eq.y, self.screen_height),
            "Width": str(eq.width), "Height": str(eq.height),
            "Show": "1" if eq.show_on_load is not False else "0",
        }
        return specs, eqmain

    def _default_ini_text(self) -> str:
        if self.base_ini_text is not None:
            return self.base_ini_text
        candidates = (
            self.root / "layouts" / "spin-live" / DEFAULT_INI_NAME,
            self.root / "layouts" / self.preset / DEFAULT_INI_NAME,
            self.root / DEFAULT_INI_NAME,
        )
        for candidate in candidates:
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8", errors="replace")
        raise FileNotFoundError("No SpinUI character-layout base was found.")

    def export_ini_text(self) -> str:
        safe_skin_name(self.skin_name)
        specs, eqmain = self.placement_specs()
        transformed = layout.transform(
            self._default_ini_text(), self.preset, specs, eqmain,
            chat_font=self.chat_font(),
            skin_name=self.skin_name,
            rebuild_chat=not self.preserve_imported_chat,
        )
        default_name = (
            "default4k.ini" if self.screen_height >= 2160 else "default1440.ini")
        default_path = self.source_skin / default_name
        if default_path.is_file():
            transformed = layout.merge_missing(
                transformed, default_path.read_text(encoding="utf-8", errors="replace"))
        return transformed

    def export_ini(self, path: Path) -> Path:
        path = path.resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        normalized = self.export_ini_text().replace("\r\n", "\n").replace("\r", "\n")
        payload = normalized.replace("\n", "\r\n").encode("utf-8")
        if path.is_file() and path.read_bytes() == payload:
            return path
        if path.is_file():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = path.with_name(f"{path.name}.studio-backup-{stamp}")
            suffix = 1
            while backup.exists():
                backup = path.with_name(
                    f"{path.name}.studio-backup-{stamp}-{suffix}")
                suffix += 1
            shutil.copy2(path, backup)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
        return path

    def import_ini(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.optionxform = str
        parser.read_string(text)
        by_folded = {section.casefold(): section for section in parser.sections()}
        for name, state in self.windows.items():
            section_name = by_folded.get(name.casefold())
            if section_name is None:
                continue
            section = parser[section_name]
            has_width = "Width" in section
            has_height = "Height" in section
            width = int(section.get("Width", state.width))
            height = int(section.get("Height", state.height))
            if width <= 0 or height <= 0:
                raise ValueError(f"{section_name} has an invalid {width}x{height} size.")
            x_fraction = parse_percent(section.get("XPos", percent(state.x, self.screen_width)))
            y_fraction = parse_percent(section.get("YPos", percent(state.y, self.screen_height)))
            x = reference_pixel(section.get("XRef", "left"), x_fraction,
                                self.screen_width, width)
            y = reference_pixel(section.get("YRef", "top"), y_fraction,
                                self.screen_height, height)
            # If the client wrote explicit dimensions, they are authoritative
            # even when the current preset normally treats the XML window as
            # fixed. This preserves spell-gem/hotbar orientation and scale.
            if has_width:
                state.width = min(width, self.screen_width)
            if has_height:
                state.height = min(height, self.screen_height)
            self.move(name, x, y)
            if "Show" in section:
                state.show_on_load = section["Show"].strip() == "1"
                state.visible = state.show_on_load
            else:
                state.show_on_load = None
                state.visible = name in IMPLICIT_IMPORT_VISIBLE
        main_name = by_folded.get("main")
        if main_name is not None:
            self.skin_name = parser[main_name].get("UISkin", self.skin_name)
        if re.fullmatch(r"UI_[A-Za-z]{1,32}_[a-z]{2,24}_LO[1-9][0-9]?\.ini",
                        path.name):
            self.ini_name = path.name
        self.base_ini_text = text
        self.preserve_imported_chat = True

    def project_payload(self) -> dict:
        return {
            "schema": PROJECT_SCHEMA,
            "resolution": [self.screen_width, self.screen_height],
            "preset": self.preset,
            "skin_name": self.skin_name,
            "ini_name": self.ini_name,
            "accents": dict(self.accent_hex),
            "base_ini_text": self.base_ini_text,
            "preserve_imported_chat": self.preserve_imported_chat,
            "custom_skin": (
                None if self.custom_skin is None else str(self.custom_skin)),
            "custom_skin_sizes": {
                name: list(size)
                for name, size in self.custom_skin_sizes.items()
            },
            "windows": {
                name: asdict(state) for name, state in self.windows.items()
            },
            "accuracy": {
                "geometry": "pixel-derived from exported INI",
                "chrome": "real SpinUI textures",
                "content": "simulated; eqgame.exe supplies live state",
            },
        }

    def save_project(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.project_payload(), indent=2) + "\n", encoding="utf-8")
        return path

    def load_project(self, path: Path) -> None:
        payload = json.loads(path.read_text(encoding="utf-8"))
        schema = payload.get("schema")
        if schema not in {1, PROJECT_SCHEMA}:
            raise ValueError("Unsupported SpinUI Studio project schema.")
        width, height = payload["resolution"]
        if (int(width), int(height)) not in RESOLUTIONS:
            supported = ", ".join(f"{w}x{h}" for w, h in RESOLUTIONS)
            raise ValueError(
                f"Project resolution {width}x{height} is not supported; "
                f"supported: {supported}.")
        self.screen_width, self.screen_height = int(width), int(height)
        self.reset_preset(payload["preset"])
        self.skin_name = safe_skin_name(payload["skin_name"])
        self.ini_name = safe_ini_name(payload["ini_name"])
        self.accent_hex = dict(payload["accents"])
        self.palette = palette_from_hex(self.accent_hex)
        self.base_ini_text = payload.get("base_ini_text")
        self.preserve_imported_chat = bool(
            payload.get("preserve_imported_chat", False))
        # Restore a downloaded-UI association when its folder still exists.
        # The exact per-window geometry below wins either way, so a moved
        # folder degrades to plain SpinUI provenance without losing layout.
        self.custom_skin = None
        self.custom_skin_sizes = {}
        stored_skin = payload.get("custom_skin")
        if stored_skin:
            stored_path = Path(stored_skin)
            if (stored_path / "EQUI.xml").is_file():
                self.custom_skin = stored_path.resolve()
                self.custom_skin_sizes = {
                    name: (int(size[0]), int(size[1]))
                    for name, size in payload.get(
                        "custom_skin_sizes", {}).items()
                }
        for name, values in payload["windows"].items():
            if name not in self.windows:
                continue
            state = self.windows[name]
            state.x = int(values["x"])
            state.y = int(values["y"])
            state.width = int(values["width"])
            state.height = int(values["height"])
            state.visible = bool(values["visible"])
            if schema == 1:
                state.show_on_load = state.visible
            else:
                show_on_load = values.get("show_on_load")
                state.show_on_load = (
                    None if show_on_load is None else bool(show_on_load)
                )
            self.move(name, state.x, state.y)

    def build_bundle(self, destination: Path) -> Path:
        """Build a non-destructive, ready-to-install skin + character INI."""
        if self.custom_skin is not None:
            raise ValueError(
                "BUILD FINAL UI compiles the bundled SpinUI skin, but a "
                f"downloaded UI ({self.custom_skin.name}) is active. Use "
                "EXPORT INI and install that UI folder yourself, or choose "
                "the bundled spinui_reloaded folder to switch back first.")
        safe_skin_name(self.skin_name)
        safe_ini_name(self.ini_name)
        destination = destination.resolve()
        if destination.exists():
            raise FileExistsError(
                f"{destination} already exists; choose a new bundle folder.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging: Path | None = Path(tempfile.mkdtemp(
            prefix=f".{destination.name}-", dir=destination.parent))
        try:
            assert staging is not None
            output_skin = staging / self.skin_name
            shutil.copytree(self.source_skin, output_skin)
            apply_palette_to_xml(output_skin, self.palette)
            texture_builder.generate(
                source_skin=self.source_skin,
                output_skin=output_skin,
                palette=self.palette,
                preview_dir=None,
                quiet=True,
            )
            for ini_path in output_skin.glob("default*.ini"):
                text = ini_path.read_text(encoding="utf-8", errors="replace")
                text = re.sub(
                    r"(?im)^UISkin=[^\r\n]*$",
                    f"UISkin={self.skin_name}",
                    text,
                )
                ini_path.write_text(text, encoding="utf-8")
            (staging / self.ini_name).write_text(
                self.export_ini_text(), encoding="utf-8", newline="\r\n")
            self.save_project(staging / "spinui-studio.json")
            (staging / "INSTALL.txt").write_text(
                "SpinUI Studio custom build\n\n"
                f"1. Close EverQuest completely.\n"
                f"2. Copy {self.skin_name} into EverQuest Legends\\uifiles\\.\n"
                f"3. Copy {self.ini_name} beside eqgame.exe.\n"
                f"4. In game use /loadskin {self.skin_name} 1 if needed.\n\n"
                "Geometry and chrome are generated from SpinUI sources. Live names, "
                "gauges, buffs, item icons, and chat text are supplied by EverQuest.\n",
                encoding="utf-8",
            )
            os.replace(staging, destination)
            staging = None
        finally:
            if staging is not None and staging.exists():
                shutil.rmtree(staging)
        return destination


class RenderSnapshot:
    """Immutable copy of everything render_scene reads.

    A background thread paints from the snapshot while the Tk thread keeps
    editing the live model, so a half-dragged window can never tear a frame
    or race a dict mutation.
    """

    def __init__(self, model: StudioModel):
        self.root = model.root
        self.source_skin = model.source_skin
        self.custom_skin = model.custom_skin
        self.screen_width = model.screen_width
        self.screen_height = model.screen_height
        self.palette = dict(model.palette)
        self.windows = {
            name: WindowState(**asdict(state))
            for name, state in model.windows.items()
        }
        self._visible = list(model.visible_names())

    def visible_names(self) -> list[str]:
        return list(self._visible)


def apply_palette_to_xml(
        skin: Path, palette: dict[str, tuple[int, int, int]]) -> int:
    """Replace canonical accent triples only inside XML color structures."""
    replacements = 0
    mapping = {
        DEFAULT_ACCENTS[name]: palette[name]
        for name in DEFAULT_ACCENTS
        if palette.get(name) != DEFAULT_ACCENTS[name]
    }
    for path in skin.glob("*.xml"):
        text = path.read_text(encoding="utf-8", errors="strict")
        revised = text
        for old, new in mapping.items():
            pattern = re.compile(
                rf"(<R>\s*){old[0]}(\s*</R>\s*<G>\s*){old[1]}"
                rf"(\s*</G>\s*<B>\s*){old[2]}(\s*</B>)"
            )
            revised, count = pattern.subn(
                rf"\g<1>{new[0]}\g<2>{new[1]}\g<3>{new[2]}\g<4>",
                revised,
            )
            replacements += count
        if revised != text:
            path.write_text(revised, encoding="utf-8")
    return replacements


def apply_preview_palette(palette: dict[str, tuple[int, int, int]]) -> None:
    for name, value in palette.items():
        if hasattr(preview, name):
            setattr(preview, name, value)


_BACKGROUND_CACHE: dict[tuple[int, int], Image.Image] = {}
_INVENTORY_CACHE: dict[tuple[tuple[str, tuple[int, int, int]], ...], Image.Image] = {}
_ACTIVE_PREVIEW_SKIN: Path | None = None


def studio_background(width: int, height: int) -> Image.Image:
    key = width, height
    cached = _BACKGROUND_CACHE.get(key)
    if cached is not None:
        return cached.copy()
    image = Image.new("RGBA", key, (10, 14, 18, 255))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        t = y / max(1, height - 1)
        draw.line(
            [(0, y), (width, y)],
            fill=(round(18 + 16 * t), round(24 + 16 * t),
                  round(29 + 12 * t), 255),
        )
    for cx, cy, radius, color in (
        (int(width * .25), int(height * .35), int(height * .32), (190, 135, 60, 35)),
        (int(width * .62), int(height * .28), int(height * .38), (50, 130, 135, 31)),
    ):
        glow = Image.new("RGBA", (radius * 2, radius * 2), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse(
            (0, 0, radius * 2 - 1, radius * 2 - 1), fill=color)
        image.alpha_composite(
            glow.filter(ImageFilter.GaussianBlur(max(20, radius // 4))),
            (cx - radius, cy - radius),
        )
    draw = ImageDraw.Draw(image)
    draw.text(
        (width // 2, height // 2 - 20),
        "SPINUI STUDIO · OFFLINE PREVIEW",
        font=preview.F(max(16, height // 50), True),
        fill=(255, 255, 255, 28),
        anchor="mm",
    )
    draw.text(
        (width // 2, height // 2 + 24),
        "PIXEL GEOMETRY · SIMULATED GAME STATE",
        font=preview.F(max(10, height // 85), True),
        fill=(255, 255, 255, 22),
        anchor="mm",
    )
    _BACKGROUND_CACHE[key] = image
    return image.copy()


def _draw_pet(canvas: Image.Image, state: WindowState) -> None:
    x, y = state.x, state.y
    frame_y = y + 28 if state.name == "PetInfoWindow_2" else y
    preview.glass_window(
        canvas, x, frame_y, min(356, state.width), min(181, state.height),
        alpha=248)
    preview.text(
        canvas, (x + 10, frame_y + 7), "COMPANION", 9,
        preview.GOLD_BRIGHT, True)
    preview.gauge(canvas, x + 5, frame_y + 20, 346, 23, 1.0, preview.HP,
                  "a fire elemental", "100")
    draw = ImageDraw.Draw(canvas)
    draw.polygon(
        [(x + 16, frame_y + 49), (x + 23, frame_y + 56),
         (x + 16, frame_y + 63), (x + 9, frame_y + 56)],
        outline=preview.GOLD + (255,),
    )
    preview.gauge(canvas, x + 33, frame_y + 48, 318, 21, .82, preview.HP,
                  "a gnoll guardsman", "82")
    labels = (
        "attack", "follow", "guard", "taunt", "sit", "stop", "regroup",
        "back", "leave", "inventory", "none", "none", "none", "none",
    )
    positions = [
        *( (10 + (index % 4) * 84, 74 + (index // 4) * 25)
           for index in range(12) ),
        (94, 149), (178, 149),
    ]
    for index, ((bx, by), label) in enumerate(zip(positions, labels)):
        active = index == 1
        draw.rounded_rectangle(
            [x + bx, frame_y + by, x + bx + 77, frame_y + by + 22],
            radius=3,
            fill=preview.BG3 + (255,) if active else preview.BG1 + (255,),
            outline=(preview.GOLD if active else preview.LINE) + (255,),
        )
        preview.text(
            canvas, (x + bx + 39, frame_y + by + 11), label, 10,
            preview.GOLD_BRIGHT if active else preview.TEXT,
            active, "mm",
        )
    if state.name in {"PetInfoWindow", "PetInfoWindow_3"}:
        # Side rail is borderless in the client. Populate enough effects to
        # show the vertical-first flow while preserving transparent empties.
        rail_x = x + 353
        for index in range(min(12, max(0, (state.width - 357) // 24 * 2))):
            row = index % 7
            column = index // 7
            preview.slot(
                canvas, rail_x + column * 24, y + 2 + row * 24, 24,
                preview.ICONS[index % len(preview.ICONS)],
            )
    else:
        # Top/bottom variants have one compact horizontal row at minimum.
        rail_y = y + 2 if state.name == "PetInfoWindow_2" else y + state.height - 31
        for index in range(8):
            preview.slot(
                canvas, x + 4 + index * 24, rail_y, 24,
                preview.ICONS[index % len(preview.ICONS)],
            )


def _best_grid(width: int, height: int, count: int,
               candidates: Iterable[tuple[int, int]]) -> tuple[int, int, int]:
    """Choose the largest square-cell grid that fits the current INI size."""
    best = (1, count, 1)
    for columns, rows in candidates:
        if columns * rows < count:
            continue
        size = max(1, min((width - 8) // columns, (height - 8) // rows, 40))
        if size > best[2]:
            best = columns, rows, size
    return best


def _draw_hotbar(canvas: Image.Image, state: WindowState, page: int) -> None:
    x, y, width, height = state.x, state.y, state.width, state.height
    preview.glass_window(canvas, x, y, width, height, alpha=242)
    columns, rows, size = _best_grid(
        width, height, 12, ((12, 1), (6, 2), (2, 6), (1, 12)))
    step_x = (width - 8) / columns
    step_y = (height - 8) / rows
    for index in range(12):
        column = index % columns
        row = index // columns
        sx = round(x + 4 + column * step_x + (step_x - size) / 2)
        sy = round(y + 4 + row * step_y + (step_y - size) / 2)
        preview.slot(
            canvas, sx, sy, size,
            preview.ICONS[(index * 3 + page) % len(preview.ICONS)]
            if index < 8 else None,
        )
        if size >= 26:
            preview.text(
                canvas, (sx + 2, sy + 1), str(index + 1), 7,
                preview.DIM,
            )


def _draw_gems_adaptive(canvas: Image.Image, state: WindowState) -> None:
    x, y, width, height = state.x, state.y, state.width, state.height
    preview.glass_window(canvas, x, y, width, height, alpha=242)
    columns, rows, size = _best_grid(
        width, height, 14, ((14, 1), (1, 14)))
    step_x = (width - 8) / columns
    step_y = (height - 8) / rows
    for index in range(14):
        column = index % columns
        row = index // columns
        sx = round(x + 4 + column * step_x + (step_x - size) / 2)
        sy = round(y + 4 + row * step_y + (step_y - size) / 2)
        preview.slot(
            canvas, sx, sy, size,
            preview.ICONS[index % len(preview.ICONS)] if index != 11 else None,
        )


def _inventory_image(model: StudioModel) -> Image.Image | None:
    key = tuple(sorted(model.palette.items()))
    if key in _INVENTORY_CACHE:
        return _INVENTORY_CACHE[key].copy()
    path = model.root / "docs" / "previews" / "equipment_page.png"
    if not path.is_file():
        return None
    image = Image.open(path).convert("RGBA")
    if image.size[0] >= 1400 and image.size[1] >= 1400:
        image = image.crop((40, 40, 1360, 1376)).resize((660, 668), Image.Resampling.LANCZOS)
    replacements = {
        DEFAULT_ACCENTS[name]: model.palette[name]
        for name in DEFAULT_ACCENTS if name in model.palette
    }
    source_pixels = (
        image.get_flattened_data()
        if hasattr(image, "get_flattened_data") else image.getdata()
    )
    pixels = [
        replacements.get(pixel[:3], pixel[:3]) + (pixel[3],)
        for pixel in source_pixels
    ]
    image.putdata(pixels)
    if len(_INVENTORY_CACHE) >= 8:
        _INVENTORY_CACHE.pop(next(iter(_INVENTORY_CACHE)))
    _INVENTORY_CACHE[key] = image
    return image.copy()


def _draw_generic(canvas: Image.Image, state: WindowState) -> None:
    preview.std_window(
        canvas, state.x, state.y, state.width, state.height,
        title=state.name, alpha=242)


CHAT_LINES = {
    "MainChat": [
        (preview.TEXT_DIM, "Welcome to EverQuest Legends!"),
        ((90, 200, 120), "You have entered Blackburrow."),
        (preview.TEXT, "Your faction standing has changed."),
        ((240, 240, 140), "You gain party experience!! (0.42%)"),
    ],
    "Chat 1": [
        ((120, 220, 250), "Grimlord tells the group, 'inc left side'"),
        ((230, 120, 220), "Obscurity tells you, 'port is ready'"),
        ((90, 200, 240), "Frogenstein tells the guild, 'grats!'"),
    ],
    "Chat 2": [
        (preview.TEXT, "You slash a gnoll for 213 points of damage."),
        ((240, 200, 90), "You score a critical hit! (692)"),
        ((150, 170, 230), "Your pet hits a gnoll for 118 damage."),
        ((240, 200, 90), "You have slain a gnoll!"),
    ],
    "Chat 3": [(preview.TEXT_DIM, "Optional fourth chat container")],
}


_RENDER_LOCK = threading.Lock()


def render_scene(model: StudioModel | RenderSnapshot) -> Image.Image:
    # render_preview keeps module-level palette/texture state, so scene
    # painting is serialized: the GUI's background render worker and any
    # direct caller (SAVE PREVIEW, CLI) must never interleave.
    with _RENDER_LOCK:
        return _render_scene_locked(model)


def _render_scene_locked(model: StudioModel | RenderSnapshot) -> Image.Image:
    global _ACTIVE_PREVIEW_SKIN
    apply_preview_palette(model.palette)
    if _ACTIVE_PREVIEW_SKIN != model.source_skin:
        preview.SKIN = model.source_skin
        preview.TEX.clear()
        _ACTIVE_PREVIEW_SKIN = model.source_skin
    canvas = studio_background(model.screen_width, model.screen_height)
    if model.custom_skin is not None:
        ImageDraw.Draw(canvas).text(
            (model.screen_width // 2, model.screen_height // 2 + 58),
            f"DOWNLOADED UI GEOMETRY · {model.custom_skin.name.upper()} · "
            "SPINUI PLACEHOLDER ART",
            font=preview.F(max(10, model.screen_height // 85), True),
            fill=(255, 255, 255, 30),
            anchor="mm",
        )
    for name in model.visible_names():
        state = model.windows[name]
        x, y, width, height = state.x, state.y, state.width, state.height
        if name in CHAT_LINES:
            preview.draw_chat(
                canvas, x, y, width, height,
                {"MainChat": "Main Chat", "Chat 1": "Social",
                 "Chat 2": "Combat", "Chat 3": "Chat 3"}[name],
                CHAT_LINES[name],
                "/say " if name == "MainChat" else "",
            )
        elif name == "PlayerWindow":
            preview.draw_player(canvas, x, y)
        elif name == "TargetWindow":
            preview.draw_target(canvas, x, y)
        elif name.startswith("PetInfoWindow"):
            _draw_pet(canvas, state)
        elif name == "StanceWnd":
            preview.draw_stance(canvas, x, y, width, height)
        elif name == "CastingWindow":
            preview.draw_casting(canvas, x, y, width, height)
        elif name == "AggroMeterWnd":
            preview.draw_aggro(canvas, x, y, width, height)
        elif name == "CastSpellWnd":
            _draw_gems_adaptive(canvas, state)
        elif name.startswith("HotButtonWnd"):
            suffix = name.removeprefix("HotButtonWnd")
            page = int(suffix) if suffix.isdigit() else 1
            _draw_hotbar(canvas, state, page)
        elif name in {"BuffWindow", "BuffWindow_13"}:
            preview.draw_buffs(canvas, x, y, "Spell Effects", 18, width, height)
        elif name in {"ShortDurationBuffWindow", "ShortDurationBuffWindow_13"}:
            preview.draw_buffs(canvas, x, y, "Song Effects", 8, width, height)
        elif name == "GroupWindow":
            local = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            preview.draw_group(local, 0, 0)
            canvas.alpha_composite(local, (x, y))
        elif name == "MapViewWnd":
            preview.draw_map(canvas, x, y, width, height)
        elif name == "TrackingWnd":
            preview.draw_tracker(canvas, x, y, width, height)
        elif name == "CompassWindow":
            preview.draw_compass(canvas, x, y, width, height)
        elif name.startswith("BagInv"):
            preview.draw_bag(canvas, x, y, name.replace("BagInv", "Bag "))
        elif name == "EQMainWnd":
            preview.draw_eqmain(canvas, x, y, width, height)
        elif name == "InventoryWindow":
            inventory = _inventory_image(model)
            if inventory is None:
                _draw_generic(canvas, state)
            else:
                canvas.alpha_composite(
                    inventory.resize((width, height), Image.Resampling.LANCZOS),
                    (x, y),
                )
        else:
            _draw_generic(canvas, state)
    return canvas


class StudioApp:
    def __init__(self, model: StudioModel, project: Path | None = None,
                 *, offer_import: bool = True):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.model = model
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.geometry("1600x940")
        self.root.minsize(1180, 720)
        self.root.configure(bg=BG)
        self.root.option_add("*Font", ("Segoe UI", 9))
        self.selected: str | None = None
        self.drag_origin: tuple[str, int, int, int, int, int, int] | None = None
        self.photo = None
        self.scale = .4
        self.project_path = project
        self.render_after_id = None
        self.rendering = False
        self.render_pending = False
        # Scene rendering happens on a worker thread; results arrive through
        # this queue. The cached full-resolution scene and its state key let
        # clicks, selections, and no-op events skip repainting entirely.
        self.render_results: queue.Queue = queue.Queue()
        self.scene_key = None
        self.scene_image = None
        self.rendered_key = None
        self.rendered_target = None
        self.status = tk.StringVar(value="Ready")
        self.resolution_var = tk.StringVar(
            value=RESOLUTIONS[(model.screen_width, model.screen_height)])
        self.preset_var = tk.StringVar(value=model.preset)
        self.skin_var = tk.StringVar(value=model.skin_name)
        self.ini_var = tk.StringVar(value=model.ini_name)
        self.show_var = tk.BooleanVar(value=False)
        self.load_mode_var = tk.StringVar(value=LOAD_PRESERVE)
        self.inspector_vars = {
            key: tk.StringVar() for key in ("x", "y", "width", "height")
        }
        self.root.report_callback_exception = self.report_callback_exception
        self._build()
        self.root.bind("<Control-s>", lambda _event: self.save_project())
        self.root.bind("<KeyPress>", self.key_nudge)
        self.schedule_render(50)
        if offer_import:
            self.root.after(400, self.offer_current_ini)

    def _build(self) -> None:
        tk, ttk = self.tk, self.ttk
        toolbar = tk.Frame(self.root, bg=PANEL, height=48)
        toolbar.pack(fill="x")
        tk.Label(
            toolbar, text="SPINUI STUDIO", bg=PANEL, fg=GOLD_BRIGHT,
            font=("Segoe UI Semibold", 14),
        ).pack(side="left", padx=(14, 20), pady=10)
        for label, command in (
            ("OPEN PROJECT", self.open_project),
            ("SAVE PROJECT", self.save_project),
            ("IMPORT CURRENT INI", self.import_ini),
            ("USE DOWNLOADED UI", self.use_downloaded_ui),
            ("EXPORT INI", self.export_ini),
            ("SAVE PREVIEW", self.save_preview),
            ("BUILD FINAL UI", self.build_bundle),
        ):
            tk.Button(
                toolbar, text=label, command=command, bg=RAISED, fg=TEXT,
                activebackground=CYAN, activeforeground=BG, relief="flat",
                padx=10, pady=5,
            ).pack(side="left", padx=3)
        tk.Label(toolbar, text="Game resolution", bg=PANEL, fg=DIM).pack(
            side="left", padx=(18, 5))
        resolution = ttk.Combobox(
            toolbar, textvariable=self.resolution_var, state="readonly",
            width=20, values=tuple(RESOLUTIONS.values()),
        )
        resolution.pack(side="left")
        resolution.bind("<<ComboboxSelected>>", self.change_resolution)
        tk.Label(toolbar, text="Chat preset", bg=PANEL, fg=DIM).pack(
            side="left", padx=(14, 5))
        self.preset_combo = ttk.Combobox(
            toolbar, textvariable=self.preset_var, state="readonly", width=14,
            values=(CUSTOM_PRESET_LABEL, *tuple(layout.CHAT_PRESETS)),
        )
        self.preset_combo.pack(side="left")
        self.preset_combo.bind("<<ComboboxSelected>>", self.change_preset)
        self._sync_preset_availability()

        body = tk.PanedWindow(
            self.root, orient="horizontal", bg=BG, sashwidth=6,
            sashrelief="flat")
        body.pack(fill="both", expand=True)

        left = tk.Frame(body, bg=PANEL, width=300)
        body.add(left, minsize=285)
        tk.Label(
            left, text="WINDOWS", bg=PANEL, fg=GOLD_BRIGHT,
            font=("Segoe UI Semibold", 10),
        ).pack(anchor="w", padx=12, pady=(12, 6))
        self.tree = ttk.Treeview(
            left, columns=("preview", "load", "geometry"),
            show="headings", selectmode="browse")
        self.tree.heading("preview", text="VIEW")
        self.tree.heading("load", text="LOAD")
        self.tree.heading("geometry", text="WINDOW · X,Y · W×H")
        self.tree.column("preview", width=42, anchor="center", stretch=False)
        self.tree.column("load", width=42, anchor="center", stretch=False)
        self.tree.column("geometry", width=196, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.tree.bind("<<TreeviewSelect>>", self.select_tree)
        self.tree.bind("<Double-1>", self.toggle_tree)
        tk.Label(
            left,
            text="Double-click toggles preview only. Drag to move; drag the "
                 "gold corner to resize. Click the canvas, then arrow keys "
                 "nudge 1px (Shift = 10px); in this list, arrows browse rows.",
            bg=PANEL, fg=DIM, justify="left", wraplength=270,
        ).pack(anchor="w", padx=12, pady=(0, 12))

        center = tk.Frame(body, bg="#05070a")
        body.add(center, minsize=650, stretch="always")
        self.canvas = tk.Canvas(
            center, bg="#05070a", highlightthickness=0, cursor="arrow")
        self.canvas.pack(fill="both", expand=True, padx=6, pady=6)
        self.canvas.bind("<Button-1>", self.canvas_down)
        self.canvas.bind("<B1-Motion>", self.canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self.canvas_up)
        self.canvas.bind("<Configure>", lambda _event: self.schedule_render(80))

        right = tk.Frame(body, bg=PANEL, width=280)
        body.add(right, minsize=260)
        self._build_inspector(right)

        footer = tk.Frame(self.root, bg=PANEL, height=30)
        footer.pack(fill="x")
        tk.Label(footer, textvariable=self.status, bg=PANEL, fg=DIM).pack(
            side="left", padx=12, pady=6)
        tk.Label(
            footer,
            text="REAL TEXTURES + INI GEOMETRY · LIVE GAME DATA IS SIMULATED",
            bg=PANEL, fg=CYAN, font=("Segoe UI Semibold", 8),
        ).pack(side="right", padx=12)
        self.refresh_tree()

    def _build_inspector(self, parent) -> None:
        tk = self.tk
        tk.Label(
            parent, text="INSPECTOR", bg=PANEL, fg=GOLD_BRIGHT,
            font=("Segoe UI Semibold", 10),
        ).pack(anchor="w", padx=14, pady=(12, 8))
        self.selected_label = tk.Label(
            parent, text="Select a window", bg=PANEL, fg=TEXT,
            font=("Segoe UI Semibold", 11))
        self.selected_label.pack(anchor="w", padx=14, pady=(0, 8))
        grid = tk.Frame(parent, bg=PANEL)
        grid.pack(fill="x", padx=14)
        for row, key in enumerate(("x", "y", "width", "height")):
            tk.Label(grid, text=key.upper(), bg=PANEL, fg=DIM, width=7,
                     anchor="w").grid(row=row, column=0, sticky="w", pady=3)
            tk.Entry(
                grid, textvariable=self.inspector_vars[key], bg=RAISED, fg=TEXT,
                insertbackground=TEXT, relief="flat", width=14,
            ).grid(row=row, column=1, sticky="ew", pady=3)
        grid.columnconfigure(1, weight=1)
        tk.Checkbutton(
            parent, text="Preview on canvas", variable=self.show_var,
            command=self.toggle_selected_preview, bg=PANEL, fg=TEXT,
            selectcolor=RAISED, activebackground=PANEL,
            activeforeground=TEXT,
        ).pack(anchor="w", padx=14, pady=(8, 4))
        tk.Label(parent, text="IN-GAME START STATE", bg=PANEL, fg=DIM).pack(
            anchor="w", padx=14, pady=(4, 2))
        load_mode = self.ttk.Combobox(
            parent, textvariable=self.load_mode_var, state="readonly",
            values=(LOAD_PRESERVE, LOAD_SHOW, LOAD_HIDE))
        load_mode.pack(fill="x", padx=14)
        load_mode.bind("<<ComboboxSelected>>", self.apply_load_mode)
        actions = tk.Frame(parent, bg=PANEL)
        actions.pack(fill="x", padx=14, pady=(8, 6))
        for label, command in (
                ("CENTER", self.center_selected),
                ("SHOW", lambda: self.set_selected_preview(True)),
                ("HIDE", lambda: self.set_selected_preview(False))):
            tk.Button(
                actions, text=label, command=command, bg=RAISED, fg=TEXT,
                activebackground=CYAN, activeforeground=BG, relief="flat",
                padx=5, pady=4,
            ).pack(side="left", fill="x", expand=True, padx=2)
        tk.Button(
            parent, text="APPLY GEOMETRY", command=self.apply_inspector,
            bg=CYAN, fg=BG, relief="flat", padx=10, pady=6,
        ).pack(fill="x", padx=14, pady=(0, 14))

        tk.Frame(parent, bg=LINE, height=1).pack(fill="x", padx=14, pady=4)
        tk.Label(
            parent, text="ACCENT COLORS", bg=PANEL, fg=GOLD_BRIGHT,
            font=("Segoe UI Semibold", 10),
        ).pack(anchor="w", padx=14, pady=(10, 6))
        self.swatches = {}
        for role, title in (
            ("venom", "Interaction / Venom"),
            ("gold", "Heraldic / Gold"),
            ("ember", "Warning / Ember"),
        ):
            row = tk.Frame(parent, bg=PANEL)
            row.pack(fill="x", padx=14, pady=3)
            swatch = tk.Button(
                row, width=3, relief="flat",
                command=lambda value=role: self.choose_color(value))
            swatch.pack(side="left", padx=(0, 8))
            self.swatches[role] = swatch
            tk.Label(row, text=title, bg=PANEL, fg=TEXT).pack(side="left")
        self.refresh_swatches()

        tk.Frame(parent, bg=LINE, height=1).pack(fill="x", padx=14, pady=12)
        for title, variable in (
            ("SKIN FOLDER", self.skin_var),
            ("CHARACTER INI", self.ini_var),
        ):
            tk.Label(parent, text=title, bg=PANEL, fg=DIM).pack(
                anchor="w", padx=14, pady=(3, 2))
            tk.Entry(
                parent, textvariable=variable, bg=RAISED, fg=TEXT,
                insertbackground=TEXT, relief="flat",
            ).pack(fill="x", padx=14)
        tk.Label(
            parent,
            text="The build is written to a new folder. Existing game files are "
                 "never overwritten by Studio.",
            bg=PANEL, fg=DIM, justify="left", wraplength=245,
        ).pack(anchor="w", padx=14, pady=12)

    def _tree_values(self, name: str) -> tuple[str, str, str]:
        state = self.model.windows[name]
        geometry = f"{name} · {state.x},{state.y} · {state.width}×{state.height}"
        load_state = (
            "—" if state.show_on_load is None
            else ("●" if state.show_on_load else "○")
        )
        return ("●" if state.visible else "○", load_state, geometry)

    def refresh_tree(self) -> None:
        # Update rows in place when the window set is unchanged. Rebuilding
        # fires deselect/select churn through <<TreeviewSelect>> on every
        # drag release and makes the list flicker.
        names = self.model.ordered_names()
        if list(self.tree.get_children()) != names:
            for item in self.tree.get_children():
                self.tree.delete(item)
            for name in names:
                self.tree.insert(
                    "", "end", iid=name, values=self._tree_values(name))
        else:
            for name in names:
                self.tree.item(name, values=self._tree_values(name))
        if (self.selected in self.model.windows
                and self.tree.selection() != (self.selected,)):
            self.tree.selection_set(self.selected)

    def refresh_swatches(self) -> None:
        for role, button in self.swatches.items():
            button.configure(bg=self.model.accent_hex[role],
                             activebackground=self.model.accent_hex[role])

    def select(self, name: str | None) -> None:
        self.selected = name
        if name is None:
            self.selected_label.configure(text="Select a window")
            self.draw_selection()
            return
        state = self.model.windows[name]
        self.selected_label.configure(
            text=name + ("" if state.resizable else " · fixed size"))
        for key in self.inspector_vars:
            value = getattr(state, key)
            self.inspector_vars[key].set(str(value))
        self.show_var.set(state.visible)
        self.load_mode_var.set(
            LOAD_PRESERVE if state.show_on_load is None
            else (LOAD_SHOW if state.show_on_load else LOAD_HIDE)
        )
        # Only touch the tree when its selection actually differs. Tk fires
        # <<TreeviewSelect>> on every selection_set even when nothing
        # changed, and select_tree() calls back into select(): an
        # unconditional set here self-replenishes the event queue forever
        # and the app stops responding after the first canvas click.
        if self.tree.selection() != (name,):
            self.tree.selection_set(name)
        self.tree.see(name)
        self.draw_selection()

    def select_tree(self, _event=None) -> None:
        selection = self.tree.selection()
        if selection:
            self.select(selection[0])

    def toggle_tree(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        self.selected = selection[0]
        self.set_selected_preview(
            not self.model.windows[self.selected].visible)

    def toggle_selected_preview(self) -> None:
        if self.selected is not None:
            self.set_selected_preview(self.show_var.get())

    def set_selected_preview(self, visible: bool) -> None:
        if self.selected is None:
            return
        self.model.set_visible(self.selected, visible)
        self.show_var.set(visible)
        self.select(self.selected)
        self.refresh_tree()
        self.schedule_render()

    def apply_load_mode(self, _event=None) -> None:
        if self.selected is None:
            return
        value = self.load_mode_var.get()
        show = None if value == LOAD_PRESERVE else value == LOAD_SHOW
        self.model.set_show_on_load(self.selected, show)
        self.refresh_tree()

    def center_selected(self) -> None:
        if self.selected is None:
            return
        state = self.model.windows[self.selected]
        self.model.move(
            self.selected,
            (self.model.screen_width - state.width) // 2,
            (self.model.screen_height - state.height) // 2,
        )
        self.select(self.selected)
        self.refresh_tree()
        self.schedule_render()

    def key_nudge(self, event) -> None:
        if self.selected is None:
            return
        focus = self.root.focus_get()
        # Text inputs own their keystrokes; the window list owns arrow/space
        # navigation. Nudging there would silently drag the selected window
        # while the user is only browsing the list.
        if focus is not None and focus.winfo_class() in {
                "Entry", "TEntry", "TCombobox", "Text", "Treeview"}:
            return
        if event.keysym == "space":
            self.set_selected_preview(
                not self.model.windows[self.selected].visible)
            return
        directions = {
            "Left": (-1, 0), "Right": (1, 0),
            "Up": (0, -1), "Down": (0, 1),
        }
        if event.keysym not in directions:
            return
        step = 10 if event.state & 0x0001 else 1
        dx, dy = directions[event.keysym]
        state = self.model.windows[self.selected]
        self.model.move(
            self.selected, state.x + dx * step, state.y + dy * step)
        self.select(self.selected)
        self.refresh_tree()
        self.schedule_render()

    def canvas_point(self, event) -> tuple[int, int]:
        return round(event.x / self.scale), round(event.y / self.scale)

    def canvas_down(self, event) -> None:
        # Take keyboard focus so arrow-key nudging always works after any
        # canvas interaction, even if the window list was used beforehand.
        self.canvas.focus_set()
        gx, gy = self.canvas_point(event)
        chosen = None
        mode = "move"
        if self.selected is not None:
            selected_state = self.model.windows[self.selected]
            x0, y0, x1, y1 = selected_state.rect()
            handle = max(16, round(10 / max(self.scale, .05)))
            if (selected_state.visible and selected_state.resizable
                    and x1 - handle <= gx <= x1
                    and y1 - handle <= gy <= y1):
                chosen = self.selected
                mode = "resize"
        if chosen is None:
            for name in reversed(self.model.visible_names()):
                x0, y0, x1, y1 = self.model.windows[name].rect()
                if x0 <= gx < x1 and y0 <= gy < y1:
                    chosen = name
                    break
        self.select(chosen)
        if chosen is not None:
            state = self.model.windows[chosen]
            self.drag_origin = (
                mode, gx, gy, state.x, state.y, state.width, state.height)

    def canvas_drag(self, event) -> None:
        if self.selected is None or self.drag_origin is None:
            return
        gx, gy = self.canvas_point(event)
        mode, start_x, start_y, window_x, window_y, width, height = (
            self.drag_origin)
        if mode == "resize":
            self.model.resize(
                self.selected,
                width + gx - start_x,
                height + gy - start_y,
            )
        else:
            self.model.move(
                self.selected,
                window_x + gx - start_x,
                window_y + gy - start_y,
            )
        state = self.model.windows[self.selected]
        for key in self.inspector_vars:
            self.inspector_vars[key].set(str(getattr(state, key)))
        self.draw_selection()

    def canvas_up(self, _event) -> None:
        if self.drag_origin is not None:
            self.drag_origin = None
            self.refresh_tree()
            self.schedule_render()

    def draw_selection(self) -> None:
        self.canvas.delete("selection")
        if self.selected is None:
            return
        state = self.model.windows[self.selected]
        x0, y0, x1, y1 = (
            state.x * self.scale, state.y * self.scale,
            (state.x + state.width) * self.scale,
            (state.y + state.height) * self.scale,
        )
        self.canvas.create_rectangle(
            x0, y0, x1, y1, outline=GOLD_BRIGHT, width=2, tags="selection")
        self.canvas.create_text(
            x0 + 5, y0 + 5, text=self.selected, anchor="nw",
            fill=GOLD_BRIGHT, font=("Segoe UI Semibold", 9), tags="selection")
        if state.resizable:
            self.canvas.create_rectangle(
                max(x0, x1 - 10), max(y0, y1 - 10), x1, y1,
                fill=GOLD_BRIGHT, outline=BG, width=1, tags="selection")

    def schedule_render(self, delay: int = 30) -> None:
        """Coalesce resize/toggle bursts into one non-reentrant render."""
        if self.rendering:
            self.render_pending = True
            return
        if self.render_after_id is not None:
            try:
                self.root.after_cancel(self.render_after_id)
            except Exception:
                pass
        self.render_after_id = self.root.after(delay, self.render)

    def _scene_state_key(self):
        model = self.model
        return (
            model.screen_width, model.screen_height,
            tuple(sorted(model.palette.items())),
            None if model.custom_skin is None else str(model.custom_skin),
            tuple(
                (name, state.x, state.y, state.width, state.height,
                 state.visible)
                for name, state in sorted(model.windows.items())
            ),
        )

    def render(self) -> None:
        """Start a background repaint; the Tk thread never blocks on it.

        Selecting or clicking a window changes no scene state, so those hit
        the cache and skip painting entirely. Geometry, palette, and
        visibility changes render on a worker thread and land through
        _apply_render_result.
        """
        self.render_after_id = None
        if self.rendering:
            self.render_pending = True
            return
        try:
            self.model.skin_name = (
                self.skin_var.get().strip() or DEFAULT_SKIN_NAME)
            self.model.ini_name = (
                self.ini_var.get().strip() or DEFAULT_INI_NAME)
            key = self._scene_state_key()
            target = (max(320, self.canvas.winfo_width()),
                      max(240, self.canvas.winfo_height()))
            if (key == self.rendered_key and target == self.rendered_target
                    and self.photo is not None):
                self.draw_selection()
                return
            snapshot = RenderSnapshot(self.model)
            cached_scene = self.scene_image if key == self.scene_key else None
            self.rendering = True
            self.status.set("Rendering pixel-accurate geometry…")

            def worker():
                try:
                    scene = (cached_scene if cached_scene is not None
                             else render_scene(snapshot))
                    scale = min(target[0] / snapshot.screen_width,
                                target[1] / snapshot.screen_height)
                    display = scene.resize(
                        (max(1, round(snapshot.screen_width * scale)),
                         max(1, round(snapshot.screen_height * scale))),
                        Image.Resampling.LANCZOS,
                    )
                    self.render_results.put(
                        ("ok", key, target, scene, display, scale))
                except Exception:
                    self.render_results.put(
                        ("error", traceback.format_exc()))

            threading.Thread(
                target=worker, daemon=True, name="studio-render").start()
            self.root.after(15, self._poll_render_result)
        except Exception:
            details = traceback.format_exc()
            log = write_crash_log(details)
            self.status.set(f"Preview error recorded in {log}")

    def _poll_render_result(self) -> None:
        try:
            result = self.render_results.get_nowait()
        except queue.Empty:
            self.root.after(30, self._poll_render_result)
            return
        self.rendering = False
        try:
            self._apply_render_result(result)
        except Exception:
            details = traceback.format_exc()
            log = write_crash_log(details)
            self.status.set(f"Preview error recorded in {log}")
        finally:
            if self.render_pending:
                self.render_pending = False
                self.schedule_render()

    def _apply_render_result(self, result) -> None:
        if result[0] == "error":
            log = write_crash_log(result[1])
            self.status.set(f"Preview error recorded in {log}")
            return
        _tag, key, target, scene, display, scale = result
        self.scene_key = key
        self.scene_image = scene
        self.scale = scale
        from PIL import ImageTk
        new_photo = ImageTk.PhotoImage(display, master=self.root)
        self.canvas.delete("all")
        self.photo = new_photo
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        self.canvas.configure(
            scrollregion=(0, 0, display.width, display.height))
        self.rendered_key = key
        self.rendered_target = target
        self.draw_selection()
        problems = self.model.validation()
        self.status.set(
            f"{self.model.screen_width}×{self.model.screen_height} · "
            f"{len(self.model.visible_names())} previewed · "
            + ("no overlaps" if not problems
               else f"{len(problems)} overlap warning(s)")
        )

    def apply_inspector(self) -> None:
        if self.selected is None:
            return
        try:
            state = self.model.windows[self.selected]
            x = int(self.inspector_vars["x"].get())
            y = int(self.inspector_vars["y"].get())
            width = int(self.inspector_vars["width"].get())
            height = int(self.inspector_vars["height"].get())
            if state.resizable:
                self.model.resize(self.selected, width, height)
            elif (width, height) != (state.width, state.height):
                raise ValueError(f"{self.selected} uses a client-fixed XML size.")
            self.model.move(self.selected, x, y)
            self.model.set_visible(self.selected, self.show_var.get())
            self.select(self.selected)
            self.refresh_tree()
            self.schedule_render()
        except Exception as exc:
            self._error(str(exc))

    def choose_color(self, role: str) -> None:
        from tkinter import colorchooser
        selected = colorchooser.askcolor(
            color=self.model.accent_hex[role],
            title=f"Choose {role} accent",
            parent=self.root,
        )[1]
        if selected:
            try:
                self.model.set_accent(role, selected)
                self.refresh_swatches()
                self.schedule_render()
            except Exception as exc:
                self._error(str(exc))

    def _sync_preset_availability(self) -> None:
        """Chat presets rearrange the 3440x1440 chat row only."""
        self.preset_combo.configure(
            state="readonly" if self.model.is_ultrawide() else "disabled")

    def _sync_resolution_controls(self) -> None:
        self.resolution_var.set(RESOLUTIONS[
            (self.model.screen_width, self.model.screen_height)])
        self._sync_preset_availability()

    def change_resolution(self, _event=None) -> None:
        from tkinter import messagebox
        current = (self.model.screen_width, self.model.screen_height)
        size = RESOLUTION_BY_LABEL.get(self.resolution_var.get(), current)
        if size == current:
            return
        if not messagebox.askyesno(
                APP_NAME,
                "Switch the canvas to this game resolution?\n\n"
                "Window geometry resets to the audited release layout for "
                "that resolution. Import your character INI again (or open a "
                "project) to continue from saved positions.",
                parent=self.root):
            self.resolution_var.set(RESOLUTIONS[current])
            return
        self.model.set_resolution(*size)
        self.selected = None
        self.preset_var.set(self.model.preset)
        self._sync_preset_availability()
        self.refresh_tree()
        self.schedule_render()

    def change_preset(self, _event=None) -> None:
        from tkinter import messagebox
        value = self.preset_var.get()
        if value == CUSTOM_PRESET_LABEL:
            return
        if value == self.model.preset and not self.model.preserve_imported_chat:
            return
        if not messagebox.askyesno(
                APP_NAME, "Reset window geometry to this preset?", parent=self.root):
            self.preset_var.set(
                CUSTOM_PRESET_LABEL if self.model.preserve_imported_chat
                else self.model.preset)
            return
        self.model.reset_preset(value)
        self.selected = None
        self.refresh_tree()
        self.schedule_render()

    def open_project(self) -> None:
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            parent=self.root, title="Open SpinUI Studio project",
            filetypes=(("SpinUI Studio", "*.json"), ("All files", "*.*")))
        if not path:
            return
        try:
            self.model.load_project(Path(path))
            self.project_path = Path(path)
            self.preset_var.set(
                CUSTOM_PRESET_LABEL if self.model.preserve_imported_chat
                else self.model.preset)
            self._sync_resolution_controls()
            self.skin_var.set(self.model.skin_name)
            self.ini_var.set(self.model.ini_name)
            self.refresh_tree()
            self.refresh_swatches()
            self.schedule_render()
        except Exception as exc:
            self._error(str(exc))

    def save_project(self) -> None:
        from tkinter import filedialog
        path = self.project_path
        if path is None:
            selected = filedialog.asksaveasfilename(
                parent=self.root, title="Save SpinUI Studio project",
                defaultextension=".json",
                filetypes=(("SpinUI Studio", "*.json"),))
            if not selected:
                return
            path = Path(selected)
        try:
            self._sync_names()
            self.model.save_project(path)
            self.project_path = path
            self.status.set(f"Saved project: {path}")
        except Exception as exc:
            self._error(str(exc))

    def offer_current_ini(self) -> None:
        from tkinter import messagebox
        candidates = discover_character_inis()
        if not candidates:
            return
        current = candidates[0]
        if messagebox.askyesno(
                APP_NAME,
                "Use your current in-game layout as the starting point?\n\n"
                f"{current}\n\n"
                "Studio reads this file only. It will not modify EverQuest.",
                parent=self.root):
            self._load_ini_path(current)

    def import_ini(self) -> None:
        from tkinter import filedialog
        candidates = discover_character_inis()
        options = {}
        if candidates:
            options = {
                "initialdir": str(candidates[0].parent),
                "initialfile": candidates[0].name,
            }
        selected = filedialog.askopenfilename(
            parent=self.root, title="Import EverQuest character UI",
            filetypes=(("EverQuest UI", "UI_*_LO*.ini"), ("INI files", "*.ini")),
            **options)
        if not selected:
            return
        self._load_ini_path(Path(selected))

    def use_downloaded_ui(self) -> None:
        from tkinter import filedialog, messagebox
        selected = filedialog.askdirectory(
            parent=self.root,
            title="Choose a UI folder containing EQUI.xml "
                  "(pick the bundled spinui_reloaded to switch back)")
        if not selected:
            return
        try:
            note = self.model.use_skin(Path(selected))
            self.skin_var.set(self.model.skin_name)
            self.refresh_tree()
            if self.selected is not None:
                self.select(self.selected)
            self.schedule_render()
            self.status.set(note)
            messagebox.showinfo(APP_NAME, note, parent=self.root)
        except Exception as exc:
            self._error(str(exc))

    def _load_ini_path(self, path: Path) -> None:
        try:
            self.model.import_ini(path)
            self.preset_var.set(CUSTOM_PRESET_LABEL)
            self.skin_var.set(self.model.skin_name)
            self.ini_var.set(self.model.ini_name)
            self.refresh_tree()
            self.schedule_render()
            self.status.set(
                f"Imported exact anchors, dimensions, and start states from {path}")
        except Exception as exc:
            self._error(str(exc))

    def export_ini(self) -> None:
        from tkinter import filedialog, messagebox
        try:
            self._sync_names()
        except Exception as exc:
            self._error(str(exc))
            return
        selected = filedialog.asksaveasfilename(
            parent=self.root, title="Export EverQuest character UI",
            initialfile=self.model.ini_name, defaultextension=".ini",
            filetypes=(("EverQuest UI", "*.ini"),))
        if not selected:
            return
        try:
            target = Path(selected)
            if (target.parent / "eqgame.exe").is_file() and not messagebox.askyesno(
                    APP_NAME,
                    "This is an EverQuest game folder. Confirm EverQuest is "
                    "fully closed before Studio writes the character UI.\n\n"
                    "Continue with an automatic timestamped backup?",
                    parent=self.root):
                return
            previous = target.read_bytes() if target.is_file() else None
            self.model.export_ini(target)
            self.status.set(f"Exported game-ready INI: {selected}")
            if previous is not None and target.read_bytes() != previous:
                messagebox.showinfo(
                    APP_NAME,
                    "The INI was exported atomically. The previous file was "
                    "preserved beside it as a timestamped .studio-backup.",
                    parent=self.root,
                )
        except Exception as exc:
            self._error(str(exc))

    def save_preview(self) -> None:
        from tkinter import filedialog
        selected = filedialog.asksaveasfilename(
            parent=self.root, title="Save full-resolution SpinUI preview",
            initialfile="spinui-preview-3440x1440.png", defaultextension=".png",
            filetypes=(("PNG image", "*.png"),))
        if not selected:
            return
        try:
            render_scene(self.model).save(selected, format="PNG", optimize=True)
            self.status.set(f"Saved full-resolution preview: {selected}")
        except Exception as exc:
            self._error(str(exc))

    def build_bundle(self) -> None:
        from tkinter import filedialog, messagebox
        try:
            self._sync_names()
        except Exception as exc:
            self._error(str(exc))
            return
        parent = filedialog.askdirectory(
            parent=self.root, title="Choose parent folder for the custom build")
        if not parent:
            return
        destination = Path(parent) / f"{self.model.skin_name}-bundle"
        try:
            result = self.model.build_bundle(destination)
            messagebox.showinfo(
                APP_NAME,
                f"Built the final UI and INI without changing your game files:\n\n{result}",
                parent=self.root,
            )
            self.status.set(f"Built final UI bundle: {result}")
        except Exception as exc:
            self._error(str(exc))

    def _sync_names(self) -> None:
        self.model.skin_name = safe_skin_name(self.skin_var.get())
        self.model.ini_name = safe_ini_name(self.ini_var.get())

    def _error(self, message: str) -> None:
        from tkinter import messagebox
        messagebox.showerror(APP_NAME, message, parent=self.root)
        self.status.set(message)

    def report_callback_exception(self, exc_type, exc_value, exc_tb) -> None:
        details = "".join(
            traceback.format_exception(exc_type, exc_value, exc_tb))
        log = write_crash_log(details)
        self.status.set(f"Recovered from an error; details: {log}")
        try:
            from tkinter import messagebox
            messagebox.showerror(
                APP_NAME,
                "Studio recovered from an unexpected error instead of "
                f"closing.\n\nDetails were saved to:\n{log}",
                parent=self.root,
            )
        except Exception:
            pass

    def run(self) -> None:
        self.root.mainloop()


def selftest() -> int:
    if getattr(sys, "frozen", False):
        # A one-file build can import tkinter yet still omit the Tcl runtime
        # data needed when the real GUI opens. Exercise that packaged-only
        # dependency without requiring a visible desktop.
        import tkinter as tk
        interpreter = tk.Tcl()
        assert interpreter.eval("info patchlevel")
    assert safe_skin_name("spinui_custom") == "spinui_custom"
    for invalid in ("", "1bad", "x", "bad name"):
        try:
            safe_skin_name(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe skin name accepted: {invalid!r}")
    assert safe_ini_name("UI_Spin_qeynos_LO1.ini")
    assert reference_pixel("center", -0.21511627, 3440, 360) == 1170
    assert reference_pixel("center", 0.20486109, 1440, 193) == 771
    assert reference_pixel("right", 0.02238372, 3440, 230) == 3133
    custom = accent_palette(
        venom=(40, 180, 210), gold=(190, 120, 40), ember=(230, 80, 55))
    assert custom["CYAN"] == (40, 180, 210)
    venom_only = accent_palette(venom=(40, 180, 210))
    assert venom_only["GOLD_DEEP"] == DEFAULT_ACCENTS["GOLD_DEEP"]
    assert venom_only["GOLD_BRIGHT"] == DEFAULT_ACCENTS["GOLD_BRIGHT"]
    model = StudioModel()
    assert (model.screen_width, model.screen_height) == DEFAULT_SCREEN
    assert model.windows["MainChat"].width == 820
    assert model.windows["PetInfoWindow"].height == 181
    model.move("PlayerWindow", -100, 99999)
    player = model.windows["PlayerWindow"]
    assert player.x == 0 and player.y == model.screen_height - player.height
    try:
        model.resize("PlayerWindow", 500, 500)
    except ValueError:
        pass
    else:
        raise AssertionError("client-fixed window resized")
    model.resize("MainChat", 640, 220)
    assert (model.windows["MainChat"].width, model.windows["MainChat"].height) == (640, 220)
    model.set_accent("venom", "#28b4d2")
    model.set_accent("gold", "#be7828")
    assert model.palette["CYAN"] == (40, 180, 210)

    sample = (
        "<TextColor><R>52</R><G>218</G><B>190</B></TextColor>"
        "<Tint><R>219</R><G>158</G><B>42</B></Tint>"
    )
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        skin = root / "skin"
        skin.mkdir()
        xml = skin / "sample.xml"
        xml.write_text(sample, encoding="utf-8")
        count = apply_palette_to_xml(skin, model.palette)
        assert count == 2
        revised = xml.read_text(encoding="utf-8")
        assert "<R>40</R><G>180</G><B>210</B>" in revised

        project = root / "project.json"
        model.save_project(project)
        clone = StudioModel()
        clone.load_project(project)
        assert clone.accent_hex == model.accent_hex
        assert clone.windows["MainChat"].width == 640
        assert clone.windows["MainChat"].show_on_load is None

        exported = root / DEFAULT_INI_NAME
        model.export_ini(exported)
        exported_text = exported.read_text(encoding="utf-8")
        assert f"UISkin={model.skin_name}" in exported_text
        assert "Width=640" in exported_text
        exported.write_text("stale\n", encoding="utf-8")
        model.export_ini(exported)
        assert len(list(root.glob(
            f"{DEFAULT_INI_NAME}.studio-backup-*"))) == 1
        model.export_ini(exported)
        assert len(list(root.glob(
            f"{DEFAULT_INI_NAME}.studio-backup-*"))) == 1

        # The historical Spin profile contains all three EQ anchor modes and
        # user-scaled horizontal/vertical controls. Importing and exporting it
        # must preserve the actual pixels, sizes, start states, and chat map.
        live_source = model.root / "layouts" / "spin-live" / DEFAULT_INI_NAME
        live = StudioModel()
        live.import_ini(live_source)
        assert (live.windows["PlayerWindow"].x,
                live.windows["PlayerWindow"].y) == (1170, 771)
        assert live.windows["Chat 1"].x == 713
        assert (live.windows["CastSpellWnd"].width,
                live.windows["CastSpellWnd"].height) == (595, 48)
        visible_hotbars = [
            name for name in live.ordered_names()
            if name.startswith("HotButtonWnd") and live.windows[name].visible
        ]
        assert visible_hotbars == ["HotButtonWnd", "HotButtonWnd2"]
        assert live.preserve_imported_chat
        live.windows["PetInfoWindow"].visible = True
        assert live.windows["PetInfoWindow"].show_on_load is False
        live_project = root / "live-project.json"
        live.save_project(live_project)
        live_clone = StudioModel()
        live_clone.load_project(live_project)
        assert live_clone.base_ini_text == live.base_ini_text
        assert live_clone.preserve_imported_chat
        live_export = root / "live-export.ini"
        live.export_ini(live_export)
        live_roundtrip = StudioModel()
        live_roundtrip.import_ini(live_export)
        for name, expected in live.windows.items():
            actual = live_roundtrip.windows[name]
            assert (
                actual.x, actual.y, actual.width, actual.height,
                actual.show_on_load,
            ) == (
                expected.x, expected.y, expected.width, expected.height,
                expected.show_on_load,
            ), name
        original_chat = dict(
            layout.parse_ini(live_source.read_text(encoding="utf-8"))
        )["ChatManager"]
        exported_chat = dict(
            layout.parse_ini(live_export.read_text(encoding="utf-8"))
        )["ChatManager"]
        assert exported_chat == original_chat
        assert dict(
            layout.parse_ini(live_export.read_text(encoding="utf-8"))
        )["PetInfoWindow"].count("Show=0") == 1

        # The player's own transparency and fade settings must survive the
        # import → export round trip untouched; presets force styling only
        # onto fresh, non-imported layouts.
        original_sections = dict(
            layout.parse_ini(live_source.read_text(encoding="utf-8")))
        exported_sections = dict(
            layout.parse_ini(live_export.read_text(encoding="utf-8")))
        for section in ("MainChat", "Chat 1", "Chat 2", "MapViewWnd"):
            for key in ("Alpha=", "FadeToAlpha=", "Fades="):
                wanted = [line for line in original_sections[section]
                          if line.startswith(key)]
                got = [line for line in exported_sections[section]
                       if line.startswith(key)]
                assert got == wanted, (section, key, got, wanted)

        # Every supported game resolution drives its audited release table,
        # exports with the matching chat font, and round-trips exactly.
        for size, expected_player, font in (
                ((2560, 1440), (956, 770), layout.CHAT_FONT_1440),
                ((3840, 2160), (1388, 1470), layout.CHAT_FONT_2160)):
            scaled = StudioModel(resolution=size)
            assert (scaled.screen_width, scaled.screen_height) == size
            scaled_player = scaled.windows["PlayerWindow"]
            assert (scaled_player.x, scaled_player.y) == expected_player, size
            assert not [problem for problem in scaled.validation()
                        if "off-screen" in problem], size
            scaled_text = scaled.export_ini_text()
            assert f"ChatWindow0_FontStyle={font}" in scaled_text
            scaled_path = root / f"scaled-{size[0]}x{size[1]}.ini"
            scaled.export_ini(scaled_path)
            reimport = StudioModel(resolution=size)
            reimport.import_ini(scaled_path)
            for name, expected_state in scaled.windows.items():
                actual = reimport.windows[name]
                assert (actual.x, actual.y, actual.width, actual.height) == (
                    expected_state.x, expected_state.y,
                    expected_state.width, expected_state.height), (size, name)
            scaled_project = root / f"scaled-{size[0]}x{size[1]}.json"
            scaled.save_project(scaled_project)
            restored = StudioModel()
            restored.load_project(scaled_project)
            assert (restored.screen_width, restored.screen_height) == size
            assert restored.windows["PlayerWindow"].x == expected_player[0]
            assert render_scene(scaled).size == size

        # Downloaded third-party UI support: Studio adopts the skin's declared
        # window footprints, targets its UISkin=, survives preset/resolution
        # resets and project round trips, and reverts cleanly to SpinUI.
        parsed_spinui, _ = read_skin_geometry(
            model.source_skin, ["PlayerWindow", "PetInfoWindow", "BuffWindow_13"])
        assert parsed_spinui["PlayerWindow"] == (360, 193)
        assert parsed_spinui["PetInfoWindow"] == (513, 181)
        assert parsed_spinui["BuffWindow_13"] == (216, 640)  # variant mapping

        third_party = root / "third_party_ui"
        third_party.mkdir()
        (third_party / "EQUI.xml").write_text(
            "<XML>"
            "<Include>EQUI_PlayerWindow.xml</Include>"
            "<Include>EQUI_TargetWindow.xml</Include>"
            "<Include>EQUI_GroupWindow.xml</Include>"
            "<Include>broken.xml</Include>"
            "</XML>",
            encoding="utf-8")
        (third_party / "EQUI_PlayerWindow.xml").write_text(
            "<XML><Screen item='PlayerWindow'>"
            "<Size><CX>300</CX><CY>150</CY></Size></Screen></XML>",
            encoding="utf-8")
        (third_party / "EQUI_TargetWindow.xml").write_text(
            "<XML><Screen item='TargetWindow'>"
            "<Size><CX>210</CX><CY>90</CY></Size></Screen></XML>",
            encoding="utf-8")
        # Declared but runtime-sized by the client: must NOT be adopted.
        (third_party / "EQUI_GroupWindow.xml").write_text(
            "<XML><Screen item='GroupWindow'>"
            "<Size><CX>210</CX><CY>70</CY></Size></Screen></XML>",
            encoding="utf-8")
        (third_party / "broken.xml").write_text(
            "<XML><unclosed>", encoding="utf-8")
        skinned = StudioModel()
        chat_width = skinned.windows["MainChat"].width
        note = skinned.use_skin(third_party)
        assert "third_party_ui" in note and "2 windows" in note
        assert (skinned.windows["PlayerWindow"].width,
                skinned.windows["PlayerWindow"].height) == (300, 150)
        assert (skinned.windows["TargetWindow"].width,
                skinned.windows["TargetWindow"].height) == (210, 90)
        assert (skinned.windows["GroupWindow"].width,
                skinned.windows["GroupWindow"].height) == (230, 204)
        assert skinned.windows["MainChat"].width == chat_width  # INI-sized
        assert skinned.skin_name == "third_party_ui"
        skinned_text = skinned.export_ini_text()
        assert "UISkin=third_party_ui" in skinned_text
        assert dict(layout.parse_ini(skinned_text))[
            "PlayerWindow"].count("Width=300") == 1
        skinned.reset_preset("hybrid")
        assert skinned.windows["PlayerWindow"].width == 300
        skinned.set_resolution(2560, 1440)
        assert skinned.windows["PlayerWindow"].width == 300
        try:
            skinned.build_bundle(root / "never-built")
        except ValueError:
            pass
        else:
            raise AssertionError("build allowed with a downloaded UI active")
        skinned_project = root / "skinned.json"
        skinned.save_project(skinned_project)
        skinned_clone = StudioModel()
        skinned_clone.load_project(skinned_project)
        assert skinned_clone.custom_skin == third_party.resolve()
        assert skinned_clone.windows["PlayerWindow"].width == 300
        assert render_scene(skinned_clone).size == (2560, 1440)
        revert = skinned.use_skin(skinned.source_skin)
        assert skinned.custom_skin is None and "SpinUI" in revert
        assert (skinned.windows["PlayerWindow"].width,
                skinned.windows["PlayerWindow"].height) == (360, 193)
        try:
            skinned.use_skin(root)  # no EQUI.xml here
        except ValueError:
            pass
        else:
            raise AssertionError("non-UI folder accepted as a skin")

        ultra = StudioModel()
        eq_main = ultra.windows["EQMainWnd"]
        assert (eq_main.x, eq_main.y) == (
            3440 - 8 - eq_main.width, 1440 - 4 - eq_main.height)
        ultra.set_resolution(2560, 1440)
        assert ultra.windows["PlayerWindow"].x == 956
        try:
            ultra.set_resolution(1920, 1080)
        except ValueError:
            pass
        else:
            raise AssertionError("unsupported resolution accepted")

        image = render_scene(model)
        assert image.size == DEFAULT_SCREEN
        # The GUI paints from an immutable snapshot on a worker thread; the
        # snapshot must render pixel-identically to the live model.
        snapshot_image = render_scene(RenderSnapshot(model))
        assert snapshot_image.tobytes() == image.tobytes()
        image.resize((860, 360), Image.Resampling.LANCZOS).save(
            root / "preview.png")
        assert (root / "preview.png").stat().st_size > 20_000

        # Exercise every selectable window independently, including all four
        # pet variants, then together. A single bad renderer must fail CI
        # instead of becoming a user-facing GUI crash.
        toggle_model = StudioModel()
        toggle_names = toggle_model.ordered_names()
        for selected_name in toggle_names:
            for name in toggle_names:
                toggle_model.windows[name].visible = name == selected_name
            assert render_scene(toggle_model).size == DEFAULT_SCREEN
        for name in toggle_names:
            toggle_model.windows[name].visible = True
        assert render_scene(toggle_model).size == DEFAULT_SCREEN

        texture_out = root / "custom-skin"
        painted = texture_builder.generate(
            source_skin=model.source_skin,
            output_skin=texture_out,
            palette=model.palette,
            preview_dir=None,
            quiet=True,
        )
        assert len(painted) == 12
        for name in painted:
            header = (texture_out / name).read_bytes()[:18]
            assert len(header) == 18 and header[2] == 2

        mini_root = root / "release"
        mini_skin = mini_root / "spinui_reloaded"
        mini_skin.mkdir(parents=True)
        shutil.copy2(model.source_skin / "EQUI.xml", mini_skin / "EQUI.xml")
        shutil.copy2(
            model.source_skin / "default1440.ini", mini_skin / "default1440.ini")
        for name in painted:
            shutil.copy2(model.source_skin / name, mini_skin / name)
        mini_layout = mini_root / "layouts" / "spin-live"
        mini_layout.mkdir(parents=True)
        mini_layout.joinpath(DEFAULT_INI_NAME).write_text(
            model._default_ini_text(), encoding="utf-8")
        bundle_model = StudioModel(mini_root)
        bundle_model.set_accent("venom", "#28b4d2")
        bundle = bundle_model.build_bundle(root / "final-bundle")
        assert (bundle / bundle_model.skin_name / "EQUI.xml").is_file()
        assert (bundle / bundle_model.ini_name).is_file()
        assert (bundle / "spinui-studio.json").is_file()

    print(
        "SpinUI Studio selftest: ALL PASS\n"
        "  63 window toggles | exact INI round-trip at 3440/2560/4K | "
        "imported styling preserved | downloaded-UI geometry | project | "
        "palette XML/TGA | bundle"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--render-preview", type=Path)
    parser.add_argument("--project", type=Path)
    parser.add_argument("--ini", type=Path,
                        help="start from an EverQuest character UI INI")
    supported = ", ".join(f"{w}x{h}" for w, h in RESOLUTIONS)
    parser.add_argument("--resolution", metavar="WxH",
                        help=f"canvas game resolution ({supported})")
    parser.add_argument("--skin", type=Path, metavar="FOLDER",
                        help="adopt a downloaded UI folder's window geometry")
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    try:
        # Everything from asset discovery onward runs inside the guard so a
        # packaged --windowed build reports startup problems (for example a
        # SpinUIStudio.exe moved away from its release assets) instead of
        # exiting silently.
        model = StudioModel()
        if args.resolution is not None:
            match = re.fullmatch(r"(\d{3,4})\s*[xX×]\s*(\d{3,4})",
                                 args.resolution.strip())
            if match is None:
                raise ValueError(
                    f"--resolution must look like 2560x1440, got "
                    f"{args.resolution!r}")
            model.set_resolution(int(match.group(1)), int(match.group(2)))
        if args.skin is not None:
            print(model.use_skin(args.skin))
        if args.project is not None:
            model.load_project(args.project)
        if args.ini is not None:
            model.import_ini(args.ini)
        if args.render_preview is not None:
            output = args.render_preview.resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            render_scene(model).convert("RGB").save(output, quality=94)
            print(f"wrote {output}")
            return 0
        StudioApp(
            model, args.project,
            offer_import=args.project is None and args.ini is None,
        ).run()
    except Exception as exc:
        details = traceback.format_exc()
        log = write_crash_log(details)
        if getattr(sys, "frozen", False):
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0,
                    f"SpinUI Studio could not start.\n\n{exc}\n\n"
                    f"Details: {log}",
                    APP_NAME,
                    0x10,
                )
            except Exception:
                pass
            return 1
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
