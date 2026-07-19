#!/usr/bin/env python3
"""Render the current Loremaster full panel and mini strip for the README."""

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from spinui_theme import (BG1, BG2, BG3, CYAN, EMBER, GOLD, GOLD_BRIGHT, GREEN,
                          LINE, PARCHMENT, TEXT, TEXT_DIM, VOID)

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "docs" / "previews"

BG = BG1
PANEL = BG2
RAISED = BG3
DIM = TEXT_DIM


def font_path(*names):
    roots = [Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/dejavu")]
    for root in roots:
        for name in names:
            path = root / name
            if path.exists():
                return path
    return None


SANS = font_path("segoeui.ttf", "DejaVuSans.ttf")
BOLD = font_path("seguisb.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf")
SERIF = font_path("georgiab.ttf", "DejaVuSerif-Bold.ttf")
SYMBOL = font_path("seguisym.ttf", "DejaVuSans.ttf")


def F(size, bold=False, serif=False):
    path = SERIF if serif else (BOLD if bold else SANS)
    return ImageFont.truetype(str(path), size) if path else ImageFont.load_default()


def FS(size):
    return ImageFont.truetype(str(SYMBOL), size) if SYMBOL else F(size)


def hexagon(draw, cx, cy, radius, color, width=1, inner=False):
    pts = [(cx + radius * math.cos(math.radians(90 + i * 60)),
            cy + radius * math.sin(math.radians(90 + i * 60))) for i in range(7)]
    draw.line(pts, fill=color, width=width, joint="curve")
    if inner:
        draw.ellipse([cx - 2, cy - 2, cx + 2, cy + 2], fill=EMBER)


def section(draw, width, y, name, value, pinned, expanded=False):
    accent = CYAN if expanded else LINE
    hexagon(draw, 19, y + 9, 6, accent)
    draw.text((31, y + 9), name, font=F(9, bold=True, serif=True),
              fill=accent if expanded else TEXT_DIM, anchor="lm")
    draw.text((width - 57, y + 9), value, font=F(11, bold=True), fill=TEXT, anchor="rm")
    draw.text((width - 39, y + 9), "✦" if pinned else "◇", font=FS(10),
              fill=GOLD_BRIGHT if pinned else LINE, anchor="mm")
    draw.text((width - 18, y + 9), "▾" if expanded else "▸", font=FS(9), fill=DIM, anchor="mm")
    draw.line([(12, y + 20), (width - 12, y + 20)], fill=accent)
    draw.line([(12, y + 21), (width - 12, y + 21)], fill=(5, 6, 9))
    return y + 27


def main():
    width, height = 430, 600
    panel = Image.new("RGB", (width, height), GOLD)
    d = ImageDraw.Draw(panel)
    d.rectangle([1, 1, width - 2, height - 2], fill=BG)

    # Heraldic masthead, identity, and mode switcher.
    d.rectangle([2, 2, width - 3, 33], fill=PANEL)
    hexagon(d, 16, 17, 8, GOLD_BRIGHT, 2, inner=True)
    d.text((30, 17), "SPIN'S LOREMASTER", font=F(11, serif=True), fill=GOLD_BRIGHT, anchor="lm")
    d.ellipse([width - 89, 13, width - 81, 21], fill=GREEN)
    d.text((width - 12, 17), "↻   —   ×", font=FS(11), fill=DIM, anchor="rm")
    d.rectangle([2, 34, width - 3, 35], fill=EMBER)
    d.text((10, 48), "SPIN · QEYNOS", font=F(8, serif=True), fill=PARCHMENT, anchor="lm")
    d.text((width - 10, 48), "session 1h44m · since 3:38 PM", font=F(8), fill=DIM, anchor="rm")
    d.text((10, 64), "Blackburrow", font=F(9), fill=TEXT, anchor="lm")
    d.text((width - 10, 64), "THE ADVENTURER'S CHRONICLE", font=F(7, serif=True), fill=LINE, anchor="rm")

    d.rectangle([10, 75, width - 10, 98], fill=VOID)
    tab_w = (width - 20) / 3
    d.rectangle([10, 75, int(10 + tab_w), 98], fill=RAISED)
    for i, (label, color) in enumerate((("FIGHT", CYAN), ("SESSION", DIM), ("RECORDS", DIM))):
        d.text((10 + tab_w * (i + .5), 87), label, font=F(8, serif=True), fill=color, anchor="mm")

    # Raised three-stat hero band.
    hero_top, hero_bottom = 103, 157
    d.rectangle([10, hero_top, width - 10, hero_bottom], fill=RAISED)
    d.rectangle([10, hero_top, 12, hero_bottom], fill=CYAN)
    for x in (width // 3, 2 * width // 3):
        d.line([(x, hero_top + 7), (x, hero_bottom - 7)], fill=LINE)
    for i, (value, label, color) in enumerate((
        ("1,284", "FIGHT DPS", GOLD_BRIGHT),
        ("946", "SESSION", TEXT),
        ("2,105", "BEST", CYAN),
    )):
        cx = int((i + 0.5) * width / 3)
        d.text((cx, 120), value, font=F(20, bold=True), fill=color, anchor="ma")
        d.text((cx, 145), label, font=F(7, serif=True), fill=DIM, anchor="ma")
    d.rectangle([10, 162, width - 10, 163], fill=GOLD)

    y = section(d, width, 171, "COMBAT", "1,284 dps", True, expanded=True)
    for text in (
        "Dealt 11.5k (5.1k melee / 6.5k spell) · 21 crits · 77% accuracy",
        "Biggest hit: 692 (Melee on Froglok shin knight)",
        "Taken 1,973 · avoided 315 attacks",
    ):
        d.text((31, y), text, font=F(8), fill=DIM)
        y += 14
    d.text((31, y + 2), "Damage by attack", font=F(8, bold=True), fill=GOLD)
    y += 18
    for name, value, share in (
        ("Careless Lightning", "4,449 · 39% · 556/s", .39),
        ("Melee", "3,347 · 29% · 418/s", .29),
        ("Pet (Gann)", "1,541 · 13% · 193/s", .13),
        ("DoT: Flame Lick", "672 · 6% · 84/s", .06),
    ):
        d.rectangle([29, y - 2, width - 16, y + 11], fill=(9, 16, 20))
        d.rectangle([29, y - 2, 29 + int((width - 45) * share), y + 11], fill=(18, 48, 47))
        d.line([(29, y - 2), (29 + int((width - 45) * share), y - 2)], fill=(30, 116, 104))
        d.text((31, y), name, font=F(8), fill=TEXT)
        d.text((width - 17, y), value, font=F(8), fill=GOLD_BRIGHT, anchor="ra")
        y += 14
    y += 4

    for name, value, pinned in (
        ("SLAYING", "47 (+9)", True),
        ("SPOILS", "23 items", False),
        ("COIN", "2p 9g 1s 6c", True),
        ("PROGRESSION", "18.6% xp, +3 AA", True),
        ("STANDING", "7 factions", False),
        ("JOURNEY", "0 deaths", False),
    ):
        y = section(d, width, y, name, value, pinned)

    # Actionable footer makes first-run setup discoverable.
    d.rectangle([2, height - 29, width - 3, height - 3], fill=PANEL)
    d.text((10, height - 16), "tailing eqlog_Spin_qeynos.txt", font=F(8), fill=DIM, anchor="lm")
    d.rectangle([width - 69, height - 25, width - 8, height - 7], fill=RAISED, outline=LINE)
    d.text((width - 38, height - 16), "CHANGE", font=F(7, serif=True), fill=GOLD_BRIGHT, anchor="mm")

    # Mini mode uses the same material and the same pinned ledger vocabulary.
    mini_w, mini_h = 520, 32
    mini = Image.new("RGB", (mini_w, mini_h), GOLD)
    md = ImageDraw.Draw(mini)
    md.rectangle([1, 1, mini_w - 2, mini_h - 2], fill=BG)
    md.rectangle([1, 1, 4, mini_h - 2], fill=EMBER)
    x = 14
    for i, (name, value) in enumerate((
        ("COMBAT", "1,284 dps"), ("SLAYING", "47"),
        ("COIN", "2p 9g"), ("PROGRESSION", "18.6% · +3 AA"),
    )):
        if i:
            md.line([(x, 7), (x, mini_h - 8)], fill=GOLD)
            x += 10
        md.text((x, mini_h // 2), name, font=F(7, serif=True), fill=GOLD, anchor="lm")
        x += int(md.textlength(name, font=F(7, serif=True))) + 5
        md.text((x, mini_h // 2), value, font=F(9, bold=True), fill=TEXT, anchor="lm")
        x += int(md.textlength(value, font=F(9, bold=True))) + 12
    md.text((mini_w - 35, mini_h // 2), "LOG", font=F(7, serif=True), fill=GOLD_BRIGHT, anchor="rm")
    md.text((mini_w - 10, mini_h // 2), "▣", font=FS(10), fill=DIM, anchor="rm")

    OUT.mkdir(parents=True, exist_ok=True)
    canvas = Image.new("RGB", (max(width, mini_w) * 2 + 80,
                                height * 2 + mini_h * 2 + 110), (26, 24, 30))
    canvas.paste(panel.resize((width * 2, height * 2), Image.Resampling.LANCZOS), (40, 40))
    canvas.paste(mini.resize((mini_w * 2, mini_h * 2), Image.Resampling.LANCZOS),
                 (40, height * 2 + 70))
    canvas.save(OUT / "loremaster_panel.png")
    print("wrote docs/previews/loremaster_panel.png")


if __name__ == "__main__":
    main()
