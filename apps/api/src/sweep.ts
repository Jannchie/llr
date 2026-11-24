// The hourly sweep of sessions/: the only code here that deletes a photo's
// files without being asked to.
//
// A directory the catalog does not name is one of three things. A photo that
// was removed while something held its file open (Windows will not delete a
// RAW the worker has mapped): finishing that removal is the sweep's job. An
// import that died halfway. Or — the case that must not cost anyone a photo —
// a directory whose row went missing for a reason nobody intended: a catalog
// restored from an older backup, a migration that went wrong, a cache root
// pointed at a place that already had a sessions/ of its own.
//
// So the removal path says which directories it meant to delete (a mark left
// in them), and only those are deleted. Any other orphan is moved to trash/,
// minus the decode cache that can be rebuilt, and kept there for a week. A
// directory this server would not have named is not touched at all.

import { mkdir, readdir, rename, rm, stat, utimes, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

// linear-*.bin / export-*.jpg are per-request scratch that the request itself
// deletes. Anything still there (the API died mid-render) is orphaned, so it is
// swept on age. Well above the daemon's request timeout. An orphaned directory
// waits as long before anything happens to it: it may be an import in flight.
export const SCRATCH_TTL_MS = 60 * 60 * 1000;
export const TRASH_TTL_MS = 7 * 24 * 60 * 60 * 1000;
const SCRATCH_FILE = /^(?:linear-[\w-]+\.bin|export-[\w-]+\.jpg)$/;
// What a photo's directory holds that the worker can make again from the
// source: its decode cache and per-decode analyses. Hundreds of MB a photo,
// and no reason to keep them in the trash.
const REBUILDABLE = /^cache-.*\.(?:f16|json|tmp)$|\.npz$|^(?:linear|export)-/;
// A photo id is a UUID (randomUUID at import). A directory named anything else
// was not made by this server.
const PHOTO_DIR = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const REMOVED_MARK = ".removed";
const TRASHED_MARK = ".trashed";

/** Record that this directory was meant to be deleted, for the sweep to finish the job. */
export async function markRemoved(dir: string): Promise<void> {
  await writeFile(resolve(dir, REMOVED_MARK), "").catch(() => {});
}

const exists = (path: string) => stat(path).then(() => true, () => false);

/** The names of the directories in `root`: none when it is not there yet, none (and `onError`) when it cannot be read. */
async function subdirs(root: string, onError: (error: unknown) => void): Promise<string[]> {
  try {
    return (await readdir(root, { withFileTypes: true })).filter(entry => entry.isDirectory()).map(entry => entry.name);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") onError(error);
    return [];
  }
}

export type SweepResult = { deleted: string[]; trashed: string[]; expired: string[] };

/**
 * One pass. `known` is the catalog's photo ids, or null while the catalog
 * cannot yet say what it owns (the pre-catalog library has not been adopted),
 * in which case only per-request scratch is swept.
 */
export async function sweepSessions(opts: {
  sessionsRoot: string;
  trashRoot: string;
  known: ReadonlySet<string> | null;
  now?: number;
  warn?: (message: string) => void;
}): Promise<SweepResult> {
  const { sessionsRoot, trashRoot, known } = opts;
  const now = opts.now ?? Date.now();
  const warn = opts.warn ?? (() => {});
  const result: SweepResult = { deleted: [], trashed: [], expired: [] };

  for (const name of await subdirs(sessionsRoot, error => warn(`session cleanup failed: ${message(error)}`))) {
    const dir = resolve(sessionsRoot, name);
    try {
      if (!known || known.has(name)) {
        await sweepScratch(dir, now);
        continue;
      }
      if (now - (await stat(dir)).mtimeMs <= SCRATCH_TTL_MS) continue;
      if (await exists(resolve(dir, REMOVED_MARK))) {
        await rm(dir, { recursive: true, force: true });
        result.deleted.push(name);
      } else if (PHOTO_DIR.test(name)) {
        await moveToTrash(dir, resolve(trashRoot, name), now);
        result.trashed.push(name);
      }
    } catch (error) {
      warn(`session cleanup failed for ${dir}: ${message(error)}`);
    }
  }

  for (const name of await subdirs(trashRoot, error => warn(`trash cleanup failed: ${message(error)}`))) {
    const dir = resolve(trashRoot, name);
    try {
      // Its own mtime is from before it was trashed; the mark is the clock.
      const since = await stat(resolve(dir, TRASHED_MARK)).then(s => s.mtimeMs, () => null);
      if (since === null || now - since <= TRASH_TTL_MS) continue;
      await rm(dir, { recursive: true, force: true });
      result.expired.push(name);
    } catch (error) {
      warn(`trash cleanup failed for ${dir}: ${message(error)}`);
    }
  }
  return result;
}

async function moveToTrash(dir: string, target: string, now: number): Promise<void> {
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    if (entry.isFile() && REBUILDABLE.test(entry.name)) await rm(resolve(dir, entry.name), { force: true });
  }
  await mkdir(resolve(target, ".."), { recursive: true });
  // A second orphan of the same name (restored, orphaned again) replaces the first.
  await rm(target, { recursive: true, force: true });
  await rename(dir, target);
  // The mark's mtime is when the week starts counting, on the sweep's clock.
  const mark = resolve(target, TRASHED_MARK);
  await writeFile(mark, new Date(now).toISOString());
  await utimes(mark, now / 1000, now / 1000);
}

async function sweepScratch(dir: string, now: number): Promise<void> {
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    if (!entry.isFile() || !SCRATCH_FILE.test(entry.name)) continue;
    const path = resolve(dir, entry.name);
    if (now - (await stat(path)).mtimeMs > SCRATCH_TTL_MS) await rm(path, { force: true });
  }
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
