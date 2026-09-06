#!/usr/bin/env python3
"""Static release gate for the EverQuest Legends Combat Command Center."""

from __future__ import annotations

import configparser
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image

# The effects-row grid is authored once in tools/restyle_combat.py; the audit
# imports it so a geometry change can never pass by editing only one side.
from restyle_combat import (ATTACK_PERIMETER_TEXTURE, ATTACK_RAIL_TEXTURE,
                           EFFECT_CHIP, EFFECT_ICON, EFFECT_NAME_WIDTH,
                           EFFECT_NAME_X, EFFECT_PLATE_BLEED,
                           EFFECT_ROW_WIDTH, EFFECT_TIMER_FONT,
                           EFFECT_TIMER_HALF_WIDTH, EXTENDED_TARGET_COUNT,
                           EXTENDED_TARGET_GUTTER,
                           EXTENDED_TARGET_LAYOUT,
                           EXTENDED_TARGET_MIN_SIZE,
                           EXTENDED_TARGET_ROW_SIZE,
                           EXTENDED_TARGET_TILE,
                           EXTENDED_TARGET_WINDOW_SIZE,
                           PLAYER_MIN_SIZE, TARGET_MIN_SIZE,
                           SPELL_LEDGER_EQTYPES, SPELL_LEDGER_ICON_SIZE,
                           SPELL_LEDGER_MENU_NAME, SPELL_LEDGER_ROW_SIZE,
                           SPELL_LEDGER_VARIANT, SPELL_LEDGER_WINDOW_BOUNDS,
                           SPELL_LEDGER_WINDOW_SIZE)


REPO = Path(__file__).resolve().parent.parent
SKIN = REPO / "spinui_reloaded"
GLASS_SKIN = REPO / "spinui_glass"
COMBAT_SKINS = (SKIN, GLASS_SKIN, REPO / "spinui_pearlescent")
STOCK = Path(r"C:\EQLegends\uifiles\default")

# Pin the client-native attack contract independently of the generator. The
# white RLE source maximizes the native red phase while EQ owns the cadence.
ATTACK_TEXTURE_SIZE = (128, 32)
ATTACK_TEXTURE_COLOR = (255, 255, 255, 255)
ATTACK_PERIMETER_COLORS = {
    (255, 255, 255, 255): 2 * 32 + (128 - 2),
    (255, 255, 255, 0): 128 * 32 - (2 * 32 + (128 - 2)),
}
ATTACK_EDGE_WIDTH = 3
ATTACK_SOURCE_SIZES = {
    "A_AttackIndicator": (128, 32),
    "A_AttackIndicatorTop": (128, 2),
    "A_AttackIndicatorBottom": (128, 2),
    "A_AttackIndicatorLeft": (2, 32),
    "A_AttackIndicatorRight": (128, 2),
    "A_AttackIndicatorFill": (128, 32),
}
ATTACK_GEOMETRY = {
    "A_AttackIndicatorAnimTop": (67, 70, 0, 0),
    "A_AttackIndicatorAnimBottom": (9, 6, 0, 0),
    "A_AttackIndicatorAnimLeft": (67, 6, 0, 3),
    "A_AttackIndicatorAnimRight": (67, 6, 3, 0),
}
ATTACK_ANCHORS = {
    "A_AttackIndicatorAnimTop": ("true", "true", "true", "false"),
    "A_AttackIndicatorAnimBottom": ("false", "false", "true", "false"),
    "A_AttackIndicatorAnimLeft": ("true", "false", "true", "true"),
    "A_AttackIndicatorAnimRight": ("true", "false", "false", "false"),
}
PLAYER_WINDOW_FILES = tuple(
    ["EQUI_PlayerWindow.xml"]
    + [f"EQUI_PlayerWindow{index}.xml" for index in range(1, 7)]
)

TEXT = (241, 231, 212)
TEXT_DIM = (172, 154, 126)
GOLD_BRIGHT = (248, 214, 140)
CYAN = (126, 170, 244)
HP = (222, 62, 72)
MANA = (66, 126, 244)
ENDURANCE = (208, 162, 84)
PET = (152, 132, 104)
BG1 = (19, 14, 9)

FILES = (
    "EQUI_ActionsWindow.xml",
    "EQUI_PlayerWindow.xml",
    "EQUI_TargetWindow.xml",
    "EQUI_TargetOfTargetWindow.xml",
    "EQUI_GroupWindow.xml",
    "EQUI_ExtendedTargetWnd.xml",
    "EQUI_RaidWindow.xml",
    "EQUI_BuffWindow.xml",
    "EQUI_ShortDurationBuffWindow.xml",
    "EQUI_CastingWindow.xml",
    "EQUI_CastSpellWnd.xml",
    "EQUI_HotButtonWnd.xml",
    "EQUI_StanceWnd.xml",
)

CANONICAL_VARIANTS = {
    "EQUI_PlayerWindow.xml": tuple(f"EQUI_PlayerWindow{i}.xml" for i in range(1, 7)),
    "EQUI_TargetWindow.xml": tuple(f"EQUI_TargetWindow{i}.xml" for i in range(1, 7)),
    "EQUI_TargetOfTargetWindow.xml": ("EQUI_TargetOfTargetWindow1.xml",),
    "EQUI_BuffWindow.xml": tuple(f"EQUI_BuffWindow{i}.xml" for i in range(1, 18)),
    "EQUI_ShortDurationBuffWindow.xml": tuple(
        f"EQUI_ShortDurationBuffWindow{i}.xml" for i in range(1, 18)
    ),
    "EQUI_CastingWindow.xml": ("EQUI_CastingWindow1.xml",),
    "EQUI_CastSpellWnd.xml": tuple(f"EQUI_CastSpellWnd{i}.xml" for i in range(1, 3)),
}


def fail(message: str) -> None:
    raise AssertionError(message)


def root_for(name: str) -> ET.Element:
    return root_for_path(SKIN / name)


def root_for_path(path: Path) -> ET.Element:
    try:
        return ET.parse(path).getroot()
    except ET.ParseError as exc:
        fail(f"invalid XML {path}: {exc}")


def item(root: ET.Element, tag: str, name: str) -> ET.Element:
    node = root.find(f".//{tag}[@item='{name}']")
    if node is None:
        fail(f"missing {tag} item {name}")
    return node


def child_text(node: ET.Element, path: str) -> str:
    value = node.findtext(path)
    if value is None:
        fail(f"missing {path} in {node.tag} {node.get('item', '')}")
    return value.strip()


def child_int(node: ET.Element, path: str) -> int:
    return int(child_text(node, path))


def dimensions(node: ET.Element) -> tuple[int, int]:
    return child_int(node, "Size/CX"), child_int(node, "Size/CY")


def dimensions_at(node: ET.Element, path: str) -> tuple[int, int]:
    return child_int(node, f"{path}/CX"), child_int(node, f"{path}/CY")


def color(node: ET.Element, container: str) -> tuple[int, int, int]:
    return tuple(child_int(node, f"{container}/{channel}")
                 for channel in ("R", "G", "B"))


def require_binding(root: ET.Element, tag: str, name: str,
                    screen_id: str | None = None,
                    eq_type: int | None = None) -> ET.Element:
    node = item(root, tag, name)
    if screen_id is not None and child_text(node, "ScreenID") != screen_id:
        fail(f"{name} ScreenID changed")
    if eq_type is not None and child_int(node, "EQType") != eq_type:
        fail(f"{name} EQType changed")
    return node


def require_fill(root: ET.Element, name: str,
                 expected: tuple[int, int, int]) -> None:
    node = item(root, "Gauge", name)
    if color(node, "FillTint") != expected:
        fail(f"{name} lost canonical fill color")


def anchored_rect(node: ET.Element, width: int,
                  height: int) -> tuple[int, int, int, int]:
    """Resolve EQ's edge-anchor offsets into a concrete LTRB rectangle."""
    top_offset, bottom_offset, left_offset, right_offset = (
        child_int(node, tag) for tag in (
            "TopAnchorOffset", "BottomAnchorOffset",
            "LeftAnchorOffset", "RightAnchorOffset",
        )
    )
    top = (top_offset if child_text(node, "TopAnchorToTop") == "true"
           else height - top_offset)
    bottom = (bottom_offset if child_text(node, "BottomAnchorToTop") == "true"
              else height - bottom_offset)
    left = (left_offset if child_text(node, "LeftAnchorToLeft") == "true"
            else width - left_offset)
    right = (right_offset if child_text(node, "RightAnchorToLeft") == "true"
             else width - right_offset)
    return left, top, right, bottom


