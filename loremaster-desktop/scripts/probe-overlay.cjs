// Development-only Electron integration probe. Run after pnpm build with an
// isolated LOREMASTER_DESKTOP_DATA_DIR and LOREMASTER_OVERLAY_PROBE_PATH.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const settle = (milliseconds = 180) => new Promise((resolve) => setTimeout(resolve, milliseconds));
const source = (name, category, total, hits, maximum) => ({ name, category, total, hits, maximum });
const selfSources = [
  source("Melee", "melee", 18100, 34, 690),
  source("Flame of Light", "spell", 9700, 9, 1520),
  source("Kick", "melee", 6600, 18, 410),
  source("Damage shield", "damage_shield", 1400, 20, 70),
  source("Flaming Strike", "proc", 950, 5, 190),
  source("Suffocating Sphere", "dot", 820, 4, 205),
  source("Bash", "melee", 550, 3, 220),
  source("Riposte", "melee", 280, 1, 280),
];
const rangerSources = [
  source("Double Bow Shot", "melee", 21000, 24, 1055),
  source("Melee", "melee", 13000, 35, 720),
  source("Ranged", "melee", 8000, 18, 610),
  source("Flame Lick", "dot", 2000, 8, 250),
  source("Kick", "melee", 1200, 5, 260),
  source("Non-melee: Aromek", "unknown", 1500, 3, 550),
  source("Damage shield", "damage_shield", 1100, 11, 100),
  source("Bash", "melee", 410, 2, 230),
];
const actor = (name, role, sources) => {
  const damage = sources.reduce((total, row) => total + row.total, 0);
  const hits = sources.reduce((total, row) => total + row.hits, 0);
  const maximum = Math.max(0, ...sources.map((row) => row.maximum));
  return {
    name, role, encounterDamage: damage, encounterDps: damage / 117,
    encounterHits: hits, encounterMaximum: maximum,
    sessionDamage: damage, sessionDps: damage / 117,
    sessionHits: hits, sessionMaximum: maximum, sources,
  };
};

function fixtureEvent() {
  const actors = [
    actor("Spin", "self", selfSources),
    actor("an abhorrent", "charmed", [source("Melee", "melee", 22000, 50, 650), source("Suffocating Sphere", "dot", 3500, 14, 250)]),
    actor("Aromek", "group", rangerSources),
    actor("Verdume", "group", [source("Melee", "melee", 35180, 60, 980)]),
    actor("Lilith", "group", [source("Melee", "melee", 18490, 45, 720)]),
    actor("Nearby", "observed", [source("Melee", "melee", 99999, 99, 9999)]),
  ];
  const encounter = {
    encounterId: "overlay-probe-1", name: "Lady Vox", active: true,
    startedAt: "2026-08-29T19:00:00Z", endedAt: "", seconds: 117,
    damage: 63900, dps: 63900 / 117, personalDamage: 38400,
    charmedPetDamage: 25500, summonedPetDamage: 0,
    damageTaken: 7180, healingDone: 8300, healsReceived: 7900,
    kills: 0, crits: 14, misses: 9, sources: selfSources,
    targets: [source("Lady Vox", "unknown", 63900, 158, 1520)],
    actors, healingSources: [], timeline: [], zone: "The Permafrost Caverns",
  };
  return {
    protocolVersion: 1, sequence: 99, occurredAt: "2026-08-29T19:01:57Z", eventType: "engine.snapshot",
    snapshot: {
      protocolVersion: 1, sequence: 99, observedAt: "2026-08-29T19:01:57Z",
      character: { name: "Spin", level: 50, composition: "PAL/MNK/ENC", zone: encounter.zone },
      groupMembers: ["Aromek", "Verdume", "Lilith"],
      combat: {
        active: true, autoAttack: false, encounterName: encounter.name,
        fightDps: encounter.dps, sessionDps: encounter.dps,
        personalDamage: 38400, charmedPetDamage: 25500, summonedPetDamage: 0,
        fightSeconds: 117, fightDamage: 63900, fightPersonalDamage: 38400,
        fightCharmedPetDamage: 25500, fightSummonedPetDamage: 0,
        damageTaken: 7180, healingDone: 8300, kills: 0, crits: 14, misses: 9,
      },
      breakdown: { sources: selfSources, targets: encounter.targets, actors: [] },
      encounters: [encounter], controls: [], hiddenControlRows: 0,
      controlNoticeCount: 0, controlAmbiguityCount: 0, alerts: [],
    },
  };
}

