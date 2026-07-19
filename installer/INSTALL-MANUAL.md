# SpinUI manual installation

This package does not require the installer. It contains the complete
`spinui_reloaded` skin, Spin's Loremaster, the default 3440x1440 INI, and the
alternate layout presets.

## 1. Close EverQuest completely

EverQuest rewrites character UI files when it exits. Do not copy or replace an
INI while the game is running.

## 2. Install the skin

Copy the complete `spinui_reloaded` folder into the game's `uifiles` folder.
The final path should look like:

```text
<EverQuest folder>\uifiles\spinui_reloaded\EQUI.xml
```

Common EverQuest folders include:

```text
C:\Users\Public\Daybreak Game Company\Installed Games\EverQuest Legends
C:\Program Files (x86)\Steam\steamapps\common\EverQuest
```

In game, select it with:

```text
/loadskin spinui_reloaded 1
```

The `1` preserves your current window positions.

## 3. Optional: install a 3440x1440 layout

Skip this step if you want to keep your existing window arrangement.

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

To start Loremaster with Windows without showing it before the game launches,
create a shortcut in `shell:startup` whose target is:

```text
"C:\path\to\Loremaster.exe" --wait-for-eq
```

The waiting process remains hidden and uses a lightweight process check until
`eqgame.exe` starts.

## Updating or removing

- Update the skin by copying a newer `spinui_reloaded` folder over the existing
  folder.
- Remove the skin by deleting only `uifiles\spinui_reloaded` while EQ is closed.
- Restore your layout from the backup you made in step 3.
- Loremaster stores its configuration and character records in
  `%LOCALAPPDATA%\SpinsLoremaster`.
