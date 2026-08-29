const assert = require("node:assert/strict");
const {
  boundedCompanionLayout,
  companionSurfaceSize,
} = require("../dist-electron/companion-layout.js");

assert.deepEqual(companionSurfaceSize(0, 1, 1, false), { width: 340, height: 78 });
assert.deepEqual(companionSurfaceSize(1, 0, 1, false), { width: 340, height: 81 });
assert.deepEqual(companionSurfaceSize(1, 1, 1, false), { width: 340, height: 158 });

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

console.log("Companion layout tests: PASS | bounded rows, exact borders, honest overflow");
