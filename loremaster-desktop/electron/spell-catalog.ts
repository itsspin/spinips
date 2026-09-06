import { readFile, stat } from "node:fs/promises";
import path from "node:path";

export const SPELL_CLASSES = ["WAR", "CLR", "PAL", "RNG", "SHD", "DRU", "MNK", "BRD", "ROG", "SHM", "NEC", "WIZ", "MAG", "ENC", "BST", "BER"] as const;
export interface SpellEntry {
  id: number; name: string; levels: Record<string, number>;
  mana: number; castSeconds: number; beneficial: boolean;
}
export interface SpellCatalog {
  status: "ready" | "missing" | "error"; spells: SpellEntry[]; detail: string; updatedAt: string;
}

export function parseSpellCatalog(text: string): SpellEntry[] {
  const spells: SpellEntry[] = [];
  const seen = new Set<number>();
  for (const line of text.split(/\r?\n/)) {
    const fields = line.split("^");
    if (fields.length < 166 || !/^\d+$/.test(fields[0]) || !fields[1]?.trim()) continue;
    const id = Number(fields[0]);
    if (seen.has(id)) continue;
    // Current Legends client: sixteen class requirements occupy fields 36–51.
    // Reject drift/malformed requirements instead of inventing unlock levels.
    const required = fields.slice(36, 52).map((value) => /^\d+$/.test(value) ? Number(value) : NaN);
    if (required.some((value) => !Number.isInteger(value) || value < 0 || value > 255)) continue;
    const levels = Object.fromEntries(SPELL_CLASSES.flatMap((name, index) =>
      required[index] >= 1 && required[index] <= 125 ? [[name, required[index]]] : []));
    if (!Object.keys(levels).length) continue;
    const mana = Number(fields[14]);
    const cast = Number(fields[8]);
    if (!Number.isFinite(mana) || mana < 0 || !Number.isFinite(cast) || cast < 0) continue;
    seen.add(id);
    spells.push({ id, name: fields[1].trim().slice(0, 160), levels, mana,
      castSeconds: cast / 1000, beneficial: fields[28] !== "0" });
    if (spells.length >= 30_000) break;
  }
  return spells.sort((a, b) => a.name.localeCompare(b.name) || a.id - b.id);
}

export class SpellCatalogService {
  private cached: { key: string; value: SpellCatalog } | null = null;
  private pending: { root: string; value: Promise<SpellCatalog> } | null = null;
  load(root: string): Promise<SpellCatalog> {
    if (this.pending?.root === root) return this.pending.value;
    const value = this.read(root);
    this.pending = { root, value };
    void value.finally(() => { if (this.pending?.value === value) this.pending = null; });
    return value;
  }
  private async read(root: string): Promise<SpellCatalog> {
    if (!root) return { status: "missing", spells: [], updatedAt: "", detail: "Select your EverQuest folder to read its spell catalog." };
    try {
      const filename = path.join(root, "spells_us.txt");
      const info = await stat(filename);
      if (!info.isFile() || info.size > 64 * 1024 * 1024) throw new Error("Spell catalog exceeds the supported file size.");
      const key = `${filename}:${info.size}:${info.mtimeMs}`;
      if (this.cached?.key === key) return this.cached.value;
      const spells = parseSpellCatalog(await readFile(filename, "latin1"));
      if (!spells.length) throw new Error("The installed spell format is not recognized.");
      const value: SpellCatalog = { status: "ready", spells, updatedAt: info.mtime.toISOString(),
        detail: "Requirements from your installed game. Unlock eligibility does not confirm a spell is learned." };
      this.cached = { key, value };
      return value;
    } catch (error) {
      return { status: "error", spells: [], updatedAt: "", detail: error instanceof Error && "code" in error && error.code === "ENOENT"
        ? "No spells_us.txt found. Choose the folder containing eqgame.exe." : "The local spell catalog could not be read. Check your game installation." };
    }
  }
}
