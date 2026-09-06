import { execFile } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { once } from "node:events";
import { createReadStream, createWriteStream, existsSync } from "node:fs";
import {
  lstat,
  mkdir,
  readFile,
  readdir,
  realpath,
  rename,
  rm,
  stat,
  unlink,
  writeFile,
} from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";
import { compareVersions, parseChecksums } from "./portable-updater.js";

const RELEASE_API = "https://api.github.com/repos/itsspin/spinips/releases/latest";
const RELEASE_PAGE = "https://github.com/itsspin/spinips/releases/latest";
const ARCHIVE_ASSET = "SpinUI-UI.zip";
const MANIFEST_ASSET = "SpinUI-Update.json";
const CHECKSUM_ASSET = "SHA256SUMS.txt";
const TREE_HASH_ALGORITHM = "sha256-path-size-content-v1";
const RECEIPT_FILENAME = "spinui-update-receipts.json";
const OWNED_UPDATE_DIRECTORY = ".loremaster-spinui-updates";
const MAX_RELEASE_BYTES = 2 * 1024 * 1024;
const MAX_MANIFEST_BYTES = 8 * 1024 * 1024;
const MAX_CHECKSUM_BYTES = 1024 * 1024;
const MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024;
const MAX_THEME_FILES = 10_000;
const MAX_THEME_BYTES = 4 * 1024 * 1024 * 1024;
const MAX_THEME_FILE_BYTES = 512 * 1024 * 1024;
const MAX_REDIRECTS = 5;
const INACTIVITY_TIMEOUT_MS = 30_000;

const OFFICIAL_DOWNLOAD_HOSTS = new Set([
  "api.github.com",
  "github.com",
  "objects.githubusercontent.com",
  "release-assets.githubusercontent.com",
]);

export const SPINUI_SKINS = ["spinui_reloaded", "spinui_glass", "spinui_pearlescent"] as const;
export type SpinUISkinName = (typeof SPINUI_SKINS)[number];

export type SpinUISkinPhase =
  | "idle"
  | "checking"
  | "missing"
  | "current"
  | "available"
  | "modified"
  | "downloading"
  | "verifying"
  | "waiting-for-eq"
  | "installing"
  | "installed"
  | "error";

export interface SpinUISkinStatus {
  name: SpinUISkinName;
  phase: SpinUISkinPhase;
  installed: boolean;
  installedVersion: string | null;
  latestVersion: string | null;
  percent: number;
  detail: string;
  modified: boolean;
}

export interface SpinUIUpdateState {
  eqRoot: string | null;
  latestVersion: string | null;
  releaseUrl: string;
  lastCheckedAt: string | null;
  busy: boolean;
  themes: Record<SpinUISkinName, SpinUISkinStatus>;
}

export interface SpinUISkinInstallResult {
  theme: SpinUISkinName;
  version: string;
  targetPath: string;
  backupPath: string | null;
}

interface ReleaseAsset {
  name: string;
  url: string;
  size: number;
  sha256: string;
}

interface SpinUIRelease {
  version: string;
  releaseUrl: string;
  archive: ReleaseAsset;
  manifestAsset: ReleaseAsset;
  checksums: ReleaseAsset;
  manifest: SpinUIManifest;
}

interface SpinUIManifestFile {
  path: string;
  size: number;
  sha256: string;
}

interface SpinUIManifestTheme {
  fileCount: number;
  totalBytes: number;
  treeSha256: string;
  files: SpinUIManifestFile[];
}

interface SpinUIManifest {
  schemaVersion: 1;
  releaseVersion: string;
  treeHashAlgorithm: typeof TREE_HASH_ALGORITHM;
  archive: {
    name: typeof ARCHIVE_ASSET;
    size: number;
    sha256: string;
  };
  themes: Partial<Record<SpinUISkinName, SpinUIManifestTheme>>;
}

interface ReceiptEntry {
  eqRoot: string;
  targetPath: string;
  theme: SpinUISkinName;
  version: string;
  treeSha256: string;
  installedAt: string;
}

interface ReceiptFile {
  schemaVersion: 1;
  installations: ReceiptEntry[];
}

type ExtractImplementation = (archivePath: string, destination: string) => Promise<void>;
type EqProcessCheck = () => Promise<boolean>;
type StateListener = (state: SpinUIUpdateState) => void;

export interface SpinUISkinUpdaterOptions {
  userDataDir: string;
  eqRoot?: string | null;
  fetchImpl?: typeof fetch;
  extractImpl?: ExtractImplementation;
  eqProcessCheck?: EqProcessCheck;
  archiveMaximumBytes?: number;
}

export class EverQuestRunningError extends Error {
  constructor() {
    super("Close EverQuest before installing SpinUI files, then try again.");
    this.name = "EverQuestRunningError";
  }
}

function status(name: SpinUISkinName, detail = "Ready to check this SpinUI theme."): SpinUISkinStatus {
  return {
    name,
    phase: "idle",
    installed: false,
    installedVersion: null,
    latestVersion: null,
    percent: 0,
    detail,
    modified: false,
  };
}

function initialState(eqRoot: string | null): SpinUIUpdateState {
  return {
    eqRoot,
    latestVersion: null,
    releaseUrl: RELEASE_PAGE,
    lastCheckedAt: null,
    busy: false,
    themes: {
      spinui_reloaded: status("spinui_reloaded"),
      spinui_glass: status("spinui_glass"),
      spinui_pearlescent: status("spinui_pearlescent"),
    },
  };
}

