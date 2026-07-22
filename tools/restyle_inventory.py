#!/usr/bin/env python3
"""Compact cinematic equipment screen for Spin's UI Reloaded (v3).

Migrates the v2 Narcissus composition (SPIN-DECO-2, 780x800) to the compact
v3 layout (SPIN-DECO-3, 660x700):

* Hex plates tighten from 56px/62 pitch to 46px/50 pitch.
* The left rail becomes a single 12-slot column — 8 armor slots on steel
  plates with the 4 weapon slots continuing on gold plates at its base.
* The right rail holds the 9 jewelry slots, a deliberate gap, then the two
  Any slots seated at its base — no separate bottom row.
* The center is a pure stat ledger: Vitals, Primary Attributes, Resists,
  Additional Modifiers, Mitigation, and Bind/Origin/Deity are kept in
  categorical columns with a balanced lower context anchor.
* The native class crest (85x171 viewport, 75x142 art) moves to the identity
  rail between Destroy and the bag grid, forming one character card that is
  visible on every tab and keeps its drop-to-auto-equip behavior.

All 23 InvSlot items keep their ScreenIDs/EQTypes — only geometry moves.
The 46px hex art lives in spin_deco.tga rows at y=64 (see
tools/add_spin_deco_small_hexes.py).

Idempotent: refuses to run twice (looks for the SPIN-DECO-3 marker) and
requires the v2 composition as input.
Run from repo root:  python3 tools/restyle_inventory.py
"""

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
XMLF = REPO / "spinui_reloaded" / "EQUI_InventoryWindow.xml"

# rails: EQ slot ids in top-to-bottom order
LEFT_RAIL = [2, 3, 5, 6, 8, 17, 7, 12]          # head face neck shoulder back chest arms hands
WEAPON_ROW = [13, 14, 11, 22]                   # primary secondary range ammo — left rail base, gold
RIGHT_RAIL = [1, 4, 9, 10, 20, 18, 19, 15, 16]  # ears wrists waist legs feet rings
ANY_ROW = [0, 21]                               # IS_ANY1 / IS_ANY2 — right rail base, after a gap

PITCH = 50
PLATE = 46
SLOT_INSET = 3
L_X, R_X = 4, 422
RAIL_Y = 4
ANY_GAP_ROWS = 1          # empty rail positions between jewelry and the Any pair

# retained for tools/render_equipment_preview.py compatibility
W_Y = RAIL_Y + 8 * PITCH  # first weapon plate y (left rail, position 8)
W_X0 = L_X

CANVAS = (472, 606)
PAGE = (485, 620)
PAGE_LOCATION = (5, 22)        # centers the 485px page inside the 495px tab host
WINDOW = (660, 700)
STATS1 = (58, 6, 356, 238)     # Vitals + break | attributes + Resists (15 pieces each)
STATS2 = (58, 252, 356, 94)    # Additional Modifiers | Mitigation (6 rows each)
STATS3 = (58, 436, 356, 62)    # Lower-rail anchor: Bind / Origin / Deity
CREST = (536, 142)             # window-level identity-rail crest (85x171)
BAGS = (536, 320)              # visible bag columns align with the class crest
LEDGER_HEADER_GOLD = (219, 158, 42)


def slot_pos(slot_id):
    """Return ((plate_x, plate_y), gold) for an equipment slot id."""
    if slot_id in LEFT_RAIL:
        i = LEFT_RAIL.index(slot_id)
        return (L_X, RAIL_Y + i * PITCH), False
    if slot_id in WEAPON_ROW:
        i = WEAPON_ROW.index(slot_id)
        return (L_X, RAIL_Y + (len(LEFT_RAIL) + i) * PITCH), True
    if slot_id in RIGHT_RAIL:
        i = RIGHT_RAIL.index(slot_id)
        return (R_X, RAIL_Y + i * PITCH), False
    i = ANY_ROW.index(slot_id)
    row = len(RIGHT_RAIL) + ANY_GAP_ROWS + i
    return (R_X, RAIL_Y + row * PITCH), False


