#!/usr/bin/env python3
"""Compact Loadouts/Personas tab for Spin's UI Reloaded (v3).

Migrates the v2 full-size persona composition (SPIN-PERSONA, 585x720 page)
to the compact v3 canvas that matches the 660x700 inventory window:

* Page shrinks to the 485x620 tab canvas.
* Equipment condenses to a 469x270 composition on 46px hex plates: a 2x4
  armor cluster left, the native persona model centered, a 3x3 jewelry
  cluster right, and a centered weapon row plus separated Any pair below.
* Loadout table, actions, and class-level cards re-flow beneath.

The client-required PersonaInvSlot identifiers, the 85x171 model viewport,
and the 75x142 native art are preserved.

Idempotent: looks for the SPIN-PERSONA-3 marker. Requires v2 (SPIN-PERSONA)
and the v3 equipment pass (SPIN-DECO-3) to have run first.
Run from repo root:  python3 tools/restyle_persona.py
"""

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
XMLF = REPO / "spinui_reloaded" / "EQUI_InventoryWindow.xml"

PLATE = 46
SLOT_INSET = 3

# plate coordinates inside the 469x270 IWP_Equipment canvas
PLATE_POSITIONS = {
    # left armor cluster, 2x4: ear/neck, shoulder/wrist, ring/arms, chest/back
    1: (8, 8), 5: (58, 8), 6: (8, 58), 9: (58, 58),
    15: (8, 108), 7: (58, 108), 17: (8, 158), 8: (58, 158),
    # right jewelry cluster, 3x3
    4: (323, 8), 3: (373, 8), 2: (423, 8),
    10: (323, 58), 16: (373, 58), 12: (423, 58),
    20: (323, 108), 18: (373, 108), 19: (423, 108),
    # centered weapon row plus separated Any pair
    13: (72, 216), 14: (122, 216), 11: (172, 216), 22: (222, 216),
    0: (296, 216), 21: (346, 216),
}
GOLD_SLOTS = {13, 14, 11, 22}

EQUIP_CANVAS = (469, 270)
PAGE = (485, 620)


def set_location(text, kind, item, x, y, cx=None, cy=None):
    pattern = re.compile(
        rf'(<{kind} item="{re.escape(item)}">.*?<Location>\s*<X>)-?\d+'
        rf'(</X>\s*<Y>)-?\d+(</Y>)[^\S\r\n]*', re.S)
    text, count = pattern.subn(rf'\g<1>{x}\g<2>{y}\g<3>', text, count=1)
    assert count == 1, f"location {item}"
    if cx is not None:
        pattern = re.compile(
            rf'(<{kind} item="{re.escape(item)}">.*?<Size>\s*<CX>)\d+'
            rf'(</CX>\s*<CY>)\d+(</CY>)', re.S)
        text, count = pattern.subn(rf'\g<1>{cx}\g<2>{cy}\g<3>', text, count=1)
        assert count == 1, f"size {item}"
    return text


def set_anchor(text, kind, item, field, value):
    pattern = re.compile(
        rf'(<{kind} item="{re.escape(item)}">.*?<{field}>)-?\d+(</{field}>)', re.S)
    text, count = pattern.subn(rf'\g<1>{value}\g<2>', text, count=1)
    assert count == 1, f"{field} {item}"
    return text


def set_field(text, kind, item, field, value):
    pattern = re.compile(
        rf'(<{kind} item="{re.escape(item)}">.*?<{field}>)[^<]*(</{field}>)', re.S)
    text, count = pattern.subn(rf'\g<1>{value}\g<2>', text, count=1)
    assert count == 1, f"{field} {item}"
    return text


def main():
    text = XMLF.read_text(encoding="utf-8")
    if "SPIN-PERSONA-3" in text:
        print("persona tab already at v3 — nothing to do")
        return 0
    assert "SPIN-PERSONA" in text, "run history: restyle_persona v2 first"
    assert "SPIN-DECO-3" in text, "run restyle_inventory.py (v3) first"

    text = text.replace(
        "<!-- SPIN-PERSONA: full-size persona equipment composition -->",
        "<!-- SPIN-PERSONA-3: compact persona equipment composition -->", 1)

    for slot_id, (px, py) in PLATE_POSITIONS.items():
        text = set_location(text, "InvSlot", f"PersonaInvSlot{slot_id}",
                            px + SLOT_INSET, py + SLOT_INSET)
        text = set_location(text, "StaticAnimation", f"IWP_HexPlate{slot_id}",
                            px, py, PLATE, PLATE)
        text = set_field(
            text, "StaticAnimation", f"IWP_HexPlate{slot_id}", "Animation",
            "A_SpinHexGoldSm" if slot_id in GOLD_SLOTS else "A_SpinHexSm")

    text = set_location(text, "Screen", "IWP_Equipment", 8, 24)
    pattern = re.compile(
        r'(<Screen item="IWP_Equipment">.*?<Size>).*?(</Size>)', re.S)
    replacement = (rf'\g<1>\n\t\t\t<CX>{EQUIP_CANVAS[0]}</CX>\n'
                   rf'\t\t\t<CY>{EQUIP_CANVAS[1]}</CY>\n\t\t\g<2>')
    text, count = pattern.subn(replacement, text, count=1)
    assert count == 1, "persona equipment size"
    text = set_location(text, "Screen", "IWP_CharacterView", 192, 8, 85, 171)
    text = set_location(text, "Page", "IW_LoadoutPage", 0, 22, *PAGE)
    text = set_location(text, "Label", "IWP_EquipmentLabel", 12, 5, 220, 14)
    text = set_location(text, "Label", "IWP_LoadoutLabel", 12, 300, 220, 14)
    text = set_location(text, "Label", "IWP_ClassesLabel", 12, 480, 220, 14)

    for field, value in (("LeftAnchorOffset", 8), ("TopAnchorOffset", 316),
                         ("RightAnchorOffset", 8), ("BottomAnchorOffset", 440)):
        text = set_anchor(text, "Listbox", "IWP_LoadoutList", field, value)
    for item, left, right in (("IWP_AddLoadout", 8, 86),
                              ("IWP_EditLoadout", 92, 170),
                              ("IWP_SwapLoadout", 176, 254)):
        for field, value in (("LeftAnchorOffset", left), ("RightAnchorOffset", right),
                             ("TopAnchorOffset", 448), ("BottomAnchorOffset", 472)):
            text = set_anchor(text, "Button", item, field, value)
    text = set_location(text, "Label", "IWP_LoadoutSwapAvailableLabel", 276, 453, 128, 14)
    text = set_location(text, "Label", "IWP_LoadoutSwapAvailable", 406, 453, 40, 14)

    for index, x in enumerate((8, 134, 260, 386), 1):
        text = set_location(text, "Screen", f"IWP_CharacterLevels{index}", x, 496, 90, 75)
    for field, value in (("LeftAnchorOffset", 8), ("TopAnchorOffset", 496),
                         ("RightAnchorOffset", 8), ("BottomAnchorOffset", 610)):
        text = set_anchor(text, "Listbox", "IWP_ClassList", field, value)

    XMLF.write_text(text, encoding="utf-8")
    ET.parse(XMLF)
    for slot_id in range(23):
        assert f'item="IWP_HexPlate{slot_id}"' in text
        assert f"<Pieces>IWP_HexPlate{slot_id}</Pieces>" in text
    print("persona/loadouts tab compacted: clustered slots + model + loadouts "
          f"+ class levels on the {PAGE[0]}x{PAGE[1]} canvas — parse OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
