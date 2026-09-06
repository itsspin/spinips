const assert = require("node:assert/strict");
const { mkdtemp, writeFile, rm } = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { parseSpellCatalog, SpellCatalogService } = require("../dist-electron/spell-catalog.js");
function row(id, name, classes) {
  const fields = Array(173).fill("0");
  fields[0] = String(id); fields[1] = name; fields[8] = "1500"; fields[14] = "30"; fields[28] = "1";
  for (let n=36; n<52; n++) fields[n] = "255";
  for (const [index, level] of Object.entries(classes)) fields[36 + Number(index)] = String(level);
  return fields.join("^");
}
async function main() {
  const text = [row(202, "Courage", {1: 1, 2: 8}), row(190, "Dazzle", {13: 47}),
    row(292, "Mesmerize", {13: 2}), row(5, "NPC only", {}), row(6, "Bad level", {0: "bad"})].join("\r\n");
  const spells = parseSpellCatalog(text + "\n" + row(202, "duplicate", {1: 2}));
  assert.equal(spells.length, 3);
  assert.deepEqual(spells[0].levels, {CLR: 1, PAL: 8});
  assert.equal(spells[1].levels.ENC, 47);
  assert.equal(spells[0].castSeconds, 1.5);
  assert.deepEqual(parseSpellCatalog("not a supported format"), []);
  const root = await mkdtemp(path.join(os.tmpdir(), "spin-spells-"));
  try {
    const service = new SpellCatalogService();
    assert.equal((await service.load("")).status, "missing");
    assert.equal((await service.load(root)).status, "error");
    await writeFile(path.join(root, "spells_us.txt"), text);
    const [first, second] = await Promise.all([service.load(root), service.load(root)]);
    assert.equal(first.status, "ready");
    assert.equal(first, second, "concurrent requests should share the parse");
    assert.equal(first, await service.load(root), "unchanged catalogs should be cached");
    await writeFile(path.join(root, "spells_us.txt"), text + "\n" + row(99, "New spell", {13: 50}));
    assert.equal((await service.load(root)).spells.length, 4);
    console.log("Spell catalog: PASS | class levels, format checks, caching and refresh");
  } finally { await rm(root, { recursive: true, force: true }); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