SMALL_HEX_ART = """
	<!-- SPIN-DECO-3: compact 46px hex plates -->
	<Ui2DAnimation item="A_SpinHexSm">
		<Cycle>true</Cycle>
		<Frames>
			<Texture>spin_deco.tga</Texture>
			<Location>
				<X>0</X>
				<Y>64</Y>
			</Location>
			<Size>
				<CX>46</CX>
				<CY>46</CY>
			</Size>
			<Hotspot>
				<X>0</X>
				<Y>0</Y>
			</Hotspot>
			<Duration>1000</Duration>
		</Frames>
	</Ui2DAnimation>
	<Ui2DAnimation item="A_SpinHexGoldSm">
		<Cycle>true</Cycle>
		<Frames>
			<Texture>spin_deco.tga</Texture>
			<Location>
				<X>64</X>
				<Y>64</Y>
			</Location>
			<Size>
				<CX>46</CX>
				<CY>46</CY>
			</Size>
			<Hotspot>
				<X>0</X>
				<Y>0</Y>
			</Hotspot>
			<Duration>1000</Duration>
		</Frames>
	</Ui2DAnimation>
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


def set_block_field(text, item_kind, item_name, field, value):
    pat = re.compile(
        r'(<' + item_kind + r' item="' + re.escape(item_name) + r'">.*?<' +
        re.escape(field) + r'>)([^<]*)(</' + re.escape(field) + r'>)', re.S)
    text, n = pat.subn(lambda m: m.group(1) + str(value) + m.group(3), text, count=1)
    assert n == 1, f"{field} {item_name}"
    return text


def set_label_style(text, item_name, font, color):
    """Set an inventory heading's explicit font and RGB without touching peers."""
    pat = re.compile(
        r'(<Label item="' + re.escape(item_name) + r'">)(.*?)(</Label>)',
        re.S)

    def update(match):
        body = match.group(2)
        font_pat = re.compile(r'<Font>\d+</Font>')
        if font_pat.search(body):
            body = font_pat.sub(f'<Font>{font}</Font>', body, count=1)
        else:
            body = f'\n\t\t<Font>{font}</Font>' + body
        size_pat = re.compile(r'(<Size>\s*<CX>)\d+(</CX>)', re.S)
        body, resized = size_pat.subn(r'\g<1>175\g<2>', body, count=1)
        assert resized == 1, f"Size {item_name}"
        color_pat = re.compile(
            r'(<TextColor>\s*<R>)\d+(</R>\s*<G>)\d+(</G>\s*<B>)\d+(</B>\s*</TextColor>)',
            re.S)
        body, changed = color_pat.subn(
            lambda color_match: (
                color_match.group(1) + str(color[0])
                + color_match.group(2) + str(color[1])
                + color_match.group(3) + str(color[2])
                + color_match.group(4)
            ),
            body,
            count=1,
        )
        assert changed == 1, f"TextColor {item_name}"
        return match.group(1) + body + match.group(3)

    text, n = pat.subn(update, text, count=1)
    assert n == 1, f"Label {item_name}"
    return text


def organize_stat_ledger(text):
    """Activate the semantic headings and make both tile flows deterministic."""
    # The stock definitions ship commented out.  Keep the 8px Vitals spacer
    # as the explicit column break, and expose only the Resists heading from
    # the second commented group (its own spacer is not needed).
    pat = re.compile(
        r'<!--\s*(<Screen item="IWS_StatsSpacerScreen">.*?</Screen>)\s*-->',
        re.S)
    text, n = pat.subn(r'\1', text, count=1)
    assert n == 1, "IWS_StatsSpacerScreen definition"

    pat = re.compile(
        r'(<!--\s*<Screen item="IWS_ResistsSpacerScreen">.*?</Screen>)\s*'
        r'(<Label item="IWS_ResistsLabel">.*?</Label>\s*'
        r'<Screen item="IWS_ResistsLabelScreen">.*?</Screen>)\s*-->',
        re.S)
    text, n = pat.subn(r'\1 -->\n\t\2', text, count=1)
    assert n == 1, "IWS_ResistsLabel definition"

    pat = re.compile(
        r'<!--\s*<Pieces>Screen:IWS_StatsSpacerScreen</Pieces>\s*-->')
    text, n = pat.subn(
        '<!-- Deliberate column break: Vitals left, attributes/resists right. -->\n'
        '\t\t<Pieces>Screen:IWS_StatsSpacerScreen</Pieces>', text, count=1)
    assert n == 1, "IWS_StatsSpacerScreen piece"

    pat = re.compile(
        r'<!--\s*<Pieces>Screen:IWS_ResistsSpacerScreen</Pieces>\s*'
        r'<Pieces>Screen:IWS_ResistsLabelScreen</Pieces>\s*-->')
    text, n = pat.subn(
        '<Pieces>Screen:IWS_ResistsLabelScreen</Pieces>', text, count=1)
    assert n == 1, "IWS_ResistsLabelScreen piece"

    old_attributes = """\
\t\t<Pieces>Screen:IWS_StrengthScreen</Pieces>
\t\t<Pieces>Screen:IWS_StaminaScreen</Pieces>
\t\t<Pieces>Screen:IWS_IntelligenceScreen</Pieces>
\t\t<Pieces>Screen:IWS_WisdomScreen</Pieces>
\t\t<Pieces>Screen:IWS_AgilityScreen</Pieces>
\t\t<Pieces>Screen:IWS_DexterityScreen</Pieces>
\t\t<Pieces>Screen:IWS_CharismaScreen</Pieces>"""
    new_attributes = """\
\t\t<Pieces>Screen:IWS_StrengthScreen</Pieces>
\t\t<Pieces>Screen:IWS_StaminaScreen</Pieces>
\t\t<Pieces>Screen:IWS_AgilityScreen</Pieces>
\t\t<Pieces>Screen:IWS_DexterityScreen</Pieces>
\t\t<Pieces>Screen:IWS_WisdomScreen</Pieces>
\t\t<Pieces>Screen:IWS_IntelligenceScreen</Pieces>
\t\t<Pieces>Screen:IWS_CharismaScreen</Pieces>"""
    assert text.count(old_attributes) == 1, "primary attribute order"
    text = text.replace(old_attributes, new_attributes, 1)

    old_piece = "\t\t<Pieces>Screen:IWS_ModsSpacerScreen</Pieces>"
    new_piece = "\t\t<Pieces>Screen:IWS_ModsLabelScreen</Pieces>"
    assert text.count(old_piece) == 1, "Mitigation column heading"
    text = text.replace(old_piece, new_piece, 1)
    text = set_block_field(
        text, "Label", "IWS_StatsLabel", "Text", "Primary Attributes")
    text = set_block_field(
        text, "Label", "IWS_ModsLabel", "Text", "Mitigation")
    for heading in (
            "IWS_VitalsLabel", "IWS_StatsLabel", "IWS_ResistsLabel",
            "IWS_HeroicModsLabel", "IWS_ModsLabel", "IWS_AdditionalLabel"):
        text = set_label_style(text, heading, 3, LEDGER_HEADER_GOLD)
    return text


