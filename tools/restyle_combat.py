#!/usr/bin/env python3
"""Apply the canonical Legends Combat Command Center treatment.

The files in this pass are hand-authored SIDL, so this transformer deliberately
edits only named blocks and leaves ordering, comments, ScreenIDs, EQTypes, and
all latent legacy rows untouched.  Running it repeatedly is idempotent.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable


REPO = Path(__file__).resolve().parent.parent
SKIN = REPO / "spinui_reloaded"

# Obsidian / venom / ember accessibility palette.  The same values live in
# spinui_theme.py for rendered documentation; they are repeated here so this
# script has no Pillow/runtime dependency.
TEXT = (238, 242, 243)
TEXT_DIM = (146, 161, 169)
GOLD = (219, 158, 42)
GOLD_BRIGHT = (250, 205, 95)
CYAN = (52, 218, 190)
CYAN_BRIGHT = (150, 240, 222)
HP = (222, 62, 72)
MANA = (66, 126, 244)
ENDURANCE = (219, 158, 42)
PET = (112, 137, 158)

# Older SpinUI releases exposed a large collection of visual variants.  Most of
# those files predate the July Legends schema and can lose live controls when a
# saved layout still selects them.  Keep the filenames as compatibility aliases
# (so existing INIs continue to load), but make every alias use the canonical,
# current-schema command frame.  MenuName is removed from aliases so the picker
# presents one deliberate signature design instead of dozens of identical rows.
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
    raise RuntimeError(message)


def write_ascii(path: Path, text: str) -> None:
    """Write deterministic LF XML without inherited trailing whitespace."""
    lines = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        original_prefix = re.match(r"[ \t]*", line).group(0)
        prefix = original_prefix
        while " \t" in prefix:
            prefix = prefix.replace(" \t", "\t")
        lines.append(prefix + line[len(original_prefix):])
    cleaned = "\n".join(lines) + "\n"
    path.write_text(cleaned, encoding="ascii", newline="")


def item_pattern(tag: str, name: str) -> re.Pattern[str]:
    return re.compile(
        rf"<{tag}\s+item=\"{re.escape(name)}\">.*?</{tag}>", re.DOTALL
    )


def change_item(text: str, tag: str, name: str,
                transform: Callable[[str], str]) -> str:
    pattern = item_pattern(tag, name)
    match = pattern.search(text)
    if match is None:
        fail(f"missing {tag} item {name}")
    updated = transform(match.group(0))
    return text[:match.start()] + updated + text[match.end():]


def change_matching(text: str, tag: str, name_pattern: str,
                    transform: Callable[[str], str]) -> str:
    pattern = re.compile(
        rf"<{tag}\s+item=\"(?:{name_pattern})\">.*?</{tag}>", re.DOTALL
    )
    count = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return transform(match.group(0))

    result = pattern.sub(repl, text)
    if count == 0:
        fail(f"no {tag} items matched {name_pattern}")
    return result


def set_value(block: str, tag: str, value: str | int,
              *, required: bool = True) -> str:
    pattern = re.compile(rf"(<{tag}>).*?(</{tag}>)", re.DOTALL)
    result, count = pattern.subn(
        lambda m: f"{m.group(1)}{value}{m.group(2)}", block, count=1
    )
    if required and count != 1:
        fail(f"missing <{tag}> in item block")
    return result


def set_or_add_value(block: str, tag: str, value: str | int,
                     *, after: str) -> str:
    """Set a scalar tag, or add it after a stable neighboring scalar tag."""
    if re.search(rf"<{tag}>.*?</{tag}>", block, re.DOTALL):
        return set_value(block, tag, value)
    anchor = re.search(rf"</{after}>", block)
    if anchor is None:
        fail(f"cannot add <{tag}>: missing </{after}> anchor")
    indent_match = re.search(rf"\n([ \t]+)<{after}>", block)
    indent = indent_match.group(1) if indent_match else "\t\t"
    addition = f"\n{indent}<{tag}>{value}</{tag}>"
    return block[:anchor.end()] + addition + block[anchor.end():]


def set_container(block: str, container: str, **values: int) -> str:
    pattern = re.compile(
        rf"(<{container}>)(.*?)(</{container}>)", re.DOTALL
    )
    match = pattern.search(block)
    if match is None:
        fail(f"missing <{container}> in item block")
    body = match.group(2)
    for tag, value in values.items():
        body = set_value(body, tag, value)
    replacement = match.group(1) + body + match.group(3)
    return block[:match.start()] + replacement + block[match.end():]


def set_color(block: str, container: str, rgb: tuple[int, int, int],
              *, insert: bool = False) -> str:
    pattern = re.compile(
        rf"(<{container}>)(.*?)(</{container}>)", re.DOTALL
    )
    match = pattern.search(block)
    if match is None:
        if not insert:
            fail(f"missing <{container}> in item block")
        indent_match = re.search(r"\n([ \t]+)<", block)
        indent = indent_match.group(1) if indent_match else "\t\t"
        child = indent + "\t"
        payload = (
            f"{indent}<{container}>\n"
            f"{child}<R>{rgb[0]}</R>\n"
            f"{child}<G>{rgb[1]}</G>\n"
            f"{child}<B>{rgb[2]}</B>\n"
            f"{indent}</{container}>\n"
        )
        anchor = re.search(r"\n[ \t]+<(?:RelativePosition|Text|NoWrap)>", block)
        if anchor is None:
            fail(f"no insertion point for <{container}>")
        return block[:anchor.start() + 1] + payload + block[anchor.start() + 1:]
    body = match.group(2)
    for tag, value in zip(("R", "G", "B"), rgb):
        body = set_value(body, tag, value)
    replacement = match.group(1) + body + match.group(3)
    return block[:match.start()] + replacement + block[match.end():]


def set_font(block: str, value: int) -> str:
    if re.search(r"<Font>.*?</Font>", block, re.DOTALL):
        return set_value(block, "Font", value)
    match = re.search(r"</(?:ScreenID|EQType)>", block)
    if match is None:
        fail("cannot add font: item has no ScreenID or EQType")
    indent_match = re.search(r"\n([ \t]+)<(?:ScreenID|EQType)>", block)
    indent = indent_match.group(1) if indent_match else "\t\t"
    return block[:match.end()] + f"\n{indent}<Font>{value}</Font>" + block[match.end():]


def style_gauge(block: str, color: tuple[int, int, int],
                lines: tuple[int, int, int] | None = None) -> str:
    block = set_color(block, "FillTint", color)
    if lines is not None:
        block = set_color(block, "LinesFillTint", lines)
    return block


def set_root_widths(block: str, width: int) -> str:
    # Group Size and GroupSizeN records are all direct children of the root.
    return re.sub(r"(<CX>)\d+(</CX>)", rf"\g<1>{width}\g<2>", block)


def style_buff_file(path: Path, prefix: str, count: int,
                    title: str, height: int) -> None:
    text = path.read_text(encoding="ascii")
    template = f"{prefix}_Player_Buff_Template"
    text = change_item(
        text, "Button", template,
        lambda b: set_container(
            set_container(set_font(b, 2), "Size", CX=216, CY=20),
            "DecalSize", CX=20, CY=20,
        ),
    )
    for index in range(count):
        label = f"{prefix}_Buff{index}"

        def label_style(block: str) -> str:
            block = set_font(block, 3)
            block = set_container(block, "Location", X=27, Y=1)
            block = set_container(block, "Size", CX=179, CY=18)
            return set_color(block, "TextColor", TEXT, insert=True)

        text = change_item(text, "Label", label, label_style)

    text = change_item(
        text, "Label", f"{prefix}_Buff_FrontSpacer",
        lambda b: set_container(
            set_container(b, "Location", X=0, Y=1), "Size", CX=27, CY=18
        ),
    )
    text = change_item(
        text, "Label", f"{prefix}_Buff_BackSpacer",
        lambda b: set_container(
            set_container(b, "Location", X=206, Y=1), "Size", CX=10, CY=18
        ),
    )
    text = change_item(
        text, "Screen", f"{prefix}_00_Screen",
        lambda b: set_container(b, "Size", CX=216, CY=20),
    )
    text = change_item(
        text, "TileLayoutBox", f"{prefix}_Buttons",
        lambda b: set_value(set_value(b, "LeftAnchorOffset", 1), "Spacing", 0),
    )
    text = change_item(
        text, "TileLayoutBox", f"{prefix}_Labels",
        lambda b: set_value(set_value(b, "LeftAnchorOffset", 1), "Spacing", 0),
    )
    window_name = "BuffWindow" if prefix == "BW" else "ShortDurationBuffWindow"
    background_name = f"{prefix}_Background"

    def background_style(block: str) -> str:
        # Active effect buttons remain fully interactive, while the unused
        # maximum-slot canvas no longer paints an opaque inset rectangle.
        block = set_or_add_value(
            block, "Style_Transparent", "true", after="Style_HScroll"
        )
        return set_value(block, "Style_Border", "false")

    text = change_item(text, "Screen", background_name, background_style)

    def window_style(block: str) -> str:
        block = set_value(block, "Text", title)
        block = set_container(block, "Size", CX=216, CY=height)
        block = set_value(block, "Style_Transparent", "true")
        block = set_value(block, "DrawTemplate", "WDT_RoundedTransparentNoArrow")
        block = set_value(block, "Style_Titlebar", "true")
        # Keep the title rail for dragging, but never draw a perimeter around
        # the entire maximum buff-slot canvas.  That outline is visible even
        # when most effect rows are empty and recreates the oversized slab the
        # transparent treatment is intended to remove.
        block = set_value(block, "Style_Border", "false")
        block = set_value(block, "Style_ClientMovable", "true")
        block = set_or_add_value(
            block, "ClickThroughEmptyBuffs", "true",
            after="Style_ClientMovable",
        )
        block = set_value(block, "KeepOnScreen", "true")
        return block

    text = change_item(text, "Screen", window_name, window_style)
    write_ascii(path, text)


def style_player() -> None:
    path = SKIN / "EQUI_PlayerWindow.xml"
    text = path.read_text(encoding="ascii")
    # XP is ember gold, AA is venom — including the LinesFill sub-tick
    # overlay, which the stock skin left pure blue on both bars and made the
    # two gauges indistinguishable at a glance.
    gauges = {
        "Player_HP": (HP, None),
        "Player_Mana": (MANA, None),
        "Player_Fatigue": (ENDURANCE, None),
        "Pet_HP": (PET, None),
        "PW_ExpGauge": (GOLD, GOLD_BRIGHT),
        "PW_AltAdvGauge": (CYAN, CYAN_BRIGHT),
        "PW_Castspell_Gauge": (CYAN, None),
    }
    for name, (color, lines) in gauges.items():
        text = change_item(text, "Gauge", name,
                           lambda b, c=color, l=lines: style_gauge(b, c, l))
    for name, color in (("PW_Level", GOLD_BRIGHT), ("PW_Class", TEXT),
                        ("PW_StanceLabel", GOLD_BRIGHT),
                        ("PW_InvocationInfo", CYAN)):
        text = change_item(
            text, "Label", name,
            lambda b, c=color: set_color(set_font(b, 4), "TextColor", c,
                                         insert=True),
        )

    def stance_rail(block: str) -> str:
        # Legends can return a longer stance name (for example "Defensive
        # Stance") than the stock two-point font was designed around.  Give
        # the left rail a real, inset column and enough line height for Font 4
        # descenders without letting it collide with invocation text.
        block = set_value(block, "TopAnchorOffset", 91)
        block = set_value(block, "BottomAnchorOffset", 107)
        block = set_value(block, "LeftAnchorOffset", 6)
        block = set_value(block, "RightAnchorOffset", 132)
        return block

    def invocation_rail(block: str) -> str:
        # Reserve an independent right-aligned column for values such as
        # "Empower".  The four-pixel gutter keeps both dynamic labels legible
        # even when the three-class identity uses the full command-frame width.
        block = set_value(block, "TopAnchorOffset", 91)
        block = set_value(block, "BottomAnchorOffset", 107)
        block = set_value(block, "LeftAnchorOffset", 232)
        block = set_value(block, "RightAnchorOffset", 6)
        return block

    text = change_item(text, "Label", "PW_StanceLabel", stance_rail)
    text = change_item(text, "Label", "PW_InvocationInfo", invocation_rail)

    def root_style(block: str) -> str:
        block = set_container(block, "Size", CX=360, CY=193)
        block = set_value(block, "MenuName", "Legends Command Frame - Buffs on Top")
        # The root exists mostly to host the interaction/buff canvas.  Painting
        # its full 360x193 border creates the large faint perimeter seen in the
        # live client; the compact PlayerSubWindow remains the visible frame.
        block = set_value(block, "Style_Border", "false")
        return block

    text = change_item(text, "Screen", "PlayerWindow", root_style)
    write_ascii(path, text)


def style_target() -> None:
    path = SKIN / "EQUI_TargetWindow.xml"
    text = path.read_text(encoding="ascii")
    gauges = {
        "Target_HP": HP,
        "Target_HP_NameOnly": HP,
        "TTargetOfTarget_HP": HP,
        "Target_Mana": MANA,
        "Target_Endurance": ENDURANCE,
        "Castspell_Gauge": CYAN,
    }
    for name, color in gauges.items():
        text = change_item(text, "Gauge", name,
                           lambda b, c=color: style_gauge(b, c))
    for name, color in (("Target_Class", TEXT), ("Target_Level", GOLD_BRIGHT),
                        ("ToT_Class", TEXT), ("ToT_Level", GOLD_BRIGHT)):
        text = change_item(
            text, "Label", name,
            lambda b, c=color: set_color(set_font(b, 3), "TextColor", c,
                                         insert=True),
        )

    def root_style(block: str) -> str:
        block = set_container(block, "Size", CX=360, CY=193)
        block = set_value(block, "MenuName", "Legends Command Frame - Buffs on Top")
        # Keep the root transparent and interactive while letting the compact
        # TargetSubWindow provide the only visible perimeter.
        block = set_value(block, "Style_Border", "false")
        return block

    text = change_item(text, "Screen", "TargetWindow", root_style)
    write_ascii(path, text)


def style_target_of_target() -> None:
    path = SKIN / "EQUI_TargetOfTargetWindow.xml"
    text = path.read_text(encoding="ascii")
    text = change_item(text, "Gauge", "TargetOfTarget_HP",
                       lambda b: style_gauge(b, HP))
    for name, color in (("ToTW_Level", GOLD_BRIGHT), ("ToTW_Class", TEXT)):
        text = change_item(
            text, "Label", name,
            lambda b, c=color: set_color(set_font(b, 3), "TextColor", c),
        )

    def root_style(block: str) -> str:
        block = set_container(block, "Size", CX=240, CY=53)
        block = set_value(block, "Text", "TARGET OF TARGET")
        block = set_value(block, "MinVSize", 53)
        block = set_value(block, "MaxVSize", 53)
        block = set_value(block, "MinHSize", 180)
        block = set_value(block, "MaxHSize", 360)
        return block

    text = change_item(text, "Screen", "TargetOfTargetWindow", root_style)
    write_ascii(path, text)


def style_group() -> None:
    path = SKIN / "EQUI_GroupWindow.xml"
    text = path.read_text(encoding="ascii")
    for pattern, color in ((r"GW_Gauge(?:[1-9]|10|11)", HP),
                           (r"GW_ManaGauge(?:[1-9]|10|11)", MANA),
                           (r"GW_STAGauge(?:[1-9]|10|11)", ENDURANCE),
                           (r"GW_PetGauge(?:[1-9]|10|11)", PET)):
        text = change_matching(text, "Gauge", pattern,
                               lambda b, c=color: style_gauge(b, c))
    text = change_matching(text, "Label", r"GW_HPLabel(?:[1-9]|10|11)",
                           lambda b: set_font(b, 3))
    text = change_matching(text, "Label", r"GW_AggroPctPlayer(?:[1-9]|10|11)",
                           lambda b: set_color(set_font(b, 2), "TextColor",
                                               GOLD_BRIGHT, insert=True))

    def root_style(block: str) -> str:
        block = set_root_widths(block, 230)
        block = set_container(block, "Size", CX=230, CY=70)
        for index in range(1, 12):
            expected_height = 120 + (index - 1) * 42
            block = set_container(block, f"GroupSize{index}",
                                  CX=230, CY=expected_height)
        block = set_value(block, "Text", "GROUP")
        block = set_value(block, "MenuName", "Legends Command Frames")
        return block

    text = change_item(text, "Screen", "GroupWindow", root_style)
    write_ascii(path, text)


def style_extended_targets() -> None:
    path = SKIN / "EQUI_ExtendedTargetWnd.xml"
    text = path.read_text(encoding="ascii")
    target_index = r"(?:[0-9]|1[0-9]|2[0-2])"
    for pattern, color in ((rf"ETW_Gauge{target_index}", HP),
                           (rf"ETW_ManaGauge{target_index}", MANA),
                           (rf"ETW_CastGauge{target_index}", CYAN),
                           (rf"ETW_STAGauge{target_index}", ENDURANCE)):
        text = change_matching(text, "Gauge", pattern,
                               lambda b, c=color: style_gauge(b, c))
    text = change_matching(text, "Label", rf"ETW_HPLabel{target_index}",
                           lambda b: set_font(b, 2))
    text = change_matching(
        text, "Label", rf"ETW_AggroPct{target_index}",
        lambda b: set_color(set_font(b, 2), "TextColor", GOLD_BRIGHT,
                            insert=True),
    )

    def root_style(block: str) -> str:
        block = set_container(block, "Size", CX=178, CY=300)
        block = set_value(block, "Text", "EXTENDED TARGETS")
        block = set_value(block, "MinHSize", 170)
        return block

    text = change_item(text, "Screen", "ExtendedTargetWnd", root_style)
    write_ascii(path, text)


def style_casting() -> None:
    path = SKIN / "EQUI_CastingWindow.xml"
    text = path.read_text(encoding="ascii")

    def gauge_style(block: str) -> str:
        block = style_gauge(block, CYAN)
        block = set_value(block, "TopAnchorOffset", 6)
        return set_value(block, "BottomAnchorOffset", 30)

    text = change_item(text, "Gauge", "Casting_Gauge", gauge_style)

    def label_style(block: str) -> str:
        block = set_font(block, 4)
        block = set_color(block, "TextColor", TEXT, insert=True)
        block = set_value(block, "TopAnchorOffset", 6)
        return set_value(block, "BottomAnchorOffset", 30)

    text = change_item(text, "Label", "Casting_SpellName", label_style)

    def root_style(block: str) -> str:
        block = set_container(block, "Size", CX=380, CY=36)
        block = set_value(block, "Text", "CASTING")
        block = set_or_add_value(block, "MenuName", "Legends Command Cast Bar",
                                 after="Text")
        block = set_value(block, "DrawTemplate", "WDT_RoundedNoTitle")
        block = set_value(block, "Style_Titlebar", "false")
        block = set_value(block, "MinHSize", 200)
        block = set_value(block, "MinVSize", 36)
        block = set_value(block, "MaxHSize", 600)
        block = set_value(block, "MaxVSize", 36)
        return block

    text = change_item(text, "Screen", "CastingWindow", root_style)
    write_ascii(path, text)


def style_spell_gems() -> None:
    path = SKIN / "EQUI_CastSpellWnd.xml"
    text = path.read_text(encoding="ascii")
    for index in range(14):
        def gem_style(block: str) -> str:
            # Legends' own default_modern skin uses a 36px icon centered in
            # this exact 40px socket.  The former 24px/13px geometry left the
            # art undersized and visibly off-center in horizontal layouts.
            block = set_value(block, "SpellIconOffsetX", 2)
            block = set_value(block, "SpellIconOffsetY", 2)
            block = set_value(block, "SpellIconSizeX", 36)
            return set_value(block, "SpellIconSizeY", 36)

        text = change_item(text, "SpellGem", f"CSPW_Spell{index}", gem_style)
    text = change_item(
        text, "Screen", "CastSpellWnd",
        lambda b: set_or_add_value(
            set_container(b, "Size", CX=52, CY=623),
            "MenuName", "Legends Spell Deck", after="Text",
        ),
    )
    write_ascii(path, text)


def style_hotbuttons() -> None:
    path = SKIN / "EQUI_HotButtonWnd.xml"
    text = path.read_text(encoding="ascii")
    text = change_matching(text, "HotButton", r"HB_Button(?:[1-9]|10|11|12)",
                           lambda b: set_font(b, 2))
    for name in ("HB_HorizontalCurrentPageLabel", "HB_VerticalCurrentPageLabel"):
        text = change_item(
            text, "Label", name,
            lambda b: set_color(set_font(b, 2), "TextColor", GOLD_BRIGHT,
                                insert=True),
        )
    write_ascii(path, text)


CYAN_DIM = (36, 152, 133)

# Twin-wing rail: static STANCE / INVOCATION captions bracket the bar, the
# dynamic names sit inside their wing, and an ember gem marks the split.
WING_BLOCK = """	<!-- SPIN-WING: stance / invocation twin-wing rail -->
	<TextureInfo item="spin_deco.tga">
		<Size>
			<CX>128</CX>
			<CY>128</CY>
		</Size>
	</TextureInfo>
	<Ui2DAnimation item="A_SpinWingGem">
		<Cycle>true</Cycle>
		<Frames>
			<Texture>spin_deco.tga</Texture>
			<Location>
				<X>112</X>
				<Y>64</Y>
			</Location>
			<Size>
				<CX>12</CX>
				<CY>12</CY>
			</Size>
			<Hotspot>
				<X>0</X>
				<Y>0</Y>
			</Hotspot>
			<Duration>1000</Duration>
		</Frames>
	</Ui2DAnimation>
	<Label item="SW_StanceCaption">
		<Font>1</Font>
		<RelativePosition>true</RelativePosition>
		<AutoStretch>true</AutoStretch>
		<TopAnchorOffset>2</TopAnchorOffset>
		<BottomAnchorOffset>13</BottomAnchorOffset>
		<LeftAnchorOffset>2</LeftAnchorOffset>
		<RightAnchorOffset>46</RightAnchorOffset>
		<TopAnchorToTop>true</TopAnchorToTop>
		<BottomAnchorToTop>true</BottomAnchorToTop>
		<LeftAnchorToLeft>true</LeftAnchorToLeft>
		<RightAnchorToLeft>true</RightAnchorToLeft>
		<Text>STANCE</Text>
		<TextColor>
			<R>219</R>
			<G>158</G>
			<B>42</B>
		</TextColor>
		<NoWrap>true</NoWrap>
		<AlignCenter>false</AlignCenter>
		<AlignRight>false</AlignRight>
		<AlignLeft>true</AlignLeft>
	</Label>
	<Label item="SW_InvocationCaption">
		<Font>1</Font>
		<RelativePosition>true</RelativePosition>
		<AutoStretch>true</AutoStretch>
		<TopAnchorOffset>2</TopAnchorOffset>
		<BottomAnchorOffset>13</BottomAnchorOffset>
		<LeftAnchorOffset>62</LeftAnchorOffset>
		<RightAnchorOffset>2</RightAnchorOffset>
		<TopAnchorToTop>true</TopAnchorToTop>
		<BottomAnchorToTop>true</BottomAnchorToTop>
		<LeftAnchorToLeft>false</LeftAnchorToLeft>
		<RightAnchorToLeft>false</RightAnchorToLeft>
		<Text>INVOCATION</Text>
		<TextColor>
			<R>36</R>
			<G>152</G>
			<B>133</B>
		</TextColor>
		<NoWrap>true</NoWrap>
		<AlignCenter>false</AlignCenter>
		<AlignRight>true</AlignRight>
		<AlignLeft>false</AlignLeft>
	</Label>
	<StaticAnimation item="SW_WingGem">
		<ScreenID>SW_WingGem</ScreenID>
		<RelativePosition>true</RelativePosition>
		<Location>
			<X>214</X>
			<Y>1</Y>
		</Location>
		<Size>
			<CX>12</CX>
			<CY>12</CY>
		</Size>
		<Animation>A_SpinWingGem</Animation>
	</StaticAnimation>
