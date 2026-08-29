import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import {
  PROTOCOL_VERSION,
  isEngineHealth,
  isEngineSnapshotEvent,
  isGearPlanView,
  type ControlTimerView,
  type CombatActorRole,
  type CombatActorView,
  type DesktopSettings,
  type EncounterView,
  type EngineHealth,
  type EngineSnapshotEvent,
  type GearPlanView,
  type LoremasterTheme,
  type RaidContextView,
  type SeedCompanionLayout,
  type SeedMeterMode,
  type AlertSoundKind,
  type AlertSoundPreset,
  type UpdateCenterState,
  type UpdateComponentId,
  type UpdateComponentPhase,
  type UpdateComponentState,
} from "./protocol";
import { CombatArchive } from "./CombatArchive";
import { LootChronicle } from "./LootChronicle";
import { abilityCategoryLabel, abilityIdentityStyle, actorIdentityStyle, normalizeAbilityCategory } from "./visualIdentity";

const roman = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"];
const raidDifficulties = [0, 1, 2, 3, 4] as const;
const cogSource = "./loremaster-cog.png";
const eqToolsUrl = "https://eqlegendstools.com/";
const eqToolsCharSheetUrl = "https://eqlegendstools.com/char-sheet/";
const soundKinds: readonly { id: AlertSoundKind; label: string; detail: string }[] = [
  { id: "default", label: "General alerts", detail: "Raid prompts and alert previews" },
  { id: "charmBreak", label: "Charm breaks", detail: "Urgent recharm warning" },
  { id: "tell", label: "Incoming tells", detail: "Direct player messages" },
  { id: "summon", label: "Summoned", detail: "Boss summon warning" },
  { id: "death", label: "Death", detail: "Character death" },
  { id: "bigHit", label: "Big hits", detail: "Damage threshold warning" },
  { id: "nameCalled", label: "Name called", detail: "Group, raid, or guild mention" },
  { id: "mez", label: "Mez urgent", detail: "Safe window closing" },
  { id: "lull", label: "Lull urgent", detail: "Safe window closing" },
];
const soundPresets: readonly { id: AlertSoundPreset; label: string }[] = [
  { id: "rune", label: "Rune Pulse" },
  { id: "crystal", label: "Crystal Chime" },
  { id: "ember", label: "Ember Alarm" },
  { id: "bell", label: "Temple Bell" },
  { id: "custom", label: "Custom File" },
  { id: "silent", label: "Silent" },
];
const defaultSoundProfiles: DesktopSettings["alerts"]["soundProfiles"] = {
  default: { preset: "rune", customPath: "" }, charmBreak: { preset: "ember", customPath: "" },
  tell: { preset: "crystal", customPath: "" }, summon: { preset: "ember", customPath: "" },
  death: { preset: "ember", customPath: "" }, bigHit: { preset: "rune", customPath: "" },
  nameCalled: { preset: "crystal", customPath: "" }, mez: { preset: "rune", customPath: "" },
  lull: { preset: "bell", customPath: "" },
};

function normalizeTheme(value: unknown): LoremasterTheme {
  return value === "glass" ? "glass" : "vellum";
}

function applyTheme(value: unknown): LoremasterTheme {
  const theme = normalizeTheme(value);
  document.documentElement.dataset.theme = theme;
  return theme;
}

const defaultDesktopSettings: DesktopSettings = {
  logPath: "", eqRoot: "", autoCheckUpdates: true,
  raidDifficulty: null, bisBuildPath: "", inventoryPath: "",
  uiTheme: "vellum",
  alwaysOnTop: true, fontScale: 1.15, composition: "", splitCharmedPetDps: false,
  seedMeterVisible: true, seedMeterMode: "all", seedMeterOpacity: 0.9,
  stanceAdvisorEnabled: false, itemNetworkLookups: true, seedPosition: null,
  alerts: {
    alertsEnabled: true, alertSound: true, alertSeconds: 5, alertAnchor: "auto",
    alertCharmBreak: true, alertTells: true, alertSummon: true, alertDeath: true,
    alertBigHit: true, alertNameCalled: true, bigHitThreshold: 800,
    mezTimersEnabled: true, mezTimerSound: false, mezWarningSeconds: 10,
    lullTimersEnabled: true, lullTimerSound: false, lullWarningSeconds: 12,
    soundProfiles: defaultSoundProfiles,
  },
};

const updateComponentIds: readonly UpdateComponentId[] = ["loremaster", "spinui_reloaded", "spinui_glass"];
const updateComponentLabels: Record<UpdateComponentId, { name: string; eyebrow: string }> = {
  loremaster: { name: "Loremaster", eyebrow: "DESKTOP OVERLAY" },
  spinui_reloaded: { name: "SpinUI Reloaded", eyebrow: "VELLUM SKIN" },
  spinui_glass: { name: "SpinUI Glass", eyebrow: "FROST SKIN" },
};
const emptyUpdateState: UpdateCenterState = {
  currentVersion: "",
  latestVersion: "",
  lastCheckedAt: "",
  eqRoot: "",
  busy: false,
  components: {
    loremaster: { id: "loremaster", phase: "idle", currentVersion: "", latestVersion: "", progress: null, detail: "Ready to check" },
    spinui_reloaded: { id: "spinui_reloaded", phase: "idle", currentVersion: "", latestVersion: "", progress: null, detail: "Select your EverQuest folder to check this skin" },
    spinui_glass: { id: "spinui_glass", phase: "idle", currentVersion: "", latestVersion: "", progress: null, detail: "Select your EverQuest folder to check this skin" },
  },
};

const emptyGearPlan: GearPlanView = {
  status: "empty", detail: "Import an EQ Legends Tools character build to begin.",
  buildName: "", classes: [], exportedAt: "", buildPath: "", inventoryPath: "",
  catalogUpdatedAt: "", goals: [], equippedGoalCount: 0, bagUpgradeCount: 0,
  ownedGoalCount: 0, missingGoalCount: 0, routes: [],
};

const emptyEvent: EngineSnapshotEvent = {
  protocolVersion: PROTOCOL_VERSION,
  sequence: 0,
  occurredAt: new Date(0).toISOString(),
  eventType: "engine.snapshot",
  snapshot: {
    protocolVersion: PROTOCOL_VERSION,
    sequence: 0,
    observedAt: new Date(0).toISOString(),
    character: { name: "?", level: 0, composition: "", zone: "" },
    groupMembers: [],
    combat: {
      active: false, autoAttack: false, encounterName: "", fightDps: 0, sessionDps: 0,
      personalDamage: 0, charmedPetDamage: 0, summonedPetDamage: 0,
      fightSeconds: 0, fightDamage: 0, fightPersonalDamage: 0,
      fightCharmedPetDamage: 0, fightSummonedPetDamage: 0,
      damageTaken: 0, healingDone: 0, kills: 0, crits: 0, misses: 0,
    },
    breakdown: { sources: [], targets: [], actors: [] },
    encounters: [],
    controls: [], hiddenControlRows: 0, controlNoticeCount: 0,
    controlAmbiguityCount: 0,
  },
};

const initialHealth: EngineHealth = {
  state: "starting",
  detail: "Starting the local parser engine",
  configuredPath: "",
  activeLogPath: "",
  character: "?",
  server: "?",
};

function formatDps(value: number): string {
  return value >= 1000 ? `${(value / 1000).toFixed(value >= 10000 ? 0 : 1)}k` : String(Math.round(value));
}

function formatMeterValue(value: number): string {
  const safe = Math.max(0, Number(value) || 0);
  if (safe >= 1_000_000) return `${(safe / 1_000_000).toFixed(safe >= 10_000_000 ? 0 : 1)}m`;
  if (safe >= 100_000) return `${Math.round(safe / 1_000)}k`;
  if (safe >= 1_000) return `${(safe / 1_000).toFixed(1)}k`;
  return Math.round(safe).toLocaleString();
}

function formatDuration(value: number): string {
  const seconds = Math.max(0, Math.floor(value));
  const minutes = Math.floor(seconds / 60);
  const remainder = String(seconds % 60).padStart(2, "0");
  return minutes > 0 ? `${minutes}:${remainder}` : `${seconds}s`;
}

function spellLabel(control: ControlTimerView): string {
  const rank = control.rank > 0 ? roman[control.rank] ?? String(control.rank) : "";
  return `${control.spell}${rank ? ` ${rank}` : ""}`;
}

function phaseLabel(control: ControlTimerView): string {
  if (control.state !== "active") return `? ${control.state.toUpperCase()}`;
  if (control.lastTick) return "!! LAST TICK";
  if (control.urgency === "critical") return "!! CRITICAL";
  if (control.urgency === "warning") return "! WARNING";
  return "SAFE";
}

function ControlRow({ control }: { control: ControlTimerView }) {
  const progress = control.durationSeconds > 0
    ? Math.max(0, Math.min(1, control.safeRemainingSeconds / control.durationSeconds))
    : 0;
  const detail = control.state === "active"
    ? `${control.kind.toUpperCase()} · ${spellLabel(control)} · ${control.confidence.toUpperCase()}`
    : `${control.kind.toUpperCase()} · ${spellLabel(control)} · ${control.ambiguity}`;
  return (
    <article
      className={`control-row ${control.kind} ${control.state} ${control.urgency}`}
      aria-label={`${control.kind} ${control.target} ${phaseLabel(control)}`}
    >
      <span className="control-accent" aria-hidden="true" />
      <div className="control-copy">
        <strong>{control.target}{control.count > 1 ? ` ×${control.count} · earliest` : ""}</strong>
        <small>{detail}</small>
      </div>
      <div className="control-time">
        <strong>{control.state === "active" ? `${Math.ceil(control.safeRemainingSeconds)}s` : "—"}</strong>
        <small>{phaseLabel(control)}</small>
      </div>
      {control.state === "active" && (
        <span className="control-meter" aria-hidden="true">
          <span style={{ transform: `scaleX(${progress})` }} />
        </span>
      )}
    </article>
  );
}

function CogMark({ compact = false }: { compact?: boolean }) {
  return <span className={`brand-cog ${compact ? "compact" : ""}`} aria-hidden="true">
    <img src={cogSource} alt="" />
  </span>;
}

