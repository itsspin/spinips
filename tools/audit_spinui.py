#!/usr/bin/env python3
"""Release-grade structural audit for SpinUI assets and critical geometry.

This intentionally uses only the Python standard library so the same checks
run locally and in the Windows packaging workflow before a release is built.
"""

from __future__ import annotations

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

    variants = {
        "EQUI_PetInfoWindow.xml": (356, 255),
        "EQUI_PetInfoWindow1.xml": (356, 255),
        "EQUI_PetInfoWindow2.xml": (356, 255),
        "EQUI_PetInfoWindow3.xml": (460, 255),
    }
    command_items = [f"PIW_Pet{i}_Button" for i in range(14)]
    command_pieces = command_items.copy()

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
            if (child_int(button, "Size/CX"), child_int(button, "Size/CY")) != (84, 23):
                fail(f"{filename} {button_name} must remain 84x23")

        tile = item(root, "TileLayoutBox", "PIW_PetButtons")
        pieces = [node.text for node in tile.findall("Pieces") if node.text]
        if pieces != command_pieces:
            fail(f"{filename} must contain each pet command exactly once: {pieces}")
        if tile.findtext("HorizontalFirst", "").strip().casefold() != "true":
            fail(f"{filename} pet commands must flow row-first")

        window_width, window_height = actual_size
        subwindow = item(root, "Screen", "PetInfoSubWindow")
        sub_left, sub_top, sub_right, sub_bottom = _stretched_rect(
            subwindow, window_width, window_height,
            label=f"{filename} pet info panel",
        )
        sub_width = sub_right - sub_left
        sub_height = sub_bottom - sub_top
        tile_left, tile_top, tile_right, tile_bottom = _stretched_rect(
            tile, sub_width, sub_height,
            label=f"{filename} pet command grid",
        )
        tile_width = tile_right - tile_left
        tile_height = tile_bottom - tile_top
        spacing = child_int(tile, "Spacing")
        secondary_spacing = child_int(tile, "SecondarySpacing")
        button_width, button_height = 84, 23
        columns = (tile_width + spacing) // (button_width + spacing)
        if columns != 4:
            fail(f"{filename} pet commands must retain four readable columns")

        button_rects: list[tuple[int, int, int, int]] = []
        for index, button_name in enumerate(command_items):
            column = index % columns
            row = index // columns
            left = tile_left + column * (button_width + spacing)
            top = tile_top + row * (button_height + secondary_spacing)
            rect = (left, top, left + button_width, top + button_height)
            if rect[2] > tile_right or rect[3] > tile_bottom:
                fail(f"{filename} {button_name} clips outside the command grid")
            if any(_rects_overlap(rect, prior) for prior in button_rects):
                fail(f"{filename} {button_name} overlaps another pet command")
            button_rects.append(rect)

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
    if root.find(".//*[@item='IW_HexPlate23']") is not None or root.find(
            ".//*[@item='IW_HexPlate24']") is not None:
        fail("bag slots 23/24 regained equipment hex plates")

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

    # v3 rails: 12-position columns on 46px plates at pitch 50, slots inset 3.
    from restyle_inventory import (ANY_ROW, BAGS, BAG_BOX, BAG_GRID_WIDTH,
                                   BAG_SLOT_SIZE, BAG_SPACING, CANVAS, CREST,
                                   CREST_SIZE, FOOTER_SLOT_X, FOOTER_Y,
                                   IDENTITY_LABEL_GEOMETRY, LEFT_RAIL, L_X,
                                   PAGE, PAGE_LOCATION, PITCH, PLATE,
                                   RIGHT_RAIL, R_X, SLOT_INSET, STATS1,
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
        or name_label.findtext("AlignCenter") != "true"
        or name_label.findtext("AlignRight") != "false"
    ):
        fail("player name must remain centered, shadowed, and unclipped")
    if (
        level_label.findtext("AlignRight") != "false"
        or class_label.findtext("AlignRight") != "true"
    ):
        fail("level and class must remain in separate identity-rail cells")

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

    ledger_left_gap = STATS1[0] - (L_X + PLATE)
    ledger_right_gap = R_X - (STATS1[0] + STATS1[2])
    right_canvas_margin = CANVAS[0] - (R_X + PLATE)
    if (ledger_left_gap, ledger_right_gap, right_canvas_margin) != (10, 4, 6):
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
        (px, py), gold = slot_pos(slot_id)
        slot = item(root, "InvSlot", f"InvSlot{slot_id}")
        plate = item(root, "StaticAnimation", f"IW_HexPlate{slot_id}")
        if (child_int(plate, "Location/X"), child_int(plate, "Location/Y")) != (px, py):
            fail(f"equipment plate {slot_id} left its rail position")
        if (child_int(plate, "Size/CX"), child_int(plate, "Size/CY")) != (PLATE, PLATE):
            fail(f"equipment plate {slot_id} is no longer {PLATE}px")
        if (child_int(slot, "Location/X") != px + SLOT_INSET
                or child_int(slot, "Location/Y") != py + SLOT_INSET):
            fail(f"equipment slot {slot_id} lost its hex alignment")
        if (child_int(slot, "Size/CX"), child_int(slot, "Size/CY")) != (40, 40):
            fail(f"equipment slot {slot_id} must remain 40x40")
        expected_animation = "A_SpinHexGoldSm" if gold else "A_SpinHexSm"
        if plate.findtext("Animation") != expected_animation:
            fail(f"equipment plate {slot_id} lost its semantic rail treatment")
        if not (slot.findtext("Background") or "").strip():
            fail(f"equipment slot {slot_id} lost its native slot label art")
        if px < 0 or py < 0 or px + PLATE > canvas_w or py + PLATE > canvas_h:
            fail(f"equipment plate {slot_id} exceeds the canvas")
        slot_x = child_int(slot, "Location/X")
        slot_y = child_int(slot, "Location/Y")
        if (slot_x < px or slot_y < py
                or slot_x + 40 > px + PLATE or slot_y + 40 > py + PLATE):
            fail(f"equipment slot {slot_id} clips outside its hex plate")
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
        fail("equipment footer must retain its 12px intra-group gaps")
    if any_x[0] - (weapon_x[-1] + PLATE) != 28:
        fail("Any pair must retain its deliberate 28px footer separation")
    footer_left_margin = weapon_x[0]
    footer_right_margin = canvas_w - (any_x[-1] + PLATE)
    if (footer_left_margin, footer_right_margin) != (60, 60):
        fail("equipment footer must remain exactly centered in the canvas")
    footer_to_right_rail = R_X - (any_x[-1] + PLATE)
    if footer_to_right_rail != 8:
        fail("equipment footer must retain 8px beside the jewelry rail")

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
        if color != (219, 158, 42):
            fail(f"inventory ledger heading lost canonical gold: {label_name}")

    stats1_bottom = STATS1[1] + STATS1[3]
    stats2_bottom = STATS2[1] + STATS2[3]
    stats3_bottom = STATS3[1] + STATS3[3]
    if STATS2[1] - stats1_bottom != 8:
        fail("inventory ledger must retain its 8px primary section gap")
    if STATS3[1] - stats2_bottom != 8:
        fail("Additional Information must sit 8px below the modifier ledger")
    if FOOTER_Y - stats3_bottom != 16:
        fail("equipment footer must sit 16px below Additional Information")
    if canvas_h - (FOOTER_Y + PLATE) != 6:
        fail("equipment footer must retain its 6px lower canvas margin")
    if stats3_bottom > canvas_h:
        fail("inventory ledger exceeds the equipment canvas")

    persona = item(root, "Screen", "IWP_Equipment")
    persona_page = item(root, "Page", "IW_LoadoutPage")
    persona_view = item(root, "Screen", "IWP_CharacterView")
    persona_anim = item(root, "StaticAnimation", "PersonaAnim")
    persona_slots = [n.text for n in persona.findall("Pieces")
                     if n.text and n.text.startswith("PersonaInvSlot")]
    persona_plates = [n.text for n in persona.findall("Pieces")
                      if n.text and n.text.startswith("IWP_HexPlate")]
    if set(persona_slots) != {f"PersonaInvSlot{i}" for i in range(23)}:
        fail("persona equipment membership changed")
    if set(persona_plates) != {f"IWP_HexPlate{i}" for i in range(23)}:
        fail("persona hex plates changed")
    if (child_int(persona_page, "Size/CX"), child_int(persona_page, "Size/CY")) != (485, 620):
        fail("Loadouts/Personas page must use the full 485x620 tab canvas")
    if (child_int(persona, "Size/CX"), child_int(persona, "Size/CY")) != (469, 270):
        fail("persona equipment canvas changed")
    if (child_int(persona_view, "Size/CX"), child_int(persona_view, "Size/CY")) != (85, 171):
        fail("persona character viewport must remain 85x171")
    if (child_int(persona_anim, "Size/CX"), child_int(persona_anim, "Size/CY")) != (75, 142):
        fail("native persona artwork must remain 75x142")
    for slot_id in range(23):
        slot = item(root, "InvSlot", f"PersonaInvSlot{slot_id}")
        plate = item(root, "StaticAnimation", f"IWP_HexPlate{slot_id}")
        if (child_int(slot, "Location/X") != child_int(plate, "Location/X") + 3
                or child_int(slot, "Location/Y") != child_int(plate, "Location/Y") + 3):
            fail(f"persona equipment slot {slot_id} lost its hex alignment")
        if (child_int(plate, "Location/X") < 0
                or child_int(plate, "Location/Y") < 0
                or child_int(plate, "Location/X") + child_int(plate, "Size/CX") > 469
                or child_int(plate, "Location/Y") + child_int(plate, "Size/CY") > 270):
            fail(f"persona equipment plate {slot_id} exceeds its canvas")


def main() -> int:
    xml_files, texture_refs, template_count, template_reference_files = audit_xml()
    tga_count, dds_count, cur_count = audit_binary_assets()
    audit_pet_geometry()
    audit_inventory_geometry()
    print("SpinUI asset audit: ALL PASS")
    print(f"  XML {len(xml_files)} | texture refs {len(texture_refs)} | "
          f"TGA {tga_count} | DDS {dds_count} | CUR {cur_count}")
    print(f"  window templates {template_count} | "
          f"reference files {template_reference_files} | no unresolved symbols")
    print("  pet 356x255 | commands 14 | four columns | variants 4")
    print("  inventory 660x668 | equipment 23 | ledger 15/15 + 6/6 | footer 6 | persona 23 | bags 12")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"SpinUI asset audit: FAIL — {exc}", file=sys.stderr)
        raise SystemExit(1)
