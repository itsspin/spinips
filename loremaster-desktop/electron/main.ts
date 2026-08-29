import { app, BrowserWindow, dialog, ipcMain, Menu, nativeImage, screen, shell, Tray } from "electron";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { existsSync, readFileSync, readdirSync, renameSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";
import { createInterface } from "node:readline";
import {
  buildGearPlan,
  catalogFromEqToolsData,
  emptyGearPlan,
  parseEqToolsBuild,
  parseInventory,
  type GearCatalogRecord,
  type GearPlanView,
  type InventoryEntry,
} from "./gear-plan";
import { ItemIntelligenceService } from "./item-intelligence";
import {
  acknowledgePortableUpdateRelaunch,
  PortableUpdateService,
  type StagedPortableUpdate,
  type UpdateCheckResult,
  type UpdateProgress,
} from "./portable-updater";
import {
  EverQuestRunningError,
  SPINUI_SKINS,
  SpinUISkinUpdateService,
  type SpinUISkinName,
  type SpinUISkinStatus,
  type SpinUIUpdateState,
} from "./spinui-updater";
import { boundedCompanionLayout, companionSurfaceSize } from "./companion-layout";

const processStartedAt = performance.now();
app.commandLine.appendSwitch("autoplay-policy", "no-user-gesture-required");
if (process.env.LOREMASTER_DESKTOP_DATA_DIR) {
  app.setPath("userData", path.resolve(process.env.LOREMASTER_DESKTOP_DATA_DIR));
}
const ownsSingleInstance = app.requestSingleInstanceLock();
if (!ownsSingleInstance) app.quit();
let mainWindow: BrowserWindow | null = null;
let alertWindow: BrowserWindow | null = null;
let controlWindow: BrowserWindow | null = null;
let engine: EngineSupervisor | null = null;
let windowExpanded = false;
let windowAnalysis = false;
let expansionDirection: "up" | "down" = "down";
let movingWindowProgrammatically = false;
let tray: Tray | null = null;
let trayMinimizeNoticeShown = false;
let topmostReassertTimers: NodeJS.Timeout[] = [];
let topmostHeartbeatTimer: NodeJS.Timeout | null = null;
let itemIntelligence: ItemIntelligenceService | null = null;
let portableUpdater: PortableUpdateService | null = null;
let spinUISkinUpdater: SpinUISkinUpdateService | null = null;
let stagedPortableUpdate: StagedPortableUpdate | null = null;
let portableInstallHandoff = false;
let portableRelaunchAcknowledged = false;
let portableRelaunchAcknowledgeInFlight = false;
let rendererHealthyForUpdate = false;
let engineHealthyForUpdate = false;
let updateCenterState: UpdateCenterState | null = null;

const SEED_SIZE = { width: 128, height: 74 } as const;
const EXPANDED_SIZE = { width: 470, height: 580 } as const;
const ANALYSIS_SIZE = { width: 1180, height: 760 } as const;
const ALERT_SIZE = { width: 420, height: 112 } as const;
const screenshotControls = [
  {
    kind: "mez", state: "active", target: "an essence carrier", count: 2,
    spell: "Mesmerization", rank: 5, landedAt: "2026-08-06T20:00:03.000Z",
    safeExpiresAt: "2026-08-06T20:00:39.000Z", expiresAt: "2026-08-06T20:00:45.000Z",
    durationSeconds: 36, safeRemainingSeconds: 28, remainingSeconds: 34,
    lastTick: false, urgency: "safe", confidence: "confirmed", ambiguity: "",
  },
  {
    kind: "lull", state: "active", target: "a soul carrier", count: 1,
    spell: "Calm", rank: 5, landedAt: "2026-08-06T20:00:32.000Z",
    safeExpiresAt: "2026-08-06T20:01:32.000Z", expiresAt: "2026-08-06T20:01:38.000Z",
    durationSeconds: 60, safeRemainingSeconds: 9, remainingSeconds: 15,
    lastTick: false, urgency: "warning", confidence: "exact", ambiguity: "",
  },
] as const;
type AlertAnchor = "auto" | "above" | "below" | "left" | "right";
type AlertSoundKind = "default" | "charmBreak" | "tell" | "summon" | "death" | "bigHit" | "nameCalled" | "mez" | "lull";
type AlertSoundPreset = "rune" | "crystal" | "ember" | "bell" | "custom" | "silent";
interface AlertSoundProfile { preset: AlertSoundPreset; customPath: string }
type AlertSoundProfiles = Record<AlertSoundKind, AlertSoundProfile>;

export interface AlertSettings {
  alertsEnabled: boolean;
  alertSound: boolean;
  alertSeconds: number;
  alertAnchor: AlertAnchor;
  alertCharmBreak: boolean;
  alertTells: boolean;
  alertSummon: boolean;
  alertDeath: boolean;
  alertBigHit: boolean;
  alertNameCalled: boolean;
  bigHitThreshold: number;
  mezTimersEnabled: boolean;
  mezTimerSound: boolean;
  mezWarningSeconds: number;
  lullTimersEnabled: boolean;
  lullTimerSound: boolean;
  lullWarningSeconds: number;
  soundProfiles: AlertSoundProfiles;
}

interface DesktopSettings {
  logPath: string;
  eqRoot: string;
  autoCheckUpdates: boolean;
  raidDifficulty: number | null;
  bisBuildPath: string;
  inventoryPath: string;
  uiTheme: "vellum" | "glass";
  alwaysOnTop: boolean;
  fontScale: number;
  composition: string;
  splitCharmedPetDps: boolean;
  seedMeterVisible: boolean;
  seedMeterMode: "self" | "group" | "pet" | "all";
  seedMeterOpacity: number;
  stanceAdvisorEnabled: boolean;
  itemNetworkLookups: boolean;
  seedPosition: { x: number; y: number } | null;
  alerts: AlertSettings;
}

type UpdateComponentId = "loremaster" | "spinui_reloaded" | "spinui_glass";
type UpdateComponentPhase =
  | "idle" | "checking" | "current" | "available" | "not-installed" | "modified"
  | "downloading" | "verifying" | "ready" | "waiting-for-eq" | "installing"
  | "restart-required" | "error";
interface UpdateComponentState {
  id: UpdateComponentId;
  phase: UpdateComponentPhase;
  currentVersion: string;
  latestVersion: string;
  progress: number | null;
  detail: string;
}
interface UpdateCenterState {
  currentVersion: string;
  latestVersion: string;
  lastCheckedAt: string;
  eqRoot: string;
  busy: boolean;
  components: Record<UpdateComponentId, UpdateComponentState>;
}

interface EngineHealth {
  state: "starting" | "searching" | "live" | "error" | "stopped";
  detail: string;
  configuredPath: string;
  activeLogPath: string;
  character: string;
  server: string;
}

const defaultHealth: EngineHealth = {
  state: "starting",
  detail: "Starting the local parser engine",
  configuredPath: "",
  activeLogPath: "",
  character: "?",
  server: "?",
};

const ALERT_SOUND_KINDS: readonly AlertSoundKind[] = [
  "default", "charmBreak", "tell", "summon", "death", "bigHit", "nameCalled", "mez", "lull",
];
const ALERT_SOUND_PRESETS: readonly AlertSoundPreset[] = ["rune", "crystal", "ember", "bell", "custom", "silent"];
const defaultSoundProfiles: AlertSoundProfiles = {
  default: { preset: "rune", customPath: "" },
  charmBreak: { preset: "ember", customPath: "" },
  tell: { preset: "crystal", customPath: "" },
  summon: { preset: "ember", customPath: "" },
  death: { preset: "ember", customPath: "" },
  bigHit: { preset: "rune", customPath: "" },
  nameCalled: { preset: "crystal", customPath: "" },
  mez: { preset: "rune", customPath: "" },
  lull: { preset: "bell", customPath: "" },
};

function normalizeSoundProfiles(value: unknown): AlertSoundProfiles {
  const source = value && typeof value === "object" ? value as Record<string, unknown> : {};
  return Object.fromEntries(ALERT_SOUND_KINDS.map((kind) => {
    const candidate = source[kind] && typeof source[kind] === "object"
      ? source[kind] as Partial<AlertSoundProfile> : {};
    const preset = ALERT_SOUND_PRESETS.includes(candidate.preset as AlertSoundPreset)
      ? candidate.preset as AlertSoundPreset : defaultSoundProfiles[kind].preset;
    const customPath = typeof candidate.customPath === "string" && candidate.customPath.length <= 4096
      ? candidate.customPath : "";
    return [kind, { preset, customPath }];
  })) as AlertSoundProfiles;
}

const defaultAlertSettings: AlertSettings = {
  alertsEnabled: true,
  alertSound: true,
  alertSeconds: 5,
  alertAnchor: "auto",
  alertCharmBreak: true,
  alertTells: true,
  alertSummon: true,
  alertDeath: true,
  alertBigHit: true,
  alertNameCalled: true,
  bigHitThreshold: 800,
  mezTimersEnabled: true,
  mezTimerSound: false,
  mezWarningSeconds: 10,
  lullTimersEnabled: true,
  lullTimerSound: false,
  lullWarningSeconds: 12,
  soundProfiles: defaultSoundProfiles,
};

const defaultSettings: DesktopSettings = {
  logPath: "",
  eqRoot: "",
  autoCheckUpdates: true,
  raidDifficulty: null,
  bisBuildPath: "",
  inventoryPath: "",
  uiTheme: "vellum",
  alwaysOnTop: true,
  fontScale: 1.15,
  composition: "",
  splitCharmedPetDps: false,
  seedMeterVisible: true,
  seedMeterMode: "all",
  seedMeterOpacity: 0.9,
  stanceAdvisorEnabled: false,
  itemNetworkLookups: true,
  seedPosition: null,
  alerts: defaultAlertSettings,
};

function settingsPath(): string {
  return path.join(app.getPath("userData"), "desktop-settings.json");
}

function readSettings(): DesktopSettings {
  try {
    const value = JSON.parse(readFileSync(settingsPath(), "utf8")) as Partial<DesktopSettings>;
    const raidDifficulty = Number.isInteger(value.raidDifficulty) && Number(value.raidDifficulty) >= 0 && Number(value.raidDifficulty) <= 4
      ? Number(value.raidDifficulty)
      : null;
    const alertValue = value.alerts && typeof value.alerts === "object"
      ? value.alerts as Partial<AlertSettings>
      : {};
    const alertAnchor = ["auto", "above", "below", "left", "right"].includes(String(alertValue.alertAnchor))
      ? alertValue.alertAnchor as AlertAnchor
      : defaultAlertSettings.alertAnchor;
    const seedPosition = value.seedPosition && Number.isFinite(value.seedPosition.x) && Number.isFinite(value.seedPosition.y)
      ? { x: Math.round(value.seedPosition.x), y: Math.round(value.seedPosition.y) }
      : null;
    const clampInteger = (candidate: unknown, fallback: number, low: number, high: number) => {
      const numeric = Number(candidate);
      return Number.isFinite(numeric) ? Math.max(low, Math.min(high, Math.round(numeric))) : fallback;
    };
    const boolean = (candidate: unknown, fallback: boolean) => typeof candidate === "boolean" ? candidate : fallback;
    const logPath = typeof value.logPath === "string" ? value.logPath : "";
    const eqRoot = resolveEqRoot(typeof value.eqRoot === "string" ? value.eqRoot : "")
      || resolveEqRoot(logPath);
    return {
      logPath,
      eqRoot,
      autoCheckUpdates: boolean(value.autoCheckUpdates, true),
      raidDifficulty,
      bisBuildPath: typeof value.bisBuildPath === "string" ? value.bisBuildPath : "",
      inventoryPath: typeof value.inventoryPath === "string" ? value.inventoryPath : "",
      uiTheme: value.uiTheme === "glass" ? "glass" : "vellum",
      alwaysOnTop: boolean(value.alwaysOnTop, true),
      fontScale: clampInteger(value.fontScale === undefined ? 115 : Number(value.fontScale) * 100, 115, 90, 160) / 100,
      composition: typeof value.composition === "string" ? value.composition.slice(0, 48) : "",
      splitCharmedPetDps: boolean(value.splitCharmedPetDps, false),
      seedMeterVisible: boolean(value.seedMeterVisible, true),
      seedMeterMode: ["self", "group", "pet", "all"].includes(String(value.seedMeterMode))
        ? value.seedMeterMode as DesktopSettings["seedMeterMode"]
        : "all",
      seedMeterOpacity: Math.round(clamp(
        Number.isFinite(Number(value.seedMeterOpacity)) ? Number(value.seedMeterOpacity) : 0.9,
        0.35,
        1,
      ) * 20) / 20,
      stanceAdvisorEnabled: boolean(value.stanceAdvisorEnabled, false),
      itemNetworkLookups: boolean(value.itemNetworkLookups, true),
      seedPosition,
      alerts: {
        alertsEnabled: boolean(alertValue.alertsEnabled, defaultAlertSettings.alertsEnabled),
        alertSound: boolean(alertValue.alertSound, defaultAlertSettings.alertSound),
        alertSeconds: clampInteger(alertValue.alertSeconds, defaultAlertSettings.alertSeconds, 1, 15),
        alertAnchor,
        alertCharmBreak: boolean(alertValue.alertCharmBreak, true),
        alertTells: boolean(alertValue.alertTells, true),
        alertSummon: boolean(alertValue.alertSummon, true),
        alertDeath: boolean(alertValue.alertDeath, true),
        alertBigHit: boolean(alertValue.alertBigHit, true),
        alertNameCalled: boolean(alertValue.alertNameCalled, true),
        bigHitThreshold: clampInteger(alertValue.bigHitThreshold, 800, 1, 999999),
        mezTimersEnabled: boolean(alertValue.mezTimersEnabled, true),
        mezTimerSound: boolean(alertValue.mezTimerSound, false),
        mezWarningSeconds: clampInteger(alertValue.mezWarningSeconds, 10, 3, 30),
        lullTimersEnabled: boolean(alertValue.lullTimersEnabled, true),
        lullTimerSound: boolean(alertValue.lullTimerSound, false),
        lullWarningSeconds: clampInteger(alertValue.lullWarningSeconds, 12, 3, 30),
        soundProfiles: normalizeSoundProfiles(alertValue.soundProfiles),
      },
    };
  } catch {
    return { ...defaultSettings, alerts: { ...defaultAlertSettings, soundProfiles: normalizeSoundProfiles(null) } };
  }
}

function resolveEqRoot(value: string): string {
  const raw = value.trim().replace(/^"|"$/g, "");
  if (!raw) return "";
  let candidate = path.resolve(raw);
  try {
    if (statSync(candidate).isFile()) candidate = path.dirname(candidate);
  } catch {
    if (path.extname(candidate)) candidate = path.dirname(candidate);
  }
  for (let depth = 0; depth < 5; depth += 1) {
    if (existsSync(path.join(candidate, "eqgame.exe"))) return candidate;
    const parent = path.dirname(candidate);
    if (parent === candidate) break;
    candidate = parent;
  }
  return "";
}

function saveSettings(settings: DesktopSettings): void {
  const target = settingsPath();
  const temporary = `${target}.tmp`;
  writeFileSync(temporary, `${JSON.stringify(settings, null, 2)}\n`, "utf8");
  renameSync(temporary, target);
}

function maybeAcknowledgePortableRelaunch(): void {
  if (portableRelaunchAcknowledged || portableRelaunchAcknowledgeInFlight
      || !rendererHealthyForUpdate || !engineHealthyForUpdate) return;
  portableRelaunchAcknowledgeInFlight = true;
  void acknowledgePortableUpdateRelaunch(app.getPath("userData")).then((acknowledged) => {
    portableRelaunchAcknowledged = acknowledged;
  }).catch((error: unknown) => {
    console.error("Could not acknowledge the verified Loremaster update relaunch", error);
  }).finally(() => {
    portableRelaunchAcknowledgeInFlight = false;
  });
}

function updateMetadataPath(): string {
  return path.join(app.getPath("userData"), "update-center.json");
}

function readUpdateMetadata(): { lastCheckedAt: string; lastNotifiedVersion: string } {
  try {
    const value = JSON.parse(readFileSync(updateMetadataPath(), "utf8")) as Record<string, unknown>;
    return {
      lastCheckedAt: typeof value.lastCheckedAt === "string" ? value.lastCheckedAt : "",
      lastNotifiedVersion: typeof value.lastNotifiedVersion === "string" ? value.lastNotifiedVersion : "",
    };
  } catch {
    return { lastCheckedAt: "", lastNotifiedVersion: "" };
  }
}

function saveUpdateMetadata(value: { lastCheckedAt: string; lastNotifiedVersion: string }): void {
  const target = updateMetadataPath();
  const temporary = `${target}.tmp`;
  writeFileSync(temporary, `${JSON.stringify({ schemaVersion: 1, ...value }, null, 2)}\n`, "utf8");
  renameSync(temporary, target);
}

function initialUpdateCenterState(settings = readSettings()): UpdateCenterState {
  const currentVersion = app.getVersion();
  const metadata = readUpdateMetadata();
  const component = (id: UpdateComponentId, detail: string): UpdateComponentState => ({
    id, phase: "idle", currentVersion: id === "loremaster" ? currentVersion : "",
    latestVersion: "", progress: null, detail,
  });
  return {
    currentVersion,
    latestVersion: "",
    lastCheckedAt: metadata.lastCheckedAt,
    eqRoot: settings.eqRoot,
    busy: false,
    components: {
      loremaster: component("loremaster", "Ready to check the official SpinUI release."),
      spinui_reloaded: component("spinui_reloaded", settings.eqRoot ? "Ready to verify SpinUI Reloaded." : "Select your EverQuest folder to check this skin."),
      spinui_glass: component("spinui_glass", settings.eqRoot ? "Ready to verify SpinUI Glass." : "Select your EverQuest folder to check this skin."),
    },
  };
}

function publishUpdateCenter(): UpdateCenterState {
  updateCenterState ??= initialUpdateCenterState();
  const snapshot = structuredClone(updateCenterState);
  mainWindow?.webContents.send("updates:state", snapshot);
  return snapshot;
}

function portablePhase(value: UpdateProgress["phase"]): UpdateComponentPhase {
  return value;
}

function skinPhase(value: SpinUISkinStatus["phase"]): UpdateComponentPhase {
  if (value === "missing") return "not-installed";
  if (value === "installed") return "current";
  return value;
}

function mergePortableUpdate(progress: UpdateProgress, check?: UpdateCheckResult): void {
  updateCenterState ??= initialUpdateCenterState();
  const current = updateCenterState.components.loremaster;
  updateCenterState.components.loremaster = {
    ...current,
    phase: portablePhase(progress.phase),
    currentVersion: check?.currentVersion || current.currentVersion || app.getVersion(),
    latestVersion: check?.latestVersion || progress.version || current.latestVersion,
    progress: ["downloading", "verifying", "installing"].includes(progress.phase) ? progress.percent : null,
    detail: progress.detail,
  };
  if (check) {
    updateCenterState.latestVersion = check.latestVersion || updateCenterState.latestVersion;
  }
  updateCenterState.busy = ["downloading", "verifying", "installing"].includes(progress.phase)
    || Boolean(spinUISkinUpdater?.getState().busy);
  publishUpdateCenter();
}

function mergeSkinUpdate(state: SpinUIUpdateState): void {
  updateCenterState ??= initialUpdateCenterState();
  updateCenterState.eqRoot = state.eqRoot || updateCenterState.eqRoot;
  updateCenterState.latestVersion = state.latestVersion || updateCenterState.latestVersion;
  updateCenterState.lastCheckedAt = state.lastCheckedAt || updateCenterState.lastCheckedAt;
  for (const theme of SPINUI_SKINS) {
    const skin = state.themes[theme];
    updateCenterState.components[theme] = {
      id: theme,
      phase: skinPhase(skin.phase),
      currentVersion: skin.installedVersion || "",
      latestVersion: skin.latestVersion || "",
      progress: ["downloading", "verifying", "installing"].includes(skin.phase) ? skin.percent : null,
      detail: skin.detail,
    };
  }
  updateCenterState.busy = state.busy
    || ["downloading", "verifying", "installing"].includes(portableUpdater?.getProgress().phase ?? "idle");
  publishUpdateCenter();
}

function initializeUpdateServices(): void {
  updateCenterState = initialUpdateCenterState(engine?.getState().settings ?? readSettings());
  portableUpdater = new PortableUpdateService({
    currentVersion: app.getVersion(),
    userDataDir: app.getPath("userData"),
    ...(app.isPackaged ? {} : { executablePath: null }),
  });
  portableUpdater.subscribe((progress) => mergePortableUpdate(progress));
  spinUISkinUpdater = new SpinUISkinUpdateService({
    userDataDir: app.getPath("userData"),
    eqRoot: updateCenterState.eqRoot || null,
  });
  spinUISkinUpdater.subscribe(mergeSkinUpdate);
}

async function checkAllUpdates(): Promise<UpdateCenterState> {
  updateCenterState ??= initialUpdateCenterState();
  if (!portableUpdater || !spinUISkinUpdater) initializeUpdateServices();
  updateCenterState.busy = true;
  publishUpdateCenter();
  const eqRoot = engine?.getState().settings.eqRoot || updateCenterState.eqRoot || undefined;
  let portable: UpdateCheckResult | null = null;
  try {
    [portable] = await Promise.all([
      portableUpdater!.check(),
      spinUISkinUpdater!.check(eqRoot),
    ]);
    mergePortableUpdate(portableUpdater!.getProgress(), portable);
    mergeSkinUpdate(spinUISkinUpdater!.getState());
  } finally {
    updateCenterState.busy = false;
  }
  const checkedAt = new Date().toISOString();
  updateCenterState.lastCheckedAt = checkedAt;
  const metadata = readUpdateMetadata();
  const availableVersion = updateCenterState.components.loremaster.phase === "available"
    ? updateCenterState.components.loremaster.latestVersion
    : SPINUI_SKINS.some((theme) => ["available", "modified"].includes(updateCenterState!.components[theme].phase))
      ? updateCenterState.latestVersion : "";
  if (availableVersion && availableVersion !== metadata.lastNotifiedVersion) {
    const changedComponents = (Object.values(updateCenterState.components) as UpdateComponentState[])
      .filter((component) => ["available", "modified"].includes(component.phase))
      .map((component) => component.id === "loremaster" ? "Loremaster"
        : component.id === "spinui_reloaded" ? "SpinUI Reloaded" : "SpinUI Glass");
    alertWindow?.webContents.send("alerts:test", {
      id: `update-${availableVersion}`,
      severity: "info",
      title: `UPDATE ${availableVersion} READY`,
      target: `${changedComponents.join(" + ")} can be updated from Settings.`,
    });
    if (tray && process.platform === "win32") {
      tray.displayBalloon({
        title: `Loremaster ${availableVersion} update ready`,
        content: `${changedComponents.join(" + ")} can be updated in one click from Settings.`,
        iconType: "info",
      });
    }
    metadata.lastNotifiedVersion = availableVersion;
  }
  saveUpdateMetadata({ ...metadata, lastCheckedAt: checkedAt });
  return publishUpdateCenter();
}

function newestEqLog(selectedPath: string): string {
  const cleaned = selectedPath.trim().replace(/^"|"$/g, "");
  const candidates = cleaned
    ? [cleaned, path.join(cleaned, "Logs")]
    : [
        "C:\\EQLegends\\Logs", "C:\\EQLegends",
        "D:\\EQLegends\\Logs", "D:\\EQLegends",
        "E:\\EQLegends\\Logs", "E:\\EQLegends",
        "C:\\Users\\Public\\Daybreak Game Company\\Installed Games\\EverQuest Legends\\Logs",
        "C:\\Users\\Public\\Daybreak Game Company\\Installed Games\\EverQuest Legends",
        "C:\\Users\\Public\\Daybreak Game Company\\Installed Games\\EverQuest\\Logs",
        "C:\\Users\\Public\\Daybreak Game Company\\Installed Games\\EverQuest",
      ];
  if (cleaned.toLowerCase().endsWith(".txt") && existsSync(cleaned)) return path.resolve(cleaned);
  let newest = "";
  let newestMtime = -1;
  for (const directory of [...new Set(candidates)]) {
    try {
      for (const name of readdirSync(directory)) {
        if (!/^eqlog_.+_.+\.txt$/i.test(name)) continue;
        const candidate = path.join(directory, name);
        const mtime = statSync(candidate).mtimeMs;
        if (mtime > newestMtime) {
          newest = candidate;
          newestMtime = mtime;
        }
      }
    } catch {
      // Missing and protected candidate folders are normal during auto-detect.
    }
  }
  return newest;
}

class EngineSupervisor {
  private child: ChildProcessWithoutNullStreams | null = null;
  private stopping = false;
  private restartCount = 0;
  private restartTimer: NodeJS.Timeout | null = null;
  private attachmentRetryTimer: NodeJS.Timeout | null = null;
  private journalRequestSequence = 0;
  private readonly journalRequests = new Map<string, {
    resolve: (value: unknown) => void;
    timer: NodeJS.Timeout;
  }>();
  private health: EngineHealth = { ...defaultHealth };
  private snapshot: unknown = null;
  private settings = readSettings();
  private gearCatalog: GearCatalogRecord[] = [];
  private catalogUpdatedAt = "";
  private gearPlan: GearPlanView = emptyGearPlan(
    this.settings.bisBuildPath, this.settings.inventoryPath);

  constructor() {
    this.readCatalogCache();
    this.rebuildGearPlan();
  }

  start(): void {
    this.stopping = false;
    this.spawnWorker();
  }

  private command(): { executable: string; args: string[] } {
    if (app.isPackaged) {
      return {
        executable: path.join(process.resourcesPath, "engine", "LoremasterEngine.exe"),
        args: [],
      };
    }
    const script = path.resolve(app.getAppPath(), "..", "loremaster", "desktop_worker.py");
    const configuredPython = process.env.LOREMASTER_PYTHON;
    if (configuredPython) return { executable: configuredPython, args: [script] };
    return process.platform === "win32"
      ? { executable: "py", args: ["-3", script] }
      : { executable: "python3", args: [script] };
  }

  private publishHealth(health: EngineHealth): void {
    this.health = health;
    mainWindow?.webContents.send("engine:health", health);
    if (health.state === "searching" || health.state === "live") engineHealthyForUpdate = true;
    maybeAcknowledgePortableRelaunch();
  }

  private spawnWorker(): void {
    const command = this.command();
    if (app.isPackaged && !existsSync(command.executable)) {
      this.publishHealth({
        ...defaultHealth,
        state: "error",
        detail: "The packaged parser engine is missing. Reinstall Loremaster.",
      });
      return;
    }
    this.publishHealth({
      ...defaultHealth,
      configuredPath: this.settings.logPath,
      detail: this.restartCount > 0 ? "Restarting the local parser engine" : defaultHealth.detail,
    });
    try {
      this.child = spawn(command.executable, command.args, {
        // In a packaged build app.getAppPath() points at app.asar, which is a
        // file rather than a valid working directory. Windows reports that as
        // a spawn failure even when LoremasterEngine.exe exists and is valid.
        cwd: app.isPackaged ? process.resourcesPath : app.getAppPath(),
        env: {
          ...process.env,
          PYTHONUNBUFFERED: "1",
          LOREMASTER_APP_DATA_DIR: app.getPath("userData"),
        },
        stdio: ["pipe", "pipe", "pipe"],
        windowsHide: true,
      });
    } catch (error) {
      this.handleSpawnFailure(error);
      return;
    }
    const child = this.child;
    createInterface({ input: child.stdout }).on("line", (line) => this.handleLine(line));
    createInterface({ input: child.stderr }).on("line", (line) => {
      console.error(`[LoremasterEngine] ${line}`);
    });
    child.once("spawn", () => {
      const parserLogPath = newestEqLog(this.settings.logPath) || this.settings.logPath;
      this.send({
        type: "engine.initialize",
        logPath: parserLogPath,
        raidDifficulty: this.settings.raidDifficulty,
        composition: this.settings.composition,
        alertConfig: this.settings.alerts,
      });
    });
    child.once("error", (error) => this.handleSpawnFailure(error));
    child.once("exit", (code) => {
      this.child = null;
      this.settleJournalRequests();
      if (this.stopping) return;
      this.publishHealth({
        ...this.health,
        state: "error",
        detail: `Parser engine stopped unexpectedly${code === null ? "" : ` (${code})`}`,
      });
      if (this.restartCount < 3) {
        const delay = 350 * (2 ** this.restartCount);
        this.restartCount += 1;
        this.restartTimer = setTimeout(() => this.spawnWorker(), delay);
      }
    });
  }

  private handleSpawnFailure(error: unknown): void {
    const message = error instanceof Error ? error.message : String(error);
    this.publishHealth({
      ...this.health,
      state: "error",
      detail: `Could not start the parser engine: ${message}`,
    });
  }

  private handleLine(line: string): void {
    if (line.length > 2_000_000) return;
    let event: Record<string, unknown>;
    try {
      event = JSON.parse(line) as Record<string, unknown>;
    } catch {
      console.error("Loremaster engine emitted invalid JSON");
      return;
    }
    if (event.protocolVersion !== 1 || typeof event.eventType !== "string") return;
    if (event.eventType === "engine.loot-query" && typeof event.requestId === "string") {
      const pending = this.journalRequests.get(event.requestId);
      if (pending) {
        clearTimeout(pending.timer);
        this.journalRequests.delete(event.requestId);
        pending.resolve(event.result);
      }
    } else if (event.eventType === "engine.snapshot" && event.snapshot) {
      this.snapshot = event;
      mainWindow?.webContents.send("engine:snapshot", event);
      alertWindow?.webContents.send("engine:snapshot", event);
      syncControlWindow();
      if (process.env.LOREMASTER_SCREENSHOT_VIEW !== "controls") {
        controlWindow?.webContents.send("engine:snapshot", event);
      }
    } else if (event.eventType === "engine.health" || event.eventType === "engine.ready") {
      const health = event.health as EngineHealth | undefined;
      if (health && typeof health.state === "string") {
        this.restartCount = 0;
        this.publishHealth({ ...health, configuredPath: this.settings.logPath });
      }
    } else if (event.eventType === "engine.error") {
      this.publishHealth({
        ...this.health,
        state: "error",
        detail: typeof event.message === "string" ? event.message : "Parser engine error",
      });
    }
  }

  private send(command: Record<string, unknown>): void {
    if (!this.child?.stdin.writable) return;
    this.child.stdin.write(`${JSON.stringify(command)}\n`);
  }

  private settleJournalRequests(): void {
    for (const pending of this.journalRequests.values()) {
      clearTimeout(pending.timer);
      pending.resolve({ rows: [], total: 0, offset: 0, hasMore: false });
    }
    this.journalRequests.clear();
  }

  queryLoot(value: unknown): Promise<unknown> {
    const request = value && typeof value === "object"
      ? value as Record<string, unknown> : {};
    const scope = ["all", "mine", "others", "known"].includes(String(request.scope))
      ? String(request.scope) : "all";
    const rawTier = request.raidTier;
    const raidTier = rawTier === "open" || rawTier === "all"
      ? rawTier
      : typeof rawTier === "number" && Number.isInteger(rawTier) && rawTier >= 0 && rawTier <= 4
        ? Number(rawTier) : "all";
    const filters = {
      query: String(request.query ?? "").trim().slice(0, 160),
      zone: String(request.zone ?? "").trim().slice(0, 160),
      raidTier,
      scope,
      offset: Math.floor(clamp(Number(request.offset) || 0, 0, 1_000_000)),
      limit: Math.floor(clamp(Number(request.limit) || 100, 1, 250)),
    };
    if (!this.child?.stdin.writable) {
      return Promise.resolve({ rows: [], total: 0, offset: filters.offset, hasMore: false });
    }
    const requestId = `loot-${process.pid}-${++this.journalRequestSequence}`;
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        this.journalRequests.delete(requestId);
        resolve({ rows: [], total: 0, offset: filters.offset, hasMore: false });
      }, 5_000);
      this.journalRequests.set(requestId, { resolve, timer });
      this.send({ type: "engine.query-loot", requestId, filters });
    });
  }

  cacheItem(value: unknown): void {
    if (!value || typeof value !== "object") return;
    this.send({ type: "engine.cache-item", item: value });
  }

  getState(): { health: EngineHealth; snapshot: unknown; settings: DesktopSettings; gearPlan: GearPlanView } {
    return { health: this.health, snapshot: this.snapshot, settings: this.settings, gearPlan: this.gearPlan };
  }

  setLogPath(logPath: string): void {
    const detectedEqRoot = resolveEqRoot(logPath);
    this.settings = {
      ...this.settings,
      logPath,
      ...(detectedEqRoot && !this.settings.eqRoot ? { eqRoot: detectedEqRoot } : {}),
    };
    saveSettings(this.settings);
    const parserLogPath = newestEqLog(logPath);
    this.send({ type: "engine.set-log-path", logPath: parserLogPath || logPath });
    this.publishHealth({
      ...this.health,
      state: "searching",
      detail: parserLogPath
        ? `Found ${path.basename(parserLogPath)}; attaching the parser`
        : logPath ? "No eqlog_*.txt found in the selected folder or its Logs folder"
          : "Searching common EverQuest Legends log locations",
      configuredPath: logPath,
      activeLogPath: "",
    });
    if (this.attachmentRetryTimer) clearTimeout(this.attachmentRetryTimer);
    if (parserLogPath) {
      this.attachmentRetryTimer = setTimeout(() => {
        if (this.health.state === "live") return;
        this.send({ type: "engine.set-log-path", logPath: parserLogPath });
        this.publishHealth({
          ...this.health,
          state: "searching",
          detail: `Log found; retrying ${path.basename(parserLogPath)}`,
          configuredPath: this.settings.logPath,
          activeLogPath: parserLogPath,
        });
      }, 2500);
    }
  }

  setRaidDifficulty(raidDifficulty: number | null): void {
    this.settings = { ...this.settings, raidDifficulty };
    saveSettings(this.settings);
    this.send({ type: "engine.set-raid-difficulty", raidDifficulty });
  }

  updateDesktopSettings(patch: Partial<Pick<DesktopSettings, "uiTheme" | "alwaysOnTop" | "fontScale" | "composition" | "splitCharmedPetDps" | "seedMeterVisible" | "seedMeterMode" | "seedMeterOpacity" | "stanceAdvisorEnabled" | "itemNetworkLookups" | "eqRoot" | "autoCheckUpdates">> & {
    alerts?: Partial<AlertSettings>;
  }): DesktopSettings {
    const nextAlerts = patch.alerts ? {
      ...this.settings.alerts,
      ...patch.alerts,
      soundProfiles: patch.alerts.soundProfiles
        ? normalizeSoundProfiles(patch.alerts.soundProfiles)
        : this.settings.alerts.soundProfiles,
    } : this.settings.alerts;
    const previousScale = this.settings.fontScale;
    const nextScale = typeof patch.fontScale === "number" ? clamp(patch.fontScale, 0.9, 1.6) : previousScale;
    const nextSeedPosition = nextScale !== previousScale
      ? scaledSeedPosition(this.settings.seedPosition, previousScale, nextScale)
      : this.settings.seedPosition;
    this.settings = {
      ...this.settings,
      ...(patch.uiTheme === "vellum" || patch.uiTheme === "glass" ? { uiTheme: patch.uiTheme } : {}),
      ...(typeof patch.alwaysOnTop === "boolean" ? { alwaysOnTop: patch.alwaysOnTop } : {}),
      fontScale: nextScale,
      seedPosition: nextSeedPosition,
      ...(typeof patch.composition === "string" ? { composition: patch.composition.trim().slice(0, 48) } : {}),
      ...(typeof patch.splitCharmedPetDps === "boolean" ? { splitCharmedPetDps: patch.splitCharmedPetDps } : {}),
      ...(typeof patch.seedMeterVisible === "boolean" ? { seedMeterVisible: patch.seedMeterVisible } : {}),
      ...(["self", "group", "pet", "all"].includes(String(patch.seedMeterMode))
        ? { seedMeterMode: patch.seedMeterMode as DesktopSettings["seedMeterMode"] }
        : {}),
      ...(typeof patch.seedMeterOpacity === "number" && Number.isFinite(patch.seedMeterOpacity)
        ? { seedMeterOpacity: Math.round(clamp(patch.seedMeterOpacity, 0.35, 1) * 20) / 20 }
        : {}),
      ...(typeof patch.stanceAdvisorEnabled === "boolean" ? { stanceAdvisorEnabled: patch.stanceAdvisorEnabled } : {}),
      ...(typeof patch.itemNetworkLookups === "boolean" ? { itemNetworkLookups: patch.itemNetworkLookups } : {}),
      ...(typeof patch.autoCheckUpdates === "boolean" ? { autoCheckUpdates: patch.autoCheckUpdates } : {}),
      ...(typeof patch.eqRoot === "string" ? { eqRoot: resolveEqRoot(patch.eqRoot) } : {}),
      alerts: nextAlerts,
    };
    saveSettings(this.settings);
    this.send({ type: "engine.set-alert-config", alertConfig: this.settings.alerts });
    if (typeof patch.composition === "string") {
      this.send({ type: "engine.set-composition", composition: this.settings.composition });
    }
    applyDisplayScale(this.settings.fontScale);
    applyAlwaysOnTop(this.settings.alwaysOnTop);
    positionAlertWindow();
    mainWindow?.webContents.send("settings:changed", this.settings);
    alertWindow?.webContents.send("settings:changed", this.settings);
    controlWindow?.webContents.send("settings:changed", this.settings);
    syncControlWindow();
    return this.settings;
  }

  saveSeedPosition(position: { x: number; y: number }): void {
    this.settings = { ...this.settings, seedPosition: position };
    saveSettings(this.settings);
  }

  setRaidCompletion(target: string, difficulty: number, completed: boolean): void {
    this.send({ type: "engine.set-raid-completion", target, difficulty, completed });
  }

  private catalogCachePath(): string {
    return path.join(app.getPath("userData"), "eq-legends-tools-gear-cache.json");
  }

  private readCatalogCache(): void {
    try {
      const value = JSON.parse(readFileSync(this.catalogCachePath(), "utf8")) as {
        updatedAt?: unknown; records?: unknown;
      };
      if (!Array.isArray(value.records)) return;
      this.gearCatalog = value.records as GearCatalogRecord[];
      this.catalogUpdatedAt = typeof value.updatedAt === "string" ? value.updatedAt : "";
    } catch {
      this.gearCatalog = [];
      this.catalogUpdatedAt = "";
    }
  }

  private saveCatalogCache(): void {
    const target = this.catalogCachePath();
    const temporary = `${target}.tmp`;
    writeFileSync(temporary, JSON.stringify({
      schemaVersion: 1,
      source: "https://eqlegendstools.com/",
      credit: "EQ Legends Tools by FlammHammer",
      updatedAt: this.catalogUpdatedAt,
      records: this.gearCatalog,
    }), "utf8");
    renameSync(temporary, target);
  }

  private rebuildGearPlan(): void {
    const { bisBuildPath, inventoryPath } = this.settings;
    if (!bisBuildPath) {
      this.gearPlan = emptyGearPlan(bisBuildPath, inventoryPath);
      this.publishGearPlan();
      return;
    }
    try {
      const buildText = readFileSync(bisBuildPath, "utf8");
      if (buildText.length > 5_000_000) throw new Error("Character build is larger than 5 MB");
      const build = parseEqToolsBuild(buildText);
      let inventory: InventoryEntry[] = [];
      if (inventoryPath && existsSync(inventoryPath)) {
        const inventoryText = readFileSync(inventoryPath, "utf8");
        if (inventoryText.length > 20_000_000) throw new Error("Inventory file is larger than 20 MB");
        inventory = parseInventory(inventoryText);
      }
      this.gearPlan = buildGearPlan(build, inventory, this.gearCatalog, {
        buildPath: bisBuildPath,
        inventoryPath,
        catalogUpdatedAt: this.catalogUpdatedAt,
      });
    } catch (error) {
      this.gearPlan = {
        ...emptyGearPlan(bisBuildPath, inventoryPath),
        status: "error",
        detail: error instanceof Error ? error.message : String(error),
      };
    }
    this.publishGearPlan();
  }

  private publishGearPlan(): void {
    mainWindow?.webContents.send("gear:state", this.gearPlan);
  }

  async setBisBuildPath(bisBuildPath: string): Promise<void> {
    this.settings = { ...this.settings, bisBuildPath };
    saveSettings(this.settings);
    await this.refreshGearCatalog();
  }

  setInventoryPath(inventoryPath: string): void {
    this.settings = { ...this.settings, inventoryPath };
    saveSettings(this.settings);
    this.rebuildGearPlan();
  }

  async refreshGearCatalog(): Promise<boolean> {
    try {
      const response = await fetch("https://eqlegendstools.com/api/char-sheet-data", {
        headers: {
          Accept: "application/json",
          Referer: "https://eqlegendstools.com/char-sheet/",
          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Loremaster/0.1",
        },
      });
      if (!response.ok) throw new Error(`EQ Legends Tools returned HTTP ${response.status}`);
      const text = await response.text();
      if (text.length > 40_000_000) throw new Error("EQ Legends Tools catalog exceeded the safety limit");
      this.gearCatalog = catalogFromEqToolsData(JSON.parse(text));
      this.catalogUpdatedAt = new Date().toISOString();
      this.saveCatalogCache();
      this.rebuildGearPlan();
      return true;
    } catch (error) {
      console.error("Could not refresh EQ Legends Tools gear data", error);
      this.rebuildGearPlan();
      return false;
    }
  }

  reset(): void {
    this.send({ type: "engine.reset" });
  }

  stop(): void {
    this.stopping = true;
    if (this.restartTimer) clearTimeout(this.restartTimer);
    if (this.attachmentRetryTimer) clearTimeout(this.attachmentRetryTimer);
    this.settleJournalRequests();
    this.send({ type: "engine.shutdown" });
    const child = this.child;
    if (child) setTimeout(() => {
      if (!child.killed) child.kill();
    }, 900);
  }
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.max(minimum, Math.min(maximum, value));
}