function RuneSeed({ event, health, settings, onExpand, onCycleMeter, onToggleMeter }: {
  event: EngineSnapshotEvent;
  health: EngineHealth;
  settings: DesktopSettings;
  onExpand: () => void;
  onCycleMeter: () => void;
  onToggleMeter: () => void;
}) {
  const { combat } = event.snapshot;
  const urgent = health.state === "error" || Boolean(event.snapshot.alerts?.length) ||
    Boolean(event.snapshot.weekly?.pendingRaidTarget) || event.snapshot.controls.some((control) =>
    control.state !== "active" || control.urgency !== "safe");
  return (
    <div className={`rune-seed ${urgent ? "urgent" : ""} ${combat.autoAttack ? "attacking" : ""}`}>
      <span className="seed-drag" aria-hidden="true" />
      <button className="seed-action" onClick={onExpand} type="button"
        aria-label={`${formatDps(combat.fightDps)} DPS${combat.autoAttack ? ", auto attack on" : ""}${urgent ? ", urgent signal" : ""}`}>
        <CogMark compact />
        <span className="seed-metric"><strong>{formatDps(combat.fightDps)}</strong><small>DPS</small></span>
      </button>
      <span className="seed-meter-quickbar">
        <button type="button" onClick={onCycleMeter}
          title={`DPS meter: ${seedMeterModeCopy[settings.seedMeterMode].label}. Click for next view.`}
          aria-label={`Change DPS meter view. Current view: ${seedMeterModeCopy[settings.seedMeterMode].label}`}>
          {seedMeterModeCopy[settings.seedMeterMode].short}
        </button>
        <button className={settings.seedMeterVisible ? "visible" : "hidden"} type="button"
          onClick={onToggleMeter} aria-pressed={settings.seedMeterVisible}
          title={`${settings.seedMeterVisible ? "Hide" : "Show"} the compact DPS meter`}
          aria-label={`${settings.seedMeterVisible ? "Hide" : "Show"} compact DPS meter`}>
          <i aria-hidden="true" />
        </button>
      </span>
      {urgent && <span className="seed-alert" aria-label="urgent signal">!</span>}
    </div>
  );
}

function SeedControlRow({ control }: { control: ControlTimerView }) {
  const progress = control.durationSeconds > 0
    ? Math.max(0, Math.min(1, control.safeRemainingSeconds / control.durationSeconds))
    : 0;
  const seconds = Math.ceil(control.safeRemainingSeconds > 0
    ? control.safeRemainingSeconds
    : control.remainingSeconds);
  return <article className={`seed-control-row ${control.kind} ${control.urgency}`}>
    <i className="seed-control-accent" aria-hidden="true" />
    <span className="seed-control-copy">
      <strong>{control.target}{control.count > 1 ? ` ×${control.count}` : ""}</strong>
      <small>{control.kind.toUpperCase()} · {spellLabel(control)}</small>
    </span>
    <span className="seed-control-time">
      <strong>{seconds}s</strong>
      <small>{phaseLabel(control)}</small>
    </span>
    <i className="seed-control-meter" aria-hidden="true"><i style={{ transform: `scaleX(${progress})` }} /></i>
  </article>;
}

const seedMeterModes: readonly SeedMeterMode[] = ["self", "group", "pet", "all"];
const seedMeterModeCopy: Record<SeedMeterMode, { label: string; short: string; detail: string }> = {
  self: { label: "SELF", short: "SELF", detail: "Your highest-damage abilities" },
  group: { label: "GROUP", short: "GRP", detail: "You and verified group members" },
  pet: { label: "PETS", short: "PET", detail: "Charmed and summoned pets" },
  all: { label: "ALL", short: "ALL", detail: "Self, pets, and verified group" },
};

interface SeedMeterRowView {
  key: string;
  name: string;
  detail: string;
  damage: number;
  dps: number;
  style: CSSProperties;
}

interface SeedMeterView {
  rows: readonly SeedMeterRowView[];
  totalDamage: number;
  totalDps: number;
  active: boolean;
}

function seedMeterView(event: EngineSnapshotEvent, mode: SeedMeterMode): SeedMeterView {
  const encounter = event.snapshot.encounters?.at(-1) ?? currentEncounter(event);
  const seconds = Math.max(1, Number(encounter.seconds) || 0);
  const actors = [...(encounter.actors ?? [])];
  const actorRow = (actor: CombatActorView, detail = actorRoleLabels[actor.role]): SeedMeterRowView => ({
    key: `${actor.role}-${actor.name.toLocaleLowerCase()}`,
    name: actor.name,
    detail,
    damage: Math.max(0, Number(actor.encounterDamage) || 0),
    dps: Math.max(0, Number(actor.encounterDps) || (Number(actor.encounterDamage) || 0) / seconds),
    style: actorIdentityStyle(actor.name),
  });
  const selfActor = actors.find((actor) => actor.role === "self");
  const selfDamage = Math.max(0, Number(encounter.personalDamage) || selfActor?.encounterDamage || 0);
  const selfRow: SeedMeterRowView = {
    key: "self",
    name: event.snapshot.character.name !== "?" ? event.snapshot.character.name : "YOU",
    detail: "SELF",
    damage: selfDamage,
    dps: selfDamage / seconds,
    style: actorIdentityStyle(event.snapshot.character.name || "self"),
  };
  const ownCombinedDamage = Math.max(0, Number(encounter.damage) ||
    selfDamage + Math.max(0, encounter.charmedPetDamage) + Math.max(0, encounter.summonedPetDamage));
  const combinedSelfRow: SeedMeterRowView = {
    ...selfRow,
    key: "self-and-pets",
    detail: "SELF + PETS",
    damage: ownCombinedDamage,
    dps: ownCombinedDamage / seconds,
  };
  const groupRows = actors
    .filter((actor) => actor.role === "group" && actor.encounterDamage > 0)
    .map((actor) => actorRow(actor, "GROUP"));
  const petActors = actors.filter((actor) =>
    (actor.role === "charmed" || actor.role === "summoned") && actor.encounterDamage > 0);
  const petRows = petActors.map((actor) => actorRow(actor));
  if (!petActors.some((actor) => actor.role === "charmed") && encounter.charmedPetDamage > 0) {
    petRows.push({
      key: "charmed-pet", name: "Charmed pet", detail: "CHARMED PET",
      damage: encounter.charmedPetDamage, dps: encounter.charmedPetDamage / seconds,
      style: actorIdentityStyle("charmed pet"),
    });
  }
  if (!petActors.some((actor) => actor.role === "summoned") && encounter.summonedPetDamage > 0) {
    petRows.push({
      key: "summoned-pet", name: "Summoned pet", detail: "SUMMONED PET",
      damage: encounter.summonedPetDamage, dps: encounter.summonedPetDamage / seconds,
      style: actorIdentityStyle("summoned pet"),
    });
  }

  let rows: SeedMeterRowView[];
  let totalDamage: number;
  if (mode === "self") {
    const abilityRows = (encounter.sources ?? []).filter((source) => {
      const category = normalizeAbilityCategory(source.category, source.name);
      return source.total > 0 && category !== "pet";
    }).map((source) => {
      const category = normalizeAbilityCategory(source.category, source.name);
      return {
        key: `ability-${source.name.toLocaleLowerCase()}`,
        name: source.name,
        detail: `${abilityCategoryLabel(category)} · ${source.hits} HIT${source.hits === 1 ? "" : "S"} · MAX ${source.maximum.toLocaleString()}`,
        damage: source.total,
        dps: source.total / seconds,
        style: abilityIdentityStyle(category),
      } satisfies SeedMeterRowView;
    });
    rows = abilityRows.length > 0 ? abilityRows : (selfDamage > 0 ? [selfRow] : []);
    totalDamage = selfDamage;
  } else if (mode === "pet") {
    rows = petRows;
    totalDamage = Math.max(0, encounter.charmedPetDamage) + Math.max(0, encounter.summonedPetDamage);
  } else if (mode === "group") {
    rows = [...(ownCombinedDamage > 0 ? [combinedSelfRow] : []), ...groupRows];
    totalDamage = rows.reduce((total, row) => total + row.damage, 0);
  } else {
    rows = [...(selfDamage > 0 ? [selfRow] : []), ...petRows, ...groupRows];
    totalDamage = rows.reduce((total, row) => total + row.damage, 0);
  }
  rows.sort((left, right) => right.damage - left.damage || left.name.localeCompare(right.name));
  return {
    rows,
    totalDamage,
    totalDps: totalDamage / seconds,
    active: Boolean(encounter.active),
  };
}

function SeedControlSurface() {
  const [event, setEvent] = useState(emptyEvent);
  const [settings, setSettings] = useState(defaultDesktopSettings);
  const [layout, setLayout] = useState<SeedCompanionLayout>({ meterRows: 6, controlRows: 6 });
  useEffect(() => {
    document.body.classList.add("control-window");
    const desktop = window.loremasterDesktop;
    if (!desktop) return () => document.body.classList.remove("control-window");
    void desktop.getEngineState().then((state) => {
      if (isEngineSnapshotEvent(state.snapshot)) setEvent(state.snapshot);
      applyTheme(state.settings.uiTheme);
      setSettings(state.settings);
    });
    const removeSnapshot = desktop.onSnapshot((value) => {
      if (isEngineSnapshotEvent(value)) setEvent(value);
    });
    const removeSettings = desktop.onSettings((value) => {
      if (value && typeof value === "object") {
        const next = value as DesktopSettings;
        applyTheme(next.uiTheme);
        setSettings(next);
      }
    });
    const removeLayout = desktop.onCompanionLayout((value) => {
      const meterRows = Math.max(0, Math.min(6, Math.floor(Number(value?.meterRows) || 0)));
      const controlRows = Math.max(0, Math.min(6, Math.floor(Number(value?.controlRows) || 0)));
      setLayout({ meterRows, controlRows });
    });
    return () => {
      removeSnapshot();
      removeSettings();
      removeLayout();
      document.body.classList.remove("control-window");
    };
  }, []);

  const availableControls = event.snapshot.controls.filter((control) =>
    control.state === "active" &&
    (control.kind === "mez" ? settings.alerts.mezTimersEnabled : settings.alerts.lullTimersEnabled),
  );
  const controls = availableControls.slice(0, layout.controlRows);
  const hiddenControls = Math.max(0, availableControls.length - controls.length);
  const meter = seedMeterView(event, settings.seedMeterMode);
  const meterRows = settings.seedMeterVisible ? meter.rows.slice(0, layout.meterRows) : [];
  const hiddenMeterRows = Math.max(0, meter.rows.length - meterRows.length);
  const meterVisible = settings.seedMeterVisible && meterRows.length > 0;
  if (controls.length === 0 && !meterVisible) {
    return <div className="seed-companion-surface empty" />;
  }
  const maximumDamage = Math.max(1, ...meterRows.map((row) => row.damage));
  return <main className="seed-companion-surface" aria-live="polite">
    {meterVisible && <section className={`seed-meter-surface mode-${settings.seedMeterMode}`}
      style={{ "--seed-meter-opacity": settings.seedMeterOpacity } as CSSProperties}
      aria-label={`${seedMeterModeCopy[settings.seedMeterMode].label} DPS meter`}>
      <header>
        <span><i /> DPS · {seedMeterModeCopy[settings.seedMeterMode].label}</span>
        <strong>{meter.active ? "LIVE" : "LAST FIGHT"} · {formatMeterValue(meter.totalDamage)} DMG · {formatDps(meter.totalDps)} DPS</strong>
      </header>
      <div>{meterRows.map((row, index) => <article className="seed-meter-row" style={row.style} key={row.key}>
        <i className="seed-meter-fill" aria-hidden="true" style={{ width: `${Math.max(3, row.damage / maximumDamage * 100)}%` }} />
        <b className="seed-meter-rank">{index + 1}</b>
        <span><b><i className="actor-swatch" aria-hidden="true" />{row.name}</b><small>{row.detail} · {meter.totalDamage > 0 ? Math.round(row.damage / meter.totalDamage * 100) : 0}%</small></span>
        <strong>{formatDps(row.dps)}<small>DPS</small></strong>
        <em>{formatMeterValue(row.damage)}<small>DMG</small></em>
      </article>)}</div>
      {hiddenMeterRows > 0 && <footer>+{hiddenMeterRows} MORE · OPEN ANALYZE FOR THE FULL TABLE</footer>}
    </section>}
    {controls.length > 0 && <section className="seed-control-surface" aria-label="Active mez and lull timers">
      <header><span><i /> CONTROL</span><strong>{hiddenControls > 0
        ? `${controls.length} SHOWN · +${hiddenControls} MORE`
        : `${controls.length} ACTIVE · MEZ / LULL`}</strong></header>
      <div>{controls.map((control, index) => <SeedControlRow
        key={`${control.kind}-${control.target}-${control.landedAt}-${index}`}
        control={control}
      />)}</div>
    </section>}
  </main>;
}

