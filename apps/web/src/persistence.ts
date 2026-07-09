// Local persistence for the editor session, so a refresh keeps the imported
// image list and each image's edit state + undo history.
//
// Backed by IndexedDB: writes are asynchronous (the previous localStorage
// store serialized the whole session synchronously on the main thread, right
// in the gaps of a slider drag) and values are structured-cloned, so there is
// no JSON round-trip and no 5 MB quota shared with the thumbnails. A one-time
// migration imports any existing localStorage session.
//
// Two records, deliberately separated:
//   - `llr.state.v1`  — small structured session (sources, per-image edits,
//      view settings). Rewritten on every edit.
//   - `llr.thumbs.v1` — cached downscaled thumbnails (data URLs). Rewritten only
//      when a new thumbnail is generated, so the hot edit-save path stays cheap.
//
// Image pixels are NOT stored here: the RAW files live server-side under
// tmp/sessions/<id> and are re-decoded on demand by their (persisted) id.

const DB_NAME = "llr";
const DB_VERSION = 1;
const STORE = "kv";
const STATE_KEY = "llr.state.v1";
const THUMBS_KEY = "llr.thumbs.v1";

// Cap per-image undo history in the persisted payload to keep the session
// record bounded; the in-memory history is unaffected.
const PERSIST_HISTORY_CAP = 50;

export type PersistedSource = {
  id: string;
  name: string;
  size: number;
  embeddedUrl: string;
};

export type PersistedEdit<S> = {
  snapshot: S;        // current live edit
  history: S[];       // undo/redo stack
  historyIndex: number;
};

export type PersistedState<S, V> = {
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

async function idbDelete(key: string): Promise<void> {
  const db = await openDb();
  if (!db) return;
  await new Promise<void>((resolve) => {
    try {
      const tx = db.transaction(STORE, "readwrite");
      tx.objectStore(STORE).delete(key);
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

function isValidState<S, V>(s: PersistedState<S, V> | null): s is PersistedState<S, V> {
  return !!s && s.version === 1 && Array.isArray(s.sources);
}

export async function loadState<S, V>(): Promise<PersistedState<S, V> | null> {
  const fromDb = await idbGet<PersistedState<S, V>>(STATE_KEY);
  if (isValidState(fromDb)) return fromDb;
  // One-time migration from the previous localStorage store.
  const legacy = readLegacy<PersistedState<S, V>>(STATE_KEY);
  if (isValidState(legacy)) {
    void idbSet(STATE_KEY, legacy).then((ok) => { if (ok) dropLegacy(STATE_KEY); });
    return legacy;
  }
  return null;
}

function trimEdit<S>(e: PersistedEdit<S>): PersistedEdit<S> {
  if (!Array.isArray(e.history) || e.history.length <= PERSIST_HISTORY_CAP) return e;
  const drop = e.history.length - PERSIST_HISTORY_CAP;
  return {
    snapshot: e.snapshot,
    history: e.history.slice(drop),
    historyIndex: Math.max(0, e.historyIndex - drop),
  };
}

export async function saveState<S, V>(state: PersistedState<S, V>): Promise<void> {
  const edits: Record<string, PersistedEdit<S>> = {};
  for (const [id, e] of Object.entries(state.edits)) edits[id] = trimEdit(e);
  await idbSet(STATE_KEY, { ...state, edits });
}

export async function clearState(): Promise<void> {
  await idbDelete(STATE_KEY);
  dropLegacy(STATE_KEY);
}

// --- Thumbnails ---

export async function loadThumbs(): Promise<Record<string, string>> {
  const fromDb = await idbGet<Record<string, string>>(THUMBS_KEY);
  if (fromDb) return fromDb;
  const legacy = readLegacy<Record<string, string>>(THUMBS_KEY);
  if (legacy) {
    void idbSet(THUMBS_KEY, legacy).then((ok) => { if (ok) dropLegacy(THUMBS_KEY); });
    return legacy;
  }
  return {};
}

export async function saveThumbs(thumbs: Record<string, string>): Promise<void> {
  await idbSet(THUMBS_KEY, thumbs);
}

// Downscale an image URL to a compact JPEG data URL for offline thumbnail
// caching. Returns null if the image can't be loaded/decoded.
export async function generateThumb(url: string, max = 320): Promise<string | null> {
  try {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.src = url;
    await img.decode();
    const longEdge = Math.max(img.naturalWidth, img.naturalHeight) || 1;
    const scale = Math.min(1, max / longEdge);
    const w = Math.max(1, Math.round(img.naturalWidth * scale));
    const h = Math.max(1, Math.round(img.naturalHeight * scale));
    const cvs = document.createElement("canvas");
    cvs.width = w;
    cvs.height = h;
    const ctx = cvs.getContext("2d");
    if (!ctx) return null;
    ctx.drawImage(img, 0, 0, w, h);
    return cvs.toDataURL("image/jpeg", 0.7);
  } catch {
    return null;
  }
}