function scaledSize(size: { width: number; height: number }, scale?: number) {
  const factor = scale ?? engine?.getState().settings.fontScale ?? defaultSettings.fontScale;
  return {
    width: Math.round(size.width * factor),
    height: Math.round(size.height * factor),
  };
}

function scaledSeedPosition(
  position: { x: number; y: number } | null,
  previousScale: number,
  nextScale: number,
): { x: number; y: number } | null {
  if (!position || previousScale === nextScale) return position;
  const previous = scaledSize(SEED_SIZE, previousScale);
  const next = scaledSize(SEED_SIZE, nextScale);
  const display = screen.getDisplayNearestPoint({
    x: position.x + Math.round(previous.width / 2),
    y: position.y + Math.round(previous.height / 2),
  });
  const workArea = display.workArea;
  const anchorRight = position.x + previous.width / 2 > workArea.x + workArea.width / 2;
  const anchorBottom = position.y + previous.height / 2 > workArea.y + workArea.height / 2;
  const x = anchorRight ? position.x + previous.width - next.width : position.x;
  const y = anchorBottom ? position.y + previous.height - next.height : position.y;
  return {
    x: clamp(x, workArea.x, workArea.x + workArea.width - next.width),
    y: clamp(y, workArea.y, workArea.y + workArea.height - next.height),
  };
}

