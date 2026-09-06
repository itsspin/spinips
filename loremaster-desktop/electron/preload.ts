import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("loremasterDesktop", {
  getRuntimeMetrics: () => ipcRenderer.invoke("runtime:metrics"),
  getEngineState: () => ipcRenderer.invoke("engine:get-state"),
  chooseLogFolder: () => ipcRenderer.invoke("engine:choose-log-folder"),
  setLogPath: (value: string) => ipcRenderer.invoke("engine:set-log-path", value),
  setRaidDifficulty: (value: number | null) => ipcRenderer.invoke("engine:set-raid-difficulty", value),
  setRaidCompletion: (target: string, difficulty: number, completed: boolean) =>
    ipcRenderer.invoke("engine:set-raid-completion", target, difficulty, completed),
  updateSettings: (value: unknown) => ipcRenderer.invoke("settings:update", value),
  chooseBisBuild: () => ipcRenderer.invoke("gear:choose-build"),
  chooseInventory: () => ipcRenderer.invoke("gear:choose-inventory"),
  refreshGearData: () => ipcRenderer.invoke("gear:refresh"),
  openExternal: (value: string) => ipcRenderer.invoke("external:open", value),
  lookupItem: (name: string) => ipcRenderer.invoke("items:lookup", name),
  getSpellCatalog: () => ipcRenderer.invoke("progression:spells"),
  setXpCheckpoint: (level: number, percent: number) => ipcRenderer.invoke("progression:checkpoint", level, percent),
  queryLoot: (request: unknown) => ipcRenderer.invoke("journal:query-loot", request),
  getUpdateState: () => ipcRenderer.invoke("updates:get-state"),
  checkForUpdates: () => ipcRenderer.invoke("updates:check"),
  chooseUpdateEqRoot: () => ipcRenderer.invoke("updates:choose-eq-root"),
  installUpdates: (ids: readonly string[]) => ipcRenderer.invoke("updates:install", ids),
  resetEngine: () => ipcRenderer.send("engine:reset"),
  testAlert: () => ipcRenderer.send("alerts:test"),
  chooseAlertSound: (kind: string) => ipcRenderer.invoke("alerts:choose-sound", kind),
  readAlertSound: (kind: string) => ipcRenderer.invoke("alerts:read-sound", kind),
  onSnapshot: (callback: (event: unknown) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, value: unknown) => callback(value);
    ipcRenderer.on("engine:snapshot", listener);
    return () => ipcRenderer.removeListener("engine:snapshot", listener);
  },
  onHealth: (callback: (health: unknown) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, value: unknown) => callback(value);
    ipcRenderer.on("engine:health", listener);
    return () => ipcRenderer.removeListener("engine:health", listener);
  },
  onGearPlan: (callback: (gearPlan: unknown) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, value: unknown) => callback(value);
    ipcRenderer.on("gear:state", listener);
    return () => ipcRenderer.removeListener("gear:state", listener);
  },
  onSettings: (callback: (settings: unknown) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, value: unknown) => callback(value);
    ipcRenderer.on("settings:changed", listener);
    return () => ipcRenderer.removeListener("settings:changed", listener);
  },
  onCompanionLayout: (callback: (layout: unknown) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, value: unknown) => callback(value);
    ipcRenderer.on("window:companion-layout", listener);
    return () => ipcRenderer.removeListener("window:companion-layout", listener);
  },
  onCompanionInteraction: (callback: (state: unknown) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, value: unknown) => callback(value);
    ipcRenderer.on("window:companion-interaction", listener);
    return () => ipcRenderer.removeListener("window:companion-interaction", listener);
  },
  onUpdateState: (callback: (state: unknown) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, value: unknown) => callback(value);
    ipcRenderer.on("updates:state", listener);
    return () => ipcRenderer.removeListener("updates:state", listener);
  },
  onTestAlert: (callback: (alert: unknown) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, value: unknown) => callback(value);
    ipcRenderer.on("alerts:test", listener);
    return () => ipcRenderer.removeListener("alerts:test", listener);
  },
  setExpanded: (expanded: boolean) => ipcRenderer.send("window:set-mode", expanded),
  setAnalysis: (active: boolean) => ipcRenderer.send("window:set-analysis", active),
  setCompanionInspecting: (active: boolean) => ipcRenderer.send("window:companion-inspect", active),
  setCompanionDetailRows: (count: number) => ipcRenderer.send("window:companion-detail-rows", count),
  minimizeWindow: () => ipcRenderer.send("window:minimize"),
  closeWindow: () => ipcRenderer.send("window:close"),
});
