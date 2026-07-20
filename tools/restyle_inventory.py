#!/usr/bin/env python3
"""Narcissus-inspired equipment screen for Spin's UI Reloaded.

Rebuilds the Inventory window's Equipment tab as a cinematic composition:
two vertical slot rails (armor left, jewelry right) on floating hex plates,
weapons plus a separated Any-slot pair along the bottom, the class crest, and
the stat columns flowing between the rails.  The window grows to 780x800,
leaving a generous identity and twelve-slot bag rail on the right.

All 23 InvSlot items keep their ScreenIDs/EQTypes — only Locations move.
New art lives in spin_deco.tga; decorative StaticAnimations are additive.

Idempotent: refuses to run twice (looks for the SPIN-DECO marker).
Run from repo root:  python3 tools/restyle_inventory.py
"""

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
XMLF = REPO / "spinui_reloaded" / "EQUI_InventoryWindow.xml"

# rails: EQ slot ids in top-to-bottom order
LEFT_RAIL = [2, 3, 5, 6, 8, 17, 7, 12]         # head face neck shoulder back chest arms hands
RIGHT_RAIL = [1, 4, 9, 10, 20, 18, 19, 15, 16]  # ears wrists waist legs feet rings
WEAPON_ROW = [13, 14, 11, 22]                   # primary secondary range ammo
ANY_ROW = [0, 21]                               # IS_ANY1 / IS_ANY2

PITCH = 62
PLATE = 56
L_X, R_X = 4, 513
RAIL_Y = 4
W_Y = 658
W_X0 = 98
ANY_X0 = 358


def slot_pos(slot_id):
    if slot_id in LEFT_RAIL:
        i = LEFT_RAIL.index(slot_id)
        return (L_X, RAIL_Y + i * PITCH), False
    if slot_id in RIGHT_RAIL:
        i = RIGHT_RAIL.index(slot_id)
        return (R_X, RAIL_Y + i * PITCH), False
    if slot_id in WEAPON_ROW:
        i = WEAPON_ROW.index(slot_id)
        return (W_X0 + i * PITCH, W_Y), True
    i = ANY_ROW.index(slot_id)
    return (ANY_X0 + i * PITCH, W_Y), False


HEADER_ART = """
	<!-- SPIN-DECO: Narcissus-style equipment rails -->
	<TextureInfo item="spin_deco.tga">
		<Size>
			<CX>128</CX>
			<CY>128</CY>
		</Size>
	</TextureInfo>
	<Ui2DAnimation item="A_SpinHex">
		<Cycle>true</Cycle>
		<Frames>
			<Texture>spin_deco.tga</Texture>
			<Location>
				<X>0</X>
				<Y>0</Y>
			</Location>
			<Size>
				<CX>56</CX>
				<CY>56</CY>
			</Size>
			<Hotspot>
				<X>0</X>
				<Y>0</Y>
			</Hotspot>
			<Duration>1000</Duration>
		</Frames>
	</Ui2DAnimation>
	<Ui2DAnimation item="A_SpinHexGold">
		<Cycle>true</Cycle>
		<Frames>
			<Texture>spin_deco.tga</Texture>
			<Location>
				<X>64</X>
				<Y>0</Y>
			</Location>
			<Size>
				<CX>56</CX>
				<CY>56</CY>
			</Size>
			<Hotspot>
				<X>0</X>
				<Y>0</Y>
			</Hotspot>
			<Duration>1000</Duration>
		</Frames>
	</Ui2DAnimation>
"""


def hexplate_item(idx, x, y, gold):
    anim = "A_SpinHexGold" if gold else "A_SpinHex"
    return f"""	<StaticAnimation item="IW_HexPlate{idx}">
		<ScreenID>IW_HexPlate{idx}</ScreenID>
		<RelativePosition>true</RelativePosition>
		<Location>
			<X>{x}</X>
			<Y>{y}</Y>
		</Location>
		<Size>
			<CX>{PLATE}</CX>
			<CY>{PLATE}</CY>
		</Size>
		<Animation>{anim}</Animation>
	</StaticAnimation>
"""


def set_block_location(text, item_kind, item_name, x, y, cx=None, cy=None):
    pat = re.compile(
        r'(<' + item_kind + r' item="' + re.escape(item_name) + r'">.*?<Location>\s*<X>)(-?\d+)(</X>\s*<Y>)(-?\d+)(</Y>)',
        re.S)
    text, n = pat.subn(lambda m: m.group(1) + str(x) + m.group(3) + str(y) + m.group(5), text, count=1)
    assert n == 1, f"location {item_name}"
    if cx is not None:
        pat = re.compile(
            r'(<' + item_kind + r' item="' + re.escape(item_name) + r'">.*?<Size>\s*<CX>)(\d+)(</CX>\s*<CY>)(\d+)(</CY>)',
            re.S)
        text, n = pat.subn(lambda m: m.group(1) + str(cx) + m.group(3) + str(cy) + m.group(5), text, count=1)
        assert n == 1, f"size {item_name}"
    return text


def set_block_value(text, item_kind, item_name, field, value):
    pat = re.compile(
        r'(<' + item_kind + r' item="' + re.escape(item_name) + r'">.*?<' +
        re.escape(field) + r'>)(-?\d+)(</' + re.escape(field) + r'>)', re.S)
    text, n = pat.subn(lambda m: m.group(1) + str(value) + m.group(3), text, count=1)
    assert n == 1, f"{field} {item_name}"
    return text