function visibleSeedControls(value: unknown, settings: DesktopSettings): Record<string, unknown>[] {
  if (!value || typeof value !== "object") return [];
  const snapshot = (value as { snapshot?: unknown }).snapshot;
  if (!snapshot || typeof snapshot !== "object") return [];
  const controls = (snapshot as { controls?: unknown }).controls;
  if (!Array.isArray(controls)) return [];
  return controls.filter((candidate): candidate is Record<string, unknown> => {
    if (!candidate || typeof candidate !== "object") return false;
    const control = candidate as Record<string, unknown>;
    if (control.state !== "active") return false;
    if (control.kind === "mez") return settings.alerts.mezTimersEnabled;
    if (control.kind === "lull") return settings.alerts.lullTimersEnabled;
    return false;
  });
}

function visibleSeedMeterRows(value: unknown, settings: DesktopSettings): Record<string, unknown>[] {
  if (!settings.seedMeterVisible || !value || typeof value !== "object") return [];
  const snapshot = (value as { snapshot?: unknown }).snapshot;
  if (!snapshot || typeof snapshot !== "object") return [];
  const encounters = (snapshot as { encounters?: unknown }).encounters;
  if (!Array.isArray(encounters) || encounters.length === 0) return [];
  const latest = encounters[encounters.length - 1];
  if (!latest || typeof latest !== "object") return [];
  const encounter = latest as Record<string, unknown>;
  const actors = Array.isArray(encounter.actors)
    ? encounter.actors.filter((candidate): candidate is Record<string, unknown> => Boolean(candidate) && typeof candidate === "object")
    : [];
  const group = actors.filter((candidate) => (
    Boolean(candidate) && typeof candidate === "object" &&
    candidate.role === "group" && Number(candidate.encounterDamage) > 0
  ));
  const pets = actors.filter((candidate) => (
    (candidate.role === "charmed" || candidate.role === "summoned") &&
    Number(candidate.encounterDamage) > 0
  ));
  const selfActorDamage = Number(actors.find((actor) => actor.role === "self")?.encounterDamage) || 0;
  const personalDamage = Math.max(0, Number(encounter.personalDamage) || selfActorDamage);
  const charmedDamage = Math.max(0, Number(encounter.charmedPetDamage) || 0);
  const summonedDamage = Math.max(0, Number(encounter.summonedPetDamage) || 0);
  const combinedDamage = Math.max(0, Number(encounter.damage) || personalDamage + charmedDamage + summonedDamage);
  const synthetic = (role: string, damage: number): Record<string, unknown> => ({
    role, encounterDamage: damage, name: role,
  });
  let rows: Record<string, unknown>[];
  if (settings.seedMeterMode === "self") {
    const sources = Array.isArray(encounter.sources)
      ? encounter.sources.filter((candidate): candidate is Record<string, unknown> => {
        if (!candidate || typeof candidate !== "object" || Number((candidate as Record<string, unknown>).total) <= 0) return false;
        const category = String((candidate as Record<string, unknown>).category ?? "")
          .toLocaleLowerCase().replace(/[\s-]+/g, "_");
        return !["pet", "charmed", "summoned"].includes(category);
      })
      : [];
    rows = sources.length > 0 ? sources : (personalDamage > 0 ? [synthetic("self", personalDamage)] : []);
  } else if (settings.seedMeterMode === "pet") {
    rows = [...pets];
    if (!pets.some((actor) => actor.role === "charmed") && charmedDamage > 0) rows.push(synthetic("charmed", charmedDamage));
    if (!pets.some((actor) => actor.role === "summoned") && summonedDamage > 0) rows.push(synthetic("summoned", summonedDamage));
  } else if (settings.seedMeterMode === "group") {
    rows = [...(combinedDamage > 0 ? [synthetic("self-and-pets", combinedDamage)] : []), ...group];
  } else {
    rows = [...(personalDamage > 0 ? [synthetic("self", personalDamage)] : []), ...pets, ...group];
    if (!pets.some((actor) => actor.role === "charmed") && charmedDamage > 0) rows.push(synthetic("charmed", charmedDamage));
    if (!pets.some((actor) => actor.role === "summoned") && summonedDamage > 0) rows.push(synthetic("summoned", summonedDamage));
  }
  rows.sort((left, right) => (
    Number(right.encounterDamage) - Number(left.encounterDamage)
    || Number(right.total) - Number(left.total)
  ));
  return rows;
}