function SettingsToggle({ checked, label, detail, onChange }: {
  checked: boolean; label: string; detail?: string; onChange: (checked: boolean) => void;
}) {
  return <label className="settings-toggle"><span><b>{label}</b>{detail && <small>{detail}</small>}</span>
    <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /><i /></label>;
}

const updatePhaseLabels: Record<UpdateComponentPhase, string> = {
  idle: "READY",
  checking: "CHECKING",
  current: "CURRENT",
  available: "UPDATE READY",
  "not-installed": "NOT INSTALLED",
  modified: "REPAIR AVAILABLE",
  downloading: "DOWNLOADING",
  verifying: "VERIFYING",
  ready: "READY TO APPLY",
  "waiting-for-eq": "CLOSE EVERQUEST",
  installing: "INSTALLING",
  "restart-required": "RESTART REQUIRED",
  error: "NEEDS ATTENTION",
};

function updateActionLabel(phase: UpdateComponentPhase): string | null {
  if (phase === "available") return "UPDATE";
  if (phase === "not-installed") return "INSTALL";
  if (phase === "modified") return "REPAIR";
  if (phase === "ready") return "APPLY";
  if (phase === "waiting-for-eq") return "TRY AGAIN";
  if (phase === "restart-required") return "RESTART";
  return null;
}

function updateProgressPercent(progress: number | null): number | null {
  if (progress === null || !Number.isFinite(progress)) return null;
  const percentage = progress >= 0 && progress <= 1 ? progress * 100 : progress;
  return Math.max(0, Math.min(100, Math.round(percentage)));
}

function formatLastUpdateCheck(value: string): string {
  if (!value) return "NOT CHECKED YET";
  const checked = new Date(value);
  if (Number.isNaN(checked.getTime())) return "CHECKED RECENTLY";
  return `CHECKED ${checked.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).toUpperCase()}`;
}

function UpdateComponentCard({ component, busy, onInstall }: {
  component: UpdateComponentState;
  busy: boolean;
  onInstall: (id: UpdateComponentId) => void;
}) {
  const copy = updateComponentLabels[component.id];
  const action = updateActionLabel(component.phase);
  const progress = updateProgressPercent(component.progress);
  const active = ["checking", "downloading", "verifying", "installing"].includes(component.phase);
  const currentVersion = component.currentVersion || (component.phase === "not-installed" ? "Not installed" : "Unknown");
  const latestVersion = component.latestVersion || "";
  return <section className={`update-component ${component.id} phase-${component.phase}`} aria-label={`${copy.name} update status`}>
    <header>
      <span><small>{copy.eyebrow}</small><b>{copy.name}</b></span>
      <em><i aria-hidden="true" />{updatePhaseLabels[component.phase]}</em>
    </header>
    <div className="update-version-line">
      <span><small>ON THIS PC</small><b>{currentVersion.startsWith("v") || currentVersion === "Unknown" || currentVersion === "Not installed" ? currentVersion : `v${currentVersion}`}</b></span>
      {latestVersion && latestVersion !== component.currentVersion && <span><small>AVAILABLE</small><b>{latestVersion.startsWith("v") ? latestVersion : `v${latestVersion}`}</b></span>}
    </div>
    <p>{component.detail || "Ready to check the official SpinUI release."}</p>
    {(active || progress !== null) && <div className={`update-progress ${progress === null ? "indeterminate" : ""}`} role="progressbar"
      aria-label={`${copy.name} update progress`} aria-valuemin={0} aria-valuemax={100}
      aria-valuenow={progress ?? undefined} aria-valuetext={progress === null ? updatePhaseLabels[component.phase] : `${progress}%`}>
      <i style={{ width: `${progress ?? 18}%` }} /><span>{progress === null ? updatePhaseLabels[component.phase] : `${progress}%`}</span>
    </div>}
    {action && <button className="component-update-action" type="button" disabled={busy}
      onClick={() => onInstall(component.id)}>{action}</button>}
  </section>;
}

