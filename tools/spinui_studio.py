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
import re
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass
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
PROJECT_SCHEMA = 1
DEFAULT_SCREEN = (3440, 1440)
DEFAULT_SKIN_NAME = "spinui_custom"
DEFAULT_INI_NAME = "UI_Spin_qeynos_LO1.ini"

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
        return round(total / 2 + fraction * total - size / 2)
    raise ValueError(f"unsupported INI reference: {ref}")


@dataclass
class WindowState:
    name: str
    x: int
    y: int
    width: int
    height: int
    visible: bool
    resizable: bool

    def rect(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.x + self.width, self.y + self.height


RESIZABLE = {
    "MainChat", "Chat 1", "Chat 2", "Chat 3",
    "MapViewWnd", "TrackingWnd", "ExtendedTargetWnd",
    "BuffWindow", "BuffWindow_13",
    "ShortDurationBuffWindow", "ShortDurationBuffWindow_13",
    "PetInfoWindow_1", "PetInfoWindow_2", "PetInfoWindow_3",
    "BigBankWnd",
}

MINIMUMS = {
    "MainChat": (360, 160), "Chat 1": (360, 160), "Chat 2": (360, 160),
    "Chat 3": (360, 160), "MapViewWnd": (360, 280),
    "TrackingWnd": (260, 220), "ExtendedTargetWnd": (150, 180),
    "BuffWindow": (160, 160), "ShortDurationBuffWindow": (160, 120),
    "PetInfoWindow_1": (356, 209), "PetInfoWindow_2": (356, 209),
    "PetInfoWindow_3": (441, 181), "BigBankWnd": (287, 280),
}

FALLBACK_SIZES = {
    "EQMainWnd": (392, 36),
    "TargetOfTargetWindow": (240, 53),
    "StanceWnd": (440, 56),
    "CastingWindow": (380, 36),
    "AggroMeterWnd": (220, 48),
}


class StudioModel:
    def __init__(self, root: Path | None = None, *,
                 resolution: tuple[int, int] = DEFAULT_SCREEN,
                 preset: str = layout.DEFAULT_PRESET):
        self.root = (root or release_root()).resolve()
        self.source_skin = self.root / "spinui_reloaded"
        if not (self.source_skin / "EQUI.xml").is_file():
            raise FileNotFoundError(
                f"{APP_NAME} needs a spinui_reloaded folder beside the application: "
                f"{self.source_skin}"
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
        self.reset_preset(preset)

    def reset_preset(self, preset: str) -> None:
        if preset not in layout.CHAT_PRESETS:
            raise ValueError(f"unknown layout preset: {preset}")
        self.preset = preset
        specs = layout.preset_placements(preset)
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
            )
        # EQMain is generated separately from PLACEMENTS.
        eq_width, eq_height = FALLBACK_SIZES["EQMainWnd"]
        windows["EQMainWnd"] = WindowState(
            "EQMainWnd",
            self.screen_width - 8 - eq_width,
            self.screen_height - 8 - eq_height,
            eq_width, eq_height, True, False,
        )
        self.windows = windows

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

    def ordered_names(self) -> list[str]:
        preferred = list(layout.preset_placements(self.preset))
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
        original = layout.preset_placements(self.preset)
        for name, source in original.items():
            state = self.windows[name]
            spec = {key: value for key, value in source.items() if key != "_rect"}
            spec.update({
                "XRef": "left", "YRef": "top",
                "XPos": percent(state.x, self.screen_width),
                "YPos": percent(state.y, self.screen_height),
                "Width": str(state.width), "Height": str(state.height),
                "Show": "1" if state.visible else "0",
                "_rect": (state.x, state.y, state.width, state.height),
            })
            specs[name] = spec
        eq = self.windows["EQMainWnd"]
        eqmain = {
            "XRef": "left", "YRef": "top",
            "XPos": percent(eq.x, self.screen_width),
            "YPos": percent(eq.y, self.screen_height),
            "Width": str(eq.width), "Height": str(eq.height),
            "Show": "1" if eq.visible else "0",
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
            skin_name=self.skin_name,
        )
        default_path = self.source_skin / "default1440.ini"
        if default_path.is_file():
            transformed = layout.merge_missing(
                transformed, default_path.read_text(encoding="utf-8", errors="replace"))
        return transformed

    def export_ini(self, path: Path) -> Path:
        path = path.resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.export_ini_text(), encoding="utf-8", newline="\r\n")
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
            width = int(section.get("Width", state.width))
            height = int(section.get("Height", state.height))
            x_fraction = parse_percent(section.get("XPos", percent(state.x, self.screen_width)))
            y_fraction = parse_percent(section.get("YPos", percent(state.y, self.screen_height)))
            x = reference_pixel(section.get("XRef", "left"), x_fraction,
                                self.screen_width, width)
            y = reference_pixel(section.get("YRef", "top"), y_fraction,
                                self.screen_height, height)
            if state.resizable:
                self.resize(name, width, height)
            self.move(name, x, y)
            if "Show" in section:
                state.visible = section["Show"].strip() == "1"
        main_name = by_folded.get("main")
        if main_name is not None:
            self.skin_name = parser[main_name].get("UISkin", self.skin_name)
        self.base_ini_text = text

    def project_payload(self) -> dict:
        return {
            "schema": PROJECT_SCHEMA,
            "resolution": [self.screen_width, self.screen_height],
            "preset": self.preset,
            "skin_name": self.skin_name,
            "ini_name": self.ini_name,
            "accents": dict(self.accent_hex),
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
        if payload.get("schema") != PROJECT_SCHEMA:
            raise ValueError("Unsupported SpinUI Studio project schema.")
        width, height = payload["resolution"]
        self.screen_width, self.screen_height = int(width), int(height)
        self.reset_preset(payload["preset"])
        self.skin_name = safe_skin_name(payload["skin_name"])
        self.ini_name = safe_ini_name(payload["ini_name"])
        self.accent_hex = dict(payload["accents"])
        self.palette = palette_from_hex(self.accent_hex)
        for name, values in payload["windows"].items():
            if name not in self.windows:
                continue
            state = self.windows[name]
            state.x = int(values["x"])
            state.y = int(values["y"])
            state.width = int(values["width"])
            state.height = int(values["height"])
            state.visible = bool(values["visible"])
            self.move(name, state.x, state.y)

    def build_bundle(self, destination: Path) -> Path:
        """Build a non-destructive, ready-to-install skin + character INI."""
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
    pixels = [
        replacements.get(pixel[:3], pixel[:3]) + (pixel[3],)
        for pixel in image.getdata()
    ]
    image.putdata(pixels)
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


def render_scene(model: StudioModel) -> Image.Image:
    apply_preview_palette(model.palette)
    preview.SKIN = model.source_skin
    preview.TEX.clear()
    canvas = studio_background(model.screen_width, model.screen_height)
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
            preview.draw_gems(canvas, x, y, width, height)
        elif name == "HotButtonWnd" or name == "HotButtonWnd11":
            preview.draw_hotbar_v(canvas, x, y, width, height, 1)
        elif name.startswith("HotButtonWnd"):
            suffix = name.removeprefix("HotButtonWnd")
            page = int(suffix) if suffix.isdigit() else 1
            preview.draw_hotbar_h(canvas, x, y, width, height, page, 8)
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
    def __init__(self, model: StudioModel, project: Path | None = None):
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
        self.drag_origin: tuple[int, int, int, int] | None = None
        self.photo = None
        self.scale = .4
        self.project_path = project
        self.status = tk.StringVar(value="Ready")
        self.preset_var = tk.StringVar(value=model.preset)
        self.skin_var = tk.StringVar(value=model.skin_name)
        self.ini_var = tk.StringVar(value=model.ini_name)
        self.show_var = tk.BooleanVar(value=False)
        self.inspector_vars = {
            key: tk.StringVar() for key in ("x", "y", "width", "height")
        }
        self._build()
        self.root.after(50, self.render)

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
            ("IMPORT INI", self.import_ini),
            ("EXPORT INI", self.export_ini),
            ("SAVE PREVIEW", self.save_preview),
            ("BUILD FINAL UI", self.build_bundle),
        ):
            tk.Button(
                toolbar, text=label, command=command, bg=RAISED, fg=TEXT,
                activebackground=CYAN, activeforeground=BG, relief="flat",
                padx=10, pady=5,
            ).pack(side="left", padx=3)
        tk.Label(toolbar, text="Preset", bg=PANEL, fg=DIM).pack(
            side="left", padx=(18, 5))
        preset = ttk.Combobox(
            toolbar, textvariable=self.preset_var, state="readonly", width=14,
            values=tuple(layout.CHAT_PRESETS),
        )
        preset.pack(side="left")
        preset.bind("<<ComboboxSelected>>", self.change_preset)

        body = tk.PanedWindow(
            self.root, orient="horizontal", bg=BG, sashwidth=6,
            sashrelief="flat")
        body.pack(fill="both", expand=True)

        left = tk.Frame(body, bg=PANEL, width=270)
        body.add(left, minsize=230)
        tk.Label(
            left, text="WINDOWS", bg=PANEL, fg=GOLD_BRIGHT,
            font=("Segoe UI Semibold", 10),
        ).pack(anchor="w", padx=12, pady=(12, 6))
        self.tree = ttk.Treeview(
            left, columns=("show", "geometry"), show="headings", selectmode="browse")
        self.tree.heading("show", text="ON")
        self.tree.heading("geometry", text="WINDOW · X,Y · W×H")
        self.tree.column("show", width=38, anchor="center", stretch=False)
        self.tree.column("geometry", width=220, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.tree.bind("<<TreeviewSelect>>", self.select_tree)
        self.tree.bind("<Double-1>", self.toggle_tree)
        tk.Label(
            left,
            text="Double-click a row to show/hide it.\nDrag visible windows directly on the preview.",
            bg=PANEL, fg=DIM, justify="left", wraplength=240,
        ).pack(anchor="w", padx=12, pady=(0, 12))

        center = tk.Frame(body, bg="#05070a")
        body.add(center, minsize=650, stretch="always")
        self.canvas = tk.Canvas(
            center, bg="#05070a", highlightthickness=0, cursor="arrow")
        self.canvas.pack(fill="both", expand=True, padx=6, pady=6)
        self.canvas.bind("<Button-1>", self.canvas_down)
        self.canvas.bind("<B1-Motion>", self.canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self.canvas_up)
        self.canvas.bind("<Configure>", lambda _event: self.root.after_idle(self.render))

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
            parent, text="Visible", variable=self.show_var,
            command=self.apply_inspector, bg=PANEL, fg=TEXT,
            selectcolor=RAISED, activebackground=PANEL,
            activeforeground=TEXT,
        ).pack(anchor="w", padx=14, pady=(8, 4))
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

    def refresh_tree(self) -> None:
        selected = self.selected
        for item in self.tree.get_children():
            self.tree.delete(item)
        for name in self.model.ordered_names():
            state = self.model.windows[name]
            geometry = f"{name} · {state.x},{state.y} · {state.width}×{state.height}"
            self.tree.insert(
                "", "end", iid=name, values=("●" if state.visible else "○", geometry))
        if selected in self.model.windows:
            self.tree.selection_set(selected)

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
        name = selection[0]
        self.model.set_visible(name, not self.model.windows[name].visible)
        self.select(name)
        self.refresh_tree()
        self.render()

    def canvas_point(self, event) -> tuple[int, int]:
        return round(event.x / self.scale), round(event.y / self.scale)

    def canvas_down(self, event) -> None:
        gx, gy = self.canvas_point(event)
        chosen = None
        for name in reversed(self.model.visible_names()):
            x0, y0, x1, y1 = self.model.windows[name].rect()
            if x0 <= gx < x1 and y0 <= gy < y1:
                chosen = name
                break
        self.select(chosen)
        if chosen is not None:
            state = self.model.windows[chosen]
            self.drag_origin = (gx, gy, state.x, state.y)

    def canvas_drag(self, event) -> None:
        if self.selected is None or self.drag_origin is None:
            return
        gx, gy = self.canvas_point(event)
        start_x, start_y, window_x, window_y = self.drag_origin
        self.model.move(
            self.selected,
            window_x + gx - start_x,
            window_y + gy - start_y,
        )
        state = self.model.windows[self.selected]
        self.inspector_vars["x"].set(str(state.x))
        self.inspector_vars["y"].set(str(state.y))
        self.draw_selection()

    def canvas_up(self, _event) -> None:
        if self.drag_origin is not None:
            self.drag_origin = None
            self.refresh_tree()
            self.render()

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

    def render(self) -> None:
        self.model.skin_name = self.skin_var.get().strip() or DEFAULT_SKIN_NAME
        self.model.ini_name = self.ini_var.get().strip() or DEFAULT_INI_NAME
        self.status.set("Rendering pixel-accurate geometry…")
        self.root.update_idletasks()
        image = render_scene(self.model)
        available_width = max(320, self.canvas.winfo_width())
        available_height = max(240, self.canvas.winfo_height())
        self.scale = min(
            available_width / self.model.screen_width,
            available_height / self.model.screen_height,
        )
        display = image.resize(
            (max(1, round(image.width * self.scale)),
             max(1, round(image.height * self.scale))),
            Image.Resampling.LANCZOS,
        )
        from PIL import ImageTk
        self.photo = ImageTk.PhotoImage(display)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        self.canvas.configure(
            scrollregion=(0, 0, display.width, display.height))
        self.draw_selection()
        problems = self.model.validation()
        self.status.set(
            f"{self.model.screen_width}×{self.model.screen_height} · "
            f"{len(self.model.visible_names())} visible · "
            + ("no overlaps" if not problems else f"{len(problems)} overlap warning(s)")
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
            self.render()
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
                self.render()
            except Exception as exc:
                self._error(str(exc))

    def change_preset(self, _event=None) -> None:
        from tkinter import messagebox
        value = self.preset_var.get()
        if value == self.model.preset:
            return
        if not messagebox.askyesno(
                APP_NAME, "Reset window geometry to this preset?", parent=self.root):
            self.preset_var.set(self.model.preset)
            return
        self.model.reset_preset(value)
        self.selected = None
        self.refresh_tree()
        self.render()

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
            self.preset_var.set(self.model.preset)
            self.skin_var.set(self.model.skin_name)
            self.ini_var.set(self.model.ini_name)
            self.refresh_tree()
            self.refresh_swatches()
            self.render()
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

    def import_ini(self) -> None:
        from tkinter import filedialog
        selected = filedialog.askopenfilename(
            parent=self.root, title="Import EverQuest character UI",
            filetypes=(("EverQuest UI", "UI_*_LO*.ini"), ("INI files", "*.ini")))
        if not selected:
            return
        try:
            self.model.import_ini(Path(selected))
            self.skin_var.set(self.model.skin_name)
            self.refresh_tree()
            self.render()
            self.status.set(f"Imported geometry from {selected}")
        except Exception as exc:
            self._error(str(exc))

    def export_ini(self) -> None:
        from tkinter import filedialog
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
            self.model.export_ini(Path(selected))
            self.status.set(f"Exported game-ready INI: {selected}")
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

        exported = root / DEFAULT_INI_NAME
        model.export_ini(exported)
        exported_text = exported.read_text(encoding="utf-8")
        assert f"UISkin={model.skin_name}" in exported_text
        assert "Width=640" in exported_text

        image = render_scene(model)
        assert image.size == DEFAULT_SCREEN
        image.resize((860, 360), Image.Resampling.LANCZOS).save(
            root / "preview.png")
        assert (root / "preview.png").stat().st_size > 20_000

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
        "  project round-trip | INI export | palette XML/TGA | 3440 preview | bundle"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--render-preview", type=Path)
    parser.add_argument("--project", type=Path)
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    model = StudioModel()
    if args.project is not None:
        model.load_project(args.project)
    if args.render_preview is not None:
        output = args.render_preview.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        render_scene(model).convert("RGB").save(output, quality=94)
        print(f"wrote {output}")
        return 0
    StudioApp(model, args.project).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