def audit_attack_indicator_contract() -> None:
    """Prove every theme/variant keeps a visible native attack perimeter."""
    for skin in COMBAT_SKINS:
        for texture_name, expected_colors in (
                (ATTACK_RAIL_TEXTURE,
                 [(128 * 32, ATTACK_TEXTURE_COLOR)]),
                (ATTACK_PERIMETER_TEXTURE, ATTACK_PERIMETER_COLORS)):
            texture_path = skin / texture_name
            payload = texture_path.read_bytes()
            if len(payload) < 18:
                fail(f"{skin.name}/{texture_name} has no complete TGA header")
            width = int.from_bytes(payload[12:14], "little")
            height = int.from_bytes(payload[14:16], "little")
            if ((width, height) != ATTACK_TEXTURE_SIZE
                    or payload[2] != 10
                    or payload[16] != 32
                    or payload[17] & 0x0F != 8):
                fail(
                    f"{skin.name}/{texture_name} lost the 32-bit RLE "
                    f"envelope (type={payload[2]}, size={width}x{height}, "
                    f"depth={payload[16]}, descriptor={payload[17]})"
                )
            with Image.open(texture_path) as source:
                colors = source.convert("RGBA").getcolors(
                    maxcolors=width * height)
            if isinstance(expected_colors, dict):
                actual_colors = {
                    color_value: count for count, color_value in (colors or [])
                }
                if actual_colors != expected_colors:
                    fail(f"{skin.name}/{texture_name} lost perimeter alpha")
            elif colors != expected_colors:
                fail(f"{skin.name}/{texture_name} lost opaque white rail")

        for filename in PLAYER_WINDOW_FILES:
            path = skin / filename
            root = root_for_path(path)
            label = f"{skin.name}/{filename}"

            for name, expected_size in ATTACK_SOURCE_SIZES.items():
                animation = item(root, "Ui2DAnimation", name)
                frames = animation.findall("Frames")
                if len(frames) != 1:
                    fail(f"{label} {name} must have one native frame")
                frame = frames[0]
                expected_texture = (ATTACK_PERIMETER_TEXTURE
                                    if name == "A_AttackIndicatorFill"
                                    else ATTACK_RAIL_TEXTURE)
                if (child_text(animation, "Cycle") != "false"
                        or child_text(frame, "Texture") != expected_texture
                        or dimensions_at(frame, "Size") != expected_size
                        or (child_int(frame, "Location/X"),
                            child_int(frame, "Location/Y")) != (0, 0)
                        or frame.find("Duration") is not None):
                    fail(f"{label} {name} violates the native flash contract")

            edge_nodes: dict[str, ET.Element] = {}
            for name, expected_offsets in ATTACK_GEOMETRY.items():
                edge = item(root, "StaticAnimation", name)
                edge_nodes[name] = edge
                suffix = name.removeprefix("A_AttackIndicatorAnim")
                actual_offsets = tuple(child_int(edge, tag) for tag in (
                    "TopAnchorOffset", "BottomAnchorOffset",
                    "LeftAnchorOffset", "RightAnchorOffset",
                ))
                actual_anchors = tuple(child_text(edge, tag) for tag in (
                    "TopAnchorToTop", "BottomAnchorToTop",
                    "LeftAnchorToLeft", "RightAnchorToLeft",
                ))
                if (child_text(edge, "ScreenID") != name
                        or child_text(edge, "Animation")
                        != f"A_AttackIndicator{suffix}"
                        or child_text(edge, "AutoDraw") != "false"
                        or child_text(edge, "AutoStretch") != "true"
                        or actual_offsets != expected_offsets
                        or actual_anchors != ATTACK_ANCHORS[name]):
                    fail(
                        f"{label} {name} lost its native state binding or "
                        f"outer-seam geometry: {actual_offsets} {actual_anchors}"
                    )

            fill = item(root, "StaticAnimation", "A_AttackIndicatorAnimFill")
            if (child_text(fill, "ScreenID") != "A_AttackIndicatorAnimFill"
                    or child_text(fill, "Animation") != "A_AttackIndicatorFill"
                    or child_text(fill, "AutoDraw") != "false"
                    or child_text(fill, "AutoStretch") != "true"
                    or tuple(child_int(fill, tag) for tag in (
                        "TopAnchorOffset", "BottomAnchorOffset",
                        "LeftAnchorOffset", "RightAnchorOffset",
                    )) != (70, 6, 0, 0)):
                fail(f"{label} attack fill lost its topmost perimeter binding")

            window = item(root, "Screen", "PlayerWindow")
            if dimensions(window) != (360, 193):
                fail(f"{label} changed the canonical player-window size")
            minimum = (
                child_int(window, "MinHSize"), child_int(window, "MinVSize")
            )
            if minimum != PLAYER_MIN_SIZE:
                fail(f"{label} changed the safe minimum size: {minimum}")
            root_pieces = [(piece.text or "").strip()
                           for piece in window.findall("Pieces")]
            expected_pieces = [
                f"A_AttackIndicatorAnim{edge}"
                for edge in ("Top", "Bottom", "Left", "Right", "Fill")
            ]
            subwindow = item(root, "Screen", "PlayerSubWindow")
            subwindow_pieces = [(piece.text or "").strip()
                                for piece in subwindow.findall("Pieces")]
            if (root_pieces[-5:] != expected_pieces
                    or any(root_pieces.count(piece) != 1
                           for piece in expected_pieces)
                    or any(piece in subwindow_pieces
                           for piece in expected_pieces)):
                fail(
                    f"{label} attack controls must be unique direct "
                    "PlayerWindow children in the final compositor slot"
                )

            subwindow_offsets = tuple(child_int(subwindow, tag) for tag in (
                "TopAnchorOffset", "BottomAnchorOffset",
                "LeftAnchorOffset", "RightAnchorOffset",
            ))
            if subwindow_offsets != (70, 9, 3, 3):
                fail(
                    f"{label} themed player frame can cover the native "
                    f"attack perimeter: {subwindow_offsets}"
                )

            buff_window = item(root, "Screen", "PW_BuffWindow")
            buff_offsets = (
                child_int(buff_window, "TopAnchorOffset"),
                child_int(buff_window, "BottomAnchorOffset"),
            )
            if buff_offsets != (0, 70):
                fail(
                    f"{label} buff strip can overlap the top attack rail: "
                    f"{buff_offsets}"
                )

            combat_state = item(root, "Button", "PW_CombatStateAnim")
            if child_text(combat_state, "Style_Transparent") != "false":
                fail(f"{label} changed the stock combat-state button contract")

            # Resolve real rectangles at both supported extremes.  This catches
            # gaps, clipped corners, and one-sided anchor regressions that an
            # XML symbol-table check cannot see.
            for size in ((360, 193), PLAYER_MIN_SIZE):
                frame_width, frame_height = size
                expected_rects = {
                    "A_AttackIndicatorAnimTop":
                        (0, 67, frame_width, 70),
                    "A_AttackIndicatorAnimBottom":
                        (0, frame_height - 9, frame_width,
                         frame_height - 6),
                    "A_AttackIndicatorAnimLeft":
                        (0, 67, 3, frame_height - 6),
                    "A_AttackIndicatorAnimRight":
                        (frame_width - 3, 67, frame_width,
                         frame_height - 6),
                }
                actual_rects = {
                    name: anchored_rect(node, frame_width, frame_height)
                    for name, node in edge_nodes.items()
                }
                if actual_rects != expected_rects:
                    fail(
                        f"{label} attack perimeter is discontinuous at "
                        f"{frame_width}x{frame_height}: {actual_rects}"
                    )


