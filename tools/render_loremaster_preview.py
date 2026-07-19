#!/usr/bin/env python3
"""Render a static mock of Loremaster's ledger design (full + mini).

Outputs docs/previews/loremaster_panel.png at 2x scale.
Run from repo root:  python3 tools/render_loremaster_preview.py
"""

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "docs" / "previews"

BG = (11, 13, 18)
PANEL = (16, 19, 27)
GOLD = (201, 162, 39)
GOLD_BRIGHT = (232, 197, 92)
CYAN = (65, 199, 228)
TEXT = (232, 234, 240)
DIM = (154, 163, 181)
LINE = (58, 65, 82)
GREEN = (63, 191, 107)

FD = Path("/usr/share/fonts/truetype/dejavu")


def F(size, bold=False):
    return ImageFont.truetype(str(FD / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")), size)


def hexagon(d, cx, cy, r, color, width=1):
    pts = [(cx + r * math.cos(math.radians(90 + i * 60)),
            cy + r * math.sin(math.radians(90 + i * 60))) for i in range(7)]
    d.line(pts, fill=color, width=width, joint="curve")


def main():
    W, H = 420, 560
    img = Image.new("RGB", (W, H), (26, 24, 30))
    d = ImageDraw.Draw(img)

    # panel with ember frame
    d.rectangle([0, 0, W - 1, H - 1], fill=GOLD)
    d.rectangle([1, 1, W - 2, H - 2], fill=BG)
    d.rectangle([2, 2, W - 3, 26], fill=PANEL)
    hexagon(d, 14, 14, 7, GOLD_BRIGHT, 2)
    d.text((26, 14), "LOREMASTER", font=F(12, True), fill=GOLD_BRIGHT, anchor="lm")
    d.text((122, 15), "Spin (qeynos)", font=F(10), fill=DIM, anchor="lm")
    d.text((W - 12, 14), "↺   —   ✕", font=F(11, True), fill=DIM, anchor="rm")
    d.ellipse([W - 86, 10, W - 78, 18], fill=GREEN)
    d.text((10, 36), "Blackburrow", font=F(10), fill=TEXT)
    d.text((W - 10, 36), "session 1h44m (since 3:38 PM)", font=F(10), fill=DIM, anchor="ra")

    # hero band
    for i, (v, lab, col) in enumerate((("1,284", "FIGHT DPS", GOLD_BRIGHT),
                                       ("946", "SESSION", TEXT),
                                       ("2,105", "BEST", CYAN))):
        cx = 10 + i * 133 + 66
        d.text((cx, 58), v, font=F(22, True), fill=col, anchor="ma")
        d.text((cx, 86), lab, font=F(9), fill=DIM, anchor="ma")
    d.rectangle([10, 102, W - 10, 103], fill=GOLD)

    def section(y, name, value, pinned, expanded=False):
        hexagon(d, 18, y + 8, 6, GOLD, 1)
        d.text((30, y + 8), name, font=F(10, True), fill=GOLD, anchor="lm")
        d.text((W - 56, y + 8), value, font=F(11, True), fill=TEXT, anchor="rm")
        d.text((W - 38, y + 8), "✦" if pinned else "◇", font=F(11),
               fill=GOLD_BRIGHT if pinned else LINE, anchor="mm")
        d.text((W - 18, y + 8), "▾" if expanded else "▸", font=F(10), fill=DIM, anchor="mm")
        d.line([(12, y + 18), (W - 12, y + 18)], fill=GOLD, width=1)
        d.line([(12, y + 19), (W - 12, y + 19)], fill=(5, 6, 9), width=1)
        return y + 24

    y = 112
    y = section(y, "COMBAT", "1,284 dps ⚔", True, expanded=True)
    for left, right, dimmed in (
        ("Dealt 11.5k (5.1k melee / 6.5k spell) · 21 crits · 77% accuracy", "", True),
        ("Biggest hit: 692 (Melee on Froglok shin knight)", "", True),
        ("Taken 1,973 · avoided 315 attacks", "", True),
    ):
        d.text((30, y), left, font=F(9), fill=DIM)
        y += 14
    d.text((30, y + 2), "Damage by attack", font=F(9, True), fill=GOLD)
    y += 17
    for name, val in (("Careless Lightning", "4,449 · 117 hits · avg 38.0"),
                      ("Melee", "3,347 · 126 hits · avg 26.6"),
                      ("Pet (Gann)", "1,541 · 168 hits · avg 9.2"),
                      ("DoT: Flame Lick", "672 · 21 ticks · avg 32.0")):
        d.text((30, y), name, font=F(9), fill=TEXT)
        d.text((W - 16, y), val, font=F(9), fill=GOLD_BRIGHT, anchor="ra")
        y += 14
    y += 6

    y = section(y, "SLAYING", "96 (+15)", True)
    y = section(y, "SPOILS", "38 items", False)
    y = section(y, "COIN", "2p 9g 1s 6c", True)
    y = section(y, "PROGRESSION", "14.2% xp, +1 lvl", True)
    y = section(y, "STANDING", "4 factions", False)
    y = section(y, "JOURNEY", "0 deaths", False)
    d.text((10, H - 18), "tailing eqlog_Spin_qeynos.txt", font=F(9), fill=DIM)

    # mini strip
    MW, MH = 470, 30
    mini = Image.new("RGB", (MW, MH), (26, 24, 30))
    md = ImageDraw.Draw(mini)
    md.rectangle([0, 0, MW - 1, MH - 1], fill=GOLD)
    md.rectangle([1, 1, MW - 2, MH - 2], fill=BG)
    md.rectangle([1, 1, 4, MH - 2], fill=GOLD)
    mx = 14
    for i, (nm, val) in enumerate((("COMBAT", "1,284 dps"), ("SLAYING", "96"),
                                   ("COIN", "2p 9g"), ("PROGRESSION", "14.2% · lvl 3h41m"))):
        if i:
            md.line([(mx, 7), (mx, MH - 8)], fill=GOLD)
            mx += 10
        md.text((mx, MH // 2), nm, font=F(8), fill=GOLD, anchor="lm")
        mx += int(md.textlength(nm, font=F(8))) + 5
        md.text((mx, MH // 2), val, font=F(10, True), fill=TEXT, anchor="lm")
        mx += int(md.textlength(val, font=F(10, True))) + 12
    md.text((MW - 10, MH // 2), "▣", font=F(10), fill=DIM, anchor="rm")

    OUT.mkdir(parents=True, exist_ok=True)
    canvas = Image.new("RGB", (W * 2 + 80, H * 2 + MH * 2 + 110), (26, 24, 30))
    canvas.paste(img.resize((W * 2, H * 2), Image.LANCZOS), (40, 40))
    canvas.paste(mini.resize((MW * 2, MH * 2), Image.LANCZOS), (40, H * 2 + 70))
    canvas.save(OUT / "loremaster_panel.png")
    print("wrote docs/previews/loremaster_panel.png")


if __name__ == "__main__":
    main()
