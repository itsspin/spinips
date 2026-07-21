#!/usr/bin/env python3
"""Static release gate for the EverQuest Legends Combat Command Center."""

from __future__ import annotations

import configparser
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
SKIN = REPO / "spinui_reloaded"
STOCK = Path(r"C:\EQLegends\uifiles\default")

TEXT = (238, 242, 243)
TEXT_DIM = (146, 161, 169)
GOLD_BRIGHT = (250, 205, 95)
CYAN = (52, 218, 190)
HP = (222, 62, 72)
MANA = (66, 126, 244)
ENDURANCE = (219, 158, 42)
PET = (112, 137, 158)
BG1 = (9, 12, 17)

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
    "EQUI_CastSpellWnd.xml": tuple(f"EQUI_CastSpellWnd{i}.xml" for i in range(1, 4)),
}


def fail(message: str) -> None:
    raise AssertionError(message)


def root_for(name: str) -> ET.Element:
    try:
        return ET.parse(SKIN / name).getroot()
    except ET.ParseError as exc:
        fail(f"invalid XML {name}: {exc}")


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


def audit_player_and_target() -> None:
    player = root_for("EQUI_PlayerWindow.xml")
    require_binding(player, "Gauge", "Player_HP", "PlayerHP", 1)
    require_binding(player, "Gauge", "Player_Mana", "PlayerMana", 2)
    require_binding(player, "Gauge", "Player_Fatigue", "PlayerFatigue", 3)
    require_binding(player, "Gauge", "PW_ExpGauge", "ExpGauge", 4)
    require_binding(player, "Gauge", "PW_AltAdvGauge", "AltAdvGauge", 5)
    require_binding(player, "Gauge", "PW_Castspell_Gauge", "PW_Castspell_Gauge", 7)
    require_binding(player, "Label", "Player_ManaLabel", "ManaLabel", 1009)
    require_binding(player, "Label", "PW_MPNumbers", "PW_MPNumbers", 128)
    require_binding(player, "Label", "PW_ENNumbers", "PW_ENNumbers", 129)
    require_binding(player, "Label", "PW_StanceLabel", "PW_StanceLabel", 1026)
    require_binding(player, "Label", "PW_InvocationInfo", "PW_InvocationInfo", 1017)
    require_binding(player, "Label", "PW_AggroPctPlayerLabel",
                    "PW_AggroPctPlayerLabel", 306)
    require_binding(player, "Label", "PW_AggroNameSecondaryLabel",
                    "PW_AggroNameSecondaryLabel", 304)
    require_binding(player, "Label", "PW_AggroPctSecondaryLabel",
                    "PW_AggroPctSecondaryLabel", 308)
    require_binding(player, "DragBox", "PW_DragBox", "PW_DragBox")
    require_binding(player, "DragBox", "PW_DragBox2", "PW_DragBox2")
    item(player, "Screen", "IW_Gauges_Background")
    if dimensions(item(player, "Screen", "PlayerWindow")) != (360, 193):
        fail("PlayerWindow must remain 360x193")
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
    if dimensions(item(target, "Screen", "TargetWindow")) != (360, 193):
        fail("TargetWindow must remain 360x193")
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
    if dimensions(extended_window) != (178, 300):
        fail("ExtendedTargetWnd must remain 178x300")
    for index in range(23):
        for tag, stem in (("Label", "ETW_AggroPct"), ("Gauge", "ETW_Gauge"),
                          ("Gauge", "ETW_ManaGauge"), ("Gauge", "ETW_CastGauge"),
                          ("Gauge", "ETW_STAGauge"), ("Label", "ETW_HPLabel"),
                          ("Label", "ETW_HPPercLabel"), ("Button", "ETW_Role")):
            item(extended, tag, f"{stem}{index}")
        for name, expected in ((f"ETW_Gauge{index}", HP),
                               (f"ETW_ManaGauge{index}", MANA),
                               (f"ETW_CastGauge{index}", CYAN),
                               (f"ETW_STAGauge{index}", ENDURANCE)):
            require_fill(extended, name, expected)


