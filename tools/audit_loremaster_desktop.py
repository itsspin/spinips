#!/usr/bin/env python3
"""Static contract for the Electron Loremaster themes and supported tracking.

This audit intentionally checks the seams where a partially implemented theme
can look correct in the main window while leaving the Seed, alerts, or archive
on the old palette.  It also keeps the retired Instance Information screen OCR
from returning through a stale hotkey or protocol field.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
DESKTOP = REPO / "loremaster-desktop"


class AuditFailure(RuntimeError):
    pass


def fail(message: str) -> None:
    raise AuditFailure(message)


def read(relative: str) -> str:
    path = REPO / relative
    if not path.is_file():
        fail(f"required source is missing: {relative}")
    return path.read_text(encoding="utf-8")


def require(source: str, values: tuple[str, ...], owner: str) -> None:
    missing = [value for value in values if value not in source]
    if missing:
        fail(f"{owner} is missing: " + ", ".join(missing))


def audit_theme_contract() -> None:
    protocol = read("loremaster-desktop/src/protocol.ts")
    app = read("loremaster-desktop/src/App.tsx")
    renderer = read("loremaster-desktop/src/main.tsx")
    electron = read("loremaster-desktop/electron/main.ts")
    companion_layout = read("loremaster-desktop/electron/companion-layout.ts")
    preload = read("loremaster-desktop/electron/preload.ts")
    base_styles = read("loremaster-desktop/src/styles.css")
    themes = read("loremaster-desktop/src/themes.css")
    readme = read("loremaster-desktop/README.md")
    portable_updater = read("loremaster-desktop/electron/portable-updater.ts")
    skin_updater = read("loremaster-desktop/electron/spinui-updater.ts")

    if not re.search(
        r'type\s+LoremasterTheme\s*=\s*["\']vellum["\']\s*\|\s*["\']glass["\']',
        protocol,
    ):
        fail("protocol must expose the exact vellum | glass theme union")
    require(protocol, ("uiTheme: LoremasterTheme",), "renderer protocol")
    require(
        protocol,
        (
            'SeedMeterMode = "self" | "group" | "pet" | "all"',
            "SeedCompanionLayout",
            "seedMeterVisible: boolean",
            "seedMeterMode: SeedMeterMode",
            "seedMeterOpacity: number",
        ),
        "Seed meter settings protocol",
    )
    require(
        electron,
        ("uiTheme", '"vellum"', '"glass"', "settings:changed"),
        "Electron settings persistence",
    )
    if electron.count("uiTheme") < 6:
        fail("Electron does not normalize, persist, update, and seed the theme")
    require(
        electron,
        (
            "seedMeterVisible",
            "seedMeterMode",
            "seedMeterOpacity",
            "visibleSeedMeterRows",
            "boundedCompanionLayout",
            '"window:companion-layout"',
            "workArea.height - gap * 2",
        ),
        "Electron Seed meter persistence and sizing",
    )
    require(
        companion_layout,
        (
            "COMPANION_MAX_METER_ROWS",
            "COMPANION_MAX_CONTROL_ROWS",
            "METER_FOOTER_HEIGHT",
            "JOINED_SECTION_BORDER_HEIGHT",
            "const meterHiddenRows = Math.max",
            "const controlHiddenRows = Math.max",
        ),
        "bounded companion layout math",
    )
    if "controlWindow.setIgnoreMouseEvents(true)" not in electron:
        fail("the companion meter must remain click-through over EverQuest")
    require(
        preload,
        ("onCompanionLayout", '"window:companion-layout"'),
        "bounded companion layout bridge",
    )
    require(
        app,
        (
            "VELLUM & EMBER",
            "MIDNIGHT FROST GLASS",
            "SpinUI Reloaded",
            "SpinUI Glass",
            'role="radiogroup"',
            "uiTheme",
        ),
        "Settings theme picker",
    )
    require(
        app,
        (
            "ALERT SOUND STUDIO",
            "Rune Pulse",
            "Crystal Chime",
            "Ember Alarm",
            "Temple Bell",
            "sound-preset-trigger",
            "sound-preset-menu",
            'aria-haspopup="listbox"',
            "previewConfiguredSound",
            "soundKindForAlert",
        ),
        "per-alert sound studio",
    )
    require(
        protocol,
        ("AlertSoundKind", "AlertSoundPreset", "soundProfiles"),
        "sound profile protocol",
    )
    require(
        electron,
        (
            "alerts:choose-sound",
            "alerts:read-sound",
            "CUSTOM_SOUND_MAX_BYTES",
            "normalizeSoundProfiles",
        ),
        "custom sound boundary",
    )
    require(
        preload,
        ("chooseAlertSound", "readAlertSound"),
        "custom sound preload API",
    )
    if 'role="radio"' not in app and "aria-pressed" not in app:
        fail("theme choices need an accessible selected-state contract")
    if app.count("applyTheme(") < 4:
        fail("theme is not applied to all main, alert, and control surfaces")
    require(
        app,
        (
            "seed-meter-quickbar",
            "seed-meter-surface",
            "seed-meter-row",
            "seed-meter-opacity",
            "hiddenMeterRows",
            "hiddenControls",
            "onCompanionLayout",
            'detail: "SELF + PETS"',
            'const seedMeterModes: readonly SeedMeterMode[] = ["self", "group", "pet", "all"]',
        ),
        "configurable compact DPS meter",
    )
    if "activeGroup" in app or "groupMembers.has" in electron:
        fail("historical group actors must trust their persisted role instead of the current roster")
    if re.search(r"seed-meter-surface[^\n]*style=\{\{\s*opacity", app):
        fail("Seed meter opacity must not fade its text and DPS values")
    require(
        renderer,
        ("data", "theme", "document.documentElement"),
        "pre-render theme seed",
    )
    if renderer.count('import "./themes.css";') != 1:
        fail("theme stylesheet must be imported exactly once after the base CSS")
    if not re.search(r"\.settings-toggle\s*\{[^}]*position:\s*relative", base_styles):
        fail("Settings toggles need a local containing block to prevent focus-scroll blanks")
    require(
        base_styles,
        (".settings-toggle input", "width: 1px", "height: 1px", "clip-path: inset(50%)"),
        "accessible Settings toggle concealment",
    )

    lowered = themes.casefold()
    require(
        lowered,
        (
            '[data-theme="glass"]',
            "#0c0906",
            "#130e09",
            "#685030",
            "#d0a254",
            "#f1e7d4",
            "#03080e",
            "#060f18",
            "#30798f",
            "#69e1f2",
            "#55f2be",
            "#ab80ff",
            "#e8f8fc",
            "--danger",
            "--warning",
            "--success",
        ),
        "canonical theme palette",
    )
    for selector in (
        ".rune-seed",
        ".loremaster-shell",
        ".settings-card",
        ".alert-surface",
        ".seed-control-surface",
        ".seed-meter-surface",
        ".weekly-card",
        ".gear-card",
        ".archive-shell",
        ".archive-fights",
        ".archive-report",
        ".theme-picker",
        ".theme-option",
    ):
        if selector not in themes:
            fail(f"theme stylesheet does not cover {selector}")
    require(
        base_styles,
        (
            ".seed-meter-quickbar",
            ".seed-meter-surface",
            ".seed-meter-row",
            "--seed-meter-opacity",
        ),
        "compact DPS meter styling",
    )
    if "backdrop-filter" in lowered:
        fail("transparent Electron windows must not depend on live blur")
    require(
        readme,
        ("Vellum & Ember", "Midnight Frost Glass", "spinui_reloaded", "spinui_glass", "Alert Sound Studio"),
        "Loremaster desktop documentation",
    )
    require(
        protocol,
        ("UpdateCenterState", "UpdateComponentId", "eqRoot: string", "autoCheckUpdates: boolean"),
        "update-center protocol",
    )
    require(
        app,
        ("SPINUI UPDATE CENTER", "CHECK ALL", "UPDATE ALL", "YOUR DATA STAYS YOURS"),
        "Settings update center",
    )
    require(
        preload,
        ("getUpdateState", "chooseUpdateEqRoot", "installUpdates", "onUpdateState"),
        "update-center preload API",
    )
    require(
        electron,
        ("PortableUpdateService", "SpinUISkinUpdateService", "acknowledgePortableUpdateRelaunch"),
        "update-center main integration",
    )
    require(
        portable_updater,
        ("SHA256SUMS.txt", "PORTABLE_EXECUTABLE_FILE", "expectedSha256", "previous"),
        "portable update verification and rollback",
    )
    require(
        skin_updater,
        ("SpinUI-Update.json", "SpinUI-UI.zip", "eqgame.exe", "treeSha256", "backups"),
        "skin update verification and rollback",
    )


def audit_retired_lockout_ocr() -> None:
    runtime_sources = {
        "Electron main": read("loremaster-desktop/electron/main.ts"),
        "renderer": read("loremaster-desktop/src/App.tsx"),
        "protocol": read("loremaster-desktop/src/protocol.ts"),
        "desktop worker": read("loremaster/desktop_worker.py"),
    }
    forbidden = (
        "scan-alt-z",
        "scanAltZ",
        "altZLockout",
        "altZScan",
        "instance_lockout_ocr",
        "instance_lockouts",
        "globalShortcut",
    )
    for owner, source in runtime_sources.items():
        found = [value for value in forbidden if value.casefold() in source.casefold()]
        if found:
            fail(f"{owner} still contains retired lockout OCR: {', '.join(found)}")
    for relative in (
        "loremaster/instance_lockout_ocr.py",
        "loremaster/tests/test_instance_lockout_ocr.py",
    ):
        if (REPO / relative).exists():
            fail(f"retired lockout OCR file still exists: {relative}")


def main() -> int:
    try:
        audit_theme_contract()
        audit_retired_lockout_ocr()
    except AuditFailure as exc:
        print(f"Loremaster desktop audit: FAIL\n  {exc}", file=sys.stderr)
        return 1
    print("Loremaster desktop audit: ALL PASS")
    print("  Vellum & Ember + Midnight Frost Glass | persistent cross-window themes")
    print("  Seed meter | self abilities + whole group + pets + all | opacity + quick toggle")
    print("  verified portable + isolated skin updates | rollback-safe settings workflow")
    print("  no Instance Information OCR or reserved Ctrl+Shift+Z shortcut")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