function SettingsPanel({ health, raidContext, raidDifficulty, settings, onSettings, onRaidDifficulty, onClose }: {
  health: EngineHealth;
  raidContext?: RaidContextView | null;
  raidDifficulty: number | null;
  settings: DesktopSettings;
  onSettings: (settings: DesktopSettings) => void;
  onRaidDifficulty: (value: number | null) => void;
  onClose: () => void;
}) {
  const [manualPath, setManualPath] = useState(health.configuredPath);
  const [draft, setDraft] = useState(settings);
  const [activeSoundMenu, setActiveSoundMenu] = useState<AlertSoundKind | null>(null);
  const [updateState, setUpdateState] = useState<UpdateCenterState>(() => ({
    ...emptyUpdateState,
    eqRoot: settings.eqRoot,
  }));
  const [checkingUpdate, setCheckingUpdate] = useState(false);
  useEffect(() => {
    if (!activeSoundMenu) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setActiveSoundMenu(null);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [activeSoundMenu]);
  useEffect(() => {
    const desktop = window.loremasterDesktop;
    if (!desktop) return undefined;
    void desktop.getUpdateState().then(setUpdateState).catch(() => undefined);
    return desktop.onUpdateState((state) => setUpdateState(state));
  }, []);
  const chooseFolder = async () => {
    const selected = await window.loremasterDesktop?.chooseLogFolder();
    if (selected) setManualPath(selected);
  };
  const saveManual = async () => {
    await window.loremasterDesktop?.setLogPath(manualPath.trim());
    onClose();
  };
  const checkForUpdates = async () => {
    setCheckingUpdate(true);
    try {
      const result = await window.loremasterDesktop?.checkForUpdates();
      if (result) setUpdateState(result);
    } finally {
      setCheckingUpdate(false);
    }
  };
  const chooseUpdateEqRoot = async () => {
    const state = await window.loremasterDesktop?.chooseUpdateEqRoot();
    if (!state) return;
    setUpdateState(state);
    setDraft((current) => ({ ...current, eqRoot: state.eqRoot }));
  };
  const installUpdates = async (ids: readonly UpdateComponentId[]) => {
    if (ids.length === 0) return;
    const state = await window.loremasterDesktop?.installUpdates(ids);
    if (state) setUpdateState(state);
  };
  const patchDraft = (value: Partial<DesktopSettings>) => setDraft((current) => ({ ...current, ...value }));
  const patchAlerts = (value: Partial<DesktopSettings["alerts"]>) => setDraft((current) => ({
    ...current, alerts: { ...current.alerts, ...value },
  }));
  const patchSoundProfile = (kind: AlertSoundKind, preset: AlertSoundPreset) => {
    setDraft((current) => ({
      ...current,
      alerts: {
        ...current.alerts,
        soundProfiles: {
          ...current.alerts.soundProfiles,
          [kind]: { ...current.alerts.soundProfiles[kind], preset },
        },
      },
    }));
  };
  const chooseCustomSound = async (kind: AlertSoundKind) => {
    const synchronized = await window.loremasterDesktop?.updateSettings({ alerts: draft.alerts });
    if (synchronized) setDraft(synchronized);
    const saved = await window.loremasterDesktop?.chooseAlertSound(kind);
    if (!saved) return;
    setDraft(saved);
    onSettings(saved);
  };
  const selectSoundPreset = async (kind: AlertSoundKind, preset: AlertSoundPreset) => {
    setActiveSoundMenu(null);
    if (preset === "custom" && !draft.alerts.soundProfiles[kind].customPath) {
      await chooseCustomSound(kind);
      return;
    }
    patchSoundProfile(kind, preset);
  };
  const savePreferences = async () => {
    const saved = await window.loremasterDesktop?.updateSettings({
      uiTheme: draft.uiTheme,
      alwaysOnTop: draft.alwaysOnTop,
      fontScale: draft.fontScale,
      composition: draft.composition,
      splitCharmedPetDps: draft.splitCharmedPetDps,
      seedMeterVisible: draft.seedMeterVisible,
      seedMeterMode: draft.seedMeterMode,
      seedMeterOpacity: draft.seedMeterOpacity,
      stanceAdvisorEnabled: draft.stanceAdvisorEnabled,
      itemNetworkLookups: draft.itemNetworkLookups,
      autoCheckUpdates: draft.autoCheckUpdates,
      alerts: draft.alerts,
    });
    if (saved) onSettings(saved);
  };
  const selectTheme = async (uiTheme: LoremasterTheme) => {
    applyTheme(uiTheme);
    patchDraft({ uiTheme });
    const saved = await window.loremasterDesktop?.updateSettings({ uiTheme });
    if (saved) {
      setDraft(saved);
      applyTheme(saved.uiTheme);
      onSettings(saved);
    }
  };
  const changeFontScale = async (delta: number) => {
    const fontScale = Math.max(0.9, Math.min(1.6, Math.round((draft.fontScale + delta) * 20) / 20));
    setDraft((current) => ({ ...current, fontScale }));
    const saved = await window.loremasterDesktop?.updateSettings({ fontScale });
    if (saved) onSettings(saved);
  };
  const changeSeedMeter = async (patch: Partial<Pick<DesktopSettings, "seedMeterVisible" | "seedMeterMode" | "seedMeterOpacity">>) => {
    patchDraft(patch);
    const saved = await window.loremasterDesktop?.updateSettings(patch);
    if (saved) {
      setDraft(saved);
      onSettings(saved);
    }
  };
  const changeAutoCheckUpdates = async (autoCheckUpdates: boolean) => {
    patchDraft({ autoCheckUpdates });
    const saved = await window.loremasterDesktop?.updateSettings({ autoCheckUpdates });
    if (saved) {
      setDraft(saved);
      onSettings(saved);
    }
  };
  const allUpdateIds = updateComponentIds.filter((id) =>
    ["available", "modified", "ready", "waiting-for-eq", "restart-required"].includes(updateState.components[id].phase));
  return (
    <section className="settings-panel" aria-label="Loremaster settings">
      <header><div><small>CONFIGURATION</small><h2>ENGINE + LOGS</h2></div><button onClick={onClose}>DONE</button></header>
      <article className="settings-card appearance-card">
        <label>APPEARANCE</label>
        <p>Match Loremaster to your active SpinUI skin. The choice applies immediately to the HUD, Rune Seed, timers, and alerts.</p>
        <div className="theme-picker" role="radiogroup" aria-label="Loremaster visual theme">
          {([
            { id: "vellum", name: "VELLUM & EMBER", detail: "Matches SpinUI Reloaded" },
            { id: "glass", name: "MIDNIGHT FROST GLASS", detail: "Matches SpinUI Glass" },
          ] as const).map((option) => <button
            className={`theme-option ${option.id} ${draft.uiTheme === option.id ? "selected" : ""}`}
            type="button"
            role="radio"
            aria-checked={draft.uiTheme === option.id}
            key={option.id}
            onClick={() => void selectTheme(option.id)}
          >
            <span className="theme-preview" aria-hidden="true"><i /><i /><i /></span>
            <span className="theme-option-copy"><b>{option.name}</b><small>{option.detail}</small></span>
            <em>{draft.uiTheme === option.id ? "ACTIVE" : "SELECT"}</em>
          </button>)}
        </div>
      </article>
      <article className="settings-card">
        <label htmlFor="eq-path">EVERQUEST DIRECTORY</label>
        <p>Choose the game folder or its Logs folder. Loremaster automatically follows the newest character log.</p>
        <div className="path-row">
          <input id="eq-path" value={manualPath} onChange={(event) => setManualPath(event.target.value)}
            placeholder="Auto-detect common EverQuest locations" />
          <button type="button" onClick={() => void chooseFolder()}>CHANGE</button>
        </div>
        <div className={`engine-status ${health.state}`}><i /><span><b>{health.state.toUpperCase()}</b>{health.detail}</span></div>
        {health.activeLogPath && <code title={health.activeLogPath}>{health.activeLogPath}</code>}
        <div className="settings-actions">
          <button type="button" onClick={() => void saveManual()}>SAVE LOCATION</button>
          <button type="button" onClick={() => { setManualPath(""); void window.loremasterDesktop?.setLogPath(""); }}>USE AUTO-DETECT</button>
        </div>
      </article>
      <article className="settings-card">
        <label>HUD BEHAVIOR</label>
        <p>The Rune Seed stays edge-safe, remembers its location, and can remain visible above EverQuest.</p>
        <label htmlFor="class-composition">CLASS COMPOSITION</label>
        <p>Used when the log does not announce the active trio. Example: PAL/MNK/ENC.</p>
        <input id="class-composition" value={draft.composition}
          maxLength={48} placeholder="PAL/MNK/ENC"
          onChange={(event) => patchDraft({ composition: event.target.value.toUpperCase() })} />
        <SettingsToggle checked={draft.alwaysOnTop} label="Keep Loremaster above EverQuest"
          onChange={(alwaysOnTop) => patchDraft({ alwaysOnTop })} />
        <SettingsToggle checked={draft.splitCharmedPetDps} label="Split self and charmed-pet DPS"
          detail="Shows separate live fight rates while preserving the accurate combined total."
          onChange={(splitCharmedPetDps) => patchDraft({ splitCharmedPetDps })} />
        <div className="seed-meter-setting">
          <SettingsToggle checked={draft.seedMeterVisible} label="Show compact DPS meter beside the Rune Seed"
            detail="The Seed always remains visible. Use its small view and eye controls to switch or hide this click-through meter instantly."
            onChange={(seedMeterVisible) => void changeSeedMeter({ seedMeterVisible })} />
          <div className={`seed-meter-preferences ${draft.seedMeterVisible ? "enabled" : "disabled"}`}>
            <span className="setting-caption">METER VIEW</span>
            <div className="seed-meter-mode-picker" role="radiogroup" aria-label="Compact DPS meter view">
              {seedMeterModes.map((mode) => <button className={draft.seedMeterMode === mode ? "selected" : ""}
                type="button" role="radio" aria-checked={draft.seedMeterMode === mode} key={mode}
                title={seedMeterModeCopy[mode].detail}
                onClick={() => void changeSeedMeter({ seedMeterMode: mode, seedMeterVisible: true })}>
                <b>{seedMeterModeCopy[mode].label}</b><small>{seedMeterModeCopy[mode].detail}</small>
              </button>)}
            </div>
            <label className="seed-meter-opacity" htmlFor="seed-meter-opacity">
              <span><b>BACKGROUND OPACITY</b><small>Text and DPS bars stay crisp at every level.</small></span>
              <input id="seed-meter-opacity" type="range" min="0.35" max="1" step="0.05"
                value={draft.seedMeterOpacity}
                onChange={(event) => patchDraft({ seedMeterOpacity: Number(event.target.value) })}
                onPointerUp={(event) => void changeSeedMeter({ seedMeterOpacity: Number(event.currentTarget.value) })}
                onKeyUp={(event) => void changeSeedMeter({ seedMeterOpacity: Number(event.currentTarget.value) })}
                onBlur={(event) => void changeSeedMeter({ seedMeterOpacity: Number(event.currentTarget.value) })} />
              <strong>{Math.round(draft.seedMeterOpacity * 100)}%</strong>
            </label>
          </div>
        </div>
        <SettingsToggle checked={draft.stanceAdvisorEnabled} label="Enable encounter stance advisor"
          detail="Adds an evidence-based offense or defense lean. Disabled by default; logs cannot see your active stance."
          onChange={(stanceAdvisorEnabled) => patchDraft({ stanceAdvisorEnabled })} />
        <SettingsToggle checked={draft.itemNetworkLookups} label="Allow EQL Wiki item lookups"
          detail="When enabled, only the selected item's name is sent for an EQL Wiki lookup. Cached profiles remain available offline."
          onChange={(itemNetworkLookups) => patchDraft({ itemNetworkLookups })} />
        <div className="font-scale-setting">
          <span><b>HUD TEXT SIZE</b><small>Applies immediately to the Seed, expanded HUD, settings, and alerts.</small></span>
          <div><button type="button" aria-label="Decrease HUD text size" onClick={() => void changeFontScale(-0.05)}>A−</button>
            <strong>{Math.round(draft.fontScale * 100)}%</strong>
            <button type="button" aria-label="Increase HUD text size" onClick={() => void changeFontScale(0.05)}>A+</button></div>
        </div>
      </article>
      <article className="settings-card">
        <label>CROWD CONTROL TIMERS</label>
        <p>Only confirmed own-cast control is timed. Warning thresholds use the guaranteed-safe window.</p>
        <SettingsToggle checked={draft.alerts.mezTimersEnabled} label="Show mez timers"
          onChange={(mezTimersEnabled) => patchAlerts({ mezTimersEnabled })} />
        <SettingsToggle checked={draft.alerts.mezTimerSound} label="Sound when mez becomes urgent"
          onChange={(mezTimerSound) => patchAlerts({ mezTimerSound })} />
        <div className="number-setting"><span>MEZ WARNING</span><input type="number" min="3" max="30"
          value={draft.alerts.mezWarningSeconds} onChange={(event) => patchAlerts({ mezWarningSeconds: Number(event.target.value) })} /><b>SEC</b></div>
        <SettingsToggle checked={draft.alerts.lullTimersEnabled} label="Show lull timers"
          onChange={(lullTimersEnabled) => patchAlerts({ lullTimersEnabled })} />
        <SettingsToggle checked={draft.alerts.lullTimerSound} label="Sound when lull becomes urgent"
          onChange={(lullTimerSound) => patchAlerts({ lullTimerSound })} />
        <div className="number-setting"><span>LULL WARNING</span><input type="number" min="3" max="30"
          value={draft.alerts.lullWarningSeconds} onChange={(event) => patchAlerts({ lullWarningSeconds: Number(event.target.value) })} /><b>SEC</b></div>
      </article>
      <article className="settings-card">
        <label>ALERTS + PLACEMENT</label>
        <p>Alerts use a separate click-through surface so they remain readable beside the Seed without stealing input from EQ.</p>
        <SettingsToggle checked={draft.alerts.alertsEnabled} label="Enable alert banners"
          onChange={(alertsEnabled) => patchAlerts({ alertsEnabled })} />
        <SettingsToggle checked={draft.alerts.alertSound} label="Play alert sound"
          onChange={(alertSound) => patchAlerts({ alertSound })} />
        <div className="anchor-picker" role="group" aria-label="Alert location">
          {(["auto", "above", "below", "left", "right"] as const).map((anchor) => <button type="button"
            className={draft.alerts.alertAnchor === anchor ? "selected" : ""} key={anchor}
            onClick={() => patchAlerts({ alertAnchor: anchor })}>{anchor.toUpperCase()}</button>)}
        </div>
        <div className="trigger-grid">
          <SettingsToggle checked={draft.alerts.alertCharmBreak} label="Charm break" onChange={(alertCharmBreak) => patchAlerts({ alertCharmBreak })} />
          <SettingsToggle checked={draft.alerts.alertTells} label="Incoming tells" onChange={(alertTells) => patchAlerts({ alertTells })} />
          <SettingsToggle checked={draft.alerts.alertSummon} label="Summoned" onChange={(alertSummon) => patchAlerts({ alertSummon })} />
          <SettingsToggle checked={draft.alerts.alertDeath} label="Death" onChange={(alertDeath) => patchAlerts({ alertDeath })} />
          <SettingsToggle checked={draft.alerts.alertBigHit} label="Big hit" onChange={(alertBigHit) => patchAlerts({ alertBigHit })} />
          <SettingsToggle checked={draft.alerts.alertNameCalled} label="Name called" onChange={(alertNameCalled) => patchAlerts({ alertNameCalled })} />
        </div>
        <div className="number-setting"><span>BIG HIT</span><input type="number" min="1" max="999999"
          value={draft.alerts.bigHitThreshold} onChange={(event) => patchAlerts({ bigHitThreshold: Number(event.target.value) })} /><b>DMG</b></div>
        <div className="number-setting"><span>BANNER LIFE</span><input type="number" min="1" max="15"
          value={draft.alerts.alertSeconds} onChange={(event) => patchAlerts({ alertSeconds: Number(event.target.value) })} /><b>SEC</b></div>
        <button type="button" onClick={() => window.loremasterDesktop?.testAlert()}>TEST ALERT</button>
        <button className="save-preferences" type="button" onClick={() => void savePreferences()}>SAVE HUD + ALERT SETTINGS</button>
      </article>
      <article className="settings-card sound-studio">
        <label>ALERT SOUND STUDIO</label>
        <p>Give each alert a distinct cue. Presets are generated locally; custom WAV, MP3, OGG, or M4A files stay on this computer.</p>
        <div className="sound-profile-list">
          {soundKinds.map((kind) => {
            const profile = draft.alerts.soundProfiles[kind.id];
            const customName = profile.customPath.split(/[\\/]/).pop() || "Choose an audio file";
            const presetLabel = soundPresets.find((preset) => preset.id === profile.preset)?.label ?? "Rune Pulse";
            const menuOpen = activeSoundMenu === kind.id;
            return <section className="sound-profile" key={kind.id}>
              <span><b>{kind.label}</b><small>{kind.detail}</small></span>
              <button className={`sound-preset-trigger ${menuOpen ? "open" : ""}`} type="button"
                aria-label={`${kind.label} sound preset`}
                aria-haspopup="listbox" aria-expanded={menuOpen}
                onClick={() => setActiveSoundMenu((current) => current === kind.id ? null : kind.id)}>
                <span>{presetLabel}</span><i aria-hidden="true" />
              </button>
              {profile.preset === "custom" && <button className="sound-file" type="button"
                title={profile.customPath || "No custom sound selected"}
                onClick={() => void chooseCustomSound(kind.id)}>{customName}</button>}
              <button className="sound-preview" type="button" aria-label={`Preview ${kind.label} sound`}
                onClick={() => void previewConfiguredSound(kind.id, profile, kind.id === "tell" || kind.id === "nameCalled" ? "info" : kind.id === "bigHit" || kind.id === "mez" || kind.id === "lull" ? "warn" : "danger")}>▶</button>
              {menuOpen && <div className="sound-preset-menu" role="listbox" aria-label={`${kind.label} sound choices`}>
                {soundPresets.map((preset) => <button type="button" role="option"
                  aria-selected={profile.preset === preset.id}
                  className={profile.preset === preset.id ? "selected" : ""}
                  key={preset.id} onClick={() => void selectSoundPreset(kind.id, preset.id)}>
                  <i aria-hidden="true" /><span>{preset.label}</span>
                </button>)}
              </div>}
            </section>;
          })}
        </div>
        <small className="sound-note">Custom files are validated at playback and limited to 8 MB. If a file moves or cannot be decoded, Loremaster falls back to the matching preset cue.</small>
      </article>
      <article className="settings-card">
        <label>RAID DIFFICULTY FALLBACK</label>
        <p>Loremaster reads D0–D4 directly from the instance-entry log line. Use this manual fallback only when no verified Solo or Group instance context is available.</p>
        {raidContext && <div className="settings-raid-evidence">
          <span><i /> AUTO-DETECTED</span>
          <b>D{raidContext.difficulty} · {raidContext.label} · {raidContext.mode}</b>
          <small>{raidContext.instanceName}</small>
        </div>}
        <div className="difficulty-picker" role="group" aria-label="Manual raid difficulty fallback">
          {raidDifficulties.map((difficulty) => <button
            className={raidDifficulty === difficulty ? "selected" : ""}
            key={difficulty}
            type="button"
            aria-pressed={raidDifficulty === difficulty}
            onClick={() => onRaidDifficulty(difficulty)}
          >D{difficulty}</button>)}
          <button className={raidDifficulty === null ? "selected unset" : "unset"} type="button"
            aria-pressed={raidDifficulty === null} onClick={() => onRaidDifficulty(null)}>UNSET</button>
        </div>
      </article>
      <article className="settings-card">
        <label>BEST-IN-SLOT GEAR PATH</label>
        <p>Build and export your desired character on EQ Legends Tools, then import its JSON here. Run <code className="inline-code">/outputfile inventory</code> in game and import the resulting TXT to find goal pieces already in your bags.</p>
        <div className="gear-import-actions">
          <button type="button" onClick={() => void window.loremasterDesktop?.openExternal(eqToolsCharSheetUrl)}>BUILD ON EQL TOOLS ↗</button>
          <button type="button" onClick={() => void window.loremasterDesktop?.chooseBisBuild()}>IMPORT BUILD JSON</button>
          <button type="button" onClick={() => void window.loremasterDesktop?.chooseInventory()}>IMPORT INVENTORY</button>
          <button type="button" onClick={() => void window.loremasterDesktop?.refreshGearData()}>REFRESH SOURCES</button>
        </div>
        <p className="source-credit">Gear data and character-sheet workflow by <button type="button" onClick={() => void window.loremasterDesktop?.openExternal(eqToolsUrl)}>EQ Legends Tools ↗</button>, created by FlammHammer.</p>
      </article>
      <article className="settings-card compact">
        <label>SESSION</label>
        <p>Reset live encounter and session totals. Weekly boss history remains intact.</p>
        <button type="button" onClick={() => window.loremasterDesktop?.resetEngine()}>RESET SESSION</button>
      </article>
      <article className="settings-card update-center-card">
        <header className="update-center-heading">
          <span><label>SPINUI UPDATE CENTER</label><b>{updateState.currentVersion ? `LOREMASTER v${updateState.currentVersion.replace(/^v/i, "")}` : "LOREMASTER"}</b></span>
          <small>{formatLastUpdateCheck(updateState.lastCheckedAt)}</small>
        </header>
        <p>Keep Loremaster and either SpinUI skin current from one trusted release. Downloads are verified before anything is replaced.</p>
        <div className="update-center-toolbar">
          <button type="button" disabled={checkingUpdate || updateState.busy} onClick={() => void checkForUpdates()}>
            {checkingUpdate || updateState.components.loremaster.phase === "checking" ? "CHECKING..." : "CHECK ALL"}
          </button>
          <button className="update-all" type="button" disabled={updateState.busy || allUpdateIds.length === 0}
            onClick={() => void installUpdates(allUpdateIds)}>{updateState.busy ? "WORKING..." : `UPDATE ALL${allUpdateIds.length ? ` (${allUpdateIds.length})` : ""}`}</button>
        </div>
        <div className="update-install-path">
          <span><small>EVERQUEST INSTALL</small><b title={updateState.eqRoot || draft.eqRoot}>{updateState.eqRoot || draft.eqRoot || "Not selected"}</b></span>
          <button type="button" disabled={updateState.busy} onClick={() => void chooseUpdateEqRoot()}>{updateState.eqRoot || draft.eqRoot ? "CHANGE" : "SELECT"}</button>
        </div>
        <div className="update-component-grid">
          {updateComponentIds.map((id) => <UpdateComponentCard key={id} component={updateState.components[id]}
            busy={updateState.busy} onInstall={(componentId) => void installUpdates([componentId])} />)}
        </div>
        <SettingsToggle checked={draft.autoCheckUpdates} label="Check automatically when Loremaster starts"
          detail="Checks at most once per day. Downloads and installs still require your click."
          onChange={(autoCheckUpdates) => void changeAutoCheckUpdates(autoCheckUpdates)} />
        <div className="update-safety-note"><i aria-hidden="true">&#10003;</i><span><b>YOUR DATA STAYS YOURS</b><small>Updates preserve Loremaster settings, combat and loot history, EverQuest logs, UI layouts, and every other custom skin.</small></span></div>
      </article>
    </section>
  );
}

function presetFallback(severity: "danger" | "warn" | "info"): "rune" | "crystal" | "ember" {
  return severity === "danger" ? "ember" : severity === "warn" ? "rune" : "crystal";
}

function playPresetSignal(preset: "rune" | "crystal" | "ember" | "bell", severity: "danger" | "warn" | "info") {
  try {
    const context = new AudioContext();
    const master = context.createGain();
    master.gain.value = .72;
    master.connect(context.destination);
    const notes = preset === "crystal"
      ? [{ at: 0, hz: 880, length: .32, type: "triangle" as OscillatorType, volume: .11 }, { at: .08, hz: 1320, length: .44, type: "sine" as OscillatorType, volume: .08 }]
      : preset === "ember"
        ? [{ at: 0, hz: 760, length: .18, type: "sawtooth" as OscillatorType, volume: .12 }, { at: .19, hz: 520, length: .24, type: "square" as OscillatorType, volume: .08 }]
        : preset === "bell"
          ? [{ at: 0, hz: 523.25, length: .7, type: "sine" as OscillatorType, volume: .11 }, { at: 0, hz: 1046.5, length: .5, type: "sine" as OscillatorType, volume: .05 }]
          : [{ at: 0, hz: severity === "danger" ? 620 : 440, length: .25, type: "sine" as OscillatorType, volume: .12 }, { at: .13, hz: severity === "danger" ? 820 : 660, length: .3, type: "triangle" as OscillatorType, volume: .09 }];
    let endAt = context.currentTime;
    for (const note of notes) {
      const starts = context.currentTime + note.at;
      const ends = starts + note.length;
      endAt = Math.max(endAt, ends);
      const oscillator = context.createOscillator();
      const gain = context.createGain();
      oscillator.type = note.type;
      oscillator.frequency.value = note.hz;
      gain.gain.setValueAtTime(.0001, starts);
      gain.gain.exponentialRampToValueAtTime(note.volume, starts + .012);
      gain.gain.exponentialRampToValueAtTime(.0001, ends);
      oscillator.connect(gain).connect(master);
      oscillator.start(starts);
      oscillator.stop(ends);
    }
    setTimeout(() => void context.close(), Math.ceil((endAt - context.currentTime + .08) * 1000));
  } catch {
    // The visual alert remains authoritative if an audio device is absent.
  }
}

async function previewConfiguredSound(kind: AlertSoundKind, profile: DesktopSettings["alerts"]["soundProfiles"][AlertSoundKind], severity: "danger" | "warn" | "info") {
  if (profile.preset === "silent") return;
  if (profile.preset === "custom") {
    try {
      const custom = await window.loremasterDesktop?.readAlertSound(kind);
      if (custom?.bytes?.byteLength) {
        const mime = custom.extension === ".wav" ? "audio/wav"
          : custom.extension === ".mp3" ? "audio/mpeg"
            : custom.extension === ".ogg" ? "audio/ogg" : "audio/mp4";
        const blob = new Blob([new Uint8Array(custom.bytes)], { type: mime });
        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);
        audio.volume = .82;
        const release = () => URL.revokeObjectURL(url);
        audio.addEventListener("ended", release, { once: true });
        audio.addEventListener("error", release, { once: true });
        await audio.play();
        return;
      }
    } catch {
      // Moved or unsupported custom files fall back to a clear preset cue.
    }
    playPresetSignal(presetFallback(severity), severity);
    return;
  }
  playPresetSignal(profile.preset, severity);
}