"""

WING_PIECES = """		<Pieces>SW_StanceCaption</Pieces>
		<Pieces>SW_InvocationCaption</Pieces>
		<Pieces>SW_WingGem</Pieces>
"""


def style_stance_file(path: Path, menu_name: str | None = None) -> None:
    text = path.read_text(encoding="ascii")
    if 'item="SW_StanceCaption"' not in text:
        schema = re.search(r"<Schema[^>]*/>", text)
        if schema is None:
            fail(f"missing Schema in {path.name}")
        text = text[:schema.end()] + "\n" + WING_BLOCK + text[schema.end():]
        rail_anchor = "\t\t<Pieces>SW_StanceLabel</Pieces>\n"
        if rail_anchor not in text:
            fail(f"missing stance rail pieces in {path.name}")
        text = text.replace(rail_anchor, WING_PIECES + rail_anchor, 1)
    def center_in_wing(block: str, color, left: int, right: int) -> str:
        # The dynamic names center inside their wing box, so any stance or
        # invocation name the client returns sits balanced with no overlap —
        # the boxes stop short of the center gem on both sides.
        block = set_color(set_font(block, 3), "TextColor", color)
        block = set_value(block, "LeftAnchorOffset", left)
        block = set_value(block, "RightAnchorOffset", right)
        block = set_value(block, "AlignCenter", "true")
        block = set_value(block, "AlignLeft", "false")
        block = set_value(block, "AlignRight", "false")
        return block

    text = change_item(
        text, "Label", "SW_StanceLabel",
        lambda b: center_in_wing(b, GOLD_BRIGHT, 48, 208),
    )
    text = change_item(
        text, "Label", "SW_InvocationLabel",
        lambda b: center_in_wing(b, CYAN, 208, 64),
    )
    text = change_item(text, "Button", "SW_ButtonTemplate",
                       lambda b: set_font(b, 2))

    def root_style(block: str) -> str:
        block = set_container(block, "Size", CX=440, CY=56)
        if menu_name is not None:
            block = set_value(block, "MenuName", menu_name)
        block = set_value(block, "MinVSize", 56)
        return block

    text = change_item(text, "Screen", "StanceWnd", root_style)
    write_ascii(path, text)


def style_stance() -> None:
    style_stance_file(
        SKIN / "EQUI_StanceWnd.xml", "Stance and Invocation Command Bar"
    )
    # These two remain useful positional alternatives.  Unlike the retired
    # legacy frame variants, both already contain the complete July binding set.
    style_stance_file(SKIN / "EQUI_StanceWnd1.xml")
    style_stance_file(SKIN / "EQUI_StanceWnd2.xml")


def sync_canonical_variants() -> None:
    """Turn schema-stale variants into hidden, safe compatibility aliases."""
    marker = (
        "\n\n\t<!-- Canonical Legends compatibility alias: current bindings, "
        "signature visuals. -->"
    )
    for canonical_name, variant_names in CANONICAL_VARIANTS.items():
        source = (SKIN / canonical_name).read_text(encoding="ascii")
        source = re.sub(
            r"\n[ \t]*<MenuName>.*?</MenuName>", "", source,
            flags=re.DOTALL,
        )
        source, count = re.subn(
            r"(<Schema\b[^>]*/>)", rf"\g<1>{marker}", source, count=1
        )
        if count != 1:
            fail(f"missing Schema declaration in {canonical_name}")
        for variant_name in variant_names:
            write_ascii(SKIN / variant_name, source)


def style_experience_gauges() -> None:
    """One color identity per progression bar, everywhere it appears.

    XP is ember gold and AA is venom — including the LinesFill sub-tick
    overlay, which the stock skin left pure blue on every experience-type
    gauge so the two bars were indistinguishable at a glance.
    """
    path = SKIN / "EQUI_InventoryWindow.xml"
    text = path.read_text(encoding="utf-8")
    text = change_item(text, "Gauge", "IW_ExpGauge",
                       lambda b: style_gauge(b, GOLD, GOLD_BRIGHT))
    text = change_item(text, "Gauge", "IW_AltAdvGauge",
                       lambda b: style_gauge(b, CYAN, CYAN_BRIGHT))
    path.write_text(text, encoding="utf-8")

    path = SKIN / "EQUI_AAWindow.xml"
    text = path.read_text(encoding="ascii")
    text = change_item(text, "Gauge", "AAW_ExpGauge",
                       lambda b: style_gauge(b, CYAN, CYAN_BRIGHT))
    write_ascii(path, text)


def style_raid() -> None:
    path = SKIN / "EQUI_RaidWindow.xml"
    text = path.read_text(encoding="ascii")
    text = change_matching(
        text, "Page", r"RAID_(?:Member|Note)Page",
        lambda b: set_color(b, "TabTextActiveColor", GOLD_BRIGHT),
    )
    text = change_item(text, "Screen", "RaidWindow",
                       lambda b: set_value(b, "Text", "RAID // EIGHT"))
    write_ascii(path, text)


def main() -> int:
    style_buff_file(SKIN / "EQUI_BuffWindow.xml", "BW", 30,
                    "SPELL EFFECTS", 640)
    style_buff_file(SKIN / "EQUI_ShortDurationBuffWindow.xml", "SDBW", 15,
                    "SONG EFFECTS", 324)
    style_player()
    style_target()
    style_target_of_target()
    style_group()
    style_extended_targets()
    style_casting()
    style_spell_gems()
    style_hotbuttons()
    style_stance()
    style_experience_gauges()
    style_raid()
    sync_canonical_variants()
    print("Combat Command Center restyle: complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
