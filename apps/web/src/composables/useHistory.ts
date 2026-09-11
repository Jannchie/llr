import { computed, nextTick, ref, shallowRef } from "vue";

// Debounced per-image undo/redo over immutable snapshots. The caller supplies
// how to capture the live edit state and how to push a snapshot back into it;
// switching images swaps the exposed `history`/`historyIndex` refs directly.
export function useHistory<S>(opts: {
  capture: () => S;
  apply: (s: S) => void;
  suspended: () => boolean;    // true while restoring (no commits scheduled)
  onCommitted: () => void;     // persist hook, called after undo/redo applies
  maxEntries?: number;
  debounceMs?: number;
}) {
  const maxEntries = opts.maxEntries ?? 100;
  const debounceMs = opts.debounceMs ?? 300;

  // shallowRef: snapshots are immutable once captured and only length/index are
  // read reactively — deep-proxying up to 100 snapshots per push is pure cost.
  const history = shallowRef<S[]>([]);
  const historyIndex = ref(-1);
  const pendingDirty = ref(false);
  let historyTimer = 0;
  // Who made an entry, by snapshot identity. Not persisted: the stored shape is
  // the bare snapshot list, and a reloaded session reads well enough from the
  // diff between neighbours (see the caller's step labels).
  const labels = new WeakMap<object, string>();
  let pendingLabel: string | undefined;

  const canUndo = computed(() => historyIndex.value > 0 || pendingDirty.value);
  const canRedo = computed(() => historyIndex.value < history.value.length - 1);

  function snapshotsEqual(a: S, b: S): boolean {
    return JSON.stringify(a) === JSON.stringify(b);
  }

  // Commit the current edit state as a new history entry, dropping any redo branch.
  function commit(label = pendingLabel): void {
    pendingDirty.value = false;
    pendingLabel = undefined;
    const snap = opts.capture();
    const cur = history.value[historyIndex.value];
    if (cur && snapshotsEqual(cur, snap)) return;
    if (label) labels.set(snap as object, label);
    const next = history.value.slice(0, historyIndex.value + 1);
    next.push(snap);
    if (next.length > maxEntries) next.shift();
    history.value = next;
    historyIndex.value = next.length - 1;
  }

  // Coalesce rapid edits (slider/curve drags) into one entry, committed after a quiet period.
  function scheduleCommit(label?: string): void {
    if (opts.suspended()) return;
    pendingDirty.value = true;
    if (label) pendingLabel = label;
    if (historyTimer) clearTimeout(historyTimer);
    historyTimer = window.setTimeout(() => { historyTimer = 0; commit(); }, debounceMs);
  }

  function flushPending(label?: string): void {
    if (historyTimer) { clearTimeout(historyTimer); historyTimer = 0; }
    if (pendingDirty.value) commit(label);
  }

  const labelOf = (i: number): string | undefined => labels.get(history.value[i] as object);

  // Jump straight to an entry (the history panel's click), undo/redo being the
  // ±1 special cases.
  function jumpTo(i: number): void {
    flushPending();
    if (i < 0 || i >= history.value.length || i === historyIndex.value) return;
    historyIndex.value = i;
    opts.apply(history.value[i]);
    nextTick(() => opts.onCommitted());
  }

  function undo(): void { flushPending(); jumpTo(historyIndex.value - 1); }
  function redo(): void { flushPending(); jumpTo(historyIndex.value + 1); }

  function init(): void {
    history.value = [opts.capture()];
    historyIndex.value = 0;
    pendingDirty.value = false;
  }

  return {
    history, historyIndex, pendingDirty, canUndo, canRedo,
    commit, scheduleCommit, flushPending, undo, redo, jumpTo, labelOf, init,
  };
}
