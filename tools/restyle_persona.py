#!/usr/bin/env python3
"""Bring the Loadouts/Personas tab up to SpinUI's equipment-screen quality.

The stock tab is a 388x401 legacy panel and declares its persona equipment
container as 0x0.  This pass uses the full 585x720 Inventory canvas, gives the
client-owned persona model its native viewport, arranges all 23 equipment
slots on SpinUI hex plates, and preserves the loadout and class controls below.

Idempotent: the SPIN-PERSONA marker is written with the generated plate block.
Run from the repository root after ``restyle_inventory.py``.
"""

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
XMLF = REPO / "spinui_reloaded" / "EQUI_InventoryWindow.xml"

SLOT_POSITIONS = {
    # left equipment rail
    1: (35, 10), 5: (95, 10), 6: (35, 66), 9: (95, 66),
    15: (35, 122), 7: (95, 122), 17: (35, 178), 8: (95, 178),
    # right equipment rail
    4: (394, 10), 3: (454, 10), 2: (394, 66), 10: (454, 66),
    16: (394, 122), 12: (454, 122), 20: (394, 178),
    18: (454, 178), 19: (514, 178),
    # centered weapon row plus separated Any pair
    13: (124, 240), 14: (176, 240), 11: (228, 240), 22: (280, 240),
    0: (352, 240), 21: (404, 240),
}
GOLD_SLOTS = {13, 14, 11, 22}


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


def plate(slot_id, x, y):
    anim = "A_SpinHexGold" if slot_id in GOLD_SLOTS else "A_SpinHex"
    return f'''\t<StaticAnimation item="IWP_HexPlate{slot_id}">
\t\t<ScreenID>IWP_HexPlate{slot_id}</ScreenID>
\t\t<RelativePosition>true</RelativePosition>
\t\t<Location><X>{x - 8}</X><Y>{y - 8}</Y></Location>
\t\t<Size><CX>56</CX><CY>56</CY></Size>
\t\t<Animation>{anim}</Animation>
\t</StaticAnimation>
'''


def main():
    text = XMLF.read_text(encoding="utf-8")
    if "SPIN-PERSONA" in text:
        print("persona tab already restyled — nothing to do")
        return 0
    assert 'item="A_SpinHex"' in text, "run restyle_inventory.py first"

    for slot_id, (x, y) in SLOT_POSITIONS.items():
        text = set_location(text, "InvSlot", f"PersonaInvSlot{slot_id}", x, y)

    plates = "\t<!-- SPIN-PERSONA: full-size persona equipment composition -->\n"
    plates += "".join(plate(slot_id, *SLOT_POSITIONS[slot_id]) for slot_id in range(23))
    anchor = '\t<Screen item="IWP_Equipment">'
    assert anchor in text
    text = text.replace(anchor, plates + anchor, 1)
    pieces = "".join(
        f"\t\t<Pieces>IWP_HexPlate{slot_id}</Pieces>\n" for slot_id in range(23))
    anchor = "\t\t<Pieces>PersonaInvSlot0</Pieces>"
    assert anchor in text
    text = text.replace(anchor, pieces + anchor, 1)

    # IWP_Equipment carries a commented legacy Size before its active 0x0
    # values, so replace that Size block explicitly instead of allowing a
    # cross-element regex to reach PersonaAnim.
    text = set_location(text, "Screen", "IWP_Equipment", 8, 24)
    pattern = re.compile(
        r'(<Screen item="IWP_Equipment">.*?<Size>).*?(</Size>)', re.S)
    replacement = (r'\g<1>\n\t\t\t<CX>569</CX>\n'
                   r'\t\t\t<CY>292</CY>\n\t\t\g<2>')
    text, count = pattern.subn(replacement, text, count=1)
    assert count == 1, "persona equipment size"
    text = set_location(text, "StaticAnimation", "PersonaAnim", 3, 11, 75, 142)
    text = set_location(text, "Screen", "IWP_CharacterView", 242, 25, 85, 171)
    text = set_location(text, "Page", "IW_LoadoutPage", 0, 22, 585, 720)
    text = set_location(text, "Label", "IWP_EquipmentLabel", 12, 5, 220, 14)
    text = set_location(text, "Label", "IWP_LoadoutLabel", 12, 320, 220, 14)
    text = set_location(text, "Label", "IWP_ClassesLabel", 12, 562, 220, 14)

    for field, value in (("LeftAnchorOffset", 8), ("TopAnchorOffset", 338),
                         ("RightAnchorOffset", 8), ("BottomAnchorOffset", 518)):
        text = set_anchor(text, "Listbox", "IWP_LoadoutList", field, value)
    for item, left, right in (("IWP_AddLoadout", 8, 86),
                              ("IWP_EditLoadout", 92, 170),
                              ("IWP_SwapLoadout", 176, 254)):
        for field, value in (("LeftAnchorOffset", left), ("RightAnchorOffset", right),
                             ("TopAnchorOffset", 526), ("BottomAnchorOffset", 550)):
            text = set_anchor(text, "Button", item, field, value)
    text = set_location(text, "Label", "IWP_LoadoutSwapAvailableLabel", 276, 531, 128, 14)
    text = set_location(text, "Label", "IWP_LoadoutSwapAvailable", 406, 531, 40, 14)

    for index, x in enumerate((98, 190, 282, 374), 1):
        text = set_location(text, "Screen", f"IWP_CharacterLevels{index}", x, 582, 90, 75)
    for field, value in (("LeftAnchorOffset", 8), ("TopAnchorOffset", 582),
                         ("RightAnchorOffset", 8), ("BottomAnchorOffset", 710)):
        text = set_anchor(text, "Listbox", "IWP_ClassList", field, value)

    # The equipment heading was commented out in the legacy tab.
    text, count = re.subn(
        r'<!--\s*<Pieces>IWP_EquipmentLabel</Pieces>\s*-->',
        '<Pieces>IWP_EquipmentLabel</Pieces>', text, count=1)
    assert count == 1, "equipment label piece"

    XMLF.write_text(text, encoding="utf-8")
    ET.parse(XMLF)
    for slot_id in range(23):
        assert f'item="IWP_HexPlate{slot_id}"' in text
        assert f"<Pieces>IWP_HexPlate{slot_id}</Pieces>" in text
    print("persona/loadouts tab restyled: 23 slots + model + loadouts + class levels — parse OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