def audit_player_and_target() -> None:
    player = root_for("EQUI_PlayerWindow.xml")
    require_binding(player, "Gauge", "Player_HP", "PlayerHP", 1)
    require_binding(player, "Gauge", "Player_Mana", "PlayerMana", 2)
    require_binding(player, "Gauge", "Player_Fatigue", "PlayerFatigue", 3)
    exp = require_binding(player, "Gauge", "PW_ExpGauge", "ExpGauge", 4)
    alt_adv = require_binding(
        player, "Gauge", "PW_AltAdvGauge", "AltAdvGauge", 5
    )
    for gauge in (exp, alt_adv):
        if child_text(gauge, "DrawLinesFill") != "false":
            fail(
                f"{gauge.get('item')} must show total 0-100 progression "
                "without the 20% sub-tick overlay"
            )
        if child_text(gauge, "GaugeDrawTemplate/Lines") != "A_GaugeLines":
            fail(f"{gauge.get('item')} lost its fixed progression ticks")
    shared_progression_paths = (
        "GaugeOffsetY",
        "AutoStretch",
        "LeftAnchorOffset",
        "RightAnchorOffset",
        "RightAnchorToLeft",
        "GaugeDrawTemplate/Background",
        "GaugeDrawTemplate/Fill",
        "GaugeDrawTemplate/Lines",
    )
    for path in shared_progression_paths:
        if child_text(exp, path) != child_text(alt_adv, path):
            fail(f"player EXP and AA horizontal rendering differs at {path}")
    exp_height = (child_int(exp, "BottomAnchorOffset")
                  - child_int(exp, "TopAnchorOffset"))
    aa_height = (child_int(alt_adv, "BottomAnchorOffset")
                 - child_int(alt_adv, "TopAnchorOffset"))
    if (exp_height, aa_height) != (10, 11):
        fail(
            "player AA gauge must retain its one-pixel fractional-scale "
            f"weight correction: EXP {exp_height}, AA {aa_height}"
        )
    require_binding(player, "Gauge", "PW_Castspell_Gauge", "PW_Castspell_Gauge", 7)
    require_binding(player, "Label", "Player_ManaLabel", "ManaLabel", 1009)
    require_binding(player, "Label", "PW_MPNumbers", "PW_MPNumbers", 128)
    require_binding(player, "Label", "PW_ENNumbers", "PW_ENNumbers", 129)
    stance = require_binding(
        player, "Label", "PW_StanceLabel", "PW_StanceLabel", 1026
    )
    invocation = require_binding(
        player, "Label", "PW_InvocationInfo", "PW_InvocationInfo", 1017
    )
    require_binding(player, "Label", "PW_AggroPctPlayerLabel",
                    "PW_AggroPctPlayerLabel", 306)
    require_binding(player, "Label", "PW_AggroNameSecondaryLabel",
                    "PW_AggroNameSecondaryLabel", 304)
    require_binding(player, "Label", "PW_AggroPctSecondaryLabel",
                    "PW_AggroPctSecondaryLabel", 308)
    require_binding(player, "DragBox", "PW_DragBox", "PW_DragBox")
    require_binding(player, "DragBox", "PW_DragBox2", "PW_DragBox2")
    item(player, "Screen", "IW_Gauges_Background")
    player_subwindow = item(player, "Screen", "PlayerSubWindow")
    if child_text(player_subwindow, "Style_Border") != "true":
        fail("PlayerSubWindow must remain the visible compact command frame")
    player_window = item(player, "Screen", "PlayerWindow")
    if dimensions(player_window) != (360, 193):
        fail("PlayerWindow must remain 360x193")
    if (child_text(player_window, "Style_Border") != "false" or
            child_text(player_window, "Style_Transparent") != "true"):
        fail("PlayerWindow reintroduced the faint maximum-canvas perimeter")
    if (child_text(player_window, "Style_ClientMovable") != "true" or
            child_text(player_window, "ClickThroughEmptyBuffs") != "true"):
        fail("PlayerWindow interaction or empty-buff click-through changed")
    if child_text(player_window, "Style_Sizable") != "true":
        fail("PlayerWindow must remain user-resizable")
    player_min_size = (
        child_int(player_window, "MinHSize"),
        child_int(player_window, "MinVSize"),
    )
    if player_min_size != PLAYER_MIN_SIZE:
        fail(f"PlayerWindow resize bounds changed: {player_min_size}")
    for label, expected, alignment in (
        (stance, (96, 112, 6, 132), "false"),
        (invocation, (96, 112, 232, 6), "true"),
    ):
        actual = tuple(
            child_int(label, tag) for tag in (
                "TopAnchorOffset", "BottomAnchorOffset",
                "LeftAnchorOffset", "RightAnchorOffset",
            )
        )
        if actual != expected:
            fail(f"{label.get('item')} bottom-rail geometry changed")
        if child_text(label, "AlignRight") != alignment:
            fail(f"{label.get('item')} bottom-rail alignment changed")
        if child_int(label, "Font") != 4 or child_text(label, "NoWrap") != "true":
            fail(f"{label.get('item')} lost its readable single-line treatment")
    for name, expected in (("Player_HP", HP), ("Player_Mana", MANA),
                           ("Player_Fatigue", ENDURANCE), ("Pet_HP", PET),
                           ("PW_ExpGauge", ENDURANCE),
                           ("PW_AltAdvGauge", CYAN),
                           ("PW_Castspell_Gauge", CYAN)):
        require_fill(player, name, expected)

    target = root_for("EQUI_TargetWindow.xml")
    require_binding(target, "Gauge", "Target_HP", "TargetHP", 6)
    require_binding(target, "Gauge", "Target_Mana", "TargetMana", 186)
    require_binding(target, "Gauge", "Castspell_Gauge", "Castspell_Gauge", 187)
    require_binding(target, "Gauge", "Target_Endurance", "TargetEndurance", 188)
    require_binding(target, "Label", "Target_ENDNumbers", "Target_ENDNumbers", 1013)
    require_binding(target, "DragBox", "TW_DragBox", "TW_DragBox")
    item(target, "Screen", "Target_Gauges_Background")
    target_subwindow = item(target, "Screen", "TargetSubWindow")
    if child_text(target_subwindow, "Style_Border") != "true":
        fail("TargetSubWindow must remain the visible compact command frame")
    target_window = item(target, "Screen", "TargetWindow")
    if dimensions(target_window) != (360, 193):
        fail("TargetWindow must remain 360x193")
    if (child_text(target_window, "Style_Border") != "false" or
            child_text(target_window, "Style_Transparent") != "true"):
        fail("TargetWindow reintroduced the faint maximum-canvas perimeter")
    if (child_text(target_window, "Style_ClientMovable") != "true" or
            child_text(target_window, "ClickThroughEmptyBuffs") != "true"):
        fail("TargetWindow interaction or empty-buff click-through changed")
    if child_text(target_window, "Style_Sizable") != "true":
        fail("TargetWindow must remain user-resizable")
    target_min_size = (
        child_int(target_window, "MinHSize"),
        child_int(target_window, "MinVSize"),
    )
    if target_min_size != TARGET_MIN_SIZE:
        fail(f"TargetWindow resize bounds changed: {target_min_size}")
    for name, expected in (("Target_HP", HP), ("Target_HP_NameOnly", HP),
                           ("TTargetOfTarget_HP", HP),
                           ("Target_Mana", MANA),
                           ("Target_Endurance", ENDURANCE),
                           ("Castspell_Gauge", CYAN)):
        require_fill(target, name, expected)

    tot = root_for("EQUI_TargetOfTargetWindow.xml")
    require_binding(tot, "Gauge", "TargetOfTarget_HP", "TargetOfTarget_HP", 27)
    item(tot, "Screen", "ToTW_Background")
    tot_window = item(tot, "Screen", "TargetOfTargetWindow")
    if dimensions(tot_window) != (240, 53):
        fail("TargetOfTargetWindow must remain compact at 240x53")
    if child_int(tot_window, "MinVSize") != 53 or child_int(tot_window, "MaxVSize") != 53:
        fail("TargetOfTargetWindow vertical clamp changed")


