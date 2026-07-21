#!/usr/bin/env python3
"""Render the compact v3 Loadouts/Personas Inventory tab for visual QA."""

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
from render_equipment_preview import FILLED, SLOT_ABBR  # noqa: E402
from restyle_inventory import BAGS, CREST, WINDOW  # noqa: E402
from restyle_persona import (EQUIP_CANVAS, GOLD_SLOTS, PLATE,  # noqa: E402
                             PLATE_POSITIONS, SLOT_INSET)
from spinui_theme import (BG1, BG2, BG3, CYAN, GOLD, GOLD_BRIGHT, LINE,  # noqa: E402
                          LINE_SOFT, TEXT, TEXT_DIM)

SKIN = REPO / "spinui_reloaded"
OUT = REPO / "docs" / "previews" / "persona_page.png"


def font(size, bold=False):
    names = (("seguisb.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf") if bold
             else ("segoeui.ttf", "DejaVuSans.ttf"))
    for root in (Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/dejavu")):
        for name in names:
            path = root / name
            if path.exists():
                return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def button(draw, box, label, accent=False):
    draw.rounded_rectangle(box, radius=3, fill=BG3 + (255,),
                           outline=(GOLD if accent else LINE) + (255,))
    draw.text(((box[0] + box[2]) // 2, (box[1] + box[3]) // 2), label,
              font=font(9, accent), fill=GOLD_BRIGHT if accent else TEXT, anchor="mm")


def main():
    w, h = WINDOW
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=5,
                        fill=(9, 13, 18, 250), outline=LINE + (255,))
    d.rectangle([1, 1, w - 2, 18], fill=BG2 + (255,))
    d.line([(1, 1), (w - 2, 1)], fill=CYAN + (255,))
    d.line([(1, 17), (w - 2, 17)], fill=GOLD + (255,))
    d.text((10, 9), "Inventory", font=font(11, True), fill=TEXT, anchor="lm")
    d.text((w - 12, 9), "—   ×", font=font(11, True), fill=TEXT_DIM, anchor="rm")

    tabs = (("Equipment", 96), ("Pet", 60), ("Loadouts", 96), ("Storage", 84))
    x = 8
    for name, tw in tabs:
        active = name == "Loadouts"
        d.rounded_rectangle([x, 24, x + tw, 44], radius=4,
                            fill=(23, 34, 42, 255) if active else BG1 + (255,),
                            outline=(CYAN if active else LINE_SOFT) + (255,))
        if active:
            d.line([(x + 4, 43), (x + tw - 4, 43)], fill=CYAN + (255,), width=2)
        d.text((x + tw // 2, 34), name, font=font(10, active),
               fill=TEXT if active else TEXT_DIM, anchor="mm")
        x += tw + 4

    page_x, page_y = 8, 48
    d.text((page_x + 12, page_y + 6), "CHARACTER EQUIPMENT", font=font(9, True), fill=GOLD)
    ox, oy = page_x + 8, page_y + 24
    d.rounded_rectangle([ox, oy, ox + EQUIP_CANVAS[0], oy + EQUIP_CANVAS[1]], radius=4,
                        fill=(7, 11, 15, 230), outline=LINE_SOFT + (255,))
    hexes = Image.open(SKIN / "spin_deco.tga").convert("RGBA")
    steel = hexes.crop((0, 64, 46, 110))
    gold = hexes.crop((64, 64, 110, 110))
    for slot_id in range(23):
        px, py = PLATE_POSITIONS[slot_id]
        img.alpha_composite(gold if slot_id in GOLD_SLOTS else steel,
                            (ox + px, oy + py))
        color = FILLED.get(slot_id)
        inner = (ox + px + SLOT_INSET, oy + py + SLOT_INSET)
        if color:
            d.rounded_rectangle([inner[0] + 4, inner[1] + 4,
                                 inner[0] + 36, inner[1] + 36], radius=3,
                                fill=color + (255,))
        else:
            d.text((ox + px + PLATE // 2, oy + py + PLATE // 2), SLOT_ABBR[slot_id],
                   font=font(7), fill=TEXT_DIM, anchor="mm")

    # Client-owned persona art at the exact 85x171 native viewport.
    vx, vy = ox + 192, oy + 8
    d.rounded_rectangle([vx, vy, vx + 85, vy + 171], radius=4,
                        fill=BG1 + (255,), outline=GOLD + (220,))
    d.ellipse([vx + 25, vy + 18, vx + 60, vy + 53], fill=(154, 115, 79, 255))
    d.polygon([(vx + 20, vy + 57), (vx + 65, vy + 57), (vx + 75, vy + 130),
               (vx + 42, vy + 153), (vx + 10, vy + 130)],
              fill=(68, 104, 129, 255), outline=CYAN + (180,))
    d.text((vx + 42, vy + 160), "LOADOUT", font=font(7, True),
           fill=GOLD_BRIGHT, anchor="mm")

    d.text((page_x + 12, page_y + 300), "CHARACTER LOADOUTS", font=font(9, True), fill=GOLD)
    list_box = [page_x + 8, page_y + 316, page_x + 477, page_y + 440]
    d.rectangle(list_box, fill=BG1 + (255,), outline=LINE + (255,))
    widths = (36, 104, 124, 205)
    cx = list_box[0]
    for heading, width in zip(("#", "RACE", "PRIMARY CLASS", "SECONDARY CLASSES"), widths):
        d.rectangle([cx, list_box[1], cx + width, list_box[1] + 21], fill=BG2 + (255,))
        d.text((cx + 6, list_box[1] + 11), heading, font=font(7, True), fill=TEXT_DIM, anchor="lm")
        cx += width
        d.line([(cx, list_box[1]), (cx, list_box[3])], fill=LINE_SOFT + (255,))
    rows = (("1", "Ogre", "Warrior 39", "Druid 31 · Bard 26"),
            ("2", "Ogre", "Druid 31", "Warrior 39 · Bard 26"),
            ("3", "Ogre", "Bard 26", "Warrior 39 · Druid 31"))
    for index, row in enumerate(rows):
        ry = list_box[1] + 22 + index * 32
        if index == 0:
            d.rectangle([list_box[0] + 1, ry, list_box[2] - 1, ry + 30], fill=(17, 42, 46, 255))
            d.rectangle([list_box[0] + 1, ry, list_box[0] + 3, ry + 30], fill=CYAN + (255,))
        cx = list_box[0]
        for value, width in zip(row, widths):
            d.text((cx + 7, ry + 15), value, font=font(9, index == 0), fill=TEXT, anchor="lm")
            cx += width
    button(d, [page_x + 8, page_y + 448, page_x + 86, page_y + 472], "ADD")
    button(d, [page_x + 92, page_y + 448, page_x + 170, page_y + 472], "EDIT")
    button(d, [page_x + 176, page_y + 448, page_x + 254, page_y + 472], "SWAP", True)
    d.text((page_x + 276, page_y + 460), "SWAPPING:  LIVE",
           font=font(8, True), fill=CYAN, anchor="lm")

    d.text((page_x + 12, page_y + 480), "CLASS LEVELS", font=font(9, True), fill=GOLD)
    for index, (name, level) in enumerate((("WARRIOR", 39), ("DRUID", 31),
                                           ("BARD", 26), ("ROGUE", 1))):
        bx = page_x + 8 + index * 126
        by = page_y + 496
        d.rounded_rectangle([bx, by, bx + 89, by + 74], radius=3,
                            fill=BG1 + (255,), outline=(CYAN if index == 0 else LINE) + (255,))
        d.text((bx + 45, by + 23), str(level), font=font(20, True),
               fill=GOLD_BRIGHT if index == 0 else TEXT, anchor="mm")
        d.text((bx + 45, by + 55), name, font=font(7, True),
               fill=CYAN if index == 0 else TEXT_DIM, anchor="mm")

    # Stable identity rail: name, Destroy, class crest, and the bag grid.
    rail_x = w - 165
    d.line([(rail_x - 4, 24), (rail_x - 4, h - 28)], fill=LINE_SOFT + (255,))
    d.text((rail_x + 82, 35), "Spin", font=font(17, True), fill=GOLD_BRIGHT, anchor="mm")
    d.text((rail_x + 82, 55), "39 WAR/DRU/BRD", font=font(10, True), fill=TEXT, anchor="mm")
    button(d, [rail_x + 14, 120, rail_x + 150, 138], "DESTROY")
    ccx, ccy = CREST
    d.rounded_rectangle([ccx, ccy, ccx + 85, ccy + 171], radius=4,
                        fill=(7, 11, 15, 255), outline=GOLD + (220,))
    d.text((ccx + 42, ccy + 85), "CLASS\nCREST", font=font(8, True),
           fill=TEXT_DIM, anchor="mm", align="center")
    for i in range(12):
        bx = BAGS[0] + (i % 2) * 43
        by = BAGS[1] + (i // 2) * 43
        d.rounded_rectangle([bx, by, bx + 40, by + 40], radius=4,
                            fill=BG1 + (255,), outline=LINE + (255,))
        d.polygon([(bx + 20, by + 7), (bx + 31, by + 16), (bx + 28, by + 31),
                   (bx + 12, by + 31), (bx + 9, by + 16)],
                  fill=((85 + i * 7) % 145, 74, 55, 255), outline=GOLD + (140,))
    button(d, [rail_x + 48, h - 20, rail_x + 160, h - 4], "DONE", True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    scaled = img.resize((w * 2, h * 2), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (w * 2 + 80, h * 2 + 80), (26, 24, 30))
    canvas.paste(scaled, (40, 40), scaled)
    canvas.save(OUT)
    print(f"wrote {OUT.relative_to(REPO)}")


if __name__ == "__main__":
    main()
