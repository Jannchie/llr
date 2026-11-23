// Getting edits to the server, and knowing when that is not happening.
//
// The pixels are rendered in the browser, so every slider keeps working with
// the API gone — and a save that fails used to be dropped back into the dirty
// set without a word, to be tried again only if the user happened to edit
// something else. An afternoon of work could sit unsaved behind a UI that
// looked fine. This owns the dirty set instead: a failed save is retried on a
// timer until it lands, and `failing` says so for as long as it has not.

import { ref } from "vue";
import { fitForKeepalive, trimEdit, type PersistedEdit } from "./edits";

/** Seconds between attempts while saves keep failing; the last one repeats. */
export const SAVE_RETRY_MS = [2_000, 5_000, 15_000, 30_000];

const statusOf = (error: unknown): number | undefined =>
  typeof (error as { status?: unknown })?.status === "number" ? (error as { status: number }).status : undefined;

export function createEditSaver<S>(opts: {
  /** The edit to store for a photo right now; null or undefined when there is none to store. */
  edit: (id: string) => PersistedEdit<S> | null | undefined;
  put: (id: string, edit: PersistedEdit<S>, init?: { keepalive?: boolean }) => Promise<void>;
  /** Called once when saves start failing — not on every retry. */
  onFailing?: (error: unknown) => void;
}) {
  const dirty = new Set<string>();
  // Photos whose last save did not land. Kept apart from `dirty` so that a
  // success elsewhere cannot make a pending failure look resolved.
  const unsaved = new Set<string>();
  const failing = ref(false);
  let retryTimer: ReturnType<typeof setTimeout> | 0 = 0;
  let attempt = 0;

  function settle(): void {
    if (unsaved.size) return;
    failing.value = false;
    attempt = 0;
    if (retryTimer) { clearTimeout(retryTimer); retryTimer = 0; }
  }

  function failed(id: string, error: unknown): void {
    unsaved.add(id);
    dirty.add(id);
    if (!failing.value) {
      failing.value = true;
      opts.onFailing?.(error);
    }
    if (!retryTimer) {
      const wait = SAVE_RETRY_MS[Math.min(attempt, SAVE_RETRY_MS.length - 1)];
      attempt += 1;
      retryTimer = setTimeout(() => { retryTimer = 0; flush(); }, wait);
    }
  }

  async function save(id: string, edit: PersistedEdit<S>): Promise<void> {
    try {
      await opts.put(id, trimEdit(edit));
    } catch (error) {
      // Too large for the server: fifty full snapshots of a heavily brushed
      // edit can be. Trying the same body again cannot work, so the history is
      // cut down until it fits — the edit itself matters more than its undo.
      if (statusOf(error) !== 413) throw error;
      await opts.put(id, fitForKeepalive(edit));
    }
  }

  /** Send every dirty edit. Failures stay dirty and are retried on a timer. */
  function flush(): void {
    const ids = [...dirty];
    dirty.clear();
    for (const id of ids) {
      const edit = opts.edit(id);
      if (!edit) { unsaved.delete(id); continue; }
      save(id, edit).then(
        () => { if (!dirty.has(id)) unsaved.delete(id); settle(); },
        (error: unknown) => {
          // The photo is gone from the library: there is nothing left to save to.
          if (statusOf(error) === 404) { unsaved.delete(id); settle(); return; }
          failed(id, error);
        },
      );
    }
    if (!ids.length) settle();
  }

  /** The tab is closing: only a keepalive request survives, and only a small one. */
  function flushOnUnload(): void {
    for (const id of dirty) {
      const edit = opts.edit(id);
      if (edit) void opts.put(id, fitForKeepalive(edit), { keepalive: true }).catch(() => {});
    }
    dirty.clear();
  }

  return {
    /** True from the first save that fails until every edit has landed. */
    failing,
    mark: (id: string) => { dirty.add(id); },
    /** The photo left the library: drop what was pending for it. */
    forget: (id: string) => { dirty.delete(id); unsaved.delete(id); settle(); },
    flush,
    flushOnUnload,
    /** Try now rather than at the next timer (the user asked). */
    retry: () => { if (retryTimer) { clearTimeout(retryTimer); retryTimer = 0; } flush(); },
  };
}
