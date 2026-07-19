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

SKIN = REPO / "spinui_reloaded"
OUT = REPO / "docs" / "previews"

GOLD = (201, 162, 39)
GOLD_BRIGHT = (232, 197, 92)
CYAN = (65, 199, 228)
TEXT = (232, 234, 240)
DIM = (154, 163, 181)
LINE = (58, 65, 82)
LINE_SOFT = (38, 43, 56)
BG1 = (16, 19, 27)

FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")


def F(size, bold=False):
    return ImageFont.truetype(str(FONT_DIR / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")), size)


SLOT_NAMES = {0: "Charm", 1: "Ear", 2: "Head", 3: "Face", 4: "Ear", 5: "Neck",
              6: "Shoulders", 7: "Arms", 8: "Back", 9: "Wrist", 10: "Wrist",
              11: "Range", 12: "Hands", 13: "Primary", 14: "Secondary",
              15: "Ring", 16: "Ring", 17: "Chest", 18: "Legs", 19: "Feet",
              20: "Waist", 21: "Power", 22: "Ammo"}
FILLED = {2: (96, 60, 140), 5: (60, 96, 140), 6: (140, 90, 50), 8: (60, 120, 80),
          17: (150, 60, 60), 7: (90, 70, 130), 12: (50, 110, 120), 1: (130, 90, 50),
          4: (80, 80, 120), 9: (120, 60, 60), 10: (150, 120, 50), 20: (96, 60, 40),
          18: (60, 96, 140), 19: (140, 60, 96), 13: (170, 140, 60), 14: (110, 110, 130),
          11: (60, 140, 130), 16: (140, 100, 160)}


def main():
    W, H = 720, 800
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # window: rounded obsidian glass + titlebar
    d.rounded_rectangle([0, 0, W - 1, H - 1], radius=6, fill=(14, 17, 24, 250), outline=LINE + (255,))
    d.rectangle([1, 1, W - 2, 18], fill=(24, 28, 39, 255))
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
                            fill=(31, 36, 50, 255) if active else (16, 19, 27, 255),
                            outline=(GOLD if active else LINE_SOFT) + (255,))
        d.text((tx + tw // 2, 34), t, font=F(11, active), fill=GOLD_BRIGHT if active else DIM, anchor="mm")
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
    d.text((sx + 4, 210), "Weight", font=F(9), fill=DIM)
    d.text((sx + 104, 210), "86 / 196", font=F(9), fill=TEXT, anchor="ra")
    for i, (c, amt) in enumerate((((222, 188, 96), "4,087"), ((218, 165, 32), "4,699"),
                                  ((192, 192, 200), "4,256"), ((184, 115, 51), "1,853"))):
        y = 700 + i * 22
        d.ellipse([sx + 6, y, sx + 20, y + 14], fill=c + (255,), outline=(0, 0, 0, 180))
        d.text((sx + 28, y + 2), amt, font=F(10), fill=TEXT)
    d.rounded_rectangle([sx + 4, 640, sx + 104, 660], radius=3, fill=(120, 30, 30, 255), outline=LINE + (255,))
    d.text((sx + 54, 650), "Destroy", font=F(10, True), fill=TEXT, anchor="mm")

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

    for slot_id in range(23):
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
    stats_l = [("Character Vitals", None), ("HP", "3,206 / 3,206"), ("Mana", "1,622 / 1,622"),
               ("Endurance", "2,212 / 2,212"), ("AC", "348 / 388"), ("Attack", "2,961"),
               ("Attack Speed", "113%"), ("HP Regen", "66"), ("Mana Regen", "40"),
               ("End Regen", "36"), ("Primary DPS", "184.2"), ("Ranged DPS", "96.0")]
    stats_r = [("Stats & Resists", None), ("Strength", "196 / 510"), ("Stamina", "183 / 510"),
               ("Intelligence", "65 / 510"), ("Wisdom", "87 / 510"), ("Agility", "82 / 510"),
               ("Dexterity", "120 / 510"), ("Magic", "70 / 1000"), ("Fire", "71 / 1000"),
               ("Cold", "65 / 1000"), ("Disease", "25 / 1000"), ("Poison", "15 / 1000")]
    for col, sxx in ((stats_l, ox + 92), (stats_r, ox + 302)):
        yy = oy + 116
        for lab, val in col:
            if val is None:
                d.text((sxx, yy), lab.upper(), font=F(9, True), fill=GOLD)
                d.line([(sxx, yy + 14), (sxx + 190, yy + 14)], fill=GOLD + (110,))
                yy += 22
            else:
                d.text((sxx, yy), lab, font=F(10), fill=DIM)
                d.text((sxx + 190, yy), val, font=F(10, True), fill=TEXT, anchor="ra")
                yy += 19

    # heroic band
    d.text((ox + 92, oy + 420), "HEROIC MODS", font=F(9, True), fill=GOLD)
    d.line([(ox + 92, oy + 434), (ox + 492, oy + 434)], fill=GOLD + (110,))
    for i, (lab, val) in enumerate((("Accuracy", "+58"), ("Avoidance", "+31"), ("Combat Effects", "+44"),
                                    ("Strikethrough", "+12"), ("Stun Resist", "+18"), ("DS Mitigation", "+9"))):
        colx = ox + 92 + (i % 3) * 136
        rowy = oy + 442 + (i // 3) * 19
        d.text((colx, rowy), lab, font=F(9), fill=DIM)
        d.text((colx + 118, rowy), val, font=F(9, True), fill=CYAN, anchor="ra")

    d.text((ox + 92, oy + 500), "ORIGIN  Qeynos      BIND  Blackburrow      DEITY  Tunare",
           font=F(9), fill=DIM)

    OUT.mkdir(parents=True, exist_ok=True)
    img2 = img.resize((W * 2, H * 2), Image.LANCZOS)
    canvas = Image.new("RGB", (W * 2 + 80, H * 2 + 80), (26, 24, 30))
    canvas.paste(img2, (40, 40), img2)
    canvas.save(OUT / "equipment_page.png")
    print("wrote docs/previews/equipment_page.png")


if __name__ == "__main__":
    main()
