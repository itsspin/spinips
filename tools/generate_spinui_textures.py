#!/usr/bin/env python3
"""Spin's UI Reloaded — "Obsidian & Ember" texture generator.

Regenerates the shared chrome textures (window borders, titlebars, buttons,
gauges, scrollbars, tabs, backgrounds) that every EverQuest window draws from,
giving the whole interface a dark-glass look with ember-gold and arcane-cyan
accents.

Safety model: each output texture starts as a pixel-perfect copy of the
pristine original (read from git), and only the explicitly listed cells
are repainted.  Icon art (coins, crystals, tab glyphs, book buttons) is never
touched, so nothing the client references can go missing.

Run from the repo root:  python3 tools/generate_spinui_textures.py
"""

import io
import struct
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parent.parent
SKIN = REPO / "spinui_reloaded"
PRISTINE_REF = "250214c"  # initial commit; fall back to worktree bytes if absent

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------
BG0 = (11, 13, 18)        # deepest obsidian
BG1 = (16, 19, 27)        # panel base
BG2 = (24, 28, 39)        # raised glass
BG3 = (31, 36, 50)        # hover glass
LINE_SOFT = (38, 43, 56)
LINE = (58, 65, 82)       # steel outline
LINE_BRIGHT = (84, 93, 116)
GOLD_DEEP = (138, 109, 20)
GOLD = (201, 162, 39)     # ember gold
GOLD_BRIGHT = (232, 197, 92)
CYAN = (65, 199, 228)     # arcane cyan
CYAN_DEEP = (24, 108, 128)
TEXT = (232, 234, 240)
TEXT_DIM = (154, 163, 181)
RED = (217, 58, 63)


def _run_git_show(relpath: str) -> bytes | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO), "show", f"{PRISTINE_REF}:default_modern/{relpath}"],
            capture_output=True,
        )
        if out.returncode == 0 and out.stdout:
            return out.stdout
    except OSError:
        pass
    return None


def load_pristine(name: str) -> Image.Image:
    raw = _run_git_show(name)
    if raw is None:
        raw = (SKIN / name).read_bytes()
    return Image.open(io.BytesIO(raw)).convert("RGBA")


def save_tga(img: Image.Image, path: Path) -> None:
    """Write an uncompressed 32-bit TGA, bottom-left origin, 8 alpha bits —
    the same envelope as the stock skin textures."""
    w, h = img.size
    header = struct.pack(
        "<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, w, h, 32, 0x08
    )
    px = img.load()
    rows = bytearray()
    for y in range(h - 1, -1, -1):  # bottom-up
        for x in range(w):
            r, g, b, a = px[x, y]
            rows += bytes((b, g, r, a))
    path.write_bytes(header + bytes(rows))


# ---------------------------------------------------------------------------
# Drawing helpers (all operate on an RGBA image, coordinates are cell-local)
# ---------------------------------------------------------------------------

def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(len(a)))


def vgrad(img, box, top, bottom):
    x0, y0, x1, y1 = box
    h = max(1, y1 - y0 - 1)
    d = ImageDraw.Draw(img)
    for i, y in enumerate(range(y0, y1)):
        c = lerp(top, bottom, i / h)
        if len(c) == 3:
            c = c + (255,)
        d.line([(x0, y), (x1 - 1, y)], fill=c)


def fill(img, box, color):
    if len(color) == 3:
        color = color + (255,)
    ImageDraw.Draw(img).rectangle([box[0], box[1], box[2] - 1, box[3] - 1], fill=color)


def outline(img, box, color, alpha=255):
    if len(color) == 3:
        color = color + (alpha,)
    ImageDraw.Draw(img).rectangle([box[0], box[1], box[2] - 1, box[3] - 1], outline=color)


def hline(img, x0, x1, y, color, alpha=255):
    if len(color) == 3:
        color = color + (alpha,)
    ImageDraw.Draw(img).line([(x0, y), (x1 - 1, y)], fill=color)


def clear(img, box):
    fill(img, box, (0, 0, 0, 0))


