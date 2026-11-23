// Daily copies of the library.
//
// catalog.db is the only place a photo's edit and its history exist, and it
// lives in a cache directory — the kind of place a cleanup tool, a careless
// `rm`, or a bad migration reaches. So once a day a consistent copy goes into
// `backups/` beside it, and the oldest beyond a week's worth are dropped.
// Restoring is replacing catalog.db (and removing its -wal / -shm) with one of
// them while the server is stopped.
//
// Not a substitute for backing up the directory: the imported RAWs are not in
// here, only what was done to them.

import { mkdirSync, readdirSync, renameSync, rmSync } from "node:fs";
import { join } from "node:path";

/** What a backup needs of the catalog: a consistent copy written to a new path. */
export interface BackupSource {
  backupTo(path: string): void;
}

/** How many daily copies are kept. */
export const BACKUPS_KEPT = 7;

const DAILY = /^catalog-\d{4}-\d{2}-\d{2}\.db$/;

/** `catalog-2026-11-23.db`, by the local date: "today's" is the user's today. */
export function dailyBackupName(now: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `catalog-${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}.db`;
}

/**
 * Write today's copy into `dir` unless it is already there, then drop the
 * oldest daily copies beyond `keep`. Returns the path written, or null when
 * today's existed. Only files this function names are ever removed: a copy
 * taken before a migration, or anything else put in the folder, stays.
 */
export function writeDailyBackup(source: BackupSource, dir: string, now = new Date(), keep = BACKUPS_KEPT): string | null {
  mkdirSync(dir, { recursive: true });
  const name = dailyBackupName(now);
  const existing = readdirSync(dir).filter(f => DAILY.test(f));
  if (existing.includes(name)) return null;

  // Under a temporary name first: a copy cut short (a full disk, a kill) must
  // not be left looking like a day's backup, and VACUUM INTO refuses to write
  // over a file, so a leftover from such a run is cleared out of the way.
  const target = join(dir, name);
  const partial = `${target}.partial`;
  rmSync(partial, { force: true });
  try {
    source.backupTo(partial);
    renameSync(partial, target);
  } catch (error) {
    rmSync(partial, { force: true });
    throw error;
  }

  // The names sort by date, so the oldest are first.
  const dated = [...existing, name].sort();
  for (const old of dated.slice(0, Math.max(0, dated.length - keep))) {
    rmSync(join(dir, old), { force: true });
  }
  return target;
}