function applyDisplayScale(scale: number): void {
  const factor = clamp(scale, 0.9, 1.6);
  mainWindow?.webContents.setZoomFactor(factor);
  alertWindow?.webContents.setZoomFactor(factor);
  controlWindow?.webContents.setZoomFactor(factor);
  if (mainWindow) {
    if (windowAnalysis) setAnalysisMode(true, true);
    else setWindowMode(windowExpanded, true);
  }
  syncControlWindow();
}

function overlayWindows(): BrowserWindow[] {
  return [mainWindow, alertWindow, controlWindow].filter(
    (window): window is BrowserWindow => Boolean(window && !window.isDestroyed()),
  );
}

function trayIcon(): Electron.NativeImage | null {
  const candidates = [
    path.join(app.getAppPath(), "dist", "loremaster.ico"),
    path.join(app.getAppPath(), "dist", "loremaster-cog.png"),
    path.resolve(app.getAppPath(), "..", "loremaster", "assets", "loremaster.ico"),
    path.resolve(app.getAppPath(), "..", "loremaster", "assets", "loremaster-cog.png"),
  ];
  for (const candidate of candidates) {
    if (!existsSync(candidate)) continue;
    const image = nativeImage.createFromPath(candidate);
    if (!image.isEmpty()) return image;
  }
  return null;
}

