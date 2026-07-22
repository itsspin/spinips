#!/usr/bin/env python3
"""Make every SpinUI pet layout deterministic, readable, and clickable.

EverQuest subtracts rounded-frame insets before flowing a TileLayoutBox. The
old 84px four-column command grid fit the XML width by only four pixels, so the
Legends client wrapped it to three columns and pushed Inventory plus later
native commands below the clickable panel. Legends places these command
buttons directly; SpinUI now does the same while retaining every native
ScreenID and allowing Legends to inject each label/action.

The compact geometry keeps the proven 356x181 command panel intact. The fixed
default moves its 42-icon effect tray into a 156px side rail, eliminating the
empty-looking slab above Companion without sacrificing Legends capacity.
Resizable top/bottom variants start at one 24px row and grow the buff region,
not the command panel, when the user needs more effect rows. The compact right
variant starts with 21 visible effects and also grows independently.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SKIN = REPO / "spinui_reloaded"
VARIANTS = tuple(SKIN / f"EQUI_PetInfoWindow{suffix}.xml"
                 for suffix in ("", "1", "2", "3"))

BUTTON_SIZE = (78, 23)
COMMAND_POSITIONS = {
    **{
        index: (10 + (index % 4) * 84, 74 + (index // 4) * 25)
        for index in range(12)
    },
    12: (94, 149),
    13: (178, 149),
}
COMMAND_ITEMS = tuple(f"PIW_Pet{i}_Button" for i in range(14))
PET_PANEL_SIZE = (356, 181)
WINDOW_SIZES = {
    "EQUI_PetInfoWindow.xml": (513, 181),
    "EQUI_PetInfoWindow1.xml": (356, 209),
    "EQUI_PetInfoWindow2.xml": (356, 209),
    "EQUI_PetInfoWindow3.xml": (441, 181),
}
BUFF_RECTS = {
    "EQUI_PetInfoWindow.xml": (353, 2, 509, 179),
    "EQUI_PetInfoWindow1.xml": (4, 178, 352, 207),
    "EQUI_PetInfoWindow2.xml": (4, 2, 352, 31),
    "EQUI_PetInfoWindow3.xml": (353, 2, 437, 179),
}
SUBWINDOW_RECTS = {
    "EQUI_PetInfoWindow.xml": (0, 0, 356, 181),
    "EQUI_PetInfoWindow1.xml": (0, 0, 356, 181),
    "EQUI_PetInfoWindow2.xml": (0, 28, 356, 209),
    "EQUI_PetInfoWindow3.xml": (0, 0, 356, 181),
}
BUFF_CAPACITY = {
    "EQUI_PetInfoWindow.xml": 42,
    "EQUI_PetInfoWindow1.xml": 14,
    "EQUI_PetInfoWindow2.xml": 14,
    "EQUI_PetInfoWindow3.xml": 21,
}


def _item_block(text: str, tag: str, item_name: str) -> tuple[re.Match[str], str]:
    pattern = re.compile(
        rf'(<{tag} item="{re.escape(item_name)}">)(.*?)(</{tag}>)', re.S)
    match = pattern.search(text)
    if match is None:
        raise ValueError(f"missing {tag} {item_name}")
    return match, match.group(2)


def _replace_item(text: str, tag: str, item_name: str, body: str) -> str:
    match, _ = _item_block(text, tag, item_name)
    return text[:match.start()] + match.group(1) + body + match.group(3) + text[match.end():]


def _set_scalar(body: str, field: str, value: str) -> str:
    pattern = re.compile(rf'(<{field}>)[^<]*(</{field}>)')
    body, count = pattern.subn(rf'\g<1>{value}\g<2>', body, count=1)
    if count != 1:
        raise ValueError(f"missing field {field}")
    return body


def _set_item_fields(
        text: str, tag: str, item_name: str, fields: dict[str, object]) -> str:
    _, body = _item_block(text, tag, item_name)
    for field, value in fields.items():
        body = _set_scalar(body, field, str(value).lower()
                           if isinstance(value, bool) else str(value))
    return _replace_item(text, tag, item_name, body)


def _set_button(text: str, index: int) -> str:
    item_name = COMMAND_ITEMS[index]
    _, body = _item_block(text, "Button", item_name)
    location = (
        "\n\t\t<Location>\n"
        f"\t\t\t<X>{COMMAND_POSITIONS[index][0]}</X>\n"
        f"\t\t\t<Y>{COMMAND_POSITIONS[index][1]}</Y>\n"
        "\t\t</Location>"
    )
    location_pattern = re.compile(
        r'\s*<Location>.*?</Location>[ \t]*', re.S)
    if location_pattern.search(body):
        body = location_pattern.sub(location, body, count=1)
    else:
        anchor = "\n\t\t<RelativePosition>true</RelativePosition>"
        if anchor not in body:
            raise ValueError(f"missing RelativePosition in {item_name}")
        body = body.replace(anchor, anchor + location, 1)
    body = _set_scalar(body, "CX", str(BUTTON_SIZE[0]))
    body = _set_scalar(body, "CY", str(BUTTON_SIZE[1]))
    return _replace_item(text, "Button", item_name, body)


def _direct_command_pieces(text: str) -> str:
    match, body = _item_block(text, "Screen", "PetInfoSubWindow")
    direct = "\n".join(f"\t\t<Pieces>{name}</Pieces>" for name in COMMAND_ITEMS)
    tile_piece = "\t\t<Pieces>TileLayoutBox:PIW_PetButtons</Pieces>"
    existing = [
        node for node in re.findall(r'<Pieces>([^<]+)</Pieces>', body)
        if node in COMMAND_ITEMS
    ]
    if tile_piece in body:
        body = re.sub(
            r'\t\t<Pieces>TileLayoutBox:PIW_PetButtons</Pieces>[ \t]*',
            direct,
            body,
            count=1,
        )
    elif existing != list(COMMAND_ITEMS):
        raise ValueError("pet subwindow lost its native command pieces")
    body = re.sub(
        r'(<Pieces>PIW_Pet\d+_Button</Pieces>)[ \t]+', r'\1', body)
    return text[:match.start()] + match.group(1) + body + match.group(3) + text[match.end():]


def _remove_flow_grid(text: str) -> str:
    pattern = re.compile(
        r'\n\t<TileLayoutBox item="PIW_PetButtons">.*?</TileLayoutBox>\s*', re.S)
    return pattern.sub("\n\t", text, count=1)


def _polish_buff_host(text: str) -> str:
    _, body = _item_block(text, "Screen", "PIW_BuffWindow")
    body = _set_scalar(body, "Style_Transparent", "true")
    body = _set_scalar(body, "Style_Border", "false")
    return _replace_item(text, "Screen", "PIW_BuffWindow", body)


def _compact_geometry(text: str, filename: str) -> str:
    """Apply the minimum polished geometry for one Legends menu variant."""
    width, height = WINDOW_SIZES[filename]
    text = _set_item_fields(
        text, "Screen", "PetInfoWindow", {"CX": width, "CY": height})

    if filename == "EQUI_PetInfoWindow.xml":
        # Fixed default: trade the empty top slab for a full-capacity side rail.
        text = _set_item_fields(text, "Screen", "PetInfoWindow", {
            "MenuName": "Fixed Size - Buffs on Right",
        })
        text = _set_item_fields(text, "Screen", "PetInfoSubWindow", {
            "TopAnchorOffset": 0,
            "BottomAnchorOffset": 181,
            "RightAnchorOffset": 356,
            "TopAnchorToTop": True,
            "BottomAnchorToTop": True,
            "RightAnchorToLeft": True,
        })
        text = _set_item_fields(text, "Screen", "PIW_BuffWindow", {
            "LeftAnchorOffset": 353,
            "TopAnchorOffset": 2,
            "RightAnchorOffset": 4,
            "BottomAnchorOffset": 2,
            "LeftAnchorToLeft": True,
            "TopAnchorToTop": True,
            "BottomAnchorToTop": False,
        })
        text = _set_item_fields(text, "DragBox", "PIWDragBox1", {
            "TopAnchorOffset": 0,
            "BottomAnchorOffset": 24,
            "RightAnchorOffset": 356,
            "TopAnchorToTop": True,
            "BottomAnchorToTop": True,
            "RightAnchorToLeft": True,
        })
    elif filename == "EQUI_PetInfoWindow1.xml":
        # Bottom: pin the command panel at 181px; extra height adds buff rows.
        text = _set_item_fields(text, "Screen", "PetInfoWindow", {
            "MinVSize": 209,
            "MaxHSize": 356,
        })
        text = _set_item_fields(text, "Screen", "PetInfoSubWindow", {
            "TopAnchorOffset": 0,
            "BottomAnchorOffset": 181,
            "TopAnchorToTop": True,
            "BottomAnchorToTop": True,
        })
        text = _set_item_fields(text, "Screen", "PIW_BuffWindow", {
            "TopAnchorOffset": 178,
            "BottomAnchorOffset": 2,
            "TopAnchorToTop": True,
            "BottomAnchorToTop": False,
        })
    elif filename == "EQUI_PetInfoWindow2.xml":
        # Top: pin the command panel to the bottom; added height grows buffs.
        text = _set_item_fields(text, "Screen", "PetInfoWindow", {
            "MinVSize": 209,
            "MaxHSize": 356,
        })
        text = _set_item_fields(text, "Screen", "PetInfoSubWindow", {
            "TopAnchorOffset": 181,
            "BottomAnchorOffset": 0,
            "TopAnchorToTop": False,
            "BottomAnchorToTop": False,
        })
        text = _set_item_fields(text, "Screen", "PIW_BuffWindow", {
            "TopAnchorOffset": 2,
            "BottomAnchorOffset": 178,
            "TopAnchorToTop": True,
            "BottomAnchorToTop": False,
        })
        text = _set_item_fields(text, "DragBox", "PIWDragBox1", {
            "TopAnchorOffset": 181,
            "BottomAnchorOffset": 163,
            "TopAnchorToTop": False,
            "BottomAnchorToTop": False,
        })
    elif filename == "EQUI_PetInfoWindow3.xml":
        # Right: fixed 356x181 command panel; the buff rail owns extra size.
        text = _set_item_fields(text, "Screen", "PetInfoWindow", {
            "MinHSize": 441,
            "MinVSize": 181,
        })
        text = _set_item_fields(text, "Screen", "PetInfoSubWindow", {
            "TopAnchorOffset": 0,
            "BottomAnchorOffset": 181,
            "RightAnchorOffset": 356,
            "TopAnchorToTop": True,
            "BottomAnchorToTop": True,
            "RightAnchorToLeft": True,
        })
        text = _set_item_fields(text, "Screen", "PIW_BuffWindow", {
            "LeftAnchorOffset": 353,
            "TopAnchorOffset": 2,
            "RightAnchorOffset": 4,
            "BottomAnchorOffset": 2,
            "LeftAnchorToLeft": True,
            "TopAnchorToTop": True,
            "BottomAnchorToTop": False,
        })
        text = _set_item_fields(text, "DragBox", "PIWDragBox1", {
            "TopAnchorOffset": 0,
            "BottomAnchorOffset": 24,
            "RightAnchorOffset": 356,
            "TopAnchorToTop": True,
            "BottomAnchorToTop": True,
            "RightAnchorToLeft": True,
        })
    else:
        raise ValueError(f"unexpected Pet variant: {filename}")

    # Start effects at the visible edge. Side rails flow down first; horizontal
    # trays flow across first so every variant has an intentional reading order.
    text = _set_item_fields(text, "TileLayoutBox", "PIW_BuffButtons", {
        # The top-tray variant grows upward; anchoring its icons to the lower
        # seam avoids recreating an empty band above Companion after resize.
        "AnchorToTop": filename != "EQUI_PetInfoWindow2.xml",
        "HorizontalFirst": filename in (
            "EQUI_PetInfoWindow1.xml", "EQUI_PetInfoWindow2.xml"),
    })
    return text


def restyle(path: Path) -> bool:
    payload = path.read_bytes()
    text = payload.decode("utf-8")
    revised = text
    for index in range(14):
        revised = _set_button(revised, index)
    revised = _direct_command_pieces(revised)
    revised = _remove_flow_grid(revised)
    revised = _polish_buff_host(revised)
    revised = _compact_geometry(revised, path.name)
    ET.fromstring(revised)
    if revised == text:
        return False
    path.write_bytes(revised.encode("utf-8"))
    return True


def main() -> int:
    changed = [path.name for path in VARIANTS if restyle(path)]
    print("pet layouts updated: " + (", ".join(changed) if changed else "none"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
