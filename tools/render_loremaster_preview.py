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
LORE_HOTKEY = "CTRL+SHIFT+E"

# Deterministic documentation example, verified against the EQL Wiki
# MediaWiki wikitext endpoint for https://eqlwiki.com/Cloak_of_Flames.
CLOAK_OF_FLAMES = {
    "title": "Cloak of Flames",
    "profile": (
        ("MAGIC ITEM", CYAN),
        ("Slot: BACK", TEXT),
        ("AC: 10", TEXT),
        ("DEX: +9   AGI: +9   HP: +50", TEXT),
        ("SV FIRE: +15", TEXT),
        ("Haste: +36%", TEXT),
        ("WT: 0.1   Size: MEDIUM", TEXT),
        ("Class: ALL   Race: ALL", TEXT),
    ),
    "drops": (("Nagafen's Lair", "Lord Nagafen"),),
}


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


def render_lore_lens():
    """Render the item-intelligence companion shown beside EQ's tooltip."""
    width, height = 392, 560
    lens = Image.new("RGB", (width, height), GOLD)
    d = ImageDraw.Draw(lens)
    d.rectangle([1, 1, width - 2, height - 2], fill=BG)

    d.rectangle([2, 2, width - 3, 34], fill=PANEL)
    d.rectangle([2, 2, 5, 34], fill=CYAN)
    d.text((14, 18), "LORE LENS", font=F(10, serif=True),
           fill=GOLD_BRIGHT, anchor="lm")
    d.text((width - 34, 18), f"{LORE_HOTKEY}  •  SETTINGS",
           font=F(6, serif=True),
           fill=DIM, anchor="rm")
    d.text((width - 12, 18), "×", font=F(10, bold=True), fill=DIM, anchor="mm")

    d.rectangle([10, 43, width - 10, 72], fill=RAISED)
    d.rectangle([12, 46, width - 89, 69], fill=VOID, outline=LINE)
    d.text((19, 58), CLOAK_OF_FLAMES["title"], font=F(9), fill=TEXT, anchor="lm")
    d.rectangle([width - 84, 46, width - 13, 69], fill=PANEL, outline=LINE)
    d.text((width - 48, 58), "SEARCH", font=F(7, serif=True),
           fill=GOLD_BRIGHT, anchor="mm")

    y = 84
    d.text((14, y), CLOAK_OF_FLAMES["title"].upper(), font=F(13, serif=True),
           fill=GOLD_BRIGHT)
    y += 27
    d.text((14, y), "ITEM PROFILE", font=F(7, serif=True), fill=GOLD)
    y += 17
    for line, color in CLOAK_OF_FLAMES["profile"]:
        d.text((14, y), line, font=F(8, bold=(color == CYAN)), fill=color)
        y += 15

    d.text((14, y + 4), "DROPS FROM", font=F(7, serif=True), fill=GOLD)
    y += 24
    for zone, creature in CLOAK_OF_FLAMES["drops"]:
        d.text((14, y), zone, font=F(8, bold=True), fill=GOLD_BRIGHT)
        y += 14
        d.text((26, y), "• " + creature, font=F(8), fill=TEXT)
        y += 16

    for heading, empty in (
        ("SOLD BY", "This item cannot be purchased from merchants."),
        ("RELATED QUESTS", "This item has no related quests."),
        ("PLAYER CRAFTED", "This item is not crafted by players."),
        ("TRADESKILL RECIPES", "This item is not used in player tradeskills."),
    ):
        d.text((14, y), heading, font=F(7, serif=True), fill=GOLD)
        y += 15
        d.text((14, y), empty, font=F(7), fill=DIM)
        y += 19

    d.rectangle([2, height - 53, width - 3, height - 24], fill=PANEL)
    d.text((10, height - 39), "EQL WIKI  •  CACHED JUST NOW", font=F(7, serif=True),
           fill=DIM, anchor="lm")
    d.rectangle([width - 153, height - 49, width - 8, height - 28],
                fill=RAISED, outline=LINE)
    d.text((width - 86, height - 39), "OPEN FULL WIKI PAGE",
           font=F(7, serif=True), fill=CYAN, anchor="mm")
    d.text((width - 19, height - 39), "↗", font=FS(8), fill=CYAN,
           anchor="mm")
    d.text((width // 2, height - 12),
           "SAFE LOOKUP  •  CLIPBOARD OR SEARCH  •  NO EQ INJECTION",
           font=F(6, serif=True), fill=LINE, anchor="mm")
    return lens


def main():
    width, height = 400, 740
    panel = Image.new("RGB", (width, height), GOLD)
    d = ImageDraw.Draw(panel)
    d.rectangle([1, 1, width - 2, height - 2], fill=BG)

    # Heraldic masthead, identity, and mode switcher.
    d.rectangle([2, 2, width - 3, 33], fill=PANEL)
    hexagon(d, 16, 17, 8, GOLD_BRIGHT, 2, inner=True)
    d.text((30, 17), "SPIN'S LOREMASTER", font=F(11, serif=True), fill=GOLD_BRIGHT, anchor="lm")
    d.ellipse([width - 197, 13, width - 189, 21], fill=GREEN)
    d.text((width - 12, 17), "LOCK   RESET   LORE   HUD   ×", font=F(8, bold=True), fill=DIM, anchor="rm")
    d.rectangle([2, 34, width - 3, 35], fill=EMBER)
    d.text((10, 48), "SPIN · QEYNOS", font=F(8, serif=True), fill=PARCHMENT, anchor="lm")
    d.text((width - 10, 48), "session 1h44m · since 3:38 PM", font=F(8), fill=DIM, anchor="rm")
    d.text((10, 64), "Blackburrow", font=F(9), fill=TEXT, anchor="lm")
    d.rectangle([218, 55, width - 10, 72], fill=RAISED)
    d.text((width - 16, 64), f"LORE LENS  •  {LORE_HOTKEY}",
           font=F(7, serif=True), fill=CYAN, anchor="rm")

    d.rectangle([10, 75, width - 10, 98], fill=VOID)
    tab_w = (width - 20) / 3
    d.rectangle([10, 75, int(10 + tab_w), 98], fill=RAISED)
    for i, (label, color) in enumerate((("FIGHT", CYAN), ("SESSION", DIM), ("RECORDS", DIM))):
        d.text((10 + tab_w * (i + .5), 87), label, font=F(8, serif=True), fill=color, anchor="mm")

    # Encounter browser keeps history one click away without leaving the HUD.
    d.rectangle([10, 103, width - 10, 125], fill=BG)
    d.rectangle([10, 103, 67, 124], fill=RAISED, outline=LINE)
    d.text((38, 114), "‹ OLDER", font=F(7, serif=True), fill=CYAN, anchor="mm")
    d.text((width // 2, 114), "LIVE · FROGLOK SHIN KNIGHT",
           font=F(7, serif=True), fill=GOLD_BRIGHT, anchor="mm")
    d.rectangle([width - 67, 103, width - 10, 124], fill=RAISED, outline=LINE)
    d.text((width - 38, 114), "NEWER ›", font=F(7, serif=True), fill=LINE, anchor="mm")

    # Encounter Lab pivots keep deep analysis one click away.
    lab_top, lab_bottom = 129, 149
    lab_w = (width - 20) / 5
    for i, label in enumerate((
        "OVERVIEW", "DAMAGE", "HEALING", "TARGETS", "TIMELINE",
    )):
        x0 = int(10 + i * lab_w)
        x1 = int(10 + (i + 1) * lab_w) - 1
        d.rectangle([x0, lab_top, x1, lab_bottom],
                    fill=RAISED if label == "OVERVIEW" else VOID)
        d.text(((x0 + x1) // 2, (lab_top + lab_bottom) // 2), label,
               font=F(6, serif=True),
               fill=CYAN if label == "OVERVIEW" else DIM, anchor="mm")

    # Raised three-stat hero band.
    hero_top, hero_bottom = 156, 210
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
        d.text((cx, hero_top + 21), value, font=F(20, bold=True),
               fill=color, anchor="mm")
        d.text((cx, hero_top + 42), label, font=F(7, serif=True),
               fill=DIM, anchor="mm")
    d.rectangle([10, 215, width - 10, 216], fill=GOLD)

    y = section(d, width, 224, "COMBAT", "1,284 dps", True, expanded=True)
    for text in (
        "LIVE ENCOUNTER · Froglok shin knight · 9s",
        "11.5k damage · 1,284 dps · 21 crits · 3 misses",
        "7 enemies slain · 2 target types",
        "Taken 1,973 · healed 2,410 · received 1,235",
    ):
        d.text((31, y), text, font=F(8), fill=DIM)
        y += 14

    d.text((31, y + 2), "Compared with previous", font=F(8, bold=True), fill=GOLD)
    d.text((width - 17, y + 2), "+178 dps · previous 1,106 dps", font=F(7),
           fill=GOLD_BRIGHT, anchor="ra")
    y += 18
    d.text((31, y + 2), "Observed encounter actors", font=F(8, bold=True), fill=GOLD)
    d.text((width - 17, y + 2), "damage · share · dps", font=F(7),
           fill=GOLD_BRIGHT, anchor="ra")
    y += 18
    for name, value, share in (
        ("Spin", "11.5k · 100% · 1,284/s", 1.0),
        ("Froglok shin knight", "target · 7 slain", .68),
    ):
        d.rectangle([31, y + 11, 31 + int(330 * share), y + 12], fill=CYAN)
        d.text((31, y), name, font=F(8), fill=TEXT)
        d.text((width - 17, y), value, font=F(7), fill=GOLD_BRIGHT, anchor="ra")
        y += 17
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
    d.text((10, height - 16), "live · eqlog_Spin_qeynos.txt", font=F(8), fill=GREEN, anchor="lm")
    d.rectangle([width - 140, height - 25, width - 69, height - 7], fill=RAISED, outline=LINE)
    d.text((width - 104, height - 16), "CLICK-THRU", font=F(6, serif=True), fill=CYAN, anchor="mm")
    d.rectangle([width - 65, height - 25, width - 8, height - 7], fill=RAISED, outline=LINE)
    d.text((width - 36, height - 16), "CHANGE", font=F(7, serif=True), fill=GOLD_BRIGHT, anchor="mm")

    # Mini mode uses the same material and the same pinned ledger vocabulary.
    mini_w, mini_h = 720, 34
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
    controls_left = mini_w - 286
    if x > controls_left - 6:
        raise RuntimeError("720px Loremaster HUD stat cells overlap its controls")
    md.rectangle([controls_left, 4, mini_w - 132, mini_h - 5],
                 fill=RAISED, outline=LINE)
    md.text(((controls_left + mini_w - 132) // 2, mini_h // 2),
            f"LORE LENS  {LORE_HOTKEY}", font=F(6, serif=True),
            fill=CYAN, anchor="mm")
    md.text((mini_w - 126, mini_h // 2), "● LIVE", font=F(6, bold=True),
            fill=GREEN, anchor="rm")
    md.rectangle([mini_w - 122, 4, mini_w - 72, mini_h - 5], fill=RAISED, outline=LINE)
    md.text((mini_w - 97, mini_h // 2), "LOCK", font=F(7, serif=True), fill=DIM, anchor="mm")
    md.rectangle([mini_w - 69, 4, mini_w - 4, mini_h - 5], fill=RAISED, outline=LINE)
    md.text((mini_w - 36, mini_h // 2), "DETAILS", font=F(7, serif=True),
            fill=CYAN, anchor="mm")

    lore_lens = render_lore_lens()
    OUT.mkdir(parents=True, exist_ok=True)
    canvas = Image.new("RGB", (width * 2 + lore_lens.width * 2 + 120,
                                height * 2 + mini_h * 2 + 110), (26, 24, 30))
    canvas.paste(panel.resize((width * 2, height * 2), Image.Resampling.LANCZOS), (40, 40))
    canvas.paste(lore_lens.resize((lore_lens.width * 2, lore_lens.height * 2),
                                  Image.Resampling.LANCZOS),
                 (width * 2 + 80, 40))
    canvas.paste(mini.resize((mini_w * 2, mini_h * 2), Image.Resampling.LANCZOS),
                 (40, height * 2 + 70))
    canvas.save(OUT / "loremaster_panel.png")
    print("wrote docs/previews/loremaster_panel.png")


if __name__ == "__main__":
    main()
