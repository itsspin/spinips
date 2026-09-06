import { useEffect, useMemo, useState } from "react";
import type { EngineSnapshotEvent, SpellCatalog } from "./protocol";
import "./leveling.css";

const CLASSES = ["WAR", "CLR", "PAL", "RNG", "SHD", "DRU", "MNK", "BRD", "ROG", "SHM", "NEC", "WIZ", "MAG", "ENC", "BST", "BER"];
const emptyCatalog: SpellCatalog = { status: "missing", spells: [], detail: "Reading your installed spell catalog…", updatedAt: "" };
const number = (value: number | null | undefined, suffix = "") => value == null ? "—" : value.toLocaleString(undefined, { maximumFractionDigits: 2 }) + suffix;
const selectedClasses = (value: string) => value.toUpperCase().split(/[\s/,+]+/).filter(code => CLASSES.includes(code));
function duration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  const minutes = Math.ceil(seconds / 60);
  return minutes >= 60 ? Math.floor(minutes / 60) + "h " + minutes % 60 + "m" : minutes + "m";
}

export default function Leveling({ event, onHud, onSeed }: {
  event: EngineSnapshotEvent; onHud: () => void; onSeed: () => void;
}) {
  const progress = event.snapshot.progression;
  const character = event.snapshot.character;
  const [classes, setClasses] = useState(selectedClasses(character.composition));
  const [level, setLevel] = useState(progress?.level || character.level || 1);
  const [scope, setScope] = useState<"next" | "all">("next");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [catalog, setCatalog] = useState(emptyCatalog);
  const [loading, setLoading] = useState(false);
  const [checkpoint, setCheckpoint] = useState("");
  const [message, setMessage] = useState("");
  const refresh = async () => {
    setLoading(true);
    try { setCatalog(await window.loremasterDesktop?.getSpellCatalog() ?? emptyCatalog); }
    catch { setCatalog({ ...emptyCatalog, status: "error", detail: "Spell catalog unavailable. Try refreshing." }); }
    finally { setLoading(false); }
  };
  useEffect(() => { void refresh(); }, []);
  useEffect(() => {
    setClasses(selectedClasses(character.composition)); setMessage(""); setCheckpoint("");
  }, [character.name, character.composition]);
  useEffect(() => { setLevel(progress?.level || character.level || 1); }, [character.name, character.level, progress?.level]);
  useEffect(() => { setPage(0); }, [classes, level, scope, search]);
  const matches = useMemo(() => catalog.spells.flatMap(spell => {
    const eligible = classes.filter(code => spell.levels[code] && (scope === "all" || spell.levels[code] > level));
    if (!eligible.length || !spell.name.toLowerCase().includes(search.trim().toLowerCase())) return [];
    return [{ ...spell, eligible, unlock: Math.min(...eligible.map(code => spell.levels[code])) }];
  }).sort((a, b) => a.unlock - b.unlock || a.name.localeCompare(b.name)), [catalog.spells, classes, scope, level, search]);
  const visiblePage = Math.min(page, Math.max(0, Math.ceil(matches.length / 20) - 1));
  const recent = progress?.history.slice(-30) ?? [];
  const maximum = Math.max(1, ...recent.map(point => point.percent));
  const recordCheckpoint = async () => {
    const value = Number(checkpoint);
    if (!checkpoint.trim() || !Number.isFinite(value) || value < 0 || value >= 100) { setMessage("Enter your current XP percentage, from 0 to below 100."); return; }
    try {
      const saved = await window.loremasterDesktop?.setXpCheckpoint(level, value);
      setMessage(saved ? "Checkpoint sent. Record another after at least a minute to measure your pace." : "The parser is unavailable. Check your log settings and try again.");
    } catch { setMessage("Checkpoint could not be saved. Try again when the parser is ready."); }
  };
  return <main className="loremaster-shell leveling-shell">
    <header className="masthead"><div><p>THE ASCENT</p><small>LEVELING · SPELLS · AA</small></div>
      <nav className="masthead-actions"><button onClick={onHud}>HUD</button><button onClick={onSeed}>SEED</button>
        <button aria-label="Minimize Loremaster" onClick={() => window.loremasterDesktop?.minimizeWindow()}>—</button>
        <button aria-label="Close Loremaster" onClick={() => window.loremasterDesktop?.closeWindow()}>×</button></nav></header>
    <div className="ascent-content">
      <section className="ascent-hero">
        <div><small>YOUR NEXT CHAPTER</small><h1>{character.name === "?" ? "Begin your ascent" : character.name}</h1><p>{character.composition || "Choose your classes below"} · {character.zone || "Awaiting zone evidence"}</p></div>
        <div className="ascent-level"><small>OBSERVED LEVEL</small><strong>{progress?.level || character.level || "—"}</strong></div>
        <div className="ascent-progress"><span>Current level progress</span><b>{number(progress?.currentPercent, "%")}</b>
          <div role="progressbar" aria-label="Current level XP" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress?.currentPercent ?? undefined}>
            <i style={{ width: (progress?.currentPercent ?? 0) + "%" }} /></div>
          <small>{progress?.currentPercent == null ? "Record an XP checkpoint to establish your position." : "Based on your last checkpoint or observed level-up and measured gains."}</small></div>
      </section>
      <section className="ascent-metrics" aria-label="Leveling session statistics">
        <article><small>XP / HOUR</small><strong>{number(progress?.percentPerHour, "%")}</strong><span>{progress?.rateSource === "checkpoints" ? "Last checkpoint interval" : "Measured session pace"}</span></article>
        <article><small>EST. NEXT LEVEL</small><strong>{duration(progress?.secondsToLevel)}</strong><span>At the measured pace</span></article>
        <article><small>AA EARNED</small><strong>{number(progress?.aaEarned)}</strong><span>{number(progress?.aaPerHour)} points / hour</span></article>
        <article><small>XP GAINS</small><strong>{number(progress?.xpEvents)}</strong><span>{number(progress?.levelsGained)} levels · {duration(progress?.sessionSeconds)}</span></article>
      </section>
      <section className="ascent-checkpoint"><div><h2>Keep your pace accurate</h2><p>When logs omit XP amounts, enter the level and XP % shown in game twice, at least a minute apart. Checkpoint rates expire after 30 minutes. A different level starts a new interval.</p></div>
        <form onSubmit={event => { event.preventDefault(); void recordCheckpoint(); }}>
          <label>Level<input aria-label="Level for spell planning and XP checkpoint" type="number" min={1} max={125} value={level} onChange={event => setLevel(Math.max(1, Math.min(125, Number(event.target.value) || 1)))} /></label>
          <label>XP %<input aria-label="Current XP percentage" type="number" min={0} max={99.999} step="any" value={checkpoint} placeholder="e.g. 42.5" onChange={event => setCheckpoint(event.target.value)} /></label>
          <button type="submit">RECORD XP</button></form>{message && <p role="status">{message}</p>}
      </section>
      {recent.length > 0 && <section className="ascent-history"><header><h2>Recent gains</h2><span>{number(progress?.gainedPercent, "%")} measured this session</span></header>
        <div className="ascent-bars" aria-label="Recent measured XP gains">{recent.map((point, index) => <i key={point.at + index} tabIndex={0} style={{ height: Math.max(5, point.percent / maximum * 100) + "%" }}
          title={new Date(point.at).toLocaleTimeString() + " · +" + number(point.percent, "%")} aria-label={"XP gain " + number(point.percent, "%")} />)}</div></section>}
      <section className="ascent-spells"><header><div><small>PLAN YOUR LOADOUT</small><h2>Spells on the horizon</h2></div><button disabled={loading} onClick={() => void refresh()}>{loading ? "READING…" : "REFRESH"}</button></header>
        <p>{catalog.detail}</p>
        {catalog.status !== "ready" && <button onClick={async () => {
          try { await window.loremasterDesktop?.chooseUpdateEqRoot(); await refresh(); }
          catch { setMessage("The game folder could not be selected. Try again from Settings."); }
        }}>CHOOSE GAME FOLDER</button>}
        <div className="ascent-classes" aria-label="Classes to plan">{CLASSES.map(code => <button key={code} aria-pressed={classes.includes(code)} onClick={() => setClasses(current => current.includes(code) ? current.filter(value => value !== code) : [...current, code])}>{code}</button>)}</div>
        <div className="ascent-filters"><input aria-label="Search upcoming spells" placeholder="Search a spell or ability…" value={search} onChange={event => setSearch(event.target.value)} />
          <button aria-pressed={scope === "next"} onClick={() => setScope("next")}>AFTER LEVEL {level}</button><button aria-pressed={scope === "all"} onClick={() => setScope("all")}>ALL LEVELS</button></div>
        <div className="ascent-spell-list">{matches.slice(visiblePage * 20, visiblePage * 20 + 20).map(spell => <article key={spell.id}>
          <b className="ascent-unlock">{spell.unlock}<small>LEVEL</small></b><div><h3>{spell.name}</h3><p>{spell.eligible.map(code => code + " " + spell.levels[code]).join(" · ")}</p></div>
          <span title="Classification reported by the installed spell data; some dispels use the beneficial flag.">{spell.beneficial ? "BENEFICIAL" : "DETRIMENTAL"}<small>{spell.mana} mana · {spell.castSeconds}s cast</small></span>
          <button aria-label={"Read about " + spell.name} onClick={() => void window.loremasterDesktop?.openExternal("https://eqlwiki.com/" + encodeURIComponent(spell.name.replaceAll(" ", "_")))}>WIKI ↗</button></article>)}</div>
        {!matches.length && <p className="ascent-empty">{!classes.length ? "Select a class to explore its spell path." : catalog.status === "ready" ? "No matching spells in this level range. Try All Levels or another class." : "Your local spell catalog will appear here."}</p>}
        <footer><span>{matches.length} matching spells · eligibility, not learned status</span><button disabled={visiblePage === 0} onClick={() => setPage(visiblePage - 1)}>‹ PREV</button><b>{visiblePage + 1} / {Math.max(1, Math.ceil(matches.length / 20))}</b><button disabled={(visiblePage + 1) * 20 >= matches.length} onClick={() => setPage(visiblePage + 1)}>NEXT ›</button></footer>
      </section>
    </div>
  </main>;
}