function restoreLoremasterWindow(): void {
  const window = mainWindow;
  if (!window || window.isDestroyed()) {
    createWindow();
    return;
  }
  if (window.isMinimized()) window.restore();
  window.setSkipTaskbar(Boolean(tray));
  window.show();
  window.focus();
  window.moveTop();
  alertWindow?.showInactive();
  positionAlertWindow();
  syncControlWindow();
  applyAlwaysOnTop(engine?.getState().settings.alwaysOnTop ?? defaultSettings.alwaysOnTop);
}

function quitLoremaster(): void {
  app.quit();
}

function ensureTray(): boolean {
  if (tray && !tray.isDestroyed()) return true;
  const icon = trayIcon();
  if (!icon) {
    console.error("Could not create the Loremaster tray icon; using taskbar minimize");
    return false;
  }
  try {
    tray = new Tray(icon);
    tray.setToolTip("Loremaster — click to restore");
    tray.setContextMenu(Menu.buildFromTemplate([
      { label: "Show Loremaster", click: restoreLoremasterWindow },
      { type: "separator" },
      { label: "Quit Loremaster", click: quitLoremaster },
    ]));
    tray.on("click", restoreLoremasterWindow);
    tray.on("double-click", restoreLoremasterWindow);
    return true;
  } catch (error) {
    tray = null;
    console.error("Could not initialize the Loremaster tray icon", error);
    return false;
  }
}

function minimizeLoremasterWindow(): void {
  const window = mainWindow;
  if (!window || window.isDestroyed()) return;
  if (ensureTray()) {
    alertWindow?.hide();
    controlWindow?.hide();
    window.hide();
    if (process.platform === "win32" && !trayMinimizeNoticeShown) {
      trayMinimizeNoticeShown = true;
      try {
        tray?.displayBalloon({
          title: "Loremaster is still running",
          content: "Click the tray icon to restore it, or right-click and choose Quit Loremaster.",
          iconType: "info",
          noSound: true,
        });
      } catch {
        // Some Windows notification policies suppress tray balloons.
      }
    }
    return;
  }
  // A taskbar button is the recovery path if Windows refuses tray creation.
  window.setSkipTaskbar(false);
  window.minimize();
}

function reinforceOverlayZOrder(enabled: boolean): void {
  const level = process.platform === "win32" ? "screen-saver" : "floating";
  for (const window of overlayWindows()) {
    try {
      window.setAlwaysOnTop(enabled, level, enabled ? 1 : 0);
      if (enabled && window.isVisible()) window.moveTop();
    } catch (error) {
      console.warn("Could not reinforce Loremaster overlay z-order", error);
    }
  }
}

function clearTopmostReassertions(): void {
  for (const timer of topmostReassertTimers) clearTimeout(timer);
  topmostReassertTimers = [];
}

function scheduleTopmostReassertion(): void {
  clearTopmostReassertions();
  if (!(engine?.getState().settings.alwaysOnTop ?? defaultSettings.alwaysOnTop)) return;
  for (const delay of [60, 240]) {
    const timer = setTimeout(() => {
      topmostReassertTimers = topmostReassertTimers.filter((candidate) => candidate !== timer);
      reinforceOverlayZOrder(true);
    }, delay);
    timer.unref();
    topmostReassertTimers.push(timer);
  }
}

function applyAlwaysOnTop(enabled: boolean): void {
  clearTopmostReassertions();
  reinforceOverlayZOrder(enabled);
  if (enabled) scheduleTopmostReassertion();
}

function startTopmostHeartbeat(): void {
  if (topmostHeartbeatTimer) return;
  topmostHeartbeatTimer = setInterval(() => {
    if (engine?.getState().settings.alwaysOnTop ?? defaultSettings.alwaysOnTop) {
      reinforceOverlayZOrder(true);
    }
  }, 2_000);
  topmostHeartbeatTimer.unref();
}

function syncControlWindow(): void {
  if (!mainWindow || !controlWindow || controlWindow.isDestroyed()) return;
  if (process.env.LOREMASTER_SCREENSHOT_VIEW === "controls") return;
  const settings = engine?.getState().settings ?? defaultSettings;
  const controls = visibleSeedControls(engine?.getState().snapshot, settings);
  const meterRows = visibleSeedMeterRows(engine?.getState().snapshot, settings);
  if (!mainWindow.isVisible() || windowExpanded || (controls.length === 0 && meterRows.length === 0)) {
    controlWindow.hide();
    positionAlertWindow();
    return;
  }

  const anchor = mainWindow.getBounds();
  const workArea = screen.getDisplayMatching(anchor).workArea;
  const gap = Math.max(5, Math.round(6 * settings.fontScale));
  const layout = boundedCompanionLayout(
    meterRows.length,
    controls.length,
    settings.fontScale,
    Math.max(1, workArea.height - gap * 2),
  );
  const panelSize = layout.panelSize;
  const spaceRight = workArea.x + workArea.width - (anchor.x + anchor.width);
  const spaceLeft = anchor.x - workArea.x;
  const spaceAbove = anchor.y - workArea.y;
  const spaceBelow = workArea.y + workArea.height - (anchor.y + anchor.height);

  let x: number;
  let y: number;
  if (spaceAbove >= panelSize.height + gap) {
    x = anchor.x + Math.round((anchor.width - panelSize.width) / 2);
    y = anchor.y - panelSize.height - gap;
  } else if (spaceRight >= panelSize.width + gap) {
    x = anchor.x + anchor.width + gap;
    y = anchor.y + Math.round((anchor.height - panelSize.height) / 2);
  } else if (spaceLeft >= panelSize.width + gap) {
    x = anchor.x - panelSize.width - gap;
    y = anchor.y + Math.round((anchor.height - panelSize.height) / 2);
  } else {
    x = anchor.x + Math.round((anchor.width - panelSize.width) / 2);
    y = anchor.y + anchor.height + gap;
  }
  x = clamp(x, workArea.x, Math.max(workArea.x, workArea.x + workArea.width - panelSize.width));
  y = clamp(y, workArea.y, Math.max(workArea.y, workArea.y + workArea.height - panelSize.height));
  controlWindow.setBounds({ x, y, ...panelSize }, false);
  if (!controlWindow.webContents.isLoadingMainFrame()) {
    controlWindow.webContents.send("window:companion-layout", {
      meterRows: layout.meterRows,
      controlRows: layout.controlRows,
    });
    controlWindow.showInactive();
  }
  positionAlertWindow();
}

function canonicalSeedAnchor(settings: DesktopSettings, fallback: Electron.Rectangle) {
  const size = scaledSize(SEED_SIZE, settings.fontScale);
  const saved = settings.seedPosition ?? { x: fallback.x, y: fallback.y };
  const display = screen.getDisplayNearestPoint({
    x: saved.x + Math.round(size.width / 2),
    y: saved.y + Math.round(size.height / 2),
  });
  const workArea = display.workArea;
  return {
    workArea,
    bounds: {
      x: clamp(saved.x, workArea.x, workArea.x + workArea.width - size.width),
      y: clamp(saved.y, workArea.y, workArea.y + workArea.height - size.height),
      ...size,
    },
  };
}

function positionAlertWindow(): void {
  if (!mainWindow || !alertWindow || alertWindow.isDestroyed()) return;
  const settings = engine?.getState().settings ?? defaultSettings;
  const alertSize = scaledSize(ALERT_SIZE, settings.fontScale);
  const anchorBounds = mainWindow.getBounds();
  const companionBounds = controlWindow?.isVisible() ? controlWindow.getBounds() : null;
  const companionAbove = Boolean(
    companionBounds && companionBounds.y + companionBounds.height <= anchorBounds.y);
  const workArea = screen.getDisplayMatching(anchorBounds).workArea;
  const gap = 10;
  let anchor = settings.alerts.alertAnchor;
  if (anchor === "auto") {
    const aboveEdge = companionAbove && companionBounds
      ? companionBounds.y
      : anchorBounds.y;
    const above = aboveEdge - workArea.y;
    const below = workArea.y + workArea.height - (anchorBounds.y + anchorBounds.height);
    const right = workArea.x + workArea.width - (anchorBounds.x + anchorBounds.width);
    anchor = above >= alertSize.height + gap && above >= below
      ? "above"
      : right >= alertSize.width + gap ? "right"
        : below >= alertSize.height + gap ? "below" : "left";
  }
  let x = anchorBounds.x + Math.round((anchorBounds.width - alertSize.width) / 2);
  let y = (companionAbove && companionBounds ? companionBounds.y : anchorBounds.y)
    - alertSize.height - gap;
  if (anchor === "below") y = anchorBounds.y + anchorBounds.height + gap;
  if (anchor === "left") {
    x = anchorBounds.x - alertSize.width - gap;
    y = anchorBounds.y + Math.round((anchorBounds.height - alertSize.height) / 2);
  }
  if (anchor === "right") {
    x = anchorBounds.x + anchorBounds.width + gap;
    y = anchorBounds.y + Math.round((anchorBounds.height - alertSize.height) / 2);
  }
  x = clamp(x, workArea.x, workArea.x + workArea.width - alertSize.width);
  y = clamp(y, workArea.y, workArea.y + workArea.height - alertSize.height);
  alertWindow.setBounds({ x, y, ...alertSize }, false);
}

