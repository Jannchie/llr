import { ref, shallowRef, watch, type Ref } from "vue";
import { useVirtualWindow } from "./useVirtualWindow";

// Windowing for the filmstrip: one row of items laid out by arithmetic
// (filmLayout.ts), so only the items that intersect the scroll viewport — plus
// `overscan` either side — are mounted. Items may differ in width (group
// dividers are narrower than cells), so the range is found by binary search
// over their x rather than by division.
//
// The strip is a single row by construction (the track hides its vertical
// overflow), so there is no lane count here: the library grid, whose rows vary
// in height, windows with useVirtualRows instead. Both ride useVirtualWindow
// for the scroll/resize plumbing.

export type StripItem = { x: number; w: number };

/** Index of the first item whose right edge reaches past `pos`. */
export function firstItemAfter(items: readonly StripItem[], pos: number): number {
  let lo = 0;
  let hi = items.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (items[mid].x + items[mid].w <= pos) lo = mid + 1; else hi = mid;
  }
  return lo;
}

export function useVirtualStrip(opts: {
  container: Ref<HTMLElement | null>;
  items: Ref<readonly StripItem[]>;
  overscan?: number;
}) {
  const overscan = opts.overscan ?? 2;

  const viewport = ref(0); // scroll extent of the container, px
  const scrollPos = ref(0);

  // [start, end) — item indices to render.
  const range = shallowRef({ start: 0, end: 0 });

  function recompute(): void {
    const items = opts.items.value;
    const first = Math.max(0, firstItemAfter(items, scrollPos.value) - overscan);
    const last = Math.min(items.length, firstItemAfter(items, scrollPos.value + viewport.value) + 1 + overscan);
    if (first !== range.value.start || last !== range.value.end) range.value = { start: first, end: last };
  }

  useVirtualWindow({
    container: opts.container,
    measure: (el) => {
      viewport.value = el.clientWidth;
      scrollPos.value = el.scrollLeft;
      recompute();
    },
  });
  watch(opts.items, recompute);

  /** Scroll so item `i` is fully visible, moving as little as possible. */
  function scrollToIndex(i: number): void {
    const el = opts.container.value;
    const item = opts.items.value[i];
    if (!el || !item) return;
    const from = item.x;
    const to = item.x + item.w;
    const pos = el.scrollLeft;
    const size = el.clientWidth;
    let next = pos;
    if (from < pos) next = from;
    else if (to > pos + size) next = to - size;
    if (next !== pos) el.scrollLeft = next;
  }

  return { range, scrollToIndex };
}
