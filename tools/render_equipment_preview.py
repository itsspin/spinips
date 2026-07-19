#!/usr/bin/env python3
"""Render a mock of the Narcissus-style Equipment screen at its real geometry.

Outputs docs/previews/equipment_page.png (2x scale for detail review).
Run from repo root:  python3 tools/render_equipment_preview.py
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
from restyle_inventory import (LEFT_RAIL, RIGHT_RAIL, WEAPON_ROW,  # noqa: E402
                               PITCH, PLATE, L_X, R_X, RAIL_Y, W_Y, W_X0, slot_pos)
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


SLOT_NAMES = {0: "Charm", 1: "Ear", 2: "Head", 3: "Face", 4: "Ear", 5: "Neck",
              6: "Shoulders", 7: "Arms", 8: "Back", 9: "Wrist", 10: "Wrist",
              11: "Range", 12: "Hands", 13: "Primary", 14: "Secondary",
              15: "Ring", 16: "Ring", 17: "Chest", 18: "Legs", 19: "Feet",
              20: "Waist", 21: "Power", 22: "Ammo", 23: "Any", 24: "Any"}
FILLED = {2: (96, 60, 140), 5: (60, 96, 140), 6: (140, 90, 50), 8: (60, 120, 80),
          17: (150, 60, 60), 7: (90, 70, 130), 12: (50, 110, 120), 1: (130, 90, 50),
          4: (80, 80, 120), 9: (120, 60, 60), 10: (150, 120, 50), 20: (96, 60, 40),
          18: (60, 96, 140), 19: (140, 60, 96), 13: (170, 140, 60), 14: (110, 110, 130),
          11: (60, 140, 130), 16: (140, 100, 160), 23: (150, 90, 150)}


def main():
    W, H = 720, 800
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

    # right sidebar
    sx = W - 115
    d.line([(sx - 4, 24), (sx - 4, H - 30)], fill=LINE_SOFT + (255,))
    d.text((sx + 52, 60), "Spin", font=F(17, True), fill=GOLD_BRIGHT, anchor="mm")
    d.text((sx + 52, 82), "40", font=F(12, True), fill=TEXT, anchor="mm")
    d.text((sx + 52, 98), "WAR/DRU/BRD", font=F(9), fill=DIM, anchor="mm")
    for lab, pct, col, y in (("NEXT LEVEL", 0.06, GOLD, 130), ("NEXT AA", 0.17, CYAN, 168)):
        d.text((sx + 4, y), lab, font=F(8), fill=DIM)
        d.rectangle([sx + 4, y + 11, sx + 104, y + 19], fill=(8, 10, 14, 255), outline=LINE_SOFT + (255,))
        d.rectangle([sx + 4, y + 11, sx + 4 + int(100 * pct), y + 19], fill=col + (255,))
        d.text((sx + 104, y), f"{int(pct*100)}%", font=F(8), fill=TEXT, anchor="ra")
    d.text((sx + 4, 206), "Weight", font=F(9), fill=DIM)
    d.text((sx + 104, 206), "85 / 196", font=F(9), fill=TEXT, anchor="ra")
    d.text((sx + 4, 220), "Weight (Worn)", font=F(9), fill=DIM)
    d.text((sx + 104, 220), "42", font=F(9), fill=TEXT, anchor="ra")
    for i, (c, amt) in enumerate((((222, 188, 96), "4,087"), ((218, 165, 32), "4,699"),
                                  ((192, 192, 200), "4,256"), ((184, 115, 51), "1,853"))):
        y = 700 + i * 22
        d.ellipse([sx + 6, y, sx + 20, y + 14], fill=c + (255,), outline=(0, 0, 0, 180))
        d.text((sx + 28, y + 2), amt, font=F(10), fill=TEXT)
    # ten bag slots, two clean columns (the IW_Slots tile box)
    slots_img = Image.open(SKIN / "window_pieces01.tga").convert("RGBA").crop((180, 110, 221, 151))
    bagcols = [(150, 110, 60), (96, 70, 40), None, (120, 90, 60), (140, 100, 50),
               None, (110, 80, 45), (150, 110, 60), None, (100, 75, 50)]
    for i in range(10):
        bx = sx + 8 + (i % 2) * 46
        by = 240 + (i // 2) * 46
        img.alpha_composite(slots_img.resize((42, 42)), (bx, by))
        if bagcols[i]:
            d.polygon([(bx + 21, by + 8), (bx + 33, by + 16), (bx + 33, by + 30),
                       (bx + 21, by + 36), (bx + 9, by + 30), (bx + 9, by + 16)],
                      fill=bagcols[i] + (255,))
            d.ellipse([bx + 15, by + 8, bx + 27, by + 16], fill=(70, 50, 30, 255))
    # buttons — the full stock set, right-anchored like the client draws them
    btns = [("Appear.", 486), ("Skills", 512), ("Alt. Adv.", 512), ("Achiev.", 538),
            ("Find Item", 538)]
    d.rounded_rectangle([sx + 4, 486, sx + 104, 506], radius=3, fill=BG2 + (255,), outline=LINE + (255,))
    d.text((sx + 54, 496), "Appear.", font=F(10), fill=TEXT, anchor="mm")
    for i, lab in enumerate(("Skills", "Alt. Adv.", "Achiev.", "Find Item")):
        bx = sx + 4 + (i % 2) * 52
        by = 512 + (i // 2) * 26
        d.rounded_rectangle([bx, by, bx + 48, by + 20], radius=3, fill=BG2 + (255,), outline=LINE + (255,))
        d.text((bx + 24, by + 10), lab, font=F(8), fill=TEXT, anchor="mm")
    d.rounded_rectangle([sx + 4, 570, sx + 104, 590], radius=3, fill=(120, 30, 30, 255), outline=LINE + (255,))
    d.text((sx + 54, 580), "Destroy", font=F(10, True), fill=TEXT, anchor="mm")
    d.rounded_rectangle([sx + 4, 596, sx + 104, 616], radius=3, fill=BG2 + (255,), outline=GOLD + (255,))
    d.text((sx + 54, 606), "Done", font=F(10, True), fill=GOLD_BRIGHT, anchor="mm")

    # ---- equipment page (0,22)+(6,6) origin -> page content at (8, 50) ----
    ox, oy = 8, 50
    hexes = Image.open(SKIN / "spin_deco.tga").convert("RGBA")
    hex_steel = hexes.crop((0, 0, 56, 56))
    hex_gold = hexes.crop((64, 0, 120, 56))

    # crest centerpiece
    cx, cy = ox + 248, oy + 10
    crest = hex_gold.resize((76, 76), Image.LANCZOS)
    img.alpha_composite(crest, (cx, cy))
    d.polygon([(cx + 38, cy + 14), (cx + 58, cy + 26), (cx + 58, cy + 50),
               (cx + 38, cy + 62), (cx + 18, cy + 50), (cx + 18, cy + 26)], fill=(96, 60, 140, 90))
    d.text((cx + 38, cy + 37), "S", font=F(30, True), fill=GOLD_BRIGHT, anchor="mm")
    d.text((ox + 286, oy + 96), "S P I N", font=F(15, True), fill=GOLD_BRIGHT, anchor="ma")

    for slot_id in range(25):
        (px, py), gold = slot_pos(slot_id)
        plate = hex_gold if gold else hex_steel
        img.alpha_composite(plate, (ox + px, oy + py))
        if slot_id in FILLED:
            c = FILLED[slot_id]
            d.rounded_rectangle([ox + px + 9, oy + py + 9, ox + px + 46, oy + py + 46],
                                radius=3, fill=c + (255,))
            d.rectangle([ox + px + 9, oy + py + 9, ox + px + 46, oy + py + 17],
                        fill=(255, 255, 255, 26))
        # weapon-row captions only — in game, empty slots show their ghost icons
        if slot_id in WEAPON_ROW:
            d.text((ox + px + PLATE // 2, oy + py + PLATE + 3), SLOT_NAMES[slot_id],
                   font=F(9), fill=DIM, anchor="ma")

    # stat columns between the rails
    stats_l = [("Character Vitals", None), ("HP", "3,025 / 3,025"), ("Mana", "1,622 / 1,622"),
               ("End", "2,212 / 2,212"), ("AC", "337/388 | 351"), ("Attack", "296 | 511"),
               ("Attack Speed %", "113"), ("Velocity", "0"), ("HP Regen", "112"),
               ("Mana Regen", "20"), ("End Regen", "36"), ("Primary DPS", "184.2"),
               ("Secondary DPS", "62.4"), ("Ranged DPS", "96.0")]
    stats_r = [("Stats & Resists", None), ("Strength", "196/510 +0"), ("Stamina", "183/510 +0"),
               ("Intelligence", "65/510 +0"), ("Wisdom", "87/510 +0"), ("Agility", "92/510 +0"),
               ("Dexterity", "120/510 +0"), ("Charisma", "52/510 +0"), ("SV. Magic", "70/1000"),
               ("SV. Fire", "71/1000"), ("SV. Cold", "25/1000"), ("SV. Disease", "25/1000"),
               ("SV. Poison", "15/1000"), ("SV. Void", "50/1000")]
    for col, sxx in ((stats_l, ox + 92), (stats_r, ox + 302)):
        yy = oy + 112
        for lab, val in col:
            if val is None:
                d.text((sxx, yy), lab.upper(), font=F(9, True), fill=GOLD)
                d.line([(sxx, yy + 14), (sxx + 190, yy + 14)], fill=GOLD + (110,))
                yy += 22
            else:
                d.text((sxx, yy), lab, font=F(10), fill=DIM)
                d.text((sxx + 190, yy), val, font=F(10, True), fill=TEXT, anchor="ra")
                yy += 19

    # additional modifiers — the complete stock block
    d.text((ox + 92, oy + 408), "ADDITIONAL MODIFIERS", font=F(9, True), fill=GOLD)
    d.line([(ox + 92, oy + 422), (ox + 492, oy + 422)], fill=GOLD + (110,))
    mods = (("Accuracy", "0/150"), ("Damage Shielding", "0/35"),
            ("Avoidance", "0/100"), ("Damage Shield Mitig", "0/25"),
            ("Combat Effects", "0/100"), ("DoT Shielding", "0/35"),
            ("Strike Through", "0/35"), ("Melee Shielding", "0/35"),
            ("Stun Resist", "0/35"), ("Spell Shielding", "0/35"))
    for i, (lab, val) in enumerate(mods):
        colx = ox + 92 + (i % 2) * 210
        rowy = oy + 430 + (i // 2) * 17
        d.text((colx, rowy), lab, font=F(9), fill=DIM)
        d.text((colx + 190, rowy), val, font=F(9, True), fill=CYAN, anchor="ra")

    d.text((ox + 92, oy + 524), "ADDITIONAL INFORMATION", font=F(9, True), fill=GOLD)
    d.line([(ox + 92, oy + 538), (ox + 492, oy + 538)], fill=GOLD + (110,))
    for i, (lab, val) in enumerate((("Bind", "Dagnor's Cauldron"), ("Origin", "Oggok"),
                                    ("Deity", "Agnostic"))):
        rowy = oy + 546 + i * 16
        d.text((ox + 92, rowy), lab, font=F(9), fill=DIM)
        d.text((ox + 492, rowy), val, font=F(9), fill=TEXT, anchor="ra")

    OUT.mkdir(parents=True, exist_ok=True)
    img2 = img.resize((W * 2, H * 2), Image.LANCZOS)
    canvas = Image.new("RGB", (W * 2 + 80, H * 2 + 80), (26, 24, 30))
    canvas.paste(img2, (40, 40), img2)
    canvas.save(OUT / "equipment_page.png")
    print("wrote docs/previews/equipment_page.png")


if __name__ == "__main__":
    main()