def audit_group_and_extended_targets() -> None:
    group = root_for("EQUI_GroupWindow.xml")
    window = require_binding(group, "Screen", "GroupWindow", "GroupWindow")
    if dimensions(window) != (230, 70):
        fail("GroupWindow root must remain 230x70")
    for index in range(1, 12):
        require_binding(group, "Gauge", f"GW_Gauge{index}", f"Gauge{index}", 1000 + index)
        require_binding(group, "Gauge", f"GW_ManaGauge{index}",
                        f"ManaGauge{index}", 1100 + index)
        require_binding(group, "Gauge", f"GW_STAGauge{index}",
                        f"STAGauge{index}", 1200 + index)
        require_binding(group, "Gauge", f"GW_PetGauge{index}",
                        f"PetGauge{index}", 1300 + index)
        require_binding(group, "Label", f"GW_HPLabel{index}",
                        f"HPLabel{index}", 1400 + index)
        for role in ("Tank", "Assist", "Puller", "MarkNPC"):
            require_binding(group, "Button", f"GW_GroupRole{role}{index}",
                            f"GroupRole{role}{index}")
        for name, expected in ((f"GW_Gauge{index}", HP),
                               (f"GW_ManaGauge{index}", MANA),
                               (f"GW_STAGauge{index}", ENDURANCE),
                               (f"GW_PetGauge{index}", PET)):
            require_fill(group, name, expected)
        size = window.find(f"GroupSize{index}")
        if size is None:
            fail(f"GroupSize{index} missing")
        if child_int(size, "CX") != 230 or child_int(size, "CY") != 120 + (index - 1) * 42:
            fail(f"GroupSize{index} geometry changed")
    if child_int(window.find("GroupSize3"), "CY") != 204:  # type: ignore[arg-type]
        fail("four-player Legends group composition is no longer 204px tall")

    extended = root_for("EQUI_ExtendedTargetWnd.xml")
    extended_window = item(extended, "Screen", "ExtendedTargetWnd")
    if dimensions(extended_window) != EXTENDED_TARGET_WINDOW_SIZE:
        fail(
            "ExtendedTargetWnd default size changed: "
            f"{dimensions(extended_window)}"
        )
    if tuple(child_int(extended_window, field) for field in (
            "MinHSize", "MinVSize")) != EXTENDED_TARGET_MIN_SIZE:
        fail("ExtendedTargetWnd no longer preserves one complete compact row")
    if child_text(extended_window, "Layout") != EXTENDED_TARGET_LAYOUT:
        fail("ExtendedTargetWnd lost the parent layout that instantiates its rows")
    if [node.text for node in extended_window.findall("Pieces")] != [
            f"TileLayoutBox:{EXTENDED_TARGET_TILE}"]:
        fail("ExtendedTargetWnd must mount only its responsive target tile")
    for field, expected in (
        ("Style_Sizable", "true"),
        ("Style_VScroll", "true"),
        ("Style_AutoVScroll", "true"),
        ("Style_Titlebar", "true"),
        ("Style_Minimizebox", "true"),
        ("KeepOnScreen", "true"),
    ):
        if child_text(extended_window, field) != expected:
            fail(f"ExtendedTargetWnd {field} changed")
    if child_text(extended_window, "DrawTemplate") != "WDT_Rounded":
        fail("ExtendedTargetWnd lost its SpinUI frame")

    layout_rules = item(extended, "LayoutVertical", EXTENDED_TARGET_LAYOUT)
    if (child_text(layout_rules, "ResizeVertical") != "true"
            or child_text(layout_rules, "ResizeHorizontal") != "true"):
        fail("ExtendedTargetWnd can no longer resize on both axes")

    expected_tile_members = [
        f"Screen:ETW_Ext{index}" for index in range(EXTENDED_TARGET_COUNT)
    ]
    tile = item(extended, "TileLayoutBox", EXTENDED_TARGET_TILE)
    if [node.text for node in tile.findall("Pieces")] != expected_tile_members:
        fail("extended-target tile order no longer covers slots 0 through 22")
    if (child_int(tile, "Spacing"), child_int(tile, "SecondarySpacing")) != (
            0, EXTENDED_TARGET_GUTTER):
        fail("extended-target row or column spacing changed")
    for field in (
            "HorizontalFirst", "FirstPieceTemplate", "AnchorToTop",
            "AnchorToLeft"):
        if child_text(tile, field) != "true":
            fail(f"extended-target tile {field} must remain true")
    if tile.find("SnapToChildren") is not None:
        fail("extended-target tile must remain a shrinkable scroll viewport")
    for field in ("Style_VScroll", "Style_AutoVScroll"):
        if tile.find(field) is not None:
            fail(f"extended-target tile must leave {field} to its parent window")

    local_geometry = {
        "ETW_AggroPct": (20, 32),
        "ETW_Gauge": (1, 23),
        "ETW_ManaGauge": (24, 28),
        "ETW_CastGauge": (24, 28),
        "ETW_STAGauge": (29, 31),
        "ETW_HPLabel": (14, 30),
        "ETW_HPPercLabel": (14, 30),
        "ETW_Role": (3, 19),
    }
    row_stems = tuple(local_geometry)
    mounted_controls = []
    for index in range(EXTENDED_TARGET_COUNT):
        def extended_eq_type(legacy_start: int, legends_start: int) -> int:
            return (legacy_start + index if index < 20
                    else legends_start + index - 20)

        bound = {
            "ETW_AggroPct": require_binding(
                extended, "Label", f"ETW_AggroPct{index}",
                f"ETW_AggroPct{index}", extended_eq_type(314, 391),
            ),
            "ETW_Gauge": require_binding(
                extended, "Gauge", f"ETW_Gauge{index}",
                f"ETW_Gauge{index}", extended_eq_type(42, 151),
            ),
            "ETW_ManaGauge": require_binding(
                extended, "Gauge", f"ETW_ManaGauge{index}",
                f"ETW_ManaGauge{index}", extended_eq_type(62, 161),
            ),
            "ETW_CastGauge": require_binding(
                extended, "Gauge", f"ETW_CastGauge{index}",
                f"ETW_CastGauge{index}", 189 + index,
            ),
            "ETW_STAGauge": require_binding(
                extended, "Gauge", f"ETW_STAGauge{index}",
                f"ETW_STAGauge{index}", extended_eq_type(82, 171),
            ),
            "ETW_HPLabel": require_binding(
                extended, "Label", f"ETW_HPLabel{index}",
                f"ETW_HPLabel{index}", extended_eq_type(151, 361),
            ),
            "ETW_HPPercLabel": require_binding(
                extended, "Label", f"ETW_HPPercLabel{index}",
                f"ETW_HPPercLabel{index}",
            ),
            "ETW_Role": require_binding(
                extended, "Button", f"ETW_Role{index}",
                f"ETW_Role{index}",
            ),
        }
        for stem, node in bound.items():
            actual = (
                child_int(node, "TopAnchorOffset"),
                child_int(node, "BottomAnchorOffset"),
            )
            if actual != local_geometry[stem]:
                fail(f"{stem}{index} left its responsive row geometry")

        row = require_binding(
            extended, "Screen", f"ETW_Ext{index}", f"ETW_Ext{index}"
        )
        if dimensions(row) != EXTENDED_TARGET_ROW_SIZE:
            fail(f"extended-target row {index} geometry changed")
        expected_row_members = [f"{stem}{index}" for stem in row_stems]
        actual_row_members = [node.text for node in row.findall("Pieces")]
        if actual_row_members != expected_row_members:
            fail(f"extended-target row {index} membership changed")
        mounted_controls.extend(actual_row_members)

        for name, expected in ((f"ETW_Gauge{index}", HP),
                               (f"ETW_ManaGauge{index}", MANA),
                               (f"ETW_CastGauge{index}", CYAN),
                               (f"ETW_STAGauge{index}", ENDURANCE)):
            require_fill(extended, name, expected)

    expected_controls = [
        f"{stem}{index}"
        for index in range(EXTENDED_TARGET_COUNT)
        for stem in row_stems
    ]
    if mounted_controls != expected_controls:
        fail("extended-target controls are missing, duplicated, or reordered")

    # The 24px allowance models the rounded frame plus active vertical
    # scrollbar at the narrow default.  These breakpoints prove widening the
    # outer window creates useful columns while 170px remains one column.
    breakpoint_results = []
    for outer_width in (170, 320, 470, 620):
        usable_width = outer_width - 24
        columns = max(1, (usable_width + EXTENDED_TARGET_GUTTER) // (
            EXTENDED_TARGET_ROW_SIZE[0] + EXTENDED_TARGET_GUTTER
        ))
        breakpoint_results.append((columns, math.ceil(
            EXTENDED_TARGET_COUNT / columns
        )))
    if breakpoint_results != [(1, 23), (2, 12), (3, 8), (4, 6)]:
        fail(f"extended-target responsive breakpoints drifted: {breakpoint_results}")


