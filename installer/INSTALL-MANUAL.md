# SpinUI manual installation

This package does not require the installer. It contains the complete
`spinui_reloaded` skin, Spin's Loremaster, the optional 3440x1440 character
INI, and the alternate ultrawide chat presets. The skin itself includes a
separately generated standard 2560x1440 default.

## 1. Close EverQuest completely

EverQuest rewrites character UI files when it exits. Do not copy or replace an
INI while the game is running.

## 2. Install the skin

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

## 3. Optional: install a 3440x1440 layout

Skip this step if you want to keep your existing window arrangement or do not
play at 3440x1440. Standard 2560x1440 players can use the skin's validated
`default1440.ini`; the ultrawide character INI should not be squeezed onto a
16:9 screen.

Before replacing anything, make a backup of the character INI in the
EverQuest folder. Its name follows this pattern:

```text
UI_<Character>_<server>_LO1.ini
```

Choose `UI_Spin_qeynos_LO1.ini` for the combat-focused layout, or choose the
same file from `layouts\social-focus` or `layouts\hybrid`. Rename the selected
file to match your character's existing INI, then copy it beside `eqgame.exe`.

## 4. Run Spin's Loremaster

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
