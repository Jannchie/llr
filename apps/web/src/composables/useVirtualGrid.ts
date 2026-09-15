import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch, type Ref } from "vue";

// Windowing for a grid (or strip) of fixed-size cells: only the cells that
// intersect the scroll viewport — plus `overscan` rows either side — are
// mounted, so a folder of thousands of photos costs the DOM a screenful.
// Nothing is measured: every cell is `cell.w × cell.h`, so an index maps to a
// position by arithmetic, and the scroll handler is a few divisions on the
// next animation frame.
//
// `axis` is the scroll direction: "y" lays cells out in rows that wrap across
// the width (the grid), "x" in a single row along the width (the filmstrip).
export function useVirtualGrid(opts: {
  container: Ref<HTMLElement | null>;
  count: Ref<number>;
  cell: { w: number; h: number; gap: number };
  axis: "x" | "y";
  overscan?: number;
}) {
  const { cell, axis } = opts;
  const overscan = opts.overscan ?? 2;
  const mainStep = (axis === "y" ? cell.h : cell.w) + cell.gap;
  const crossStep = (axis === "y" ? cell.w : cell.h) + cell.gap;

  // Cells across the non-scrolling axis (columns for a grid, 1 for a strip).
  const lanes = ref(1);
  const viewport = ref(0); // scroll-axis extent of the container, px
  const scrollPos = ref(0);

  const slots = computed(() => Math.ceil(opts.count.value / lanes.value));
  const totalSize = computed(() => Math.max(0, slots.value * mainStep - cell.gap));

  // [start, end) — item indices to render.
  const range = shallowRef({ start: 0, end: 0 });

  function recompute(): void {
    const first = Math.max(0, Math.floor(scrollPos.value / mainStep) - overscan);
    const last = Math.min(slots.value, Math.ceil((scrollPos.value + viewport.value) / mainStep) + overscan);
    const start = first * lanes.value;
    const end = Math.min(opts.count.value, last * lanes.value);
    if (start !== range.value.start || end !== range.value.end) range.value = { start, end };
  }

  let raf = 0;
  function onScroll(): void {
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      const el = opts.container.value;
      if (!el) return;
      scrollPos.value = axis === "y" ? el.scrollTop : el.scrollLeft;
      recompute();
    });
  }

  function measure(): void {
    const el = opts.container.value;
    if (!el) return;
    const cross = axis === "y" ? el.clientWidth : el.clientHeight;
    lanes.value = Math.max(1, Math.floor((cross + cell.gap) / crossStep));
    viewport.value = axis === "y" ? el.clientHeight : el.clientWidth;
    scrollPos.value = axis === "y" ? el.scrollTop : el.scrollLeft;
    recompute();
  }

  let ro: ResizeObserver | null = null;
  onMounted(() => {
    const el = opts.container.value;
    if (!el) return;
    el.addEventListener("scroll", onScroll, { passive: true });
    ro = new ResizeObserver(measure);
    ro.observe(el);
    measure();
  });
  onBeforeUnmount(() => {
    opts.container.value?.removeEventListener("scroll", onScroll);
    ro?.disconnect();
    if (raf) cancelAnimationFrame(raf);
  });
  watch(opts.count, recompute);

  function itemStyle(i: number): Record<string, string> {
    const lane = i % lanes.value;
    const slot = Math.floor(i / lanes.value);
    const main = `${slot * mainStep}px`;
    const cross = `${lane * crossStep}px`;
    return {
      position: "absolute",
      width: `${cell.w}px`,
      height: `${cell.h}px`,
      top: axis === "y" ? main : cross,
      left: axis === "y" ? cross : main,
    };
  }

  /** Scroll so item `i` is fully visible, moving as little as possible. */
  function scrollToIndex(i: number): void {
    const el = opts.container.value;
    if (!el || i < 0) return;
    const slot = Math.floor(i / lanes.value);
    const from = slot * mainStep;
    const to = from + mainStep - cell.gap;
    const pos = axis === "y" ? el.scrollTop : el.scrollLeft;
    const size = axis === "y" ? el.clientHeight : el.clientWidth;
    let next = pos;
    if (from < pos) next = from;
    else if (to > pos + size) next = to - size;
    if (next === pos) return;
    if (axis === "y") el.scrollTop = next; else el.scrollLeft = next;
  }

  return { lanes, range, totalSize, itemStyle, scrollToIndex, measure };
}
