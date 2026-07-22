#!/usr/bin/env python3
"""Compact cinematic equipment screen for Spin's UI Reloaded (v4).

Migrates the earlier Narcissus composition to the compact v4 layout
(SPIN-DECO-4, 660x668):

* Native 40px Legends equipment wells replace the decorative hex underlays.
* The vertical rails hold 8 armor slots on the left and 9 jewelry slots on
  the right.
* Primary, Secondary, Range, Ammo, and the separated Any pair form one
  centered horizontal footer rail beneath Additional Information.
* The center is a pure stat ledger: Vitals, Primary Attributes, Resists,
  Additional Modifiers, Mitigation, and Bind/Origin/Deity are kept in
  categorical columns with a balanced lower context anchor.
* The native class crest (85x171 viewport, 75x142 art) moves to the identity
  rail between Destroy and the bag grid, forming one character card that is
  visible on every tab and keeps its drop-to-auto-equip behavior.

All 23 InvSlot items keep their ScreenIDs/EQTypes — only geometry moves.
Their native backgrounds remain present so Legends can draw dynamic
unusable/no-bonus state after a loadout change.

Idempotent: an existing SPIN-DECO-4 marker is a clean no-op. The final polish
also upgrades an already-generated SPIN-DECO-3 file in place.
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
SLOT_SIZE = 40
# Migration-only geometry used while converting a historical v2 file. The
# v4 output removes these plates entirely.
LEGACY_PLATE = 46
LEGACY_SLOT_INSET = 3
L_X, R_X = 7, 419
RAIL_Y = 7
BAG_SLOT_SIZE = 40
BAG_SPACING = 3
BAG_GRID_WIDTH = 2 * BAG_SLOT_SIZE + BAG_SPACING
CREST_SIZE = (85, 171)
FOOTER_Y = 447
FOOTER_SLOT_X = {
    13: 63, 14: 121, 11: 179, 22: 237,
    0: 311, 21: 369,
}

# retained for tools/render_equipment_preview.py compatibility
W_Y = FOOTER_Y
W_X0 = FOOTER_SLOT_X[WEAPON_ROW[0]]

CANVAS = (472, 496)
PAGE = (485, 620)
PAGE_LOCATION = (5, 22)        # centers the 485px page inside the 495px tab host
WINDOW = (660, 668)
STATS1 = (60, 6, 356, 238)     # Vitals + break | attributes + Resists (15 pieces each)
STATS2 = (60, 252, 356, 94)    # Additional Modifiers | Mitigation (6 rows each)
STATS3 = (60, 354, 356, 62)    # Bind / Origin / Deity directly above the footer rail
CREST = (536, 142)             # window-level identity-rail crest (85x171)
BAGS = (537, 320)              # visible 83px bag grid centers under the 85px crest
BAG_BOX = (112, 256)           # exact 2x6 grid height plus one safety pixel
IDENTITY_LABEL_GEOMETRY = {
    # item: (font, left, right, top, bottom)
    "IW_Name": (4, 162, 4, 0, 20),
    "IW_Level": (3, 162, 130, 20, 34),
    "IW_Class": (3, 128, 4, 20, 34),
}
LEDGER_HEADER_GOLD = (219, 158, 42)


def slot_pos(slot_id):
    """Return ((slot_x, slot_y), weapon_group) for an equipment slot id."""
    if slot_id in FOOTER_SLOT_X:
        return (FOOTER_SLOT_X[slot_id], FOOTER_Y), slot_id in WEAPON_ROW
    if slot_id in LEFT_RAIL:
        i = LEFT_RAIL.index(slot_id)
        return (L_X, RAIL_Y + i * PITCH), False
    if slot_id in RIGHT_RAIL:
        i = RIGHT_RAIL.index(slot_id)
        return (R_X, RAIL_Y + i * PITCH), False
    raise ValueError(f"unknown equipment slot id: {slot_id}")


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


def set_block_size(text, item_kind, item_name, cx, cy):
    """Set or add an explicit size without requiring the block to have a location."""
    pat = re.compile(
        r'(<' + item_kind + r' item="' + re.escape(item_name) + r'">)(.*?)(</' + item_kind + r'>)',
        re.S)

    def update(match):
        body = match.group(2)
        size_pat = re.compile(
            r'(<Size>\s*<CX>)\d+(</CX>\s*<CY>)\d+(</CY>\s*</Size>)',
            re.S)
        if size_pat.search(body):
            body = size_pat.sub(
                lambda size: (
                    size.group(1) + str(cx) + size.group(2)
                    + str(cy) + size.group(3)
                ),
                body,
                count=1,
            )
        else:
            anchor = re.compile(r'(<RelativePosition>true</RelativePosition>)')
            size_xml = (
                r'\1\n\t\t<Size>\n'
                f'\t\t\t<CX>{cx}</CX>\n'
                f'\t\t\t<CY>{cy}</CY>\n'
                '\t\t</Size>'
            )
            body, inserted = anchor.subn(size_xml, body, count=1)
            assert inserted == 1, f"size anchor {item_name}"
        return match.group(1) + body + match.group(3)

    text, n = pat.subn(update, text, count=1)
    assert n == 1, f"size {item_name}"
    return text


def set_block_field(text, item_kind, item_name, field, value):
    pat = re.compile(
        r'(<' + item_kind + r' item="' + re.escape(item_name) + r'">.*?<' +
        re.escape(field) + r'>)([^<]*)(</' + re.escape(field) + r'>)', re.S)
    text, n = pat.subn(lambda m: m.group(1) + str(value) + m.group(3), text, count=1)
    assert n == 1, f"{field} {item_name}"
    return text


def set_or_add_block_field(
        text, item_kind, item_name, field, value, before_field):
    """Set a field, inserting it at a schema-safe anchor when absent."""
    pat = re.compile(
        r'(<' + item_kind + r' item="' + re.escape(item_name) + r'">)(.*?)(</' + item_kind + r'>)',
        re.S)

    def update(match):
        body = match.group(2)
        field_pat = re.compile(
            r'(<' + re.escape(field) + r'>)[^<]*(</' + re.escape(field) + r'>)')
        if field_pat.search(body):
            body = field_pat.sub(
                lambda found: found.group(1) + str(value) + found.group(2),
                body,
                count=1,
            )
        else:
            anchor = f"<{before_field}>"
            assert body.count(anchor) == 1, f"{before_field} anchor {item_name}"
            body = body.replace(
                anchor,
                f"<{field}>{value}</{field}>\n\t\t{anchor}",
                1,
            )
        return match.group(1) + body + match.group(3)

    text, n = pat.subn(update, text, count=1)
    assert n == 1, f"{field} {item_name}"
    return text


def set_or_add_block_field_after(
        text, item_kind, item_name, field, value, after_field):
    """Set a field, inserting it after its schema predecessor when absent."""
    pattern = re.compile(
        r'(<' + item_kind + r' item="' + re.escape(item_name)
        + r'">)(.*?)(</' + item_kind + r'>)',
        re.S,
    )

    def update(match):
        body = match.group(2)
        field_pattern = re.compile(
            r'(<' + re.escape(field) + r'>)[^<]*(</'
            + re.escape(field) + r'>)')
        if field_pattern.search(body):
            body = field_pattern.sub(
                lambda found: found.group(1) + str(value) + found.group(2),
                body,
                count=1,
            )
        else:
            anchor = f"</{after_field}>"
            assert body.count(anchor) == 1, f"{after_field} anchor {item_name}"
            body = body.replace(
                anchor,
                anchor + f"\n\t\t<{field}>{value}</{field}>",
                1,
            )
        return match.group(1) + body + match.group(3)

    text, count = pattern.subn(update, text, count=1)
    assert count == 1, f"{field} {item_name}"
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


def remove_item_block(text, kind, item_name):
    """Remove one complete top-level SIDL item definition."""
    pattern = re.compile(
        r'\n?\t<' + kind + r' item="' + re.escape(item_name)
        + r'">.*?</' + kind + r'>[ \t]*(?:\r?\n)?',
        re.S,
    )
    text, count = pattern.subn("\n", text, count=1)
    assert count == 1, f"remove {kind} {item_name}"
    return text


def remove_piece(text, item_name):
    pattern = re.compile(
        r'^[ \t]*<Pieces>' + re.escape(item_name)
        + r'</Pieces>[ \t]*(?:\r?\n)?',
        re.M,
    )
    text, count = pattern.subn("", text, count=1)
    assert count == 1, f"remove Pieces {item_name}"
    return text


def finalize_v4(text):
    """Upgrade the compact v3 result to the native-slot Legends polish."""
    assert "SPIN-DECO-3" in text, "expects compact v3 equipment input"
    text = text.replace(
        "<!-- SPIN-DECO-3: compact 46px hex plates -->",
        "<!-- SPIN-DECO-4: native Legends runtime item-state bindings -->",
        1,
    )

    # The decorative hexes were purely static underlays.  Native InvSlot
    # controls remain untouched functionally so the client owns every visual
    # usable/unusable transition after a loadout swap.
    for slot_id in range(23):
        text = remove_item_block(
            text, "StaticAnimation", f"IW_HexPlate{slot_id}")
        text = remove_piece(text, f"IW_HexPlate{slot_id}")
        (slot_x, slot_y), _ = slot_pos(slot_id)
        text = set_block_location(
            text, "InvSlot", f"InvSlot{slot_id}", slot_x, slot_y,
            SLOT_SIZE, SLOT_SIZE,
        )

    text = set_block_location(text, "Screen", "IW_Equipment", 6, 6, *CANVAS)
    text = set_block_location(text, "TileLayoutBox", "IW_Stats", *STATS1)
    text = set_block_location(text, "TileLayoutBox", "IW_Stats2", *STATS2)
    text = set_block_location(text, "TileLayoutBox", "IW_Stats3", *STATS3)

    # Give the identity stack the same 20px name height as current Legends,
    # then move the gauges/weight rhythm down four pixels without approaching
    # the Destroy control.
    for label_name, (font, left, right, top, bottom) in IDENTITY_LABEL_GEOMETRY.items():
        text = set_or_add_block_field(
            text, "Label", label_name, "Font", font, "RelativePosition")
        for field, value in (
                ("LeftAnchorOffset", left), ("RightAnchorOffset", right),
                ("TopAnchorOffset", top), ("BottomAnchorOffset", bottom)):
            text = set_block_field(text, "Label", label_name, field, value)
    text = set_or_add_block_field_after(
        text, "Label", "IW_Name", "AlignVCenter", "true", "AlignRight")

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
    for item_name, (top, bottom) in identity_flow.items():
        kind = "Gauge" if item_name.endswith("Gauge") else "Label"
        text = set_block_field(text, kind, item_name, "TopAnchorOffset", top)
        text = set_block_field(text, kind, item_name, "BottomAnchorOffset", bottom)

    # Keep the three context rows at 347px while guaranteeing that the full
    # word "Origin" has room at the in-game font metrics.
    for key_name, value_name in (
            ("IWS_Bind", "IWS_BindZone"),
            ("IWS_Origin", "IWS_OriginZone"),
            ("IWS_Deity", "IWS_DeityName")):
        text = set_block_location(text, "Label", key_name, 0, 0, 44, 14)
        text = set_block_location(text, "Label", value_name, 44, 0, 303, 14)

    return text


def main():
    s = XMLF.read_text()
    if "SPIN-DECO-4" in s:
        print("already at v4 - nothing to do")
        return 0
    if "SPIN-DECO-3" in s:
        s = finalize_v4(s)
        XMLF.write_text(s)
        ET.parse(XMLF)
        print("equipment screen finalized: native Legends slots + centered "
              "footer + unclipped identity rail - parse OK")
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
                               px + LEGACY_SLOT_INSET, py + LEGACY_SLOT_INSET)
        s = set_block_location(s, "StaticAnimation", f"IW_HexPlate{slot_id}",
                               px, py, LEGACY_PLATE, LEGACY_PLATE)
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
    # InvSlot23 is FirstPieceTemplate: without this explicit size, all twelve
    # inherited bag slots collapse to zero/undefined dimensions in the client.
    s = set_block_size(
        s, "InvSlot", "InvSlot23", BAG_SLOT_SIZE, BAG_SLOT_SIZE)
    s = set_block_location(s, "TileLayoutBox", "IW_Slots", *BAGS, *BAG_BOX)

    # Give the identity header its full rail width, a readable name font, and
    # separate non-overlapping level/class cells.
    for label_name, (font, left, right, top, bottom) in IDENTITY_LABEL_GEOMETRY.items():
        s = set_or_add_block_field(
            s, "Label", label_name, "Font", font, "RelativePosition")
        for field, value in (
                ("LeftAnchorOffset", left), ("RightAnchorOffset", right),
                ("TopAnchorOffset", top), ("BottomAnchorOffset", bottom)):
            s = set_block_field(s, "Label", label_name, field, value)
    s = set_block_field(s, "Label", "IW_Name", "AlignCenter", "true")
    s = set_block_field(s, "Label", "IW_Name", "AlignRight", "false")
    s = set_block_field(s, "Label", "IW_Level", "AlignRight", "false")
    s = set_block_field(s, "Label", "IW_Class", "AlignRight", "true")
    s = set_or_add_block_field(
        s, "Label", "IW_Name", "FontShadow", "true", "NoWrap")

    s = finalize_v4(s)

    XMLF.write_text(s)
    ET.parse(XMLF)

    # reference integrity
    for i in range(23):
        assert f'item="IW_HexPlate{i}"' not in s
        assert f"<ScreenID>InvSlot{i}</ScreenID>" in s
        assert f"<EQType>inventory/Equip {i}</EQType>" in s
    print("equipment screen compacted: native slots + stat ledger + rail crest, "
          f"window {WINDOW[0]}x{WINDOW[1]} — parse OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
