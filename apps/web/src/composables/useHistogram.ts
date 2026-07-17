import { ref, watch } from "vue";
import { renderHistogram } from "../rendering/histogram";
import type { HistogramView, PipelineRenderer } from "../rendering/pipeline-renderer";

/**
 * The histogram overlay: throttled GPU read-back drawn onto a 2D canvas.
 *
 * Owns the canvas ref, its ResizeObserver, the ~11 Hz throttle, and the
 * busy/retry logic around the async read-back. What to bin comes from the
 * caller: the live renderer, a readiness guard, and an optional crop-mode
 * view override.
 */
export function useHistogram(opts: {
  /** The live preview renderer (null before the first decode). */
  renderer: () => PipelineRenderer | null;
  /** False while there is nothing meaningful to bin (no pixels / no layout). */
  ready: () => boolean;
  /** Histogram render window override (crop editor); undefined = full output. */
  view: () => HistogramView | undefined;
  minIntervalMs?: number;
}) {
  const canvasRef = ref<HTMLCanvasElement | null>(null);
  const MIN_MS = opts.minIntervalMs ?? 90;

  let timer = 0;
  let last = 0;
  let busy = false;

  // The histogram redraws at ~11 Hz during slider drags; reading
  // getBoundingClientRect there forces a layout each time, so track the CSS
  // size with a ResizeObserver instead.
  const size = { w: 0, h: 0 };
  let resizeObs: ResizeObserver | null = null;
  watch(canvasRef, (canvas) => {
    resizeObs?.disconnect();
    resizeObs = null;
    size.w = 0;
    size.h = 0;
    if (canvas) {
      resizeObs = new ResizeObserver((entries) => {
        const r = entries[entries.length - 1]?.contentRect;
        if (!r) return;
        size.w = r.width;
        size.h = r.height;
        schedule();
      });
      resizeObs.observe(canvas);
    }
  });

  async function update(): Promise<void> {
    const canvas = canvasRef.value;
    const renderer = opts.renderer();
    if (!canvas || !renderer || !opts.ready()) return;
    if (busy) { schedule(); return; } // a read is in flight; retry after it
    let w = size.w;
    let h = size.h;
    // Not observed/laid out yet: fall back to a one-off layout read; the
    // ResizeObserver reschedules once the canvas gets its real size.
    if (w <= 0 || h <= 0) {
      const rect = canvas.getBoundingClientRect();
      w = rect.width;
      h = rect.height;
      if (w <= 0 || h <= 0) return;
    }
    busy = true;
    try {
      const bins = await renderer.readHistogram(opts.view());
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      const dpr = window.devicePixelRatio || 1;
      // Assigning width/height resets the canvas even when unchanged — skip it.
      const bw = Math.round(w * dpr);
      const bh = Math.round(h * dpr);
      if (canvas.width !== bw) canvas.width = bw;
      if (canvas.height !== bh) canvas.height = bh;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      renderHistogram(ctx, w, h, bins);
    } catch {
      // Best-effort overlay: a failed read-back (context loss mid-frame) just
      // leaves the previous histogram on screen instead of rejecting unhandled.
    } finally {
      busy = false;
    }
  }

  function schedule(): void {
    if (timer) return; // a trailing update is already pending
    const wait = Math.max(0, MIN_MS - (performance.now() - last));
    timer = window.setTimeout(() => {
      timer = 0;
      last = performance.now();
      void update();
    }, wait);
  }

  /** Drop any pending update (renderer teardown). */
  function cancel(): void {
    if (timer) { clearTimeout(timer); timer = 0; }
  }

  /** Full teardown: pending update + the ResizeObserver. */
  function dispose(): void {
    cancel();
    resizeObs?.disconnect();
    resizeObs = null;
  }

  return { canvasRef, schedule, cancel, dispose };
}
