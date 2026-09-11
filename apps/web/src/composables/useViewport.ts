import { computed, reactive, ref, type Ref } from "vue";

// Pan / zoom / fit state for the image stage. Owns the viewport element ref
// and every transform derived from it; the caller wires the returned handlers
// to the viewport element and re-runs `recomputeFit` when the output dims or
// the viewport size change.
export function useViewport(opts: {
  imageW: Ref<number>;  // displayed output dims (crop result / straighten bbox)
  imageH: Ref<number>;
  srcW: Ref<number>;    // decoded preview dims
  srcH: Ref<number>;
  srcFullW: Ref<number>; // full-resolution dims (for 100% = original 1:1)
  srcFullH: Ref<number>;
  cropMode: Ref<boolean>; // the crop editor owns plain drags; pan needs Space or the middle button
  // Origin of the sub-rectangle the canvas actually renders, in output-frame
  // px (see visibleWindow). The canvas is positioned there rather than at the
  // frame's corner. Null/absent means it covers the whole frame.
  renderOrigin?: Ref<{ x: number; y: number } | null>;
}) {
  const { imageW, imageH, srcW, srcH, srcFullW, srcFullH, cropMode } = opts;

  const zoom = ref(0); // 0 = no image, 1 = fit to viewport
  const pan = reactive({ x: 0, y: 0 });
  const fitScale = ref(1);
  const viewportRef = ref<HTMLDivElement | null>(null);
  const isPanning = ref(false);
  // Space held: drags pan even where a tool (the crop editor) owns the pointer.
  const spaceHeld = ref(false);
  let panStartX = 0;
  let panStartY = 0;
  let panStartPanX = 0;
  let panStartPanY = 0;

  // Where the frame's top-left lands on screen, and the scale it is drawn at.
  // Everything positional below is derived from this so the canvas placement and
  // the visible-window maths cannot drift apart.
  function framePlacement(): { scale: number; tx: number; ty: number; vw: number; vh: number } | null {
    const vp = viewportRef.value;
    if (!vp || !imageW.value || !imageH.value) return null;
    const vw = vp.clientWidth;
    const vh = vp.clientHeight;
    const scale = fitScale.value * zoom.value;
    return {
      scale, vw, vh,
      tx: (vw - imageW.value * scale) / 2 + pan.x,
      ty: (vh - imageH.value * scale) / 2 + pan.y,
    };
  }

  // Places a box covering the whole output frame: the compare overlays, which
  // are always full-frame images.
  const displayTransform = computed(() => {
    const fp = framePlacement();
    return fp ? `translate(${fp.tx}px, ${fp.ty}px) scale(${fp.scale})` : '';
  });

  // Places the canvas, which covers only the render window and so is offset to
  // that window's origin. Identical to displayTransform when there is no window.
  const canvasTransform = computed(() => {
    const fp = framePlacement();
    if (!fp) return '';
    const o = opts.renderOrigin?.value;
    if (!o) return `translate(${fp.tx}px, ${fp.ty}px) scale(${fp.scale})`;
    return `translate(${fp.tx + o.x * fp.scale}px, ${fp.ty + o.y * fp.scale}px) scale(${fp.scale})`;
  });

  /**
   * The part of the output frame the viewport can currently see, in output-frame
   * pixels, grown by `slack` of the visible size on each side so small pans stay
   * inside it. Null when the whole frame is visible — the fit view, where a
   * window would only add arithmetic.
   */
  function visibleWindow(slack = 0): { x: number; y: number; w: number; h: number } | null {
    const fp = framePlacement();
    if (!fp || !(fp.scale > 0)) return null;
    const { scale, tx, ty, vw, vh } = fp;
    const iw = imageW.value, ih = imageH.value;
    let x0 = Math.max(0, -tx / scale);
    let y0 = Math.max(0, -ty / scale);
    let x1 = Math.min(iw, (vw - tx) / scale);
    let y1 = Math.min(ih, (vh - ty) / scale);
    if (!(x1 > x0) || !(y1 > y0)) return null;   // scrolled entirely off-screen
    const mx = (x1 - x0) * slack;
    const my = (y1 - y0) * slack;
    x0 = Math.max(0, Math.floor(x0 - mx));
    y0 = Math.max(0, Math.floor(y0 - my));
    x1 = Math.min(iw, Math.ceil(x1 + mx));
    y1 = Math.min(ih, Math.ceil(y1 + my));
    if (x0 <= 0 && y0 <= 0 && x1 >= iw && y1 >= ih) return null;  // whole frame
    return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
  }

  // Ratio that rescales preview px → original full-res px (≤1; 1 when the preview
  // is already full resolution). Lets us report/zoom relative to the original.
  const previewToFull = computed(() => {
    const previewLong = Math.max(srcW.value, srcH.value);
    const fullLong = Math.max(srcFullW.value, srcFullH.value);
    return previewLong > 0 && fullLong > 0 ? previewLong / fullLong : 1;
  });

  // Zoom % is reported relative to the original full-resolution image (100% =
  // one original pixel per CSS pixel), not the downscaled preview: `scale` maps
  // preview px → screen px, and `previewToFull` rescales that to original px.
  const zoomPercent = computed(() => {
    if (!imageW.value || !imageH.value) return 0;
    return Math.round(fitScale.value * zoom.value * previewToFull.value * 100);
  });

  // Internal zoom factor (1 = fit) that displays the original image at 100%
  // (1:1 original px per CSS px), clamped to the allowed zoom range.
  const fullResZoom = computed(() => {
    const z = fitScale.value > 0 && previewToFull.value > 0
      ? 1 / (fitScale.value * previewToFull.value) : 1;
    return Math.min(50, Math.max(0.1, z));
  });

  function recomputeFit(): void {
    const vp = viewportRef.value;
    if (!vp || !imageW.value || !imageH.value) return;
    const margin = cropMode.value ? 0.86 : 1; // leave room for crop handles
    fitScale.value = Math.min(vp.clientWidth / imageW.value, vp.clientHeight / imageH.value) * margin;
  }

  function startPan(e: MouseEvent): void {
    const middle = e.button === 1;
    if (!(middle || e.button === 0)) return;
    if (cropMode.value && !(middle || spaceHeld.value)) return;
    if (middle) e.preventDefault(); // no autoscroll
    isPanning.value = true;
    panStartX = e.clientX;
    panStartY = e.clientY;
    panStartPanX = pan.x;
    panStartPanY = pan.y;
  }

  function doPan(e: MouseEvent): void {
    if (!isPanning.value) return;
    pan.x = panStartPanX + (e.clientX - panStartX);
    pan.y = panStartPanY + (e.clientY - panStartY);
  }

  function stopPan(): void {
    isPanning.value = false;
  }

  function applyZoom(newZoom: number, mx: number, my: number): void {
    if (!viewportRef.value || !imageW.value || !imageH.value) return;
    const vp = viewportRef.value;
    const vw = vp.clientWidth;
    const vh = vp.clientHeight;
    const oldScale = fitScale.value * zoom.value;
    const newScale = fitScale.value * newZoom;
    const oldTx = (vw - imageW.value * oldScale) / 2 + pan.x;
    const oldTy = (vh - imageH.value * oldScale) / 2 + pan.y;
    const imgX = (mx - oldTx) / oldScale;
    const imgY = (my - oldTy) / oldScale;
    pan.x = (mx - imgX * newScale) - (vw - imageW.value * newScale) / 2;
    pan.y = (my - imgY * newScale) - (vh - imageH.value * newScale) / 2;
    zoom.value = newZoom;
  }

  function onWheel(e: WheelEvent): void {
    if (!imageW.value || !imageH.value || !viewportRef.value) return;
    e.preventDefault();
    const delta = -e.deltaY;
    const factor = delta > 0 ? 1.1 : 1 / 1.1;
    const newZoom = Math.max(0.1, Math.min(50, zoom.value * factor));
    if (newZoom === zoom.value) return;
    const vp = viewportRef.value;
    const rect = vp.getBoundingClientRect();
    applyZoom(newZoom, e.clientX - rect.left, e.clientY - rect.top);
  }

  function zoomIn(): void {
    const newZoom = Math.min(50, zoom.value * 1.25);
    const vp = viewportRef.value;
    if (vp) applyZoom(newZoom, vp.clientWidth / 2, vp.clientHeight / 2);
  }

  function zoomOut(): void {
    const newZoom = Math.max(0.1, zoom.value / 1.25);
    const vp = viewportRef.value;
    if (vp) applyZoom(newZoom, vp.clientWidth / 2, vp.clientHeight / 2);
  }

  function fitView(): void {
    zoom.value = 1;
    pan.x = 0;
    pan.y = 0;
  }

  // Zoom to 100% (original 1:1), anchored at the viewport center.
  function zoomToFull(): void {
    const vp = viewportRef.value;
    if (vp) applyZoom(fullResZoom.value, vp.clientWidth / 2, vp.clientHeight / 2);
  }

  // Toggle between Fit and 100% (original 1:1). From Fit, zoom to 100% anchored at
  // the cursor; from any other zoom, return to a centered Fit.
  function onDoubleClick(e: MouseEvent): void {
    if (cropMode.value || !imageW.value || !imageH.value || !viewportRef.value) return;
    if (Math.abs(zoom.value - 1) < 1e-3) {
      const rect = viewportRef.value.getBoundingClientRect();
      applyZoom(fullResZoom.value, e.clientX - rect.left, e.clientY - rect.top);
    } else {
      fitView();
    }
  }

  return {
    zoom, pan, fitScale, viewportRef, isPanning, spaceHeld,
    displayTransform, canvasTransform, previewToFull, zoomPercent, fullResZoom, visibleWindow,
    recomputeFit, startPan, doPan, stopPan, applyZoom,
    onWheel, zoomIn, zoomOut, fitView, zoomToFull, onDoubleClick,
  };
}