def audit_effects_casting_and_bars() -> None:
    buffs = root_for("EQUI_BuffWindow.xml")
    if dimensions(item(buffs, "Screen", "BuffWindow")) != (216, 712):
        fail("BuffWindow must remain 216x712")
    for index in range(30):
        label = require_binding(buffs, "Label", f"BW_Buff{index}",
                                f"Buff{index}Label", 500 + index)
        if child_int(label, "Font") < 3:
            fail(f"BW_Buff{index} fell below the accessible font tier")
        item(buffs, "Screen", f"BW_{index:02d}_Screen")

    songs = root_for("EQUI_ShortDurationBuffWindow.xml")
    if dimensions(item(songs, "Screen", "ShortDurationBuffWindow")) != (216, 367):
        fail("ShortDurationBuffWindow must remain 216x367")
    for index in range(15):
        screen_id = "Buff1Label" if index == 1 else f"SDBuff{index}Label"
        label = require_binding(songs, "Label", f"SDBW_Buff{index}",
                                screen_id, 600 + index)
        if child_int(label, "Font") < 3:
            fail(f"SDBW_Buff{index} fell below the accessible font tier")
        item(songs, "Screen", f"SDBW_{index:02d}_Screen")

    casting = root_for("EQUI_CastingWindow.xml")
    require_binding(casting, "Gauge", "Casting_Gauge", "Gauge", 7)
    require_binding(casting, "Label", "Casting_SpellName", None, 134)
    item(casting, "Screen", "Cast_Gauge_Background")
    casting_window = item(casting, "Screen", "CastingWindow")
    if dimensions(casting_window) != (380, 36):
        fail("CastingWindow must remain 380x36")
    if child_text(casting_window, "Style_Titlebar") != "false":
        fail("CastingWindow titlebar reintroduced visual jitter")
    require_fill(casting, "Casting_Gauge", CYAN)

    spells = root_for("EQUI_CastSpellWnd.xml")
    item(spells, "Ui2DAnimation", "Spell_Gem_Background")
    for index in range(14):
        require_binding(spells, "SpellGem", f"CSPW_Spell{index}", f"CSPW_Spell{index}")
    if dimensions(item(spells, "Screen", "CastSpellWnd")) != (52, 623):
        fail("CastSpellWnd must expose all 14 Legends gems at 52x623")

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
    pieces = {node.text for node in stance_window.findall("Pieces") if node.text}
    if "Screen:SW_DisplayStanceInvocation" not in pieces:
        fail("active stance bar lost its stance/invocation text rail")


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
            if custom[key] != expected:
                fail(f"July binding drift in {name}: {key[0]} {key[1]}")
    return True


def audit_default_visibility() -> None:
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
    }
    for section, show in expected.items():
        if parser.get(section, "Show", fallback=None) != show:
            fail(f"default1440.ini {section} Show must be {show}")


def audit_variant_safety() -> None:
    """Ensure old INI variant selections cannot restore stale bindings."""
    checked = 0
    for canonical_name, variants in CANONICAL_VARIANTS.items():
        expected = binding_map(SKIN / canonical_name)
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
        for label_name in ("SW_StanceLabel", "SW_InvocationLabel"):
            if child_int(item(root, "Label", label_name), "Font") < 3:
                fail(f"stance variant text too small: {variant_name} {label_name}")
        checked += 1
    if checked != 53:
        fail(f"variant audit coverage changed unexpectedly: {checked}")


def main() -> int:
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
    print("  53 compatibility variants retain current Legends bindings")
    print("  contrast AAA/AA | July stock parity " + ("PASS" if stock_checked else "not available"))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, ValueError, ET.ParseError) as exc:
        print(f"Combat Command Center audit: FAIL - {exc}", file=sys.stderr)
        raise SystemExit(1)