def audit_effect_row_geometry(root: ET.Element, prefix: str, label: str) -> None:
    """Prove the row's two client-owned overlays stay where they belong.

    One button width controls both the centered countdown and the stretched
    beneficial/detrimental background plate, so the chip has to stay icon
    sized (or the plate becomes a slab of flat colour beside every icon)
    while a worst-case countdown still cannot reach the name column.
    """
    chip = item(root, "Button", f"{prefix}_Player_Buff_Template")
    chip_size = dimensions(chip)
    if chip_size != EFFECT_CHIP:
        fail(f"{label} effect chip must stay {EFFECT_CHIP[0]}x{EFFECT_CHIP[1]}")
    if dimensions_at(chip, "DecalSize") != EFFECT_ICON:
        fail(f"{label} icon geometry drifted")
    if (child_int(chip, "DecalOffset/X"),
            child_int(chip, "DecalOffset/Y")) != (0, 0):
        fail(f"{label} icon left the chip's left edge")
    if child_int(chip, "Font") < EFFECT_TIMER_FONT:
        fail(f"{label} countdown fell below the accessible font tier")
    if chip.findtext("FontShadow") != "true":
        fail(f"{label} countdown lost its shadow")
    if color(chip, "TextColor") != GOLD_BRIGHT:
        fail(f"{label} countdown lost its ember-gold separation from names")
    if chip_size[0] - EFFECT_ICON[0] > EFFECT_PLATE_BLEED:
        fail(f"{label} chip outgrew its icon; the client's plate becomes a slab")
    center = chip_size[0] // 2
    if center + EFFECT_TIMER_HALF_WIDTH > EFFECT_NAME_X:
        fail(f"{label} countdown can spill onto the effect-name column")


def audit_effects_casting_and_bars() -> None:
    buffs = root_for("EQUI_BuffWindow.xml")
    buff_window = item(buffs, "Screen", "BuffWindow")
    if dimensions(buff_window) != (EFFECT_ROW_WIDTH, 640):
        fail(f"BuffWindow must remain {EFFECT_ROW_WIDTH}x640")
    if child_text(buff_window, "Style_Transparent") != "true":
        fail("BuffWindow must not paint an opaque maximum-slot canvas")
    if child_text(buff_window, "DrawTemplate") != "WDT_RoundedTransparentNoArrow":
        fail("BuffWindow lost its slim transparent command header")
    if child_text(buff_window, "Style_Border") != "false":
        fail("BuffWindow reintroduced the full-height maximum-slot perimeter")
    if child_text(buff_window, "ClickThroughEmptyBuffs") != "true":
        fail("BuffWindow empty slots must remain click-through")
    buff_background = item(buffs, "Screen", "BW_Background")
    if (child_text(buff_background, "Style_Transparent") != "true" or
            child_text(buff_background, "Style_Border") != "false"):
        fail("BuffWindow inset background became opaque")
    audit_effect_row_geometry(buffs, "BW", "BuffWindow")
    if dimensions(item(buffs, "Screen", "BW_00_Screen")) != (EFFECT_ROW_WIDTH, 20):
        fail("BuffWindow label row template drifted")
    for index in range(30):
        label = require_binding(buffs, "Label", f"BW_Buff{index}",
                                f"Buff{index}Label", 500 + index)
        if child_int(label, "Font") < 3:
            fail(f"BW_Buff{index} fell below the accessible font tier")
        if dimensions(label) != (EFFECT_NAME_WIDTH, 18):
            fail(f"BW_Buff{index} geometry drifted")
        if (child_int(label, "Location/X"),
                child_int(label, "Location/Y")) != (EFFECT_NAME_X, 1):
            fail(f"BW_Buff{index} lost its timer-clear alignment")
        item(buffs, "Screen", f"BW_{index:02d}_Screen")

    songs = root_for("EQUI_ShortDurationBuffWindow.xml")
    song_window = item(songs, "Screen", "ShortDurationBuffWindow")
    if dimensions(song_window) != (EFFECT_ROW_WIDTH, 324):
        fail(f"ShortDurationBuffWindow must remain {EFFECT_ROW_WIDTH}x324")
    if child_text(song_window, "Style_Transparent") != "true":
        fail("ShortDurationBuffWindow must not paint an opaque maximum-slot canvas")
    if child_text(song_window, "DrawTemplate") != "WDT_RoundedTransparentNoArrow":
        fail("ShortDurationBuffWindow lost its slim transparent command header")
    if child_text(song_window, "Style_Border") != "false":
        fail("ShortDurationBuffWindow reintroduced the full-height maximum-slot perimeter")
    if child_text(song_window, "ClickThroughEmptyBuffs") != "true":
        fail("ShortDurationBuffWindow empty slots must remain click-through")
    song_background = item(songs, "Screen", "SDBW_Background")
    if (child_text(song_background, "Style_Transparent") != "true" or
            child_text(song_background, "Style_Border") != "false"):
        fail("ShortDurationBuffWindow inset background became opaque")
    audit_effect_row_geometry(songs, "SDBW", "ShortDurationBuffWindow")
    if dimensions(item(songs, "Screen", "SDBW_00_Screen")) != (EFFECT_ROW_WIDTH, 20):
        fail("ShortDurationBuffWindow label row template drifted")
    for index in range(15):
        screen_id = f"SDBuff{index}Label"
        label = require_binding(songs, "Label", f"SDBW_Buff{index}",
                                screen_id, 600 + index)
        if child_int(label, "Font") < 3:
            fail(f"SDBW_Buff{index} fell below the accessible font tier")
        if dimensions(label) != (EFFECT_NAME_WIDTH, 18):
            fail(f"SDBW_Buff{index} geometry drifted")
        if (child_int(label, "Location/X"),
                child_int(label, "Location/Y")) != (EFFECT_NAME_X, 1):
            fail(f"SDBW_Buff{index} lost compact row alignment")
        item(songs, "Screen", f"SDBW_{index:02d}_Screen")

    casting = root_for("EQUI_CastingWindow.xml")
    require_binding(casting, "Gauge", "Casting_Gauge", "Gauge", 7)
    require_binding(casting, "Label", "Casting_SpellName", None, 134)
    item(casting, "Screen", "Cast_Gauge_Background")
    drag_box = require_binding(casting, "DragBox", "CWDragBox", "CWDragBox")
    expected_drag_anchors = {
        "TopAnchorToTop": "true",
        "BottomAnchorToTop": "false",
        "LeftAnchorToLeft": "true",
        "RightAnchorToLeft": "false",
    }
    for tag, expected in expected_drag_anchors.items():
        if child_text(drag_box, tag) != expected:
            fail(f"CastingWindow drag surface lost {tag}={expected}")
    for tag in ("TopAnchorOffset", "BottomAnchorOffset",
                "LeftAnchorOffset", "RightAnchorOffset"):
        if child_int(drag_box, tag) != 0:
            fail(f"CastingWindow drag surface must cover the full client area: {tag}")
    casting_window = item(casting, "Screen", "CastingWindow")
    if dimensions(casting_window) != (380, 36):
        fail("CastingWindow must remain 380x36")
    if child_text(casting_window, "Style_Titlebar") != "false":
        fail("CastingWindow titlebar reintroduced visual jitter")
    casting_pieces = [
        node.text for node in casting_window.findall("Pieces") if node.text
    ]
    if not casting_pieces or casting_pieces[-1] != "DragBox:CWDragBox":
        fail("CastingWindow must mount its drag surface above the visual pieces")
    require_fill(casting, "Casting_Gauge", CYAN)

    spells = root_for("EQUI_CastSpellWnd.xml")
    item(spells, "Ui2DAnimation", "Spell_Gem_Background")
    for index in range(14):
        gem = require_binding(
            spells, "SpellGem", f"CSPW_Spell{index}", f"CSPW_Spell{index}"
        )
        if dimensions(gem) != (40, 40):
            fail(f"CSPW_Spell{index} socket geometry drifted")
        if (child_int(gem, "SpellIconOffsetX"),
                child_int(gem, "SpellIconOffsetY")) != (2, 2):
            fail(f"CSPW_Spell{index} icon is no longer centered")
        if (child_int(gem, "SpellIconSizeX"),
                child_int(gem, "SpellIconSizeY")) != (36, 36):
            fail(f"CSPW_Spell{index} icon no longer fills its socket")
    spell_window = item(spells, "Screen", "CastSpellWnd")
    if dimensions(spell_window) != (52, 623):
        fail("CastSpellWnd must expose all 14 Legends gems at 52x623")
    if child_text(spell_window, "MenuName") != "Legends Spell Deck":
        fail("the icon-only Legends Spell Deck must remain the default display")

    hotbars = root_for("EQUI_HotButtonWnd.xml")
    for index in range(1, 13):
        button = require_binding(hotbars, "HotButton", f"HB_Button{index}",
                                 f"HB_Button{index}")
        if child_int(button, "Font") < 2:
            fail(f"HB_Button{index} key label is too small")
    for index in range(1, 12):
        name = "HotButtonWnd" if index == 1 else f"HotButtonWnd{index}"
        item(hotbars, "Screen", name)

    stance = root_for("EQUI_StanceWnd.xml")
    stance_label = require_binding(stance, "Label", "SW_StanceLabel", None, 1026)
    invocation_label = require_binding(stance, "Label", "SW_InvocationLabel", None, 1017)
    if child_int(stance_label, "Font") < 3 or child_int(invocation_label, "Font") < 3:
        fail("stance/invocation labels fell below the accessible font tier")
    stance_window = item(stance, "Screen", "StanceWnd")
    if dimensions(stance_window) != (440, 56):
        fail("StanceWnd must remain 440x56")
    if (child_int(stance_window, "MinHSize"),
            child_int(stance_window, "MinVSize")) != (20, 20):
        fail("StanceWnd must accept stock-sized saved layout frames")
    for orientation in ("SW_HorizontalOrientationTemplate",
                        "SW_VerticalOrientationTemplate"):
        node = item(stance, "Screen", orientation)
        if (child_int(node, "RightAnchorOffset"),
                child_int(node, "BottomAnchorOffset")) != (0, 0):
            fail(f"{orientation} lost the July stance-layout contract")
    pieces = {node.text for node in stance_window.findall("Pieces") if node.text}
    if "Screen:SW_DisplayStanceInvocation" not in pieces:
        fail("active stance bar lost its stance/invocation text rail")

    # Twin-wing rail: static captions bracket the bar, the dynamic names sit
    # inside their wing, and the ember gem marks the split.
    for caption, expected_text in (("SW_StanceCaption", "STANCE"),
                                   ("SW_InvocationCaption", "INVOCATION")):
        node = item(stance, "Label", caption)
        if child_text(node, "Text") != expected_text:
            fail(f"{caption} lost its wing caption text")
    item(stance, "StaticAnimation", "SW_WingGem")
    item(stance, "Ui2DAnimation", "A_SpinWingGem")
    rail = item(stance, "Screen", "SW_DisplayStanceInvocation")
    rail_pieces = [node.text for node in rail.findall("Pieces") if node.text]
    for piece in ("SW_StanceCaption", "SW_InvocationCaption", "SW_WingGem"):
        if piece not in rail_pieces:
            fail(f"stance rail lost wing piece {piece}")
    if rail_pieces.index("SW_WingGem") > rail_pieces.index("SW_StanceLabel"):
        fail("wing gem must render beneath the dynamic stance name")
    if (child_int(stance_label, "LeftAnchorOffset") != 48
            or child_int(invocation_label, "RightAnchorOffset") != 64):
        fail("dynamic stance/invocation names left their wings")


