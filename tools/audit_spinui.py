#!/usr/bin/env python3
"""Release-grade structural audit for SpinUI assets and critical geometry.

This intentionally uses only the Python standard library so the same checks
run locally and in the Windows packaging workflow before a release is built.
"""

from __future__ import annotations

import configparser
import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
SKIN = REPO / "spinui_reloaded"

# These are supplied by EverQuest's default UI and are valid fallback assets.
CLIENT_PROVIDED = {
    "sidl.xml",
    "eq_expansion_logos.tga",
    "eq_expansion_logos2.tga",
    "window_fg_cart.tga",
    "equi_dynamiczonewnd.xml",
    "equi_factionwnd.xml",
    "equi_personalinstancewnd.xml",
}

# These controls hold server-populated lists and instance actions.  Keeping
# local copies makes /loadskin rebuild a snapshot of their hierarchy while the
# client still owns live data bindings.  Always resolve the running client's
# definitions instead so hot swaps cannot strand stale controls.
STATEFUL_CLIENT_FALLBACKS = {
    "equi_dynamiczonewnd.xml",
    "equi_factionwnd.xml",
    "equi_personalinstancewnd.xml",
}


def fail(message: str) -> None:
    raise AssertionError(message)


def item(root: ET.Element, tag: str, name: str) -> ET.Element:
    node = root.find(f".//{tag}[@item='{name}']")
    if node is None:
        fail(f"missing {tag} item {name}")
    return node


def child_int(node: ET.Element, path: str) -> int:
    value = node.findtext(path)
    if value is None:
        fail(f"missing {path} in {node.tag} {node.get('item', '')}")
    return int(value)


def vertical_tile_columns(root: ET.Element, box: ET.Element) -> list[list[str]]:
    """Resolve a vertical-first TileLayoutBox into the columns EQ will draw."""
    if (box.findtext("HorizontalFirst") or "").strip().casefold() != "false":
        fail(f"{box.get('item', '')} must remain vertical-first")
    height = child_int(box, "Size/CY")
    spacing = child_int(box, "Spacing")
    columns: list[list[str]] = [[]]
    used = 0
    for piece in box.findall("Pieces"):
        reference = (piece.text or "").strip()
        if not reference:
            continue
        tag, separator, name = reference.partition(":")
        if not separator:
            tag, name = "Screen", reference
        row = item(root, tag, name)
        row_height = child_int(row, "Size/CY")
        needed = row_height if not columns[-1] else spacing + row_height
        if columns[-1] and used + needed > height:
            columns.append([])
            used = 0
            needed = row_height
        if row_height > height:
            fail(f"{box.get('item', '')} row {name} exceeds its column")
        columns[-1].append(name)
        used += needed
    return columns


def audit_window_draw_templates(
        roots: dict[Path, ET.Element]) -> tuple[int, int]:
    """Mirror EQ's global WindowDrawTemplate symbol-table validation.

    A well-formed XML document can still make the client reject the entire
    skin when a Screen's DrawTemplate names a WindowDrawTemplate that was
    never declared.  Keep this separate from asset validation so the failure
    identifies the missing symbol and every source file that references it.
    """

    declared: set[str] = set()
    duplicate_declarations: set[str] = set()
    references: dict[str, set[str]] = {}

    for path, root in roots.items():
        for node in root.iter("WindowDrawTemplate"):
            name = (node.get("item") or "").strip()
            if not name:
                continue
            if name in declared:
                duplicate_declarations.add(name)
            declared.add(name)
        for node in root.iter("DrawTemplate"):
            name = (node.text or "").strip()
            if name.startswith("WDT_"):
                references.setdefault(name, set()).add(path.name)

    if duplicate_declarations:
        fail(
            "duplicate WindowDrawTemplate declarations: "
            f"{sorted(duplicate_declarations)}"
        )

    undefined = sorted(set(references) - declared)
    if undefined:
        details = []
        for name in undefined:
            files = sorted(references[name], key=str.casefold)
            display = ", ".join(files[:5])
            if len(files) > 5:
                display += f", +{len(files) - 5} more"
            details.append(f"{name} ({display})")
        fail("undeclared WindowDrawTemplate references: " + "; ".join(details))

    return len(declared), sum(len(paths) for paths in references.values())


def audit_xml() -> tuple[list[Path], set[str], int, int]:
    xml_files = sorted(SKIN.glob("*.xml"))
    if not xml_files:
        fail("skin contains no XML files")
    roots: dict[Path, ET.Element] = {}
    for path in xml_files:
        try:
            roots[path] = ET.parse(path).getroot()
        except ET.ParseError as exc:
            fail(f"invalid XML {path.name}: {exc}")

    available = {p.name.casefold() for p in SKIN.iterdir() if p.is_file()}
    equi = roots[SKIN / "EQUI.xml"]
    includes = {n.text.strip() for n in equi.iter("Include") if n.text and n.text.strip()}
    include_names = {
        Path(ref.replace("\\", "/")).name.casefold() for ref in includes
    }
    for fallback in sorted(STATEFUL_CLIENT_FALLBACKS):
        if fallback in available:
            fail(f"stateful client window must not be overridden: {fallback}")
        if fallback not in include_names:
            fail(f"stateful client fallback missing from EQUI.xml: {fallback}")
    missing_includes = {
        ref for ref in includes
        if Path(ref.replace("\\", "/")).name.casefold() not in available
        and Path(ref.replace("\\", "/")).name.casefold() not in CLIENT_PROVIDED
    }
    if missing_includes:
        fail(f"missing EQUI.xml includes: {sorted(missing_includes)}")

    texture_refs: set[str] = set()
    for root in roots.values():
        for node in root.iter("Texture"):
            if node.text and node.text.strip().lower().endswith((".tga", ".dds")):
                texture_refs.add(node.text.strip())
    missing_textures = {
        ref for ref in texture_refs
        if Path(ref.replace("\\", "/")).name.casefold() not in available
        and Path(ref.replace("\\", "/")).name.casefold() not in CLIENT_PROVIDED
    }
    if missing_textures:
        fail(f"missing referenced textures: {sorted(missing_textures)}")
    template_count, template_reference_files = audit_window_draw_templates(roots)
    return xml_files, texture_refs, template_count, template_reference_files


def audit_binary_assets() -> tuple[int, int, int]:
    tga_files = sorted(SKIN.glob("*.tga"))
    dds_files = sorted(SKIN.glob("*.dds"))
    cur_files = sorted(SKIN.glob("*.cur"))
    for path in tga_files:
        header = path.read_bytes()[:18]
        if len(header) != 18:
            fail(f"truncated TGA header: {path.name}")
        width, height = struct.unpack_from("<HH", header, 12)
        depth = header[16]
        if width <= 0 or height <= 0 or depth not in (8, 16, 24, 32):
            fail(f"invalid TGA geometry: {path.name} ({width}x{height}x{depth})")
    for path in dds_files:
        header = path.read_bytes()[:128]
        if len(header) != 128 or header[:4] != b"DDS ":
            fail(f"invalid DDS header: {path.name}")
        height, width = struct.unpack_from("<II", header, 12)
        if width <= 0 or height <= 0:
            fail(f"invalid DDS geometry: {path.name} ({width}x{height})")
    for path in cur_files:
        header = path.read_bytes()[:6]
        if len(header) != 6:
            fail(f"truncated CUR header: {path.name}")
        reserved, kind, count = struct.unpack("<HHH", header)
        if reserved != 0 or kind != 2 or count <= 0:
            fail(f"invalid CUR directory: {path.name}")
    return len(tga_files), len(dds_files), len(cur_files)


def _anchor_flag(node: ET.Element, tag: str, default: bool) -> bool:
    value = node.findtext(tag)
    if value is None:
        return default
    return value.strip().casefold() == "true"


def _stretched_rect(
        node: ET.Element, parent_width: int, parent_height: int,
        *, label: str) -> tuple[int, int, int, int]:
    """Resolve the anchored rectangles used by the polished pet layout."""

    left_offset = child_int(node, "LeftAnchorOffset")
    right_offset = child_int(node, "RightAnchorOffset")
    top_offset = child_int(node, "TopAnchorOffset")
    bottom_offset = child_int(node, "BottomAnchorOffset")
    left = left_offset if _anchor_flag(node, "LeftAnchorToLeft", True) else parent_width - left_offset
    right = right_offset if _anchor_flag(node, "RightAnchorToLeft", False) else parent_width - right_offset
    top = top_offset if _anchor_flag(node, "TopAnchorToTop", True) else parent_height - top_offset
    bottom = bottom_offset if _anchor_flag(node, "BottomAnchorToTop", True) else parent_height - bottom_offset
    if not (0 <= left < right <= parent_width and 0 <= top < bottom <= parent_height):
        fail(
            f"{label} leaves its parent: "
            f"({left}, {top})-({right}, {bottom}) in {parent_width}x{parent_height}"
        )
    return left, top, right, bottom


def _rects_overlap(
        first: tuple[int, int, int, int],
        second: tuple[int, int, int, int]) -> bool:
    return not (
        first[2] <= second[0] or second[2] <= first[0]
        or first[3] <= second[1] or second[3] <= first[1]
    )


