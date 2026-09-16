import { onBeforeUnmount, onMounted, type Ref } from "vue";

// The plumbing every windowed list in the app needs: a passive scroll listener
// that re-derives the visible range once per animation frame, and a
// ResizeObserver for the size changes a scroll never reports. What "the visible
// range" means is the caller's: `measure` reads the container into the caller's
// own state and rebuilds its range from it, which is the only part that differs
// between the grid (rows of a measured height) and the filmstrip (fixed cells
// along x).
export function useVirtualWindow(opts: {
  container: Ref<HTMLElement | null>;
  /** Read the container's geometry into the caller's refs and re-derive the range. */
  measure: (el: HTMLElement) => void;
}): void {
  let raf = 0;
  function onScroll(): void {
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      const el = opts.container.value;
      if (el) opts.measure(el);
    });
  }

  let ro: ResizeObserver | null = null;
  onMounted(() => {
    const el = opts.container.value;
    if (!el) return;
    el.addEventListener("scroll", onScroll, { passive: true });
    ro = new ResizeObserver(() => {
      const current = opts.container.value;
      if (current) opts.measure(current);
    });
    ro.observe(el);
    opts.measure(el);
  });

  onBeforeUnmount(() => {
    opts.container.value?.removeEventListener("scroll", onScroll);
    ro?.disconnect();
    if (raf) cancelAnimationFrame(raf);
  });
}
