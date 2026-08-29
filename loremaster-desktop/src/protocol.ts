export const PROTOCOL_VERSION = 1 as const;

export type ControlKind = "mez" | "lull";
export type ControlState = "active" | "unconfirmed" | "ambiguous" | "failed";
export type ControlUrgency = "safe" | "warning" | "critical";

export interface CharacterView {
  name: string;
  level: number;
  composition: string;
  zone: string;
}

export interface CombatView {
  active: boolean;
  autoAttack: boolean;
  encounterName: string;
  fightDps: number;
  sessionDps: number;
  personalDamage: number;
  charmedPetDamage: number;
  summonedPetDamage: number;
  fightSeconds: number;
  fightDamage: number;
  fightPersonalDamage: number;
  fightCharmedPetDamage: number;
  fightSummonedPetDamage: number;
  damageTaken: number;
  healingDone: number;
  kills: number;
  crits: number;
  misses: number;
}

export interface CombatMetricView {
  name: string;
  total: number;
  hits: number;
  maximum: number;
  category?: CombatAbilityCategory;
}

export type CombatAbilityCategory =
  | "melee"
  | "spell"
  | "dot"
  | "proc"
  | "damage_shield"
  | "pet"
  | "healing"
  | "unknown";

export interface CombatHealingMetricView extends CombatMetricView {
  overheal: number;
}

export interface EncounterTimelinePointView {
  second: number;
  outgoing: number;
  incoming: number;
  healing: number;
  kills: number;
}

export interface CombatBreakdownView {
  sources: readonly CombatMetricView[];
  targets: readonly CombatMetricView[];
  actors: readonly CombatMetricView[];
}

export type CombatActorRole = "self" | "charmed" | "summoned" | "group" | "observed";

export interface CombatActorView {
  name: string;
  role: CombatActorRole;
  encounterDamage: number;
  encounterDps: number;
  encounterHits: number;
  encounterMaximum: number;
  sessionDamage: number;
  sessionDps: number;
  sessionHits: number;
  sessionMaximum: number;
}

export interface EncounterView {
  encounterId: string;
  name: string;
  active: boolean;
  startedAt: string;
  endedAt: string;
  seconds: number;
  damage: number;
  dps: number;
  personalDamage: number;
  charmedPetDamage: number;
  summonedPetDamage: number;
  damageTaken: number;
  healingDone: number;
  healsReceived: number;
  kills: number;
  crits: number;
  misses: number;
  sources: readonly CombatMetricView[];
  targets: readonly CombatMetricView[];
  actors: readonly CombatActorView[];
  healingSources: readonly CombatHealingMetricView[];
  timeline: readonly EncounterTimelinePointView[];
  zone?: string;
  raidTier?: number | null;
  raidMode?: string;
  summaryOnly?: boolean;
}

export interface LootItemInfoView {
  title: string;
  stats: readonly string[];
  notes: readonly string[];
  sections: Readonly<Record<string, readonly string[]>>;
  url: string;
  freshness: string;
}

export interface ItemLookupView extends LootItemInfoView {
  status: "ready" | "not-found" | "offline" | "error";
  requestedName: string;
  detail: string;
}

export interface LootEventView {
  eventId: string;
  occurredAt: string;
  item: string;
  itemKey: string;
  quantity: number;
  looter: string;
  source: string;
  zone: string;
  character: string;
  server: string;
  encounterId: string;
  acquisitionType: string;
  raidTier: number | null;
  raidMode: string;
  itemInfo?: LootItemInfoView | null;
}

export type LootQueryScope = "all" | "mine" | "others" | "known";

export interface LootQueryRequest {
  query?: string;
  zone?: string;
  raidTier?: number | "open" | "all";
  scope?: LootQueryScope;
  offset?: number;
  limit?: number;
}

export interface LootQueryResult {
  rows: readonly LootEventView[];
  total: number;
  offset: number;
  hasMore: boolean;
}

export interface JournalEncounterView {
  encounterId: string;
  startedAt: string;
  endedAt: string;
  name: string;
  zone: string;
  character: string;
  server: string;
  raidTier: number | null;
  raidMode: string;
  seconds: number;
  damage: number;
  dps: number;
  kills: number;
}

