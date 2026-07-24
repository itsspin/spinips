# SpinUI manual installation

This package does not require the installer. It contains the complete
`spinui_reloaded` skin, SpinUI Studio, Spin's Loremaster, the optional
3440x1440 character INI, and the alternate ultrawide chat presets. The skin
itself includes a separately generated standard 2560x1440 default.

> **Safest layout option:** use `SpinUIInstaller.exe` from the guided Installer
> package when you want an ultrawide preset without replacing character data.
> **Keep Existing** is its recommended default. If you opt into Combat Focus,
> Social Focus, or Hybrid, it changes `UISkin`, audited window anchors,
> positions, sizes, and visibility (Show, Alpha, and fade settings), and it
> replaces the `[ChatManager]` section so the preset's Main/Combat/Social chat
> routing takes effect. It preserves locks, click-through, hotbar and spell
> data, loadouts, client-added settings, and all unknown values in every other
> section. An actual change receives a byte-exact,
> timestamped backup such as
> `UI_Spin_qeynos_LO1.ini.spinui-backup-20260720-214500`; applying an identical
> preset again writes nothing and creates no extra backup.

## 1. Close EverQuest completely

EverQuest rewrites character UI files when it exits. Do not copy or replace an
INI while the game is running.

## 2. Optional: design offline with SpinUI Studio

Run `SpinUIStudio.exe` directly from the extracted package, keeping it beside
the `spinui_reloaded`, `layouts`, and `UI_Spin_qeynos_LO1.ini` content.
(Studio is also published on its own as `SpinUI-Studio.zip`, with a dedicated
guide inside.) Pick your game resolution in the toolbar — 3440x1440,
2560x1440, or 3840x2160 — and the preview uses the real SpinUI textures and
the same INI geometry that is exported to EverQuest at that resolution.
Studio detects common game installs and offers
to import the newest character UI INI as its starting point. Drag windows,
drag the gold corner to resize supported controls, enter exact pixel
positions, nudge with arrow keys, and customize the Venom, Gold, and Ember
accents. **Preview on canvas** is separate from **In-game start state**, so a
hidden pet, bag, or inventory window can be positioned without forcing it open
at login. **USE DOWNLOADED UI** can point Studio at any other UI folder (for
example an EQInterface skin) to arrange a layout against that skin's true
window footprints and export an INI targeting it.

Use **SAVE PROJECT** to keep an editable JSON project, **SAVE PREVIEW** for a
full-resolution PNG, **EXPORT INI** for only the character layout, or **BUILD
FINAL UI** for a complete custom skin and INI bundle. Studio writes to a new
folder. If you explicitly export over a live character INI, confirm EverQuest
is closed; Studio creates a timestamped byte-exact backup and performs an
atomic replacement.

Names, chat lines, buffs, gauges, items, and similar runtime values in the
preview are deterministic samples. Their surrounding geometry and built skin
assets are authoritative, but only `eqgame.exe` can reproduce its own font
rasterization and supply live state. Perform one final in-game smoke test when
the servers are available.

## 3. Install the skin

For a clean update, rename or remove an older `spinui_reloaded` folder first,
then copy the complete new folder into the game's `uifiles` folder. This keeps
renamed or retired files from an earlier release from lingering in the skin.
The final path should look like:

```text
<EverQuest folder>\uifiles\spinui_reloaded\EQUI.xml
```

Common EverQuest folders include:

```text
C:\EQLegends
C:\Users\Public\Daybreak Game Company\Installed Games\EverQuest Legends
C:\Program Files (x86)\Steam\steamapps\common\EverQuest
```

In game, select it with:

```text
/loadskin spinui_reloaded 1
```

The `1` preserves your current window positions.

## 4. Optional: install a 3440x1440 layout

Skip this step if you want to keep your existing window arrangement or do not
play at 3440x1440. Standard 2560x1440 players can use the skin's validated
`default1440.ini`; the ultrawide character INI should not be squeezed onto a
16:9 screen.

