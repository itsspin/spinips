#!/usr/bin/env python3
"""Build the optional, binding-safe ``spinui_glass`` EverQuest skin.

The canonical SpinUI XML remains the source of truth.  This builder mirrors
that complete payload, changes presentation-only colors, retargets a small set
of native controls to a dedicated Glass atlas, and paints every shared chrome
surface plus a purpose-built dark glass spellbook.  Screen IDs, EQTypes,
pieces, sizes, and gameplay bindings are never rewritten.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

import generate_spinui_textures as chrome
from spinui_glass_theme import (FROST, FROST_DIM, ICE, ICE_BRIGHT, MINT,
                                MINT_BRIGHT, VIOLET, VISUAL_COLOR_TAGS,
                                XML_TEXT_COLOR_MAP, texture_tokens)


REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "spinui_reloaded"
OUTPUT = REPO / "spinui_glass"
THEME_README_NAME = "GLASS_THEME.md"
SPELLBOOK_MENU_NAME = "Glass Codex - Large Spell Icons"
ANIMATIONS = SOURCE / "EQUI_Animations.xml"
CONTROL_ATLAS = "spin_glass_controls.tga"
TEXT_SUFFIXES = {".xml", ".ini", ".md", ".txt"}

GLASS_README = """# SpinUI Glass

SpinUI Glass is the optional midnight-glass visual variant of SpinUI Reloaded.
It preserves the same Legends-safe bindings, layouts, resizable windows, Spell
Ledger, responsive Extended Targets, and command-center behavior.

Install the release normally, then choose **spinui_glass** from EverQuest's
Load UI Skin window, or run `/loadskin spinui_glass 1` to preserve positions.