def audit_pet_geometry() -> None:
    """Guard the readable 1440p pet hierarchy and every EQ layout variant."""

    from restyle_pet import (BUFF_BORDER_INSET, BUFF_CAPACITY, BUFF_CELLS,
                             BUFF_DECALS, BUFF_PLATE_BLEED, BUFF_RECTS,
                             BUFF_TIMER_FONT, BUTTON_SIZE, COMMAND_ITEMS,
                             COMMAND_POSITIONS, PET_PANEL_SIZE,
                             SUBWINDOW_RECTS, TIMER_COLOR, WINDOW_SIZES)

    variants = WINDOW_SIZES
    command_items = list(COMMAND_ITEMS)
    menu_names = {
        "EQUI_PetInfoWindow.xml": "Fixed Size - Buffs on Bottom",
        "EQUI_PetInfoWindow1.xml": "Resizable - Buffs on Bottom",
        "EQUI_PetInfoWindow2.xml": "Resizable - Buffs on Top",
        "EQUI_PetInfoWindow3.xml": "Resizable - Buffs on Right",
    }

    for filename, expected_size in variants.items():
        path = SKIN / filename
        try:
            root = ET.parse(path).getroot()
        except (OSError, ET.ParseError) as exc:
            fail(f"invalid pet layout variant {filename}: {exc}")

        window = item(root, "Screen", "PetInfoWindow")
        actual_size = (child_int(window, "Size/CX"), child_int(window, "Size/CY"))
        if actual_size != expected_size:
            fail(f"{filename} pet window must remain {expected_size[0]}x{expected_size[1]}")
        if window.findtext("MenuName") != menu_names[filename]:
            fail(f"{filename} advertises the wrong Pet layout menu name")
        expected_sizable = filename != "EQUI_PetInfoWindow.xml"
        if window.findtext("Style_Sizable") != str(expected_sizable).lower():
            fail(f"{filename} has the wrong fixed/resizable behavior")
        if filename in ("EQUI_PetInfoWindow1.xml", "EQUI_PetInfoWindow2.xml"):
            bounds = tuple(child_int(window, field) for field in (
                "MinHSize", "MinVSize", "MaxHSize", "MaxVSize"))
            compact = WINDOW_SIZES[filename]
            if bounds != (compact[0], compact[1], compact[0], 480):
                fail(
                    f"{filename} must resize vertically from "
                    f"{compact[0]}x{compact[1]}: {bounds}"
                )
        elif filename == "EQUI_PetInfoWindow3.xml":
            bounds = tuple(child_int(window, field) for field in (
                "MinHSize", "MinVSize", "MaxHSize", "MaxVSize"))
            if bounds != (441, 181, 700, 480):
                fail(f"{filename} lost its right-rail resize bounds: {bounds}")

        for animation_name in ("PetBlueIconBackground", "PetRedIconBackground"):
            animations = root.findall(f".//Ui2DAnimation[@item='{animation_name}']")
            if len(animations) != 1:
                fail(f"{filename} must define {animation_name} exactly once")

        definitions = [
            node.get("item", "") for node in root.findall(".//Button")
            if node.get("item", "").startswith("PIW_Pet")
            and node.get("item", "").endswith("_Button")
        ]
        if definitions != command_items or len(set(definitions)) != 14:
            fail(f"{filename} pet command definitions changed: {definitions}")

        for index, button_name in enumerate(command_items):
            button = item(root, "Button", button_name)
            if button.findtext("ScreenID") != f"Pet{index}_Button":
                fail(f"{filename} {button_name} lost its EQ command binding")
            if (button.findtext("Style_Checkbox"), button.findtext("Text")) != (
                    "true", "none"):
                fail(f"{filename} {button_name} lost its client-owned command semantics")
            if (child_int(button, "Size/CX"), child_int(button, "Size/CY")) != BUTTON_SIZE:
                fail(f"{filename} {button_name} must remain {BUTTON_SIZE[0]}x{BUTTON_SIZE[1]}")
            actual_location = (
                child_int(button, "Location/X"), child_int(button, "Location/Y"))
            if actual_location != COMMAND_POSITIONS[index]:
                fail(
                    f"{filename} {button_name} lost its deterministic placement: "
                    f"{actual_location}"
                )

        if root.find(".//TileLayoutBox[@item='PIW_PetButtons']") is not None:
            fail(f"{filename} must not reintroduce the client-wrapping command grid")

        window_width, window_height = actual_size
        subwindow = item(root, "Screen", "PetInfoSubWindow")
        sub_left, sub_top, sub_right, sub_bottom = _stretched_rect(
            subwindow, window_width, window_height,
            label=f"{filename} pet info panel",
        )
        sub_width = sub_right - sub_left
        sub_height = sub_bottom - sub_top
        if (sub_left, sub_top, sub_right, sub_bottom) != SUBWINDOW_RECTS[filename]:
            fail(
                f"{filename} command panel left its compact "
                f"{PET_PANEL_SIZE[0]}x{PET_PANEL_SIZE[1]} frame"
            )
        if (sub_width, sub_height) != PET_PANEL_SIZE:
            fail(f"{filename} command panel must remain {PET_PANEL_SIZE}")
        # A panel nested at the outer frame's own corner must not paint a
        # second frame or reserve window controls there: that is what made the
        # top-left corner read as a patch that did not fit.
        if (subwindow.findtext("Style_Border") != "false"
                or subwindow.findtext("Style_Transparent") != "true"):
            fail(f"{filename} command panel reintroduced a duplicate frame")
        for control in ("Style_Qmarkbox", "Style_Closebox", "Style_Minimizebox",
                        "Style_Titlebar"):
            if subwindow.findtext(control) != "false":
                fail(f"{filename} command panel reserved a stray {control}")
        subwindow_pieces = [
            node.text for node in subwindow.findall("Pieces") if node.text]
        direct_commands = [
            name for name in subwindow_pieces if name in command_items]
        if direct_commands != command_items:
            fail(
                f"{filename} must mount every native pet command directly once: "
                f"{direct_commands}"
            )

        button_rects: list[tuple[int, int, int, int]] = []
        for index, button_name in enumerate(command_items):
            left, top = COMMAND_POSITIONS[index]
            rect = (
                left, top, left + BUTTON_SIZE[0], top + BUTTON_SIZE[1])
            if rect[0] < 0 or rect[1] < 0 or rect[2] > sub_width or rect[3] > sub_height:
                fail(f"{filename} {button_name} clips outside the pet info panel")
            if any(_rects_overlap(rect, prior) for prior in button_rects):
                fail(f"{filename} {button_name} overlaps another pet command")
            button_rects.append(rect)

        columns = sorted({left for left, _ in COMMAND_POSITIONS.values()})
        if columns != [10, 94, 178, 262]:
            fail(f"{filename} pet commands lost their four-column rhythm")
        if sub_width - max(rect[2] for rect in button_rects) < 14:
            fail(f"{filename} pet commands lost their Legends-frame safety inset")

        buff_host = item(root, "Screen", "PIW_BuffWindow")
        if len(root.findall(".//Screen[@item='PIW_BuffWindow']")) != 1:
            fail(f"{filename} must define exactly one native pet buff host")
        # The rail's own frame is the divider between commands and effects now
        # that the command panel paints none; it also has to be paid for in
        # rail size, because the client subtracts it before flowing the tiles.
        if (
            buff_host.findtext("Style_Transparent") != "false"
            or buff_host.findtext("Style_Border") != "true"
        ):
            fail(f"{filename} pet buff rail must stay a framed recessed well")
        if buff_host.findtext("ScreenID") != "PetBuffWindow":
            fail(f"{filename} pet buff host lost its Legends ScreenID")
        if [node.text for node in buff_host.findall("Pieces")] != [
                "TileLayoutBox:PIW_BuffButtons"]:
            fail(f"{filename} pet buff host must mount its tile box exactly once")
        buff_rect = _stretched_rect(
            buff_host, window_width, window_height,
            label=f"{filename} pet buff host")
        if buff_rect != BUFF_RECTS[filename]:
            fail(f"{filename} pet buff host left its compact frame: {buff_rect}")
        if window.findtext("ClickThroughEmptyBuffs") != "true":
            fail(f"{filename} empty buff pixels must remain click-through")

        buff_tile = item(root, "TileLayoutBox", "PIW_BuffButtons")
        buff_template = item(root, "Button", "PIW_PetBuff_Template")
        if len(root.findall(".//TileLayoutBox[@item='PIW_BuffButtons']")) != 1:
            fail(f"{filename} must define exactly one native pet buff tile box")
        if len(root.findall(".//Button[@item='PIW_PetBuff_Template']")) != 1:
            fail(f"{filename} must define exactly one native pet buff template")
        if buff_tile.findtext("ScreenID") != "PetBuffButtons":
            fail(f"{filename} pet buff tile box lost its Legends ScreenID")
        decal_size, decal_offset = BUFF_DECALS[filename]
        if (
            buff_template.findtext("ScreenID"),
            buff_template.findtext("Style_Checkbox"),
            buff_template.findtext("Template"),
            child_int(buff_template, "DecalSize/CX"),
            child_int(buff_template, "DecalSize/CY"),
        ) != ("Pet_Buff_Template", "false", "BDT_PetRedBuff", *decal_size):
            fail(f"{filename} pet buff template lost its Legends binding or decal")
        if (child_int(buff_template, "DecalOffset/X"),
                child_int(buff_template, "DecalOffset/Y")) != decal_offset:
            fail(f"{filename} pet effect icon left its cell corner")
        template_size = (
            child_int(buff_template, "Size/CX"),
            child_int(buff_template, "Size/CY"),
        )
        if template_size != BUFF_CELLS[filename]:
            fail(
                f"{filename} pet buff template must remain "
                f"{BUFF_CELLS[filename][0]}x{BUFF_CELLS[filename][1]}"
            )
        # The cell may not outgrow its icon: the client stretches a solid
        # blue/red beneficial-vs-detrimental tile across the whole cell, so a
        # wider cell turns that tile into a slab of flat colour per effect.
        if (template_size[0] - (decal_offset[0] + decal_size[0])
                > BUFF_PLATE_BLEED):
            fail(f"{filename} pet effect cell outgrew its icon; the "
                 f"beneficial/detrimental tile becomes a slab")
        if child_int(buff_template, "Font") < BUFF_TIMER_FONT:
            fail(f"{filename} pet countdown fell below the accessible font tier")
        if buff_template.findtext("FontShadow") != "true":
            fail(f"{filename} pet countdown lost its shadow")
        if tuple(child_int(buff_template, f"TextColor/{channel}")
                 for channel in ("R", "G", "B")) != TIMER_COLOR:
            fail(f"{filename} pet countdown lost its ember-gold tint")
        expected_anchor_top = filename != "EQUI_PetInfoWindow2.xml"
        if (buff_tile.findtext("FirstPieceTemplate") != "true"
                or buff_tile.findtext("AnchorToTop")
                != str(expected_anchor_top).lower()
                or [node.text for node in buff_tile.findall("Pieces")]
                != ["PIW_PetBuff_Template"]):
            fail(f"{filename} pet buff flow lost its Legends template binding")
        # Horizontal trays read left-to-right; only the right-hand side rail
        # flows down its own column first.
        expected_horizontal = filename != "EQUI_PetInfoWindow3.xml"
        if buff_tile.findtext("HorizontalFirst") != str(expected_horizontal).lower():
            fail(f"{filename} pet buff flow uses the wrong reading direction")
        buff_width = buff_rect[2] - buff_rect[0]
        buff_height = buff_rect[3] - buff_rect[1]
        flow_width = buff_width - BUFF_BORDER_INSET
        flow_height = buff_height - BUFF_BORDER_INSET
        columns = flow_width // template_size[0]
        rows = flow_height // template_size[1]
        capacity = columns * rows
        if capacity != BUFF_CAPACITY[filename]:
            fail(
                f"{filename} visible pet buff capacity changed: "
                f"{capacity}, expected {BUFF_CAPACITY[filename]}"
            )
        if not columns or not rows:
            fail(f"{filename} pet effect rail cannot flow a single cell")
        if (flow_width - columns * template_size[0] < 0
                or flow_height - rows * template_size[1] < 0):
            fail(f"{filename} pet effect rail does not pay for its own frame")

        seam_width = max(
            0, min(sub_right, buff_rect[2]) - max(sub_left, buff_rect[0]))
        seam_height = max(
            0, min(sub_bottom, buff_rect[3]) - max(sub_top, buff_rect[1]))
        if min(seam_width, seam_height) != 3:
            fail(f"{filename} pet frame seam must remain exactly 3px")
        for rect in button_rects:
            global_rect = (
                rect[0] + sub_left, rect[1] + sub_top,
                rect[2] + sub_left, rect[3] + sub_top,
            )
            if _rects_overlap(global_rect, buff_rect):
                fail(f"{filename} pet effect rail overlaps a command hit target")
        # The compact fixed default has to show a realistic pet's whole effect
        # set at once; the resizable right-click variants remain the home for
        # extreme counts.
        if filename == "EQUI_PetInfoWindow.xml" and capacity < 28:
            fail("fixed Pet default must show at least 28 effect positions")

        window_pieces = [node.text for node in window.findall("Pieces")]
        if window_pieces.count("Screen:PetInfoSubWindow") != 1:
            fail(f"{filename} must mount the pet command panel exactly once")
        if window_pieces.count("PIW_BuffWindow") != 1:
            fail(f"{filename} must mount the native pet buff host exactly once")
        if window_pieces.count("DragBox:PIWDragBox1") != 1:
            fail(f"{filename} must mount the pet drag region exactly once")

        expected_drag = ((
            0,
            window_height - PET_PANEL_SIZE[1],
            PET_PANEL_SIZE[0],
            window_height - (PET_PANEL_SIZE[1] - 18),
        )
                         if filename == "EQUI_PetInfoWindow2.xml"
                         else (0, 0, PET_PANEL_SIZE[0], 24))
        drag_rect = _stretched_rect(
            item(root, "DragBox", "PIWDragBox1"),
            window_width, window_height,
            label=f"{filename} drag region",
        )
        if drag_rect != expected_drag:
            fail(f"{filename} drag region left the command frame: {drag_rect}")

        if filename in ("EQUI_PetInfoWindow1.xml", "EQUI_PetInfoWindow2.xml"):
            if child_int(window, "MinVSize") != WINDOW_SIZES[filename][1]:
                fail(
                    f"{filename} must retain its "
                    f"{WINDOW_SIZES[filename][1]}px compact minimum height"
                )
            grown_sub = _stretched_rect(
                subwindow, window_width, window_height + 48,
                label=f"{filename} grown command panel",
            )
            grown_buff = _stretched_rect(
                buff_host, window_width, window_height + 48,
                label=f"{filename} grown buff host",
            )
            if (grown_sub[2] - grown_sub[0], grown_sub[3] - grown_sub[1]) != PET_PANEL_SIZE:
                fail(f"{filename} resizing must not stretch the command panel")
            if grown_buff[3] - grown_buff[1] != buff_height + 48:
                fail(f"{filename} added height must become buff capacity")
        elif filename == "EQUI_PetInfoWindow3.xml":
            if (child_int(window, "MinHSize"), child_int(window, "MinVSize")) != (441, 181):
                fail("right-buff Pet variant lost its compact 441x181 minimum")
            grown_sub = _stretched_rect(
                subwindow, window_width + 48, window_height + 48,
                label=f"{filename} grown command panel",
            )
            grown_buff = _stretched_rect(
                buff_host, window_width + 48, window_height + 48,
                label=f"{filename} grown buff host",
            )
            if (grown_sub[2] - grown_sub[0], grown_sub[3] - grown_sub[1]) != PET_PANEL_SIZE:
                fail("right-buff resizing must not stretch the command panel")
            if (grown_buff[2] - grown_buff[0], grown_buff[3] - grown_buff[1]) != (
                    buff_width + 48, buff_height + 48):
                fail("right-buff added size must become effect capacity")

        gauges = {
            name: _stretched_rect(
                item(root, "Gauge", name), sub_width, sub_height,
                label=f"{filename} {name}",
            )
            for name in (
                "PIW_PetHPGauge",
                "PIW_PetHPGauge_NameOnly",
                "PIW_PetTargetHPGauge",
                "PIW_PetTargetHPGauge_NameOnly",
            )
        }
        if _rects_overlap(gauges["PIW_PetHPGauge"], gauges["PIW_PetTargetHPGauge"]):
            fail(f"{filename} pet and target gauges overlap")

    profile_paths = (
        SKIN / "default720.ini",
        SKIN / "default1080.ini",
        SKIN / "default1440.ini",
        SKIN / "default4k.ini",
        REPO / "UI_Spin_qeynos_LO1.ini",
        REPO / "layouts" / "combat-focus" / "UI_Spin_qeynos_LO1.ini",
        REPO / "layouts" / "social-focus" / "UI_Spin_qeynos_LO1.ini",
        REPO / "layouts" / "hybrid" / "UI_Spin_qeynos_LO1.ini",
    )
    section_names = {
        "EQUI_PetInfoWindow.xml": "PetInfoWindow",
        "EQUI_PetInfoWindow1.xml": "PetInfoWindow_1",
        "EQUI_PetInfoWindow2.xml": "PetInfoWindow_2",
        "EQUI_PetInfoWindow3.xml": "PetInfoWindow_3",
    }
    for profile_path in profile_paths:
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.read_string(profile_path.read_text(encoding="utf-8-sig"))
        for filename, expected_size in WINDOW_SIZES.items():
            section = section_names[filename]
            if not parser.has_section(section):
                fail(f"{profile_path.name} is missing [{section}]")
            actual_size = (
                parser.getint(section, "Width"),
                parser.getint(section, "Height"),
            )
            if actual_size != expected_size:
                fail(
                    f"{profile_path.name} [{section}] is {actual_size}, "
                    f"expected {expected_size}"
                )


