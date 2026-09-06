const assert = require("node:assert/strict");
const {
  availableCompanionHeight,
  boundedCompanionLayout,
  companionChromeHeight,
  companionSurfaceSize,
  decoratedCompanionSize,
  placeCompanionSurface,
} = require("../dist-electron/companion-layout.js");

assert.deepEqual(companionSurfaceSize(0, 1, 1, false), { width: 340, height: 78 });
assert.deepEqual(companionSurfaceSize(1, 0, 1, false), { width: 340, height: 81 });
assert.deepEqual(companionSurfaceSize(1, 1, 1, false), { width: 340, height: 158 });
assert.equal(companionChromeHeight(1, true, false), 70);
assert.equal(companionChromeHeight(1.35, true, true), 138);
assert.deepEqual(
  decoratedCompanionSize({ width: 340, height: 78 }, 1, true, false, false),
  { width: 340, height: 148 },
);
assert.deepEqual(
  decoratedCompanionSize({ width: 340, height: 78 }, 1, false, true, true),
  { width: 340, height: 134 },
);

for (const scenario of [
  { scale: 1.35, workAreaHeight: 768, meterTotal: 12, controlTotal: 6 },
  { scale: 1.6, workAreaHeight: 768, meterTotal: 12, controlTotal: 6 },
  { scale: 1.6, workAreaHeight: 640, meterTotal: 6, controlTotal: 6 },
]) {
  const gap = Math.max(5, Math.round(6 * scenario.scale));
  const maxPanelHeight = scenario.workAreaHeight - gap * 2;
  const layout = boundedCompanionLayout(
    scenario.meterTotal,
    scenario.controlTotal,
    scenario.scale,
    maxPanelHeight,
  );
  assert.ok(layout.panelSize.height <= maxPanelHeight, JSON.stringify({ scenario, layout }));
  assert.ok(layout.meterRows > 0, "meter must retain at least one visible row");
  assert.ok(layout.controlRows > 0, "crowd control must retain at least one visible row");
  assert.equal(layout.meterRows + layout.meterHiddenRows, scenario.meterTotal);
  assert.equal(layout.controlRows + layout.controlHiddenRows, scenario.controlTotal);
  assert.deepEqual(
    layout.panelSize,
    companionSurfaceSize(layout.controlRows, layout.meterRows, scenario.scale, layout.meterHiddenRows > 0),
  );
}

const roomy = boundedCompanionLayout(6, 6, 1, 740);
assert.equal(roomy.meterRows, 6);
assert.equal(roomy.controlRows, 6);
assert.equal(roomy.meterHiddenRows, 0);
assert.equal(roomy.controlHiddenRows, 0);

const workArea = { x: 0, y: 0, width: 1280, height: 720 };
const panel = { width: 340, height: 240 };
assert.deepEqual(
  placeCompanionSurface({ x: 440, y: 400, width: 360, height: 90 }, panel, workArea, 8, "above"),
  { x: 450, y: 152, side: "above" },
);
assert.deepEqual(
  placeCompanionSurface({ x: 440, y: 10, width: 360, height: 90 }, panel, workArea, 8, "above"),
  { x: 450, y: 108, side: "below" },
);
assert.deepEqual(
  placeCompanionSurface({ x: 440, y: 300, width: 360, height: 90 }, panel, workArea, 8, "right"),
  { x: 808, y: 225, side: "right" },
);
assert.deepEqual(
  placeCompanionSurface({ x: 910, y: 300, width: 360, height: 90 }, panel, workArea, 8, "right"),
  { x: 562, y: 225, side: "left" },
);
const offsetPlacement = placeCompanionSurface(
  { x: -1750, y: 40, width: 360, height: 90 },
  panel,
  { x: -1920, y: 0, width: 1920, height: 1080 },
  8,
  "right",
);
assert.deepEqual(offsetPlacement, { x: -1382, y: 0, side: "right" });
assert.ok(offsetPlacement.x >= -1920 && offsetPlacement.x + panel.width <= 0);
assert.ok(offsetPlacement.y >= 0 && offsetPlacement.y + panel.height <= 1080);

const narrowWorkArea = { x: 0, y: 0, width: 1024, height: 768 };
const narrowSeed = { x: 410, y: 325, width: 205, height: 118 };
const narrowAvailable = availableCompanionHeight(narrowSeed, 544, narrowWorkArea, 10);
assert.equal(narrowAvailable, 315);
const narrowLayout = boundedCompanionLayout(6, 0, 1.6, narrowAvailable);
assert.ok(narrowLayout.panelSize.height <= narrowAvailable);
const narrowPosition = placeCompanionSurface(narrowSeed, narrowLayout.panelSize, narrowWorkArea, 10, "right");
assert.ok(
  narrowPosition.y + narrowLayout.panelSize.height <= narrowSeed.y ||
    narrowPosition.y >= narrowSeed.y + narrowSeed.height,
  "no-fit sidecars must reduce rows and use vertical space without covering the Seed",
);
const detailChrome = companionChromeHeight(1.6, true, false);
const narrowDetail = boundedCompanionLayout(24, 6, 1.6, narrowAvailable, detailChrome);
assert.equal(narrowDetail.meterRows, 0);
assert.ok(narrowDetail.controlRows > 0, "detail chrome must never evict every active timer");
assert.equal(narrowDetail.meterHiddenRows, 24);
assert.ok(narrowDetail.panelSize.height <= narrowAvailable);

// Combat can introduce/grow DPS rows while unrelated mobs remain controlled.
// The same timers stay allocated, including the tight single-section fallback.
for (const scale of [0.9, 1, 1.25, 1.35, 1.6]) {
  for (const controlTotal of [1, 2, 6, 9]) {
    for (const height of [81, 130, 160, 220, 315, 500, 740]) {
      for (const meterTotal of [0, 1, 6, 24]) {
        for (const chrome of [0, companionChromeHeight(scale, true, false)]) {
          const layout = boundedCompanionLayout(meterTotal, controlTotal, scale, height, chrome);
          assert.ok(layout.controlRows > 0, `active timers lost: ${JSON.stringify({scale, controlTotal, height, meterTotal, chrome, layout})}`);
          assert.equal(layout.controlRows + layout.controlHiddenRows, controlTotal);
          assert.equal(layout.meterRows + layout.meterHiddenRows, meterTotal);
          if (companionSurfaceSize(1, 0, scale).height <= height) {
            assert.ok(layout.panelSize.height + (layout.meterRows > 0 ? chrome : 0) <= height);
          }
        }
      }
    }
  }
}
const priority = boundedCompanionLayout(6, 2, 1, 280);
assert.equal(priority.controlRows, 2, "extra DPS rows must yield to active timers");
assert.ok(priority.meterRows > 0);
const controlOnly = boundedCompanionLayout(6, 1, 1, 100);
assert.equal(controlOnly.controlRows, 1);
assert.equal(controlOnly.meterRows, 0);

console.log("Companion layout tests: PASS | bounded rows, safe placement, exact borders, active control priority");