def main():
    s = XMLF.read_text()
    if "SPIN-DECO-3" in s:
        print("already at v3 — nothing to do")
        return 0
    assert "SPIN-DECO-2" in s, "expects the v2 composition (run history: restyle v2)"

    # 1) 46px hex art after the existing gold hex animation
    anchor = re.search(r'<Ui2DAnimation item="A_SpinHexGold">.*?</Ui2DAnimation>\n', s, re.S)
    assert anchor, "A_SpinHexGold animation"
    s = s[:anchor.end()] + SMALL_HEX_ART + s[anchor.end():]

    # 2) relocate the 23 equipment slots and retune their hex plates
    for slot_id in range(23):
        (px, py), gold = slot_pos(slot_id)
        s = set_block_location(s, "InvSlot", f"InvSlot{slot_id}",
                               px + SLOT_INSET, py + SLOT_INSET)
        s = set_block_location(s, "StaticAnimation", f"IW_HexPlate{slot_id}",
                               px, py, PLATE, PLATE)
        s = set_block_field(s, "StaticAnimation", f"IW_HexPlate{slot_id}",
                            "Animation", "A_SpinHexGoldSm" if gold else "A_SpinHexSm")

    # 3) equipment canvas, stat ledger, page, window
    s = set_block_location(s, "Screen", "IW_Equipment", 6, 6, *CANVAS)
    s = set_block_location(s, "TileLayoutBox", "IW_Stats", *STATS1)
    s = set_block_location(s, "TileLayoutBox", "IW_Stats2", *STATS2)
    s = set_block_location(s, "TileLayoutBox", "IW_Stats3", *STATS3)
    s = organize_stat_ledger(s)
    s = set_block_location(
        s, "Page", "IW_InvPage", *PAGE_LOCATION, *PAGE)

    pat = re.compile(r'(<Screen item="InventoryWindow">.*?<Size>\s*<CX>)780(</CX>\s*<CY>)800(</CY>)', re.S)
    s, n = pat.subn(rf'\g<1>{WINDOW[0]}\g<2>{WINDOW[1]}\g<3>', s, count=1)
    assert n == 1, "window size"

    # 4) the class crest joins the identity rail (window level, every tab),
    #    keeping its 85x171 viewport and drop-to-auto-equip behavior
    piece = "\t\t<Pieces>Screen:IW_CharacterView</Pieces>\n"
    equip = re.search(r'<Screen item="IW_Equipment">.*?\n\t</Screen>', s, re.S)
    assert equip and piece in equip.group(0), "crest piece inside IW_Equipment"
    block = equip.group(0).replace(piece, "", 1)
    s = s[:equip.start()] + block + s[equip.end():]
    rail_anchor = "\t\t<Pieces>TileLayoutBox:IW_Slots</Pieces>\n"
    assert rail_anchor in s, "bag rail piece"
    s = s.replace(rail_anchor, rail_anchor + piece, 1)
    s = set_block_location(s, "Screen", "IW_CharacterView", *CREST)

    # 5) bag grid drops below the crest on the identity rail
    s = set_block_location(s, "TileLayoutBox", "IW_Slots", *BAGS)

    XMLF.write_text(s)
    ET.parse(XMLF)

    # reference integrity
    for i in range(23):
        assert f'item="IW_HexPlate{i}"' in s and f"<Pieces>IW_HexPlate{i}</Pieces>" in s
    assert 'item="A_SpinHexSm"' in s and 'item="A_SpinHexGoldSm"' in s
    assert (REPO / "spinui_reloaded" / "spin_deco.tga").exists()
    print("equipment screen compacted: 12-slot rails + stat ledger + rail crest, "
          f"window {WINDOW[0]}x{WINDOW[1]} — parse OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
