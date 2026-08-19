<p align="center">
  <img src="docs/screenshots/spinui-logo-dual.jpg" alt="SPINUI logo divided between warm Vellum and Ember brass on the left and Midnight Frost Glass on the right" width="1000">
</p>

<h1 align="center">SpinUI + Spin's Loremaster</h1>

<p align="center"><strong>See more of Norrath. Read every fight.</strong></p>

<p align="center">
  Two complete native interfaces for EverQuest Legends—classic Vellum &amp; Ember and optional Midnight Frost Glass—built around a clear combat cockpit and paired with a log-driven encounter lab, adventure ledger, item companion, and alert system.
</p>

<p align="center">
  <a href="https://github.com/itsspin/spinips/releases/latest"><strong>Download the latest release</strong></a>
  · <a href="#quick-start">Quick start</a>
  · <a href="#spinui-glass--midnight-frost">Explore Glass</a>
  · <a href="#spins-loremaster">Explore Loremaster</a>
  · <a href="#layout-profiles">Choose a layout</a>
  · <a href="#4k-readability-with-spinfourkayyy">4K scaling</a>
  · <a href="#sharper-world-textures-with-spintexture">Texture upgrades</a>
</p>

<p align="center">
  Windows · 7 validated resolutions · 21 generated layout profiles · Packaged app needs no Python · No injection or game-memory access
</p>

> [!NOTE]
> SpinUI is an independent community project for EverQuest Legends and is not affiliated with or endorsed by Daybreak Game Company.

## The complete system, live

[![Ultrawide EverQuest Legends gameplay using SpinUI, with companion, player, target and action controls centered along the lower screen, chat at the bottom, map and effects at upper right, and Loremaster at right](docs/screenshots/spinui-gameplay-overview.jpg)](docs/screenshots/spinui-gameplay-overview.jpg)

<p align="center"><em>SpinUI in live play: a clear world view, centered combat controls, focused chat lanes, and Loremaster alongside the game.</em></p>

| SpinUI | Layout profiles | Spin's Loremaster |
|---|---|---|
| Shared chrome gives native windows one leather, brass, ember, and spirit-blue visual language. | Seven resolutions and three play styles, generated and checked in all 21 combinations. | Live combat, progression, loot, travel, Lore Lens, and alerts from ordinary EQ logs plus one-shot, user-triggered screen OCR. |

SpinUI is more than a recolor. It re-composes EverQuest's native XML, textures, and layout data into a single cockpit: the world stays open, the combat loop stays on one eye-line, and information appears where it earns the space.

## SpinUI Glass — Midnight Frost

[![SpinUI Glass running in EverQuest Legends with the Equipment character sheet, companion commands, player bars, stance and invocation rows, spells, hotbuttons, chat, loot, group, Extended Targets, and Loremaster DPS Rune](docs/screenshots/spinui-glass-live.png)](docs/screenshots/spinui-glass-live.png)

<p align="center"><em>Midnight Frost live in EverQuest Legends at 3440×1440: the complete Glass character sheet and combat cockpit in game.</em></p>

**`spinui_glass` is a complete optional skin, not an overlay or a handful of recolored windows.** Every native window receives the same visual system: translucent midnight panes, disciplined one-pixel ice edges, subtle inner depth, frosted typography, toxic-mint interaction light, and violet arcane selection energy. The result borrows the information discipline of modern MMO interfaces while remaining unmistakably EverQuest.

- **Glass that remains readable.** Surface alpha is controlled rather than simply lowered. Nested panes gain depth through quiet tonal steps, while text and semantic HP, mana, endurance, XP, and AA colors keep their gameplay meaning.
- **Pixel-built control states.** Buttons, checkboxes, sliders, radio buttons, recessed fields, spell holders, tabs, titlebars, scrollbars, chat filigree, and item wells share one generated atlas language with clear normal, hover, pressed, active, and disabled states.
- **A whole-interface transformation.** Inventory, bags, bank, merchant, pet, group, player, target, Extended Targets, stance and invocation bars, spell deck, chat, map, options, tradeskills, loot, journal, and the spellbook all inherit the Glass system.
- **Modern without client tricks.** Glass uses ordinary EQ SIDL XML and TGA textures—no injection, shaders, game-memory reads, or background process. The look stays crisp because it is painted into native assets rather than blurred over the game.
- **Binding-safe by construction.** `spinui_glass` is generated from `spinui_reloaded`; an automated semantic-tree audit proves all 257 XML documents and more than 13,000 item, ScreenID, and EQType bindings remain aligned.

<p align="center">
  <a href="docs/previews/spinui_glass_equipment.png">
    <img src="docs/previews/spinui_glass_equipment.png" alt="SpinUI Glass inventory and character sheet with frosted text, cyan wells, violet progression, and mint active states" width="46%">
  </a>
  <a href="docs/previews/spinui_glass_spellbook.png">
    <img src="docs/previews/spinui_glass_spellbook.png" alt="SpinUI Glass Codex with etched midnight pages, violet binding, icy spell wells, and mint memorized-state cues" width="46%">
  </a>