function cloneState(state: SpinUIUpdateState): SpinUIUpdateState {
  return {
    ...state,
    themes: {
      spinui_reloaded: { ...state.themes.spinui_reloaded },
      spinui_glass: { ...state.themes.spinui_glass },
      spinui_pearlescent: { ...state.themes.spinui_pearlescent },
    },
  };
}

function boundedPercent(value: number): number {
  return Math.max(0, Math.min(100, Math.round(value)));
}

function normalizeVersion(value: string): string {
  const normalized = value.trim().replace(/^v/i, "");
  compareVersions(normalized, normalized);
  return normalized;
}

function isSha256(value: unknown): value is string {
  return typeof value === "string" && /^[0-9a-f]{64}$/i.test(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function compareUtf8(left: string, right: string): number {
  return Buffer.compare(Buffer.from(left, "utf8"), Buffer.from(right, "utf8"));
}

function safeRelativeThemePath(value: unknown): string {
  if (typeof value !== "string" || !value || value.includes("\0") || value.includes("\\")) {
    throw new Error("The SpinUI manifest contains an unsafe theme path.");
  }
  if (value.startsWith("/") || /^[A-Za-z]:/.test(value)) {
    throw new Error("The SpinUI manifest contains an absolute theme path.");
  }
  const parts = value.split("/");
  if (parts.some((part) => {
    const stem = part.split(".", 1)[0];
    return !part
      || part === "."
      || part === ".."
      || part.endsWith(".")
      || part.endsWith(" ")
      || /[<>:"|?*\x00-\x1f]/.test(part)
      || /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])$/i.test(stem);
  })) {
    throw new Error("The SpinUI manifest contains an unsafe theme path.");
  }
  const normalized = path.posix.normalize(value);
  if (normalized !== value) throw new Error("The SpinUI manifest contains a non-canonical theme path.");
  return value;
}

export function computeSpinUITreeSha256(files: readonly SpinUIManifestFile[]): string {
  const digest = createHash("sha256");
  for (const file of files) {
    digest.update(Buffer.from(file.path, "utf8"));
    digest.update(Buffer.from([0]));
    digest.update(Buffer.from(String(file.size), "ascii"));
    digest.update(Buffer.from([0]));
    digest.update(Buffer.from(file.sha256, "hex"));
    digest.update(Buffer.from("\n", "ascii"));
  }
  return digest.digest("hex");
}

function parseTheme(value: unknown, name: SpinUISkinName): SpinUIManifestTheme {
  if (!isRecord(value) || !Array.isArray(value.files)) {
    throw new Error(`The SpinUI manifest is missing the ${name} inventory.`);
  }
  const rawFiles = value.files;
  if (!Number.isSafeInteger(value.fileCount) || (value.fileCount as number) < 1 || (value.fileCount as number) > MAX_THEME_FILES) {
    throw new Error(`The ${name} manifest file count is outside the safe limit.`);
  }
  if (!Number.isSafeInteger(value.totalBytes) || (value.totalBytes as number) < 1 || (value.totalBytes as number) > MAX_THEME_BYTES) {
    throw new Error(`The ${name} manifest size is outside the safe limit.`);
  }
  if (!isSha256(value.treeSha256)) throw new Error(`The ${name} tree checksum is invalid.`);
  if (rawFiles.length !== value.fileCount) throw new Error(`The ${name} manifest file count does not match its inventory.`);

  const seen = new Set<string>();
  const files: SpinUIManifestFile[] = rawFiles.map((entry, index) => {
    if (!isRecord(entry)) throw new Error(`The ${name} manifest contains an invalid file entry.`);
    const relative = safeRelativeThemePath(entry.path);
    const folded = relative.toLocaleLowerCase("en-US");
    if (seen.has(folded)) throw new Error(`The ${name} manifest contains a Windows-colliding path.`);
    seen.add(folded);
    if (!Number.isSafeInteger(entry.size) || (entry.size as number) < 0 || (entry.size as number) > MAX_THEME_FILE_BYTES) {
      throw new Error(`The ${name} manifest contains an unsafe file size.`);
    }
    if (!isSha256(entry.sha256)) throw new Error(`The ${name} manifest contains an invalid file checksum.`);
    if (index > 0 && compareUtf8(String(rawFiles[index - 1] && (rawFiles[index - 1] as Record<string, unknown>).path), relative) >= 0) {
      throw new Error(`The ${name} manifest inventory is not in deterministic order.`);
    }
    return { path: relative, size: entry.size as number, sha256: entry.sha256.toLowerCase() };
  });
  const totalBytes = files.reduce((total, file) => total + file.size, 0);
  if (totalBytes !== value.totalBytes) throw new Error(`The ${name} manifest byte total is inconsistent.`);
  const treeSha256 = computeSpinUITreeSha256(files);
  if (treeSha256 !== value.treeSha256.toLowerCase()) throw new Error(`The ${name} manifest tree checksum is inconsistent.`);
  return {
    fileCount: files.length,
    totalBytes,
    treeSha256,
    files,
  };
}

export function parseSpinUIManifest(value: unknown): SpinUIManifest {
  if (!isRecord(value) || value.schemaVersion !== 1 || value.treeHashAlgorithm !== TREE_HASH_ALGORITHM) {
    throw new Error("This SpinUI update manifest format is not supported.");
  }
  const releaseVersion = normalizeVersion(String(value.releaseVersion ?? ""));
  if (!isRecord(value.archive) || value.archive.name !== ARCHIVE_ASSET) {
    throw new Error("The SpinUI manifest does not identify the official UI archive.");
  }
  if (!Number.isSafeInteger(value.archive.size) || (value.archive.size as number) < 1 || (value.archive.size as number) > MAX_ARCHIVE_BYTES) {
    throw new Error("The SpinUI archive size is outside the safe limit.");
  }
  if (!isSha256(value.archive.sha256)) throw new Error("The SpinUI archive checksum is invalid.");
  if (!isRecord(value.themes)) throw new Error("The SpinUI manifest has no theme inventories.");
  const themeKeys = Object.keys(value.themes).sort();
  if (!themeKeys.includes("spinui_reloaded") || !themeKeys.includes("spinui_glass")
      || themeKeys.some((key) => !SPINUI_SKINS.includes(key as SpinUISkinName))) {
    throw new Error("The SpinUI manifest must contain the supported theme inventories.");
  }
  return {
    schemaVersion: 1,
    releaseVersion,
    treeHashAlgorithm: TREE_HASH_ALGORITHM,
    archive: {
      name: ARCHIVE_ASSET,
      size: value.archive.size as number,
      sha256: value.archive.sha256.toLowerCase(),
    },
    themes: {
      spinui_reloaded: parseTheme(value.themes.spinui_reloaded, "spinui_reloaded"),
      spinui_glass: parseTheme(value.themes.spinui_glass, "spinui_glass"),
      ...(value.themes.spinui_pearlescent !== undefined
        ? { spinui_pearlescent: parseTheme(value.themes.spinui_pearlescent, "spinui_pearlescent") } : {}),
    },
  };
}

function validateOfficialUrl(value: string): URL {
  const url = new URL(value);
  if (url.protocol !== "https:" || !OFFICIAL_DOWNLOAD_HOSTS.has(url.hostname.toLowerCase())) {
    throw new Error("The SpinUI release redirected outside the official GitHub download service.");
  }
  if (url.username || url.password) throw new Error("Authenticated release URLs are not accepted.");
  return url;
}

async function fetchOfficial(fetchImpl: typeof fetch, input: string, init: RequestInit): Promise<Response> {
  let url = validateOfficialUrl(input);
  for (let redirects = 0; redirects <= MAX_REDIRECTS; redirects += 1) {
    const response = await fetchImpl(url, { ...init, redirect: "manual" });
    if (response.status < 300 || response.status >= 400) return response;
    const location = response.headers.get("location");
    if (!location) throw new Error("GitHub returned a redirect without a destination.");
    if (redirects === MAX_REDIRECTS) throw new Error("The SpinUI download redirected too many times.");
    url = validateOfficialUrl(new URL(location, url).toString());
  }
  throw new Error("The SpinUI release download could not be resolved.");
}

async function readNextChunk(reader: ReadableStreamDefaultReader<Uint8Array>): Promise<ReadableStreamReadResult<Uint8Array>> {
  let timer: NodeJS.Timeout | undefined;
  try {
    return await Promise.race([
      reader.read(),
      new Promise<never>((_resolve, reject) => {
        timer = setTimeout(() => reject(new Error("The SpinUI download stopped responding.")), INACTIVITY_TIMEOUT_MS);
        timer.unref?.();
      }),
    ]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

async function readLimitedBytes(response: Response, maximum: number): Promise<Uint8Array> {
  if (!response.ok) throw new Error(`GitHub returned HTTP ${response.status}.`);
  const declared = Number(response.headers.get("content-length") ?? "0");
  if (Number.isFinite(declared) && declared > maximum) throw new Error("The SpinUI release response is unexpectedly large.");
  if (!response.body) throw new Error("GitHub returned an empty SpinUI release response.");
  const chunks: Uint8Array[] = [];
  const reader = response.body.getReader();
  let total = 0;
  while (true) {
    const { done, value } = await readNextChunk(reader);
    if (done) break;
    total += value.byteLength;
    if (total > maximum) {
      await reader.cancel();
      throw new Error("The SpinUI release response exceeded its safe size limit.");
    }
    chunks.push(value);
  }
  const joined = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    joined.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return joined;
}

function safeReleaseAsset(value: unknown): ReleaseAsset | null {
  if (!isRecord(value)) return null;
  const name = typeof value.name === "string" ? value.name : "";
  const url = typeof value.browser_download_url === "string" ? value.browser_download_url : "";
  const size = typeof value.size === "number" && Number.isSafeInteger(value.size) ? value.size : -1;
  const match = typeof value.digest === "string" ? value.digest.toLowerCase().match(/^sha256:([0-9a-f]{64})$/) : null;
  if (!name || !url || size < 0 || !match) return null;
  validateOfficialUrl(url);
  return { name, url, size, sha256: match[1] };
}

function exactAsset(assets: ReleaseAsset[], name: string): ReleaseAsset {
  const matches = assets.filter((asset) => asset.name === name);
  if (matches.length !== 1) throw new Error(`The official release must contain exactly one ${name} asset.`);
  return matches[0];
}

async function fetchVerifiedAsset(fetchImpl: typeof fetch, asset: ReleaseAsset, maximum: number): Promise<Uint8Array> {
  if (asset.size > maximum) throw new Error(`${asset.name} exceeds its safe download limit.`);
  const response = await fetchOfficial(fetchImpl, asset.url, {
    signal: AbortSignal.timeout(60_000),
    headers: { "User-Agent": "Loremaster-SpinUI-Updater" },
  });
  const bytes = await readLimitedBytes(response, maximum);
  if (bytes.byteLength !== asset.size) throw new Error(`${asset.name} size differs from GitHub release metadata.`);
  const actual = createHash("sha256").update(bytes).digest("hex");
  if (actual !== asset.sha256) throw new Error(`${asset.name} checksum differs from GitHub release metadata.`);
  return bytes;
}

async function fetchRelease(fetchImpl: typeof fetch): Promise<SpinUIRelease> {
  const response = await fetchOfficial(fetchImpl, RELEASE_API, {
    signal: AbortSignal.timeout(30_000),
    headers: {
      Accept: "application/vnd.github+json",
      "User-Agent": "Loremaster-SpinUI-Updater",
      "X-GitHub-Api-Version": "2022-11-28",
    },
  });
  const releaseBytes = await readLimitedBytes(response, MAX_RELEASE_BYTES);
  const source = JSON.parse(Buffer.from(releaseBytes).toString("utf8")) as unknown;
  if (!isRecord(source) || source.draft === true || source.prerelease === true) {
    throw new Error("GitHub did not return a stable public SpinUI release.");
  }
  const version = normalizeVersion(String(source.tag_name ?? ""));
  const assets = Array.isArray(source.assets)
    ? source.assets.map(safeReleaseAsset).filter((asset): asset is ReleaseAsset => Boolean(asset))
    : [];
  const archive = exactAsset(assets, ARCHIVE_ASSET);
  const manifestAsset = exactAsset(assets, MANIFEST_ASSET);
  const checksums = exactAsset(assets, CHECKSUM_ASSET);
  const releaseUrl = typeof source.html_url === "string" ? source.html_url : RELEASE_PAGE;
  validateOfficialUrl(releaseUrl);

  const checksumBytes = await fetchVerifiedAsset(fetchImpl, checksums, MAX_CHECKSUM_BYTES);
  const checksumMap = parseChecksums(Buffer.from(checksumBytes).toString("utf8"));
  for (const asset of [archive, manifestAsset]) {
    if (checksumMap.get(asset.name) !== asset.sha256) {
      throw new Error(`GitHub's ${asset.name} digest and SHA256SUMS.txt do not agree.`);
    }
  }
  const manifestBytes = await fetchVerifiedAsset(fetchImpl, manifestAsset, MAX_MANIFEST_BYTES);
  const manifest = parseSpinUIManifest(JSON.parse(Buffer.from(manifestBytes).toString("utf8")));
  if (manifest.releaseVersion !== version) throw new Error("The SpinUI manifest version does not match the GitHub release tag.");
  if (
    manifest.archive.name !== archive.name
    || manifest.archive.size !== archive.size
    || manifest.archive.sha256 !== archive.sha256
  ) {
    throw new Error("The SpinUI manifest does not authenticate the published UI archive.");
  }
  return { version, releaseUrl, archive, manifestAsset, checksums, manifest };
}

function directChild(root: string, name: string): string {
  const candidate = path.resolve(root, name);
  const relative = path.relative(path.resolve(root), candidate);
  if (!relative || relative !== name || relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error("The SpinUI updater refused a path outside its owned directory.");
  }
  return candidate;
}

async function isRegularFile(filePath: string): Promise<boolean> {
  try {
    return (await lstat(filePath)).isFile();
  } catch {
    return false;
  }
}

async function isRealDirectory(directory: string): Promise<boolean> {
  try {
    const info = await lstat(directory);
    return info.isDirectory() && !info.isSymbolicLink();
  } catch {
    return false;
  }
}

export async function deriveEverQuestRoot(candidate: string): Promise<string> {
  if (!candidate?.trim()) throw new Error("Choose the EverQuest Legends folder containing eqgame.exe.");
  let current = path.resolve(candidate.trim());
  try {
    const info = await lstat(current);
    if (info.isFile()) current = path.dirname(current);
    else if (!info.isDirectory() || info.isSymbolicLink()) throw new Error("The selected EverQuest path is not a real folder.");
  } catch (error) {
    if (error instanceof Error && error.message.includes("real folder")) throw error;
    current = path.dirname(current);
  }
  for (let depth = 0; depth < 8; depth += 1) {
    if (await isRegularFile(path.join(current, "eqgame.exe"))) {
      const uiFiles = path.join(current, "uifiles");
      if (!await isRealDirectory(uiFiles)) throw new Error("The selected EverQuest folder has no usable uifiles directory.");
      return realpath(current);
    }
    const parent = path.dirname(current);
    if (parent === current) break;
    current = parent;
  }
  throw new Error("Could not find eqgame.exe above the selected location.");
}

const execFileAsync = promisify(execFile);

export async function isEverQuestRunning(): Promise<boolean> {
  if (process.platform !== "win32") return false;
  const windowsRoot = process.env.SystemRoot || process.env.WINDIR;
  if (!windowsRoot || !path.isAbsolute(windowsRoot)) {
    throw new Error("The trusted Windows system directory could not be resolved.");
  }
  const tasklist = path.join(windowsRoot, "System32", "tasklist.exe");
  if (!existsSync(tasklist)) throw new Error("Windows Task List is unavailable.");
  const { stdout } = await execFileAsync(
    tasklist,
    ["/FI", "IMAGENAME eq eqgame.exe", "/FO", "CSV", "/NH"],
    { encoding: "utf8", windowsHide: true, timeout: 10_000, maxBuffer: 1024 * 1024 },
  );
  return /(?:^|[\r\n])\s*"?eqgame\.exe"?(?:,|\s|$)/i.test(stdout);
}

async function sha256File(filePath: string): Promise<string> {
  const digest = createHash("sha256");
  const stream = createReadStream(filePath);
  for await (const chunk of stream) digest.update(chunk as Buffer);
  return digest.digest("hex");
}

async function scanTheme(directory: string): Promise<SpinUIManifestTheme> {
  const rootInfo = await lstat(directory);
  if (!rootInfo.isDirectory() || rootInfo.isSymbolicLink()) throw new Error("The SpinUI theme is not a real directory.");
  const rows: SpinUIManifestFile[] = [];
  const directories = new Set<string>();
  const seen = new Set<string>();

  const visit = async (current: string, prefix: string): Promise<void> => {
    const entries = (await readdir(current, { withFileTypes: true })).sort((a, b) => compareUtf8(a.name, b.name));
    for (const entry of entries) {
      const absolute = path.join(current, entry.name);
      const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
      const info = await lstat(absolute);
      if (info.isSymbolicLink()) throw new Error(`The SpinUI theme contains a symbolic link: ${relative}`);
      if (info.isDirectory()) {
        directories.add(relative);
        await visit(absolute, relative);
        continue;
      }
      if (!info.isFile()) throw new Error(`The SpinUI theme contains an unsupported entry: ${relative}`);
      const safePath = safeRelativeThemePath(relative);
      const folded = safePath.toLocaleLowerCase("en-US");
      if (seen.has(folded)) throw new Error("The SpinUI theme contains Windows-colliding paths.");
      seen.add(folded);
      if (rows.length >= MAX_THEME_FILES || info.size > MAX_THEME_FILE_BYTES) {
        throw new Error("The installed SpinUI theme exceeds its safe scan limit.");
      }
      const digest = await sha256File(absolute);
      const after = await stat(absolute);
      if (after.size !== info.size || after.mtimeMs !== info.mtimeMs) {
        throw new Error(`The SpinUI file changed while it was being verified: ${relative}`);
      }
      rows.push({ path: safePath, size: info.size, sha256: digest });
    }
  };
  await visit(directory, "");
  rows.sort((a, b) => compareUtf8(a.path, b.path));
  if (!rows.length) throw new Error("The SpinUI theme contains no files.");
  const totalBytes = rows.reduce((total, row) => total + row.size, 0);
  if (totalBytes > MAX_THEME_BYTES) throw new Error("The installed SpinUI theme exceeds its safe size limit.");
  const validDirectories = new Set<string>();
  for (const row of rows) {
    const parts = row.path.split("/");
    for (let index = 1; index < parts.length; index += 1) validDirectories.add(parts.slice(0, index).join("/"));
  }
  for (const directoryName of directories) {
    if (!validDirectories.has(directoryName)) throw new Error(`The SpinUI theme contains an unexpected empty directory: ${directoryName}`);
  }
  return {
    fileCount: rows.length,
    totalBytes,
    treeSha256: computeSpinUITreeSha256(rows),
    files: rows,
  };
}

function treeMatches(actual: SpinUIManifestTheme, expected: SpinUIManifestTheme): boolean {
  return actual.fileCount === expected.fileCount
    && actual.totalBytes === expected.totalBytes
    && actual.treeSha256 === expected.treeSha256;
}

async function removeOwnedEntry(entryPath: string, ownedRoot: string): Promise<void> {
  const relative = path.relative(path.resolve(ownedRoot), path.resolve(entryPath));
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error("Refusing to remove a path outside Loremaster's SpinUI update directory.");
  }
  try {
    const info = await lstat(entryPath);
    if (info.isSymbolicLink() || !info.isDirectory()) await unlink(entryPath);
    else await rm(entryPath, { recursive: true, force: true });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
  }
}

async function ensureOwnedDirectory(parent: string, name: string): Promise<string> {
  const parentReal = await realpath(parent);
  const candidate = directChild(parentReal, name);
  try {
    const info = await lstat(candidate);
    if (!info.isDirectory() || info.isSymbolicLink()) {
      throw new Error("Loremaster's SpinUI update directory is not a real folder.");
    }
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    await mkdir(candidate);
  }
  const candidateReal = await realpath(candidate);
  if (path.dirname(candidateReal).toLocaleLowerCase("en-US") !== parentReal.toLocaleLowerCase("en-US")) {
    throw new Error("Loremaster's SpinUI update directory resolves outside the EverQuest folder.");
  }
  return candidateReal;
}

async function writeVerifiedArchive(
  fetchImpl: typeof fetch,
  asset: ReleaseAsset,
  destination: string,
  maximum: number,
  onProgress: (received: number) => void,
): Promise<void> {
  if (asset.size > maximum) throw new Error("The SpinUI archive exceeds its safe download limit.");
  const temporary = `${destination}.${process.pid}.${randomBytes(5).toString("hex")}.part`;
  const response = await fetchOfficial(fetchImpl, asset.url, {
    signal: AbortSignal.timeout(30 * 60_000),
    headers: { "User-Agent": "Loremaster-SpinUI-Updater" },
  });
  if (!response.ok) throw new Error(`GitHub returned HTTP ${response.status} while downloading SpinUI.`);
  if (!response.body) throw new Error("GitHub returned an empty SpinUI archive.");
  const declared = Number(response.headers.get("content-length") ?? "0");
  if (declared > 0 && declared !== asset.size) throw new Error("The SpinUI archive size differs from GitHub metadata.");
  const output = createWriteStream(temporary, { flags: "wx" });
  const digest = createHash("sha256");
  let received = 0;
  try {
    const reader = response.body.getReader();
    while (true) {
      const { done, value } = await readNextChunk(reader);
      if (done) break;
      received += value.byteLength;
      if (received > maximum || received > asset.size) {
        await reader.cancel();
        throw new Error("The SpinUI archive exceeded its authenticated size.");
      }
      digest.update(value);
      if (!output.write(value)) await once(output, "drain");
      onProgress(received);
    }
    output.end();
    await once(output, "close");
    if (received !== asset.size) throw new Error("The SpinUI archive download was incomplete.");
    if (digest.digest("hex") !== asset.sha256) throw new Error("The downloaded SpinUI archive checksum did not match the release.");
    await rm(destination, { force: true });
    await rename(temporary, destination);
  } catch (error) {
    output.destroy();
    await rm(temporary, { force: true }).catch(() => undefined);
    throw error;
  }
}

async function defaultExtract(archivePath: string, destination: string): Promise<void> {
  const { default: extract } = await import("@electron-internal/extract-zip");
  await extract(archivePath, { dir: destination });
}

function receiptKey(eqRoot: string, theme: SpinUISkinName): string {
  return `${path.resolve(eqRoot).toLocaleLowerCase("en-US")}|${theme}`;
}

export class SpinUISkinUpdateService {
  private readonly userDataDir: string;
  private readonly fetchImpl: typeof fetch;
  private readonly extractImpl: ExtractImplementation;
  private readonly eqProcessCheck: EqProcessCheck;
  private readonly archiveMaximumBytes: number;
  private readonly listeners = new Set<StateListener>();
  private state: SpinUIUpdateState;
  private checkedRelease: SpinUIRelease | null = null;
  private operation: Promise<unknown> | null = null;

  constructor(options: SpinUISkinUpdaterOptions) {
    if (!path.isAbsolute(options.userDataDir)) throw new Error("Loremaster's update data directory must be absolute.");
    this.userDataDir = path.resolve(options.userDataDir);
    this.fetchImpl = options.fetchImpl ?? fetch;
    this.extractImpl = options.extractImpl ?? defaultExtract;
    this.eqProcessCheck = options.eqProcessCheck ?? isEverQuestRunning;
    this.archiveMaximumBytes = options.archiveMaximumBytes ?? MAX_ARCHIVE_BYTES;
    this.state = initialState(options.eqRoot?.trim() ? path.resolve(options.eqRoot) : null);
  }

  subscribe(listener: StateListener): () => void {
    this.listeners.add(listener);
    listener(this.getState());
    return () => this.listeners.delete(listener);
  }

  getState(): SpinUIUpdateState {
    return cloneState(this.state);
  }

  private emit(): void {
    const snapshot = this.getState();
    for (const listener of this.listeners) listener(snapshot);
  }

  private patchTheme(theme: SpinUISkinName, patch: Partial<SpinUISkinStatus>): void {
    this.state.themes[theme] = {
      ...this.state.themes[theme],
      ...patch,
      percent: boundedPercent(patch.percent ?? this.state.themes[theme].percent),
    };
    this.emit();
  }

  private async exclusive<T>(operation: () => Promise<T>): Promise<T> {
    if (this.operation) throw new Error("Another SpinUI update operation is already running.");
    this.state.busy = true;
    this.emit();
    const running = operation();
    this.operation = running;
    try {
      return await running;
    } finally {
      this.operation = null;
      this.state.busy = false;
      this.emit();
    }
  }

  async setEqRoot(candidate: string): Promise<string> {
    if (this.operation) throw new Error("Wait for the current SpinUI update operation to finish.");
    const root = await deriveEverQuestRoot(candidate);
    this.state.eqRoot = root;
    this.emit();
    return root;
  }

  private async resolveRoot(candidate?: string): Promise<string> {
    const selected = candidate?.trim() || this.state.eqRoot;
    if (!selected) throw new Error("Choose the EverQuest Legends folder before updating SpinUI.");
    const root = await deriveEverQuestRoot(selected);
    this.state.eqRoot = root;
    return root;
  }

  async check(candidate?: string): Promise<SpinUIUpdateState> {
    return this.exclusive(async () => {
      for (const theme of SPINUI_SKINS) {
        this.patchTheme(theme, { phase: "checking", percent: 10, detail: "Checking the official SpinUI release." });
      }
      try {
        const release = await fetchRelease(this.fetchImpl);
        this.checkedRelease = release;
        this.state.latestVersion = release.version;
        this.state.releaseUrl = release.releaseUrl;
        this.state.lastCheckedAt = new Date().toISOString();
        let root: string;
        try {
          root = await this.resolveRoot(candidate);
        } catch (error) {
          const detail = error instanceof Error ? error.message : String(error);
          for (const theme of SPINUI_SKINS) {
            this.patchTheme(theme, {
              phase: "missing", percent: 100, detail,
              latestVersion: release.version, installed: false, installedVersion: null, modified: false,
            });
          }
          return this.getState();
        }
        const receipts = await this.readReceipts();
        for (const theme of SPINUI_SKINS) {
          await this.inspectInstalledTheme(root, theme, release, receipts);
        }
        return this.getState();
      } catch (error) {
        this.checkedRelease = null;
        const detail = error instanceof Error ? error.message : String(error);
        for (const theme of SPINUI_SKINS) this.patchTheme(theme, { phase: "error", percent: 0, detail });
        return this.getState();
      }
    });
  }

  private async inspectInstalledTheme(
    root: string,
    theme: SpinUISkinName,
    release: SpinUIRelease,
    receipts: Map<string, ReceiptEntry>,
  ): Promise<void> {
    const uiFiles = await realpath(path.join(root, "uifiles"));
    const target = directChild(uiFiles, theme);
    const latestVersion = release.version;
    const expected = release.manifest.themes[theme];
    if (!expected) {
      this.patchTheme(theme, { phase: "idle", installed: await isRealDirectory(target), latestVersion: null,
        percent: 0, detail: "This theme is not included in the latest published release yet.", modified: false });
      return;
    }
    if (!await isRealDirectory(target)) {
      this.patchTheme(theme, {
        phase: "missing", installed: false, installedVersion: null, latestVersion,
        percent: 100, detail: `${theme} is not installed in this EverQuest folder.`, modified: false,
      });
      return;
    }
    try {
      const actual = await scanTheme(target);
      if (treeMatches(actual, expected)) {
        this.patchTheme(theme, {
          phase: "current", installed: true, installedVersion: latestVersion, latestVersion,
          percent: 100, detail: `${theme} is verified and up to date.`, modified: false,
        });
        receipts.set(receiptKey(root, theme), {
          eqRoot: root,
          targetPath: target,
          theme,
          version: latestVersion,
          treeSha256: expected.treeSha256,
          installedAt: new Date().toISOString(),
        });
        await this.writeReceipts(receipts);
        return;
      }
      const receipt = receipts.get(receiptKey(root, theme));
      const unmodifiedOlderInstall = receipt
        && receipt.version !== latestVersion
        && receipt.treeSha256 === actual.treeSha256;
      this.patchTheme(theme, {
        phase: unmodifiedOlderInstall ? "available" : "modified",
        installed: true,
        installedVersion: receipt?.version ?? null,
        latestVersion,
        percent: 100,
        detail: unmodifiedOlderInstall
          ? `${theme} ${latestVersion} is available.`
          : `${theme} differs from the verified release; Update will replace only this theme and retain a rollback copy.`,
        modified: !unmodifiedOlderInstall,
      });
    } catch (error) {
      this.patchTheme(theme, {
        phase: "modified", installed: true, latestVersion, percent: 100, modified: true,
        detail: error instanceof Error ? error.message : String(error),
      });
    }
  }

  async install(theme: SpinUISkinName, candidate?: string): Promise<SpinUISkinInstallResult> {
    if (!SPINUI_SKINS.includes(theme)) throw new Error("Unknown SpinUI theme.");
    return this.exclusive(async () => {
      const root = await this.resolveRoot(candidate);
      const release = this.checkedRelease ?? await fetchRelease(this.fetchImpl);
      this.checkedRelease = release;
      this.state.latestVersion = release.version;
      this.state.releaseUrl = release.releaseUrl;
      return this.installOne(root, theme, release);
    });
  }

  async installAll(themes: readonly SpinUISkinName[], candidate?: string): Promise<SpinUISkinInstallResult[]> {
    const selected = [...new Set(themes)];
    if (!selected.length || selected.some((theme) => !SPINUI_SKINS.includes(theme))) {
      throw new Error("Choose at least one valid SpinUI theme to update.");
    }
    return this.exclusive(async () => {
      const root = await this.resolveRoot(candidate);
      const release = this.checkedRelease ?? await fetchRelease(this.fetchImpl);
      this.checkedRelease = release;
      this.state.latestVersion = release.version;
      this.state.releaseUrl = release.releaseUrl;
      if (selected.some((theme) => !release.manifest.themes[theme])) {
        throw new Error("A selected theme is not included in the published release yet.");
      }
      const archivePath = await this.ensureArchive(release, selected);
      if (await this.eqProcessCheck()) {
        for (const theme of selected) this.patchTheme(theme, {
          phase: "waiting-for-eq", percent: 100, detail: "Update verified. Close EverQuest, then install again.",
        });
        throw new EverQuestRunningError();
      }
      const results: SpinUISkinInstallResult[] = [];
      for (const theme of selected) results.push(await this.installOne(root, theme, release, archivePath));
      return results;
    });
  }

  private async installOne(
    root: string,
    theme: SpinUISkinName,
    release: SpinUIRelease,
    preparedArchive?: string,
  ): Promise<SpinUISkinInstallResult> {
    const expected = release.manifest.themes[theme];
    if (!expected) throw new Error("This theme is not included in the published release yet.");
    const archivePath = preparedArchive ?? await this.ensureArchive(release, [theme]);
    if (await this.eqProcessCheck()) {
      this.patchTheme(theme, {
        phase: "waiting-for-eq", percent: 100,
        detail: "Update verified. Close EverQuest, then install again.", latestVersion: release.version,
      });
      throw new EverQuestRunningError();
    }
    const uiFiles = await realpath(path.join(root, "uifiles"));
    const target = directChild(uiFiles, theme);
    const ownedReal = await ensureOwnedDirectory(root, OWNED_UPDATE_DIRECTORY);
    const stage = directChild(ownedReal, `stage-${theme}-${process.pid}-${randomBytes(6).toString("hex")}`);
    const backupsReal = await ensureOwnedDirectory(ownedReal, "backups");
    const backup = directChild(backupsReal, theme);
    let targetMoved = false;
    let newTargetMoved = false;
    let hadTarget = false;
    try {
      await mkdir(stage);
      this.patchTheme(theme, {
        phase: "verifying", percent: 85, detail: `Safely extracting and verifying ${theme}.`, latestVersion: release.version,
      });
      await this.extractImpl(archivePath, stage);
      const extractedTheme = directChild(stage, theme);
      const extracted = await scanTheme(extractedTheme);
      if (!treeMatches(extracted, expected)) {
        throw new Error(`The extracted ${theme} tree does not match the authenticated release manifest.`);
      }
      if (await this.eqProcessCheck()) throw new EverQuestRunningError();
      this.patchTheme(theme, { phase: "installing", percent: 94, detail: `Installing ${theme} with rollback protection.` });
      await removeOwnedEntry(backup, ownedReal);
      try {
        const targetInfo = await lstat(target);
        if (!targetInfo.isDirectory() || targetInfo.isSymbolicLink()) {
          throw new Error(`Refusing to replace ${theme} because it is not a real directory.`);
        }
        hadTarget = true;
        await rename(target, backup);
        targetMoved = true;
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
      }
      await rename(extractedTheme, target);
      newTargetMoved = true;
      const installed = await scanTheme(target);
      if (!treeMatches(installed, expected)) {
        throw new Error(`The installed ${theme} tree failed its final verification.`);
      }
      const receipts = await this.readReceipts();
      receipts.set(receiptKey(root, theme), {
        eqRoot: root,
        targetPath: target,
        theme,
        version: release.version,
        treeSha256: expected.treeSha256,
        installedAt: new Date().toISOString(),
      });
      await this.writeReceipts(receipts);
      this.patchTheme(theme, {
        phase: "installed", installed: true, installedVersion: release.version,
        latestVersion: release.version, percent: 100, modified: false,
        detail: `${theme} ${release.version} installed and verified.`,
      });
      return { theme, version: release.version, targetPath: target, backupPath: hadTarget ? backup : null };
    } catch (error) {
      if (newTargetMoved) await removeOwnedEntry(target, uiFiles).catch(() => undefined);
      if (targetMoved && await isRealDirectory(backup)) await rename(backup, target).catch(() => undefined);
      const waiting = error instanceof EverQuestRunningError;
      this.patchTheme(theme, {
        phase: waiting ? "waiting-for-eq" : "error",
        percent: waiting ? 100 : 0,
        detail: error instanceof Error ? error.message : String(error),
      });
      throw error;
    } finally {
      await removeOwnedEntry(stage, ownedReal).catch(() => undefined);
    }
  }

  private async ensureArchive(release: SpinUIRelease, themes: readonly SpinUISkinName[]): Promise<string> {
    const cacheDirectory = path.join(this.userDataDir, "updates", "spinui", `v${release.version}`);
    await mkdir(cacheDirectory, { recursive: true });
    const destination = directChild(cacheDirectory, ARCHIVE_ASSET);
    let validCached = false;
    if (await isRegularFile(destination)) {
      const info = await stat(destination);
      validCached = info.size === release.archive.size && await sha256File(destination) === release.archive.sha256;
    }
    if (!validCached) {
      for (const theme of themes) this.patchTheme(theme, {
        phase: "downloading", percent: 2, detail: "Downloading the authenticated SpinUI package.", latestVersion: release.version,
      });
      await writeVerifiedArchive(
        this.fetchImpl,
        release.archive,
        destination,
        this.archiveMaximumBytes,
        (received) => {
          const percent = release.archive.size > 0 ? 2 + (received / release.archive.size) * 78 : 40;
          for (const theme of themes) this.patchTheme(theme, {
            phase: "downloading", percent, detail: "Downloading the authenticated SpinUI package.",
          });
        },
      );
    }
    const finalInfo = await stat(destination);
    if (finalInfo.size !== release.manifest.archive.size || await sha256File(destination) !== release.manifest.archive.sha256) {
      await rm(destination, { force: true });
      throw new Error("The cached SpinUI archive failed manifest verification.");
    }
    return destination;
  }

  private receiptPath(): string {
    return path.join(this.userDataDir, RECEIPT_FILENAME);
  }

  private async readReceipts(): Promise<Map<string, ReceiptEntry>> {
    const result = new Map<string, ReceiptEntry>();
    try {
      const raw = await readFile(this.receiptPath(), "utf8");
      if (Buffer.byteLength(raw) > 1024 * 1024) return result;
      const parsed = JSON.parse(raw) as unknown;
      if (!isRecord(parsed) || parsed.schemaVersion !== 1 || !Array.isArray(parsed.installations)) return result;
      for (const value of parsed.installations) {
        if (!isRecord(value) || !SPINUI_SKINS.includes(value.theme as SpinUISkinName)) continue;
        if (typeof value.eqRoot !== "string" || typeof value.targetPath !== "string" || typeof value.version !== "string") continue;
        if (!isSha256(value.treeSha256) || typeof value.installedAt !== "string") continue;
        const entry: ReceiptEntry = {
          eqRoot: path.resolve(value.eqRoot),
          targetPath: path.resolve(value.targetPath),
          theme: value.theme as SpinUISkinName,
          version: normalizeVersion(value.version),
          treeSha256: value.treeSha256.toLowerCase(),
          installedAt: value.installedAt,
        };
        result.set(receiptKey(entry.eqRoot, entry.theme), entry);
      }
    } catch {
      // Missing or damaged receipts never grant trust; a full tree verification
      // will classify the installation and can safely recreate the receipt.
    }
    return result;
  }

  private async writeReceipts(receipts: Map<string, ReceiptEntry>): Promise<void> {
    await mkdir(this.userDataDir, { recursive: true });
    const payload: ReceiptFile = {
      schemaVersion: 1,
      installations: [...receipts.values()].sort((a, b) => compareUtf8(receiptKey(a.eqRoot, a.theme), receiptKey(b.eqRoot, b.theme))),
    };
    const destination = this.receiptPath();
    const temporary = `${destination}.${process.pid}.${randomBytes(5).toString("hex")}.tmp`;
    await writeFile(temporary, `${JSON.stringify(payload, null, 2)}\n`, { encoding: "utf8", flag: "wx" });
    await rm(destination, { force: true });
    await rename(temporary, destination);
  }
}

export const spinUISkinUpdaterConstants = {
  releaseApi: RELEASE_API,
  releasePage: RELEASE_PAGE,
  archiveAsset: ARCHIVE_ASSET,
  manifestAsset: MANIFEST_ASSET,
  checksumAsset: CHECKSUM_ASSET,
  treeHashAlgorithm: TREE_HASH_ALGORITHM,
  ownedUpdateDirectory: OWNED_UPDATE_DIRECTORY,
};