function soundKindForAlert(kind: string, title = ""): AlertSoundKind {
  if (kind === "charmBreak") return "charmBreak";
  if (title.includes("CALLED YOU")) return "nameCalled";
  if (kind === "tell_in" || kind.startsWith("tell")) return "tell";
  if (kind === "summoned") return "summon";
  if (kind === "death_you") return "death";
  if (["melee_in", "nuke_in", "dot_in", "nonmelee_in"].includes(kind)) return "bigHit";
  if (kind === "mez" || kind === "lull") return kind;
  return "default";
}

function AlertSurface() {
  const [event, setEvent] = useState(emptyEvent);
  const [settings, setSettings] = useState(defaultDesktopSettings);
  const [testAlert, setTestAlert] = useState<{ id: string; severity: "danger" | "warn" | "info"; title: string; target: string } | null>(null);
  const sounded = useRef(new Set<string>());
  useEffect(() => {
    document.body.classList.add("alert-window");
    const desktop = window.loremasterDesktop;
    if (!desktop) return () => document.body.classList.remove("alert-window");
    void desktop.getEngineState().then((state) => {
      if (isEngineSnapshotEvent(state.snapshot)) setEvent(state.snapshot);
      applyTheme(state.settings.uiTheme);
      setSettings(state.settings);
    });
    const removeSnapshot = desktop.onSnapshot((value) => {
      if (isEngineSnapshotEvent(value)) setEvent(value);
    });
    const removeSettings = desktop.onSettings((value) => {
      if (value && typeof value === "object") {
        const next = value as DesktopSettings;
        applyTheme(next.uiTheme);
        setSettings(next);
      }
    });
    const removeTest = desktop.onTestAlert((value) => {
      if (!value || typeof value !== "object") return;
      const alert = value as { id?: unknown; severity?: unknown; title?: unknown; target?: unknown };
      if (typeof alert.id !== "string" || typeof alert.title !== "string" || typeof alert.target !== "string") return;
      const severity = ["danger", "warn", "info"].includes(String(alert.severity))
        ? alert.severity as "danger" | "warn" | "info" : "info";
      setTestAlert({ id: alert.id, severity, title: alert.title, target: alert.target });
      setTimeout(() => setTestAlert((current) => current?.id === alert.id ? null : current), 5000);
    });
    return () => { removeSnapshot(); removeSettings(); removeTest(); document.body.classList.remove("alert-window"); };
  }, []);

  const explicit = settings.alerts.alertsEnabled ? event.snapshot.alerts?.[0] : undefined;
  const pendingRaid = event.snapshot.weekly?.pendingRaidTarget;
  const urgentControl = event.snapshot.controls.find((control) =>
    control.state === "active" && control.urgency !== "safe" &&
    (control.kind === "mez" ? settings.alerts.mezTimersEnabled : settings.alerts.lullTimersEnabled));
  const signal = testAlert ? {
    id: testAlert.id, severity: testAlert.severity, eyebrow: "ALERT PREVIEW",
    title: testAlert.title, detail: testAlert.target, kind: "test",
    soundKind: "default" as AlertSoundKind,
    shouldSound: settings.alerts.alertSound,
  } : explicit ? {
    id: explicit.id,
    severity: explicit.severity || "danger" as const,
    eyebrow: explicit.severity === "info" ? "INFORMATION" : "DANGER SIGNAL",
    title: explicit.title,
    detail: explicit.target,
    kind: explicit.kind,
    soundKind: soundKindForAlert(explicit.kind, explicit.title),
    shouldSound: settings.alerts.alertSound,
  } : pendingRaid ? {
    id: `raid-${pendingRaid}`,
    severity: "info" as const,
    eyebrow: "RAID COMPLETION",
    title: pendingRaid,
    detail: "Open Loremaster and confirm D0–D4 to record this lockout.",
    kind: "raid",
    soundKind: "default" as AlertSoundKind,
    shouldSound: settings.alerts.alertSound,
  } : urgentControl ? {
    id: `${urgentControl.kind}-${urgentControl.landedAt}-${urgentControl.urgency}`,
    severity: urgentControl.urgency === "critical" ? "danger" as const : "warn" as const,
    eyebrow: `${urgentControl.kind.toUpperCase()} ${phaseLabel(urgentControl)}`,
    title: urgentControl.target,
    detail: `${Math.ceil(urgentControl.safeRemainingSeconds)}s safe · ${spellLabel(urgentControl)}`,
    kind: urgentControl.kind,
    soundKind: urgentControl.kind as AlertSoundKind,
    shouldSound: urgentControl.kind === "mez" ? settings.alerts.mezTimerSound : settings.alerts.lullTimerSound,
  } : null;

  useEffect(() => {
    if (!signal || !signal.shouldSound || sounded.current.has(signal.id)) return;
    sounded.current.add(signal.id);
    const profile = settings.alerts.soundProfiles?.[signal.soundKind] ?? defaultSoundProfiles[signal.soundKind];
    void previewConfiguredSound(signal.soundKind, profile, signal.severity);
  }, [signal, settings.alerts.soundProfiles]);

  if (!signal) return <div className="alert-surface empty" />;
  return <div className={`alert-surface ${signal.severity} ${signal.kind.startsWith("tell") ? "tell-alert" : ""}`} role="alert">
    <span className="alert-glyph">!</span><div><small>{signal.eyebrow}</small><strong>{signal.title}</strong><p>{signal.detail}</p></div>
    <i className="alert-sweep" />
  </div>;
}

