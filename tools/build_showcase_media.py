#!/usr/bin/env python3
"""Build privacy-safe README media from real, user-supplied UI captures.

The full HUD capture supplies the showcase hero, while a dedicated lossless
inventory capture preserves the Equipment tab's small text and slot art. The
animated tour uses only real Loremaster captures; its captions and notification
rail are clearly presented as a feature tour rather than continuous gameplay
footage.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont


REPO = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO / "docs" / "screenshots"
GOLD = "#db9e2a"
GOLD_BRIGHT = "#facd5f"
CYAN = "#34dabe"
TEXT = "#eef2f3"
DIM = "#92a1a9"
BG = "#090c11"
PANEL = "#10161d"
LINE = "#303f4e"


def _font(size: int, *, bold: bool = False, serif: bool = False):
    windows = Path(r"C:\Windows\Fonts")
    choices = (
        ("georgiab.ttf" if bold else "georgia.ttf") if serif
        else ("seguisb.ttf" if bold else "segoeui.ttf"),
        "arialbd.ttf" if bold else "arial.ttf",
    )
    for name in choices:
        path = windows / name
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    target_w, target_h = size
    scale = max(target_w / image.width, target_h / image.height)
    resized = image.resize(
        (round(image.width * scale), round(image.height * scale)),
        Image.Resampling.LANCZOS,
    )
    left = (resized.width - target_w) // 2
    top = (resized.height - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))


def build_static(full_capture: Path, inventory_capture: Path,
                 output: Path) -> tuple[Path, Path]:
    source = Image.open(full_capture).convert("RGB")
    if source.size != (3440, 1440):
        raise ValueError(f"expected a 3440x1440 live capture, got {source.size}")

    # The hero is the complete, uncropped 3440x1440 frame — the whole HUD
    # including the chat row — scaled to the README banner width. Nothing is
    # retouched beyond a slight contrast lift.
    hero = source.resize((1600, 670), Image.Resampling.LANCZOS)
    hero = ImageEnhance.Contrast(hero).enhance(1.025)
    hero_path = output / "spinui-live-hero.jpg"
    hero.save(hero_path, "JPEG", quality=90, optimize=True, progressive=True)

    # Preserve the author's dedicated live Equipment capture losslessly so its
    # compact labels and item artwork stay crisp in GitHub's README renderer.
    inventory = Image.open(inventory_capture)
    if inventory.size != (670, 671):
        raise ValueError(
            f"expected a 670x671 live inventory capture, got {inventory.size}")
    inventory_path = output / "inventory-live.png"
    inventory.save(inventory_path, "PNG", optimize=True)
    return hero_path, inventory_path


def _tour_frame(background: Image.Image, panel: Image.Image, phase: int,
                phase_frame: int, phase_frames: int) -> Image.Image:
    canvas = background.copy().convert("RGBA")
    canvas.alpha_composite(Image.new("RGBA", canvas.size, (3, 6, 9, 156)))
    draw = ImageDraw.Draw(canvas)

    # Actual captured Loremaster window, unchanged apart from placement.
    panel = panel.convert("RGBA")
    panel_x, panel_y = 46, 30
    canvas.alpha_composite(panel, (panel_x, panel_y))

    right_x = 492
    draw.rounded_rectangle((right_x, 30, 932, 510), radius=8,
                           fill=PANEL, outline=GOLD, width=2)
    draw.rectangle((right_x, 30, right_x + 4, 510), fill=CYAN)
    draw.text((right_x + 28, 58), "SPIN'S LOREMASTER",
              font=_font(24, bold=True, serif=True), fill=GOLD_BRIGHT)
    draw.text((right_x + 28, 96), "A live chronicle beside EverQuest",
              font=_font(15), fill=DIM)

    headings = (
        ("ENCOUNTER", "Current pull, multi-mob targets, damage and healing"),
        ("SESSION", "Rolling abilities, actors, coin, XP and recent fights"),
        ("DETAILS", "Fast drill-downs without leaving the game"),
    )
    active_name, active_copy = headings[phase]
    y = 154
    for index, (name, copy) in enumerate(headings):
        active = index == phase
        fill = "#17222a" if active else BG
        edge = CYAN if active else LINE
        draw.rounded_rectangle((right_x + 24, y, 908, y + 72), radius=5,
                               fill=fill, outline=edge, width=2 if active else 1)
        draw.text((right_x + 40, y + 13), name, font=_font(16, bold=True),
                  fill=CYAN if active else DIM)
        draw.text((right_x + 40, y + 39), copy, font=_font(12),
                  fill=TEXT if active else DIM)
        y += 86

    draw.text((right_x + 28, 448), "REAL IN-GAME CAPTURES",
              font=_font(12, bold=True), fill=GOLD)
    draw.text((right_x + 28, 474), "Log-only  •  no EQ injection",
              font=_font(12), fill=DIM)

    # A genuine Loremaster alert treatment using values visible in the live
    # session capture. Fade it in/out so the GIF demonstrates notification UX.
    progress = phase_frame / max(1, phase_frames - 1)
    alpha = int(255 * min(1.0, progress * 3, (1.0 - progress) * 3))
    if alpha > 0 and phase in (0, 2):
        banner = Image.new("RGBA", (520, 58), (10, 18, 20, 238))
        banner_draw = ImageDraw.Draw(banner)
        banner_draw.rounded_rectangle((0, 0, 519, 57), radius=5,
                                      outline=CYAN, width=2)
        banner_draw.rectangle((0, 0, 5, 57), fill=CYAN)
        message = ("FIRE DRAKE — 41 DPS · 2 ENEMIES"
                   if phase == 0 else
                   "SESSION UPDATED — 532 DAMAGE · 41 DPS")
        banner_draw.text((25, 17), message, font=_font(15, bold=True), fill=TEXT)
        banner.putalpha(Image.new("L", banner.size, alpha))
        canvas.alpha_composite(banner, (220, 10))

    return canvas.convert("RGB")


def build_animation(hero_path: Path, encounter: Path, session: Path,
                    detail: Path, output: Path) -> Path:
    background = _cover(Image.open(hero_path).convert("RGB"), (960, 540))
    captures = [
        Image.open(encounter).convert("RGB"),
        Image.open(session).convert("RGB"),
        Image.open(detail).convert("RGB").resize((400, 480), Image.Resampling.LANCZOS),
    ]
    if any(image.size != (400, 480) for image in captures):
        raise ValueError("Loremaster live captures must be 400x480")

    frames: list[Image.Image] = []
    phase_frames = 12  # 2 seconds at 6 fps, three phases
    for phase, capture in enumerate(captures):
        for frame_index in range(phase_frames):
            frames.append(_tour_frame(
                background, capture, phase, frame_index, phase_frames))

    # A shared palette keeps the animated README asset small and stable.
    palette = frames[0].quantize(colors=128, method=Image.Quantize.MEDIANCUT)
    indexed = [frame.quantize(palette=palette, dither=Image.Dither.NONE)
               for frame in frames]
    gif_path = output / "loremaster-live-tour.gif"
    indexed[0].save(
        gif_path,
        save_all=True,
        append_images=indexed[1:],
        duration=167,
        loop=0,
        optimize=True,
        disposal=2,
    )
    return gif_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full-capture", type=Path, required=True)
    parser.add_argument("--inventory-capture", type=Path, required=True)
    parser.add_argument("--encounter", type=Path, required=True)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--detail", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    hero, inventory = build_static(
        args.full_capture, args.inventory_capture, args.output)
    animation = build_animation(
        hero, args.encounter, args.session, args.detail, args.output)
    for path in (hero, inventory, animation):
        print(f"{path.relative_to(REPO)}  {path.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