def audit_raid_and_actions() -> None:
    raid = root_for("EQUI_RaidWindow.xml")
    for index in range(1, 13):
        require_binding(raid, "Button", f"RAID_Group{index}Button",
                        f"RAID_Group{index}Button")
    raid_window = item(raid, "Screen", "RaidWindow")
    if child_text(raid_window, "Text") != "RAID // EIGHT":
        fail("RaidWindow lost its Legends eight-player identity")

    actions = root_for("EQUI_ActionsWindow.xml")
    # July 14 Legends macro/social browser and searchable action lists.
    for tag, name in (
        ("Editbox", "ACTW_MP_FilterEditBox"),
        ("Button", "ACTW_MP_NewMacroBtn"),
        ("Listbox", "ACTW_MP_MacrosList"),
        ("STMLbox", "ACTW_MP_DescriptionStmlBox"),
        ("Page", "ACTW_MacrosPage"),
        ("Editbox", "ASP_FilterEditBox"),
        ("Listbox", "ASP_SpellsList"),
        ("Listbox", "ADP_SkillSelectorList"),
        ("Listbox", "AAP_SkillSelectorList"),
        ("TabBox", "ACTW_ActionsSubwindows"),
    ):
        require_binding(actions, tag, name, name)


def relative_luminance(rgb: tuple[int, int, int]) -> float:
    def channel(value: int) -> float:
        normalized = value / 255
        return normalized / 12.92 if normalized <= 0.04045 else ((normalized + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(value) for value in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    light, dark = sorted((relative_luminance(a), relative_luminance(b)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def audit_accessibility() -> None:
    for name, value, minimum in (
        ("primary text", TEXT, 7.0),
        ("secondary text", TEXT_DIM, 4.5),
        ("gold signal", GOLD_BRIGHT, 7.0),
        ("venom signal", CYAN, 7.0),
    ):
        ratio = contrast(value, BG1)
        if ratio < minimum:
            fail(f"{name} contrast {ratio:.2f}:1 fell below {minimum:.1f}:1")
    # Resource bars are never color-only: each family audit above requires a
    # paired numeric/name label while preserving distinct fill colors.
    if len({HP, MANA, ENDURANCE, PET}) != 4:
        fail("resource palette colors collapsed")


def binding_map(path: Path) -> dict[tuple[str, str], tuple[str, str]]:
    root = ET.parse(path).getroot()
    result = {}
    for node in root.findall(".//*[@item]"):
        result[(node.tag, node.get("item", ""))] = (
            (node.findtext("ScreenID") or "").strip(),
            (node.findtext("EQType") or "").strip(),
        )
    return result


def audit_optional_stock_parity() -> bool:
    if not STOCK.is_dir():
        return False
    allowed_stock_only = {("EQUI_GroupWindow.xml", "Label", "Test1")}
    # SpinUI deliberately supplies bindings that the installed stock files
    # leave blank or misname.  Pin the exact replacements so parity remains
    # strict everywhere else and these functional fixes cannot silently drift.
    allowed_binding_overrides = {
        ("EQUI_PlayerWindow.xml", "Label", "PW_AggroPctPlayerLabel"):
            ("PW_AggroPctPlayerLabel", "306"),
        ("EQUI_PlayerWindow.xml", "Label", "PW_AggroNameSecondaryLabel"):
            ("PW_AggroNameSecondaryLabel", "304"),
        ("EQUI_PlayerWindow.xml", "Label", "PW_AggroPctSecondaryLabel"):
            ("PW_AggroPctSecondaryLabel", "308"),
        ("EQUI_ShortDurationBuffWindow.xml", "Label", "SDBW_Buff1"):
            ("SDBuff1Label", "601"),
    }
    for name in FILES:
        stock_path = STOCK / name
        if not stock_path.exists():
            continue
        stock = binding_map(stock_path)
        custom = binding_map(SKIN / name)
        for key, expected in stock.items():
            if (name, key[0], key[1]) in allowed_stock_only:
                continue
            if key not in custom:
                fail(f"July stock item missing from {name}: {key[0]} {key[1]}")
            override_key = (name, key[0], key[1])
            if (custom[key] != expected
                    and custom[key] != allowed_binding_overrides.get(override_key)):
                fail(f"July binding drift in {name}: {key[0]} {key[1]}")
    return True


def audit_default_visibility() -> None:
    manifests = sorted(SKIN.glob("default*.ini"))
    if len(manifests) != 4:
        fail(f"expected four default layout manifests, found {len(manifests)}")
    for manifest in manifests:
        selector = configparser.ConfigParser(strict=False)
        selector.optionxform = str
        selector.read(manifest, encoding="utf-8")
        if selector.get("default", "StanceWnd", fallback=None) != "0":
            fail(f"{manifest.name} must select canonical StanceWnd=0")

    parser = configparser.ConfigParser(strict=False)
    parser.optionxform = str
    parser.read(SKIN / "default1440.ini", encoding="utf-8")
    expected = {
        "BuffWindow": "1",
        "BuffWindow_13": "0",
        "ShortDurationBuffWindow": "1",
        "ShortDurationBuffWindow_13": "0",
        "PlayerWindow": "1",
        "TargetWindow": "1",
        "StanceWnd": "1",
        "CastingWindow": "1",
        "GroupWindow": "1",
        "CastSpellWnd": "1",
        "CastSpellWnd_1": "0",
        "CastSpellWnd_2": "0",
        "CastSpellWnd_3": "0",
    }
    for section, show in expected.items():
        if parser.get(section, "Show", fallback=None) != show:
            fail(f"default1440.ini {section} Show must be {show}")

    # Every shipped starting layout must continue selecting the canonical
    # icon-only deck.  The richer ledger is opt-in through Display Types.
    preset_paths = [REPO / "UI_Spin_qeynos_LO1.ini"]
    preset_paths.extend(sorted(SKIN.glob("default*.ini")))
    preset_paths.extend(sorted((REPO / "layouts").rglob("*.ini")))
    expected_spell_visibility = {
        "CastSpellWnd": "1",
        "CastSpellWnd_1": "0",
        "CastSpellWnd_2": "0",
        "CastSpellWnd_3": "0",
    }
    for path in preset_paths:
        preset = configparser.ConfigParser(strict=False)
        preset.optionxform = str
        preset.read(path, encoding="utf-8")
        for section, show in expected_spell_visibility.items():
            if preset.get(section, "Show", fallback=None) != show:
                rel = path.relative_to(REPO).as_posix()
                fail(f"{rel} {section} Show must be {show}")


def audit_spell_ledger_variant() -> None:
    """Prove Alternate3 is readable, resizable, and binding-complete."""
    root = root_for(SPELL_LEDGER_VARIANT)
    animations = root_for("EQUI_Animations.xml")
    texture_matches = animations.findall(
        ".//TextureInfo[@item='spin_spell_ledger.tga']"
    )
    if len(texture_matches) != 1:
        fail("spell ledger texture must be declared once in EQUI_Animations.xml")
    texture = texture_matches[0]
    if dimensions(texture) != (256, 96):
        fail("spell ledger texture declaration must remain 256x96")
    texture_path = SKIN / "spin_spell_ledger.tga"
    header = texture_path.read_bytes()[:18]
    if len(header) != 18:
        fail("spin_spell_ledger.tga has a truncated header")
    actual_texture_size = (
        int.from_bytes(header[12:14], "little"),
        int.from_bytes(header[14:16], "little"),
    )
    if actual_texture_size != (256, 96):
        fail(f"spin_spell_ledger.tga geometry drift: {actual_texture_size}")
    raw_texture = texture_path.read_bytes()
    if header[2] != 2 or header[16] != 32 or header[17] & 0x20:
        fail("spin_spell_ledger.tga must remain uncompressed bottom-up BGRA")

    def ledger_pixel(x: int, y: int) -> tuple[int, int, int, int]:
        width, height = actual_texture_size
        row = height - 1 - y
        offset = 18 + (row * width + x) * 4
        blue, green, red, alpha = raw_texture[offset:offset + 4]
        return red, green, blue, alpha

    # EQ category-tints SpellGem surfaces.  Keeping every atlas state's outer
    # pixel ring transparent prevents stacked blue/red/green row boxes.
    for state_y in (0, 32, 64):
        perimeter = (
            [ledger_pixel(x, state_y) for x in range(155)]
            + [ledger_pixel(x, state_y + 29) for x in range(155)]
            + [ledger_pixel(0, state_y + y) for y in range(30)]
            + [ledger_pixel(154, state_y + y) for y in range(30)]
        )
        if any(pixel[3] != 0 for pixel in perimeter):
            fail("spell ledger state art reached its outer edge")
    if ledger_pixel(77, 15)[3] == 0:
        fail("spell ledger base plate lost its inset leather surface")
    if ledger_pixel(153, 15)[3] == 0 or ledger_pixel(154, 15)[3] != 0:
        fail("spell ledger leather plate regained excess right-side gutter")
    holder_pixels = [
        ledger_pixel(x, y) for y in range(32, 62) for x in range(155)
    ]
    if any(pixel[3] != 0 for pixel in holder_pixels):
        fail("spell ledger holder can regain category-tinted row seams")
    if ledger_pixel(2, 79)[3] == 0 or ledger_pixel(153, 79)[3] != 0:
        fail("spell ledger hover focus must stay on its left edge")
    visible_pixels = [
        ledger_pixel(x, y) for y in range(96) for x in range(155)
        if ledger_pixel(x, y)[3] != 0
    ]
    if any(blue > red for red, _green, blue, _alpha in visible_pixels):
        fail("spell ledger atlas reintroduced a cool blue seam accent")

    animation_rows = {
        "A_SpinSpellLedgerBackground": 0,
        "A_SpinSpellLedgerHolder": 32,
        "A_SpinSpellLedgerHighlight": 64,
    }
    for animation_name, expected_y in animation_rows.items():
        if root.find(f".//Ui2DAnimation[@item='{animation_name}']") is not None:
            fail(
                f"{animation_name} must not be declared in the dynamic "
                "CastSpellWnd3 display-type file"
            )
        matches = animations.findall(
            f".//Ui2DAnimation[@item='{animation_name}']"
        )
        if len(matches) != 1:
            fail(
                f"{animation_name} must be declared exactly once in "
                "EQUI_Animations.xml"
            )
        animation = matches[0]
        if child_text(animation, "Frames/Texture") != "spin_spell_ledger.tga":
            fail(f"{animation_name} lost its dedicated themed texture")
        if dimensions_at(animation, "Frames/Size") != SPELL_LEDGER_ROW_SIZE:
            fail(f"{animation_name} no longer matches the spell row")
        if (child_int(animation, "Frames/Location/X"),
                child_int(animation, "Frames/Location/Y")) != (0, expected_y):
            fail(f"{animation_name} atlas position changed")

    equi = root_for("EQUI.xml")
    includes = [
        node.text.strip() for node in equi.findall(".//Include") if node.text
    ]
    if "EQUI_Animations.xml" not in includes:
        fail("EQUI.xml no longer loads the central animation registry")

    expected_layout_members = []
    for index, eq_type in enumerate(SPELL_LEDGER_EQTYPES):
        gem = require_binding(
            root, "SpellGem", f"CSPW_Spell{index}", f"CSPW_Spell{index}"
        )
        if dimensions(gem) != SPELL_LEDGER_ROW_SIZE:
            fail(f"spell ledger gem {index + 1} row geometry drifted")
        if (child_int(gem, "SpellIconOffsetX"),
                child_int(gem, "SpellIconOffsetY")) != (2, 2):
            fail(f"spell ledger gem {index + 1} icon left its socket")
        if (child_int(gem, "SpellIconSizeX"),
                child_int(gem, "SpellIconSizeY")) != SPELL_LEDGER_ICON_SIZE:
            fail(f"spell ledger gem {index + 1} icon size changed")
        template = {
            "Holder": "A_SpinSpellLedgerHolder",
            "Background": "A_SpinSpellLedgerBackground",
            "Highlight": "A_SpinSpellLedgerHighlight",
        }
        for field, expected in template.items():
            if child_text(gem, f"SpellGemDrawTemplate/{field}") != expected:
                fail(f"spell ledger gem {index + 1} lost its {field.lower()}")

        name = require_binding(
            root, "Label", f"CSPW_Spell_{index}_Label",
            f"CSPW_Spell_{index}_Label", eq_type,
        )
        if dimensions(name) != (104, 30):
            fail(f"spell ledger name {index + 1} geometry drifted")
        if (child_int(name, "Location/X"),
                child_int(name, "Location/Y")) != (33, 0):
            fail(f"spell ledger name {index + 1} left its text rail")
        if (child_int(name, "Font") < 2
                or child_text(name, "NoWrap") != "true"
                or child_text(name, "AlignVCenter") != "true"):
            fail(f"spell ledger name {index + 1} lost readable alignment")
        if color(name, "TextColor") != TEXT:
            fail(f"spell ledger name {index + 1} lost parchment color")

        number = require_binding(
            root, "Label", f"CSPW_Spell_{index}_Number",
            f"CSPW_Spell_{index}_Number",
        )
        if child_text(number, "Text") != str(index + 1):
            fail(f"spell ledger slot {index + 1} has the wrong number")
        if dimensions(number) != (12, 30):
            fail(f"spell ledger number {index + 1} geometry drifted")
        if (child_int(number, "Location/X"),
                child_int(number, "Location/Y")) != (139, 0):
            fail(f"spell ledger number {index + 1} left its slot rail")
        if (child_text(number, "AlignRight") != "true"
                or child_text(number, "AlignVCenter") != "true"):
            fail(f"spell ledger number {index + 1} lost right alignment")
        if color(number, "TextColor") != GOLD_BRIGHT:
            fail(f"spell ledger number {index + 1} lost brass color")

        row_name = f"CSPW_Spell_{index}"
        row = require_binding(root, "LayoutBox", row_name, row_name)
        if dimensions(row) != SPELL_LEDGER_ROW_SIZE:
            fail(f"spell ledger row {index + 1} geometry drifted")
        row_members = [node.text for node in row.findall("Pieces")]
        if row_members != [
            f"SpellGem:CSPW_Spell{index}",
            f"CSPW_Spell_{index}_Label",
            f"CSPW_Spell_{index}_Number",
        ]:
            fail(f"spell ledger row {index + 1} membership changed")
        expected_layout_members.append(f"LayoutBox:{row_name}")

    layout = item(root, "TileLayoutBox", "CSPW_SpellGemLayout")
    actual_layout_members = [node.text for node in layout.findall("Pieces")]
    if actual_layout_members != expected_layout_members:
        fail("spell ledger tile order no longer matches slots 1 through 14")
    if (child_int(layout, "Spacing"),
            child_int(layout, "SecondarySpacing")) != (2, 2):
        fail("spell ledger lost its compact row breathing room")
    if (child_text(layout, "HorizontalFirst") != "true"
            or child_text(layout, "SnapToChildren") != "true"):
        fail("spell ledger responsive reflow changed")

    layout_rules = item(root, "LayoutVertical", "CSPW_LayoutV")
    if (child_text(layout_rules, "ResizeVertical") != "true"
            or child_text(layout_rules, "ResizeHorizontal") != "true"):
        fail("spell ledger layout is no longer resizable on both axes")
    window = item(root, "Screen", "CastSpellWnd")
    if child_text(window, "MenuName") != SPELL_LEDGER_MENU_NAME:
        fail("spell ledger is not named in the display-type picker")
    if dimensions(window) != SPELL_LEDGER_WINDOW_SIZE:
        fail("spell ledger initial one-column size changed")
    if SPELL_LEDGER_WINDOW_SIZE[0] - SPELL_LEDGER_ROW_SIZE[0] != 2:
        fail("spell ledger frame regained excess right-side padding")
    actual_bounds = tuple(child_int(window, field) for field in (
        "MinHSize", "MinVSize", "MaxHSize", "MaxVSize",
    ))
    if actual_bounds != SPELL_LEDGER_WINDOW_BOUNDS:
        fail(f"spell ledger resize bounds changed: {actual_bounds}")
    if (child_text(window, "Style_Sizable") != "true"
            or child_text(window, "Style_ClientMovable") != "true"):
        fail("spell ledger can no longer be resized or repositioned")
    if (child_text(window, "DrawTemplate") != "WDT_RoundedNoTitle"
            or child_text(window, "Style_Titlebar") != "false"):
        fail("spell ledger lost its compact SpinUI frame")


def audit_variant_safety() -> None:
    """Ensure old INI variant selections cannot restore stale bindings."""
    checked = 0
    for canonical_name, variants in CANONICAL_VARIANTS.items():
        expected = binding_map(SKIN / canonical_name)
        canonical_root = root_for(canonical_name)

        def semantic_signature(node: ET.Element):
            """Compare aliases without whitespace/comments or picker labels."""
            children = tuple(
                semantic_signature(child)
                for child in list(node)
                if child.tag != "MenuName"
            )
            return (
                node.tag,
                tuple(sorted(node.attrib.items())),
                (node.text or "").strip(),
                children,
            )

        expected_visuals = semantic_signature(canonical_root)
        for variant_name in variants:
            path = SKIN / variant_name
            if not path.exists():
                fail(f"compatibility variant missing: {variant_name}")
            actual = binding_map(path)
            if actual != expected:
                missing = sorted(set(expected) - set(actual))
                changed = sorted(
                    key for key in set(expected) & set(actual)
                    if expected[key] != actual[key]
                )
                fail(
                    f"unsafe compatibility variant {variant_name}: "
                    f"{len(missing)} missing, {len(changed)} binding changes"
                )
            variant_root = ET.parse(path).getroot()
            if variant_root.find(".//MenuName") is not None:
                fail(f"retired duplicate variant is still exposed: {variant_name}")
            if semantic_signature(variant_root) != expected_visuals:
                fail(f"compatibility variant visual drift: {variant_name}")
            checked += 1

    # Stance keeps two genuinely useful text-position alternatives.  They must
    # remain current-schema, accessible, and compact.
    stance_expected = binding_map(SKIN / "EQUI_StanceWnd.xml")
    for variant_name in ("EQUI_StanceWnd1.xml", "EQUI_StanceWnd2.xml"):
        root = root_for(variant_name)
        if binding_map(SKIN / variant_name) != stance_expected:
            fail(f"stance variant binding drift: {variant_name}")
        window = item(root, "Screen", "StanceWnd")
        if dimensions(window) != (440, 56):
            fail(f"stance variant geometry drift: {variant_name}")
        if (child_int(window, "MinHSize"),
                child_int(window, "MinVSize")) != (20, 20):
            fail(f"stance variant rejects stock saved geometry: {variant_name}")
        expected_bottom = 15 if variant_name.endswith("2.xml") else 0
        for orientation in ("SW_HorizontalOrientationTemplate",
                            "SW_VerticalOrientationTemplate"):
            node = item(root, "Screen", orientation)
            if (child_int(node, "RightAnchorOffset"),
                    child_int(node, "BottomAnchorOffset")) != (
                        0, expected_bottom):
                fail(f"stance variant layout contract drift: {variant_name}")
        for label_name in ("SW_StanceLabel", "SW_InvocationLabel"):
            if child_int(item(root, "Label", label_name), "Font") < 3:
                fail(f"stance variant text too small: {variant_name} {label_name}")
        checked += 1
    audit_spell_ledger_variant()
    checked += 1
    if checked != 53:
        fail(f"variant audit coverage changed unexpectedly: {checked}")


def main() -> int:
    audit_attack_indicator_contract()
    audit_player_and_target()
    audit_group_and_extended_targets()
    audit_effects_casting_and_bars()
    audit_raid_and_actions()
    audit_accessibility()
    audit_default_visibility()
    audit_variant_safety()
    stock_checked = audit_optional_stock_parity()
    print("Combat Command Center audit: ALL PASS")
    print("  Player/Target/ToT | Group 1..11 | XTarget 0..22 | Raid groups 1..12")
    print("  buffs 30 | songs 15 | spell gems 14 | hotbars 11 x 12 | stance + invocation")
    print("  52 compatibility aliases + 21 foreground native attack perimeters + spell ledger")
    print("  contrast AAA/AA | July stock parity " + ("PASS" if stock_checked else "not available"))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, ValueError, ET.ParseError) as exc:
        print(f"Combat Command Center audit: FAIL - {exc}", file=sys.stderr)
        raise SystemExit(1)