def audit_inventory_progression() -> None:
    expected_names = {
        "EQUI_InventoryWindow.xml",
        "EQUI_InventoryWindow1.xml",
        "EQUI_InventoryWindow2.xml",
        "EQUI_InventoryWindow3.xml",
    }
    paths = sorted(SKIN.glob("EQUI_InventoryWindow*.xml"))
    actual_names = {path.name for path in paths}
    if actual_names != expected_names:
        fail(
            "inventory variants changed: "
            f"found {sorted(actual_names)}, expected {sorted(expected_names)}"
        )

    for path in paths:
        root = ET.parse(path).getroot()
        illusion_label = item(root, "Label", "IWM_PetIllusionLabel")
        if (illusion_label.findtext("Text") or "").strip() != "Pet Illusion:":
            fail(f"{path.name} pet illusion label is missing or renamed")
        illusion_combo = item(root, "Combobox", "IWM_PetIllusionComboBox")
        if (illusion_combo.findtext("ScreenID") or "").strip() != "IWM_PetIllusionComboBox":
            fail(f"{path.name} lost the client-native pet illusion binding")
        if (illusion_combo.findtext("Choices") or "").strip() != "None":
            fail(f"{path.name} pet illusion selector lost its safe empty choice")
        pet_page = item(root, "Page", "IW_PetInvPage")
        pet_pieces = [(node.text or "").strip() for node in pet_page.findall("Pieces")]
        for required_piece in ("IWM_PetIllusionComboBox", "IWM_PetIllusionLabel"):
            if pet_pieces.count(required_piece) != 1:
                fail(
                    f"{path.name} pet page must include {required_piece} exactly once"
                )
        gauges: list[ET.Element] = []
        for gauge_name, eq_type in (
                ("IW_ExpGauge", 4), ("IW_AltAdvGauge", 5)):
            gauge = item(root, "Gauge", gauge_name)
            gauges.append(gauge)
            if child_int(gauge, "EQType") != eq_type:
                fail(f"{path.name} {gauge_name} lost EQType {eq_type}")
            draw_lines = (gauge.findtext("DrawLinesFill") or "").strip().casefold()
            if draw_lines != "false":
                fail(
                    f"{path.name} {gauge_name} must show total 0-100 "
                    "progression without the 20-percent sub-tick overlay"
                )
            lines = (gauge.findtext("GaugeDrawTemplate/Lines") or "").strip()
            if lines != "A_GaugeLines":
                fail(f"{path.name} {gauge_name} lost its fixed progression ticks")
        shared_paths = (
            "GaugeOffsetY",
            "Location/X",
            "Size/CX",
            "AutoStretch",
            "LeftAnchorOffset",
            "RightAnchorOffset",
            "RightAnchorToLeft",
            "GaugeDrawTemplate/Background",
            "GaugeDrawTemplate/Fill",
            "GaugeDrawTemplate/Lines",
        )
        for shared_path in shared_paths:
            values = tuple(
                (gauge.findtext(shared_path) or "").strip() for gauge in gauges
            )
            if values[0] != values[1]:
                fail(
                    f"{path.name} EXP and AA horizontal rendering differs "
                    f"at {shared_path}: {values}"
                )


