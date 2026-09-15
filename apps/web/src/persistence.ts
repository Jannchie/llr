// The pre-catalog session store, kept only to migrate it. Before the API grew
// a catalog (apps/api/src/catalog.ts), the library index, each image's edit +
// undo history and the thumbnails lived here in IndexedDB; useCatalog.boot()
// reads it once, hands the photos to the API (POST /catalog/adopt), then
// clears it. New builds never write here.
//
// Layout — one key per record:
//   - `llr.session.v2` — small index: sources, activeId, view settings.
//   - `llr.edit.<id>`  — one image's edit (snapshot + capped undo history).
//   - `llr.thumb.<id>` — one image's downscaled JPEG thumbnail, as a Blob.
//   - `llr.orphans.v1` — id→timestamp ledger of edit records not referenced by
//      the index, so they can be aged out instead of deleted on sight.
// v1 records (IndexedDB and the older localStorage store) migrate on load, so
// a library from any earlier build reaches the catalog.

import { trimEdit, type PersistedEdit } from "./edits";
export type { PersistedEdit } from "./edits";

const DB_NAME = "llr";
const DB_VERSION = 1;
const STORE = "kv";
const SESSION_KEY = "llr.session.v2";
const EDIT_PREFIX = "llr.edit.";
const THUMB_PREFIX = "llr.thumb.";
const ORPHAN_KEY = "llr.orphans.v1";
// Legacy single-record keys (v1), also used by the pre-IndexedDB localStorage store.
const STATE_KEY_V1 = "llr.state.v1";
const THUMBS_KEY_V1 = "llr.thumbs.v1";

// How long an edit record may stay unreferenced by the session index before it
// is deleted — long enough to outlive a stale tab writing an index that omits
// another tab's imports.
const ORPHAN_TTL_MS = 7 * 24 * 60 * 60 * 1000;

export type PersistedSource = {
  id: string;
  name: string;
  size: number;
  embeddedUrl: string;
};

export type PersistedSession<V> = {
  version: 2;
  activeId: string | null;
  viewSettings: V;
  sources: PersistedSource[];
};

// v1 layout, kept only for migration.
type PersistedStateV1<S, V> = {
  version: 1;
  activeId: string | null;
  viewSettings: V;
  sources: PersistedSource[];
  edits: Record<string, PersistedEdit<S>>;
};

// --- IndexedDB plumbing (best-effort: every failure degrades to "no persistence") ---

let dbPromise: Promise<IDBDatabase | null> | null = null;

function openDb(): Promise<IDBDatabase | null> {
  if (!dbPromise) {
    dbPromise = new Promise((resolve) => {
      try {
        const req = indexedDB.open(DB_NAME, DB_VERSION);
        req.onupgradeneeded = () => { req.result.createObjectStore(STORE); };
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => resolve(null);
        req.onblocked = () => resolve(null);
      } catch {
        resolve(null);
      }
    });
  }
  return dbPromise;
}

