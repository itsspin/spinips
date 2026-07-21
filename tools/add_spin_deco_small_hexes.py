#!/usr/bin/env python3
"""Add 46x46 hex plate frames to spin_deco.tga for the compact equipment rails.

The v3 composition (tools/restyle_inventory.py) seats every slot on a 46px
plate. Rather than letting the client stretch the 56px art, this derives
crisp 46x46 frames from the existing steel and gold hexes and stores them in
the free texture rows at y=64:

    (0, 64)  46x46 steel hex   -> A_SpinHexSm
    (64, 64) 46x46 gold hex    -> A_SpinHexGoldSm

Idempotent: rewriting the same frames is a no-op byte-wise.
Run from repo root:  python3 tools/add_spin_deco_small_hexes.py
"""

from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parent.parent
TGA = REPO / "spinui_reloaded" / "spin_deco.tga"


def main():
    img = Image.open(TGA).convert("RGBA")
    assert img.size == (128, 128), f"unexpected spin_deco.tga size {img.size}"
    for src_x, dst_x in ((0, 0), (64, 64)):
        hex56 = img.crop((src_x, 0, src_x + 56, 56))
        hex46 = hex56.resize((46, 46), Image.LANCZOS)
        img.paste(hex46, (dst_x, 64))
    img.save(TGA)
    print("spin_deco.tga: 46x46 steel + gold hex frames written at y=64")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