def audit_aa_window() -> None:
    """Protect the AA command deck's bindings, true progress, and hierarchy."""
    root = ET.parse(SKIN / "EQUI_AAWindow.xml").getroot()
    for gauge_name, eq_type in (
            ("AAW_ExpGauge", 5), ("AAW_MercAAExpGauge", 150)):
        gauge = item(root, "Gauge", gauge_name)
        if child_int(gauge, "EQType") != eq_type:
            fail(f"AA window {gauge_name} lost EQType {eq_type}")
        if (gauge.findtext("DrawLinesFill") or "").strip().casefold() != "false":
            fail(
                f"AA window {gauge_name} must show total 0-100 progression "
                "without the 20-percent sub-tick overlay"
            )
        if (gauge.findtext("GaugeDrawTemplate/Lines") or "").strip() != "A_GaugeLines":
            fail(f"AA window {gauge_name} lost its fixed progression ticks")

    window = item(root, "Screen", "AAWindow")
    pieces = [(node.text or "").strip() for node in window.findall("Pieces")]
    for required in (
            "AAW_ProgressPanel", "AAW_ProgressPanelTitle",
            "AAW_FilterPanel", "AAW_FilterPanelTitle"):
        if pieces.count(required) != 1:
            fail(f"AA command deck must mount {required} exactly once")
    for panel_name, location, size in (
            ("AAW_ProgressPanel", (520, 2), (150, 174)),
            ("AAW_FilterPanel", (520, 180), (150, 196))):
        panel = item(root, "Screen", panel_name)
        actual_location = (
            child_int(panel, "Location/X"), child_int(panel, "Location/Y"))
        actual_size = (child_int(panel, "Size/CX"), child_int(panel, "Size/CY"))
        if actual_location != location or actual_size != size:
            fail(
                f"{panel_name} geometry changed: "
                f"{actual_location} {actual_size}"
            )

    list_specs = {
        "AAW_GeneralList": ((330, "Ability"), (62, "Rank"), (52, "Cost"), (58, "Reuse")),
        "AAW_ArchList": ((330, "Ability"), (62, "Rank"), (52, "Cost"), (58, "Reuse")),
        "AAW_ClassList": ((330, "Ability"), (62, "Rank"), (52, "Cost"), (58, "Reuse")),
        "AAW_SpecialList": ((330, "Ability"), (62, "Rank"), (52, "Cost"), (58, "Reuse")),
        "AAW_FocusList": ((330, "Ability"), (62, "Rank"), (52, "Cost"), (58, "Reuse")),
        "AAW_MercAAList": ((380, "Ability"), (66, "Rank"), (56, "Cost")),
    }
    for list_name, expected in list_specs.items():
        listbox = item(root, "Listbox", list_name)
        actual = tuple(
            (child_int(column, "Width"), (column.findtext("Heading") or "").strip())
            for column in listbox.findall("Columns")
        )
        if actual != expected:
            fail(f"AA window {list_name} columns changed: {actual}")

    expected_tabs = {
        "AAW_GeneralPage": "General",
        "AAW_ArchetypePage": "Archetype",
        "AAW_ClassPage": "Class",
        "AAW_SpecialPage": "Special",
        "AAW_FocusPage": "Focus",
        "AAW_MercAAPage": "Merc",
    }
    for page_name, tab_text in expected_tabs.items():
        page = item(root, "Page", page_name)
        if (page.findtext("TabText") or "").strip() != tab_text:
            fail(f"AA category {page_name} no longer maps to {tab_text}")