async function click(window, selector, text) {
  const clicked = await window.webContents.executeJavaScript(`(() => {
    const elements = [...document.querySelectorAll(${JSON.stringify(selector)})];
    const element = ${text === undefined ? "elements[0]" : `elements.find((item) => item.textContent.includes(${JSON.stringify(text)}))`};
    if (!element) return false;
    element.click(); return true;
  })()`);
  assert.ok(clicked, `missing interactive control: ${selector} ${text ?? ""}`);
  await settle();
}

async function geometry(window) {
  return window.webContents.executeJavaScript(`(() => {
    const surface = document.querySelector('.seed-meter-surface');
    const rect = surface?.getBoundingClientRect();
    const rows = [...document.querySelectorAll('.seed-meter-row,.seed-meter-ability')];
    return {
      viewport: { width: innerWidth, height: innerHeight },
      documentScroll: document.scrollingElement.scrollTop,
      surface: rect ? { top: rect.top, bottom: rect.bottom, width: rect.width, height: rect.height } : null,
      background: surface ? getComputedStyle(surface).backgroundImage : '',
      textOpacity: document.querySelector('.seed-meter-row strong') ? getComputedStyle(document.querySelector('.seed-meter-row strong')).opacity : '1',
      rowCount: rows.length,
      bottom: Math.max(0, ...rows.map((row) => row.getBoundingClientRect().bottom)),
      names: rows.map((row) => row.textContent),
      standalone: Boolean(document.querySelector('.seed-meter-surface.standalone')),
      idle: Boolean(document.querySelector('.seed-meter-idle')),
      detail: Boolean(document.querySelector('.seed-meter-detail')),
      emptyText: surface ? '' : document.body.innerText.slice(0, 500),
    };
  })()`);
}

async function assertFits(window, label) {
  const result = await geometry(window);
  assert.ok(result.surface, `${label}: surface missing ${JSON.stringify(result)}`);
  assert.notEqual(result.background, "none", `${label}: theme background lost its opacity binding`);
  assert.equal(result.documentScroll, 0, `${label}: document focus-scroll`);
  assert.ok(result.surface.bottom <= result.viewport.height + 2, `${label}: clipped perimeter ${JSON.stringify(result)}`);
  assert.ok(result.bottom <= result.viewport.height + 2, `${label}: clipped rows ${JSON.stringify(result)}`);
  return result;
}

function controlFixture() {
  const controls = JSON.parse(fs.readFileSync(path.join(__dirname, "../fixtures/control-replay.json"), "utf8"))
    .events[3].snapshot.controls;
  return controls.map((control, index) => ({
    ...control, target: index === 0 ? "an entranced sentinel" : "Cleric of Innoruuk",
    count: 1, safeRemainingSeconds: index === 0 ? 25 : 59,
    remainingSeconds: index === 0 ? 31 : 65, urgency: "safe", ambiguity: "",
  }));
}

async function assertControlsFit(window, expected, label) {
  assert.ok(window.isVisible(), `${label}: control window hidden`);
  const result = await window.webContents.executeJavaScript(`(() => {
    const rows = [...document.querySelectorAll('.seed-control-row')];
    const rect = document.querySelector('.seed-control-surface')?.getBoundingClientRect();
    return {
      targets: rows.map(row => row.querySelector('.seed-control-copy strong').textContent),
      timers: rows.map(row => row.querySelector('.seed-control-time strong').textContent),
      bottom: rect?.bottom, top: rect?.top, height: innerHeight,
      documentScroll: document.scrollingElement.scrollTop,
    };
  })()`);
  assert.deepEqual(result.targets, expected.map((control) => control.target), `${label}: timers lost`);
  assert.ok(result.timers.every((timer) => /\d/.test(timer)), `${label}: countdown missing`);
  assert.equal(result.documentScroll, 0);
  assert.ok(result.top >= 0 && result.bottom <= result.height + 2, `${label}: control clipped ${JSON.stringify(result)}`);
}