function setWindowMode(expanded: boolean, preserveAnchor = false): void {
  if (!mainWindow) return;
  const current = mainWindow.getBounds();
  const settings = engine?.getState().settings ?? defaultSettings;
  const anchor = canonicalSeedAnchor(settings, current);
  const workArea = anchor.workArea;
  const seedBounds = anchor.bounds;
  const expandedSize = scaledSize(EXPANDED_SIZE, settings.fontScale);
  movingWindowProgrammatically = true;
  windowExpanded = expanded;
  windowAnalysis = false;
  if (expanded) {
    const spaceBelow = workArea.y + workArea.height - (seedBounds.y + seedBounds.height);
    const spaceAbove = seedBounds.y - workArea.y;
    if (!preserveAnchor) {
      expansionDirection = spaceBelow < expandedSize.height - seedBounds.height && spaceAbove > spaceBelow
        ? "up"
        : "down";
    }
    const y = expansionDirection === "up"
      ? seedBounds.y + seedBounds.height - expandedSize.height
      : seedBounds.y;
    const target = {
      x: clamp(seedBounds.x, workArea.x, workArea.x + workArea.width - expandedSize.width),
      y: clamp(y, workArea.y, workArea.y + workArea.height - expandedSize.height),
      ...expandedSize,
    };
    mainWindow.setMinimumSize(Math.round(390 * settings.fontScale), Math.round(500 * settings.fontScale));
    mainWindow.setBounds(target, false);
  } else {
    const target = seedBounds;
    mainWindow.setMinimumSize(118, 64);
    mainWindow.setBounds(target, false);
    engine?.saveSeedPosition({ x: target.x, y: target.y });
  }
  positionAlertWindow();
  syncControlWindow();
  applyAlwaysOnTop(settings.alwaysOnTop);
  setImmediate(() => { movingWindowProgrammatically = false; });
}

function setAnalysisMode(active: boolean, preserveAnchor = false): void {
  if (!active) {
    setWindowMode(true, preserveAnchor);
    return;
  }
  if (!mainWindow) return;
  const current = mainWindow.getBounds();
  const settings = engine?.getState().settings ?? defaultSettings;
  const anchor = canonicalSeedAnchor(settings, current);
  const workArea = anchor.workArea;
  const seedBounds = anchor.bounds;
  const requested = scaledSize(ANALYSIS_SIZE, Math.min(settings.fontScale, 1.35));
  const availableWidth = Math.max(480, workArea.width - 16);
  const availableHeight = Math.max(440, workArea.height - 16);
  const targetSize = {
    width: Math.min(Math.max(780, requested.width), availableWidth),
    height: Math.min(Math.max(620, requested.height), availableHeight),
  };
  const spaceBelow = workArea.y + workArea.height - (seedBounds.y + seedBounds.height);
  const spaceAbove = seedBounds.y - workArea.y;
  if (!preserveAnchor) {
    expansionDirection = spaceBelow < targetSize.height - seedBounds.height && spaceAbove > spaceBelow
      ? "up"
      : "down";
  }
  const y = expansionDirection === "up"
    ? seedBounds.y + seedBounds.height - targetSize.height
    : seedBounds.y;
  const target = {
    x: clamp(seedBounds.x, workArea.x, workArea.x + workArea.width - targetSize.width),
    y: clamp(y, workArea.y, workArea.y + workArea.height - targetSize.height),
    ...targetSize,
  };
  movingWindowProgrammatically = true;
  windowExpanded = true;
  windowAnalysis = true;
  mainWindow.setMinimumSize(
    Math.min(Math.round(760 * settings.fontScale), target.width),
    Math.min(Math.round(560 * settings.fontScale), target.height),
  );
  mainWindow.setBounds(target, false);
  positionAlertWindow();
  syncControlWindow();
  applyAlwaysOnTop(settings.alwaysOnTop);
  setImmediate(() => { movingWindowProgrammatically = false; });
}

function rendererUrl(base: string, query: Record<string, string>): string {
  const url = new URL(base);
  for (const [key, value] of Object.entries(query)) url.searchParams.set(key, value);
  return url.toString();
}

function createAlertWindow(): void {
  const settings = engine?.getState().settings ?? defaultSettings;
  const alertSize = scaledSize(ALERT_SIZE, settings.fontScale);
  alertWindow = new BrowserWindow({
    ...alertSize,
    frame: false,
    transparent: true,
    show: false,
    focusable: false,
    skipTaskbar: true,
    resizable: false,
    alwaysOnTop: settings.alwaysOnTop,
    backgroundColor: "#00000000",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      backgroundThrottling: false,
    },
  });
  alertWindow.setIgnoreMouseEvents(true);
  applyAlwaysOnTop(settings.alwaysOnTop);
  const developmentUrl = process.env.VITE_DEV_SERVER_URL;
  const rendererQuery = { alert: "1", theme: settings.uiTheme };
  const rendererReady = developmentUrl
    ? alertWindow.loadURL(rendererUrl(developmentUrl, rendererQuery))
    : alertWindow.loadFile(path.join(app.getAppPath(), "dist", "index.html"), { query: rendererQuery });
  void rendererReady.then(() => {
    alertWindow?.webContents.setZoomFactor(settings.fontScale);
    positionAlertWindow();
    alertWindow?.showInactive();
    scheduleTopmostReassertion();
    if (process.env.LOREMASTER_SCREENSHOT_VIEW === "alert" && process.env.LOREMASTER_SCREENSHOT_PATH) {
      alertWindow?.webContents.send("alerts:test", {
        id: "visual-test", severity: "info", title: "TELL · AROMEK",
        target: "Sometimes it feels like maybe I should wait for the next pull before changing stance.",
      });
      setTimeout(() => {
        void alertWindow?.webContents.capturePage().then((image) => {
          if (image && process.env.LOREMASTER_SCREENSHOT_PATH) {
            writeFileSync(process.env.LOREMASTER_SCREENSHOT_PATH, image.toPNG());
          }
          app.quit();
        });
      }, 300);
    }
  });
  alertWindow.on("closed", () => { alertWindow = null; });
}

function createControlWindow(): void {
  const settings = engine?.getState().settings ?? defaultSettings;
  const initialSize = companionSurfaceSize(1, 0, settings.fontScale);
  controlWindow = new BrowserWindow({
    ...initialSize,
    frame: false,
    transparent: true,
    show: false,
    focusable: false,
    skipTaskbar: true,
    resizable: false,
    alwaysOnTop: settings.alwaysOnTop,
    backgroundColor: "#00000000",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      backgroundThrottling: false,
    },
  });
  controlWindow.setIgnoreMouseEvents(true);
  applyAlwaysOnTop(settings.alwaysOnTop);
  const developmentUrl = process.env.VITE_DEV_SERVER_URL;
  const rendererQuery = { controls: "1", theme: settings.uiTheme };
  const rendererReady = developmentUrl
    ? controlWindow.loadURL(rendererUrl(developmentUrl, rendererQuery))
    : controlWindow.loadFile(path.join(app.getAppPath(), "dist", "index.html"), { query: rendererQuery });
  void rendererReady.then(() => {
    controlWindow?.webContents.setZoomFactor(settings.fontScale);
    syncControlWindow();
    scheduleTopmostReassertion();
    if (process.env.LOREMASTER_SCREENSHOT_VIEW === "controls" && process.env.LOREMASTER_SCREENSHOT_PATH) {
      const fixtureEvent = {
        protocolVersion: 1,
        sequence: 4,
        occurredAt: new Date().toISOString(),
        eventType: "engine.snapshot",
        snapshot: {
          protocolVersion: 1,
          sequence: 4,
          observedAt: new Date().toISOString(),
          character: { name: "Spin", level: 50, composition: "PAL/MNK/ENC", zone: "The Plane of Sky" },
          combat: { fightDps: 328 },
          groupMembers: ["Aromek", "Verdume", "Lilith"],
          encounters: [{
            active: true,
            seconds: 117,
            damage: 59900,
            personalDamage: 34400,
            charmedPetDamage: 25500,
            summonedPetDamage: 0,
            sources: [
              { name: "Melee", category: "melee", total: 18100, hits: 34, maximum: 690 },
              { name: "Flame of Light", category: "spell", total: 9700, hits: 9, maximum: 1520 },
              { name: "Kick", category: "melee", total: 6600, hits: 18, maximum: 410 },
            ],
            actors: [
              { name: "Spin", role: "self", encounterDamage: 34400, encounterDps: 294 },
              { name: "an abhorrent", role: "charmed", encounterDamage: 25500, encounterDps: 218 },
              { name: "Aromek", role: "group", encounterDamage: 48210, encounterDps: 412 },
              { name: "Verdume", role: "group", encounterDamage: 35180, encounterDps: 301 },
              { name: "Lilith", role: "group", encounterDamage: 18490, encounterDps: 158 },
              { name: "Nearby", role: "observed", encounterDamage: 99999, encounterDps: 999 },
            ],
          }],
          controls: screenshotControls,
        },
      };
      const screenshotMeter = visibleSeedMeterRows(fixtureEvent, settings);
      const screenshotLayout = boundedCompanionLayout(
        screenshotMeter.length,
        screenshotControls.length,
        settings.fontScale,
        Number.POSITIVE_INFINITY,
      );
      controlWindow?.setBounds({ x: 80, y: 80, ...screenshotLayout.panelSize }, false);
      setTimeout(() => {
        controlWindow?.webContents.send("window:companion-layout", {
          meterRows: screenshotLayout.meterRows,
          controlRows: screenshotLayout.controlRows,
        });
        controlWindow?.webContents.send("engine:snapshot", fixtureEvent);
        controlWindow?.showInactive();
      }, 300);
      setTimeout(() => {
        void controlWindow?.webContents.capturePage().then((image) => {
          if (image && process.env.LOREMASTER_SCREENSHOT_PATH) {
            writeFileSync(process.env.LOREMASTER_SCREENSHOT_PATH, image.toPNG());
          }
          app.quit();
        });
      }, 800);
    }
  });
  controlWindow.on("closed", () => { controlWindow = null; });
}