def audit_august_2026_runtime_contract() -> None:
    """Protect native controls introduced by the August 2026 client update.

    EQ resolves these controls by ScreenID at runtime.  A skin can therefore
    parse and render normally while still producing UIErrorLog failures when
    an item is missing, renamed, or no longer mounted by its expected parent.
    """

    def require_item(
            root: ET.Element, tag: str, name: str,
            *, screen_id: str | None = None) -> ET.Element:
        matches = root.findall(f".//{tag}[@item='{name}']")
        if len(matches) != 1:
            fail(f"{name} must define exactly one {tag}; found {len(matches)}")
        node = matches[0]
        expected_screen_id = name if screen_id is None else screen_id
        actual_screen_id = (node.findtext("ScreenID") or "").strip()
        if actual_screen_id != expected_screen_id:
            fail(
                f"{name} ScreenID changed: {actual_screen_id!r}, "
                f"expected {expected_screen_id!r}"
            )
        return node

    def require_piece(
            parent: ET.Element, reference: str, *, context: str) -> None:
        pieces = [(node.text or "").strip() for node in parent.findall("Pieces")]
        if pieces.count(reference) != 1:
            fail(f"{context} must mount {reference} exactly once")

    options = ET.parse(SKIN / "EQUI_OptionsWindow.xml").getroot()
    option_contract = (
        ("Button", "ODP_SystemCursor", "OIGP_LeftLayout"),
        ("Button", "ODP_SpellbookButtonToggle", "OIGP_LeftLayout"),
        ("Label", "ODP_CursorScaleLabel", "OIGP_RightLayout"),
        ("Combobox", "ODP_CursorScale", "OIGP_RightLayout"),
        ("Label", "ODP_NameplateThreatLabel", "OINP_RightLayout"),
        ("Combobox", "ODP_NameplateThreatOptions", "OINP_RightLayout"),
    )
    for tag, name, layout_name in option_contract:
        require_item(options, tag, name)
        layout = item(options, "VerticalLayoutBox", layout_name)
        require_piece(layout, name, context=f"Options {layout_name}")

    threat_choices = [
        (choice.text or "").strip()
        for choice in item(
            options, "Combobox", "ODP_NameplateThreatOptions"
        ).findall("Choices")
    ]
    expected_threat_choices = ["Off", "Aggro Mode", "No Aggro Mode"]
    if threat_choices != expected_threat_choices:
        fail(
            "ODP_NameplateThreatOptions choices changed: "
            f"{threat_choices}, expected {expected_threat_choices}"
        )

    spellbook_names = (
        "EQUI_SpellBookWnd.xml",
        "EQUI_SpellBookWnd1.xml",
        "EQUI_SpellBookWnd2.xml",
    )
    for filename in spellbook_names:
        root = ET.parse(SKIN / filename).getroot()
        require_item(
            root, "Button", "SBW_ActionsButton", screen_id="ActionsButton"
        )
        require_item(
            root, "Button", "SBW_ActionsToggle",
            screen_id="SBW_ActionsToggle"
        )
        window = item(root, "Screen", "SpellBookWnd")
        require_piece(
            window, "SBW_ActionsButton", context=f"{filename} SpellBookWnd"
        )
        require_piece(
            window, "SBW_ActionsToggle", context=f"{filename} SpellBookWnd"
        )

    task_overlay = ET.parse(SKIN / "EQUI_TaskOverlayWnd.xml").getroot()
    for tag, name in (
        ("Label", "TO_NoRespawnTitle"),
        ("Gauge", "TO_NoRespawnProgressGauge"),
        ("Label", "TO_NoRespawnRaresLabel"),
        ("Label", "TO_NoRespawnRaresValue"),
        ("Label", "TO_NoRespawnRaresValuePct"),
        ("Screen", "TO_NoRespawnProgressScreen"),
    ):
        require_item(task_overlay, tag, name)
    for name, eq_type in (
        ("TO_NoRespawnProgressGauge", 301),
        ("TO_NoRespawnRaresValue", 417),
        ("TO_NoRespawnRaresValuePct", 418),
    ):
        tag = "Gauge" if name == "TO_NoRespawnProgressGauge" else "Label"
        if child_int(item(task_overlay, tag, name), "EQType") != eq_type:
            fail(f"{name} lost EQType {eq_type}")
    no_respawn_gauge = item(
        task_overlay, "Gauge", "TO_NoRespawnProgressGauge"
    )
    if (
        no_respawn_gauge.findtext("GaugeDrawTemplate/Background") or ""
    ).strip() != "A_GaugeBackgroundDark":
        fail("TO_NoRespawnProgressGauge lost A_GaugeBackgroundDark")
    no_respawn_screen = item(
        task_overlay, "Screen", "TO_NoRespawnProgressScreen"
    )
    for name in (
        "TO_NoRespawnTitle",
        "TO_NoRespawnProgressGauge",
        "TO_NoRespawnRaresLabel",
        "TO_NoRespawnRaresValue",
        "TO_NoRespawnRaresValuePct",
    ):
        require_piece(
            no_respawn_screen, name, context="TO_NoRespawnProgressScreen"
        )
    require_piece(
        item(task_overlay, "TileLayoutBox", "TO_TaskList"),
        "Screen:TO_NoRespawnProgressScreen",
        context="TO_TaskList",
    )

    animations = ET.parse(SKIN / "EQUI_Animations.xml").getroot()
    dark_gauge_matches = animations.findall(
        ".//Ui2DAnimation[@item='A_GaugeBackgroundDark']"
    )
    if len(dark_gauge_matches) != 1:
        fail(
            "A_GaugeBackgroundDark must be defined exactly once; "
            f"found {len(dark_gauge_matches)}"
        )
    dark_gauge = dark_gauge_matches[0]
    if (dark_gauge.findtext("Cycle") or "").strip().casefold() != "true":
        fail("A_GaugeBackgroundDark must remain cyclic")
    dark_gauge_texture = (
        dark_gauge.findtext("Frames/Texture") or ""
    ).strip()
    if dark_gauge_texture != "window_fg_pieces.tga":
        fail("A_GaugeBackgroundDark lost its texture")
    if (
        child_int(dark_gauge, "Frames/Location/X"),
        child_int(dark_gauge, "Frames/Location/Y"),
    ) != (108, 12):
        fail("A_GaugeBackgroundDark source location changed")
    if (
        child_int(dark_gauge, "Frames/Size/CX"),
        child_int(dark_gauge, "Frames/Size/CY"),
    ) != (100, 10):
        fail("A_GaugeBackgroundDark source size changed")

    marketplace = ET.parse(SKIN / "EQUI_MarketplaceWnd.xml").getroot()
    require_item(
        marketplace, "Label", "MKPW_ConfirmPurchase_AlreadyOwnedLabel"
    )
    require_piece(
        item(marketplace, "Page", "MKPW_ConfirmPage"),
        "MKPW_ConfirmPurchase_AlreadyOwnedLabel",
        context="MKPW_ConfirmPage",
    )
    marketplace_window = item(marketplace, "Screen", "MarketplaceWnd")
    if child_int(marketplace_window, "MinHSize") != 968:
        fail("MarketplaceWnd lost its current 968-pixel minimum width")

    actions = ET.parse(SKIN / "EQUI_ActionsWindow.xml").getroot()
    actions_window = item(actions, "Screen", "ActionsWindow")
    if child_int(actions_window, "MinVSize") != 415:
        fail("ActionsWindow lost its current 415-pixel minimum height")
    action_page_contract = {
        "ACTW_MacrosPage": (
            "ACTW_MP_MacrosList", "ACTW_MP_DescriptionStmlBox"),
        "ActionsSpellsPage": (
            "ASP_SpellsList", "ASP_DescriptionStmlBox"),
        "ActionsDisciplinesPage": (
            "ADP_SkillSelectorList", "ADP_SkillDescriptionStmlBox"),
        "ActionsAbilitiesPage": (
            "AAP_SkillSelectorList", "AAP_SkillDescriptionStmlBox"),
    }
    for page_name, references in action_page_contract.items():
        page = item(actions, "Page", page_name)
        for reference in references:
            require_piece(page, reference, context=page_name)


def audit_map_resizing() -> None:
    """Keep map resize bounds compatible with every shipped layout."""
    default_minimum = (400, 400)
    default_variants = {
        "EQUI_MapViewWnd.xml",
        "EQUI_MapViewWnd1.xml",
        "EQUI_MapViewWnd2.xml",
    }
    expected_variants = {"EQUI_MapViewWnd.xml"} | {
        f"EQUI_MapViewWnd{i}.xml" for i in range(1, 9)
    }

    map_paths = sorted(SKIN.glob("EQUI_MapViewWnd*.xml"))
    if {path.name for path in map_paths} != expected_variants:
        fail("map layout variants changed unexpectedly")

    for path in map_paths:
        root = ET.parse(path).getroot()
        window = item(root, "Screen", "MapViewWnd")
        if (window.findtext("Style_Sizable") or "").strip().casefold() != "true":
            fail(f"{path.name} must remain user-resizable")
        minimum = (
            child_int(window, "MinHSize"),
            child_int(window, "MinVSize"),
        )
        authored = (
            child_int(window, "Size/CX"),
            child_int(window, "Size/CY"),
        )
        if path.name in default_variants and minimum != default_minimum:
            fail(f"{path.name} default-map minimum changed: {minimum}")
        if authored[0] < minimum[0] or authored[1] < minimum[1]:
            fail(
                f"{path.name} authored size {authored} is below its "
                f"resize minimum {minimum}"
            )

    profile_paths = {
        REPO / "UI_Spin_qeynos_LO1.ini",
        *SKIN.glob("default*.ini"),
        *(REPO / "layouts").rglob("*.ini"),
    }
    for profile_path in sorted(profile_paths):
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.read_string(profile_path.read_text(encoding="utf-8-sig"))
        if not parser.has_section("MapViewWnd"):
            continue
        has_width = parser.has_option("MapViewWnd", "Width")
        has_height = parser.has_option("MapViewWnd", "Height")
        if not has_width and not has_height:
            # Legacy position-only presets inherit geometry from the active
            # character/default profile and therefore cannot violate it here.
            continue
        if not has_width or not has_height:
            fail(
                f"{profile_path.relative_to(REPO)} [MapViewWnd] must define "
                "Width and Height together"
            )
        profile_size = (
            parser.getint("MapViewWnd", "Width"),
            parser.getint("MapViewWnd", "Height"),
        )
        if (profile_size[0] < default_minimum[0]
                or profile_size[1] < default_minimum[1]):
            fail(
                f"{profile_path.relative_to(REPO)} [MapViewWnd] "
                f"{profile_size} is below the active XML minimum "
                f"{default_minimum}"
            )