export interface ControlTimerView {
  kind: ControlKind;
  state: ControlState;
  target: string;
  count: number;
  spell: string;
  rank: number;
  landedAt: string;
  safeExpiresAt: string;
  expiresAt: string;
  durationSeconds: number;
  safeRemainingSeconds: number;
  remainingSeconds: number;
  lastTick: boolean;
  urgency: ControlUrgency;
  confidence: "confirmed" | "exact" | "conservative" | "unconfirmed";
  ambiguity: string;
}

export interface EngineSnapshot {
  protocolVersion: typeof PROTOCOL_VERSION;
  sequence: number;
  observedAt: string;
  character: CharacterView;
  groupMembers: readonly string[];
  combat: CombatView;
  breakdown: CombatBreakdownView;
  encounters?: readonly EncounterView[];
  loot?: readonly LootEventView[];
  lootEventCount?: number;
  lootTotalCount?: number;
  lootUniqueCount?: number;
  journalEncounters?: readonly JournalEncounterView[];
  controls: readonly ControlTimerView[];
  hiddenControlRows: number;
  controlNoticeCount: number;
  controlAmbiguityCount: number;
  weekly?: WeeklyProgressView;
  alerts?: readonly AlertView[];
}

export interface AlertView {
  id: string;
  kind: string;
  severity: "danger" | "warn" | "info";
  title: string;
  target: string;
  occurredAt: string;
  expiresAt: string;
}

export interface WeeklyBossKillView {
  target: string;
  zone: string;
  character: string;
  killed_at: string;
  difficulty: number;
  duration_seconds?: number;
  difficulty_source?: string;
  instance_name?: string;
  instance_mode?: string;
  instance_label?: string;
  context_observed_at?: string;
  evidence?: string;
}

export interface WeeklyRaidRowView {
  target: string;
  zone: string;
  difficulties: readonly boolean[];
  bestSeconds: readonly (number | null)[];
}

export interface WeeklyProgressView {
  weekStart: string;
  nextReset: string;
  resetLabel: string;
  raidCount: number;
  trackedLockoutCount: number;
  completedCount: number;
  kills: readonly WeeklyBossKillView[];
  raids: readonly WeeklyRaidRowView[];
  activeDifficulty?: number | null;
  configuredDifficulty?: number | null;
  difficultySource?: "log-zone" | "manual" | "unknown" | string;
  raidContext?: RaidContextView | null;
  pendingRaidTarget?: string;
}

export interface RaidContextView {
  zone: string;
  instanceName: string;
  mode: string;
  difficulty: number;
  difficultyName: string;
  label: string;
  observedAt: string;
  evidence: string;
  source: string;
}

export interface GearGoalView {
  slot: string;
  itemId: string;
  itemName: string;
  itemUrl: string;
  source: string;
  zone: string;
  targets: readonly string[];
  ownership: "missing" | "equipped" | "bag" | "bank" | "owned";
  location: string;
}

export interface FarmingRouteView {
  zone: string;
  goalCount: number;
  itemNames: readonly string[];
  targets: readonly string[];
}

export interface GearPlanView {
  status: "empty" | "ready" | "error";
  detail: string;
  buildName: string;
  classes: readonly string[];
  exportedAt: string;
  buildPath: string;
  inventoryPath: string;
  catalogUpdatedAt: string;
  goals: readonly GearGoalView[];
  equippedGoalCount: number;
  bagUpgradeCount: number;
  ownedGoalCount: number;
  missingGoalCount: number;
  routes: readonly FarmingRouteView[];
}

export function isGearPlanView(value: unknown): value is GearPlanView {
  if (!value || typeof value !== "object") return false;
  const plan = value as Partial<GearPlanView>;
  return ["empty", "ready", "error"].includes(String(plan.status)) &&
    typeof plan.detail === "string" && Array.isArray(plan.goals) && Array.isArray(plan.routes);
}

export type EngineHealthState = "starting" | "searching" | "live" | "error" | "stopped";

export interface EngineHealth {
  state: EngineHealthState;
  detail: string;
  configuredPath: string;
  activeLogPath: string;
  character: string;
  server: string;
}