The skin is generated from `spinui_reloaded` by
`python tools/build_spinui_glass.py`; do not hand-edit generated files.
"""

COLOR_PATTERN = re.compile(
    rf"(<(?P<tag>{'|'.join(sorted(VISUAL_COLOR_TAGS))})>\s*<R>)"
    r"(?P<r>\d+)(</R>\s*<G>)(?P<g>\d+)(</G>\s*<B>)"
    r"(?P<b>\d+)(</B>\s*</(?P=tag)>)",
    re.DOTALL,
)


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def comparable_payload(name: str, payload: bytes) -> bytes:
    """Normalize checkout-controlled newlines while keeping assets exact.

    GitHub's Windows runners can materialize tracked text with CRLF even when
    the repository blob uses LF.  Generated XML is semantically identical in
    either form; TGA, DDS, cursor, and other binary payloads must remain
    byte-for-byte deterministic.
    """
    if Path(name).suffix.casefold() in TEXT_SUFFIXES:
        return payload.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return payload


def remap_visual_colors(text: str) -> str:
    def replacement(match: re.Match[str]) -> str:
        current = tuple(int(match.group(key)) for key in ("r", "g", "b"))
        wanted = XML_TEXT_COLOR_MAP.get(current)
        if wanted is None:
            return match.group(0)
        return (
            match.group(1) + str(wanted[0]) + match.group(4)
            + str(wanted[1]) + match.group(6) + str(wanted[2])
            + match.group(8)
        )

    return COLOR_PATTERN.sub(replacement, text)


def add_spellbook_text_colors(text: str) -> str:
    wanted = re.compile(
        r"<StaticText item=\"SBW_(?:SpellName\d+|LeftPageNum|RightPageNum)\">"
        r".*?</StaticText>",
        re.DOTALL,
    )
    color = (
        "\n\t\t<TextColor>\n"
        f"\t\t\t<R>{FROST[0]}</R>\n"
        f"\t\t\t<G>{FROST[1]}</G>\n"
        f"\t\t\t<B>{FROST[2]}</B>\n"
        "\t\t</TextColor>"
    )

    def replacement(match: re.Match[str]) -> str:
        block = match.group(0)
        if "<TextColor>" in block:
            return block
        marker = re.search(r"\n\t\t<(?:Font|ScreenID)>.*?</(?:Font|ScreenID)>", block)
        if marker is None:
            raise ValueError("spellbook text item has no color insertion anchor")
        return block[:marker.end()] + color + block[marker.end():]

    text = wanted.sub(replacement, text)
    return text.replace(
        "<MenuName>Large Spell Icons, Classic Layout</MenuName>",
        f"<MenuName>{SPELLBOOK_MENU_NAME}</MenuName>",
    )


def animation_frames(path: Path = ANIMATIONS) -> dict[str, tuple[str, int, int, int, int]]:
    root = ET.parse(path).getroot()
    result: dict[str, tuple[str, int, int, int, int]] = {}
    for node in root.findall("Ui2DAnimation"):
        name = node.get("item", "")
        frame = node.find("Frames")
        if frame is None:
            continue
        try:
            result[name] = (
                (frame.findtext("Texture") or "").strip(),
                int(frame.findtext("Location/X") or 0),
                int(frame.findtext("Location/Y") or 0),
                int(frame.findtext("Size/CX") or 0),
                int(frame.findtext("Size/CY") or 0),
            )
        except ValueError as exc:
            raise ValueError(f"invalid animation geometry for {name}") from exc
    return result


FRAMES = animation_frames()
CONTROL_PREFIXES = (
    "A_CheckBox", "A_Slider", "A_RecessedBox_",
    "A_SpellGemHolder_small", "A_SpellGemBackground_small",
)
CONTROL_NAMES = tuple(
    name for name, (texture, *_geometry) in FRAMES.items()
    if texture.casefold() == "window_pieces11a.dds"
    and name.startswith(CONTROL_PREFIXES)
)


def retarget_control_animations(text: str) -> str:
    texture_info = (
        f"\n\t<TextureInfo item=\"{CONTROL_ATLAS}\">\n"
        "\t\t<Size>\n\t\t\t<CX>256</CX>\n\t\t\t<CY>256</CY>\n"
        "\t\t</Size>\n\t</TextureInfo>\n"
    )
    schema = re.search(r"<Schema\b[^>]*/>", text)
    if schema is None:
        raise ValueError("EQUI_Animations.xml has no Schema")
    schema_end = schema.end()
    text = text[:schema_end] + texture_info + text[schema_end:]

    for name in CONTROL_NAMES:
        pattern = re.compile(
            rf"(<Ui2DAnimation item=\"{re.escape(name)}\">.*?"
            r"</Ui2DAnimation>)",
            re.DOTALL,
        )
        match = pattern.search(text)
        if match is None:
            raise ValueError(f"missing control animation {name}")
        block = match.group(1)
        old = "<Texture>window_pieces11a.dds</Texture>"
        if old not in block:
            raise ValueError(f"{name} no longer uses window_pieces11a.dds")
        block = block.replace(old, f"<Texture>{CONTROL_ATLAS}</Texture>", 1)
        text = text[:match.start(1)] + block + text[match.end(1):]
    return text


def transform_xml(name: str, payload: bytes) -> bytes:
    text = comparable_payload(name, payload).decode("ascii")
    text = remap_visual_colors(text)
    if name.casefold() == "equi_animations.xml":
        text = retarget_control_animations(text)
    elif name.casefold() == "equi_spellbookwnd.xml":
        text = add_spellbook_text_colors(text)
    elif name.casefold() == "equi.xml":
        marker = "\t<Schema xmlns=\"EverQuestData\" xmlns:dt=\"EverQuestDataTypes\" />"
        text = text.replace(
            marker,
            marker + "\n\t<!-- SpinUI Glass: binding-safe optional visual skin. -->",
            1,
        )
    return text.encode("ascii")


def rect_for(name: str) -> tuple[int, int, int, int]:
    _texture, x, y, width, height = FRAMES[name]
    return x, y, x + width, y + height


def state_for(name: str) -> str:
    lowered = name.casefold()
    if "disabled" in lowered:
        return "disabled"
    if "flyby" in lowered:
        return "flyby"
    if "pressed" in lowered or "selected" in lowered:
        return "pressed"
    return "normal"


def paint_control_atlas() -> Image.Image:
    source = Image.open(SOURCE / "window_pieces11a.dds").convert("RGBA")
    atlas = Image.new("RGBA", source.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(atlas)
    for name in CONTROL_NAMES:
        x0, y0, x1, y1 = rect_for(name)
        state = state_for(name)
        width, height = x1 - x0, y1 - y0
        disabled = state == "disabled"
        edge = FROST_DIM if disabled else (MINT if state == "flyby" else ICE)

        if name.startswith("A_CheckBox"):
            side = max(8, min(width, height) - 2)
            left = x0 + (width - side) // 2
            top = y0 + (height - side) // 2
            draw.rounded_rectangle(
                [left, top, left + side - 1, top + side - 1], radius=2,
                fill=(4, 15, 24, 218), outline=edge + (220,),
            )
            if "Pressed" in name:
                mark = FROST_DIM if disabled else MINT_BRIGHT
                draw.line(
                    [(left + 3, top + side // 2),
                     (left + side // 2 - 1, top + side - 4),
                     (left + side - 3, top + 3)],
                    fill=mark + (255,), width=2, joint="curve",
                )
        elif name.startswith("A_SliderBackground") or "SliderEndCap" in name:
            cy = (y0 + y1) // 2
            draw.line([(x0, cy), (x1 - 1, cy)], fill=(2, 8, 13, 255), width=4)
            draw.line([(x0, cy), (x1 - 1, cy)], fill=ICE + (125,), width=1)
        elif "SliderThumb" in name:
            fill_color = (8, 22, 34, 235) if not disabled else (7, 13, 18, 210)
            draw.rounded_rectangle(
                [x0, y0, x1 - 1, y1 - 1], radius=max(2, min(width, height) // 4),
                fill=fill_color, outline=edge + (240,),
            )
            if not disabled and width > 6:
                draw.line([(x0 + 3, y0 + 2), (x1 - 4, y0 + 2)],
                          fill=ICE_BRIGHT + (110,))
        elif name.startswith("A_RecessedBox_"):
            draw.rounded_rectangle(
                [x0, y0, x1 - 1, y1 - 1], radius=3,
                fill=(2, 8, 14, 232), outline=ICE + (115,),
            )
            draw.line([(x0 + 3, y1 - 2), (x1 - 4, y1 - 2)],
                      fill=VIOLET + (95,))
        else:  # small spell-gem holder/background
            draw.rounded_rectangle(
                [x0, y0, x1 - 1, y1 - 1], radius=3,
                fill=(3, 11, 18, 210), outline=ICE + (145,),
            )
    return atlas


def paint_radios() -> Image.Image:
    image = Image.open(SOURCE / "window_pieces09.tga").convert("RGBA")
    draw = ImageDraw.Draw(image)
    for name in FRAMES:
        if not name.startswith("A_RadioBtn"):
            continue
        x0, y0, x1, y1 = rect_for(name)
        draw.rectangle([x0, y0, x1 - 1, y1 - 1], fill=(0, 0, 0, 0))
        disabled = "Disabled" in name
        edge = FROST_DIM if disabled else ICE
        pad = max(2, min(x1 - x0, y1 - y0) // 5)
        draw.ellipse([x0 + pad, y0 + pad, x1 - 1 - pad, y1 - 1 - pad],
                     fill=(3, 12, 20, 220), outline=edge + (220,), width=1)
        if "Pressed" in name:
            inner = pad + max(2, min(x1 - x0, y1 - y0) // 5)
            color = FROST_DIM if disabled else MINT_BRIGHT
            draw.ellipse([x0 + inner, y0 + inner,
                          x1 - 1 - inner, y1 - 1 - inner], fill=color + (255,))
    return image


def paint_chat_chrome() -> Image.Image:
    image = Image.open(SOURCE / "window_pieces05.tga").convert("RGBA")
    pixels = image.load()
    names = [name for name in FRAMES if name.startswith(
        ("A_Chat", "A_FiligreeFrame", "A_FiligreeThinFrame",
         "A_Filigree2", "A_Filigree3"))]
    for name in names:
        x0, y0, x1, y1 = rect_for(name)
        for y in range(y0, y1):
            for x in range(x0, x1):
                r, g, b, alpha = pixels[x, y]
                if alpha == 0:
                    continue
                light = (r * 299 + g * 587 + b * 114) // 1000
                if light < 28:
                    color = (2, 8, 13)
                elif light < 72:
                    color = (8, 24, 35)
                elif light < 140:
                    color = (48, 121, 143)
                else:
                    color = ICE_BRIGHT
                pixels[x, y] = color + (alpha,)
    draw = ImageDraw.Draw(image)
    for name in ("A_ChatWindowTitleLeft", "A_ChatWindowTitleMiddle",
                 "A_ChatWindowTitleRight"):
        x0, y0, x1, y1 = rect_for(name)
        for y in range(y0, y1):
            amount = (y - y0) / max(1, y1 - y0 - 1)
            color = tuple(round((12, 38, 50)[i] * (1 - amount)
                                + (4, 12, 20)[i] * amount) for i in range(3))
            draw.line([(x0, y), (x1 - 1, y)], fill=color + (232,))
        draw.line([(x0, y0), (x1 - 1, y0)], fill=MINT + (230,))
        draw.line([(x0, y1 - 1), (x1 - 1, y1 - 1)], fill=ICE + (190,))
    return image


def glassify_spellbook(source: Image.Image, index: int) -> Image.Image:
    source = source.convert("RGBA")
    output = Image.new("RGBA", source.size, (0, 0, 0, 0))
    src, dst = source.load(), output.load()
    for y in range(source.height):
        for x in range(source.width):
            r, g, b, alpha = src[x, y]
            if alpha == 0:
                continue
            light = (r * 299 + g * 587 + b * 114) / 255000
            warm = max(0, r - b) / 255
            if light > 0.62 and warm < 0.35:
                base = tuple(round(18 + light * value) for value in (20, 38, 46))
            elif warm > 0.12:
                base = tuple(round((14, 17, 34)[i] * (1 - light)
                                   + VIOLET[i] * light * 0.62) for i in range(3))
            else:
                base = tuple(round((3, 10, 17)[i] * (1 - light)
                                   + ICE[i] * light * 0.72) for i in range(3))
            dst[x, y] = tuple(min(255, channel) for channel in base) + (min(alpha, 240),)

    # Replace the parchment writing field with a calm, purpose-built glass
    # pane.  The old ornament survives only on the physical binding/corners.
    if index == 1:
        field = lambda x, y: x >= 25
    elif index == 2:
        field = lambda x, y: x <= 230
    elif index == 3:
        field = lambda x, y: x >= 24 and y <= 104
    elif index == 4:
        field = lambda x, y: x <= 231 and y <= 104
    else:
        field = lambda x, y: False
    for y in range(source.height):
        for x in range(source.width):
            alpha = src[x, y][3]
            if not alpha or not field(x, y):
                continue
            vertical = y / max(1, source.height - 1)
            diagonal = max(0.0, 1.0 - abs((x / source.width) - vertical) * 3.5)
            dst[x, y] = (
                round(5 + 4 * vertical + 2 * diagonal),
                round(14 + 9 * vertical + 4 * diagonal),
                round(22 + 13 * vertical + 6 * diagonal),
                min(alpha, 238),
            )

    def composite_masked(overlay: Image.Image) -> None:
        alpha = ImageChops.multiply(overlay.getchannel("A"), source.getchannel("A"))
        overlay.putalpha(alpha)
        output.alpha_composite(overlay)

    if index in (1, 2):
        # Barely visible etched grid and corner circuits: detail appears only
        # when the book is open and never competes with spell names.
        overlay = Image.new("RGBA", source.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        for offset in range(32, 256, 32):
            draw.line([(offset, 18), (offset, 238)], fill=ICE + (7,))
            draw.line([(18, offset), (238, offset)], fill=ICE + (6,))
        if index == 1:
            draw.line([(44, 18), (214, 188)], fill=ICE_BRIGHT + (10,), width=2)
            draw.line([(255, 12), (255, 244)], fill=VIOLET + (145,))
            draw.line([(206, 18), (238, 18), (238, 50)], fill=MINT + (58,))
            draw.ellipse([232, 44, 240, 52], outline=ICE_BRIGHT + (95,))
        else:
            draw.line([(42, 188), (212, 18)], fill=ICE_BRIGHT + (10,), width=2)
            draw.line([(0, 12), (0, 244)], fill=VIOLET + (145,))
            draw.line([(18, 206), (18, 238), (50, 238)], fill=MINT + (58,))
            draw.ellipse([44, 232, 52, 240], outline=ICE_BRIGHT + (95,))
        composite_masked(overlay)
    elif index in (3, 4):
        overlay = Image.new("RGBA", source.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        edge_y = 101
        draw.line([(18, edge_y), (238, edge_y)], fill=ICE + (115,))
        draw.line([(30, edge_y - 2), (226, edge_y - 2)], fill=MINT + (28,))
        composite_masked(overlay)
    else:
        # The fifth texture contains only the close glyph at this crop.
        draw = ImageDraw.Draw(output)
        draw.rectangle([54, 35, 65, 46], fill=(2, 8, 14, 230), outline=ICE + (220,))
        draw.line([(57, 38), (62, 43)], fill=MINT_BRIGHT + (255,), width=1)
        draw.line([(62, 38), (57, 43)], fill=MINT_BRIGHT + (255,), width=1)
    return output


def render_overrides(target: Path) -> set[str]:
    target.mkdir(parents=True, exist_ok=True)
    names = set(chrome.generate(
        source_skin=SOURCE,
        output_skin=target,
        palette=texture_tokens(),
        preview_dir=None,
        quiet=True,
    ))

    extras = {
        "window_pieces05.tga": paint_chat_chrome(),
        "window_pieces09.tga": paint_radios(),
        CONTROL_ATLAS: paint_control_atlas(),
    }
    for index in range(1, 6):
        name = f"default_spellbook0{index}.tga"
        extras[name] = glassify_spellbook(Image.open(SOURCE / name), index)
    for name, image in extras.items():
        chrome.save_tga(image, target / name)
        names.add(name)
    return names


def expected_payloads(override_dir: Path) -> dict[str, bytes]:
    overrides = render_overrides(override_dir)
    expected: dict[str, bytes] = {}
    for path in SOURCE.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(SOURCE).as_posix()
        if relative in overrides:
            expected[relative] = (override_dir / relative).read_bytes()
        elif path.suffix.casefold() == ".xml":
            expected[relative] = transform_xml(path.name, path.read_bytes())
        else:
            expected[relative] = path.read_bytes()
    expected[CONTROL_ATLAS] = (override_dir / CONTROL_ATLAS).read_bytes()
    expected[THEME_README_NAME] = GLASS_README.encode("utf-8")
    return expected


def build(output: Path, *, check: bool = False) -> int:
    with tempfile.TemporaryDirectory(prefix="spinui-glass-") as raw_temp:
        expected = expected_payloads(Path(raw_temp))

    actual = {
        path.relative_to(output).as_posix(): path
        for path in output.rglob("*") if path.is_file()
    } if output.is_dir() else {}

    if check:
        problems: list[str] = []
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        problems.extend(f"missing {name}" for name in missing[:20])
        problems.extend(f"unexpected {name}" for name in extra[:20])
        for name in sorted(set(expected) & set(actual)):
            payload = actual[name].read_bytes()
            if sha256(comparable_payload(name, payload)) != sha256(
                comparable_payload(name, expected[name])
            ):
                problems.append(f"stale {name}")
                if len(problems) >= 40:
                    break
        if problems:
            print("SpinUI Glass build check: FAIL", file=sys.stderr)
            print("\n".join(f"  {problem}" for problem in problems), file=sys.stderr)
            return 1
        print(f"SpinUI Glass build check: PASS | {len(expected)} files")
        return 0

    output.mkdir(parents=True, exist_ok=True)
    written = 0
    for name, payload in expected.items():
        destination = output / name
        if destination.is_file() and sha256(
            comparable_payload(name, destination.read_bytes())
        ) == sha256(comparable_payload(name, payload)):
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        written += 1
    stale = sorted(set(actual) - set(expected))
    if stale:
        raise RuntimeError(
            "generated Glass skin contains stale files; remove them explicitly: "
            + ", ".join(stale[:20])
        )
    print(f"SpinUI Glass build: PASS | {len(expected)} files | {written} updated")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    return build(args.output.resolve(), check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