def audit_inventory_geometry() -> None:
    root = ET.parse(SKIN / "EQUI_InventoryWindow.xml").getroot()
    equipment = item(root, "Screen", "IW_Equipment")
    page = item(root, "Page", "IW_InvPage")
    pet_page = item(root, "Page", "IW_PetInvPage")
    loadout_page = item(root, "Page", "IW_LoadoutPage")
    storage_page = item(root, "Page", "IW_StoragePage")
    tab_host = item(root, "TabBox", "IW_Subwindows")
    view = item(root, "Screen", "IW_CharacterView")
    class_anim = item(root, "StaticAnimation", "ClassAnim")
    bags = item(root, "TileLayoutBox", "IW_Slots")
    bag_template = item(root, "InvSlot", "InvSlot23")
    name_label = item(root, "Label", "IW_Name")
    level_label = item(root, "Label", "IW_Level")
    class_label = item(root, "Label", "IW_Class")
    window = item(root, "Screen", "InventoryWindow")

    equip_slots = [n.text for n in equipment.findall("Pieces")
                   if n.text and n.text.startswith("InvSlot")]
    bag_slots = [n.text for n in bags.findall("Pieces")
                 if n.text and n.text.startswith("InvSlot")]
    if set(equip_slots) != {f"InvSlot{i}" for i in range(23)} or len(equip_slots) != 23:
        fail(f"equipment membership changed: {equip_slots}")
    if bag_slots != [f"InvSlot{i}" for i in range(23, 35)]:
        fail(f"bag rail membership changed: {bag_slots}")
    if any(root.find(f".//*[@item='IW_HexPlate{i}']") is not None
           or root.find(f".//*[@item='IWP_HexPlate{i}']") is not None
           for i in range(23)):
        fail("inventory equipment regained decorative hex underlays")
    if any(root.find(f".//*[@item='{name}']") is not None for name in (
            "A_SpinHex", "A_SpinHexGold", "A_SpinHexSm",
            "A_SpinHexGoldSm", "spin_deco.tga")):
        fail("inventory XML retained unused custom hex assets")

    if (child_int(class_anim, "Size/CX"), child_int(class_anim, "Size/CY")) != (75, 142):
        fail("native class artwork must remain 75x142")
    if child_int(window, "Size/CX") != 660 or child_int(window, "Size/CY") != 668:
        fail("inventory window must remain 660x668")
    host_width = (
        child_int(window, "Size/CX")
        - child_int(tab_host, "LeftAnchorOffset")
        - child_int(tab_host, "RightAnchorOffset")
    )
    if host_width != 495:
        fail(f"inventory tab host must remain 495px wide, got {host_width}")
    host_height = (
        child_int(window, "Size/CY")
        - child_int(tab_host, "TopAnchorOffset")
        - child_int(tab_host, "BottomAnchorOffset")
    )
    for tab_page in (page, pet_page, loadout_page, storage_page):
        page_left = child_int(tab_page, "Location/X")
        page_right = page_left + child_int(tab_page, "Size/CX")
        page_top = child_int(tab_page, "Location/Y")
        page_bottom = page_top + child_int(tab_page, "Size/CY")
        if (page_left < 0 or page_right > host_width
                or page_top < 0 or page_bottom > host_height):
            fail(
                f"{tab_page.get('item')} exceeds the tightened tab host: "
                f"{page_left}..{page_right} x {page_top}..{page_bottom} "
                f"of {host_width}x{host_height}"
            )
    if child_int(equipment, "Location/Y") + child_int(equipment, "Size/CY") > child_int(page, "Size/CY"):
        fail("equipment canvas exceeds the inventory page")

    # v3: the class crest lives on the identity rail (window level), keeping
    # its drop-to-auto-equip role visible on every tab.
    window_pieces = {n.text for n in window.findall("Pieces") if n.text}
    if "Screen:IW_CharacterView" not in window_pieces:
        fail("class crest must be a window-level identity-rail piece")
    if any(n.text and "IW_CharacterView" in n.text for n in equipment.findall("Pieces")):
        fail("class crest must no longer sit inside the equipment canvas")

    destroy = item(root, "Button", "IW_Destroy")
    crest_top = child_int(view, "Location/Y")
    crest_bottom = crest_top + child_int(view, "Size/CY")
    if child_int(destroy, "BottomAnchorOffset") + 4 > crest_top:
        fail("class crest overlaps the Destroy button")
    if crest_bottom + 4 > child_int(bags, "Location/Y"):
        fail("bag rail overlaps the class crest")
    if (child_int(bags, "Size/CX"), child_int(bags, "Size/CY")) != (112, 256):
        fail("bag rail geometry changed")
    if child_int(bags, "Location/X") + child_int(bags, "Size/CX") > child_int(window, "Size/CX"):
        fail("bag rail exceeds the tightened inventory frame")
    appearance = item(root, "Button", "IW_FacePick")
    bag_bottom = child_int(bags, "Location/Y") + child_int(bags, "Size/CY")
    appearance_top = child_int(window, "Size/CY") - child_int(
        appearance, "TopAnchorOffset")
    if appearance_top - bag_bottom != 12:
        fail("bag rail must retain 12px clearance above the bottom controls")

    # v4 rails: native 40px Legends slots at a disciplined 50px pitch.
    from restyle_inventory import (ANY_ROW, BAGS, BAG_BOX, BAG_GRID_WIDTH,
                                   BAG_SLOT_SIZE, BAG_SPACING, CANVAS, CREST,
                                   CREST_SIZE, FOOTER_SLOT_X, FOOTER_Y,
                                   IDENTITY_LABEL_GEOMETRY, LEFT_RAIL, L_X,
                                   PAGE, PAGE_LOCATION, PITCH, RIGHT_RAIL,
                                   R_X, SLOT_SIZE, STATS1,
                                   STATS2, STATS3, WEAPON_ROW, slot_pos)

    if (child_int(bags, "Size/CX"), child_int(bags, "Size/CY")) != BAG_BOX:
        fail(f"bag rail must remain the exact visible-grid box: {BAG_BOX}")

    identity_labels = {
        "IW_Name": name_label,
        "IW_Level": level_label,
        "IW_Class": class_label,
    }
    for label_name, label in identity_labels.items():
        expected = IDENTITY_LABEL_GEOMETRY[label_name]
        actual = (
            child_int(label, "Font"), child_int(label, "LeftAnchorOffset"),
            child_int(label, "RightAnchorOffset"),
            child_int(label, "TopAnchorOffset"),
            child_int(label, "BottomAnchorOffset"),
        )
        if actual != expected:
            fail(f"{label_name} lost its readable identity-rail geometry: {actual}")
    if (
        name_label.findtext("FontShadow") != "true"
        or name_label.findtext("AlignVCenter") != "true"
        or name_label.findtext("AlignCenter") != "true"
        or name_label.findtext("AlignRight") != "false"
    ):
        fail("player name must remain centered, shadowed, and unclipped")
    name_fields = [child.tag for child in name_label]
    if not (
        name_fields.index("NoWrap")
        < name_fields.index("AlignCenter")
        < name_fields.index("AlignRight")
        < name_fields.index("AlignVCenter")
    ):
        fail("IW_Name AlignVCenter violates the Legends Label schema order")
    if (
        level_label.findtext("AlignRight") != "false"
        or class_label.findtext("AlignRight") != "true"
    ):
        fail("level and class must remain in separate identity-rail cells")

    identity_flow = {
        "IW_NextLevel": (34, 49),
        "IW_ExpLabel": (34, 49),
        "IW_ExpPercLabel": (34, 49),
        "IW_ExpGauge": (48, 58),
        "IW_AltAdv": (60, 74),
        "IW_AltAdvPct": (60, 74),
        "IW_AltAdvPctLabel": (60, 74),
        "IW_AltAdvGauge": (74, 84),
        "IW_Weight": (86, 104),
        "IW_CurrentWeight": (86, 104),
        "IW_WeightNumber": (86, 104),
        "IW_MaxWeight": (86, 104),
        "IW_WeightWorn": (99, 114),
        "IW_WornWeightNumber": (99, 114),
    }
    for item_name, expected in identity_flow.items():
        kind = "Gauge" if item_name.endswith("Gauge") else "Label"
        control = item(root, kind, item_name)
        actual = (
            child_int(control, "TopAnchorOffset"),
            child_int(control, "BottomAnchorOffset"),
        )
        if actual != expected:
            fail(f"identity rail flow changed for {item_name}: {actual}")

    template_size = (
        child_int(bag_template, "Size/CX"),
        child_int(bag_template, "Size/CY"),
    )
    if template_size != (BAG_SLOT_SIZE, BAG_SLOT_SIZE):
        fail(
            "InvSlot23 must provide the explicit 40x40 FirstPieceTemplate "
            f"that makes all bag slots visible, got {template_size}"
        )
    if bags.findtext("FirstPieceTemplate") != "true":
        fail("bag rail must inherit geometry from the visible InvSlot23 template")
    if (
        child_int(bags, "Spacing") != BAG_SPACING
        or child_int(bags, "SecondarySpacing") != BAG_SPACING
    ):
        fail("bag rail spacing no longer matches its centered grid geometry")

    if (child_int(view, "Size/CX"), child_int(view, "Size/CY")) != CREST_SIZE:
        fail(f"class artwork viewport must remain {CREST_SIZE[0]}x{CREST_SIZE[1]}")

    page_geometry = (
        child_int(page, "Location/X"), child_int(page, "Location/Y"),
        child_int(page, "Size/CX"), child_int(page, "Size/CY"),
    )
    if page_geometry != (*PAGE_LOCATION, *PAGE):
        fail(
            "equipment page lost its centered tab-host alignment: "
            f"{page_geometry}"
        )
    bag_geometry = (
        child_int(bags, "Location/X"), child_int(bags, "Location/Y"),
    )
    if bag_geometry != BAGS:
        fail(f"bag rail left its class-card alignment: {bag_geometry}")
    bag_center2 = 2 * child_int(bags, "Location/X") + BAG_GRID_WIDTH
    crest_center2 = 2 * child_int(view, "Location/X") + CREST_SIZE[0]
    if bag_center2 != crest_center2:
        fail("visible bag columns must center directly under the class crest")
    if (child_int(view, "Location/X"), child_int(view, "Location/Y")) != CREST:
        fail("class crest left its identity-rail anchor")
    expected_slot_groups = {
        "armor": [2, 3, 5, 6, 8, 17, 7, 12],
        "weapons": [13, 14, 11, 22],
        "jewelry": [1, 4, 9, 10, 20, 18, 19, 15, 16],
        "utility": [0, 21],
    }
    actual_slot_groups = {
        "armor": LEFT_RAIL,
        "weapons": WEAPON_ROW,
        "jewelry": RIGHT_RAIL,
        "utility": ANY_ROW,
    }
    if actual_slot_groups != expected_slot_groups:
        fail(f"equipment rail ordering changed: {actual_slot_groups}")

    ledger_left_gap = STATS1[0] - (L_X + SLOT_SIZE)
    ledger_right_gap = R_X - (STATS1[0] + STATS1[2])
    right_canvas_margin = CANVAS[0] - (R_X + SLOT_SIZE)
    if (ledger_left_gap, ledger_right_gap, right_canvas_margin) != (13, 3, 13):
        fail(
            "equipment rail/ledger spacing changed: "
            f"{ledger_left_gap}/{ledger_right_gap}/{right_canvas_margin}"
        )

    for group_name, slot_ids in (
            ("armor", LEFT_RAIL), ("jewelry", RIGHT_RAIL)):
        positions = [slot_pos(slot_id)[0][1] for slot_id in slot_ids]
        if any(second - first != PITCH
               for first, second in zip(positions, positions[1:])):
            fail(f"{group_name} rail lost its {PITCH}px pitch: {positions}")
    canvas_w = child_int(equipment, "Size/CX")
    canvas_h = child_int(equipment, "Size/CY")
    for slot_id in range(23):
        (px, py), _ = slot_pos(slot_id)
        slot = item(root, "InvSlot", f"InvSlot{slot_id}")
        if (child_int(slot, "Location/X"), child_int(slot, "Location/Y")) != (px, py):
            fail(f"equipment slot {slot_id} left its rail position")
        if (child_int(slot, "Size/CX"), child_int(slot, "Size/CY")) != (
                SLOT_SIZE, SLOT_SIZE):
            fail(f"equipment slot {slot_id} must remain {SLOT_SIZE}x{SLOT_SIZE}")
        if slot.findtext("ScreenID") != f"InvSlot{slot_id}":
            fail(f"equipment slot {slot_id} lost its Legends ScreenID")
        if slot.findtext("EQType") != f"inventory/Equip {slot_id}":
            fail(f"equipment slot {slot_id} lost its Legends runtime binding")
        if not (slot.findtext("Background") or "").strip().startswith("A_Inv"):
            fail(f"equipment slot {slot_id} lost its native slot background")
        if (px < 0 or py < 0 or px + SLOT_SIZE > canvas_w
                or py + SLOT_SIZE > canvas_h):
            fail(f"equipment slot {slot_id} exceeds the canvas")
    footer_ids = WEAPON_ROW + ANY_ROW
    footer_positions = [slot_pos(slot_id)[0] for slot_id in footer_ids]
    expected_footer_positions = [
        (FOOTER_SLOT_X[slot_id], FOOTER_Y) for slot_id in footer_ids
    ]
    if footer_positions != expected_footer_positions:
        fail(f"equipment footer ordering changed: {footer_positions}")
    weapon_x = [FOOTER_SLOT_X[slot_id] for slot_id in WEAPON_ROW]
    any_x = [FOOTER_SLOT_X[slot_id] for slot_id in ANY_ROW]
    if (
        any(second - first != 58
            for first, second in zip(weapon_x, weapon_x[1:]))
        or any_x[1] - any_x[0] != 58
    ):
        fail("equipment footer must retain its 18px intra-group gaps")
    if any_x[0] - (weapon_x[-1] + SLOT_SIZE) != 34:
        fail("Any pair must retain its deliberate 34px footer separation")
    footer_left_margin = weapon_x[0]
    footer_right_margin = canvas_w - (any_x[-1] + SLOT_SIZE)
    if (footer_left_margin, footer_right_margin) != (63, 63):
        fail("equipment footer must remain exactly centered in the canvas")
    footer_to_right_rail = R_X - (any_x[-1] + SLOT_SIZE)
    if footer_to_right_rail != 10:
        fail("equipment footer must retain 10px beside the jewelry rail")

    # The ledger's container height is part of its information architecture:
    # it forces semantic, not accidental, vertical-first column breaks.
    stats_boxes = {
        "IW_Stats": STATS1,
        "IW_Stats2": STATS2,
        "IW_Stats3": STATS3,
    }
    for name, expected in stats_boxes.items():
        box = item(root, "TileLayoutBox", name)
        actual = (
            child_int(box, "Location/X"), child_int(box, "Location/Y"),
            child_int(box, "Size/CX"), child_int(box, "Size/CY"),
        )
        if actual != expected:
            fail(f"{name} geometry changed: {actual}, expected {expected}")
        if child_int(box, "Spacing") != 2 or child_int(box, "SecondarySpacing") != 6:
            fail(f"{name} lost its 2px row / 6px column rhythm")

    expected_stats_columns = [
        [
            "IWS_VitalsLabelScreen", "IWS_HPScreen", "IWS_ManaScreen",
            "IWS_EnduranceScreen", "IWS_ArmorClassScreen", "IWS_AttackScreen",
            "IWS_HasteScreen", "IWS_VelocityScreen", "IWS_CombatHPRegenScreen",
            "IWS_CombatManaRegenScreen", "IWS_CombatEndRegenScreen",
            "IWS_PrimaryDPSScreen", "IWS_SecondaryDPSScreen", "IWS_RangeDPSScreen",
            "IWS_StatsSpacerScreen",
        ],
        [
            "IWS_StatsLabelScreen", "IWS_StrengthScreen", "IWS_StaminaScreen",
            "IWS_AgilityScreen", "IWS_DexterityScreen", "IWS_WisdomScreen",
            "IWS_IntelligenceScreen", "IWS_CharismaScreen",
            "IWS_ResistsLabelScreen", "IWS_MagicScreen", "IWS_FireScreen",
            "IWS_ColdScreen", "IWS_DiseaseScreen", "IWS_PoisonScreen",
            "IWS_CorruptionScreen",
        ],
    ]
    expected_mod_columns = [
        [
            "IWS_HeroicModsLabelScreen", "IWS_ItemAccuracyScreen",
            "IWS_ItemAvoidanceScreen", "IWS_CombatEffectsScreen",
            "IWS_StrikeThroughScreen", "IWS_StunResistScreen",
        ],
        [
            "IWS_ModsLabelScreen", "IWS_DamageShieldingScreen",
            "IWS_DamageShieldMitigationScreen", "IWS_DoTShieldingScreen",
            "IWS_ShieldingScreen", "IWS_SpellShieldScreen",
        ],
    ]
    if vertical_tile_columns(root, item(root, "TileLayoutBox", "IW_Stats")) != expected_stats_columns:
        fail("primary inventory ledger lost its Vitals | Attributes/Resists columns")
    if vertical_tile_columns(root, item(root, "TileLayoutBox", "IW_Stats2")) != expected_mod_columns:
        fail("modifier ledger lost its Additional Modifiers | Mitigation columns")
    ledger_headings = {
        "IWS_VitalsLabel": "Character Vitals",
        "IWS_StatsLabel": "Primary Attributes",
        "IWS_ResistsLabel": "Resists",
        "IWS_HeroicModsLabel": "Additional Modifiers",
        "IWS_ModsLabel": "Mitigation",
        "IWS_AdditionalLabel": "Additional Information",
    }
    for label_name, expected_text in ledger_headings.items():
        label = item(root, "Label", label_name)
        if label.findtext("Text") != expected_text:
            fail(f"inventory ledger heading changed: {label_name}")
        if (child_int(label, "Font") != 3
                or (child_int(label, "Size/CX"), child_int(label, "Size/CY")) != (175, 14)):
            fail(f"inventory ledger heading is not legible: {label_name}")
        color = (
            child_int(label, "TextColor/R"), child_int(label, "TextColor/G"),
            child_int(label, "TextColor/B"),
        )
        if color != (208, 162, 84):
            fail(f"inventory ledger heading lost canonical gold: {label_name}")

    stats1_bottom = STATS1[1] + STATS1[3]
    stats2_bottom = STATS2[1] + STATS2[3]
    stats3_bottom = STATS3[1] + STATS3[3]
    if STATS2[1] - stats1_bottom != 8:
        fail("inventory ledger must retain its 8px primary section gap")
    if STATS3[1] - stats2_bottom != 8:
        fail("Additional Information must sit 8px below the modifier ledger")
    if FOOTER_Y - stats3_bottom != 31:
        fail("equipment footer must sit 31px below Additional Information")
    if canvas_h - (FOOTER_Y + SLOT_SIZE) != 9:
        fail("equipment footer must retain its 9px lower canvas margin")
    if stats3_bottom > canvas_h:
        fail("inventory ledger exceeds the equipment canvas")

    for key_name, value_name in (
            ("IWS_Bind", "IWS_BindZone"),
            ("IWS_Origin", "IWS_OriginZone"),
            ("IWS_Deity", "IWS_DeityName")):
        key = item(root, "Label", key_name)
        value = item(root, "Label", value_name)
        if (child_int(key, "Location/X"), child_int(key, "Size/CX")) != (0, 44):
            fail(f"Additional Information key clips: {key_name}")
        if (child_int(value, "Location/X"), child_int(value, "Size/CX")) != (44, 303):
            fail(f"Additional Information value alignment changed: {value_name}")

    persona = item(root, "Screen", "IWP_Equipment")
    persona_page = item(root, "Page", "IW_LoadoutPage")
    persona_view = item(root, "Screen", "IWP_CharacterView")
    persona_anim = item(root, "StaticAnimation", "PersonaAnim")
    from restyle_persona import (EQUIP_CANVAS, SLOT_POSITIONS,
                                 SLOT_SIZE as PERSONA_SLOT_SIZE)
    persona_slots = [n.text for n in persona.findall("Pieces")
                     if n.text and n.text.startswith("PersonaInvSlot")]
    persona_plates = [n.text for n in persona.findall("Pieces")
                       if n.text and n.text.startswith("IWP_HexPlate")]
    if set(persona_slots) != {f"PersonaInvSlot{i}" for i in range(23)}:
        fail("persona equipment membership changed")
    if persona_plates:
        fail("persona equipment regained decorative hex underlays")
    if (child_int(persona_page, "Size/CX"), child_int(persona_page, "Size/CY")) != (485, 620):
        fail("Loadouts/Personas page must use the full 485x620 tab canvas")
    if (child_int(persona, "Size/CX"), child_int(persona, "Size/CY")) != EQUIP_CANVAS:
        fail("persona equipment canvas changed")
    if (child_int(persona_view, "Size/CX"), child_int(persona_view, "Size/CY")) != (85, 171):
        fail("persona character viewport must remain 85x171")
    if (child_int(persona_anim, "Size/CX"), child_int(persona_anim, "Size/CY")) != (75, 142):
        fail("native persona artwork must remain 75x142")
    for slot_id in range(23):
        slot = item(root, "InvSlot", f"PersonaInvSlot{slot_id}")
        slot_x = child_int(slot, "Location/X")
        slot_y = child_int(slot, "Location/Y")
        if (slot_x, slot_y) != SLOT_POSITIONS[slot_id]:
            fail(f"persona equipment slot {slot_id} left its composition")
        if (child_int(slot, "Size/CX"), child_int(slot, "Size/CY")) != (
                PERSONA_SLOT_SIZE, PERSONA_SLOT_SIZE):
            fail(f"persona equipment slot {slot_id} changed size")
        if slot.findtext("ScreenID") != f"PersonaInvSlot{slot_id}":
            fail(f"persona equipment slot {slot_id} lost its Legends ScreenID")
        if slot.findtext("EQType") != f"personaInventory/Equip {slot_id}":
            fail(f"persona equipment slot {slot_id} lost its Legends binding")
        if not (slot.findtext("Background") or "").strip().startswith("A_Inv"):
            fail(f"persona equipment slot {slot_id} lost its native slot background")
        if (slot_x < 0 or slot_y < 0
                or slot_x + PERSONA_SLOT_SIZE > EQUIP_CANVAS[0]
                or slot_y + PERSONA_SLOT_SIZE > EQUIP_CANVAS[1]):
            fail(f"persona equipment slot {slot_id} exceeds its canvas")

    info_text = item(root, "Label", "IWP_LoadoutInfoText")
    info_status = item(root, "Label", "IWP_LoadoutInfoStatus")
    indicator = item(root, "Button", "IWP_LoadoutSwappableIndicator")
    if info_text.findtext("ScreenID") != "IWP_LoadoutInfoText":
        fail("loadout information label lost its current Legends ScreenID")
    if (
        child_int(info_text, "Location/X"), child_int(info_text, "Location/Y"),
        child_int(info_text, "Size/CX"), child_int(info_text, "Size/CY"),
        info_text.findtext("Text"),
    ) != (276, 453, 128, 14, "Swapping available:"):
        fail("loadout information label lost its compact status-row geometry")
    if (info_status.findtext("ScreenID") != "IWP_LoadoutInfoStatus"
            or info_status.findtext("EQType") != "1027"):
        fail("loadout availability status lost its current Legends binding")
    if (
        child_int(info_status, "Location/X"),
        child_int(info_status, "Location/Y"),
        child_int(info_status, "Size/CX"),
        child_int(info_status, "Size/CY"),
        info_status.findtext("Text"),
    ) != (406, 453, 34, 14, "YesNo"):
        fail("loadout availability status lost its compact row geometry")
    if indicator.findtext("ScreenID") != "IWP_LoadoutSwappableIndicator":
        fail("loadout swap indicator lost its current Legends ScreenID")
    if (child_int(indicator, "DecalSize/CX"),
            child_int(indicator, "DecalSize/CY")) != (20, 20):
        fail("loadout swap indicator decal must remain 20x20")
    if (
        indicator.findtext("Style_Transparent"),
        indicator.findtext("Style_Checkbox"),
        indicator.findtext("AutoStretch"),
        indicator.findtext("AlignRight"),
    ) != ("false", "false", "true", "true"):
        fail("loadout swap indicator lost its Legends button semantics")
    indicator_rect = (
        child_int(indicator, "LeftAnchorOffset"),
        child_int(indicator, "TopAnchorOffset"),
        child_int(indicator, "RightAnchorOffset"),
        child_int(indicator, "BottomAnchorOffset"),
    )
    if indicator_rect != (442, 450, 462, 470):
        fail(f"loadout swap indicator left its status row: {indicator_rect}")
    if indicator_rect[2] > child_int(persona_page, "Size/CX"):
        fail("loadout swap indicator exceeds the Loadouts page")
    loadout_pieces = {n.text for n in persona_page.findall("Pieces") if n.text}
    for control in (
            "IWP_LoadoutInfoText", "IWP_LoadoutInfoStatus",
            "IWP_LoadoutSwappableIndicator"):
        if control not in loadout_pieces:
            fail(f"loadout page does not mount {control}")
    if any(root.find(f".//*[@item='{old}']") is not None for old in (
            "IWP_LoadoutSwapAvailableLabel", "IWP_LoadoutSwapAvailable")):
        fail("obsolete pre-Legends loadout status controls remain")

    animations = ET.parse(SKIN / "EQUI_Animations.xml").getroot()
    for animation_name, expected_y in (
            ("A_IWAllowLoadoutSwap", 0), ("A_IWNoLoadoutSwap", 90)):
        matches = animations.findall(
            f".//Ui2DAnimation[@item='{animation_name}']")
        if len(matches) != 1:
            fail(f"{animation_name} must be defined exactly once")
        animation = matches[0]
        if animation.findtext("Cycle") != "true":
            fail(f"{animation_name} must remain cyclic like current Legends")
        if animation.findtext("Frames/Texture") != "window_pieces11c.dds":
            fail(f"{animation_name} lost its Legends texture")
        if (child_int(animation, "Frames/Location/X"),
                child_int(animation, "Frames/Location/Y")) != (0, expected_y):
            fail(f"{animation_name} lost its Legends decal source")
        if (child_int(animation, "Frames/Size/CX"),
                child_int(animation, "Frames/Size/CY")) != (30, 30):
            fail(f"{animation_name} must remain a 30x30 Legends frame")
        if (child_int(animation, "Frames/Hotspot/X"),
                child_int(animation, "Frames/Hotspot/Y")) != (0, 0):
            fail(f"{animation_name} hotspot changed")
        if child_int(animation, "Frames/Duration") != 1000:
            fail(f"{animation_name} duration changed")