function currentEncounter(event: EngineSnapshotEvent): EncounterView {
  const { combat, breakdown } = event.snapshot;
  return {
    encounterId: `current-${event.sequence}`,
    name: combat.encounterName || "Waiting for combat",
    active: combat.active,
    startedAt: event.occurredAt,
    endedAt: event.occurredAt,
    seconds: combat.fightSeconds,
    damage: combat.fightDamage,
    dps: combat.fightDps,
    personalDamage: combat.fightPersonalDamage,
    charmedPetDamage: combat.fightCharmedPetDamage,
    summonedPetDamage: combat.fightSummonedPetDamage,
    damageTaken: combat.damageTaken,
    healingDone: combat.healingDone,
    healsReceived: 0,
    kills: combat.kills,
    crits: combat.crits,
    misses: combat.misses,
    sources: breakdown.sources,
    targets: breakdown.targets,
    actors: breakdown.actors.map((actor) => ({
      name: actor.name, role: "observed", encounterDamage: actor.total,
      encounterDps: combat.fightSeconds > 0 ? Math.round(actor.total / combat.fightSeconds) : 0,
      encounterHits: actor.hits, encounterMaximum: actor.maximum,
      sessionDamage: actor.total, sessionDps: 0,
      sessionHits: actor.hits, sessionMaximum: actor.maximum,
    })),
    healingSources: [],
    timeline: [],
  };
}

const actorRoleLabels: Record<CombatActorRole, string> = {
  self: "SELF", charmed: "CHARMED PET", summoned: "SUMMONED PET",
  group: "GROUP", observed: "OBSERVED",
};

function stanceAdvice(encounter: EncounterView): { lean: string; detail: string; tone: "offense" | "defense" } {
  const outgoing = encounter.damage / Math.max(1, encounter.seconds);
  const incoming = encounter.damageTaken / Math.max(1, encounter.seconds);
  const pressure = encounter.damageTaken / Math.max(1, encounter.damage);
  if (encounter.damageTaken >= 1000 && (pressure >= 0.5 || incoming >= outgoing * 0.65)) {
    return {
      lean: "DEFENSE LEAN",
      detail: `${Math.round(incoming).toLocaleString()}/s incoming pressure is high relative to ${Math.round(outgoing).toLocaleString()}/s outgoing. Favor mitigation until pressure settles.`,
      tone: "defense",
    };
  }
  return {
    lean: "OFFENSE LEAN",
    detail: `${Math.round(outgoing).toLocaleString()}/s outgoing is ahead of ${Math.round(incoming).toLocaleString()}/s incoming. Favor pressure while the encounter remains stable.`,
    tone: "offense",
  };
}

function ActorDrilldown({ role, encounter, combat }: {
  role: CombatActorRole;
  encounter: EncounterView;
  combat: EngineSnapshotEvent["snapshot"]["combat"];
}) {
  const rows = encounter.actors.filter((actor) => actor.role === role);
  const sessionTotal = combat.personalDamage + combat.charmedPetDamage + combat.summonedPetDamage;
  const sessionSeconds = combat.sessionDps > 0 ? sessionTotal / combat.sessionDps : 0;
  const encounterDamage = role === "self" ? encounter.personalDamage
    : role === "charmed" ? encounter.charmedPetDamage
      : role === "summoned" ? encounter.summonedPetDamage
        : rows.reduce((total, actor) => total + actor.encounterDamage, 0);
  const sessionDamage = role === "self" ? combat.personalDamage
    : role === "charmed" ? combat.charmedPetDamage
      : role === "summoned" ? combat.summonedPetDamage
        : rows.reduce((total, actor) => total + actor.sessionDamage, 0);
  const encounterDps = encounter.seconds > 0 ? encounterDamage / encounter.seconds : 0;
  const sessionDps = sessionSeconds > 0 ? sessionDamage / sessionSeconds : 0;
  const encounterShare = encounter.damage > 0 ? Math.round(encounterDamage / encounter.damage * 100) : 0;
  return <section className={`actor-drilldown ${role}`}>
    <header><div><small>SELECTED CONTRIBUTOR</small><h3>{actorRoleLabels[role]}</h3></div><span>{encounterShare}% FIGHT SHARE</span></header>
    <div className="actor-facts">
      <span><small>ENCOUNTER DPS</small><b>{formatDps(encounterDps)}</b></span>
      <span><small>ENCOUNTER DAMAGE</small><b>{encounterDamage.toLocaleString()}</b></span>
      <span><small>SESSION DPS</small><b>{formatDps(sessionDps)}</b></span>
      <span><small>SESSION DAMAGE</small><b>{sessionDamage.toLocaleString()}</b></span>
    </div>
    {rows.length > 0 && <div className="actor-identities">{rows.map((actor: CombatActorView) => <article style={actorIdentityStyle(actor.name)} key={actor.name}>
      <span><b><i className="actor-swatch" aria-hidden="true" />{actor.name}</b><small>{actor.encounterHits} hits · max {actor.encounterMaximum.toLocaleString()}</small></span>
      <strong>{actor.encounterDamage.toLocaleString()} <small>· {formatDps(actor.encounterDps)}/s</small></strong>
    </article>)}</div>}
    {rows.length === 0 && encounterDamage > 0 && <p className="actor-estimate">The total is proven, but this older log segment does not preserve a unique pet name.</p>}
    {role === "observed" && <p className="actor-estimate">Observed actors are visible in your log and never inflate your personal DPS.</p>}
  </section>;
}

