#!/usr/bin/env python3
"""Render the signature Legends Combat Command Center for visual QA.

This is intentionally a deterministic mock: it uses the repository's real
window textures, the canonical resource palette, and the exact audited window
dimensions.  It is documentation and a fast regression surface, not a claim
that SIDL can render outside the EverQuest client.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import render_preview as ui  # noqa: E402
from spinui_theme import (  # noqa: E402
    BG1, BG2, CYAN, ENDUR, GOLD, GOLD_BRIGHT, HP, LINE, MANA, PET,
    TEXT, TEXT_DIM,
)


OUT = REPO / "docs" / "previews" / "combat_command_center.png"
W, H = 1920, 1080


def world() -> Image.Image:
    image = Image.new("RGBA", (W, H), (16, 20, 23, 255))
    draw = ImageDraw.Draw(image)
    for y in range(H):
        t = y / H
        draw.line(
            [(0, y), (W, y)],
            fill=(int(24 + 18 * t), int(31 + 20 * t), int(34 + 13 * t), 255),
        )
    noise = Image.effect_noise((W, H), 22).convert("L")
    noise_rgba = Image.merge("RGBA", (noise, noise, noise, noise.point(lambda p: 18)))
    image = Image.alpha_composite(image, noise_rgba)
    for cx, cy, radius, color in (
        (560, 360, 370, (197, 139, 55, 55)),
        (1180, 290, 460, (48, 122, 122, 48)),
        (900, 850, 420, (106, 47, 31, 35)),
    ):
        glow = Image.new("RGBA", (radius * 2, radius * 2), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse((0, 0, radius * 2, radius * 2), fill=color)
        image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(90)),
                              (cx - radius, cy - radius))
    draw = ImageDraw.Draw(image)
    draw.polygon([(0, 630), (280, 360), (520, 570), (760, 310), (1010, 620),
                  (1280, 390), (1530, 650), (1920, 430), (1920, H), (0, H)],
                 fill=(8, 12, 14, 180))
    draw.text((W // 2, 300), "EVERQUEST LEGENDS", font=ui.F(38, True),
              fill=(255, 255, 255, 25), anchor="mm")
    draw.text((W // 2, 342), "COMBAT COMMAND CENTER", font=ui.F(17, True),
              fill=CYAN + (52,), anchor="mm")
    return image


def player(canvas: Image.Image, x: int, y: int) -> None:
    ui.glass_window(canvas, x, y, 360, 193, alpha=250)
    ui.text(canvas, (x + 12, y + 12), "SPIN", 16, GOLD_BRIGHT, True)
    ui.text(canvas, (x + 348, y + 13), "40  WAR / DRU / BRD", 11,
            TEXT_DIM, anchor="ra")
    ui.text(canvas, (x + 12, y + 31), "CHANNELER  /  INVOCATION: THORNS", 9,
            CYAN, True)
    ui.gauge(canvas, x + 12, y + 49, 336, 18, .93, HP, "HP", "3206 / 3446")
    ui.gauge(canvas, x + 12, y + 73, 336, 14, .72, MANA, "MANA", "1622 / 2251")
    ui.gauge(canvas, x + 12, y + 93, 336, 14, .98, ENDUR, "END", "2212 / 2251")
    ui.gauge(canvas, x + 12, y + 115, 336, 9, .64, PET)
    ui.text(canvas, (x + 12, y + 129), "Gann  /  pet 64%", 10, TEXT_DIM)
    ui.gauge(canvas, x + 12, y + 149, 270, 8, .06, GOLD, ticks=True)
    ui.text(canvas, (x + 348, y + 146), "EXP 6%", 10, GOLD_BRIGHT, anchor="ra")
    ui.gauge(canvas, x + 12, y + 169, 270, 8, .17, CYAN, ticks=True)
    ui.text(canvas, (x + 348, y + 166), "AA 17%", 10, CYAN, anchor="ra")


def target(canvas: Image.Image, x: int, y: int) -> None:
    ui.glass_window(canvas, x, y, 360, 193, alpha=250)
    ui.text(canvas, (x + 12, y + 12), "A FROGLOK SHIN KNIGHT", 14, TEXT, True)
    ui.text(canvas, (x + 348, y + 13), "LV 38  /  WARRIOR", 10,
            GOLD_BRIGHT, anchor="ra")
    ui.gauge(canvas, x + 12, y + 42, 336, 20, .74, HP, "HOSTILE", "74%")
    ui.gauge(canvas, x + 12, y + 70, 336, 12, .36, MANA, "MANA", "36%")
    ui.gauge(canvas, x + 12, y + 88, 336, 12, .81, ENDUR, "END", "81%")
    ui.gauge(canvas, x + 12, y + 112, 336, 10, .42, CYAN)
    ui.text(canvas, (x + 12, y + 126), "CASTING  /  Frogloktik Curse", 10,
            CYAN, True)
    for index in range(7):
        ui.slot(canvas, x + 12 + index * 27, y + 151, 22,
                ui.ICONS[index] if index < 5 else None)
    ui.text(canvas, (x + 348, y + 157), "DEBUFFS", 9, TEXT_DIM, anchor="ra")


def target_of_target(canvas: Image.Image, x: int, y: int) -> None:
    ui.glass_window(canvas, x, y, 240, 53, alpha=248)
    ui.text(canvas, (x + 9, y + 7), "TARGET OF TARGET", 8, TEXT_DIM, True)
    ui.gauge(canvas, x + 9, y + 25, 222, 16, .93, HP, "SPIN", "93%")


def group(canvas: Image.Image, x: int, y: int) -> None:
    ui.glass_window(canvas, x, y, 230, 204, alpha=248)
    ui.text(canvas, (x + 10, y + 8), "GROUP  /  4", 9, GOLD_BRIGHT, True)
    members = (
        ("GRIMLORD", .97, .55, .90),
        ("NEXUS", .83, .72, .70),
        ("OBSCURITY", .64, .91, .50),
    )
    for index, (name, hp, mana, endurance) in enumerate(members):
        row = y + 30 + index * 56
        ui.text(canvas, (x + 10, row), name, 10, TEXT, True)
        ui.text(canvas, (x + 220, row), f"{round(hp * 100)}%", 9,
                TEXT_DIM, anchor="ra")
        ui.gauge(canvas, x + 10, row + 16, 210, 11, hp, HP)
        ui.gauge(canvas, x + 10, row + 30, 210, 6, mana, MANA)
        ui.gauge(canvas, x + 10, row + 39, 210, 6, endurance, ENDUR)
        ui.gauge(canvas, x + 10, row + 48, 128, 4, .60, PET)


def extended_targets(canvas: Image.Image, x: int, y: int) -> None:
    ui.glass_window(canvas, x, y, 178, 300, alpha=248)
    ui.text(canvas, (x + 9, y + 8), "EXTENDED TARGETS", 8, GOLD_BRIGHT, True)
    targets = (
        ("shin knight", .74, "42"), ("tuk shaman", .61, "31"),
        ("tuk warrior", .88, "19"), ("shin shaman", .43, "8"),
        ("tuk knight", .96, "0"), ("shin warrior", .79, "0"),
    )
    for index, (name, health, aggro) in enumerate(targets):
        row = y + 32 + index * 42
        ui.text(canvas, (x + 9, row), f"{index + 1}  {name}", 9, TEXT)
        ui.text(canvas, (x + 168, row), f"{aggro}%", 8,
                GOLD_BRIGHT, anchor="ra")
        ui.gauge(canvas, x + 9, row + 14, 160, 9, health, HP)
        ui.gauge(canvas, x + 9, row + 26, 108, 4, .48, MANA)
        ui.gauge(canvas, x + 121, row + 26, 48, 4, .67, ENDUR)


def effects(canvas: Image.Image, x: int, y: int, title: str,
            names: tuple[str, ...], height: int) -> None:
    ui.std_window(canvas, x, y, 216, height, title=title, alpha=244)
    for index, name in enumerate(names):
        row = y + 24 + index * 22
        ui.slot(canvas, x + 5, row, 20, ui.ICONS[index % len(ui.ICONS)])
        ui.text(canvas, (x + 31, row + 3), name, 10, TEXT)
        ui.text(canvas, (x + 208, row + 3), f"{27 - index}m", 9,
                TEXT_DIM, anchor="ra")


def main() -> int:
    canvas = world()

    effects(canvas, 1704, 8, "SPELL EFFECTS", (
        "Chloroplast", "Talisman of Altuna", "Storm Strength",
        "Guard of the Glade", "Spirit of Wolf", "Clarity", "Temperance",
        "Shield of Barbs", "Regrowth", "Aegolism", "Heroic Bond",
        "Form of Protection", "Speed of the Shissar", "Riotous Health",
        "Symbol of Naltron", "Protection of the Glades", "Aura of Battle",
        "Strength of Nature", "Resist Magic", "Resist Poison",
        "Resist Disease", "Resist Fire", "Resist Cold", "Levitation",
        "Enduring Breath", "Ultravision", "See Invisible", "Rune",
        "Damage Shield", "Alliance",
    ), 712)
    effects(canvas, 1488, 8, "SONG EFFECTS", (
        "Chant of Battle", "Hymn of Restoration", "Psalm of Warmth",
        "Jonthan's Warsong", "Elemental Rhythms", "Guardian Rhythms",
        "Purifying Rhythms", "Anthem de Arms", "Cassindra's Chorus",
        "Verses of Victory", "Selo's Accelerando", "Denon's Disruptive Discord",
        "Chords of Dissonance", "Largo's Absonant Binding", "Kelin's Lucid Lullaby",
    ), 367)

    ui.draw_gems(canvas, 16, 401, 52, 623)
    ui.draw_hotbar_v(canvas, 72, 508, 96, 520, 1)
    extended_targets(canvas, 1510, 728)
    group(canvas, 1690, 728)

    target_of_target(canvas, 1130, 531)
    player(canvas, 620, 594)
    target(canvas, 1068, 594)
    ui.draw_stance(canvas, 620, 794, 440, 56)
    ui.draw_casting(canvas, 1068, 804, 380, 36)
    ui.draw_aggro(canvas, 1228, 846, 220, 48)
    ui.draw_hotbar_h(canvas, 620, 900, 528, 56, 2, 12)
    ui.draw_hotbar_h(canvas, 620, 960, 528, 56, 3, 9)

    # Small signature rail: identity without consuming combat space.
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((620, 1028, 1448, 1069), radius=5,
                           fill=BG1 + (248,), outline=LINE + (255,))
    draw.line((626, 1029, 810, 1029), fill=CYAN + (255,), width=2)
    ui.text(canvas, (634, 1048), "LEGENDS COMMAND CENTER", 10,
            GOLD_BRIGHT, True, anchor="lm")
    ui.text(canvas, (823, 1048), "HP", 9, HP, True, anchor="lm")
    ui.text(canvas, (856, 1048), "MANA", 9, MANA, True, anchor="lm")
    ui.text(canvas, (909, 1048), "ENDURANCE", 9, ENDUR, True, anchor="lm")
    ui.text(canvas, (993, 1048), "PET", 9, PET, True, anchor="lm")
    ui.text(canvas, (1435, 1048), "CURRENT JULY LEGENDS SCHEMA", 9,
            TEXT_DIM, anchor="rm")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(OUT, quality=94)
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