def glass_slab(img, box, state="normal", radius=3):
    """Button plate: dark glass slab with 1px outline + top sheen.
    States: normal, flyby, pressed, pressedflyby, disabled."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    cell = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(cell)
    grads = {
        "normal": (BG2, BG1),
        "flyby": (BG3, BG2),
        "pressed": ((10, 12, 17), (20, 24, 33)),
        "pressedflyby": ((13, 15, 21), (24, 28, 39)),
        "disabled": ((13, 15, 20), (13, 15, 20)),
    }
    lines = {
        "normal": LINE,
        "flyby": CYAN,
        "pressed": GOLD,
        "pressedflyby": GOLD_BRIGHT,
        "disabled": (30, 34, 45),
    }
    top, bot = grads[state]
    for yy in range(h):
        c = lerp(top, bot, yy / max(1, h - 1))
        d.line([(0, yy), (w - 1, yy)], fill=c + (255,))
    # rounded 1px outline
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, outline=lines[state] + (255,))
    # top sheen
    if state in ("normal", "flyby"):
        d.line([(radius, 1), (w - 1 - radius, 1)], fill=(255, 255, 255, 26))
    if state == "flyby":
        d.line([(radius, h - 2), (w - 1 - radius, h - 2)], fill=CYAN + (70,))
    if state in ("pressed", "pressedflyby"):
        d.line([(radius, 1), (w - 1 - radius, 1)], fill=(0, 0, 0, 90))
    # knock out the corners outside the rounding so slabs sit clean
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(cell, (0, 0), mask)
    img.paste(out, (x0, y0))


def chevron(img, box, direction, color, thick=2, pad=None):
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    if pad is None:
        pad = max(3, min(w, h) // 3)
    d = ImageDraw.Draw(img)
    cx, cy = x0 + w // 2, y0 + h // 2
    s = max(2, min(w, h) // 2 - pad + 1)
    if len(color) == 3:
        color = color + (255,)
    if direction == "left":
        pts = [(cx + s // 2, cy - s), (cx - s // 2, cy), (cx + s // 2, cy + s)]
    elif direction == "right":
        pts = [(cx - s // 2, cy - s), (cx + s // 2, cy), (cx - s // 2, cy + s)]
    elif direction == "up":
        pts = [(cx - s, cy + s // 2), (cx, cy - s // 2), (cx + s, cy + s // 2)]
    else:
        pts = [(cx - s, cy - s // 2), (cx, cy + s // 2), (cx + s, cy - s // 2)]
    d.line(pts, fill=color, width=thick, joint="curve")


def glyph_x(img, box, color, pad=3, thick=1):
    x0, y0, x1, y1 = box
    d = ImageDraw.Draw(img)
    if len(color) == 3:
        color = color + (255,)
    d.line([(x0 + pad, y0 + pad), (x1 - 1 - pad, y1 - 1 - pad)], fill=color, width=thick)
    d.line([(x1 - 1 - pad, y0 + pad), (x0 + pad, y1 - 1 - pad)], fill=color, width=thick)


def glyph_minus(img, box, color, pad=3):
    x0, y0, x1, y1 = box
    cy = (y0 + y1) // 2
    hline(img, x0 + pad, x1 - pad, cy, color)
    hline(img, x0 + pad, x1 - pad, cy + 1, color)


def glyph_plus(img, box, color, pad=3):
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    d = ImageDraw.Draw(img)
    if len(color) == 3:
        color = color + (255,)
    d.line([(x0 + pad, cy), (x1 - 1 - pad, cy)], fill=color, width=2)
    d.line([(cx, y0 + pad), (cx, y1 - 1 - pad)], fill=color, width=2)


def glyph_qmark(img, box, color):
    """Tiny pixel question mark."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    cx = x0 + w // 2
    cy = y0 + h // 2
    d = ImageDraw.Draw(img)
    if len(color) == 3:
        color = color + (255,)
    r = max(2, min(w, h) // 4)
    d.arc([cx - r, cy - r - r // 2 - 1, cx + r, cy + r - r // 2 - 1], start=170, end=45, fill=color, width=1)
    d.point((cx, cy + r - 1), fill=color)
    d.point((cx, cy + r + 1), fill=color)


def gauge_fill_strip(img, box, ticks=False):
    """Neutral silver glass strip — the client tints this per gauge."""
    x0, y0, x1, y1 = box
    h = y1 - y0
    rows = []
    for i in range(h):
        t = i / max(1, h - 1)
        if t < 0.12:
            v, a = 250, 255
        elif t < 0.42:
            v, a = int(232 - 50 * (t - 0.12) / 0.30, ), 255
        elif t < 0.80:
            v, a = int(178 - 44 * (t - 0.42) / 0.38), 255
        else:
            v, a = int(128 - 18 * (t - 0.80) / 0.20), 255
        rows.append((v, v, v, a))
    d = ImageDraw.Draw(img)
    for i, c in enumerate(rows):
        d.line([(x0, y0 + i), (x1 - 1, y0 + i)], fill=c)
    # glass sheen band
    d.line([(x0, y0 + 1), (x1 - 1, y0 + 1)], fill=(255, 255, 255, 255))
    if ticks:
        for tx in range(x0 + 9, x1 - 1, 10):
            for yy in range(y0 + 1, y1 - 1):
                r, g, b, a = img.getpixel((tx, yy))
                img.putpixel((tx, yy), (max(0, r - 34), max(0, g - 34), max(0, b - 34), a))


def gauge_bg_strip(img, box):
    x0, y0, x1, y1 = box
    fill(img, box, (7, 9, 13, 235))
    hline(img, x0, x1, y0, (0, 0, 0), 255)
    hline(img, x0, x1, y1 - 1, LINE_SOFT, 255)
    d = ImageDraw.Draw(img)
    d.rectangle([x0, y0, x1 - 1, y1 - 1], outline=(30, 35, 46, 255))
    # inner top shadow
    hline(img, x0 + 1, x1 - 1, y0 + 1, (0, 0, 0), 140)


def recessed_slot(img, box, radius=3):
    """Item-slot: sunken obsidian well."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    cell = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(cell)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=(8, 10, 14, 255))
    for i, a in ((1, 120), (2, 60)):
        d.line([(1 + radius // 2, i), (w - 2 - radius // 2, i)], fill=(0, 0, 0, a))
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, outline=(43, 49, 63, 255))
    d.line([(radius, h - 2), (w - 1 - radius, h - 2)], fill=(255, 255, 255, 14))
    img.paste(cell, (x0, y0))


def soft_border_h(img, box, edge="top"):
    """1-2px window border strips: black outer, steel inner."""
    x0, y0, x1, y1 = box
    h = y1 - y0
    if h == 1:
        fill(img, box, LINE + (255,))
        return
    if edge == "top":
        hline(img, x0, x1, y0, (5, 6, 9), 255)
        for y in range(y0 + 1, y1):
            hline(img, x0, x1, y, LINE, 255)
    else:
        for y in range(y0, y1 - 1):
            hline(img, x0, x1, y, LINE, 255)
        hline(img, x0, x1, y1 - 1, (5, 6, 9), 255)


def soft_border_v(img, box, edge="left"):
    x0, y0, x1, y1 = box
    w = x1 - x0
    d = ImageDraw.Draw(img)
    if w == 1:
        fill(img, box, LINE + (255,))
        return
    if edge == "left":
        d.line([(x0, y0), (x0, y1 - 1)], fill=(5, 6, 9, 255))
        for x in range(x0 + 1, x1):
            d.line([(x, y0), (x, y1 - 1)], fill=LINE + (255,))
    else:
        for x in range(x0, x1 - 1):
            d.line([(x, y0), (x, y1 - 1)], fill=LINE + (255,))
        d.line([(x1 - 1, y0), (x1 - 1, y1 - 1)], fill=(5, 6, 9, 255))


def titlebar_piece(img, box, cap=None, gold_edge=True):
    """16px glass titlebar with ember-gold base line."""
    x0, y0, x1, y1 = box
    vgrad(img, box, (30, 35, 48), (13, 15, 21))
    hline(img, x0, x1, y0, (66, 74, 94), 255)          # crisp top edge
    hline(img, x0, x1, y0 + 1, (255, 255, 255), 22)    # sheen
    if gold_edge:
        hline(img, x0, x1, y1 - 2, GOLD, 200)
        hline(img, x0, x1, y1 - 1, GOLD_DEEP, 160)
    else:
        hline(img, x0, x1, y1 - 1, (5, 6, 9), 255)
    if cap == "left":
        soft_border_v(img, (x0, y0, x0 + 2, y1), "left")
    if cap == "right":
        soft_border_v(img, (x1 - 2, y0, x1, y1), "right")


def rounded_corner(img, box, corner, radius=5, alpha=242, line=LINE, body=BG1):
    """Rounded window-frame corner piece with anti-aliased curve."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    big = 4
    W, H = w * big, h * big
    R = radius * big
    cell = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(cell)
    # draw a big rounded rect positioned so this cell shows the right corner
    rect = {
        "tl": [0, 0, W + R * 3, H + R * 3],
        "tr": [-R * 3 - W, 0, W - 1, H + R * 3],
        "bl": [0, -R * 3 - H, W + R * 3, H - 1],
        "br": [-R * 3 - W, -R * 3 - H, W - 1, H - 1],
    }[corner]
    line_rgba = line if len(line) == 4 else line + (255,)
    d.rounded_rectangle(rect, radius=R, fill=body + (alpha,), outline=line_rgba, width=big)
    cell = cell.resize((w, h), Image.LANCZOS)
    img.paste(cell, (x0, y0))


def rounded_edge(img, box, orient, alpha=242, line=LINE, body=BG1):
    x0, y0, x1, y1 = box
    if orient == "top":
        fill(img, (x0, y0 + 1, x1, y1), body + (alpha,))
        hline(img, x0, x1, y0, line[:3], line[3] if len(line) == 4 else 255)
    elif orient == "bottom":
        fill(img, (x0, y0, x1, y1 - 1), body + (alpha,))
        hline(img, x0, x1, y1 - 1, line[:3], line[3] if len(line) == 4 else 255)
    elif orient == "left":
        fill(img, (x0 + 1, y0, x1, y1), body + (alpha,))
        ImageDraw.Draw(img).line([(x0, y0), (x0, y1 - 1)], fill=(line if len(line) == 4 else line + (255,)))
    else:
        fill(img, (x0, y0, x1 - 1, y1), body + (alpha,))
        ImageDraw.Draw(img).line([(x1 - 1, y0), (x1 - 1, y1 - 1)], fill=(line if len(line) == 4 else line + (255,)))


def scroll_arrow_btn(img, box, direction, state):
    x0, y0, x1, y1 = box
    glass_slab(img, box, state, radius=3)
    col = {"normal": TEXT_DIM, "flyby": CYAN, "pressed": GOLD, "disabled": (70, 76, 92)}[state]
    chevron(img, (x0 + 1, y0 + 1, x1 - 1, y1 - 1), direction, col, thick=2)


def seamless_bg(size, base, amp=3, weave=True, vign=0):
    """Tileable near-flat obsidian background with faint per-pixel grain."""
    import random
    rng = random.Random(7)
    w, h = size
    img = Image.new("RGBA", size)
    px = img.load()
    for y in range(h):
        for x in range(w):
            n = rng.randint(-amp, amp)
            wv = 0
            if weave and ((x + y) % 8 == 0):
                wv = -2
            r = max(0, min(255, base[0] + n + wv))
            g = max(0, min(255, base[1] + n + wv))
            b = max(0, min(255, base[2] + n + wv + (1 if (x * 7 + y * 3) % 13 == 0 else 0)))
            px[x, y] = (r, g, b, 255)
    return img


# ---------------------------------------------------------------------------
# Texture builders
# ---------------------------------------------------------------------------

def build_br_pieces(img):
    """window_br_pieces.tga — WDT_Def borders, titlebar, rounded frames, tabs."""
    # --- square border strips (WDT_Def & friends) ---
    soft_border_h(img, (10, 10, 23, 12), "top")     # A_BorderFrameTopLeft 13x2
    soft_border_h(img, (30, 10, 32, 12), "top")     # A_BorderFrameTop 2x2
    soft_border_h(img, (40, 10, 56, 12), "top")     # A_BorderFrameTopRight 16x2
    titlebar_piece(img, (9, 19, 22, 35), cap="left")    # A_WindowTitleLeft 13x16
    titlebar_piece(img, (30, 19, 34, 35))               # A_WindowTitleMiddle 4x16
    titlebar_piece(img, (40, 19, 53, 35), cap="right")  # A_WindowTitleRight 13x16
    soft_border_v(img, (15, 35, 18, 73), "left")    # A_BorderFrameLeftTop 3x38
    soft_border_v(img, (20, 35, 22, 73), "right")   # A_BorderFrameRightTop 2x38
    soft_border_v(img, (33, 40, 36, 42), "left")    # A_BorderFrameLeft 3x2
    soft_border_v(img, (33, 50, 36, 52), "left")    # A_BorderFrameLeftBottom 3x2
    soft_border_v(img, (39, 50, 41, 52), "right")   # A_BorderFrameRight(+Bottom) 2x2
    soft_border_h(img, (13, 80, 16, 82), "bottom")  # A_BorderFrameBottomLeft 3x2
    soft_border_h(img, (40, 80, 42, 82), "bottom")  # A_BorderFrameBottom 2x2
    soft_border_h(img, (63, 80, 65, 82), "bottom")  # A_BorderFrameBottomRight 2x2

    # --- rounded frame with title (opaque) ---
    fill(img, (99, 110, 100, 111), LINE + (255,))       # WithTitleTopLeft 1x1
    fill(img, (117, 110, 118, 111), LINE + (255,))      # WithTitleTop 1x1
    fill(img, (120, 110, 121, 111), LINE + (255,))      # WithTitleTopRight 1x1
    rounded_corner(img, (99, 113, 104, 129), "tl", radius=5)      # WithTitleLeftTop 5x16
    titlebar_piece(img, (102, 113, 116, 129))                     # RoundedFrameTitleLeft 14x16
    titlebar_piece(img, (117, 113, 119, 129))                     # RoundedFrameTitleMiddle 2x16
    titlebar_piece(img, (120, 113, 134, 129))                     # RoundedFrameTitleRight 14x16
    rounded_corner(img, (132, 113, 137, 129), "tr", radius=5)     # WithTitleRightTop 5x16

    # --- rounded frame (no title) — chat windows live here ---
    rounded_corner(img, (179, 159, 189, 164), "tl", radius=5)     # TopLeft 10x5
    rounded_edge(img, (191, 159, 195, 164), "top")                # Top 4x5
    rounded_corner(img, (201, 159, 211, 164), "tr", radius=5)     # TopRight 10x5
    rounded_corner(img, (215, 160, 220, 165), "bl", radius=4)     # TabLeftBottom 5x5
    rounded_corner(img, (220, 160, 225, 165), "br", radius=4)     # TabRightBottom 5x5
    rounded_corner(img, (206, 164, 211, 172), "tr", radius=5)     # RightTop 5x8
    rounded_corner(img, (179, 165, 184, 171), "tl", radius=5)     # LeftTop 5x6
    rounded_edge(img, (206, 169, 211, 173), "right")              # Right 5x4
    rounded_edge(img, (179, 172, 184, 176), "left")               # Left 5x4
    rounded_corner(img, (206, 174, 211, 180), "br", radius=5)     # RightBottom 5x6
    rounded_corner(img, (179, 177, 184, 181), "bl", radius=5)     # LeftBottom 5x4
    rounded_corner(img, (179, 182, 187, 187), "bl", radius=5)     # BottomLeft 8x5
    rounded_edge(img, (190, 182, 195, 187), "bottom")             # Bottom 5x5
    rounded_corner(img, (201, 182, 211, 187), "br", radius=5)     # BottomRight 10x5


def build_br_pieces_trans(img):
    """window_br_pieces01a.tga — the *Trans* rounded variants.

    These frame the invisible wrappers around the Player/Target windows and
    the hotbar banks (WDT_RoundedTransparent*).  They must stay whisper-thin:
    an opaque body here reads as an empty blocky box above the vitals plates.
    """
    A = 40  # barely-there glass
    SOFT = (58, 65, 82)
    fill(img, (99, 110, 100, 111), SOFT + (140,))
    fill(img, (117, 110, 118, 111), SOFT + (140,))
    fill(img, (120, 110, 121, 111), SOFT + (140,))
    rounded_corner(img, (99, 113, 104, 129), "tl", radius=5, alpha=A, line=SOFT + (150,))
    titlebar_piece(img, (103, 113, 116, 129))
    titlebar_piece(img, (117, 113, 119, 129))
    titlebar_piece(img, (120, 113, 133, 129))
    rounded_corner(img, (132, 113, 137, 129), "tr", radius=5, alpha=A, line=SOFT + (150,))
    rounded_corner(img, (205, 113, 210, 129), "tr", radius=5, alpha=A, line=SOFT + (150,))   # RightTopTransWithArrow
    rounded_corner(img, (251, 124, 256, 130), "br", radius=5, alpha=A, line=SOFT + (150,))   # RightBottomTransNoArrow (x=256 clamps)
    rounded_corner(img, (179, 159, 189, 164), "tl", radius=5, alpha=A, line=SOFT + (150,))
    rounded_edge(img, (191, 159, 195, 164), "top", alpha=A, line=SOFT + (150,))
    rounded_corner(img, (201, 159, 211, 164), "tr", radius=5, alpha=A, line=SOFT + (150,))
    rounded_corner(img, (215, 160, 220, 165), "bl", radius=4, alpha=A, line=SOFT + (150,))
    rounded_corner(img, (220, 160, 225, 165), "br", radius=4, alpha=A, line=SOFT + (150,))
    rounded_corner(img, (206, 164, 211, 172), "tr", radius=5, alpha=A, line=SOFT + (150,))
    rounded_corner(img, (179, 165, 184, 171), "tl", radius=5, alpha=A, line=SOFT + (150,))
    rounded_edge(img, (206, 169, 211, 173), "right", alpha=A, line=SOFT + (150,))
    rounded_edge(img, (179, 172, 184, 176), "left", alpha=A, line=SOFT + (150,))
    rounded_corner(img, (100, 174, 105, 180), "br", radius=5, alpha=A, line=SOFT + (150,))   # RightBottomTrans
    rounded_corner(img, (206, 174, 211, 180), "br", radius=5, alpha=A, line=SOFT + (150,))   # RightBottomTransWithArrow
    rounded_corner(img, (99, 175, 104, 180), "bl", radius=5, alpha=A, line=SOFT + (150,))    # LeftBottomTransWithArrow
    rounded_corner(img, (179, 177, 184, 181), "bl", radius=5, alpha=A, line=SOFT + (150,))
    rounded_corner(img, (179, 182, 187, 187), "bl", radius=5, alpha=A, line=SOFT + (150,))
    rounded_edge(img, (190, 182, 195, 187), "bottom", alpha=A, line=SOFT + (150,))
    rounded_corner(img, (201, 182, 211, 187), "br", radius=5, alpha=A, line=SOFT + (150,))


def build_pieces01(img):
    """window_pieces01.tga — gauges, window buttons, slots, highlights."""
    # Gauge fill strips (client tints these)
    gauge_fill_strip(img, (108, 10, 208, 20))            # A_GaugeFill 100x10
    gauge_fill_strip(img, (108, 30, 208, 40), ticks=True)  # A_GaugeLinesFill
    # Gauge end caps 4x10
    fill(img, (215, 20, 219, 30), (5, 6, 9, 255))
    fill(img, (211, 8, 215, 18), (5, 6, 9, 255))
    # Tile buttons 6x6
    for x, state in ((230, "normal"), (237, "pressed"), (244, "flyby")):
        clear(img, (x, 10, x + 6, 16))
        col = {"normal": TEXT_DIM, "pressed": GOLD, "flyby": CYAN}[state]
        outline(img, (x, 10, x + 6, 16), col)
    # List header 4x16 (Left/Middle/Right share the rect)
    vgrad(img, (30, 20, 34, 36), (33, 38, 52), (18, 21, 30))
    hline(img, 30, 34, 20, (66, 74, 94))
    hline(img, 30, 34, 34, GOLD, 170)
    hline(img, 30, 34, 35, (5, 6, 9))
    # Close / Min / Max buttons 12x12
    for x, state in ((100, "normal"), (112, "flyby"), (124, "pressed")):
        clear(img, (x, 90, x + 12, 102))
        col = {"normal": TEXT_DIM, "flyby": (240, 105, 110), "pressed": RED}[state]
        glyph_x(img, (x, 90, x + 12, 102), col, pad=3, thick=1)
    for x, state in ((136, "normal"), (148, "flyby"), (160, "pressed")):
        clear(img, (x, 90, x + 12, 102))
        col = {"normal": TEXT_DIM, "flyby": CYAN, "pressed": GOLD}[state]
        glyph_minus(img, (x, 90, x + 12, 102), col, pad=3)
    # Recessed box 41x41 (item slots)
    recessed_slot(img, (180, 110, 221, 151))
    # Spellbook slot 48x48
    recessed_slot(img, (130, 170, 178, 218))
    # HighlightThin 3px frame (selection glow) around (132..175, 172..216)
    for box, orient in (
        ((132, 172, 135, 175), None), ((137, 172, 171, 175), None), ((172, 172, 175, 175), None),
        ((132, 177, 135, 211), None), ((172, 177, 175, 211), None),
        ((132, 213, 135, 216), None), ((137, 213, 171, 216), None), ((172, 213, 175, 216), None),
    ):
        fill(img, box, CYAN + (90,))
        # bright core line
    for box in ((132, 173, 135, 174), (137, 173, 171, 174), (172, 173, 175, 174),
                (133, 177, 134, 211), (173, 177, 174, 211),
                (132, 214, 135, 215), (137, 214, 171, 215), (172, 214, 175, 215)):
        fill(img, box, CYAN + (230,))
    # Dividers 4x4 (and cascade menu separator at 80,175)
    for y in (170, 175, 180):
        clear(img, (80, y, 84, y + 4))
        fill(img, (80, y + 1, 84, y + 3), LINE + (255,))
    # Plus / minus buttons 14x16
    for x, state in ((100, "normal"), (114, "flyby"), (128, "pressed"), (142, "pressedflyby"), (156, "disabled")):
        glass_slab(img, (x, 220, x + 14, 236), state, radius=3)
        col = {"normal": TEXT_DIM, "flyby": CYAN, "pressed": GOLD, "pressedflyby": GOLD_BRIGHT, "disabled": (70, 76, 92)}[state]
        glyph_plus(img, (x, 220, x + 14, 236), col, pad=4)
        glass_slab(img, (x, 236, x + 14, 252), state, radius=3)
        glyph_minus(img, (x, 236, x + 14, 252), col, pad=4)
    # Chat QMark 12x21 (y 232-253) and QMark 12x12 (y 236-248) overlap in the
    # atlas — one glyph centered on the shared rows serves both crops.
    for x, state in ((171, "normal"), (183, "flyby"), (195, "pressed")):
        clear(img, (x, 232, x + 12, 253))
        col = {"normal": TEXT_DIM, "flyby": CYAN, "pressed": GOLD}[state]
        glyph_qmark(img, (x, 236, x + 12, 248), col)
    # White pixel must stay white
    fill(img, (4, 251, 5, 252), (255, 255, 255, 255))


def build_pieces03(img):
    """window_pieces03.tga — every standard button plate."""
    rows = (("normal", 0), ("flyby", 24), ("pressed", 48), ("pressedflyby", 72), ("disabled", 96))
    for state, y in rows:
        glass_slab(img, (0, y, 100, y + 24), state)      # A_Btn 100x24
        glass_slab(img, (100, y, 220, y + 24), state)    # A_BigBtn 120x24
    # Square buttons 40x40
    for x, state in ((0, "normal"), (40, "flyby"), (80, "pressed"), (120, "pressedflyby"), (160, "disabled")):
        glass_slab(img, (x, 120, x + 40, 160), state, radius=4)
    # Small buttons 48x24 (normal/flyby stacked, then pressed/pressedflyby/disabled)
    glass_slab(img, (200, 120, 248, 144), "normal")
    glass_slab(img, (200, 144, 248, 168), "flyby")
    glass_slab(img, (200, 168, 248, 192), "pressed")
    glass_slab(img, (200, 192, 248, 216), "pressedflyby")
    glass_slab(img, (200, 216, 248, 240), "disabled")
    # Small square buttons 20x20 — NOTE: overlaps title pieces region on purpose
    # in the atlas; title pieces draw first, squares repaint their cells.
    # Title pieces 56 tall (EQ button banner) — glass band with gold base
    for box in ((0, 160, 48, 216), (49, 160, 115, 216), (116, 160, 118, 216), (121, 160, 169, 216)):
        vgrad(img, box, (26, 30, 42), (12, 14, 20))
        hline(img, box[0], box[2], 160, (66, 74, 94))
        hline(img, box[0], box[2], 213, GOLD, 190)
        hline(img, box[0], box[2], 214, GOLD_DEEP, 150)
        hline(img, box[0], box[2], 215, (5, 6, 9))
    for x, state in ((0, "normal"), (20, "flyby"), (40, "pressed"), (60, "pressedflyby"), (80, "disabled")):
        glass_slab(img, (x, 160, x + 20, 180), state, radius=3)
    # Arrow buttons 30x32
    for x, y, direction, state in (
        (220, 0, "right", "normal"), (220, 32, "right", "flyby"), (220, 64, "right", "pressedflyby"),
        (170, 160, "right", "pressed"), (170, 192, "right", "disabled"),
        (0, 216, "left", "normal"), (30, 216, "left", "flyby"), (60, 216, "left", "pressedflyby"),
        (90, 216, "left", "pressed"), (120, 216, "left", "disabled"),
    ):
        glass_slab(img, (x, y, x + 30, y + 32), state, radius=4)
        col = {"normal": GOLD, "flyby": CYAN, "pressed": GOLD_BRIGHT, "pressedflyby": GOLD_BRIGHT, "disabled": (70, 76, 92)}[state]
        chevron(img, (x + 4, y + 4, x + 26, y + 28), direction, col, thick=2)


def build_fg_pieces(img):
    """window_fg_pieces.tga — gauge bg, scrollbars, tab frames, indicators."""
    # Target-of-target indicator 25x25 at (0,0): gold target diamond
    clear(img, (0, 0, 25, 25))
    d = ImageDraw.Draw(img)
    d.polygon([(12, 3), (21, 12), (12, 21), (3, 12)], outline=GOLD + (255,))
    d.polygon([(12, 6), (18, 12), (12, 18), (6, 12)], outline=GOLD_BRIGHT + (255,))
    d.point((12, 12), fill=GOLD_BRIGHT + (255,))
    # Gauge background 100x10 at (108,0)
    gauge_bg_strip(img, (108, 0, 208, 10))
    # VSB arrows 12x22
    for x, state in ((10, "normal"), (22, "flyby"), (34, "pressed"), (46, "disabled")):
        scroll_arrow_btn(img, (x, 90, x + 12, 112), "up", state)
        scroll_arrow_btn(img, (x, 112, x + 12, 134), "down", state)
    # VSB thumb 12x4 top / 12x4 bottom / 12x2 middle
    for box, part in (((70, 110, 82, 114), "top"), ((70, 120, 82, 124), "bottom"), ((70, 130, 82, 132), "mid")):
        x0, y0, x1, y1 = box
        fill(img, box, (42, 48, 64, 255))
        ImageDraw.Draw(img).line([(x0, y0), (x0, y1 - 1)], fill=LINE + (255,))
        ImageDraw.Draw(img).line([(x1 - 1, y0), (x1 - 1, y1 - 1)], fill=LINE + (255,))
        if part == "top":
            hline(img, x0, x1, y0, LINE_BRIGHT)
        if part == "bottom":
            hline(img, x0, x1, y1 - 1, (5, 6, 9))
    # HSB arrows 22x12
    for y, state in ((140, "normal"), (152, "flyby"), (164, "pressed"), (176, "disabled")):
        scroll_arrow_btn(img, (10, y, 32, y + 12), "left", state)
        scroll_arrow_btn(img, (32, y, 54, y + 12), "right", state)
    # HSB thumb pieces
    for box in ((60, 140, 64, 152), (70, 140, 74, 152), (80, 140, 82, 152)):
        fill(img, box, (42, 48, 64, 255))
        hline(img, box[0], box[2], 140, LINE_BRIGHT)
        hline(img, box[0], box[2], 151, (5, 6, 9))
    # Tab frame pieces (tops rounded 4px)
    rounded_corner(img, (140, 110, 150, 114), "tl", radius=4, body=BG2)   # TabFrameTopLeft 10x4 (+TabBottomLeft)
    rounded_edge(img, (151, 110, 153, 114), "top", body=BG2)              # TabFrameTop 2x4
    rounded_corner(img, (154, 110, 158, 114), "tl", radius=4, body=BG2)   # TabLeftBottom 4x4
    rounded_corner(img, (159, 110, 163, 114), "tr", radius=4, body=BG2)   # TabBottomRight/RightBottom 4x4
    rounded_corner(img, (164, 110, 174, 114), "tr", radius=4, body=BG2)   # TabFrameTopRight 10x4
    rounded_edge(img, (140, 115, 144, 123), "left", body=BG2)             # TabFrameLeftTop 4x8
    rounded_edge(img, (170, 115, 174, 123), "right", body=BG2)            # TabFrameRightTop 4x8
    rounded_edge(img, (140, 124, 144, 126), "left", body=BG2)             # TabFrameLeft 4x2
    rounded_edge(img, (171, 124, 175, 126), "right", body=BG2)            # TabFrameRight 4x2
    rounded_edge(img, (140, 127, 144, 135), "left", body=BG2)             # TabFrameLeftBottom 4x8
    rounded_edge(img, (171, 127, 175, 135), "right", body=BG2)            # TabFrameRightBottom 4x8
    rounded_corner(img, (140, 136, 150, 140), "bl", radius=4, body=BG2)   # TabFrameBottomLeft 10x4
    rounded_edge(img, (151, 136, 153, 140), "bottom", body=BG2)           # TabFrameBottom 2x4
    rounded_corner(img, (164, 136, 174, 140), "br", radius=4, body=BG2)   # TabFrameBottomRight 10x4
    # Inner frame 2px pieces (recessed content wells)
    for box in ((60, 170, 62, 172), (63, 170, 71, 172), (72, 170, 74, 172),
                (60, 173, 62, 181), (72, 173, 74, 181),
                (60, 182, 62, 184), (63, 182, 71, 184), (72, 182, 74, 184)):
        fill(img, box, (30, 35, 46, 255))


def build_pieces02(img):
    """window_pieces02.tga — only the two spell-gem frame cells; the icon art
    (inventory slot glyphs, round EQ buttons) is left untouched."""
    for y0, accent in ((176, False), (216, True)):  # background, holder
        box = (208, y0, 248, y0 + 40)
        clear(img, box)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([208, y0, 247, y0 + 39], radius=4, outline=LINE + (255,))
        d.rounded_rectangle([209, y0 + 1, 246, y0 + 38], radius=3, outline=(5, 6, 9, 200))
        if accent:  # gold corner ticks on the active holder
            for cx, cy, dx, dy in ((210, y0 + 2, 1, 1), (245, y0 + 2, -1, 1),
                                   (210, y0 + 37, 1, -1), (245, y0 + 37, -1, -1)):
                d.line([(cx, cy), (cx + 4 * dx, cy)], fill=GOLD + (230,))
                d.line([(cx, cy), (cx, cy + 4 * dy)], fill=GOLD + (230,))


def build_fg_pieces_black(img):
    for box in ((60, 170, 62, 172), (63, 170, 71, 172), (72, 170, 74, 172),
                (60, 173, 62, 181), (72, 173, 74, 181),
                (60, 182, 62, 184), (63, 182, 71, 184), (72, 182, 74, 184)):
        fill(img, box, (5, 6, 9, 255))


def main():
    out_previews = REPO / "docs" / "previews"
    out_previews.mkdir(parents=True, exist_ok=True)

    jobs = {
        "window_br_pieces.tga": build_br_pieces,
        "window_br_pieces01a.tga": build_br_pieces_trans,
        "window_pieces01.tga": build_pieces01,
        "window_pieces03.tga": build_pieces03,
        "window_fg_pieces.tga": build_fg_pieces,
        "window_fg_pieces_black.tga": build_fg_pieces_black,
        "window_pieces02.tga": build_pieces02,
    }
    for name, fn in jobs.items():
        img = load_pristine(name)
        fn(img)
        save_tga(img, SKIN / name)
        img.save(out_previews / (name.replace(".tga", "_after.png")))
        print("painted", name)

    # Full-tile backgrounds
    bgs = {
        "wnd_bg_light_rock.tga": ((14, 17, 24), 2),
        "wnd_bg_dark_rock.tga": ((10, 12, 17), 2),
        "wnd_bg_light_rock_inner.tga": ((12, 15, 21), 2),
        "wnd_dark_rock.tga": ((9, 11, 15), 1),
        "wnd_fg_dark_rock.tga": ((9, 11, 15), 1),
    }
    for name, (base, amp) in bgs.items():
        src = load_pristine(name)
        img = seamless_bg(src.size, base, amp=amp)
        save_tga(img, SKIN / name)
        img.save(out_previews / (name.replace(".tga", "_after.png")))
        print("painted", name)


if __name__ == "__main__":
    sys.exit(main())
