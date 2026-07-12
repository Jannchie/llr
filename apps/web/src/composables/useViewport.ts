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
  cropMode: Ref<boolean>; // pan/zoom is disabled inside the crop editor
}) {
  const { imageW, imageH, srcW, srcH, srcFullW, srcFullH, cropMode } = opts;

  const zoom = ref(0); // 0 = no image, 1 = fit to viewport
  const pan = reactive({ x: 0, y: 0 });
  const fitScale = ref(1);
  const viewportRef = ref<HTMLDivElement | null>(null);
  const isPanning = ref(false);
  let panStartX = 0;
  let panStartY = 0;
  let panStartPanX = 0;
  let panStartPanY = 0;

  const displayTransform = computed(() => {
    if (!imageW.value || !imageH.value) return '';
    const vp = viewportRef.value;
    if (!vp) return '';
    const vw = vp.clientWidth;
    const vh = vp.clientHeight;
    const scale = fitScale.value * zoom.value;
    const tx = (vw - imageW.value * scale) / 2 + pan.x;
    const ty = (vh - imageH.value * scale) / 2 + pan.y;
    return `translate(${tx}px, ${ty}px) scale(${scale})`;
  });

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
    if (e.button !== 0 || cropMode.value) return;
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
    if (cropMode.value || !imageW.value || !imageH.value || !viewportRef.value) return;
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
    zoom, pan, fitScale, viewportRef, isPanning,
    displayTransform, previewToFull, zoomPercent, fullResZoom,
    recomputeFit, startPan, doPan, stopPan, applyZoom,
    onWheel, zoomIn, zoomOut, fitView, zoomToFull, onDoubleClick,
  };
}