export type AlertAnchor = "auto" | "above" | "below" | "left" | "right";
export type LoremasterTheme = "vellum" | "glass";
export type SeedMeterMode = "self" | "group" | "pet" | "all";
export interface SeedCompanionLayout {
  meterRows: number;
  controlRows: number;
}
export type AlertSoundKind = "default" | "charmBreak" | "tell" | "summon" | "death" | "bigHit" | "nameCalled" | "mez" | "lull";
export type AlertSoundPreset = "rune" | "crystal" | "ember" | "bell" | "custom" | "silent";

export interface AlertSoundProfile {
  preset: AlertSoundPreset;
  customPath: string;
}

export type AlertSoundProfiles = Record<AlertSoundKind, AlertSoundProfile>;

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

export interface DesktopSettings {
  logPath: string;
  eqRoot: string;
  autoCheckUpdates: boolean;
  raidDifficulty: number | null;
  bisBuildPath: string;
  inventoryPath: string;
  uiTheme: LoremasterTheme;
  alwaysOnTop: boolean;
  fontScale: number;
  composition: string;
  splitCharmedPetDps: boolean;
  seedMeterVisible: boolean;
  seedMeterMode: SeedMeterMode;
  seedMeterOpacity: number;
  stanceAdvisorEnabled: boolean;
  itemNetworkLookups: boolean;
  seedPosition: { x: number; y: number } | null;
  alerts: AlertSettings;
}

export type UpdateComponentId = "loremaster" | "spinui_reloaded" | "spinui_glass";

export type UpdateComponentPhase =
  | "idle"
  | "checking"
  | "current"
  | "available"
  | "not-installed"
  | "modified"
  | "downloading"
  | "verifying"
  | "ready"
  | "waiting-for-eq"
  | "installing"
  | "restart-required"
  | "error";

export interface UpdateComponentState {
  id: UpdateComponentId;
  phase: UpdateComponentPhase;
  currentVersion: string;
  latestVersion: string;
  progress: number | null;
  detail: string;
}

export interface UpdateCenterState {
  currentVersion: string;
  latestVersion: string;
  lastCheckedAt: string;
  eqRoot: string;
  busy: boolean;
  components: Record<UpdateComponentId, UpdateComponentState>;
}

export interface EngineSnapshotEvent {
  protocolVersion: typeof PROTOCOL_VERSION;
  sequence: number;
  occurredAt: string;
  eventType: "engine.snapshot";
  snapshot: EngineSnapshot;
}

export interface ReplayFixture {
  schemaVersion: typeof PROTOCOL_VERSION;
  title: string;
  events: readonly EngineSnapshotEvent[];
}

export function isEngineSnapshotEvent(value: unknown): value is EngineSnapshotEvent {
  if (!value || typeof value !== "object") return false;
  const event = value as Partial<EngineSnapshotEvent>;
  if (event.protocolVersion !== PROTOCOL_VERSION || event.eventType !== "engine.snapshot") return false;
  const snapshot = event.snapshot as Partial<EngineSnapshot> | undefined;
  return Boolean(
    snapshot &&
    snapshot.protocolVersion === PROTOCOL_VERSION &&
    typeof snapshot.sequence === "number" &&
    snapshot.character && typeof snapshot.character.name === "string" &&
    snapshot.combat && typeof snapshot.combat.fightDps === "number" &&
    Array.isArray(snapshot.controls),
  );
}

export function isEngineHealth(value: unknown): value is EngineHealth {
  if (!value || typeof value !== "object") return false;
  const health = value as Partial<EngineHealth>;
  return ["starting", "searching", "live", "error", "stopped"].includes(String(health.state)) &&
    typeof health.detail === "string" && typeof health.configuredPath === "string";
}

export function assertReplayFixture(value: unknown): asserts value is ReplayFixture {
  if (!value || typeof value !== "object") throw new Error("fixture must be an object");
  const fixture = value as Partial<ReplayFixture>;
  if (fixture.schemaVersion !== PROTOCOL_VERSION) {
    throw new Error(`unsupported fixture protocol: ${String(fixture.schemaVersion)}`);
  }
  if (!Array.isArray(fixture.events) || fixture.events.length === 0) {
    throw new Error("fixture must include at least one snapshot event");
  }
  let lastSequence = -1;
  for (const event of fixture.events) {
    if (!isEngineSnapshotEvent(event)) {
      throw new Error("fixture event protocol mismatch");
    }
    if (event.sequence <= lastSequence) throw new Error("event sequences must increase");
    lastSequence = event.sequence;
  }
}