def main():
    s = XMLF.read_text()
    if "SPIN-DECO" in s:
        print("already restyled — nothing to do")
        return 0

    # 1) art definitions after the Schema line
    schema = re.search(r'<Schema[^>]*/>', s)
    assert schema
    s = s[:schema.end()] + "\n" + HEADER_ART + s[schema.end():]

    # 2) relocate the 23 equipment slots
    for slot_id in range(23):
        (px, py), _gold = slot_pos(slot_id)
        s = set_block_location(s, "InvSlot", f"InvSlot{slot_id}", px + 8, py + 8)

    # 3) hex plates: definitions before IW_Equipment, pieces before InvSlot0
    plates = []
    for slot_id in range(23):
        (px, py), gold = slot_pos(slot_id)
        plates.append(hexplate_item(slot_id, px, py, gold))
    anchor = '	<Screen item="IW_Equipment">'
    assert anchor in s
    s = s.replace(anchor, "".join(plates) + anchor, 1)

    piece_anchor = "\t\t<Pieces>InvSlot0</Pieces>"
    assert piece_anchor in s
    plate_pieces = "".join(f"\t\t<Pieces>IW_HexPlate{i}</Pieces>\n" for i in range(23))
    s = s.replace(piece_anchor, plate_pieces + piece_anchor, 1)

    # 4) geometry: equipment screen, crest, stat columns, page, window
    s = set_block_location(s, "Screen", "IW_Equipment", 6, 6, 573, 714)
    # ClassAnim is a client-supplied 75x142 class emblem. Its parent must keep
    # the native 85x171 viewport or EQ clips the swords/spellbook/etc.
    s = set_block_location(s, "Screen", "IW_CharacterView", 244, 0, 85, 171)
    s = set_block_location(s, "TileLayoutBox", "IW_Stats", 86, 180, 410, 290)
    s = set_block_location(s, "TileLayoutBox", "IW_Stats2", 86, 482, 410, 104)
    s = set_block_location(s, "TileLayoutBox", "IW_Stats3", 86, 592, 410, 56)
    s = set_block_location(s, "TileLayoutBox", "IW_Slots", 644, 150, 112, 280)

    # Legends exposes twelve root inventory slots (23-34). Keep every bag in
    # the dedicated rail instead of treating slots 23/24 as equipment.
    slots = re.search(r'(<TileLayoutBox item="IW_Slots">.*?</TileLayoutBox>)', s, re.S)
    assert slots, "IW_Slots"
    slot_block = slots.group(1)
    for slot_id in (23, 24):
        piece = f"\t\t<Pieces>InvSlot{slot_id}</Pieces>\n"
        if piece not in slot_block:
            slot_block = slot_block.replace(
                "\t\t<Pieces>InvSlot25</Pieces>\n",
                piece + "\t\t<Pieces>InvSlot25</Pieces>\n", 1)
    s = s[:slots.start()] + slot_block + s[slots.end():]

    # Preserve the equipment canvas while giving the identity rail 50px more
    # room for long multiclass labels and clearer gauges.
    s = set_block_value(s, "TabBox", "IW_Subwindows", "RightAnchorOffset", 165)
    for item in ("IW_Name", "IW_Level", "IW_Class", "IW_NextLevel", "IW_ExpLabel",
                 "IW_ExpPercLabel", "IW_ExpGauge", "IW_AltAdv", "IW_AltAdvPct",
                 "IW_AltAdvPctLabel", "IW_AltAdvGauge", "IW_Weight", "IW_WeightWorn"):
        kind = "Gauge" if item.endswith("Gauge") else "Label"
        s = set_block_value(s, kind, item, "LeftAnchorOffset", 158)
    s = set_block_value(s, "Button", "IW_Destroy", "LeftAnchorOffset", 145)
    s = set_block_value(s, "Button", "IW_Destroy", "TopAnchorOffset", 120)
    s = set_block_value(s, "Button", "IW_Destroy", "BottomAnchorOffset", 138)

    # page: keep Location (0,22), grow to the new tab area
    pat = re.compile(r'(<Page item="IW_InvPage">.*?<Size>\s*<CX>)388(</CX>\s*<CY>)401(</CY>)', re.S)
    s, n = pat.subn(r'\g<1>585\g<2>720\g<3>', s, count=1)
    assert n == 1, "page size"

    # window 504x495 -> 780x800
    pat = re.compile(r'(<Screen item="InventoryWindow">.*?<Size>\s*<CX>)504(</CX>\s*<CY>)495(</CY>)', re.S)
    s, n = pat.subn(r'\g<1>780\g<2>800\g<3>', s, count=1)
    assert n == 1, "window size"

    # 5) hero typography: character name in large ember type
    pat = re.compile(r'(<Label item="IW_Name">\s*<ScreenID>NameLabel</ScreenID>\s*<EQType>1</EQType>)')
    rep = r"""\1
		<Font>5</Font>
		<TextColor>
			<R>232</R>
			<G>197</G>
			<B>92</B>
		</TextColor>"""
    s, n = pat.subn(rep, s, count=1)
    assert n == 1, "IW_Name style"

    XMLF.write_text(s)
    ET.parse(XMLF)

    # reference integrity: every new piece defined, every anim/texture resolvable
    for i in range(23):
        assert f'item="IW_HexPlate{i}"' in s and f"<Pieces>IW_HexPlate{i}</Pieces>" in s
    assert 'item="A_SpinHex"' in s and 'item="A_SpinHexGold"' in s
    assert (REPO / "spinui_reloaded" / "spin_deco.tga").exists()
    print("equipment screen restyled: rails + Any pair + bag dock + hero crest, window 780x800 — parse OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