async function idbGet<T>(key: string): Promise<T | null> {
  const db = await openDb();
  if (!db) return null;
  return new Promise((resolve) => {
    try {
      const req = db.transaction(STORE, "readonly").objectStore(STORE).get(key);
      req.onsuccess = () => resolve((req.result as T | undefined) ?? null);
      req.onerror = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
}

/** All records whose key starts with `prefix`, as an id→value map (prefix stripped). */
async function idbGetByPrefix<T>(prefix: string): Promise<Map<string, T>> {
  const db = await openDb();
  const out = new Map<string, T>();
  if (!db) return out;
  return new Promise((resolve) => {
    try {
      const store = db.transaction(STORE, "readonly").objectStore(STORE);
      const range = IDBKeyRange.bound(prefix, `${prefix}￿`);
      const keysReq = store.getAllKeys(range);
      const valsReq = store.getAll(range);
      valsReq.onsuccess = () => {
        const keys = keysReq.result as string[];
        const vals = valsReq.result as T[];
        for (let i = 0; i < keys.length; i++) out.set(keys[i].slice(prefix.length), vals[i]);
        resolve(out);
      };
      valsReq.onerror = () => resolve(out);
    } catch {
      resolve(out);
    }
  });
}

async function idbSet(key: string, value: unknown): Promise<boolean> {
  const db = await openDb();
  if (!db) return false;
  return new Promise((resolve) => {
    try {
      const tx = db.transaction(STORE, "readwrite");
      tx.objectStore(STORE).put(value, key);
      tx.oncomplete = () => resolve(true);
      tx.onerror = () => resolve(false);
      tx.onabort = () => resolve(false);
    } catch {
      resolve(false);
    }
  });
}

/** Read-modify-write `key` inside a single readwrite transaction. */
async function idbUpdate<T>(key: string, fn: (cur: T | null) => T): Promise<boolean> {
  const db = await openDb();
  if (!db) return false;
  return new Promise((resolve) => {
    try {
      const tx = db.transaction(STORE, "readwrite");
      const store = tx.objectStore(STORE);
      const req = store.get(key);
      req.onsuccess = () => { store.put(fn((req.result as T | undefined) ?? null), key); };
      tx.oncomplete = () => resolve(true);
      tx.onerror = () => resolve(false);
      tx.onabort = () => resolve(false);
    } catch {
      resolve(false);
    }
  });
}

async function idbDelete(...keys: string[]): Promise<void> {
  const db = await openDb();
  if (!db) return;
  await new Promise<void>((resolve) => {
    try {
      const tx = db.transaction(STORE, "readwrite");
      for (const key of keys) tx.objectStore(STORE).delete(key);
      tx.oncomplete = () => resolve();
      tx.onerror = () => resolve();
      tx.onabort = () => resolve();
    } catch {
      resolve();
    }
  });
}

/** Read a record from the pre-IndexedDB localStorage store (migration path). */
function readLegacy<T>(key: string): T | null {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

function dropLegacy(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch {
    // ignore
  }
}

// --- Session state ---

function isValidStateV1<S, V>(s: PersistedStateV1<S, V> | null): s is PersistedStateV1<S, V> {
  return !!s && s.version === 1 && Array.isArray(s.sources);
}

function isValidSession<V>(s: PersistedSession<V> | null): s is PersistedSession<V> {
  return !!s && s.version === 2 && Array.isArray(s.sources);
}

export async function loadSession<S, V>(): Promise<{ session: PersistedSession<V>; edits: Record<string, PersistedEdit<S>> } | null> {
  const session = await idbGet<PersistedSession<V>>(SESSION_KEY);
  if (isValidSession(session)) {
    const stored = await idbGetByPrefix<PersistedEdit<S>>(EDIT_PREFIX);
    const edits: Record<string, PersistedEdit<S>> = {};
    const unreferenced: string[] = [];
    for (const [id, e] of stored) {
      if (session.sources.some(s => s.id === id)) edits[id] = e;
      else unreferenced.push(id);
    }
    void gcOrphans(unreferenced);
    return { session, edits };
  }

  // One-time migration from the v1 single-record layouts (IndexedDB, then localStorage).
  const v1 = (await idbGet<PersistedStateV1<S, V>>(STATE_KEY_V1)) ?? readLegacy<PersistedStateV1<S, V>>(STATE_KEY_V1);
  if (!isValidStateV1(v1)) return null;
  const migrated: PersistedSession<V> = {
    version: 2,
    activeId: v1.activeId,
    viewSettings: v1.viewSettings,
    sources: v1.sources,
  };
  await saveSession(migrated);
  for (const [id, e] of Object.entries(v1.edits)) await saveEdit(id, e);
  await idbDelete(STATE_KEY_V1);
  dropLegacy(STATE_KEY_V1);
  return { session: migrated, edits: v1.edits };
}

// An edit record missing from the index is not proof its image is gone: the
// index is a single record that any tab overwrites wholesale, so a stale tab
// can omit imports another tab made. Deleting on sight turns that recoverable
// mistake into a permanent one — instead, remember when a record first went
// unreferenced and only drop it once it has stayed that way for ORPHAN_TTL_MS.
// (Removing an image deletes its record outright; this path is for the rest.)
async function gcOrphans(unreferencedIds: string[]): Promise<void> {
  const seen = (await idbGet<Record<string, number>>(ORPHAN_KEY)) ?? {};
  const now = Date.now();
  const next: Record<string, number> = {};
  const expired: string[] = [];
  for (const id of unreferencedIds) {
    const since = seen[id] ?? now;
    if (now - since > ORPHAN_TTL_MS) expired.push(EDIT_PREFIX + id);
    else next[id] = since;
  }
  if (expired.length) await idbDelete(...expired);
  await idbSet(ORPHAN_KEY, next);
}

// The index is shared by every tab, so writing this tab's `sources` blindly
// would evict another tab's imports from the library for good (nothing else
// lists them). Union with whatever is stored instead, inside one transaction,
// minus the ids this tab deliberately removed.
export async function saveSession<V>(session: PersistedSession<V>, removedIds: Iterable<string> = []): Promise<void> {
  const removed = new Set(removedIds);
  await idbUpdate<PersistedSession<V>>(SESSION_KEY, (cur) => {
    if (!isValidSession(cur)) return session;
    const mine = new Set(session.sources.map(s => s.id));
    const extra = cur.sources.filter(s => !mine.has(s.id) && !removed.has(s.id));
    return extra.length ? { ...session, sources: [...session.sources, ...extra] } : session;
  });
}

export async function saveEdit<S>(id: string, edit: PersistedEdit<S>): Promise<void> {
  await idbSet(EDIT_PREFIX + id, trimEdit(edit));
}

export async function clearState(): Promise<void> {
  const edits = await idbGetByPrefix<unknown>(EDIT_PREFIX);
  await idbDelete(SESSION_KEY, STATE_KEY_V1, ORPHAN_KEY, ...[...edits.keys()].map(id => EDIT_PREFIX + id));
  dropLegacy(STATE_KEY_V1);
}

// --- Thumbnails ---

/** Drop every cached thumbnail (v2 per-image Blobs and the v1 map). The API serves them now. */
export async function clearThumbs(): Promise<void> {
  const stored = await idbGetByPrefix<unknown>(THUMB_PREFIX);
  await idbDelete(THUMBS_KEY_V1, ...[...stored.keys()].map(id => THUMB_PREFIX + id));
  dropLegacy(THUMBS_KEY_V1);
}
