const assert = require("node:assert/strict");
const { createHash } = require("node:crypto");
const { existsSync, readFileSync } = require("node:fs");
const { mkdir, mkdtemp, readFile, realpath, rm, writeFile } = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");

const {
  EverQuestRunningError,
  SpinUISkinUpdateService,
  computeSpinUITreeSha256,
  deriveEverQuestRoot,
  parseSpinUIManifest,
  spinUISkinUpdaterConstants,
} = require("../dist-electron/spinui-updater.js");

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function themeManifest(files) {
  const rows = Object.entries(files)
    .map(([filePath, content]) => ({
      path: filePath,
      size: content.length,
      sha256: sha256(content),
    }))
    .sort((left, right) => Buffer.compare(Buffer.from(left.path), Buffer.from(right.path)));
  return {
    fileCount: rows.length,
    totalBytes: rows.reduce((total, row) => total + row.size, 0),
    treeSha256: computeSpinUITreeSha256(rows),
    files: rows,
  };
}

function makeFixtureRelease(overrides = {}) {
  const archive = Buffer.from("PK\x03\x04authenticated-spinui-fixture");
  const themeFiles = {
    spinui_pearlescent: {
      "EQUI.xml": Buffer.from("<XML>pearl-2.0.0</XML>\n"),
      "art/frame.tga": Buffer.from("TGA-pearl-2.0.0"),
    },
    spinui_reloaded: {
      "EQUI.xml": Buffer.from("<XML>reloaded-2.0.0</XML>\n"),
      "art/frame.tga": Buffer.from("TGA-reloaded-2.0.0"),
    },
    spinui_glass: {
      "EQUI.xml": Buffer.from("<XML>glass-2.0.0</XML>\n"),
      "art/frame.tga": Buffer.from("TGA-glass-2.0.0"),
    },
  };
  const manifest = {
    schemaVersion: 1,
    releaseVersion: "2.0.0",
    treeHashAlgorithm: spinUISkinUpdaterConstants.treeHashAlgorithm,
    archive: {
      name: spinUISkinUpdaterConstants.archiveAsset,
      size: archive.length,
      sha256: sha256(archive),
    },
    themes: {
      ...(overrides.legacy ? {} : { spinui_pearlescent: themeManifest(themeFiles.spinui_pearlescent) }),
      spinui_reloaded: themeManifest(themeFiles.spinui_reloaded),
      spinui_glass: themeManifest(themeFiles.spinui_glass),
    },
  };
  const manifestBytes = Buffer.from(`${JSON.stringify(manifest, null, 2)}\n`);
  const checksums = Buffer.from([
    `${overrides.archiveChecksum ?? sha256(archive)}  ${spinUISkinUpdaterConstants.archiveAsset}`,
    `${sha256(manifestBytes)}  ${spinUISkinUpdaterConstants.manifestAsset}`,
    "",
  ].join("\n"));
  const release = {
    tag_name: "v2.0.0",
    html_url: "https://github.com/itsspin/spinips/releases/tag/v2.0.0",
    draft: false,
    prerelease: false,
    assets: [
      {
        name: spinUISkinUpdaterConstants.archiveAsset,
        browser_download_url: `https://github.com/itsspin/spinips/releases/download/v2.0.0/${spinUISkinUpdaterConstants.archiveAsset}`,
        size: archive.length,
        digest: `sha256:${sha256(archive)}`,
      },
      {
        name: spinUISkinUpdaterConstants.manifestAsset,
        browser_download_url: `https://github.com/itsspin/spinips/releases/download/v2.0.0/${spinUISkinUpdaterConstants.manifestAsset}`,
        size: manifestBytes.length,
        digest: `sha256:${sha256(manifestBytes)}`,
      },
      {
        name: spinUISkinUpdaterConstants.checksumAsset,
        browser_download_url: `https://github.com/itsspin/spinips/releases/download/v2.0.0/${spinUISkinUpdaterConstants.checksumAsset}`,
        size: checksums.length,
        digest: `sha256:${sha256(checksums)}`,
      },
    ],
  };
  return { archive, manifest, manifestBytes, checksums, release, themeFiles };
}

