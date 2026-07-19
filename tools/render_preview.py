#!/usr/bin/env python3
"""Render a full 3440x1440 mock screenshot of Spin's UI Reloaded.

Composites the real skin textures at the real layout coordinates from
tools/generate_spinui_layout.py, with plausible in-game content drawn in,
so the whole interface can be reviewed at a glance without launching EQ.

Outputs:
    docs/previews/spinui_reloaded_3440.png       (full size)
    docs/previews/spinui_reloaded_1720.png       (half size)

Run from repo root:  python3 tools/render_preview.py
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import generate_spinui_layout as LAYOUT  # noqa: E402
from spinui_theme import (BG1, BG2, BG3, CYAN, EMBER, ENDUR, GOLD, GOLD_BRIGHT,
                          GREEN, HP, LINE, LINE_SOFT, MANA, PARCHMENT, PET,
                          TEXT, TEXT_DIM, VOID)

SKIN = REPO / "spinui_reloaded"
OUT = REPO / "docs" / "previews"
W, H = 3440, 1440

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


# ---------------------------------------------------------------------------
# texture piece helpers
# ---------------------------------------------------------------------------
TEX = {}


def tex(name):
    if name not in TEX:
        TEX[name] = Image.open(SKIN / name).convert("RGBA")
    return TEX[name]


def cell(name, x, y, w, h):
    return tex(name).crop((x, y, x + w, y + h))


def tile_h(dst, piece, x0, x1, y):
    x = x0
    while x < x1:
        w = min(piece.width, x1 - x)
        dst.alpha_composite(piece.crop((0, 0, w, piece.height)), (x, y))
        x += w


def tile_v(dst, piece, y0, y1, x):
    y = y0
    while y < y1:
        h = min(piece.height, y1 - y)
        dst.alpha_composite(piece.crop((0, 0, piece.width, h)), (x, y))
        y += h


def tint(img, c):
    r, g, b = c
    out = img.copy()
    px = out.load()
    for yy in range(out.height):
        for xx in range(out.width):
            pr, pg, pb, pa = px[xx, yy]
            px[xx, yy] = (pr * r // 255, pg * g // 255, pb * b // 255, pa)
    return out


def rect(canvas, box, fill=None, outline=None, width=1):
    d = ImageDraw.Draw(canvas)
    d.rectangle([box[0], box[1], box[2] - 1, box[3] - 1], fill=fill, outline=outline, width=width)


def text(canvas, xy, s, size=13, color=TEXT, bold=False, anchor="la"):
    ImageDraw.Draw(canvas).text(xy, s, font=F(size, bold), fill=color, anchor=anchor)


# ---------------------------------------------------------------------------
# window chrome
# ---------------------------------------------------------------------------
def std_window(canvas, x, y, w, h, title=None, alpha=248):
    """Square-frame window: tiled bg + WDT_Def border + optional titlebar."""
    win = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    bg = tex("wnd_bg_light_rock.tga")
    for ty in range(0, h, bg.height):
        for tx in range(0, w, bg.width):
            win.alpha_composite(bg.crop((0, 0, min(bg.width, w - tx), min(bg.height, h - ty))), (tx, ty))
    top_off = 0
    if title is not None:
        tl = cell("window_br_pieces.tga", 9, 19, 13, 16)
        tm = cell("window_br_pieces.tga", 30, 19, 4, 16)
        tr = cell("window_br_pieces.tga", 40, 19, 13, 16)
        win.alpha_composite(tl, (0, 0))
        tile_h(win, tm, 13, w - 13, 0)
        win.alpha_composite(tr, (w - 13, 0))
        top_off = 16
        ImageDraw.Draw(win).text((8, 8), title, font=F(11, True), fill=TEXT, anchor="lm")
        win.alpha_composite(cell("window_pieces01.tga", 100, 90, 12, 12), (w - 15, 2))
        win.alpha_composite(cell("window_pieces01.tga", 136, 90, 12, 12), (w - 29, 2))
    tile_h(win, cell("window_br_pieces.tga", 30, 10, 2, 2), 0, w, top_off if title else 0)
    tile_h(win, cell("window_br_pieces.tga", 40, 80, 2, 2), 0, w, h - 2)
    tile_v(win, cell("window_br_pieces.tga", 33, 40, 3, 2), top_off, h - 2, 0)
    tile_v(win, cell("window_br_pieces.tga", 39, 50, 2, 2), top_off, h - 2, w - 2)
    if alpha < 255:
        a = win.getchannel("A").point(lambda v: v * alpha // 255)
        win.putalpha(a)
    canvas.alpha_composite(win, (x, y))
    return (x + 4, y + top_off + 3, x + w - 4, y + h - 3)


def glass_window(canvas, x, y, w, h, alpha=235):
    """Matte combat glass with a short, luminous signal rail."""
    win = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(win)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=5,
                        fill=BG1 + (alpha,), outline=(1, 3, 5, 255))
    d.rounded_rectangle([1, 1, w - 2, h - 2], radius=4, outline=LINE + (245,))
    rail_end = max(28, int(w * .24))
    d.line([(5, 1), (rail_end, 1)], fill=CYAN + (235,), width=2)
    d.line([(rail_end + 1, 1), (w - 6, 1)], fill=LINE_SOFT + (220,))
    canvas.alpha_composite(win, (x, y))
    return (x + 8, y + 6, x + w - 8, y + h - 6)


def gauge(canvas, x, y, w, h, pct, color, label=None, value=None, ticks=False):
    gb = cell("window_fg_pieces.tga", 108, 0, 100, 10).resize((w, h))
    canvas.alpha_composite(gb, (x, y))
    fw = max(1, int(w * pct))
    src = ("window_pieces01.tga", 108, 30 if ticks else 10, 100, 10)
    gf = tint(cell(*src), color).resize((w, h)).crop((0, 0, fw, h))
    canvas.alpha_composite(gf, (x, y))
    if label:
        text(canvas, (x + 5, y + h // 2), label, size=max(9, h - 4), color=TEXT, bold=True, anchor="lm")
    if value:
        text(canvas, (x + w - 5, y + h // 2), value, size=max(9, h - 4), color=TEXT, anchor="rm")


def slot(canvas, x, y, s=40, icon=None):
    canvas.alpha_composite(cell("window_pieces01.tga", 180, 110, 41, 41).resize((s, s)), (x, y))
    if icon:
        d = ImageDraw.Draw(canvas)
        d.rounded_rectangle([x + 4, y + 4, x + s - 5, y + s - 5], radius=3, fill=icon + (255,))
        d.rectangle([x + 4, y + 4, x + s - 5, y + 4 + max(2, s // 6)], fill=(255, 255, 255, 22))


ICONS = [(96, 60, 40), (60, 96, 140), (140, 60, 96), (60, 120, 80), (150, 120, 50),
         (90, 70, 130), (50, 110, 120), (130, 90, 50), (80, 80, 120), (120, 60, 60)]


# ---------------------------------------------------------------------------
# scene background — a stand-in for the game world
# ---------------------------------------------------------------------------
def world_bg():
    img = Image.new("RGBA", (W, H))
    px = img.load()
    import random
    rng = random.Random(11)
    for y in range(H):
        t = y / H
        base = (
            int(24 + 26 * t),
            int(28 + 22 * t),
            int(30 + 16 * t),
        )
        for x in range(W):
            n = rng.randint(-4, 4)
            px[x, y] = (base[0] + n, base[1] + n, base[2] + n, 255)
    img = img.filter(ImageFilter.GaussianBlur(2))
    d = ImageDraw.Draw(img)
    # torchlight pools so the glass panels read against light and dark
    for cx, cy, r, warm in ((700, 520, 420, True), (2050, 420, 520, False), (2900, 900, 380, True)):
        glow = Image.new("RGBA", (r * 2, r * 2), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        col = (196, 140, 60, 46) if warm else (70, 110, 130, 40)
        gd.ellipse([0, 0, r * 2, r * 2], fill=col)
        glow = glow.filter(ImageFilter.GaussianBlur(80))
        img.alpha_composite(glow, (cx - r, cy - r))
    d.text((W // 2, 640), "EVERQUEST LEGENDS", font=F(44, True), fill=(255, 255, 255, 26), anchor="mm")
    d.text((W // 2, 690), "Spin's UI Reloaded — layout preview", font=F(20), fill=(255, 255, 255, 22), anchor="mm")
    return img


# ---------------------------------------------------------------------------
# window renderers
# ---------------------------------------------------------------------------
def draw_chat(canvas, x, y, w, h, name, lines, input_line=""):
    ix0, iy0, ix1, iy1 = glass_window(canvas, x, y, w, h)
    # tab
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle([x + 8, y, x + 8 + 10 + 8 * len(name), y + 20], radius=3,
                        fill=(16, 22, 29, 255), outline=CYAN + (220,))
    d.line([(x + 10, y + 19), (x + 6 + 8 * len(name) + 10, y + 19)], fill=CYAN + (245,))
    d.line([(x + 9, y + 4), (x + 9, y + 16)], fill=GOLD + (235,), width=2)
    text(canvas, (x + 16, y + 10), name, size=12, color=GOLD_BRIGHT, bold=True, anchor="lm")
    ty = y + 30
    for color, s in lines:
        text(canvas, (x + 14, ty), s, size=14, color=color)
        ty += 21
    # input strip
    d.rectangle([x + 8, y + h - 26, x + w - 9, y + h - 8], fill=(8, 10, 14, 255), outline=LINE_SOFT + (255,))
    if input_line:
        text(canvas, (x + 14, y + h - 17), input_line, size=12, color=DIM, anchor="lm")


def draw_player(canvas, x, y):
    ix0, iy0, ix1, iy1 = std_window(canvas, x, y, 336, 193, title=None)
    text(canvas, (x + 12, y + 12), "Spin", size=17, color=GOLD_BRIGHT, bold=True)
    text(canvas, (x + 324, y + 14), "40  WAR/DRU/BRD", size=12, color=DIM, anchor="ra")
    gauge(canvas, x + 12, y + 42, 312, 18, 0.93, HP, "HP", "3206 / 3446   93%")
    gauge(canvas, x + 12, y + 66, 312, 14, 0.72, MANA, "MANA", "1622 / 2251   72%")
    gauge(canvas, x + 12, y + 86, 312, 14, 0.98, ENDUR, "END", "2212 / 2251   98%")
    gauge(canvas, x + 12, y + 108, 312, 10, 0.64, PET, None, None)
    text(canvas, (x + 12, y + 122), "Gann (pet)  64%", size=10, color=DIM)
    gauge(canvas, x + 12, y + 144, 250, 8, 0.06, GOLD, None, None, ticks=True)
    text(canvas, (x + 324, y + 141), "EXP 6%", size=10, color=DIM, anchor="ra")
    gauge(canvas, x + 12, y + 162, 250, 8, 0.17, CYAN, None, None, ticks=True)
    text(canvas, (x + 324, y + 159), "AA 17%", size=10, color=DIM, anchor="ra")


def draw_target(canvas, x, y):
    std_window(canvas, x, y, 336, 193, title=None)
    text(canvas, (x + 12, y + 10), "a froglok shin knight", size=16, color=TEXT, bold=True)
    gauge(canvas, x + 12, y + 40, 312, 22, 0.74, HP, None, "74%")
    text(canvas, (x + 12, y + 70), "Level 38 · Warrior", size=11, color=DIM)
    # target casting bar
    gauge(canvas, x + 12, y + 92, 312, 10, 0.42, CYAN, None, None)
    text(canvas, (x + 12, y + 106), "Casting: Frogloktik Curse", size=10, color=CYAN)
    # target of target
    text(canvas, (x + 12, y + 130), "ToT:", size=11, color=DIM)
    gauge(canvas, x + 48, y + 128, 200, 12, 0.93, HP, None, None)
    text(canvas, (x + 56, y + 133), "Spin", size=10, color=TEXT, anchor="lm")
    # debuff icon strip
    for i in range(6):
        slot(canvas, x + 12 + i * 26, y + 152, 22, ICONS[i] if i < 4 else None)


def draw_stance(canvas, x, y, w, h):
    glass_window(canvas, x, y, w, h, alpha=248)
    stances = ["Berserk", "Tempest", "Guardian", "Channeler", "Vagabond"]
    bw = (w - 16) // len(stances)
    for i, s in enumerate(stances):
        bx = x + 8 + i * bw
        active = i == 3
        d = ImageDraw.Draw(canvas)
        d.rounded_rectangle([bx, y + 7, bx + bw - 6, y + h - 8], radius=4,
                            fill=BG3 + (255,) if active else BG1 + (255,),
                            outline=(GOLD if active else LINE) + (255,))
        text(canvas, (bx + (bw - 6) // 2, y + h // 2), s, size=11,
             color=GOLD_BRIGHT if active else DIM, bold=active, anchor="mm")


def draw_casting(canvas, x, y, w, h):
    glass_window(canvas, x, y, w, h, alpha=250)
    gauge(canvas, x + 8, y + 8, w - 16, h - 16, 0.61, CYAN, None, None)
    ImageDraw.Draw(canvas).text((x + w // 2, y + h // 2), "Careless Lightning",
                                font=F(12, True), fill=TEXT, anchor="mm",
                                stroke_width=2, stroke_fill=(8, 10, 14))


def draw_aggro(canvas, x, y, w, h):
    glass_window(canvas, x, y, w, h, alpha=248)
    text(canvas, (x + 8, y + 8), "AGGRO", size=9, color=DIM)
    gauge(canvas, x + 8, y + 22, w - 16, 10, 0.87, (240, 140, 50), None, "87%")
    gauge(canvas, x + 8, y + 35, w - 16, 6, 1.0, (150, 60, 40), None, None)


def draw_hotbar_h(canvas, x, y, w, h, page, filled=6):
    glass_window(canvas, x, y, w, h, alpha=242)
    text(canvas, (x + 10, y + h // 2), f"{page}", size=11, color=GOLD_BRIGHT, bold=True, anchor="mm")
    for i in range(12):
        sx = x + 22 + i * 42
        slot(canvas, sx, y + (h - 40) // 2, 40, ICONS[(i * 3 + page) % len(ICONS)] if i < filled else None)
        text(canvas, (sx + 3, y + (h - 40) // 2 + 2), str(i + 1), size=8, color=DIM)


def draw_hotbar_v(canvas, x, y, w, h, page):
    glass_window(canvas, x, y, w, h, alpha=242)
    for i in range(12):
        r, c = divmod(i, 2)
        slot(canvas, x + 6 + c * 44, y + 8 + r * 43, 40, ICONS[(i + page) % len(ICONS)] if i % 3 else None)


def draw_gems(canvas, x, y, w, h):
    glass_window(canvas, x, y, w, h, alpha=242)
    gem_colors = [(150, 40, 40), (40, 90, 160), (170, 130, 40), (60, 140, 70), (120, 60, 140),
                  (40, 130, 140), (160, 80, 40), (90, 90, 120), (140, 40, 90), (40, 110, 100),
                  (110, 110, 40), (70, 70, 160), (150, 100, 60), (60, 60, 60)]
    n = 14
    step = (h - 16) / n
    for i in range(n):
        gy = y + 8 + int(i * step)
        canvas.alpha_composite(cell("window_pieces02.tga", 208, 216, 40, 40), (x + 6, gy))
        d = ImageDraw.Draw(canvas)
        if i != 11:
            d.ellipse([x + 12, gy + 6, x + 39, gy + 33], fill=gem_colors[i] + (255,))
            d.ellipse([x + 19, gy + 10, x + 31, gy + 16], fill=(255, 255, 255, 32))


def draw_group(canvas, x, y):
    members = [("Grimlord", 0.97, 0.55, 0.9), ("Nexus", 0.83, 0.72, 0.7),
               ("Obscurity", 0.64, 0.91, 0.5), ("Mustachendes", 1.0, 0.33, 0.95)]
    hgt = 24 + len(members) * 62 + 8
    std_window(canvas, x, y, 230, hgt, title="Group")
    for i, (nm, hp, mp, en) in enumerate(members):
        my = y + 24 + i * 62
        text(canvas, (x + 10, my + 2), nm, size=12, color=TEXT, bold=True)
        text(canvas, (x + 220, my + 3), f"{int(hp * 100)}%", size=10, color=DIM, anchor="ra")
        gauge(canvas, x + 10, my + 18, 210, 12, hp, HP)
        gauge(canvas, x + 10, my + 33, 210, 7, mp, MANA)
        gauge(canvas, x + 10, my + 42, 210, 7, en, ENDUR)
        gauge(canvas, x + 10, my + 51, 140, 5, 0.5 + 0.1 * i, PET)


def draw_buffs(canvas, x, y, title, rows, w=200, h=None):
    h = h or (24 + rows * 24 + 8)
    std_window(canvas, x, y, w, h, title=title, alpha=240)
    names = ["Chloroplast", "Talisman of Altuna", "Storm Strength", "Guard of the Glade",
             "Chant of Battle", "Hymn of Restoration", "Psalm of Warmth", "Jonthan's Whistling Warsong",
             "Spirit of Wolf", "Clarity", "Temperance", "Shield of Barbs", "Regrowth",
             "Aegolism", "Speed of the Shissar", "Riotous Health", "Heroic Bond", "Form of Protection"]
    for i in range(rows):
        by = y + 26 + i * 24
        slot(canvas, x + 8, by, 20, ICONS[i % len(ICONS)])
        text(canvas, (x + 34, by + 3), names[i % len(names)], size=11, color=TEXT)
        text(canvas, (x + w - 8, by + 4), f"{27 - i}m", size=10, color=DIM, anchor="ra")


def draw_map(canvas, x, y, w, h):
    ix0, iy0, ix1, iy1 = std_window(canvas, x, y, w, h, title="Map — Blackburrow", alpha=228)
    # canvas area
    rect(canvas, (x + 8, y + 24, x + w - 8, y + h - 34), fill=(9, 11, 16, 216), outline=LINE_SOFT + (255,))
    d = ImageDraw.Draw(canvas)
    import random
    rng = random.Random(3)
    # tunnels
    for path_color, pts in (
        (GOLD, [(0.1, 0.8), (0.25, 0.6), (0.4, 0.65), (0.5, 0.45), (0.7, 0.4), (0.85, 0.2)]),
        ((110, 120, 140), [(0.2, 0.9), (0.35, 0.75), (0.55, 0.7), (0.6, 0.5), (0.8, 0.55), (0.9, 0.35)]),
        ((110, 120, 140), [(0.15, 0.3), (0.3, 0.35), (0.45, 0.3), (0.5, 0.45)]),
    ):
        px = [(x + 8 + (w - 16) * a, y + 24 + (h - 58) * b) for a, b in pts]
        d.line(px, fill=path_color + (230,), width=3, joint="curve")
    for _ in range(14):
        mx = x + 20 + rng.random() * (w - 40)
        my = y + 40 + rng.random() * (h - 90)
        d.ellipse([mx - 2, my - 2, mx + 2, my + 2], fill=(200, 200, 210, 200))
    # player arrow
    cx, cy = x + w * 0.5, y + h * 0.47
    d.polygon([(cx, cy - 9), (cx + 6, cy + 7), (cx, cy + 3), (cx - 6, cy + 7)], fill=CYAN + (255,))
    text(canvas, (cx + 12, cy - 6), "Spin", size=11, color=CYAN, bold=True)
    d.ellipse([cx - 26, cy - 26, cx + 26, cy + 26], outline=CYAN + (90,), width=2)
    # POI labels
    text(canvas, (x + 30, y + 44), "to Qeynos Hills", size=10, color=GOLD_BRIGHT)
    text(canvas, (x + w - 30, y + h - 60), "to Everfrost", size=10, color=GOLD_BRIGHT, anchor="ra")
    # toolbar
    for i, lab in enumerate(("+", "−", "⊙", "✎", "⚑")):
        bx = x + 8 + i * 30
        canvas.alpha_composite(cell("window_pieces03.tga", 0, 120, 40, 40).resize((26, 26)), (bx, y + h - 31))
        text(canvas, (bx + 13, y + h - 18), lab, size=12, color=DIM, anchor="mm")
    text(canvas, (x + w - 12, y + h - 18), "Zoom 100%  ·  Level: all", size=10, color=DIM, anchor="rm")


def draw_tracker(canvas, x, y, w, h):
    std_window(canvas, x, y, w, h, title="Tracking", alpha=240)
    rows = [("a gnoll pup", (63, 191, 107)), ("a gnoll guardsman", (65, 199, 228)),
            ("Rungupp", (240, 240, 90)), ("a large rat", (154, 163, 181)),
            ("an elite gnoll guard", (217, 58, 63)), ("Brewmaster Brenzl", (240, 140, 50))]
    for i, (nm, col) in enumerate(rows):
        ry = y + 30 + i * 24
        d = ImageDraw.Draw(canvas)
        d.ellipse([x + 12, ry + 4, x + 20, ry + 12], fill=col + (255,))
        text(canvas, (x + 28, ry), nm, size=12, color=col)
        text(canvas, (x + w - 12, ry + 1), f"{120 + i * 63}m", size=10, color=DIM, anchor="ra")
    # buttons
    for i, lab in enumerate(("Track", "Filters", "Sort: Near")):
        bx = x + 10 + i * 106
        canvas.alpha_composite(cell("window_pieces03.tga", 0, 0, 100, 24), (bx, y + h - 34))
        text(canvas, (bx + 50, y + h - 22), lab, size=11, color=TEXT, anchor="mm")


def draw_loremaster(canvas, x, y, w, h):
    d = ImageDraw.Draw(canvas)
    d.rectangle([x, y, x + w - 1, y + h - 1], fill=LINE + (255,))
    d.rectangle([x + 1, y + 1, x + w - 2, y + h - 2], fill=BG1 + (250,))
    rect(canvas, (x + 1, y + 1, x + w - 1, y + 28), fill=BG2 + (255,))
    d.line([(x + 1, y + 1), (x + 120, y + 1)], fill=CYAN + (255,), width=2)
    d.line([(x + 1, y + 27), (x + w - 2, y + 27)], fill=EMBER + (255,), width=2)
    text(canvas, (x + 10, y + 7), "SPIN'S LOREMASTER", size=13, color=GOLD_BRIGHT, bold=True)
    text(canvas, (x + w - 12, y + 8), "—   ↺   ✕", size=12, color=DIM, anchor="ra")
    text(canvas, (x + 10, y + 35), "SPIN · QEYNOS", size=9, color=PARCHMENT, bold=True)
    text(canvas, (x + w - 10, y + 35), "Blackburrow", size=10, color=DIM, anchor="ra")

    tab_y = y + 51
    tab_w = (w - 20) // 3
    d.rectangle([x + 10, tab_y, x + w - 10, tab_y + 24], fill=VOID + (255,))
    d.rectangle([x + 10, tab_y, x + 10 + tab_w, tab_y + 24], fill=BG3 + (255,))
    for i, (label, color) in enumerate((("FIGHT", CYAN), ("SESSION", DIM), ("RECORDS", DIM))):
        text(canvas, (x + 10 + i * tab_w + tab_w // 2, tab_y + 12), label,
             size=9, color=color, bold=True, anchor="mm")

    hero_y = y + 81
    d.rectangle([x + 10, hero_y, x + w - 10, hero_y + 55], fill=BG3 + (255,))
    d.rectangle([x + 10, hero_y, x + 12, hero_y + 55], fill=CYAN + (255,))
    cells = [("1,284", "FIGHT DPS", GOLD_BRIGHT), ("946", "SESSION", TEXT), ("2,105", "BEST", CYAN)]
    cw = (w - 20) // 3
    for i, (v, lab, col) in enumerate(cells):
        cx = x + 10 + i * cw + cw // 2
        text(canvas, (cx, hero_y + 10), v, size=20, color=col, bold=True, anchor="ma")
        text(canvas, (cx, hero_y + 38), lab, size=9, color=DIM, anchor="ma")
    d.rectangle([x + 10, hero_y + 61, x + w - 10, hero_y + 63], fill=GOLD + (255,))

    cy = hero_y + 73
    text(canvas, (x + 14, cy), "COMBAT · FROGLOK SHIN KNIGHT", size=10, color=CYAN, bold=True)
    text(canvas, (x + w - 13, cy), "11.5k · 1,284/s", size=11, color=TEXT, bold=True, anchor="ra")
    d.line([(x + 10, cy + 17), (x + w - 10, cy + 17)], fill=CYAN + (230,))
    cy += 28
    meters = [
        ("Careless Lightning", "4,449 · 39% · 556/s", .39),
        ("Melee", "3,347 · 29% · 418/s", .29),
        ("Pet (Gann)", "1,541 · 13% · 193/s", .13),
        ("DoT: Flame Lick", "672 · 6% · 84/s", .06),
    ]
    for name, value, share in meters:
        d.rectangle([x + 12, cy, x + w - 12, cy + 22], fill=VOID + (255,))
        d.rectangle([x + 12, cy, x + 12 + int((w - 24) * share), cy + 22], fill=(18, 48, 47, 255))
        d.line([(x + 12, cy), (x + 12 + int((w - 24) * share), cy)], fill=CYAN + (190,))
        text(canvas, (x + 16, cy + 4), name, size=10, color=TEXT)
        text(canvas, (x + w - 16, cy + 4), value, size=10, color=GOLD_BRIGHT, anchor="ra")
        cy += 25
    cy += 4
    for name, value in (("SLAYING", "47 (+9)"), ("SPOILS", "23 items"),
                        ("COIN", "12p 4g"), ("PROGRESSION", "18.6% xp")):
        text(canvas, (x + 14, cy), name, size=9, color=DIM, bold=True)
        text(canvas, (x + w - 14, cy), value, size=10, color=TEXT, anchor="ra")
        d.line([(x + 10, cy + 15), (x + w - 10, cy + 15)], fill=LINE_SOFT + (220,))
        cy += 25
    return


def draw_bag(canvas, x, y, name):
    std_window(canvas, x, y, 96, 194, title=name, alpha=246)
    ic = 0
    for r in range(4):
        for c in range(2):
            ic += 1
            slot(canvas, x + 6 + c * 43, y + 22 + r * 42, 40,
                 ICONS[(r * 2 + c + len(name)) % len(ICONS)] if ic % 3 else None)


def draw_eqmain(canvas, x, y, w, h):
    glass_window(canvas, x, y, w, h, alpha=250)
    labels = ["INV", "AA", "SPELLS", "MAP", "SOC", "OPT", "HELP", "EQ"]
    bw = (w - 12) // len(labels)
    for i, lab in enumerate(labels):
        bx = x + 6 + i * bw
        d = ImageDraw.Draw(canvas)
        d.rounded_rectangle([bx, y + 5, bx + bw - 4, y + h - 6], radius=3,
                            fill=BG2 + (255,), outline=LINE_SOFT + (255,))
        text(canvas, (bx + (bw - 4) // 2, y + h // 2), lab, size=9, color=DIM, anchor="mm")


def draw_compass(canvas, x, y, w=460, h=34):
    glass_window(canvas, x, y, w, h, alpha=235)
    d = ImageDraw.Draw(canvas)
    for i in range(0, w - 20, 12):
        px = x + 10 + i
        tall = (i // 12) % 5 == 0
        d.line([(px, y + h - 10), (px, y + h - (18 if tall else 14))], fill=DIM + (200,))
    for frac, lab in ((0.12, "W"), (0.37, "N"), (0.62, "E"), (0.87, "S")):
        text(canvas, (x + int(w * frac), y + 7), lab, size=12,
             color=GOLD_BRIGHT if lab == "N" else DIM, bold=True, anchor="ma")
    d.polygon([(x + w // 2, y + 2), (x + w // 2 - 5, y - 4), (x + w // 2 + 5, y - 4)], fill=GOLD + (255,))


def draw_songs(canvas, x, y):
    draw_buffs(canvas, x, y, "Song Effects", 6, w=200, h=24 + 6 * 24 + 8)


# ---------------------------------------------------------------------------
def main():
    OUT.mkdir(parents=True, exist_ok=True)
    canvas = world_bg()

    # placements from the layout module (combat-focus preset)
    pl = LAYOUT.preset_placements("combat-focus")

    def xy(name):
        x, y, w, h = pl[name]["_rect"]
        return x, y, w, h

    # chat row
    x, y, w, h = xy("MainChat")
    draw_chat(canvas, x, y, w, h, "Main Chat", [
        (DIM, "Welcome to EverQuest Legends!"),
        ((90, 200, 120), "Frogenstein says out of character, 'and?'"),
        ((90, 200, 120), "Mustachendes says out of character, 'how about jumping straight to Plane of Sky?'"),
        (TEXT, "You have entered Blackburrow."),
        ((240, 200, 90), "--You have looted a Froglok Fine Mesh.--"),
        (DIM, "You receive 2 platinum, 4 gold from the corpse as your split."),
        ((240, 240, 140), "You gain party experience!! (0.42%)"),
        (TEXT, "Your faction standing with Sabertooths of Blackburrow got worse."),
        ((140, 200, 240), "The Marketplace is unavailable at this time."),
        ((90, 200, 120), "Grimlord says out of character, 'LEGENDS!'"),
    ], "/say ")

    x, y, w, h = xy("Chat 1")
    draw_chat(canvas, x, y, w, h, "Social", [
        ((120, 220, 250), "Grimlord tells the group, 'inc froglok shin knight'"),
        ((120, 220, 250), "Nexus tells the group, 'pet on it'"),
        ((230, 120, 220), "Obscurity tells you, 'got a port up when you need it'"),
        ((90, 220, 120), "Spin says, 'pulling left side'"),
        ((90, 200, 240), "Grimlord tells the guild, 'raid forms at 8, bring poison resist'"),
        ((240, 140, 140), "Mustachendes tells the raid, 'tanks east wall'"),
        ((120, 220, 250), "You told Obscurity, 'sweet, after this pull'"),
        ((90, 200, 240), "Frogenstein tells the guild, 'grats Spin on 40!'"),
    ], "/g ")

    x, y, w, h = xy("Chat 2")
    draw_chat(canvas, x, y, w, h, "Combat", [
        (TEXT, "You slash a froglok shin knight for 213 points of damage."),
        ((240, 200, 90), "You slash a froglok shin knight for 692 points of damage. (Critical)"),
        ((150, 170, 230), "Gann hits a froglok shin knight for 118 points of damage."),
        ((120, 200, 240), "You hit a froglok shin knight for 512 points of magic damage by Careless Lightning."),
        ((220, 130, 130), "A froglok shin knight hits YOU for 121 points of damage."),
        (DIM, "You try to slash a froglok shin knight, but miss!"),
        ((150, 170, 230), "A froglok shin knight has taken 75 damage from your Flame Lick."),
        ((240, 200, 90), "You have slain a froglok shin knight!"),
        (TEXT, "You slash a froglok tuk knight for 187 points of damage."),
        ((150, 170, 230), "Gann tells you, 'Attacking a froglok tuk knight Master.'"),
    ], "")

    # left column
    x, y, w, h = xy("CastSpellWnd")
    draw_gems(canvas, x, y, w, h)
    x, y, w, h = xy("HotButtonWnd")
    draw_hotbar_v(canvas, x, y, w, h, 1)
    x, y, w, h = xy("HotButtonWnd11")
    draw_hotbar_v(canvas, x, y, w, h, 4)
    x, y, w, h = xy("TrackingWnd")
    draw_tracker(canvas, x, y, w, h)

    # center cluster
    draw_player(canvas, *xy("PlayerWindow")[:2])
    draw_target(canvas, *xy("TargetWindow")[:2])
    x, y, w, h = xy("StanceWnd")
    draw_stance(canvas, x, y, w, h)
    x, y, w, h = xy("CastingWindow")
    draw_casting(canvas, x, y, w, h)
    x, y, w, h = xy("AggroMeterWnd")
    draw_aggro(canvas, x, y, w, h)
    for name, page, filled in (("HotButtonWnd4", 2, 9), ("HotButtonWnd5", 3, 7),
                               ("HotButtonWnd2", 1, 12), ("HotButtonWnd3", 6, 10),
                               ("HotButtonWnd8", 8, 5), ("HotButtonWnd7", 7, 8),
                               ("HotButtonWnd6", 5, 12), ("HotButtonWnd9", 9, 6),
                               ("HotButtonWnd10", 10, 4)):
        x, y, w, h = xy(name)
        draw_hotbar_h(canvas, x, y, w, h, page, filled)

    # right column
    x, y, w, h = xy("BuffWindow")
    draw_buffs(canvas, x, y, "Spell Effects", 18, w=200, h=712)
    x, y, w, h = xy("ShortDurationBuffWindow")
    draw_songs(canvas, x, y)
    draw_group(canvas, *xy("GroupWindow")[:2])
    x, y, w, h = xy("MapViewWnd")
    draw_map(canvas, x, y, w, h)

    # top / misc
    draw_compass(canvas, *xy("CompassWindow")[:2])
    draw_eqmain(canvas, 3040, 1396, 392, 36)

    # Loremaster on its shelf above the bag dock
    draw_loremaster(canvas, 2792, 676, 400, 460)

    # inventory bags tiled in the dock row
    for i, nm in enumerate(("Backpack", "Backpack", "Large Ba", "Backpack",
                            "Large Ba", "Bag of S", "Large Ba", "Light Bu")):
        draw_bag(canvas, 2500 + i * 100, 1160, nm)

    out_full = OUT / "spinui_reloaded_3440.png"
    canvas.convert("RGB").save(out_full)
    canvas.resize((W // 2, H // 2), Image.LANCZOS).convert("RGB").save(OUT / "spinui_reloaded_1720.png")
    print("wrote", out_full)


if __name__ == "__main__":
    main()
