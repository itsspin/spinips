#!/usr/bin/env python3
"""Render a mock of the compact v3 Equipment screen at its real geometry.

Outputs docs/previews/equipment_page.png (2x scale for detail review).
Run from repo root:  python3 tools/render_equipment_preview.py
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
from restyle_inventory import (ANY_ROW, BAGS, BAG_SLOT_SIZE, BAG_SPACING,  # noqa: E402
                               CREST, CREST_SIZE, LEFT_RAIL, PAGE_LOCATION,
                               PLATE, RIGHT_RAIL, SLOT_INSET, STATS1, STATS2,
                               STATS3, WEAPON_ROW, WINDOW, slot_pos)
from spinui_theme import (BG1, BG2, CYAN, GOLD, GOLD_BRIGHT, LINE, LINE_SOFT,
                          TEXT, TEXT_DIM)

SKIN = REPO / "spinui_reloaded"
OUT = REPO / "docs" / "previews"

DIM = TEXT_DIM

def F(size, bold=False):
    names = (("seguisb.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf") if bold
             else ("segoeui.ttf", "DejaVuSans.ttf"))
    for root in (Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/dejavu")):
        for name in names:
            path = root / name
            if path.exists():
                return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


SLOT_NAMES = {0: "Any", 1: "Ear", 2: "Head", 3: "Face", 4: "Ear", 5: "Neck",
              6: "Shoulders", 7: "Arms", 8: "Back", 9: "Wrist", 10: "Wrist",
              11: "Range", 12: "Hands", 13: "Primary", 14: "Secondary",
              15: "Ring", 16: "Ring", 17: "Chest", 18: "Legs", 19: "Feet",
              20: "Waist", 21: "Any", 22: "Ammo"}
SLOT_ABBR = {0: "ANY", 1: "EAR", 2: "HEAD", 3: "FACE", 4: "EAR", 5: "NECK",
             6: "SHLD", 7: "ARMS", 8: "BACK", 9: "WRST", 10: "WRST",
             11: "RNG", 12: "HAND", 13: "PRI", 14: "SEC", 15: "RING",
             16: "RING", 17: "CHST", 18: "LEGS", 19: "FEET", 20: "WAIST",
             21: "ANY", 22: "AMMO"}
FILLED = {2: (96, 60, 140), 5: (60, 96, 140), 6: (140, 90, 50), 8: (60, 120, 80),
          17: (150, 60, 60), 7: (90, 70, 130), 12: (50, 110, 120), 1: (130, 90, 50),
          4: (80, 80, 120), 9: (120, 60, 60), 10: (150, 120, 50), 20: (96, 60, 40),
          18: (60, 96, 140), 19: (140, 60, 96), 13: (170, 140, 60), 14: (110, 110, 130),
          11: (60, 140, 130), 16: (140, 100, 160)}


def ledger(d, ox, oy, box, rows):
    """Flow (label, value) rows through a tile box exactly like the client."""
    bx, by, bw, bh = box
    capacity = (bh + 2) // 16
    for i, (lab, val) in enumerate(rows):
        col, row = divmod(i, capacity)
        x = ox + bx + col * 181
        y = oy + by + row * 16
        if val is None:
            d.text((x, y), lab.upper(), font=F(9, True), fill=GOLD)
            d.line([(x, y + 13), (x + 175, y + 13)], fill=GOLD + (110,))
        else:
            d.text((x, y + 1), lab, font=F(9), fill=DIM)
            d.text((x + 175, y + 1), val, font=F(9, True), fill=TEXT, anchor="ra")


def main():
    W, H = WINDOW
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # window: rounded obsidian glass + titlebar
    d.rounded_rectangle([0, 0, W - 1, H - 1], radius=5, fill=(9, 13, 18, 250), outline=LINE + (255,))
    d.rectangle([1, 1, W - 2, 18], fill=(16, 22, 29, 255))
    d.line([(1, 1), (W - 2, 1)], fill=CYAN + (225,))
    d.line([(1, 17), (W - 2, 17)], fill=GOLD + (220,))
    d.text((10, 9), "Inventory", font=F(11, True), fill=TEXT, anchor="lm")
    d.text((W - 12, 9), "—   ✕", font=F(11, True), fill=DIM, anchor="rm")

    # tab strip
    tabs = ["Equipment", "Pet", "Loadouts", "Storage"]
    tx = 8
    for i, t in enumerate(tabs):
        tw = 24 + 8 * len(t)
        active = i == 0
        d.rounded_rectangle([tx, 24, tx + tw, 44], radius=4,
                            fill=(23, 34, 42, 255) if active else BG1 + (255,),
                            outline=(CYAN if active else LINE_SOFT) + (255,))
        if active:
            d.line([(tx + 4, 43), (tx + tw - 4, 43)], fill=CYAN + (255,), width=2)
        d.text((tx + tw // 2, 34), t, font=F(11, active), fill=TEXT if active else DIM, anchor="mm")
        tx += tw + 4

    # ---- identity rail: name, class, gauges, Destroy, crest, bags, buttons --
    sx = W - 165
    d.line([(sx - 4, 24), (sx - 4, H - 30)], fill=LINE_SOFT + (255,))
    d.text((sx + 82, 33), "Spin", font=F(15, True), fill=GOLD_BRIGHT, anchor="mm")
    d.text((sx + 4, 51), "39", font=F(9, True), fill=TEXT, anchor="lm")
    d.text((sx + 160, 51), "WAR/DRU/BRD", font=F(9, True), fill=TEXT, anchor="rm")
    for lab, pct, col, y in (("NEXT LEVEL", 0.06, GOLD, 61), ("NEXT AA", 0.17, CYAN, 83)):
        d.text((sx + 4, y), lab, font=F(8), fill=DIM)
        d.rectangle([sx + 4, y + 11, sx + 160, y + 19], fill=(8, 10, 14, 255), outline=LINE_SOFT + (255,))
        d.rectangle([sx + 4, y + 11, sx + 4 + int(156 * pct), y + 19], fill=col + (255,))
        d.text((sx + 160, y), f"{int(pct*100)}%", font=F(8), fill=TEXT, anchor="ra")
    d.text((sx + 4, 105), "WEIGHT", font=F(7), fill=DIM)
    d.text((sx + 160, 105), "85 / 172  ·  WORN 38", font=F(7), fill=TEXT, anchor="ra")

    d.rounded_rectangle([sx + 15, 120, sx + 150, 138], radius=3, fill=(120, 30, 30, 255), outline=LINE + (255,))
    d.text((sx + 82, 129), "Destroy", font=F(10, True), fill=TEXT, anchor="mm")

    # Native-proportion live class emblem on the rail. EQ supplies this artwork
    # at runtime; crossed blades represent the Warrior variant in this preview.
    cx, cy = CREST
    d.rounded_rectangle([cx, cy, cx + CREST_SIZE[0], cy + CREST_SIZE[1]], radius=4,
                        fill=(7, 11, 15, 255), outline=GOLD + (220,))
    d.rounded_rectangle([cx + 5, cy + 7, cx + 80, cy + 153], radius=3,
                        fill=(12, 18, 24, 255), outline=LINE_SOFT + (255,))
    d.polygon([(cx + 42, cy + 28), (cx + 67, cy + 45), (cx + 62, cy + 97),
               (cx + 42, cy + 120), (cx + 22, cy + 97), (cx + 17, cy + 45)],
              fill=(31, 43, 53, 255), outline=GOLD + (190,))
    d.line([(cx + 23, cy + 112), (cx + 64, cy + 42)], fill=(190, 205, 211, 255), width=7)
    d.line([(cx + 62, cy + 112), (cx + 21, cy + 42)], fill=(190, 205, 211, 255), width=7)
    d.line([(cx + 16, cy + 91), (cx + 35, cy + 102)], fill=GOLD_BRIGHT + (255,), width=4)
    d.line([(cx + 69, cy + 91), (cx + 50, cy + 102)], fill=GOLD_BRIGHT + (255,), width=4)
    d.text((cx + 42, cy + 160), "WARRIOR", font=F(7, True), fill=GOLD_BRIGHT, anchor="mm")

    # twelve bag slots, two clean columns (the IW_Slots tile box)
    slots_img = Image.open(SKIN / "window_pieces01.tga").convert("RGBA").crop((180, 110, 221, 151))
    bagcols = [(150, 110, 60), (96, 70, 40), None, (120, 90, 60), (140, 100, 50),
               None, (110, 80, 45), (150, 110, 60), None, (100, 75, 50),
               (88, 110, 120), None]
    for i in range(12):
        bx = BAGS[0] + (i % 2) * (BAG_SLOT_SIZE + BAG_SPACING)
        by = BAGS[1] + (i // 2) * (BAG_SLOT_SIZE + BAG_SPACING)
        img.alpha_composite(slots_img.resize((BAG_SLOT_SIZE, BAG_SLOT_SIZE)), (bx, by))
        if bagcols[i]:
            d.polygon([(bx + 20, by + 7), (bx + 31, by + 15), (bx + 31, by + 28),
                       (bx + 20, by + 34), (bx + 9, by + 28), (bx + 9, by + 15)],
                      fill=bagcols[i] + (255,))
            d.ellipse([bx + 14, by + 7, bx + 26, by + 15], fill=(70, 50, 30, 255))

    # buttons — the full stock set, bottom-anchored like the client draws them
    d.rounded_rectangle([sx + 4, H - 80, sx + 160, H - 62], radius=3, fill=BG2 + (255,), outline=LINE + (255,))
    d.text((sx + 82, H - 71), "Appear.", font=F(9), fill=TEXT, anchor="mm")
    for i, lab in enumerate(("Skills", "Alt. Adv.", "Achiev.", "Find Item")):
        bx = sx + 4 + (i % 2) * 80
        by = H - 60 + (i // 2) * 20
        d.rounded_rectangle([bx, by, bx + 76, by + 16], radius=3, fill=BG2 + (255,), outline=LINE + (255,))
        d.text((bx + 38, by + 8), lab, font=F(8), fill=TEXT, anchor="mm")
    d.rounded_rectangle([sx + 44, H - 20, sx + 160, H - 4], radius=3, fill=BG2 + (255,), outline=GOLD + (255,))
    d.text((sx + 102, H - 12), "Done", font=F(9, True), fill=GOLD_BRIGHT, anchor="mm")

    # money row under the page, bottom-left as the client anchors it
    for i, (c, amt) in enumerate((((222, 188, 96), "4,087"), ((218, 165, 32), "4,699"),
                                  ((192, 192, 200), "4,256"), ((184, 115, 51), "1,853"))):
        bx = 10 + i * 96
        y = H - 22
        d.ellipse([bx, y, bx + 14, y + 14], fill=c + (255,), outline=(0, 0, 0, 180))
        d.text((bx + 19, y + 2), amt, font=F(9), fill=TEXT)

    # ---- centered equipment page (5,22)+(6,6) -> content at (13, 50) ----
    ox, oy = 8 + PAGE_LOCATION[0], 50
    hexes = Image.open(SKIN / "spin_deco.tga").convert("RGBA")
    hex_steel = hexes.crop((0, 64, 46, 110))
    hex_gold = hexes.crop((64, 64, 110, 110))

    for slot_id in range(23):
        (px, py), gold = slot_pos(slot_id)
        plate = hex_gold if gold else hex_steel
        img.alpha_composite(plate, (ox + px, oy + py))
        inner = (ox + px + SLOT_INSET, oy + py + SLOT_INSET)
        if slot_id in FILLED:
            c = FILLED[slot_id]
            d.rounded_rectangle([inner[0] + 4, inner[1] + 4, inner[0] + 36, inner[1] + 36],
                                radius=3, fill=c + (255,))
            d.rectangle([inner[0] + 4, inner[1] + 4, inner[0] + 36, inner[1] + 11],
                        fill=(255, 255, 255, 26))
        else:
            d.text((ox + px + PLATE // 2, oy + py + PLATE // 2), SLOT_ABBR[slot_id],
                   font=F(7), fill=DIM, anchor="mm")

    # ---- the stat ledger between the rails ----
    # Mirror only active XML pieces.  The blank fifteenth item is the 8px
    # column-break screen that keeps all Vitals left and all attributes/
    # resists right in the client's vertical-first TileLayoutBox.
    vitals = [("Character Vitals", None), ("HP", "3,025 / 3,025"), ("Mana", "1,622 / 1,622"),
              ("End", "2,212 / 2,212"), ("AC", "337/388 | 351"), ("Attack", "296 | 511"),
              ("Attack Speed %", "113"), ("Velocity", "0"), ("HP Regen", "112"),
              ("Mana Regen", "20"), ("End Regen", "36"), ("Primary DPS", "184.2"),
              ("Secondary DPS", "62.4"), ("Ranged DPS", "96.0"), ("", ""),
              ("Primary Attributes", None), ("Strength", "196/510 +0"),
              ("Stamina", "183/510 +0"), ("Agility", "92/510 +0"),
              ("Dexterity", "120/510 +0"), ("Wisdom", "87/510 +0"),
              ("Intelligence", "65/510 +0"), ("Charisma", "52/510 +0"),
              ("Resists", None), ("SV. Magic", "70/1000"), ("SV. Fire", "71/1000"),
              ("SV. Cold", "25/1000"), ("SV. Disease", "25/1000"),
              ("SV. Poison", "15/1000"), ("SV. Void", "50/1000")]
    heroics = [("Additional Modifiers", None), ("Accuracy", "0/150"),
               ("Avoidance", "0/100"), ("Combat Effects", "0/100"),
               ("Strike Through", "0/35"), ("Stun Resist", "0/35"),
               ("Mitigation", None), ("Damage Shielding", "0/35"),
               ("Damage Shield Mitig", "0/25"), ("DoT Shielding", "0/35"),
               ("Melee Shielding", "0/35"), ("Spell Shielding", "0/35")]
    extra = [("Additional Information", None), ("Bind", "Dagnor's Cauldron"),
             ("Origin", "Oggok"), ("Deity", "Agnostic")]
    ledger(d, ox, oy, STATS1, vitals)
    ledger(d, ox, oy, STATS2, heroics)
    ledger(d, ox, oy, STATS3, extra)

    OUT.mkdir(parents=True, exist_ok=True)
    img2 = img.resize((W * 2, H * 2), Image.LANCZOS)
    canvas = Image.new("RGB", (W * 2 + 80, H * 2 + 80), (26, 24, 30))
    canvas.paste(img2, (40, 40), img2)
    canvas.save(OUT / "equipment_page.png")
    print("wrote docs/previews/equipment_page.png")


if __name__ == "__main__":
    main()