def main() -> int:
    xml_files, texture_refs, template_count, template_reference_files = audit_xml()
    tga_count, dds_count, cur_count = audit_binary_assets()
    audit_pet_geometry()
    audit_map_resizing()
    audit_inventory_progression()
    audit_aa_window()
    audit_august_2026_runtime_contract()
    audit_inventory_geometry()
    print("SpinUI asset audit: ALL PASS")
    print(f"  XML {len(xml_files)} | texture refs {len(texture_refs)} | "
          f"TGA {tga_count} | DDS {dds_count} | CUR {cur_count}")
    print(f"  window templates {template_count} | "
          f"reference files {template_reference_files} | no unresolved symbols")
    from restyle_pet import BUFF_CAPACITY, WINDOW_SIZES
    pet_default = "EQUI_PetInfoWindow.xml"
    print(
        f"  pet {WINDOW_SIZES[pet_default][0]}x{WINDOW_SIZES[pet_default][1]} "
        f"fixed | commands 14 | effects {BUFF_CAPACITY[pet_default]} "
        f"icon cells | variants {len(WINDOW_SIZES)}"
    )
    print("  inventory 660x668 | equipment 23 + pet illusion selector | "
          "AA deck 6 tabs + true progress | ledger 15/15 + 6/6 | "
          "footer 6 | persona 23 | bags 12")
    print("  Legends Aug 2026 | Options 6 | spellbooks 3 | "
          "task tracker + marketplace + actions")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"SpinUI asset audit: FAIL — {exc}", file=sys.stderr)
        raise SystemExit(1)
