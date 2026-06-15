// Local persistence for the editor session, so a refresh keeps the imported
// image list and each image's edit state + undo history.
//
// Two stores, deliberately separated:
//   - `llr.state.v1`  — small structured JSON (sources, per-image edits, view
//      settings). Rewritten on every edit, so it must stay compact.
//   - `llr.thumbs.v1` — cached downscaled thumbnails (data URLs). Rewritten only
//      when a new thumbnail is generated, so the hot edit-save path stays cheap.
//
// Image pixels are NOT stored here: the RAW files live server-side under
// tmp/sessions/<id> and are re-decoded on demand by their (persisted) id.

const STATE_KEY = "llr.state.v1";
const THUMBS_KEY = "llr.thumbs.v1";

// Cap per-image undo history in the persisted payload to keep localStorage
// bounded; the in-memory history is unaffected.
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

export function loadState<S, V>(): PersistedState<S, V> | null {
  try {
    const raw = localStorage.getItem(STATE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as PersistedState<S, V>;
    if (!parsed || parsed.version !== 1 || !Array.isArray(parsed.sources)) return null;
    return parsed;
  } catch {
    return null;
  }
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

export function saveState<S, V>(state: PersistedState<S, V>): void {
  const edits: Record<string, PersistedEdit<S>> = {};
  for (const [id, e] of Object.entries(state.edits)) edits[id] = trimEdit(e);
  try {
    localStorage.setItem(STATE_KEY, JSON.stringify({ ...state, edits }));
  } catch {
    // Quota exceeded: retry with histories collapsed to just the current edit.
    try {
      const slim: Record<string, PersistedEdit<S>> = {};
      for (const [id, e] of Object.entries(state.edits)) {
        slim[id] = { snapshot: e.snapshot, history: [e.snapshot], historyIndex: 0 };
      }
      localStorage.setItem(STATE_KEY, JSON.stringify({ ...state, edits: slim }));
    } catch {
      // Out of room even slimmed down — drop persistence silently this round.
    }
  }
}

export function clearState(): void {
  try {
    localStorage.removeItem(STATE_KEY);
  } catch {
    // ignore
  }
}

export function loadThumbs(): Record<string, string> {
  try {
    const raw = localStorage.getItem(THUMBS_KEY);
    return raw ? (JSON.parse(raw) as Record<string, string>) : {};
  } catch {
    return {};
  }
}

export function saveThumbs(thumbs: Record<string, string>): void {
  try {
    localStorage.setItem(THUMBS_KEY, JSON.stringify(thumbs));
  } catch {
    // ignore — thumbnails are a nicety, not load-bearing
  }
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