exports.runOverlayProbe = async function runOverlayProbe(context) {
  const { mainWindow, controlWindow, workArea, outputPath } = context;
  const report = { checks: [], screenshots: [] };
  const directory = path.dirname(outputPath);
  fs.mkdirSync(directory, { recursive: true });
  const capture = async (window, name) => {
    const destination = path.join(directory, `${name}.png`);
    fs.writeFileSync(destination, (await window.webContents.capturePage()).toPNG());
    report.screenshots.push(destination);
  };
  try {
    context.updateSettings({ uiTheme: "vellum", fontScale: 1.35, seedMeterPlacement: "above", seedMeterMode: "all", autoCheckUpdates: false });
    const seedPosition = { x: workArea.x + 140, y: workArea.y + workArea.height - 130 };
    context.saveSeedPosition(seedPosition);
    context.setWindowMode(false);
    context.publishSnapshot(fixtureEvent());
    await settle(300);
    const stacked = await assertFits(controlWindow, "stacked");
    assert.equal(stacked.rowCount, 5);
    assert.ok(!stacked.names.some((name) => name.includes("Nearby")));
    assert.ok(controlWindow.getBounds().y + controlWindow.getBounds().height <= mainWindow.getBounds().y);
    await capture(controlWindow, "overlay-stacked-vellum");
    await capture(mainWindow, "overlay-seed-controls");
    report.checks.push("stacked contributor totals and observed-actor exclusion");
    context.updateSettings({ seedMeterOpacity: 0.45 });
    await settle();
    const translucent = await assertFits(controlWindow, "opacity");
    assert.notEqual(translucent.background, stacked.background);
    assert.equal(translucent.textOpacity, "1");
    context.updateSettings({ seedMeterOpacity: 0.9 });
    await settle();
    report.checks.push("local theme opacity adjusts the material without fading text");

    context.setCompanionInspecting(true);
    await settle();
    assert.ok(context.isInspecting() && controlWindow.isFocusable());
    await click(controlWindow, ".seed-meter-row", "Aromek");
    const details = await assertFits(controlWindow, "group detail");
    assert.ok(details.detail);
    assert.equal(details.rowCount, 6);
    await capture(controlWindow, "overlay-group-abilities");
    await click(controlWindow, 'button[aria-label="Next recorded abilities"]');
    assert.equal((await assertFits(controlWindow, "detail page 2")).rowCount, 2);
    await controlWindow.webContents.executeJavaScript("window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))");
    await settle();
    assert.ok(!context.isInspecting() && !controlWindow.isFocusable());
    context.setCompanionInspecting(true);
    await settle();
    mainWindow.focus();
    await settle();
    assert.ok(!context.isInspecting() && !controlWindow.isFocusable());
    report.checks.push("click-to-inspect, all ability pages, Escape and blur relock");

    context.updateSettings({ seedMeterPlacement: "right" });
    await settle();
    assert.ok(controlWindow.getBounds().x >= mainWindow.getBounds().x + mainWindow.getBounds().width);
    report.checks.push("right sidecar placement");

    context.updateSettings({ seedMeterPlacement: "meter-only" });
    await settle();
    assert.ok((await assertFits(mainWindow, "meter only")).standalone);
    assert.ok(!controlWindow.isVisible());
    await click(mainWindow, ".seed-meter-row", "an abhorrent");
    const pet = await assertFits(mainWindow, "pet detail");
    assert.equal(pet.rowCount, 2);
    assert.ok(pet.names.some((name) => name.includes("Suffocating Sphere")));
    await click(mainWindow, ".seed-meter-breadcrumb button");
    report.checks.push("pet-only source evidence from a clicked contributor");
    const beforeMove = mainWindow.getBounds();
    const moved = {
      x: workArea.x + workArea.width - beforeMove.width - 70,
      y: workArea.y + workArea.height - beforeMove.height - 40,
    };
    mainWindow.setPosition(moved.x, moved.y);
    await settle();
    assert.deepEqual(context.getSettings().seedMeterPosition, moved);
    assert.deepEqual(context.getSettings().seedPosition, seedPosition);
    assert.equal(mainWindow.getBounds().x, moved.x);
    await capture(mainWindow, "overlay-meter-only-vellum");
    report.checks.push("meter-only replaces Seed, drag persists without snap-back");

    const largeParty = fixtureEvent();
    largeParty.snapshot.encounters[0].actors.push(
      actor("Meri", "group", [source("Melee", "melee", 6000, 12, 700)]),
      actor("Thalan", "group", [source("Melee", "melee", 5000, 10, 600)]),
    );
    context.publishSnapshot(largeParty);
    await settle();
    await click(mainWindow, 'button[aria-label="Next contributors"]');
    const lastContributor = await assertFits(mainWindow, "contributor page 2");
    assert.equal(lastContributor.rowCount, 1);
    await click(mainWindow, ".seed-meter-row", "Thalan");
    assert.equal((await assertFits(mainWindow, "paged contributor detail")).rowCount, 1);
    context.publishSnapshot(fixtureEvent());
    await settle();
    report.checks.push("overflow contributors remain pageable and clickable");

    await click(mainWindow, ".seed-meter-actions button", "HUD");
    const hudBounds = mainWindow.getBounds();
    assert.equal(hudBounds.x, Math.min(moved.x, workArea.x + workArea.width - hudBounds.width));
    await click(mainWindow, 'button[aria-label="Open settings"]');
    await mainWindow.webContents.executeJavaScript("document.querySelector('.seed-meter-setting').scrollIntoView({ block: 'center' })");
    await settle();
    assert.equal(await mainWindow.webContents.executeJavaScript("document.querySelectorAll('.seed-meter-placement-picker button').length"), 5);
    assert.equal(await mainWindow.webContents.executeJavaScript("document.scrollingElement.scrollTop"), 0);
    await capture(mainWindow, "overlay-appearance-settings");
    await click(mainWindow, 'button[aria-label="Collapse to Rune Seed"]');
    assert.equal(mainWindow.getBounds().x, moved.x);
    assert.equal(mainWindow.getBounds().y, moved.y);
    report.checks.push("HUD expands from active meter anchor and restores its position");

    context.updateSettings({ uiTheme: "glass", seedMeterMode: "self", fontScale: 1.6 });
    await settle();
    const self = await assertFits(mainWindow, "single self row at 160%");
    assert.equal(self.rowCount, 1);
    await click(mainWindow, ".seed-meter-row", "Spin");
    assert.equal((await assertFits(mainWindow, "self abilities at 160%")).rowCount, 6);
    await capture(mainWindow, "overlay-self-abilities-glass");
    report.checks.push("single-row perimeter and self-ability growth at 160% scaling");

    const empty = fixtureEvent();
    empty.snapshot.encounters = [];
    for (const key of Object.keys(empty.snapshot.combat)) {
      if (typeof empty.snapshot.combat[key] === "number") empty.snapshot.combat[key] = 0;
    }
    empty.snapshot.combat.active = false;
    context.publishSnapshot(empty);
    await settle();
    const idle = await assertFits(mainWindow, "no-data meter");
    assert.ok(idle.idle && idle.standalone && mainWindow.isVisible());
    await capture(mainWindow, "overlay-meter-idle-glass");
    report.checks.push("no-data Meter Only stays visible and recoverable");

    context.updateSettings({ fontScale: 1.35, seedMeterPlacement: "seed-only" });
    await settle();
    assert.ok(await mainWindow.webContents.executeJavaScript("Boolean(document.querySelector('.rune-seed'))"));
    assert.ok(!controlWindow.isVisible());
    report.checks.push("Seed Only recovery");

    // A fresh encounter must never reset or crowd out timers on other mobs.
    const controls = controlFixture();
    const betweenFights = structuredClone(empty);
    betweenFights.snapshot.controls = controls;
    context.updateSettings({ seedMeterPlacement: "above", seedMeterMode: "all", fontScale: 1.35 });
    context.publishSnapshot(betweenFights);
    await settle();
    await assertControlsFit(controlWindow, controls, "between fights");
    const nextFight = fixtureEvent();
    nextFight.snapshot.controls = controls;
    nextFight.snapshot.encounters[0].name = "a different guardian";
    nextFight.snapshot.encounters[0].encounterId = "overlay-next-fight";
    nextFight.snapshot.combat.encounterName = "a different guardian";
    nextFight.snapshot.combat.autoAttack = true;
    context.publishSnapshot(nextFight);
    await settle();
    await assertFits(controlWindow, "combat and control");
    await assertControlsFit(controlWindow, controls, "different mob enters combat");
    context.setCompanionInspecting(true);
    await settle();
    await click(controlWindow, ".seed-meter-row", "Spin");
    await assertControlsFit(controlWindow, controls, "ability detail and controls");
    context.setCompanionInspecting(false);
    await settle();
    for (const mode of ["self", "group", "pet", "all"]) {
      context.updateSettings({ seedMeterMode: mode });
      await settle();
      await assertControlsFit(controlWindow, controls, `${mode} DPS and unrelated controls`);
    }
    await capture(controlWindow, "overlay-combat-with-active-controls");
    report.checks.push("mez and lull survive different fights, every DPS content mode, and ability detail without clipping");

    for (const placement of ["right", "meter-only", "seed-only"]) {
      context.updateSettings({ seedMeterPlacement: placement });
      await settle();
      await assertControlsFit(controlWindow, controls, `${placement} controls`);
    }
    // Exercise the native tight-space allocation at the actual renderer boundary.
    context.updateSettings({ seedMeterPlacement: "above", fontScale: 1.6 });
    await settle();
    const { boundedCompanionLayout } = require("../dist-electron/companion-layout.js");
    const tight = boundedCompanionLayout(24, controls.length, 1.6, 315, 112);
    assert.equal(tight.meterRows, 0);
    assert.equal(tight.controlRows, 2);
    controlWindow.setSize(tight.panelSize.width, tight.panelSize.height);
    controlWindow.webContents.send("window:companion-layout", { meterRows: tight.meterRows, controlRows: tight.controlRows });
    await settle();
    await assertControlsFit(controlWindow, controls, "tight-space control priority");
    assert.equal((await geometry(controlWindow)).surface, null, "zero-row meter must not render over control timers");
    context.syncControlWindow();
    await settle();
    await assertControlsFit(controlWindow, controls, "room restored");
    report.checks.push("active controls persist in every placement and take priority when the damage panel cannot fit");
    const ascent = fixtureEvent();
    ascent.snapshot.character.level = 40;
    ascent.snapshot.character.composition = "PAL/MNK/ENC";
    ascent.snapshot.progression = {
      sessionSeconds: 5400, xpEvents: 84, measuredEvents: 84, gainedPercent: 31.5,
      percentPerHour: 21, rateSource: "log", currentPercent: 62.5, level: 40,
      secondsToLevel: 6428, levelsGained: 0, aaEarned: 3, aaPerHour: 2,
      history: Array.from({length: 24}, (_, n) => ({at: new Date(Date.now() - (24-n)*120000).toISOString(), percent: .1 + n%6*.1})),
    };
    context.updateSettings({ uiTheme: "pearlescent", fontScale: 1.15, eqRoot: "E:\\Eqlegends" });
    context.publishSnapshot(ascent);
    context.setWindowMode(true);
    await settle(500);
    await click(mainWindow, ".seed-action");
    await click(mainWindow, ".ascent-entry");
    await settle(650);
    assert.equal(await mainWindow.webContents.executeJavaScript("document.documentElement.dataset.theme"), "pearlescent");
    const spellCount = await mainWindow.webContents.executeJavaScript("document.querySelectorAll('.ascent-spell-list article').length");
    assert.ok(spellCount > 0 && spellCount <= 20, "spell path must load and remain paginated");
    const layout = await mainWindow.webContents.executeJavaScript(`({
      rootScroll: document.scrollingElement.scrollTop,
      width: document.querySelector('.loremaster-shell').clientWidth,
      scrollWidth: document.querySelector('.loremaster-shell').scrollWidth
    })`);
    assert.equal(layout.rootScroll, 0);
    assert.ok(layout.scrollWidth <= layout.width + 2, JSON.stringify(layout));
    await capture(mainWindow, "leveling-pearlescent");
    await mainWindow.webContents.executeJavaScript("document.querySelector('.ascent-spells').scrollIntoView({block:'start'})");
    await settle();
    await capture(mainWindow, "spell-path-pearlescent");
    for (const theme of ["vellum", "glass", "pearlescent"]) {
      context.updateSettings({uiTheme: theme, fontScale: 1.35});
      await settle();
      assert.equal(await mainWindow.webContents.executeJavaScript("document.scrollingElement.scrollTop"), 0);
      assert.equal(await mainWindow.webContents.executeJavaScript("document.documentElement.dataset.theme"), theme);
    }
    await click(mainWindow, ".ascent-classes button", "PAL");
    await click(mainWindow, ".ascent-classes button", "PAL");
    assert.equal(await mainWindow.webContents.executeJavaScript("document.scrollingElement.scrollTop"), 0);
    report.checks.push("Ascent: local spell catalog, bounded pages, all three themes, focus and scroll containment");
    await click(mainWindow, ".masthead-actions button", "HUD");
    await click(mainWindow, 'button[aria-label="Open settings"]');
    for (const theme of ["glass", "vellum", "pearlescent"]) {
      await click(mainWindow, ".theme-option." + theme);
      assert.equal(context.getSettings().uiTheme, theme);
      assert.equal(await mainWindow.webContents.executeJavaScript("document.documentElement.dataset.theme"), theme);
    }
    await capture(mainWindow, "settings-three-themes");
    const header = await mainWindow.webContents.executeJavaScript(`(() => {
      const title = document.querySelector('.masthead p').getBoundingClientRect();
      const actions = document.querySelector('.masthead-actions').getBoundingClientRect();
      return {titleBottom: title.bottom, actionsTop: actions.top};
    })()`);
    assert.ok(header.actionsTop >= header.titleBottom, "narrow HUD actions must not overlap the title");
    // Real mouse activation also focuses each hidden checkbox. This catches
    // the document-scroll regression that a synthetic element.click misses.
    const toggleCount = await mainWindow.webContents.executeJavaScript("document.querySelectorAll('.settings-toggle').length");
    for (let n = 0; n < toggleCount; n++) {
      await mainWindow.webContents.executeJavaScript(`document.querySelectorAll('.settings-toggle')[${n}].scrollIntoView({block:'center'})`);
      await settle(40);
      const point = await mainWindow.webContents.executeJavaScript(`(() => {
        const row = document.querySelectorAll('.settings-toggle')[${n}];
        const rect = row.getBoundingClientRect();
        return {x: Math.round(rect.right - 20), y: Math.round(rect.top + rect.height/2), checked: row.querySelector('input').checked};
      })()`);
      const zoom = mainWindow.webContents.getZoomFactor();
      const input = {x: Math.round(point.x * zoom), y: Math.round(point.y * zoom), button: "left", clickCount: 1};
      mainWindow.webContents.sendInputEvent({type: "mouseDown", ...input});
      mainWindow.webContents.sendInputEvent({type: "mouseUp", ...input});
      await settle(100);
      assert.equal(await mainWindow.webContents.executeJavaScript(`document.querySelectorAll('.settings-toggle')[${n}].querySelector('input').checked`), !point.checked);
      assert.equal(await mainWindow.webContents.executeJavaScript("document.scrollingElement.scrollTop"), 0);
    }
    report.checks.push("three theme options persist; all Settings toggles accept real mouse input without blanking");
    report.passed = true;
    fs.writeFileSync(outputPath, `${JSON.stringify(report, null, 2)}\n`);
    console.log(`Overlay integration probe: PASS | ${report.checks.length} checks`);
  } catch (error) {
    report.passed = false;
    report.error = error.stack || String(error);
    fs.writeFileSync(outputPath, `${JSON.stringify(report, null, 2)}\n`);
    throw error;
  }
};