</p>

<p align="center"><em>The Glass character sheet and purpose-built Glass Codex use the same surface hierarchy as the combat HUD.</em></p>

Both skins ship together. Use `/loadskin spinui_glass 1` for Midnight Frost or `/loadskin spinui_reloaded 1` for Vellum & Ember; the `1` preserves the current window positions.

## The HUD, rebuilt around the fight

[![Close-up of SpinUI companion controls, player and target bars, stance row, spell gems and hotbutton clusters](docs/screenshots/spinui-combat-hud-detail.png)](docs/screenshots/spinui-combat-hud-detail.png)

<p align="center"><em>One-glance combat: pet commands, vitals, stance, spell gems, hotbuttons, and target status without covering the fight.</em></p>

- **One centered eye-line.** Player, target, casting, stance, spell gems, and actions share a bottom-anchored combat band instead of competing with the center of the world.
- **Information has a color grammar.** HP is red, mana is blue, endurance and XP are brass gold, and AA is spirit blue. XP and AA keep persistent tick marks while their fills remain correctly scaled to the displayed percentage.
- **Native controls stay native.** Hotbuttons, spell gems, pet commands, drag/drop, tooltips, loadouts, and client-driven usable/unusable states retain their original bindings.
- **Attack state is unmistakable.** Both themes place a thick, outline-only rail directly around the normal player command frame. EverQuest natively tints it from neutral to vivid red to create the flash, the interior remains completely untouched, and every PlayerWindow display type retains the full working bar.
- **The spell deck adapts to the player.** Its default remains the compact icon-only deck; choose **Display Types -> SpinUI Spell Ledger** for a resizable alternate with every icon, live spell name, and memorized slot number.
- **Extended targets use the space you give them.** XTAR keeps its familiar single-column default, then flows all 23 Legends targets into additional complete columns as the window is widened—without losing role, aggro, cast, HP, mana, or endurance data.
- **Effects read through the artwork.** Spell, song, and pet-effect durations sit directly on their icons in shadowed ember gold instead of growing into opaque timer slabs.
- **Key HUD, chat, and map windows get lighter when they are not the focus.** Translucency and soft fade behavior reduce visual weight without hiding the information you deliberately placed.
- **The shape is deliberate at every resolution.** SpinUI recalculates docks and spacing instead of blanket-scaling text and click targets into blur.

## Inventory becomes a character sheet

<p align="center">
  <a href="docs/screenshots/spinui-inventory-equipment.png">
    <img src="docs/screenshots/spinui-inventory-equipment.png" alt="SpinUI Inventory Equipment tab showing equipped items, character vitals, primary attributes, resistances, identity, currencies and bags" width="666">
  </a>
</p>

<p align="center"><em>A complete character sheet inside the inventory—equipment, vitals, attributes, resistances, identity, currencies, and bags in one coherent panel.</em></p>

The compact **660×668 Equipment tab** preserves all **23 native equipment slots** while turning the stock window into a readable character sheet:

- Character Vitals, Primary Attributes, Resists, Additional Modifiers, and Mitigation are grouped semantically rather than scattered around decorative empty space.
- Armor, jewelry, weapons, ammo, and both Any slots remain native equipment wells with their original ScreenID and EQType behavior.
- The class crest remains the functional drop-to-auto-equip target; the bag grid, currencies, stock buttons, drag/drop, and tooltips remain client-driven.
- Multiclass Loadouts use the same compact canvas while preserving the current Legends bindings, swap indicators, and allow/deny states.

## Spin's Loremaster

Loremaster turns the text log EverQuest already writes into a live **Adventurer's Chronicle**. It understands encounters, pets, charmed creatures, healing, progression, loot, factions, and travel, then presents them in a movable companion that looks like part of SpinUI—not a generic meter pasted over it.