The Manual package cannot perform the guided installer's surgical merge.
Copying one of its preset INIs over an existing character INI replaces that
whole file, including any chat/window preferences stored there. Before doing
so, make a separate backup of the character INI in the EverQuest folder. Its
name follows this pattern:

```text
UI_<Character>_<server>_LO1.ini
```

Choose `UI_Spin_qeynos_LO1.ini` for the combat-focused layout, or choose the
same file from `layouts\social-focus` or `layouts\hybrid`. Rename the selected
file to match your character's **existing filename exactly**, then copy it
beside `eqgame.exe`. Detected layouts can use `LO2`, `LO3`, and later slots;
retain that existing suffix instead of forcing `LO1`.

For a genuinely new character target, preserve the character's capitalization
and use the canonical lowercase server token. New manual targets default to
`LO1`:

```text
UI_Spin_qeynos_LO1.ini
```

| Server shown in Legends | Filename token |
|---|---|
| Erudin (European) | `erudin` |
| Freeport | `freeport` |
| Halas | `halas` |
| Neriak | `neriak` |
| Oggok | `oggok` |
| Paineel (European) | `paineel` |
| Qeynos | `qeynos` |
| Rivervale | `rivervale` |

The guided installer offers these exact choices and previews the final
filename before writing. If a manually entered name resolves to an existing
INI, it safely merges that file rather than treating the entry as permission
to overwrite it.

## 5. Run Spin's Loremaster

Move `Loremaster.exe` anywhere you prefer, then run it. In EverQuest, type:

```text
/log on
```

Loremaster searches common Daybreak and Steam locations automatically. If it
does not find the active log, click **LOCATE LOG** and select the EverQuest
folder or its `Logs` folder.

Drag the compact HUD into place and click **LOCK**. In the detailed Encounter
Lab, **OLDER / NEWER / LIVE** browse encounters and **Overview / Damage /
Healing / Targets / Timeline** change the analysis. **CLICK-THRU** enables
click-through only if Loremaster successfully reserves the recovery shortcut;
press **Ctrl+Alt+L** at any time to restore interaction.

A small gold-and-cyan Loremaster icon remains in the Windows notification area
beside the clock, or in its **^** overflow drawer. Left-click it to restore and
focus the HUD. Right-click for **OPEN LOREMASTER**, **HIDE HUD**, or
**EXIT LOREMASTER**. Hiding keeps log tracking and the Lore Lens hotkey active;
Exit closes the program completely. A hidden state is never carried into the
next normal launch.

Hover an item in EverQuest and press **Ctrl+Shift+E**. Lore Lens opens beside
the cursor in a clear reading state, captures only that bounded cursor region,
and uses Windows OCR before validating likely titles as exact EQL Wiki item
pages. The hovered tooltip takes priority while EQ is foreground. A copied EQ
item link, bracketed item, or EQL Wiki URL is used if Hover Scan cannot identify
the title; ordinary clipboard text only prefills the search field until you
confirm it. The shortcut, Hover Scan, wiki network access, high-contrast
palette, reduced motion, and text scale are configurable through **SETTINGS**.
Lore Lens never injects into or reads memory from `eqgame.exe`.

To start Loremaster with Windows without showing it before the game launches,
create a shortcut in `shell:startup` whose target is:

```text
"C:\path\to\Loremaster.exe" --wait-for-eq
```

The waiting process remains hidden and uses a lightweight process check until
`eqgame.exe` starts. After Loremaster has opened, its notification-area icon
remains available whenever the HUD is hidden.

## Updating or removing

- Update the skin while EQ is closed by replacing the complete
  `spinui_reloaded` folder, not by merging the two versions.
- Remove the skin by deleting only `uifiles\spinui_reloaded` while EQ is closed.
- Restore your layout from the backup you made in step 3.
- Loremaster stores its configuration and character records in
  `%LOCALAPPDATA%\SpinsLoremaster`.
