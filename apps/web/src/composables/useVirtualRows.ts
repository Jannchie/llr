import { computed, ref, shallowRef, watch, type ComputedRef, type Ref } from "vue";
import { useVirtualWindow } from "./useVirtualWindow";

// Windowing for a grid whose rows are not all the same height: group headers on
// top of the bands of cells. Everything is arithmetic — a row's height is known
// before it is mounted — so the visible band is two binary searches over the
// prefix sums, and scrolling a folder of thousands costs a few divisions on the
// next animation frame.
//
// `heights` is one entry per row, excluding the gap between them. The filmstrip
// (fixed cells, one row) windows with useVirtualStrip; both ride
// useVirtualWindow for the scroll/resize plumbing.

/** Top edge of every row, in content coordinates. */
export function topsOf(heights: number[], gap: number): number[] {
  const tops: number[] = new Array(heights.length);
  let y = 0;
  for (let i = 0; i < heights.length; i++) {
    tops[i] = y;
    y += heights[i] + gap;
  }
  return tops;
}

/**
 * The first row whose bottom edge is below `y` — the one under the given
 * offset, and `heights.length` past the end. The gap between rows belongs to
 * the row after it, which is what a pointer in the gap should hit.
 */
export function rowAt(tops: number[], heights: number[], y: number): number {
  let lo = 0;
  let hi = tops.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (tops[mid] + heights[mid] <= y) lo = mid + 1; else hi = mid;
  }
  return lo;
}

export function useVirtualRows(opts: {
  container: Ref<HTMLElement | null>;
  heights: Ref<number[]>;
  /** How many cells fit across, measured here and read by the row layout. */
  lanes: Ref<number>;
  /** Cell width and the gap around it: what one lane costs across the grid. */
  cellWidth: number;
  gap: number;
  overscan?: number;
}): {
  visible: Ref<{ start: number; end: number }>;
  totalSize: ComputedRef<number>;
  tops: ComputedRef<number[]>;
  /** Vertical extent of the scroll container, px — what a PageDown covers. */
  viewport: Ref<number>;
  scrollToRow: (row: number) => void;
} {
  const overscan = opts.overscan ?? 2;
  const lanes = opts.lanes;

  const viewport = ref(0);
  const scrollPos = ref(0);

  const tops = computed(() => topsOf(opts.heights.value, opts.gap));
  const totalSize = computed(() => {
    const heights = opts.heights.value;
    const last = heights.length - 1;
    return last < 0 ? 0 : tops.value[last] + heights[last];
  });

  // [start, end) — row indices to render.
  const visible = shallowRef({ start: 0, end: 0 });

  function recompute(): void {
    const heights = opts.heights.value;
    const start = Math.max(0, rowAt(tops.value, heights, scrollPos.value) - overscan);
    const last = Math.min(heights.length, rowAt(tops.value, heights, scrollPos.value + viewport.value) + 1 + overscan);
    const end = Math.max(start, last);
    if (start !== visible.value.start || end !== visible.value.end) visible.value = { start, end };
  }

  useVirtualWindow({
    container: opts.container,
    measure: (el) => {
      // clientWidth includes the container's own padding; the cells live in the
      // content box inside it, so a lane count taken from the padded width would
      // hang the last column off the right edge.
      const style = getComputedStyle(el);
      const inner = el.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
      lanes.value = Math.max(1, Math.floor((inner + opts.gap) / (opts.cellWidth + opts.gap)));
      viewport.value = el.clientHeight;
      scrollPos.value = el.scrollTop;
      recompute();
    },
  });
  watch(opts.heights, recompute);
  watch(lanes, recompute);

  /** Scroll so row `row` is fully visible, moving as little as possible. */
  function scrollToRow(row: number): void {
    const el = opts.container.value;
    const heights = opts.heights.value;
    if (!el || row < 0 || row >= heights.length) return;
    const from = tops.value[row];
    const to = from + heights[row];
    const pos = el.scrollTop;
    const size = el.clientHeight;
    let next = pos;
    if (from < pos) next = from;
    else if (to > pos + size) next = to - size;
    if (next !== pos) el.scrollTop = next;
  }

  return { visible, totalSize, tops, viewport, scrollToRow };
}