[![Spin's Loremaster showing the rounded 92 by 48 Rune Seed states, expanded encounter parser, integrated alert rail, and Lore Lens](docs/previews/loremaster_panel.png)](docs/previews/loremaster_panel.png)

<p align="center"><em>Quiet in play, rich on demand: the Rune Seed unfolds into the complete parser, ledger, alert controls, and Lore Lens.</em></p>

[![Loremaster Settings showing Lore Lens and Alerts & Notifications, including sound, charm-break, and accessibility controls](docs/screenshots/loremaster-settings-and-hud.png)](docs/screenshots/loremaster-settings-and-hud.png)

<p align="center"><em>The full settings surface remains available for alert thresholds, sound, accessibility, Lore Lens, and advanced trigger choices.</em></p>

| Capability | What Loremaster does |
|---|---|
| **Encounter Lab** | Encounter, Session, and Records views with multi-enemy pulls plus actor, ability, healing, target, and a timeline grouped into two-second buckets. |
| **Durable Combat Archive** | Preserves encounter totals, duration, kills, zone, and raid context across restarts, while clearly labeling older summary-only records instead of fabricating actor or timeline detail. |
| **Automatic raid context** | Reads the logged Solo/Group instance suffix and D0–D4 label, then attaches exact zone, tier, mode, timestamp, and kill evidence to the weekly clear. Manual confirmation remains only for genuinely unknown tiers. |
| **Spoils Chronicle** | Stores and searches observed loot across restarts, including stacks, storage, currency, auto-sales, merges, and ranked upgrades; filters by item, source, zone, owner, and raid tier. |
| **Item intelligence** | Shows optional cached EQL Wiki stats, drops, quests, and notes beside the original loot evidence. Network lookup can be disabled without losing the local ledger or cached cards. |
| **Adventure ledger** | XP/hour, time to level, kills, loot, all ten mote grades, coin and plat/hour, factions, skills, zones, and a bounded death recap. |
| **Pets and charms** | Credits summoned pets and conservatively claimed charmed creatures; same-name charm totals are included but clearly labeled as estimates when the text log cannot distinguish actor IDs. |
| **Optional DPS attribution** | Keeps total personal DPS unchanged while optionally exposing separate Self, Charmed pet, and Summoned pet damage/DPS rows for both the current encounter and session. |
| **Mez control** | Starts a sleek target countdown only after your own recognized mez actually lands. Ranked durations use EQL's whole-tick scaling; identical mob names group honestly, and `LAST TICK` exposes the server-tick uncertainty instead of inventing an exact wake-up second. |
| **Rune Seed HUD** | A rounded 92×48 combat capsule pairs the generated SpinUI brass cog with a separate, overlap-free DPS lane. DPS is its only seeded metric; players can deliberately star up to four ledger cards to create a scrollable wheel. LIVE, READY, STALE, and ALERT use restrained trim motion; click to morph into the full parser, drag when unlocked, or right-click for settings. |
| **Lore Lens** | One-shot hovered-item OCR, exact EQL Wiki validation, cached results, and a configurable `Ctrl+Shift+E` shortcut. |
| **Plane of Sky journey planner** | Optionally recognizes looted turn-ins, privately imports `/outputfile inventory`, shows reward/class use, tracks missing pieces, recommends the remaining islands or bosses, and can place the selected island on EQ map layer 3. |
| **Alerts** | Opt-in banners and sound for tells, summons, deaths, charm breaks, big hits, name calls, and fight completion. Compact banners stay beside the Rune Seed with edge-safe Auto, Right, Left, Above, and Below placement choices. |
| **Character continuity** | Follows standard `eqlog_*.txt` activity and supports manual log-folder selection. Packaged builds store selected records and settings under `%LOCALAPPDATA%\SpinsLoremaster`; source runs keep state beside `loremaster.py`. |
| **Two native themes** | Vellum & Ember and Midnight Frost Glass are selectable in Settings so Loremaster can visually belong to either SpinUI skin. |
| **One-click verified updates** | The Settings Update Center shows the installed Loremaster version, verifies new portable builds before a rollback-safe restart, and can check, install, or repair Reloaded and Glass independently without touching EQ logs, character layouts, other skins, or the durable combat and loot journal. |

### Charm intelligence that respects the log

[![Close-up of EverQuest combat using SpinUI with a red CHARM BROKE — AN ABHORRENT Loremaster warning beside the charmed creature](docs/screenshots/loremaster-charm-break-alert-detail.jpg)](docs/screenshots/loremaster-charm-break-alert.jpg)

<p align="center"><em>When alerts are enabled, a proven charm break ignites the Rune Seed and raises a short adjacent danger toast with optional sound while the encounter remains visible.</em></p>

Loremaster does not claim every nearby creature as yours. Ownership comes from owner-only pet chatter or a known charm landing shortly after your cast. A charm-break alert fires once only when a positively claimed active charm receives a recognized charm-spell fade. Zoning, player or pet death, intentional replacement, a wrong target, and duplicate fade lines stay silent.

The global alert switch ships **off**. The **Charmed pet breaks** and **Play alert sound** preferences are ready by default, so enabling alerts is enough to activate the warning; each can still be disabled independently.

### Mez and lull timing without fake precision

Loremaster recognizes the Enchanter mez line plus supported Bard and Necromancer control songs/spells directly from the normal EQ text log. A cast alone never starts a timer: the target-specific success line must follow your own recent cast. The same evidence rule now covers Pacify, Calm, Lull, Soothe, Calm Animal, and Pacification. Harmony and Lull Animal do not expose a target-specific success line in EQL, so Loremaster labels those casts **UNCONFIRMED** instead of inventing a timer.

Fizzles, interrupts, single-target resists, fades, overwrites, damage breaks, deaths, zoning, character changes, and reset all close the appropriate state. AE mez keeps accepting interleaved successes even when another target resists. If a nearby caster overlaps a spell that shares the same actorless landing message, Loremaster quarantines the ambiguous result instead of double-counting it as yours.

EQ applies spell durations in six-second server ticks, but the log does not reveal the tick phase. Loremaster therefore counts down the duration that is guaranteed safe, then shows **LAST TICK** until the fade arrives or the final possible tick passes. Same-named enemies appear as `×N` with the earliest deadline. The shared control stack stays at four rows plus an overflow count, follows the Rune Seed or expanded HUD, never takes keyboard focus, and is click-through during play. Mez and lull visibility, warning thresholds, and one-shot sounds are independently configurable in **Settings → Crowd Control Timers**.

### An honest encounter model

- Your first action—or an owned pet's first action—opens a fight. Ten seconds of true combat silence closes it.
- Nearby activity can extend an active fight only within a 20-second grace window of your own last action, so tagging one mob does not inherit an entire camp's timeline.
- Actor rows are observational. EverQuest logs what your client can see; they are not presented as a guaranteed group or raid roster.
- Same-named charmed-pet damage is included as an explicit estimate because the text log has no actor IDs.
- Session mode aggregates the current launch or manual reset, starts fresh on character switch, and can optionally reset after a configured idle period. Records deliberately preserve only durable character records instead of pretending volatile damage, coin, or XP totals are meaningful lifetime statistics.

### Lore Lens: item intelligence on demand

Hover an item and press **Ctrl+Shift+E** by default. Lore Lens freezes one bounded region around the cursor, runs Windows OCR after the keypress, ranks up to four likely titles, and validates them against exact [EQL Wiki](https://eqlwiki.com/) pages.

- No continuous capture, no injection, and no game-memory reading.
- Conservative `m` ↔ `rn` ambiguity repair can resolve compact-font mistakes such as `Camal` → `Carnal`, but only when the repaired title reaches an exact item page.
- Typed names, copied EQ item links, bracketed item names, and EQL Wiki URLs remain available as explicit fallbacks.
- Results can include item profile data, drops, vendors, quests, crafted status, and recipes. Empty sections remain honest instead of being guessed.
- Pages are cached for seven days by default. The UI distinguishes **LIVE**, **CACHED**, **STALE CACHE**, offline, and no-exact-match states.
- Ordinary clipboard text is never transmitted automatically; it only prefills the search field for confirmation.

### Plane of Sky: loot becomes a plan

Turn on **Plane of Sky Journey Intelligence** in Settings to connect ordinary loot lines to their quest rewards automatically. Loremaster's **SPOILS** and **JOURNEY** ledgers then identify each matching turn-in, its usable class, quest NPC, and farm source without OCR. The target planner searches rewards, turn-ins, NPCs, and sources, shows owned versus missing pieces, and keeps one chosen reward visible in Journey.

For existing bags, visit a banker, open Dragon's Hoard, run `/outputfile inventory`, then use **Settings → Import inventory.txt**. Parsing is entirely local and Loremaster persists only the matching Sky turn-in names—not the character or server fields. Wind runes held in the currency tab currently require manual confirmation because the game does not place them in the inventory export. The bundled quest snapshot is attributed to the [EQ Legends Tools Plane of Sky tracker](https://eqlegendstools.com/plane-of-sky-quests/) and requires no runtime network access.

**MARK EQ MAP** writes one clearly named `Loremaster_Target_…` point to `maps/airplane_3.txt`, preserving unrelated user labels and replacing only an earlier Loremaster target. The marker uses the installed Plane of Sky map's island centers; sources without a single known island remain text guidance instead of receiving a guessed coordinate.

## What makes SpinUI different

| Principle | In practice |
|---|---|
| **One visual language per skin** | Vellum & Ember carries leather, brass, parchment, and spirit blue everywhere; Midnight Frost carries translucent navy, ice, mint, violet, and frost across the same complete window set. |
| **Native at the core** | SpinUI is SIDL XML, TGA textures, and INI layout data. It does not replace the client or trade away native behavior for a screenshot. |
| **A cockpit, not a collage** | Combat information shares one lower eye-line while the middle of the screen remains available for positioning and awareness. |
| **Resolution-aware** | Native controls stay crisp because each profile is recomputed instead of globally scaled. |
| **Adventure-aware** | Loremaster treats combat, progression, loot, travel, and item research as one journey rather than isolated utilities. |
| **Built to be changed** | Textures, layouts, inventory, pet controls, and previews come from rerunnable generators with audits and release gates. |

### Vellum & Ember

| Role | Palette | Applied to |
|---|---|---|
| Oiled leather | `#0C0906 → #2E2215` | window backgrounds, slots, and control layers |
| Brass frame | `#685030` / `#A68252` | outlines, button frames, and content wells |
| Ember seam | `#F2762C` | titlebars, interaction heat, and alert energy |
| Aged brass gold | `#D0A254` / `#F8D68C` | committed states, XP, records, and heraldic identity |
| Spirit blue | `#7EAAF4` | AA, casting, selection, and arcane accents |
| Parchment | `#F1E7D4` / `#AC9A7E` | primary and secondary text |

Every shared chrome texture is generated from the same palette. Dedicated core-window XML then adds readable gauges, effect rows, stat groupings, and control geometry without turning the rest of the interface into a different product.

### Midnight Frost Glass

| Role | Palette | Applied to |
|---|---|---|
| Midnight glass | `#03080E → #123042` with controlled alpha | primary windows, inset panels, chat, and content wells |
| Ice edge | `#69E1F2` / `#CFF7FF` | one-pixel frames, title seams, focus, and precision highlights |
| Toxic mint | `#55F2BE` / `#A4FFE0` | hover, committed interaction, active tabs, and positive state cues |
| Arcane violet | `#AB80FF` | selection, AA, magical focus, and the Glass Codex binding |
| Frost | `#E8F8FC` / `#8BB4BE` | primary and secondary typography |
| Semantic resources | native HP, mana, endurance, XP, and AA colors | gameplay meaning remains immediately recognizable |

The Glass builder starts from the complete Reloaded payload, regenerates the shared atlases, retargets only presentation controls, and then audits the parsed XML trees for structural parity. This makes the variant maintainable rather than a second hand-edited fork.

## Layout profiles

SpinUI ships seven explicit screen profiles:

`1920×1080` · `2048×1080` · `2560×1080` · `2560×1440` · `3440×1440` · `3840×1600` · `3840×2160`

Each resolution includes three play styles:

| Preset | Emphasis | 3440×1440 chat row |
|---|---|---|
| **Combat Focus** *(default preset)* | Larger combat lane | Main 700px · Social 700px · Combat 1060px |
| **Social Focus** | Raid, guild, and group conversation | Main 620px · Social 1120px · Combat 720px |
| **Hybrid** | Balanced chat with a compact combat ticker | Main 820px · Social 1000px · Combat 640px with reduced height |

That produces **21 generated combinations**. Validation keeps managed windows on-screen and checks default-visible HUDs for overlap, with explicit checks for optional pet, map, and bag states. On narrow 1080-tall profiles, the full inventory intentionally overlays some HUD space rather than shrinking into unreadability.

Choose the exact profile for your display when possible, or the nearest validated profile with the same aspect ratio. Keeping your current character layout and installing only the skin remains the safest default.

> **Raid chat:** the Raid Say filter index is not stable enough to rewrite blindly. In game, right-click the Social title area → **Filters** → **Raid Say** → **Social** (and repeat for Raid Chat if listed). EverQuest saves the choice.

## 4K readability with SpinFOURKAYYY

[**SpinFOURKAYYY**](https://github.com/itsspin/SPINFOURKAYYY) is the companion Windows scaling tool for players who want a larger, more readable EverQuest Legends interface on 4K and high-resolution displays. Native mode preserves the original frame at **100%**; enlarged modes cover every **1% step from 101% to 200%**, with roughly **110% to 125%** offering a quality-first starting range for many high-resolution and ultrawide setups.

- **Your layout remains yours.** Before an enlarged session, SpinFOURKAYYY safely fits each character's own `UI_<character>_<server>...ini` geometry to the selected source resolution. It works with default, custom, and SpinUI layouts without hardcoded character names, then captures in-game layout changes and restores native geometry when EverQuest exits.
- **One readable fullscreen presentation.** The normal Legends launcher still handles patching and sign-in. The tool launches the client at a verified windowed source size and uses its bundled Magpie engine for borderless fullscreen scaling; the recommended Readable UI path avoids unnecessary stacked sharpening and anti-aliasing passes.
- **Loremaster stays visible.** **Keep companion overlays visible** is enabled by default. It recognizes small genuine always-on-top surfaces, including Loremaster, and keeps them above the scaled game while leaving their text rendered at native Windows resolution.
- **Non-intrusive and recoverable.** It does not inject into EverQuest, alter network traffic, replace game assets, install a display driver, or resize an already-running client. Verified profiles and recovery state live under `%LOCALAPPDATA%\SpinFOURKAYYY`.

Download SpinFOURKAYYY separately from its [latest GitHub release](https://github.com/itsspin/SPINFOURKAYYY/releases/latest), extract the complete ZIP into a normal user-writable folder, choose the percentage **before** launching, click **Start EverQuest for me**, and keep the scaler open until the game exits. EverQuest, SpinFOURKAYYY, and companion overlays should run at the same Windows privilege level.

## Sharper world textures with SpinTexture

[**SpinTexture**](https://itsspin.github.io/spintexture/) is a separate companion project for improving EverQuest Legends textures. SpinUI modernizes the native interface while SpinTexture enhances the world around it, so players can use either project independently or combine them for a more complete visual upgrade.

Visit the [SpinTexture project site](https://itsspin.github.io/spintexture/) for previews, downloads, and installation guidance.

## A compact tour of the rest

| Feature | Distinguishing behavior |
|---|---|
| **Glass map** | A top-right translucent navigation surface with readable coordinates, inactive fade, and clearance from effects and the combat cluster. |
| **Three-window chat** | Main, Social, and Combat are real windows—not tabs—with preset-specific proportions and predictable routing. |
| **Pet command center** | A compact `356×210` four-column command panel retaining all 14 native commands plus the same 28 effect positions across two rows. |
| **Effect rails** | Buff, song, and pet durations render directly on icon art in shadowed ember gold; sparse client-assigned slots remain sparse instead of being falsified. |
| **Bags and bank** | Eight inventory bags park in one lower-right row; sixteen bank bags tile in an `8×2` grid beside the bank. |
| **Progression** | The player plate and inventory show distinct XP and AA gauges; the AA window carries the same segmented AA treatment, with correctly scaled fills throughout. |

<details>
<summary><strong>Why effect timers stay on the icons</strong></summary>

EverQuest draws a countdown and a beneficial/detrimental plate on the same buff button, and one width controls both. Making the button wide enough for a separate duration column also stretches the colored plate into an opaque slab. SpinUI keeps the cell icon-sized and makes the number readable with ember gold and a shadow. The generators and audits share the same cell constants so the released geometry cannot drift silently.

</details>

## Quick start

> [!IMPORTANT]
> Fully close EverQuest before copying or changing character UI INI files. The client rewrites them on logout.

### Release package

1. Download and extract **`SpinUI-Manual.zip`** from the [latest release](https://github.com/itsspin/spinips/releases/latest).
2. Fully close EverQuest, then copy `spinui_reloaded`, `spinui_glass`, or both included folders to `<EverQuest>\uifiles\`.
3. Keep your existing character UI INI for a skin-only update. If you want a complete layout, select the matching resolution and Combat Focus, Social Focus, or Hybrid profile and back up the existing character UI file before replacing it.
4. Run the included **`Loremaster.exe`**, select **`/loadskin spinui_glass 1`** or **`/loadskin spinui_reloaded 1`**, and type **`/log on`** once in game.

Releases intentionally ship the manual package and standalone Loremaster only; the Windows installer is not built or published as a release option.

Packaged releases require no Python installation. Running Loremaster from source requires Python 3.10+; the application otherwise uses the standard library, with Lore Lens calling Windows-provided OCR integration.

### Detailed installation steps

<details>
<summary><strong>Show the manual installation guide</strong></summary>

Download **`SpinUI-Manual.zip`** from the same release. It contains both UI skins, Loremaster, layouts, and a standalone [manual guide](installer/INSTALL-MANUAL.md).

1. If the skin folder you are updating already exists, rename or move it out of the way; do not merge a new release into a retired file tree.
2. Copy `spinui_glass`, `spinui_reloaded`, or both into `<EverQuest>\uifiles\` so each installed folder contains its own `EQUI.xml`.
3. Optional full layout: choose `layouts/profiles/<resolution>/<combat-focus|social-focus|hybrid>/UI_Spin_qeynos_LO1.ini`.
4. With EverQuest fully closed, make a byte-for-byte backup of the character UI file you intend to replace.
5. A manual profile replaces that entire character UI INI, including its window and chat preferences. Apply one only after making the backup in the previous step.
6. Name the preset `UI_<ExactCharacterName>_<server>_<layout-suffix>.ini`, preserving the character's existing `LO1`, `LO2`, `LO3`, or other suffix. Example: `UI_Spin_qeynos_LO1.ini`.
7. Copy that optional character UI file beside `eqgame.exe`. Do **not** replace the separate `<Character>_<server>_<layout-suffix>.ini` file or `eqclient.ini`.
8. Launch EverQuest and use `/loadskin spinui_glass 1` for Midnight Frost or `/loadskin spinui_reloaded 1` for Vellum & Ember. Run the packaged `Loremaster.exe` if desired and type `/log on` in game.

**Rollback:** restore your character UI backup and select `/loadskin default_modern 1`.

</details>

### Windows security note

SpinUI's unsigned `Loremaster.exe` may trigger SmartScreen or heuristic antivirus warnings. Release executables are built publicly by GitHub Actions from this repository. Verify downloads against the release's **`SHA256SUMS.txt`** with `Get-FileHash -Algorithm SHA256 <file>`.

## Trust by design

- **The UI is normal EQ skin content:** SIDL XML, TGA textures, and layout INIs.
- **Loremaster is non-injecting:** combat and journey tracking come from text logs; it never reads EverQuest process memory.
- **Hover Scan is explicit:** one bounded cursor region is captured only when the Lore Lens shortcut is pressed. OCR and wiki work then run outside the game's process.
- **Network behavior is visible:** EQL Wiki lookup can be disabled; cached results still work, and LIVE/CACHED/STALE states remain labeled.
- **Local state stays local:** packaged Loremaster settings, cache, and selected records live under `%LOCALAPPDATA%\SpinsLoremaster`; source runs keep them beside `loremaster.py`.
- **Manual layout changes are recoverable:** the release guide makes skin-only installation the default and requires a backup before an optional character-layout INI is replaced.
- **Alerts are opt-in:** the banner master switch ships off.

This architecture supports a transparent non-injecting workflow. As with any community tool, review the current game policies before use.

## Loremaster reference

### Running and controlling the overlay

1. Take `Loremaster.exe` from `SpinUI-Manual.zip`, download the standalone release executable, or run it from source.
2. Type `/log on` in game. Loremaster follows the newest standard EQ log it can find; **Settings → Change EverQuest Folder** or **CHANGE / LOCATE LOG** can point it to an EverQuest root or `Logs` directory.
3. Click the **Rune Seed** to unfold the full ledger; use **SEED** in the masthead to collapse it again. The transition fades the current surface, performs one atomic geometry/layout swap, then reveals the destination—avoiding a frame-by-frame child-widget reflow. Reduced motion switches instantly. Full and compact positions are remembered separately.
4. DPS is the only default Rune Seed metric. Pin additional ledger sections with ✦ to build an optional four-item carousel, then use the mouse wheel over the seed to rotate it.
5. Confirmed mez and lull timers appear beside either HUD state. Settings control each family's visibility, optional one-shot sound, and independent 3–30 second warning threshold; uncertain results are visibly labeled instead of timed.
6. Use **TOP / SHOW TOP** in Details to reclaim vertical space without changing the saved window size.
7. **LOCK** freezes movement. Detailed mode's **CLICK-THRU** enables only when Loremaster owns the `Ctrl+Alt+L` recovery shortcut; click-through always starts off after relaunch.
8. The notification-area icon can restore, hide, or exit Loremaster. Hiding keeps lightweight log tracking and the Lore Lens shortcut active.

### What the ledger tracks

| Section | At a glance | Expanded detail |
|---|---|---|
| **COMBAT** | live/session DPS | observed actors, abilities, targets, optional Self/Charm/Summoned attribution, crits, accuracy, incoming damage, healing, overheal, and timeline |
| **SLAYING** | personal and observed group kills | per-creature breakdown |
| **SPOILS** | item count | loot names, quantities, and optional Plane of Sky quest/reward matches |
| **COIN** | coin total | denomination breakdown and plat/hour |
| **PROGRESSION** | XP, levels, AA | XP/hour, estimated time to level, songs, and skills |
| **MOTES** | compact grade sequence | all ten grades, counts, and earned potential |
| **STANDING** | faction count | per-faction positive and negative movement |
| **JOURNEY** | deaths | zone chain, final-20-second death recap, recent Sky discoveries, and the selected Sky farm target |

Motes are session acquisitions, not a bag scan. Loremaster recognizes all ten potential grades and the supported corpse, stack, receive, gain, acquire, and found line formats.

### Alerts and custom rules

Built-in triggers include tells, summons, death, proven charm break, configurable big hits, name calls, and fight completion. The expanded HUD keeps the master switch plus **CHARM**, **TELL**, and **BIG HIT** toggles in a persistent alert rail; its gear opens settings for sound, every trigger, duration, threshold, an edge-safe **AUTO / RIGHT / LEFT / ABOVE / BELOW** Rune Seed placement selector, and a test alert that previews the unsaved choice.

Advanced users can add regular-expression rules to `%LOCALAPPDATA%\SpinsLoremaster\loremaster_config.json` in packaged builds, or `loremaster/loremaster_config.json` when running from source:

```json
"custom_alerts": [
  {"pattern": "begins to cast a spell", "text": "MOB CASTING", "severity": "warn"},
  {"pattern": "Rampage", "text": "RAMPAGE", "severity": "danger"}
]
```

Invalid patterns are reported once when the config loads and are ignored safely per log line.

### Accessibility

Settings include a high-contrast palette, text scaling from **85–140%**, and reduced motion. Reduced motion makes the Rune Seed transition instant and removes seed, timer, and alert animation; high-contrast and text-scale changes marked in Settings take effect on the next launch.

### Run Loremaster from source

```bat
:: Python 3.10+ with tkinter
cd loremaster
Loremaster.bat
python loremaster.py --demo
python loremaster.py --selftest
python loremaster.py --wait-for-eq
```

### Loremaster desktop

The release desktop lives in [`loremaster-desktop`](loremaster-desktop/README.md). It is a live Electron + React + TypeScript application backed by the proven parser through supervised, local-only [protocol-v1 JSONL snapshots](docs/LOREMASTER_ENGINE_PROTOCOL.md). It follows real logs, exposes self/charmed-pet damage, renders mez and lull evidence in both the Rune Seed and expanded HUD, surfaces proven charm breaks, and tracks the six classic non-Sky raid targets across independent D0–D4 weekly lockouts.

Its first **BiS Gear Path** milestone imports the version-1 JSON produced by [EQ Legends Tools Character Sheet](https://eqlegendstools.com/char-sheet/) and the local TXT produced by `/outputfile inventory`. Loremaster highlights goal pieces found in equipment, bags, or bank locations and groups missing goals into a compact, most-goals-first zone route. Imported character and inventory files remain local. Current item/source metadata is fetched only when the user imports or refreshes a build, then cached locally for offline use. Gear data and the character-sheet workflow are credited to [EQ Legends Tools](https://eqlegendstools.com/), created by **FlammHammer**.

```powershell
cd loremaster-desktop
pnpm install --frozen-lockfile
pnpm test:fixtures
pnpm test:updates
pnpm test:skin-updates
pnpm build
```

Every UI release builds and publishes the portable Electron `Loremaster.exe` with its hidden parser engine. No installer or parallel legacy executable is produced. See the [live milestone and validation gates](docs/LOREMASTER_MILESTONE_2.md).

Loremaster's **Settings → SpinUI Update Center** can check the official release,
download and verify the portable app, then replace and relaunch it with automatic
rollback if the new renderer or parser does not become healthy. Reloaded and
Glass are verified from the same release and updated as exact, isolated skin
trees only after EverQuest closes. Updates preserve Loremaster preferences and
history, EQ logs, character UI INIs, and every unrelated skin.

## Customizing and developing

<details>
<summary><strong>Build and regeneration commands</strong></summary>

```bash
pip install pillow
python3 tools/generate_spinui_textures.py
python3 tools/generate_spinui_layout.py
python3 tools/restyle_combat.py
python3 tools/restyle_pet.py
python3 tools/restyle_inventory.py
python3 tools/restyle_persona.py
python3 tools/build_spinui_glass.py
python3 tools/render_preview.py
python3 tools/render_glass_preview.py
python3 tools/render_glass_equipment_preview.py
python3 tools/render_glass_spellbook_preview.py
python3 tools/audit_spinui.py
python3 tools/audit_spinui_glass.py
python3 tools/release_quality_gate.py
```

- Recolor the interface in the palette block of `generate_spinui_textures.py` and the matching Loremaster/preview palette.
- Move windows through `PLACEMENTS` in `generate_spinui_layout.py`; generation converts coordinates and re-validates the managed layout.
- Add a chat preset or resolution through `CHAT_PRESETS` or `RESOLUTION_PROFILES`.
- Retune effect geometry through the shared `EFFECT_*` / pet buff-cell constants used by both generators and audits.
- Retune Midnight Frost in `spinui_glass_theme.py`, then rebuild the entire deterministic variant with `build_spinui_glass.py`.
- Generators start from pristine stock sources. Marker-guarded restyle migrations are safe no-ops after they have already applied.

</details>

## Troubleshooting

### Windows and installation

| Symptom | Fix |
|---|---|
| SmartScreen or antivirus warns about an executable | Verify it against release `SHA256SUMS.txt` and the public Actions build. Use the Manual package if preferred. |
| Skin does not load | Confirm `uifiles\spinui_glass\EQUI.xml` or `uifiles\spinui_reloaded\EQUI.xml`, then use the matching `/loadskin <folder> 1` command. |
| Layout did not apply | Close EverQuest completely, restore/reapply the intended character UI file, and relaunch. |
| Layout does not fit | Restore your character UI backup, then select the exact or nearest validated screen profile from the manual package. |
| Raid chat is in Main | Route Raid Say/Raid Chat to Social through the chat window's Filters menu. |
| Chat font is too large or small | Right-click the chat window → **Font**. |

### Loremaster and Lore Lens

| Symptom | Fix |
|---|---|
| Loremaster awaits a log | Type `/log on`, then use **CHANGE / LOCATE LOG** and select the EverQuest root or `Logs` folder. |
| Time-to-level is blank | It needs percentage-bearing XP lines and enough play time to establish a rate. |
| Loremaster will not move | Click **MOVE** to unlock it. The control returns to **LOCK** once movement is available. |
| Loremaster is click-through | Press **Ctrl+Alt+L** to restore interaction. Click-through is never persisted across launches. |
| Loremaster vanished | Left-click its gold-and-cyan notification-area icon, including inside the `^` overflow drawer. |
| Ctrl+Shift+E does not open Lore Lens | Check the displayed state: READY requires EQ/Loremaster foreground; CONFLICT needs a new binding in Settings; DISABLED needs Lore Lens enabled. |
| Hover Scan misses an item | Keep the complete tooltip visible through the keypress. Try windowed/borderless mode; typed names and copied item links remain available. OCR is intentionally conservative rather than guaranteed. |
| Lore Lens reports offline | Enable network lookups if desired; cached and stale-cache pages remain available. |

## Repository map

```text
spinips/
├── spinui_reloaded/          themed SIDL XML, textures, and skin defaults
├── spinui_glass/             generated Midnight Frost alternate skin
├── layouts/profiles/         seven resolutions × three play styles
├── loremaster/               encounter tracker, Lore Lens, alerts, and tests
├── installer/                legacy installer source and manual-install guide
├── tools/                    generators, restylers, audits, and release gates
├── docs/                     live screenshots and deterministic previews
└── .github/workflows/        public Windows build and packaging automation
```

---

## Credits

* **@Natural** (Discord) - restored the Options window controls that a Legends
  update added, which SpinUI's copy of that window predated: **Use System
  Cursor**, **Use Actions - Spells instead of Spellbook**, and the **Cursor
  Scaling** selector. Their build is what `EQUI_OptionsWindow.xml` carries.
* Gear data and the character-sheet workflow behind Loremaster's BiS Gear Path
  come from [EQ Legends Tools](https://eqlegendstools.com/), created by
  **FlammHammer**.

---

<p align="center"><em>Bound in leather or cut from midnight glass. See you in Norrath.</em></p>
