#!/usr/bin/env python3
"""Narcissus-inspired equipment screen for Spin's UI Reloaded.

Rebuilds the Inventory window's Equipment tab as a cinematic composition:
two vertical slot rails (armor left, jewelry right) on floating hex plates,
weapons on a gold-edged center row, the class crest as the centerpiece, and
the stat columns flowing between the rails.  The window grows to 720x800.

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
LEFT_RAIL = [2, 3, 5, 6, 8, 17, 7, 12, 0]      # head face neck shoulder back chest arms hands charm
RIGHT_RAIL = [1, 4, 9, 10, 20, 18, 19, 15, 16]  # ears wrists waist legs feet rings
WEAPON_ROW = [13, 14, 11, 22, 21]               # primary secondary range ammo power

PITCH = 62
PLATE = 56
L_X, R_X = 4, 513
RAIL_Y = 4
W_Y = 584
W_X0 = 134


def slot_pos(slot_id):
    if slot_id in LEFT_RAIL:
        i = LEFT_RAIL.index(slot_id)
        return (L_X, RAIL_Y + i * PITCH), False
    if slot_id in RIGHT_RAIL:
        i = RIGHT_RAIL.index(slot_id)
        return (R_X, RAIL_Y + i * PITCH), False
    i = WEAPON_ROW.index(slot_id)
    return (W_X0 + i * PITCH, W_Y), True


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
    s = set_block_location(s, "Screen", "IW_Equipment", 6, 6, 573, 644)
    s = set_block_location(s, "Screen", "IW_CharacterView", 248, 10, 76, 76)
    s = set_block_location(s, "TileLayoutBox", "IW_Stats", 86, 110, 410, 290)
    s = set_block_location(s, "TileLayoutBox", "IW_Stats2", 86, 412, 410, 104)
    s = set_block_location(s, "TileLayoutBox", "IW_Stats3", 86, 522, 410, 56)

    # page: keep Location (0,22), grow to the new tab area
    pat = re.compile(r'(<Page item="IW_InvPage">.*?<Size>\s*<CX>)388(</CX>\s*<CY>)401(</CY>)', re.S)
    s, n = pat.subn(r'\g<1>585\g<2>720\g<3>', s, count=1)
    assert n == 1, "page size"

    # window 504x495 -> 720x800
    pat = re.compile(r'(<Screen item="InventoryWindow">.*?<Size>\s*<CX>)504(</CX>\s*<CY>)495(</CY>)', re.S)
    s, n = pat.subn(r'\g<1>720\g<2>800\g<3>', s, count=1)
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
    print("equipment screen restyled: rails + hex plates + hero crest, window 720x800 — parse OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
