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


def audit_xml() -> tuple[list[Path], set[str]]:
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
    return xml_files, texture_refs


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


def audit_inventory_geometry() -> None:
    root = ET.parse(SKIN / "EQUI_InventoryWindow.xml").getroot()
    equipment = item(root, "Screen", "IW_Equipment")
    page = item(root, "Page", "IW_InvPage")
    view = item(root, "Screen", "IW_CharacterView")
    class_anim = item(root, "StaticAnimation", "ClassAnim")
    bags = item(root, "TileLayoutBox", "IW_Slots")
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
    if (child_int(view, "Size/CX"), child_int(view, "Size/CY")) != (85, 171):
        fail("class artwork viewport must remain 85x171")
    if child_int(window, "Size/CX") != 780 or child_int(window, "Size/CY") != 800:
        fail("inventory window must remain 780x800")
    if child_int(equipment, "Location/Y") + child_int(equipment, "Size/CY") > child_int(page, "Size/CY"):
        fail("equipment canvas exceeds the inventory page")

    destroy = item(root, "Button", "IW_Destroy")
    if child_int(destroy, "BottomAnchorOffset") + 12 > child_int(bags, "Location/Y"):
        fail("Destroy button lacks a 12px visual gap above the bag rail")
    if (child_int(bags, "Size/CX"), child_int(bags, "Size/CY")) != (112, 280):
        fail("bag rail geometry changed")

    bottom_positions = {
        13: 98, 14: 160, 11: 222, 22: 284,
        0: 358, 21: 420,
    }
    for slot_id, plate_x in bottom_positions.items():
        slot = item(root, "InvSlot", f"InvSlot{slot_id}")
        plate = item(root, "StaticAnimation", f"IW_HexPlate{slot_id}")
        if (child_int(slot, "Location/X") != plate_x + 8
                or child_int(slot, "Location/Y") != 666
                or child_int(plate, "Location/X") != plate_x
                or child_int(plate, "Location/Y") != 658):
            fail(f"bottom equipment slot {slot_id} is no longer aligned")
    ammo_right = bottom_positions[22] + 56
    if bottom_positions[0] - ammo_right < 16:
        fail("Any slots lost their visual gap after Ammo")
    row_left = min(bottom_positions.values())
    row_right = max(bottom_positions.values()) + 56
    if abs((row_left + row_right) - child_int(equipment, "Size/CX")) > 1:
        fail("bottom equipment row is no longer centered")


def main() -> int:
    xml_files, texture_refs = audit_xml()
    tga_count, dds_count, cur_count = audit_binary_assets()
    audit_inventory_geometry()
    print("SpinUI asset audit: ALL PASS")
    print(f"  XML {len(xml_files)} | texture refs {len(texture_refs)} | "
          f"TGA {tga_count} | DDS {dds_count} | CUR {cur_count}")
    print("  inventory 780x800 | equipment 23 | bottom Any 2 | bags 12 | class art 75x142")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"SpinUI asset audit: FAIL — {exc}", file=sys.stderr)
        raise SystemExit(1)
