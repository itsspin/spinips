# SpinUI Studio

**The standalone offline layout, visibility, and accent-color editor for
SpinUI on EverQuest Legends.**

SpinUI Studio is its own release: `SpinUI-Studio.zip` contains everything the
editor needs and nothing else. It does not include Spin's Loremaster, the
guided installer, or the ready-to-install UI packages — download
`SpinUI-Installer.zip` or `SpinUI-Manual.zip` for those. The `spinui_reloaded`
and `layouts` folders inside this package are Studio's build sources: Studio
reads them to render pixel-accurate previews and to build your custom skin.

## Requirements

* Windows. No Python installation is needed; `SpinUIStudio.exe` is
  self-contained.
* Keep `SpinUIStudio.exe` inside the unpacked package folder, next to
  `spinui_reloaded`, `layouts`, and `UI_Spin_qeynos_LO1.ini`. If the
  executable is moved away from these assets it cannot start, and it will
  tell you exactly that.
* EverQuest can be closed, patching, or offline the entire time. Studio never
  reads game memory and never touches your game files unless you explicitly
  export into the game folder.

## Quick start

1. Unpack the whole `SpinUI-Studio.zip` anywhere (not inside the EverQuest
   folder).
2. Run `SpinUIStudio.exe`. On startup it looks for common EverQuest Legends
   installs and offers to import your newest
   `UI_<Character>_<server>_LO#.ini`, so the canvas begins exactly at your
   current in-game positions, sizes, hotbar/spell-gem orientation, and
   show-on-load states.
3. Pick your **game resolution** in the toolbar — 3440×1440 ultrawide,
   2560×1440 standard, or 3840×2160 4K. The canvas, the audited starting
   layout, and every exported coordinate use that resolution, so what you
   compose is what the client draws.
4. Edit (see below), then either **EXPORT INI** for a game-ready character
   layout, or **BUILD FINAL UI** to produce a complete custom skin + INI
   bundle with installation notes.

## Editing

| Action | How |
|---|---|
| Move a window | Drag it on the canvas, or select it and use the X/Y fields in the inspector. |
| Nudge precisely | Click the canvas, then arrow keys move 1px; Shift+arrows move 10px. Inside the window list, arrows browse rows instead. |
| Resize | Drag the gold lower-right handle, or edit W/H in the inspector. Windows the client sizes from XML are labeled "fixed size" and refuse resizing, exactly like the game. |
| Preview a hidden window | Double-click its row (or press Space with it selected). This changes the preview only. |
| Control the in-game start state | The **IN-GAME START STATE** selector writes `Show=` on export: preserve the imported INI's value, force show, or force hide. Preview and start state are deliberately separate controls. |
| Change chat arrangement | The chat preset picker (Combat Focus / Social Focus / Hybrid) offers deliberate resets of the 3440×1440 chat row. At other resolutions the release's audited arrangement for that resolution is used. |
| Recolor the theme | The **Venom**, **Gold**, and **Ember** swatches recolor the live preview and are baked into the real XML/TGA assets when you build. |
| Use a downloaded UI | **USE DOWNLOADED UI** points Studio at any EverQuest UI folder (a SpinUI copy, an EQInterface skin, anything with an `EQUI.xml`). See below. |
| Save your work | **SAVE PROJECT** writes a small JSON you can reopen later; Ctrl+S saves in place. |

## Using a downloaded UI (EQInterface skins and friends)

EverQuest stores window layout in the character INI independently of which
skin is loaded, so Studio can arrange a layout for **any** UI, not just
SpinUI. Click **USE DOWNLOADED UI** and choose the skin folder you downloaded
(the one you would copy into `uifiles\`). Studio then:

* reads that skin's own window XML — only the files its `EQUI.xml` manifest
  actually loads — and adopts each fixed window's **true declared footprint**
  (player/target plates, pet, inventory, bank, and so on), so silhouettes on
  the canvas match that UI, not SpinUI;
* targets that skin on export: the INI's `UISkin=` is set to the folder name,
  so the client loads your layout with that UI immediately;
* keeps rendering with clearly-labeled SpinUI placeholder art — Studio does
  not re-implement the client's SIDL renderer, so the third-party skin's own
  textures are never drawn or rebuilt.

The complete workflow: import your character INI (or start from a preset),
choose the downloaded UI folder, arrange everything offline, then **EXPORT
INI**. Install the downloaded UI folder into `uifiles\` yourself, drop the
exported INI beside `eqgame.exe`, and the layout comes up under that UI.

Details worth knowing:

* Windows the downloaded UI does not redefine keep their client-default
  footprints. Windows the client re-sizes at runtime (the EQ menu bar,
  compass strip, and the group window, which grows per member) keep Studio's
  curated sizes, because a skin's declared value is only an initial hint.
* Resizable windows (chat, hotbars, buffs, map…) are always your INI's
  sizes — same as in game.
* **BUILD FINAL UI** stays a SpinUI feature: it compiles the bundled SpinUI
  sources and refuses to run while a downloaded UI is active. Use **EXPORT
  INI** instead; the downloaded UI installs as-is.
* Choosing the bundled `spinui_reloaded` folder switches back to full SpinUI
  geometry and art.

## What transfers to the game, exactly

Studio edits the same pixel geometry EverQuest stores in your character INI
and previews it with the real SpinUI window textures:

* **Authoritative:** window positions, sizes, visibility/start states, all
  EverQuest anchor modes (left/right/top/bottom/half-screen center), chat
  container geometry, and your custom accent colors. Exported values
  round-trip through import with zero geometry differences, at every
  supported resolution.
* **Preserved from your imported INI:** chat routing, window transparency
  and fade settings, locks, hotbuttons, macros, spell loadouts, and any
  client settings Studio does not edit. They pass through untouched.
* **Simulated:** names, chat text, buffs, gauges, and item icons are clearly
  labeled deterministic sample data — only `eqgame.exe` can supply live
  state. Font rasterization and client-only content flow are a close visual
  reference, not client emulation.

## Installing what you built

**BUILD FINAL UI** writes a new folder (never overwriting anything) that
contains your custom skin, the character INI, the Studio project, and an
`INSTALL.txt` with the copy steps. In short: close EverQuest, copy the skin
folder into `uifiles\`, copy the INI beside `eqgame.exe`, and use
`/loadskin <skin_name> 1` in game.

**EXPORT INI** writes only the character layout file. If you export directly
into a game folder, Studio first asks you to confirm EverQuest is closed,
creates a timestamped byte-exact `.studio-backup`, and replaces the file
atomically.

> **Golden rule:** copy INI files only while the game is fully closed — the
> client rewrites UI INIs on logout.

## Troubleshooting

* **Studio won't start and mentions `spinui_reloaded`** — the executable was
  separated from its assets. Re-unpack the full `SpinUI-Studio.zip` and run
  it from inside that folder.
* **A preview error appears in the status bar** — Studio recovered and kept
  running; the full traceback is in
  `%LOCALAPPDATA%\SpinUIStudio\spinui-studio.log`.
* **Your layout looks different in game** — confirm the toolbar resolution
  matches the resolution EverQuest actually runs at, and that you loaded the
  exported INI for the right character (`UI_<Character>_<server>_LO1.ini`,
  exact capitalization).

From a source checkout the same editor runs with
`python tools/spinui_studio.py`; deterministic checks are `--selftest` and
`--render-preview out.png`.