function createWindow(): void {
  const settings = engine?.getState().settings ?? defaultSettings;
  const seedSize = scaledSize(SEED_SIZE, settings.fontScale);
  mainWindow = new BrowserWindow({
    ...seedSize,
    ...(settings.seedPosition ?? {}),
    minWidth: 118,
    minHeight: 64,
    maxWidth: 1800,
    maxHeight: 1300,
    resizable: true,
    frame: false,
    thickFrame: false,
    transparent: true,
    show: false,
    alwaysOnTop: settings.alwaysOnTop,
    skipTaskbar: true,
    backgroundColor: "#00000000",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      backgroundThrottling: false,
    },
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("https://")) void shell.openExternal(url);
    return { action: "deny" };
  });

  const developmentUrl = process.env.VITE_DEV_SERVER_URL;
  const rendererQuery = { theme: settings.uiTheme };
  const rendererReady = developmentUrl
    ? mainWindow.loadURL(rendererUrl(developmentUrl, rendererQuery))
    : mainWindow.loadFile(path.join(app.getAppPath(), "dist", "index.html"), { query: rendererQuery });
  void rendererReady.then(() => {
    rendererHealthyForUpdate = true;
    maybeAcknowledgePortableRelaunch();
    mainWindow?.webContents.on("will-navigate", (event) => event.preventDefault());
    mainWindow?.webContents.setZoomFactor(settings.fontScale);
    setWindowMode(false);
    mainWindow?.showInactive();
    createAlertWindow();
    createControlWindow();
    applyAlwaysOnTop(settings.alwaysOnTop);
    const topmostProbePath = process.env.LOREMASTER_TOPMOST_PROBE_PATH;
    if (topmostProbePath) {
      const rawProbeScale = Number(process.env.LOREMASTER_TOPMOST_PROBE_SCALE || 1.15);
      const requestedScale = clamp(Number.isFinite(rawProbeScale) ? rawProbeScale : 1.15, 0.9, 1.6);
      engine?.updateDesktopSettings({ alwaysOnTop: true, fontScale: requestedScale });
      setTimeout(() => {
        const report = {
          requestedScale,
          configuredScale: engine?.getState().settings.fontScale,
          windows: {
            main: Boolean(mainWindow?.isAlwaysOnTop()),
            alert: Boolean(alertWindow?.isAlwaysOnTop()),
            controls: Boolean(controlWindow?.isAlwaysOnTop()),
          },
          mainBounds: mainWindow?.getBounds(),
        };
        writeFileSync(topmostProbePath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
        app.quit();
      }, 500);
    }
    const positionProbePath = process.env.LOREMASTER_POSITION_PROBE_PATH;
    const positionProbeWindow = mainWindow;
    if (positionProbePath && positionProbeWindow) {
      setTimeout(() => {
        const initialSeed = positionProbeWindow.getBounds();
        setWindowMode(true);
        const hud = positionProbeWindow.getBounds();
        setAnalysisMode(true);
        const analysis = positionProbeWindow.getBounds();
        setAnalysisMode(false);
        const hudAfterAnalysis = positionProbeWindow.getBounds();
        setWindowMode(false);
        const finalSeed = positionProbeWindow.getBounds();
        writeFileSync(positionProbePath, `${JSON.stringify({
          scale: engine?.getState().settings.fontScale,
          initialSeed,
          hud,
          analysis,
          hudAfterAnalysis,
          finalSeed,
          savedSeedPosition: engine?.getState().settings.seedPosition,
        }, null, 2)}\n`, "utf8");
        app.quit();
      }, 350);
    }
    const trayProbePath = process.env.LOREMASTER_TRAY_PROBE_PATH;
    if (trayProbePath && mainWindow) {
      setTimeout(() => {
        minimizeLoremasterWindow();
        const minimized = {
          trayAvailable: Boolean(tray && !tray.isDestroyed()),
          mainVisible: Boolean(mainWindow?.isVisible()),
          alertVisible: Boolean(alertWindow?.isVisible()),
          controlsVisible: Boolean(controlWindow?.isVisible()),
        };
        restoreLoremasterWindow();
        const restored = {
          mainVisible: Boolean(mainWindow?.isVisible()),
          mainMinimized: Boolean(mainWindow?.isMinimized()),
        };
        writeFileSync(trayProbePath, `${JSON.stringify({ minimized, restored }, null, 2)}\n`, "utf8");
        quitLoremaster();
      }, 500);
    }
    const screenshotPath = process.env.LOREMASTER_SCREENSHOT_PATH;
    if (screenshotPath && mainWindow && !["alert", "controls"].includes(process.env.LOREMASTER_SCREENSHOT_VIEW ?? "")) {
      const screenshotView = process.env.LOREMASTER_SCREENSHOT_VIEW;
      void (screenshotView?.startsWith("seed")
        ? Promise.resolve()
        : mainWindow.webContents.executeJavaScript(
          "document.querySelector('.seed-action')?.click()",
        )).then(() => new Promise((resolve) => setTimeout(resolve, screenshotView?.startsWith("seed") ? 250 : 500)))
        .then(() => screenshotView === "seed-attack"
          ? mainWindow?.webContents.executeJavaScript(
            "document.querySelector('.rune-seed')?.classList.add('attacking')")
          : undefined)
        .then(() => screenshotView === "settings" || screenshotView === "updates" || screenshotView?.startsWith("sounds")
          ? mainWindow?.webContents.executeJavaScript(
            "document.querySelector('button[aria-label=\"Open settings\"]')?.click()")
          : screenshotView === "loot"
            ? mainWindow?.webContents.executeJavaScript(
              "document.querySelector('button[aria-label=\"Open observed loot chronicle\"]')?.click()")
          : screenshotView === "analysis"
            ? mainWindow?.webContents.executeJavaScript(
              "document.querySelector('button[aria-label=\"Open full combat breakdown\"]')?.click()")
          : screenshotView === "breakdown"
            ? mainWindow?.webContents.executeJavaScript(`
              document.querySelector('.encounter-nav button:not(:disabled)')?.click();
              const details = document.querySelector('.breakdown-card');
              if (details) { details.open = true; details.scrollIntoView({ block: 'start' }); }
            `)
          : undefined)
        .then(() => new Promise((resolve) => setTimeout(resolve, 250)))
        .then(() => screenshotView?.startsWith("sounds")
          ? mainWindow?.webContents.executeJavaScript(
            "document.querySelector('.sound-studio')?.scrollIntoView({ block: 'start' })")
          : screenshotView === "updates"
            ? mainWindow?.webContents.executeJavaScript(
              "document.querySelector('.update-center-card')?.scrollIntoView({ block: 'start' })")
          : undefined)
        .then(() => screenshotView === "sounds-menu"
          ? mainWindow?.webContents.executeJavaScript(
            "document.querySelector('.sound-preset-trigger')?.click()")
          : undefined)
        .then(() => new Promise((resolve) => setTimeout(resolve, screenshotView?.startsWith("sounds") ? 180 : 0)))
        .then(() => mainWindow?.webContents.capturePage())
        .then((image) => {
          if (image) writeFileSync(screenshotPath, image.toPNG());
          app.quit();
        }).catch((error: unknown) => {
          console.error("Loremaster screenshot capture failed", error);
          app.exit(1);
        });
    }
  }).catch((error: unknown) => {
    console.error("Loremaster renderer failed to load", error);
    app.exit(1);
  });
  mainWindow.on("closed", () => {
    alertWindow?.close();
    controlWindow?.close();
    mainWindow = null;
  });
  mainWindow.on("show", scheduleTopmostReassertion);
  mainWindow.on("restore", () => {
    if (tray) mainWindow?.setSkipTaskbar(true);
    scheduleTopmostReassertion();
  });
  mainWindow.on("focus", scheduleTopmostReassertion);
  mainWindow.on("blur", scheduleTopmostReassertion);
  mainWindow.on("resize", scheduleTopmostReassertion);
  mainWindow.on("move", () => {
    positionAlertWindow();
    syncControlWindow();
    if (!windowExpanded && !movingWindowProgrammatically && mainWindow) {
      const bounds = mainWindow.getBounds();
      engine?.saveSeedPosition({ x: bounds.x, y: bounds.y });
    }
  });
}

ipcMain.handle("runtime:metrics", () => ({
  version: app.getVersion(),
  coldStartMs: Math.round(performance.now() - processStartedAt),
  residentMemoryMb: Math.round(process.memoryUsage().rss / 1024 / 1024),
  platform: process.platform,
}));

ipcMain.handle("engine:get-state", () => engine?.getState() ?? {
  health: defaultHealth, snapshot: null,
  settings: defaultSettings,
  gearPlan: emptyGearPlan(),
});
ipcMain.handle("engine:choose-log-folder", async () => {
  if (!mainWindow) return null;
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "Choose your EverQuest Legends folder or Logs folder",
    properties: ["openDirectory"],
  });
  if (result.canceled || result.filePaths.length !== 1) return null;
  engine?.setLogPath(result.filePaths[0]);
  return result.filePaths[0];
});
ipcMain.handle("engine:set-log-path", (_event, value: unknown) => {
  if (typeof value !== "string" || value.length > 4096) return false;
  engine?.setLogPath(value);
  return true;
});
ipcMain.handle("engine:set-raid-difficulty", (_event, value: unknown) => {
  if (value !== null && (!Number.isInteger(value) || Number(value) < 0 || Number(value) > 4)) return false;
  engine?.setRaidDifficulty(value === null ? null : Number(value));
  return true;
});
ipcMain.handle("engine:set-raid-completion", (_event, target: unknown, difficulty: unknown, completed: unknown) => {
  if (typeof target !== "string" || target.length > 128 || !Number.isInteger(difficulty) || Number(difficulty) < 0 || Number(difficulty) > 4 || typeof completed !== "boolean") return false;
  engine?.setRaidCompletion(target, Number(difficulty), completed);
  return true;
});
ipcMain.on("engine:reset", () => engine?.reset());
ipcMain.on("alerts:test", () => {
  positionAlertWindow();
  alertWindow?.webContents.send("alerts:test", {
    id: `test-${Date.now()}`,
    severity: "info",
    title: "LOREMASTER ALERT READY",
    target: "Placement and sound are working",
  });
});

const CUSTOM_SOUND_EXTENSIONS = new Set([".wav", ".mp3", ".ogg", ".m4a"]);
const CUSTOM_SOUND_MAX_BYTES = 8 * 1024 * 1024;

function validatedCustomSoundPath(kind: AlertSoundKind): string | null {
  const profile = engine?.getState().settings.alerts.soundProfiles[kind];
  if (!profile?.customPath) return null;
  const candidate = path.resolve(profile.customPath);
  try {
    const stats = statSync(candidate);
    return stats.isFile()
      && stats.size > 0
      && stats.size <= CUSTOM_SOUND_MAX_BYTES
      && CUSTOM_SOUND_EXTENSIONS.has(path.extname(candidate).toLowerCase())
      ? candidate : null;
  } catch {
    return null;
  }
}

ipcMain.handle("alerts:choose-sound", async (_event, value: unknown) => {
  if (!mainWindow || !engine || !ALERT_SOUND_KINDS.includes(value as AlertSoundKind)) return null;
  const kind = value as AlertSoundKind;
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "Choose a Loremaster alert sound",
    properties: ["openFile"],
    filters: [{ name: "Audio", extensions: ["wav", "mp3", "ogg", "m4a"] }],
  });
  if (result.canceled || result.filePaths.length !== 1) return null;
  const candidate = path.resolve(result.filePaths[0]);
  try {
    const stats = statSync(candidate);
    if (!stats.isFile() || stats.size <= 0 || stats.size > CUSTOM_SOUND_MAX_BYTES
        || !CUSTOM_SOUND_EXTENSIONS.has(path.extname(candidate).toLowerCase())) return null;
  } catch {
    return null;
  }
  const profiles = normalizeSoundProfiles(engine.getState().settings.alerts.soundProfiles);
  profiles[kind] = { preset: "custom", customPath: candidate };
  return engine.updateDesktopSettings({ alerts: { soundProfiles: profiles } });
});

ipcMain.handle("alerts:read-sound", (_event, value: unknown) => {
  if (!ALERT_SOUND_KINDS.includes(value as AlertSoundKind)) return null;
  const candidate = validatedCustomSoundPath(value as AlertSoundKind);
  if (!candidate) return null;
  try {
    return {
      bytes: readFileSync(candidate),
      extension: path.extname(candidate).toLowerCase(),
      name: path.basename(candidate),
    };
  } catch {
    return null;
  }
});

