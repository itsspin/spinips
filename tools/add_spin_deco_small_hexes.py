#!/usr/bin/env python3
"""Add compact deco frames to spin_deco.tga.

The v3 composition (tools/restyle_inventory.py) seats every slot on a 46px
plate. Rather than letting the client stretch the 56px art, this derives
crisp 46x46 frames from the existing steel and gold hexes and stores them in
the free texture rows at y=64:

    (0, 64)   46x46 steel hex        -> A_SpinHexSm
    (64, 64)  46x46 gold hex         -> A_SpinHexGoldSm
    (112, 64) 12x12 ember wing gem   -> A_SpinWingGem (stance bar divider)

Idempotent: rewriting the same frames is a no-op byte-wise.
Run from repo root:  python3 tools/add_spin_deco_small_hexes.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parent.parent
TGA = REPO / "spinui_reloaded" / "spin_deco.tga"


def draw_wing_gem(img):
    """Ember diamond that splits the stance/invocation wings."""
    gem = Image.new("RGBA", (12, 12), (0, 0, 0, 0))
    d = ImageDraw.Draw(gem)
    d.polygon([(5, 0), (10, 5), (5, 10), (0, 5)], fill=(219, 158, 42, 255),
              outline=(5, 7, 10, 255))
    d.polygon([(5, 2), (8, 5), (5, 8), (2, 5)], fill=(250, 205, 95, 255))
    d.point((5, 4), fill=(255, 236, 180, 255))
    img.paste(gem, (112, 64))


def main():
    img = Image.open(TGA).convert("RGBA")
    assert img.size == (128, 128), f"unexpected spin_deco.tga size {img.size}"
    for src_x, dst_x in ((0, 0), (64, 64)):
        hex56 = img.crop((src_x, 0, src_x + 56, 56))
        hex46 = hex56.resize((46, 46), Image.LANCZOS)
        img.paste(hex46, (dst_x, 64))
    draw_wing_gem(img)
    img.save(TGA)
    print("spin_deco.tga: 46x46 steel + gold hexes at y=64, 12x12 wing gem at (112,64)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
