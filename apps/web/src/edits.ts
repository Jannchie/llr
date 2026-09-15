// The persisted shape of one photo's edit — the live snapshot plus its undo
// history — and the two ways it gets trimmed before leaving the browser.
// Shared by the catalog client (which PUTs it to the API) and the legacy
// IndexedDB reader (which migrates it out).

export type PersistedEdit<S> = {
  snapshot: S;        // current live edit
  history: S[];       // undo/redo stack
  historyIndex: number;
};

// Cap per-image undo history in the persisted payload to keep the record
// bounded; the in-memory history is unaffected.
export const PERSIST_HISTORY_CAP = 50;

// A `fetch(..., { keepalive: true })` — the only request that survives the tab
// closing — is refused above ~64 KB in flight, so the unload flush trims to
// well under that.
export const KEEPALIVE_BUDGET_BYTES = 60_000;

/**
 * Keep at most the newest `cap` history entries. The window drops the oldest
 * entries, but never so many that it excludes the entry the snapshot sits on:
 * a record whose snapshot is outside its own history restores an image with
 * someone else's undo stack.
 */
export function trimEdit<S>(e: PersistedEdit<S>, cap = PERSIST_HISTORY_CAP): PersistedEdit<S> {
  if (!Array.isArray(e.history) || e.history.length <= cap) return e;
  const start = Math.min(e.history.length - cap, Math.max(0, e.historyIndex));
  return {
    snapshot: e.snapshot,
    history: e.history.slice(start, start + cap),
    historyIndex: Math.max(0, e.historyIndex - start),
  };
}

/**
 * Shrink an edit until its JSON fits `budget` bytes, dropping history from
 * the oldest end (the same invariant as trimEdit). A snapshot that alone
 * exceeds the budget is returned with just its own history entry — the
 * caller's request may still fail, but nothing smaller would be an edit.
 */
export function fitForKeepalive<S>(e: PersistedEdit<S>, budget = KEEPALIVE_BUDGET_BYTES): PersistedEdit<S> {
  let cap = Math.min(e.history.length, PERSIST_HISTORY_CAP);
  let out = trimEdit(e, cap);
  while (cap > 1 && jsonBytes(out) > budget) {
    // Halve rather than step: each probe serialises the whole record.
    cap = Math.max(1, Math.floor(cap / 2));
    out = trimEdit(e, cap);
  }
  return out;
}

function jsonBytes(v: unknown): number {
  return new TextEncoder().encode(JSON.stringify(v)).length;
}
