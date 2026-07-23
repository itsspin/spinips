"""Canonical SpinUI "Obsidian, Venom & Ember" visual tokens.

The palette keeps EverQuest's heraldic gold, but gives interactive and
combat-focused surfaces a more assertive ToxiUI-inspired teal signal.
Renderers and atlas generators import this file so documentation matches the
textures shipped to the client.
"""

from __future__ import annotations

import re

BG0 = (5, 7, 10)          # deepest obsidian / exterior shadow
BG1 = (9, 12, 17)         # matte panel
BG2 = (16, 22, 29)        # raised control
BG3 = (23, 34, 42)        # hover / selected surface
VOID = (4, 6, 9)

LINE_SOFT = (28, 38, 49)
LINE = (48, 63, 78)
LINE_BRIGHT = (78, 101, 119)

GOLD_DEEP = (121, 78, 18)
GOLD = (219, 158, 42)
GOLD_BRIGHT = (250, 205, 95)
EMBER = (229, 100, 45)

CYAN_DEEP = (15, 104, 98)
CYAN = (52, 218, 190)     # venom/arcane interaction accent

TEXT = (238, 242, 243)
TEXT_DIM = (146, 161, 169)
PARCHMENT = (216, 200, 154)

HP = (222, 62, 72)
MANA = (66, 126, 244)
ENDUR = (219, 158, 42)
PET = (112, 137, 158)
GREEN = (66, 207, 139)
RED = HP


ACCENT_KEYS = (
    "CYAN_DEEP", "CYAN", "GOLD_DEEP", "GOLD", "GOLD_BRIGHT", "EMBER",
)


def _rgb(value: tuple[int, int, int]) -> tuple[int, int, int]:
    if len(value) != 3 or any(not isinstance(channel, int) or not 0 <= channel <= 255
                              for channel in value):
        raise ValueError(f"invalid RGB color: {value!r}")
    return value


def rgb_from_hex(value: str) -> tuple[int, int, int]:
    """Parse one CSS-style ``#RRGGBB`` color for Studio/project files."""
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise ValueError(f"invalid color {value!r}; expected #RRGGBB")
    return tuple(int(value[index:index + 2], 16) for index in (1, 3, 5))


def hex_from_rgb(value: tuple[int, int, int]) -> str:
    return "#" + "".join(f"{channel:02x}" for channel in _rgb(value))


def _mix(first: tuple[int, int, int], second: tuple[int, int, int],
         amount: float) -> tuple[int, int, int]:
    return tuple(round(first[index] * (1 - amount) + second[index] * amount)
                 for index in range(3))


DEFAULT_ACCENTS = {
    "CYAN_DEEP": CYAN_DEEP,
    "CYAN": CYAN,
    "GOLD_DEEP": GOLD_DEEP,
    "GOLD": GOLD,
    "GOLD_BRIGHT": GOLD_BRIGHT,
    "EMBER": EMBER,
}


def accent_palette(*, venom: tuple[int, int, int] = CYAN,
                   gold: tuple[int, int, int] = GOLD,
                   ember: tuple[int, int, int] = EMBER) -> dict[str, tuple[int, int, int]]:
    """Create the complete accent ramp used by XML, atlases, and previews.

    The canonical colors return their hand-tuned deep/bright companions.
    Custom choices derive accessible companions deterministically so a Studio
    project can be rebuilt without storing generated binary data.
    """
    venom, gold, ember = _rgb(venom), _rgb(gold), _rgb(ember)
    return {
        "CYAN_DEEP": (
            CYAN_DEEP if venom == CYAN
            else _mix(venom, (0, 0, 0), 0.52)
        ),
        "CYAN": venom,
        "GOLD_DEEP": (
            GOLD_DEEP if gold == GOLD
            else _mix(gold, (0, 0, 0), 0.48)
        ),
        "GOLD": gold,
        "GOLD_BRIGHT": (
            GOLD_BRIGHT if gold == GOLD
            else _mix(gold, (255, 244, 205), 0.42)
        ),
        "EMBER": ember,
    }


def palette_from_hex(values: dict[str, str]) -> dict[str, tuple[int, int, int]]:
    """Expand the three user-facing colors from a Studio JSON document."""
    required = {"venom", "gold", "ember"}
    missing = required - values.keys()
    if missing:
        raise ValueError(f"theme is missing: {', '.join(sorted(missing))}")
    return accent_palette(
        venom=rgb_from_hex(values["venom"]),
        gold=rgb_from_hex(values["gold"]),
        ember=rgb_from_hex(values["ember"]),
    )