function fixtureFetch(fixture) {
  return async (input) => {
    const url = String(input);
    let body;
    if (url === spinUISkinUpdaterConstants.releaseApi) body = Buffer.from(JSON.stringify(fixture.release));
    else if (url.endsWith(`/${spinUISkinUpdaterConstants.archiveAsset}`)) body = fixture.archive;
    else if (url.endsWith(`/${spinUISkinUpdaterConstants.manifestAsset}`)) body = fixture.manifestBytes;
    else if (url.endsWith(`/${spinUISkinUpdaterConstants.checksumAsset}`)) body = fixture.checksums;
    else throw new Error(`Unexpected updater request: ${url}`);
    return new Response(body, { status: 200, headers: { "content-length": String(body.length) } });
  };
}

async function writeTree(root, files, extraFile = null) {
  for (const [relative, content] of Object.entries(files)) {
    const destination = path.join(root, ...relative.split("/"));
    await mkdir(path.dirname(destination), { recursive: true });
    await writeFile(destination, content);
  }
  if (extraFile) {
    const destination = path.join(root, extraFile);
    await mkdir(path.dirname(destination), { recursive: true });
    await writeFile(destination, "unexpected");
  }
}

function extractorFor(fixture, options = {}) {
  return async (archivePath, destination) => {
    assert.deepEqual(await readFile(archivePath), fixture.archive, "only the authenticated archive may be extracted");
    for (const theme of Object.keys(fixture.manifest.themes)) {
      await writeTree(
        path.join(destination, theme),
        fixture.themeFiles[theme],
        options.extraTheme === theme ? "unexpected.txt" : null,
      );
    }
    await writeFile(path.join(destination, "README.md"), "package documentation is never installed");
  };
}

