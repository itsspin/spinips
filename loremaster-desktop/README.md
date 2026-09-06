# Loremaster desktop

This is the live Electron + React + TypeScript migration of Loremaster. The
renderer receives immutable protocol-v1 snapshots from a supervised local
Python engine; it never reads EverQuest files or exposes Node to React.

```powershell
pnpm install --frozen-lockfile
pnpm test:fixtures
pnpm build

# Source development (point to a Python 3.10+ executable when the Windows
# Python launcher is unavailable):
$env:LOREMASTER_PYTHON = "C:\path\to\python.exe"
pnpm dev
```

`pnpm dist:windows` produces the portable `Loremaster.exe` after
`../dist/LoremasterEngine.exe` has been built. The package deliberately has no
installer target.

Electron owns windowing, animation, settings, log-folder selection and engine
health. The parser engine remains authoritative for combat attribution,
charmed pets, mez/lull evidence and D0–D4 weekly raid events. The narrow preload
exposes only versioned state, folder selection, reset, and window commands.

## Native Loremaster themes

Loremaster includes three complete presentation systems using the same parser,
layout and accessibility behavior:

- **Vellum & Ember** is the default and matches `spinui_reloaded` with oiled
  leather surfaces, parchment text, brass edges and restrained spirit-blue
  selections.
- **Midnight Frost Glass** matches `spinui_glass` with deep translucent panes,
  ice-blue edges, mint actions and violet selections.
- **Pearlescent** matches `spinui_pearlescent` with black-pearl surfaces, ivory
  text, champagne edges, seafoam actions and rose-lilac accents.

**The Ascent** (HUD → ASCENT) adds bounded session XP history, percentage/hour,
AA/hour, and an estimated next level when the evidence supports it. Optional
same-level checkpoints cover logs without XP quantities. Upcoming spells use
the selected game's `spells_us.txt`: multi-class filtering, search, 20-row
pages, and wiki links. No extra download is required for the spell catalog.

The theme picker in Settings applies immediately to the Seed, expanded HUD,
Settings, alerts, crowd-control timers and Combat Archive. The selection is
stored with the other desktop settings and restored before windows are shown,
so changing themes never restarts the parser or moves an overlay.

Alert Sound Studio includes four locally generated cues plus Silent, with a
separate selection for charm breaks, tells, summons, deaths, big hits, name
calls, mez warnings and lull warnings. Each event may instead use a local WAV,
MP3, OGG or M4A file selected through the native file picker. Custom audio is
validated and size-limited by Electron's main process; the sandboxed renderer
never receives general filesystem access.

Weekly D0–D4 progress now comes directly from the logged instance-entry line
and the later boss-kill evidence. Loremaster records the exact Solo/Group mode,
difficulty label, zone, timestamp, and evidence with the clear. A confirmation
prompt and manual grid remain as conservative fallbacks when the log truly does
not identify a tier. Loremaster deliberately does not scrape EverQuest's
Instance Information window or reserve a global lockout-screen hotkey.

## Adventure memory and Spoils Chronicle

Completed encounters and observed loot survive restarts in a local SQLite
journal using WAL mode, bounded live snapshots, and paged searches. The combat
Archive clearly distinguishes a durable summary from a current fight: totals,
duration, kills, zone, and raid context persist, while actor or timeline detail
is never invented for older summaries.

The **Spoils Chronicle** recognizes ordinary corpse loot, stacks, auto-sales,
Dragon Hoard/depot and currency storage, inventory placement, item merges, and
automatic ranked-item upgrades. Search and filter the complete local history by
item, source, zone, owner, and D0–D4 context. Selecting an item can show cached
EQL Wiki stats, drops, quests, and notes alongside the original log evidence;
network lookup is optional and can be disabled while cached cards remain usable.
All network parsing runs in Electron's main process, never in the renderer.

Combat actors retain stable, accessible colors across the Seed, HUD, and
Archive. Ability evidence is categorized as melee, spell, DoT, proc, pet,
damage shield, or healing, with unknown evidence kept explicitly unknown.
Ranger bow lines are parsed as ranged evidence, including critical,
strikethrough, and Double Bow Shot descriptors; a single combined shot line is
counted once, while misses, blocks, dodges, and rune absorbs remain
zero-damage attempts.

The DPS overlay has four content views: Self isolates the player, Group combines
the player and owned pets alongside verified party members, Pet isolates charmed
and summoned damage, and All shows each contributor separately. Choose the
layout in **Settings → HUD Behavior → DPS Overlay Appearance**:

- **Seed Only** keeps the Rune Seed without the DPS companion.
- **Auto** puts the companion where it fits best; **Above** prefers a stacked
  layout and **Right / Sidecar** prefers the Seed's right edge, flipping at the
  monitor edge when necessary.
- **Meter Only** replaces the Seed with the interactive meter, including a
  draggable header and direct HUD, Analyze, and Seed controls. Its position is
  remembered separately and it remains visible even before combat data arrives.

Use **DETAIL** on the Rune Seed to enter temporary **Inspect** mode, then click
your name, a pet, or a party member for evidence-backed ability damage, DPS,
hits, and maximum hit. Escape, Done, losing focus, or the idle timeout restores
click-through. Meter Only is always interactive. Per-actor sources come from
the log; older summaries or ambiguous non-melee evidence are labeled honestly
instead of inventing spell names. Detail pages browse up to the 24 top recorded
abilities per actor while preserving the full damage total. The Seed's view and eye controls still cycle
or hide the companion, and background opacity remains adjustable without
dimming labels, values, or bars.

Active mez/lull timers follow their controlled targets, not the current DPS
encounter. Starting a different fight does not dismiss them. The compact
companion reserves space for control timers before extra DPS rows; when both
sections cannot fit, it keeps the timers visible and temporarily omits the
meter. Hidden timer counts remain explicit on unusually crowded displays.

The Gear Path surface imports EQ Legends Tools' version-1 character-sheet JSON
and EverQuest's `/outputfile inventory` TXT locally. It identifies goal items
already equipped or held in bags/bank and groups missing pieces by source zone.
Item/source metadata is refreshed on explicit user action and cached locally.
Credit: [EQ Legends Tools](https://eqlegendstools.com/) by **FlammHammer**.

Because releases remain portable (no installer), Settings includes a complete
SpinUI Update Center. It checks the official release at most once per day,
shows the running Loremaster version, and downloads a newer portable build only
after the user chooses Update. The executable is authenticated against both
GitHub's asset digest and `SHA256SUMS.txt`, staged beside app data, then replaced
by a rollback-capable helper; the new build must report a healthy renderer and
parser start or the previous executable is restored.

The same surface verifies `spinui_reloaded` and `spinui_glass` against the
release's exact file manifest. A skin install is blocked while `eqgame.exe` is
running, staged and verified before replacement, and limited to the selected
`uifiles` child with a rollback copy. Character layout INIs, EQ logs, other
skins, Loremaster settings, and the combat/loot journal are never update
targets. Automatic checks are enabled by default; downloads and installs always
remain explicit.

See [milestone 2](../docs/LOREMASTER_MILESTONE_2.md) for the live scope and
release validation gates.

The development-only overlay integration probe exercises real Electron window
placement, contributor clicks, pagination, scaling, material opacity, and
position recovery. Set `LOREMASTER_DESKTOP_DATA_DIR` to an isolated temporary
profile and `LOREMASTER_OVERLAY_PROBE_PATH` to its JSON report path before
running `pnpm preview`; it writes sanitized screenshots beside the report and
closes itself. It does not run in packaged releases.
