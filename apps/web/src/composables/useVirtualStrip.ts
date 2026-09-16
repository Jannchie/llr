import { computed, ref, shallowRef, watch, type Ref } from "vue";
import { useVirtualWindow } from "./useVirtualWindow";

// Windowing for the filmstrip: one row of fixed-size cells, so only the cells
// that intersect the scroll viewport — plus `overscan` either side — are
// mounted, and an index maps to an x by arithmetic rather than measurement.
//
// The strip is a single row by construction (the track hides its vertical
// overflow), so there is no lane count here: the library grid, whose rows vary
// in height, windows with useVirtualRows instead. Both ride useVirtualWindow
// for the scroll/resize plumbing.
export function useVirtualStrip(opts: {
  container: Ref<HTMLElement | null>;
  count: Ref<number>;
  cell: { w: number; h: number; gap: number };
  overscan?: number;
}) {
  const { cell } = opts;
  const overscan = opts.overscan ?? 2;
  const step = cell.w + cell.gap;

  const viewport = ref(0); // scroll extent of the container, px
  const scrollPos = ref(0);
  const totalSize = computed(() => Math.max(0, opts.count.value * step - cell.gap));

  // [start, end) — item indices to render.
  const range = shallowRef({ start: 0, end: 0 });

  function recompute(): void {
    const first = Math.max(0, Math.floor(scrollPos.value / step) - overscan);
    const last = Math.min(opts.count.value, Math.ceil((scrollPos.value + viewport.value) / step) + overscan);
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
  watch(opts.count, recompute);

  function itemStyle(i: number): Record<string, string> {
    return {
      position: "absolute",
      top: "0",
      left: `${i * step}px`,
      width: `${cell.w}px`,
      height: `${cell.h}px`,
    };
  }

  /** Scroll so item `i` is fully visible, moving as little as possible. */
  function scrollToIndex(i: number): void {
    const el = opts.container.value;
    if (!el || i < 0) return;
    const from = i * step;
    const to = from + step - cell.gap;
    const pos = el.scrollLeft;
    const size = el.clientWidth;
    let next = pos;
    if (from < pos) next = from;
    else if (to > pos + size) next = to - size;
    if (next !== pos) el.scrollLeft = next;
  }

  return { range, totalSize, itemStyle, scrollToIndex };
}