async function main() {
  assert.ok(parseSpinUIManifest(makeFixtureRelease().manifest).themes.spinui_pearlescent);
  assert.equal(parseSpinUIManifest(makeFixtureRelease({legacy: true}).manifest).themes.spinui_pearlescent, undefined);
  const extractorPackage = await import("@electron-internal/extract-zip");
  assert.equal(typeof extractorPackage.default, "function", "the hardened native ZIP extractor must be loadable");

  const malicious = makeFixtureRelease().manifest;
  malicious.themes.spinui_glass.files[0].path = "../escaped.xml";
  assert.throws(() => parseSpinUIManifest(malicious), /unsafe theme path/);

  const workspace = await mkdtemp(path.join(os.tmpdir(), "spinui-updater-test-"));
  try {
    const eqRoot = path.join(workspace, "EverQuest Legends");
    const uiFiles = path.join(eqRoot, "uifiles");
    const userData = path.join(workspace, "user-data");
    const logFile = path.join(eqRoot, "Logs", "eqlog_Spin_qeynos.txt");
    await mkdir(uiFiles, { recursive: true });
    await mkdir(path.dirname(logFile), { recursive: true });
    await writeFile(path.join(eqRoot, "eqgame.exe"), "MZfixture");
    await writeFile(logFile, "log fixture");
    // Windows runners may expose os.tmpdir() through an 8.3 alias (RUNNER~1),
    // while deriveEverQuestRoot intentionally returns the canonical long path.
    const canonicalEqRoot = await realpath(eqRoot);
    assert.equal(await deriveEverQuestRoot(logFile), canonicalEqRoot);
    assert.equal(await deriveEverQuestRoot(path.join(uiFiles, "spinui_reloaded")), canonicalEqRoot);

    const fixture = makeFixtureRelease();
    const oldReloaded = path.join(uiFiles, "spinui_reloaded");
    const otherSkin = path.join(uiFiles, "some_users_custom_skin");
    const externalLayout = path.join(eqRoot, "UI_Spin_qeynos.ini");
    await mkdir(oldReloaded, { recursive: true });
    await writeFile(path.join(oldReloaded, "old-only.xml"), "preserve in rollback");
    await mkdir(otherSkin, { recursive: true });
    await writeFile(path.join(otherSkin, "keep.txt"), "do not touch");
    await writeFile(externalLayout, "[layout]\nkeep=true\n");

    let eqRunning = false;
    const progress = [];
    const service = new SpinUISkinUpdateService({
      userDataDir: userData,
      eqRoot,
      fetchImpl: fixtureFetch(fixture),
      extractImpl: extractorFor(fixture),
      eqProcessCheck: async () => eqRunning,
      archiveMaximumBytes: 8192,
    });
    service.subscribe((state) => progress.push(state));
    const before = await service.check();
    assert.equal(before.latestVersion, "2.0.0");
    assert.equal(before.themes.spinui_reloaded.phase, "modified");
    assert.equal(before.themes.spinui_glass.phase, "missing");

    const installed = await service.install("spinui_reloaded");
    assert.equal(installed.version, "2.0.0");
    assert.equal(readFileSync(path.join(oldReloaded, "EQUI.xml"), "utf8"), "<XML>reloaded-2.0.0</XML>\n");
    assert.equal(readFileSync(path.join(installed.backupPath, "old-only.xml"), "utf8"), "preserve in rollback");
    assert.equal(readFileSync(path.join(otherSkin, "keep.txt"), "utf8"), "do not touch");
    assert.equal(readFileSync(externalLayout, "utf8"), "[layout]\nkeep=true\n");
    assert.equal(existsSync(path.join(uiFiles, "README.md")), false, "package docs must never escape staging");
    assert.equal(existsSync(path.join(userData, "spinui-update-receipts.json")), true);

    const current = await service.check();
    assert.equal(current.themes.spinui_reloaded.phase, "current");
    assert.equal(current.themes.spinui_reloaded.modified, false);

    eqRunning = true;
    await assert.rejects(() => service.install("spinui_glass"), EverQuestRunningError);
    assert.equal(service.getState().themes.spinui_glass.phase, "waiting-for-eq");
    assert.equal(existsSync(path.join(uiFiles, "spinui_glass")), false);
    eqRunning = false;
    await service.install("spinui_glass");
    await service.install("spinui_pearlescent");
    assert.equal(readFileSync(path.join(uiFiles, "spinui_pearlescent", "EQUI.xml"), "utf8"), "<XML>pearl-2.0.0</XML>\n");
    assert.equal(readFileSync(path.join(uiFiles, "spinui_glass", "EQUI.xml"), "utf8"), "<XML>glass-2.0.0</XML>\n");

    await writeFile(path.join(oldReloaded, "user-modification.txt"), "modified");
    const modified = await service.check();
    assert.equal(modified.themes.spinui_reloaded.phase, "modified");
    assert.equal(modified.themes.spinui_reloaded.modified, true);

    const targetSnapshot = readFileSync(path.join(oldReloaded, "EQUI.xml"));
    const badExtractService = new SpinUISkinUpdateService({
      userDataDir: path.join(workspace, "bad-extract-data"),
      eqRoot,
      fetchImpl: fixtureFetch(fixture),
      extractImpl: extractorFor(fixture, { extraTheme: "spinui_reloaded" }),
      eqProcessCheck: async () => false,
      archiveMaximumBytes: 8192,
    });
    await badExtractService.check();
    await assert.rejects(() => badExtractService.install("spinui_reloaded"), /does not match the authenticated release manifest/);
    assert.deepEqual(readFileSync(path.join(oldReloaded, "EQUI.xml")), targetSnapshot, "verification failure must leave the installed theme intact");
    assert.equal(readFileSync(path.join(otherSkin, "keep.txt"), "utf8"), "do not touch");

    const dishonest = makeFixtureRelease({ archiveChecksum: "0".repeat(64) });
    const dishonestService = new SpinUISkinUpdateService({
      userDataDir: path.join(workspace, "dishonest-data"),
      eqRoot,
      fetchImpl: fixtureFetch(dishonest),
      extractImpl: extractorFor(dishonest),
      eqProcessCheck: async () => false,
      archiveMaximumBytes: 8192,
    });
    const rejected = await dishonestService.check();
    assert.equal(rejected.themes.spinui_reloaded.phase, "error");
    assert.match(rejected.themes.spinui_reloaded.detail, /digest and SHA256SUMS/);
    assert.equal(progress.some((state) => state.themes.spinui_reloaded.phase === "downloading"), true);
  } finally {
    await rm(workspace, { recursive: true, force: true });
  }
  process.stdout.write("SpinUI skin updater tests: PASS\n");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