ipcMain.handle("settings:update", (_event, value: unknown) => {
  if (!value || typeof value !== "object" || !engine) return null;
  const raw = value as Record<string, unknown>;
  const patch: Parameters<EngineSupervisor["updateDesktopSettings"]>[0] = {};
  if (raw.uiTheme === "vellum" || raw.uiTheme === "glass") patch.uiTheme = raw.uiTheme;
  if (typeof raw.alwaysOnTop === "boolean") patch.alwaysOnTop = raw.alwaysOnTop;
  if (Number.isFinite(Number(raw.fontScale))) patch.fontScale = clamp(Number(raw.fontScale), 0.9, 1.6);
  if (typeof raw.composition === "string") patch.composition = raw.composition.slice(0, 48);
  if (typeof raw.splitCharmedPetDps === "boolean") patch.splitCharmedPetDps = raw.splitCharmedPetDps;
  if (typeof raw.seedMeterVisible === "boolean") patch.seedMeterVisible = raw.seedMeterVisible;
  if (["self", "group", "pet", "all"].includes(String(raw.seedMeterMode))) {
    patch.seedMeterMode = raw.seedMeterMode as DesktopSettings["seedMeterMode"];
  }
  if (Number.isFinite(Number(raw.seedMeterOpacity))) {
    patch.seedMeterOpacity = clamp(Number(raw.seedMeterOpacity), 0.35, 1);
  }
  if (typeof raw.stanceAdvisorEnabled === "boolean") patch.stanceAdvisorEnabled = raw.stanceAdvisorEnabled;
  if (typeof raw.itemNetworkLookups === "boolean") patch.itemNetworkLookups = raw.itemNetworkLookups;
  if (typeof raw.autoCheckUpdates === "boolean") patch.autoCheckUpdates = raw.autoCheckUpdates;
  if (typeof raw.eqRoot === "string" && raw.eqRoot.length <= 4096) patch.eqRoot = raw.eqRoot;
  if (raw.alerts && typeof raw.alerts === "object") {
    const candidate = raw.alerts as Record<string, unknown>;
    const alerts: Partial<AlertSettings> = {};
    const booleanKeys: (keyof AlertSettings)[] = [
      "alertsEnabled", "alertSound", "alertCharmBreak", "alertTells", "alertSummon",
      "alertDeath", "alertBigHit", "alertNameCalled", "mezTimersEnabled", "mezTimerSound",
      "lullTimersEnabled", "lullTimerSound",
    ];
    for (const key of booleanKeys) if (typeof candidate[key] === "boolean") Object.assign(alerts, { [key]: candidate[key] });
    if (candidate.soundProfiles && typeof candidate.soundProfiles === "object") {
      alerts.soundProfiles = normalizeSoundProfiles(candidate.soundProfiles);
    }
    if (["auto", "above", "below", "left", "right"].includes(String(candidate.alertAnchor))) {
      alerts.alertAnchor = candidate.alertAnchor as AlertAnchor;
    }
    const numericRanges: Record<string, [number, number]> = {
      alertSeconds: [1, 15], bigHitThreshold: [1, 999999], mezWarningSeconds: [3, 30], lullWarningSeconds: [3, 30],
    };
    for (const [key, [low, high]] of Object.entries(numericRanges)) {
      const numeric = Number(candidate[key]);
      if (Number.isFinite(numeric)) Object.assign(alerts, { [key]: clamp(Math.round(numeric), low, high) });
    }
    patch.alerts = alerts;
  }
  return engine.updateDesktopSettings(patch);
});

ipcMain.handle("gear:choose-build", async () => {
  if (!mainWindow || !engine) return null;
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "Import EQ Legends Tools character build",
    properties: ["openFile"],
    filters: [{ name: "EQ Legends Tools character JSON", extensions: ["json"] }],
  });
  if (result.canceled || result.filePaths.length !== 1) return null;
  await engine.setBisBuildPath(result.filePaths[0]);
  return result.filePaths[0];
});
ipcMain.handle("gear:choose-inventory", async () => {
  if (!mainWindow || !engine) return null;
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "Import EverQuest /outputfile inventory",
    properties: ["openFile"],
    filters: [{ name: "EverQuest inventory", extensions: ["txt"] }],
  });
  if (result.canceled || result.filePaths.length !== 1) return null;
  engine.setInventoryPath(result.filePaths[0]);
  return result.filePaths[0];
});
ipcMain.handle("gear:refresh", () => engine?.refreshGearCatalog() ?? false);
ipcMain.handle("journal:query-loot", (_event, value: unknown) => (
  engine?.queryLoot(value) ?? Promise.resolve({
    rows: [], total: 0, offset: 0, hasMore: false,
  })
));
ipcMain.handle("items:lookup", async (_event, value: unknown) => {
  if (typeof value !== "string") {
    return {
      status: "not-found",
      requestedName: "",
      title: "",
      url: "https://eqlwiki.com/",
      stats: [],
      notes: [],
      sections: {},
      freshness: "live",
      detail: "Select a valid item name.",
    };
  }
  itemIntelligence ??= new ItemIntelligenceService(app.getPath("userData"));
  const result = await itemIntelligence.lookup(
    value, engine?.getState().settings.itemNetworkLookups ?? true);
  if (result.status === "ready") engine?.cacheItem(result);
  return result;
});
ipcMain.handle("external:open", async (_event, value: unknown) => {
  if (typeof value !== "string") return false;
  try {
    const url = new URL(value);
    const allowedHosts = new Set([
      "eqlegendstools.com", "www.eqlegendstools.com", "eqlwiki.com",
      "www.eqlwiki.com", "github.com",
    ]);
    if (url.protocol !== "https:" || !allowedHosts.has(url.hostname)) return false;
    await shell.openExternal(url.toString());
    return true;
  } catch {
    return false;
  }
});
ipcMain.handle("updates:get-state", () => publishUpdateCenter());
ipcMain.handle("updates:check", () => checkAllUpdates());
ipcMain.handle("updates:choose-eq-root", async () => {
  if (!mainWindow || !engine) return null;
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "Choose your EverQuest Legends folder",
    defaultPath: engine.getState().settings.eqRoot || engine.getState().settings.logPath || undefined,
    properties: ["openDirectory"],
  });
  if (result.canceled || result.filePaths.length !== 1) return null;
  try {
    if (!spinUISkinUpdater) initializeUpdateServices();
    const eqRoot = await spinUISkinUpdater!.setEqRoot(result.filePaths[0]);
    engine.updateDesktopSettings({ eqRoot });
    updateCenterState ??= initialUpdateCenterState();
    updateCenterState.eqRoot = eqRoot;
    return await checkAllUpdates();
  } catch (error) {
    await dialog.showMessageBox(mainWindow, {
      type: "warning",
      title: "EverQuest folder not recognized",
      message: error instanceof Error ? error.message : String(error),
      detail: "Choose the folder that directly contains eqgame.exe and the uifiles folder.",
    });
    return publishUpdateCenter();
  }
});
ipcMain.handle("updates:install", async (_event, value: unknown) => {
  const allowed: readonly UpdateComponentId[] = ["loremaster", "spinui_reloaded", "spinui_glass"];
  if (!Array.isArray(value) || value.length < 1 || value.length > allowed.length) return publishUpdateCenter();
  const ids = [...new Set(value)].filter((id): id is UpdateComponentId => allowed.includes(id as UpdateComponentId));
  if (!ids.length || ids.length !== new Set(value).size) return publishUpdateCenter();
  if (!portableUpdater || !spinUISkinUpdater) initializeUpdateServices();
  updateCenterState ??= initialUpdateCenterState();
  const skins = ids.filter((id): id is SpinUISkinName => id !== "loremaster");
  try {
    updateCenterState.busy = true;
    publishUpdateCenter();
    if (skins.length) {
      const eqRoot = engine?.getState().settings.eqRoot || updateCenterState.eqRoot;
      await spinUISkinUpdater!.installAll(skins, eqRoot || undefined);
      mergeSkinUpdate(spinUISkinUpdater!.getState());
    }
    if (ids.includes("loremaster")) {
      if (portableUpdater!.getProgress().phase !== "available" && !stagedPortableUpdate) {
        const check = await portableUpdater!.check();
        mergePortableUpdate(portableUpdater!.getProgress(), check);
        if (!check.updateAvailable) return publishUpdateCenter();
      }
      stagedPortableUpdate ??= await portableUpdater!.stage();
      mergePortableUpdate(portableUpdater!.getProgress());
      const confirmation = mainWindow ? await dialog.showMessageBox(mainWindow, {
        type: "info",
        title: `Install Loremaster ${stagedPortableUpdate.version}`,
        message: "The verified update is ready. Restart Loremaster now?",
        detail: "Settings, combat and loot history, EQ logs, and UI layouts are preserved. If the new build cannot start its window and parser, the previous executable is restored automatically.",
        buttons: ["INSTALL & RESTART", "LATER"],
        defaultId: 0,
        cancelId: 1,
      }) : { response: 0 };
      if (confirmation.response !== 0) {
        updateCenterState.components.loremaster = {
          ...updateCenterState.components.loremaster,
          phase: "ready",
          progress: null,
          detail: `Loremaster ${stagedPortableUpdate.version} is verified and ready.`,
        };
        updateCenterState.busy = false;
        return publishUpdateCenter();
      }
      portableUpdater!.installAndRelaunch(stagedPortableUpdate);
      portableInstallHandoff = true;
      updateCenterState.busy = true;
      publishUpdateCenter();
      setTimeout(() => app.quit(), 350);
    }
  } catch (error) {
    if (!(error instanceof EverQuestRunningError) && mainWindow) {
      await dialog.showMessageBox(mainWindow, {
        type: "error",
        title: "Update could not be completed",
        message: error instanceof Error ? error.message : String(error),
        detail: "Nothing outside the selected Loremaster or SpinUI component was changed.",
      });
    }
    mergeSkinUpdate(spinUISkinUpdater!.getState());
    mergePortableUpdate(portableUpdater!.getProgress());
  } finally {
    if (!ids.includes("loremaster") || portableUpdater!.getProgress().phase === "error") {
      updateCenterState.busy = false;
    }
  }
  return publishUpdateCenter();
});

ipcMain.on("window:set-mode", (_event, expanded: boolean) => {
  setWindowMode(Boolean(expanded));
});
ipcMain.on("window:set-analysis", (_event, active: boolean) => {
  setAnalysisMode(Boolean(active));
});

ipcMain.on("window:minimize", minimizeLoremasterWindow);
ipcMain.on("window:close", quitLoremaster);

app.whenReady().then(() => {
  if (!ownsSingleInstance) return;
  engine = new EngineSupervisor();
  engine.start();
  initializeUpdateServices();
  createWindow();
  ensureTray();
  startTopmostHeartbeat();
  screen.on("display-metrics-changed", scheduleTopmostReassertion);
  const updateSettings = engine.getState().settings;
  const lastCheck = readUpdateMetadata().lastCheckedAt;
  const lastCheckMs = Date.parse(lastCheck);
  const autoCheckDue = !Number.isFinite(lastCheckMs) || Date.now() - lastCheckMs >= 24 * 60 * 60 * 1000;
  if (app.isPackaged && updateSettings.autoCheckUpdates && autoCheckDue
      && !process.env.LOREMASTER_SCREENSHOT_PATH && !process.env.LOREMASTER_SMOKE_EXIT_MS) {
    const timer = setTimeout(() => void checkAllUpdates(), 8_000 + Math.floor(Math.random() * 5_000));
    timer.unref?.();
  }
  const smokeExitMs = Number(process.env.LOREMASTER_SMOKE_EXIT_MS || 0);
  if (Number.isFinite(smokeExitMs) && smokeExitMs >= 250) {
    setTimeout(() => app.quit(), smokeExitMs);
  }
});
app.on("before-quit", () => {
  clearTopmostReassertions();
  if (topmostHeartbeatTimer) clearInterval(topmostHeartbeatTimer);
  topmostHeartbeatTimer = null;
  tray?.destroy();
  tray = null;
  engine?.stop();
});
app.on("window-all-closed", () => {
  if (!portableInstallHandoff) app.quit();
});
app.on("second-instance", restoreLoremasterWindow);
app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
  else restoreLoremasterWindow();
});