function MainApp() {
  const [event, setEvent] = useState<EngineSnapshotEvent>(emptyEvent);
  const [health, setHealth] = useState<EngineHealth>(initialHealth);
  const [expanded, setExpanded] = useState(false);
  const [analysisOpen, setAnalysisOpen] = useState(false);
  const [lootOpen, setLootOpen] = useState(false);
  const [lootInitialEventId, setLootInitialEventId] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [raidDifficulty, setRaidDifficulty] = useState<number | null>(null);
  const [settings, setSettings] = useState<DesktopSettings>(defaultDesktopSettings);
  const [runtime, setRuntime] = useState({ coldStartMs: 0, residentMemoryMb: 0, platform: "", version: "" });
  const [gearPlan, setGearPlan] = useState<GearPlanView>(emptyGearPlan);
  const [selectedEncounterId, setSelectedEncounterId] = useState<string | null>(null);
  const [selectedActorRole, setSelectedActorRole] = useState<CombatActorRole>("self");

  useEffect(() => {
    const desktop = window.loremasterDesktop;
    if (!desktop) {
      setHealth({ ...initialHealth, state: "error", detail: "Electron bridge is unavailable" });
      return undefined;
    }
    void desktop.getRuntimeMetrics().then(setRuntime);
    void desktop.getEngineState().then((state) => {
      if (isEngineHealth(state.health)) setHealth(state.health);
      if (isEngineSnapshotEvent(state.snapshot)) setEvent(state.snapshot);
      setRaidDifficulty(state.settings.raidDifficulty);
      applyTheme(state.settings.uiTheme);
      setSettings(state.settings);
      if (isGearPlanView(state.gearPlan)) setGearPlan(state.gearPlan);
    });
    const removeSnapshot = desktop.onSnapshot((value) => {
      if (isEngineSnapshotEvent(value)) setEvent(value);
    });
    const removeHealth = desktop.onHealth((value) => {
      if (isEngineHealth(value)) setHealth(value);
    });
    const removeGearPlan = desktop.onGearPlan((value) => {
      if (isGearPlanView(value)) setGearPlan(value);
    });
    const removeSettings = desktop.onSettings((value) => {
      if (value && typeof value === "object") {
        const next = value as DesktopSettings;
        applyTheme(next.uiTheme);
        setSettings(next);
      }
    });
    return () => { removeSnapshot(); removeHealth(); removeGearPlan(); removeSettings(); };
  }, []);

  const setMode = (next: boolean) => {
    setExpanded(next);
    setAnalysisOpen(false);
    setLootOpen(false);
    if (!next) setSettingsOpen(false);
    window.loremasterDesktop?.setExpanded(next);
  };

  const showAnalysis = () => {
    setExpanded(true);
    setSettingsOpen(false);
    setLootOpen(false);
    setAnalysisOpen(true);
    window.loremasterDesktop?.setAnalysis(true);
  };

  const showHud = () => {
    setExpanded(true);
    setAnalysisOpen(false);
    setLootOpen(false);
    window.loremasterDesktop?.setAnalysis(false);
  };

  const showSpoils = (initialEventId?: string) => {
    setExpanded(true);
    setSettingsOpen(false);
    setAnalysisOpen(false);
    setLootInitialEventId(initialEventId ?? null);
    setLootOpen(true);
    window.loremasterDesktop?.setAnalysis(true);
  };

  const changeRaidDifficulty = (value: number | null) => {
    setRaidDifficulty(value);
    void window.loremasterDesktop?.setRaidDifficulty(value);
  };

  const updateAlertSetting = async (alerts: Partial<DesktopSettings["alerts"]>) => {
    const saved = await window.loremasterDesktop?.updateSettings({ alerts });
    if (saved) setSettings(saved);
  };

  const enableStanceAdvisor = async () => {
    const saved = await window.loremasterDesktop?.updateSettings({ stanceAdvisorEnabled: true });
    if (saved) setSettings(saved);
  };

  const toggleSeedMeter = async () => {
    const seedMeterVisible = !settings.seedMeterVisible;
    setSettings((current) => ({ ...current, seedMeterVisible }));
    const saved = await window.loremasterDesktop?.updateSettings({ seedMeterVisible });
    if (saved) setSettings(saved);
  };

  const cycleSeedMeter = async () => {
    const index = seedMeterModes.indexOf(settings.seedMeterMode);
    const seedMeterMode = seedMeterModes[(index + 1) % seedMeterModes.length];
    const patch = { seedMeterMode, seedMeterVisible: true } as const;
    setSettings((current) => ({ ...current, ...patch }));
    const saved = await window.loremasterDesktop?.updateSettings(patch);
    if (saved) setSettings(saved);
  };

  const damageShare = useMemo(() => {
    const total = event.snapshot.combat.personalDamage + event.snapshot.combat.charmedPetDamage;
    return total > 0 ? Math.round(event.snapshot.combat.charmedPetDamage / total * 100) : 0;
  }, [event.snapshot.combat.personalDamage, event.snapshot.combat.charmedPetDamage]);

  if (!expanded) return <RuneSeed event={event} health={health} settings={settings}
    onExpand={() => setMode(true)} onCycleMeter={() => void cycleSeedMeter()}
    onToggleMeter={() => void toggleSeedMeter()} />;

  const { snapshot } = event;
  const weekly = snapshot.weekly;
  if (lootOpen) return <LootChronicle event={event} health={health} gearPlan={gearPlan} initialEventId={lootInitialEventId}
    onAnalyze={showAnalysis} onHud={showHud} onSeed={() => setMode(false)}
    onMinimize={() => window.loremasterDesktop?.minimizeWindow()}
    onClose={() => window.loremasterDesktop?.closeWindow()} />;
  if (analysisOpen) return <CombatArchive event={event} health={health} weekly={weekly}
    onSpoils={() => showSpoils()}
    onHud={showHud} onSeed={() => setMode(false)}
    onMinimize={() => window.loremasterDesktop?.minimizeWindow()}
    onClose={() => window.loremasterDesktop?.closeWindow()} />;
  const encounters = snapshot.encounters?.length ? snapshot.encounters : [currentEncounter(event)];
  const requestedEncounterIndex = selectedEncounterId
    ? encounters.findIndex((encounter) => encounter.encounterId === selectedEncounterId)
    : encounters.length - 1;
  const encounterIndex = requestedEncounterIndex >= 0 ? requestedEncounterIndex : encounters.length - 1;
  const encounter = encounters[encounterIndex];
  const roleDamage: Record<CombatActorRole, number> = {
    self: encounter.personalDamage,
    charmed: encounter.charmedPetDamage,
    summoned: encounter.summonedPetDamage,
    group: encounter.actors.filter((actor) => actor.role === "group")
      .reduce((total, actor) => total + actor.encounterDamage, 0),
    observed: encounter.actors.filter((actor) => actor.role === "observed")
      .reduce((total, actor) => total + actor.encounterDamage, 0),
  };
  const visibleActorRoles = (Object.keys(actorRoleLabels) as CombatActorRole[])
    .filter((role) => role === "self" || roleDamage[role] > 0);
  const advice = stanceAdvice(encounter);
  return (
    <main className="loremaster-shell">
      <header className="masthead">
        <CogMark />
        <div><p>LOREMASTER{runtime.version && <em className="masthead-version">v{runtime.version.replace(/^v/i, "")}</em>}</p><small><b className={health.state} /> {health.state.toUpperCase()} · PROTOCOL {event.protocolVersion}</small></div>
        <div className="masthead-actions">
          <button type="button" onClick={showAnalysis} aria-label="Open full combat breakdown">ANALYZE</button>
          <button type="button" onClick={() => showSpoils()} aria-label="Open observed loot chronicle">SPOILS</button>
          <button type="button" onClick={() => setSettingsOpen((value) => !value)} aria-label="Open settings">SET</button>
          <button type="button" onClick={() => setMode(false)} aria-label="Collapse to Rune Seed">SEED</button>
          <button type="button" onClick={() => window.loremasterDesktop?.minimizeWindow()} aria-label="Minimize Loremaster">—</button>
          <button type="button" onClick={() => window.loremasterDesktop?.closeWindow()} aria-label="Close Loremaster">×</button>
        </div>
      </header>

      {settingsOpen ? <SettingsPanel health={health} raidContext={weekly?.raidContext}
        raidDifficulty={raidDifficulty} settings={settings}
        onSettings={setSettings}
        onRaidDifficulty={changeRaidDifficulty} onClose={() => setSettingsOpen(false)} /> : <>
        <section className="context-line">
          <span><b>{snapshot.character.name !== "?" ? snapshot.character.name : "NO LOG YET"}{snapshot.character.level > 0 ? ` · ${snapshot.character.level}` : ""}</b>{snapshot.character.composition && <em>{snapshot.character.composition.replaceAll(" ", "")}</em>}</span>
          <span>{snapshot.character.zone || health.state}</span>
        </section>

        {health.state !== "live" && <section className={`health-banner ${health.state}`}><i /><div><b>{health.state.toUpperCase()}</b><span>{health.detail}</span></div><button onClick={() => setSettingsOpen(true)}>LOG SETTINGS</button></section>}

        {snapshot.alerts?.map((alert) => <section className={`danger-toast ${alert.severity}`} key={alert.id} role="alert"><span>!</span><div><small>{alert.severity === "info" ? "INFORMATION" : alert.severity === "warn" ? "WARNING" : "DANGER SIGNAL"}</small><strong>{alert.title}{alert.target ? ` · ${alert.target}` : ""}</strong></div></section>)}

        {weekly?.pendingRaidTarget && <section className="raid-confirmation" role="alertdialog" aria-label="Confirm raid difficulty">
          <span className="raid-confirmation-glyph">✓</span>
          <div><small>RAID BOSS DEFEATED</small><strong>{weekly.pendingRaidTarget}</strong><p>Confirm the completed tier to mark this week’s lockout and preserve the clear time.</p></div>
          <div className="raid-confirmation-tiers">{raidDifficulties.map((difficulty) => <button key={difficulty} type="button"
            onClick={() => changeRaidDifficulty(difficulty)}>D{difficulty}</button>)}</div>
        </section>}

        <nav className="encounter-nav" aria-label="Encounter history">
          <button type="button" disabled={encounterIndex <= 0}
            onClick={() => setSelectedEncounterId(encounters[Math.max(0, encounterIndex - 1)].encounterId)}>‹ PREV</button>
          <span><small>{encounter.active ? "LIVE ENCOUNTER" : "ENCOUNTER HISTORY"}</small><b>{encounterIndex + 1} / {encounters.length}</b></span>
          <button type="button" disabled={encounterIndex >= encounters.length - 1}
            onClick={() => setSelectedEncounterId(encounters[Math.min(encounters.length - 1, encounterIndex + 1)].encounterId)}>NEXT ›</button>
          {selectedEncounterId && <button className="encounter-current" type="button" onClick={() => setSelectedEncounterId(null)}>CURRENT</button>}
        </nav>

        <section className="hero-card">
          <p>{encounter.name || "Waiting for combat"}</p>
          <div className="hero-metric">
            <strong>{formatDps(encounter.dps)}</strong><span>DPS</span>
            <aside><b>{formatDps(snapshot.combat.sessionDps)}</b><small>SESSION · {formatDuration(encounter.seconds)} FIGHT</small></aside>
          </div>
          <span className="hero-rule"><i style={{ width: `${Math.min(100, encounter.dps / 5)}%` }} /></span>
          {settings.splitCharmedPetDps && <div className="dps-split" aria-label="Self and pet fight DPS">
            <span><small>SELF</small><b>{formatDps(encounter.seconds > 0 ? encounter.personalDamage / encounter.seconds : 0)}</b></span>
            <span><small>CHARMED</small><b>{formatDps(encounter.seconds > 0 ? encounter.charmedPetDamage / encounter.seconds : 0)}</b></span>
            {encounter.summonedPetDamage > 0 && <span><small>SUMMONED</small><b>{formatDps(encounter.summonedPetDamage / Math.max(1, encounter.seconds))}</b></span>}
          </div>}
        </section>

        <section className="control-deck" aria-live="polite">
          <header><div><small>PRIORITY</small><h2>MEZ + LULL CONTROL</h2></div><span>{snapshot.controls.filter((row) => row.state === "active").length} TRACKED</span></header>
          <div className="control-list">
            {snapshot.controls.length > 0
              ? snapshot.controls.map((control, rowIndex) => <ControlRow key={`${control.kind}-${control.target}-${rowIndex}`} control={control} />)
              : <p className="quiet-state">No active crowd control · evidence channel clear</p>}
          </div>
          {(snapshot.hiddenControlRows > 0 || snapshot.controlAmbiguityCount > 0) && <footer>
            {snapshot.hiddenControlRows > 0 && <span>+{snapshot.hiddenControlRows} overflow</span>}
            {snapshot.controlAmbiguityCount > 0 && <span>? {snapshot.controlAmbiguityCount} ambiguous result</span>}
          </footer>}
        </section>

        <section className="stat-grid">
          <article><small>PERSONAL</small><strong>{snapshot.combat.personalDamage.toLocaleString()}</strong></article>
          <article><small>CHARMED PET</small><strong>{snapshot.combat.charmedPetDamage.toLocaleString()}</strong></article>
          <article><small>PET SHARE</small><strong>{damageShare}%</strong></article>
        </section>

        <details className="breakdown-card">
          <summary><div><small>ENCOUNTER DETAIL</small><h2>FIGHT BREAKDOWN</h2></div><span>{encounter.sources.length} SOURCES</span></summary>
          <div className="fight-facts">
            <span><small>DAMAGE</small><b>{encounter.damage.toLocaleString()}</b></span>
            <span><small>TAKEN</small><b>{encounter.damageTaken.toLocaleString()}</b></span>
            <span><small>HEALING</small><b>{encounter.healingDone.toLocaleString()}</b></span>
            <span><small>CRIT / MISS</small><b>{encounter.crits} / {encounter.misses}</b></span>
          </div>
          <div className="actor-tabs" role="tablist" aria-label="DPS contributor">
            {visibleActorRoles.map((role) => <button className={selectedActorRole === role ? "selected" : ""}
              key={role} type="button" role="tab" aria-selected={selectedActorRole === role}
              onClick={() => setSelectedActorRole(role)}>
              <small>{actorRoleLabels[role]}</small><b>{formatDps(encounter.seconds > 0 ? roleDamage[role] / encounter.seconds : 0)}</b><span>DPS</span>
            </button>)}
          </div>
          <ActorDrilldown role={visibleActorRoles.includes(selectedActorRole) ? selectedActorRole : "self"}
            encounter={encounter} combat={snapshot.combat} />
          <div className="breakdown-columns">
            <section><small>DAMAGE BY ABILITY</small>{encounter.sources.slice(0, 8).map((source) => {
              const category = normalizeAbilityCategory(source.category, source.name);
              return <article className="ability-identity-row" style={abilityIdentityStyle(category)} key={source.name}>
              <span><b><i className="actor-swatch" aria-hidden="true" />{source.name}</b><small>{abilityCategoryLabel(category)} · {source.hits} hits · max {source.maximum.toLocaleString()}</small></span><strong>{source.total.toLocaleString()}</strong>
            </article>})}</section>
            <section><small>TARGETS</small>{encounter.targets.slice(0, 8).map((target) => <article key={target.name}>
              <span><b>{target.name}</b></span><strong>{target.total.toLocaleString()}</strong>
            </article>)}</section>
          </div>
          {!settings.stanceAdvisorEnabled && <button className="stance-advisor-enable" type="button"
            onClick={() => void enableStanceAdvisor()}>SHOW OPTIONAL STANCE LEAN</button>}
          {settings.stanceAdvisorEnabled && <section className={`stance-advisor ${advice.tone}`}>
            <div><small>OPTIONAL STANCE ADVISOR · {snapshot.character.composition || "COMPOSITION UNKNOWN"}</small><strong>{advice.lean}</strong></div>
            <p>{advice.detail}</p>
            <span>Evidence only · Loremaster cannot see your active stance, HP, or mana.</span>
          </section>}
        </details>

        <details className="spoils-card">
          <summary>
            <div><small>ADVENTURE MEMORY · OBSERVED LOOT</small><h2>{snapshot.lootTotalCount ?? snapshot.loot?.reduce((total, row) => total + Math.max(1, row.quantity || 1), 0) ?? 0} ITEMS · {snapshot.lootUniqueCount ?? new Set(snapshot.loot?.map((row) => row.itemKey || row.item.toLocaleLowerCase()) ?? []).size} UNIQUE</h2></div>
            <span className="spoils-card-hint">PREVIEW</span>
          </summary>
          <button className="spoils-open" type="button" onClick={() => showSpoils()}>OPEN CHRONICLE</button>
          {(snapshot.loot?.length ?? 0) > 0 ? <div className="spoils-preview">
            {snapshot.loot!.slice(0, 4).map((loot) => <button type="button" key={loot.eventId} onClick={() => showSpoils(loot.eventId)} aria-label={`Open ${loot.item} in the Spoils Chronicle`}>
              <span><b>{loot.item}{loot.quantity > 1 ? ` ×${loot.quantity}` : ""}</b><small>{loot.looter || "UNKNOWN"} · {loot.zone || "UNKNOWN ZONE"}</small></span>
              <em>{loot.raidTier == null ? "OPEN" : `D${loot.raidTier}`}</em>
            </button>)}
          </div> : <p>No observed loot yet. Loremaster will preserve announced loot as it appears in your local log.</p>}
          <footer>Evidence-backed · unopened or filtered corpse contents are never guessed.</footer>
        </details>

        {weekly && <details className="weekly-card">
          <summary>
            <div><small>RAID RESET · D0–D4</small><h2>{weekly.completedCount} / {weekly.trackedLockoutCount} LOCKOUTS</h2></div>
            <span className={weekly.activeDifficulty == null ? "tier-needed" : ""}>
              {weekly.activeDifficulty == null ? "SET TIER" : `D${weekly.activeDifficulty}`}
            </span>
          </summary>
          {weekly.raidContext && <section className="raid-context-proof" aria-label="Current raid instance evidence">
            <span><i /> LIVE INSTANCE EVIDENCE</span>
            <div><b>{weekly.raidContext.instanceName}</b><strong>D{weekly.raidContext.difficulty} · {weekly.raidContext.mode.toUpperCase()}</strong></div>
            <p>{weekly.raidContext.evidence}</p>
          </section>}
          {weekly.pendingRaidTarget && <p className="raid-pending"><b>{weekly.pendingRaidTarget}</b> is awaiting the D0–D4 confirmation shown above.</p>}
          <div className="raid-grid" aria-label="Weekly D0 through D4 raid lockouts">
            <div className="raid-grid-head"><span>RAID TARGET</span>{raidDifficulties.map((difficulty) => <b key={difficulty}>D{difficulty}</b>)}</div>
            {weekly.raids.map((raid) => <div className="raid-grid-row" key={raid.target}>
              <span title={`${raid.target} · ${raid.zone}`}><b>{raid.target}</b><small>{weekly.activeDifficulty != null && raid.bestSeconds[weekly.activeDifficulty] != null
                ? `D${weekly.activeDifficulty} best ${formatDuration(raid.bestSeconds[weekly.activeDifficulty] ?? 0)}`
                : raid.zone}</small></span>
              {raidDifficulties.map((difficulty) => {
                const completed = Boolean(raid.difficulties[difficulty]);
                const completion = weekly.kills.find((kill) =>
                  kill.target === raid.target && kill.difficulty === difficulty);
                const completionEvidence = completion?.difficulty_source === "log-zone"
                  ? `Auto-detected from ${completion.instance_name || completion.evidence || "instance log evidence"}`
                  : completed ? "Manually confirmed" : "No completion evidence yet";
                const bestTime = raid.bestSeconds[difficulty] != null
                  ? ` · personal best ${formatDuration(raid.bestSeconds[difficulty] ?? 0)}` : "";
                return <button className={completed ? "done" : ""} key={difficulty} type="button"
                  title={`${completionEvidence}${bestTime}`}
                  aria-label={`${raid.target} D${difficulty}: ${completed ? "complete" : "not complete"}${raid.bestSeconds[difficulty] != null ? `, personal best ${formatDuration(raid.bestSeconds[difficulty] ?? 0)}` : ""}`}
                  aria-pressed={completed}
                  onClick={() => void window.loremasterDesktop?.setRaidCompletion(raid.target, difficulty, !completed)}>
                  {completed ? "✓" : "·"}
                </button>;
              })}
            </div>)}
          </div>
          <p className="raid-reset">Resets {weekly.resetLabel}. Click any cell to correct it manually.</p>
        </details>}

        <details className={`gear-card ${gearPlan.status}`}>
          <summary>
            <div><small>GEAR PATH · BIS</small><h2>{gearPlan.status === "ready" ? `${gearPlan.ownedGoalCount} / ${gearPlan.goals.length} GOALS OWNED` : "IMPORT YOUR BUILD"}</h2></div>
            <span>{gearPlan.bagUpgradeCount > 0 ? `${gearPlan.bagUpgradeCount} BAG UPGRADE${gearPlan.bagUpgradeCount === 1 ? "" : "S"}` : `${gearPlan.missingGoalCount} TO FARM`}</span>
          </summary>
          <p>{gearPlan.detail}</p>
          {gearPlan.bagUpgradeCount > 0 && <section className="bag-upgrades">
            <small>READY TO EQUIP</small>
            {gearPlan.goals.filter((goal) => goal.ownership === "bag").map((goal) => <button key={`${goal.slot}-${goal.itemId}`} type="button"
              onClick={() => void window.loremasterDesktop?.openExternal(goal.itemUrl)}>
              <b>{goal.itemName}</b><span>{goal.slot} · {goal.location}</span>
            </button>)}
          </section>}
          {gearPlan.routes.length > 0 && <section className="farm-routes">
            <small>EFFICIENT FARMING ROUTE · MOST GOALS FIRST</small>
            {gearPlan.routes.slice(0, 6).map((route, index) => <article key={route.zone}>
              <b>{index + 1}</b><div><strong>{route.zone}</strong><span>{route.itemNames.join(" · ")}</span>{route.targets.length > 0 && <small>{route.targets.join(", ")}</small>}</div>
            </article>)}
          </section>}
          <footer className="gear-credit"><button type="button" onClick={() => void window.loremasterDesktop?.openExternal(eqToolsCharSheetUrl)}>OPEN CHARACTER SHEET ↗</button><span>Data credit: EQ Legends Tools · FlammHammer</span></footer>
        </details>

        <section className={`alerts-rail ${settings.alerts.alertsEnabled ? "armed" : "muted"}`}>
          <span><i /> {settings.alerts.alertsEnabled ? "ALERTS ARMED" : "ALERTS MUTED"}</span>
          <button className={settings.alerts.alertCharmBreak ? "active" : ""} type="button"
            onClick={() => void updateAlertSetting({ alertCharmBreak: !settings.alerts.alertCharmBreak })}>CHARM</button>
          <button className={settings.alerts.mezTimersEnabled ? "active" : ""} type="button"
            onClick={() => void updateAlertSetting({ mezTimersEnabled: !settings.alerts.mezTimersEnabled })}>MEZ</button>
          <button className={settings.alerts.lullTimersEnabled ? "active" : ""} type="button"
            onClick={() => void updateAlertSetting({ lullTimersEnabled: !settings.alerts.lullTimersEnabled })}>LULL</button>
        </section>
        <footer className="health-line"><span>{health.activeLogPath ? `LIVE · ${health.character}` : "LOCAL ENGINE · NO NETWORK"}</span><span>{runtime.residentMemoryMb || "—"}MB · {runtime.coldStartMs || "—"}ms start</span></footer>
      </>}
    </main>
  );
}

export default function App() {
  const params = new URLSearchParams(window.location.search);
  if (params.get("alert") === "1") return <AlertSurface />;
  if (params.get("controls") === "1") return <SeedControlSurface />;
  return <MainApp />;
}
