import type { AlertSoundKind, DesktopSettings, EngineHealth, EngineSnapshotEvent, GearPlanView, ItemLookupView, LootQueryRequest, LootQueryResult, SeedCompanionLayout, UpdateCenterState, UpdateComponentId } from "./protocol";

export {};

declare global {
  interface Window {
    loremasterDesktop?: {
      getRuntimeMetrics: () => Promise<{
        coldStartMs: number;
        residentMemoryMb: number;
        platform: string;
        version: string;
      }>;
      getEngineState: () => Promise<{
        health: EngineHealth;
        snapshot: EngineSnapshotEvent | null;
        settings: DesktopSettings;
        gearPlan: GearPlanView;
      }>;
      chooseLogFolder: () => Promise<string | null>;
      setLogPath: (value: string) => Promise<boolean>;
      setRaidDifficulty: (value: number | null) => Promise<boolean>;
      setRaidCompletion: (target: string, difficulty: number, completed: boolean) => Promise<boolean>;
      updateSettings: (value: Partial<Omit<DesktopSettings, "alerts">> & { alerts?: Partial<DesktopSettings["alerts"]> }) => Promise<DesktopSettings | null>;
      chooseBisBuild: () => Promise<string | null>;
      chooseInventory: () => Promise<string | null>;
      refreshGearData: () => Promise<boolean>;
      openExternal: (value: string) => Promise<boolean>;
      lookupItem: (name: string) => Promise<ItemLookupView>;
      queryLoot: (request: LootQueryRequest) => Promise<LootQueryResult>;
      getUpdateState: () => Promise<UpdateCenterState>;
      checkForUpdates: () => Promise<UpdateCenterState>;
      chooseUpdateEqRoot: () => Promise<UpdateCenterState | null>;
      installUpdates: (ids: readonly UpdateComponentId[]) => Promise<UpdateCenterState>;
      resetEngine: () => void;
      testAlert: () => void;
      chooseAlertSound: (kind: AlertSoundKind) => Promise<DesktopSettings | null>;
      readAlertSound: (kind: AlertSoundKind) => Promise<{
        bytes: Uint8Array;
        extension: string;
        name: string;
      } | null>;
      onSnapshot: (callback: (event: unknown) => void) => () => void;
      onHealth: (callback: (health: unknown) => void) => () => void;
      onGearPlan: (callback: (gearPlan: unknown) => void) => () => void;
      onSettings: (callback: (settings: unknown) => void) => () => void;
      onCompanionLayout: (callback: (layout: SeedCompanionLayout) => void) => () => void;
      onUpdateState: (callback: (state: UpdateCenterState) => void) => () => void;
      onTestAlert: (callback: (alert: unknown) => void) => () => void;
      setExpanded: (expanded: boolean) => void;
      setAnalysis: (active: boolean) => void;
      minimizeWindow: () => void;
      closeWindow: () => void;
    };
  }
}
