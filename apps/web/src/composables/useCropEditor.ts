import { computed, reactive, ref, type Ref } from "vue";
import {
  imageDims, constrainCrop, applyAspectRatio, resolveAspectRatio,
  rotate90, cornersInsideImage, customAspectKey, parseCustomAspect, ratioToFraction,
  defaultCrop,
  type CropState, type Rect,
} from "../rendering/crop";
import { clamp } from "../ui";

// Aspect lock for the crop box. Defaults to the image's own ratio; part of
// the per-image snapshot so it survives image switches / undo / persistence.
export const DEFAULT_ASPECT = "orig";

export type CropHandle = "l" | "r" | "t" | "b" | "tl" | "tr" | "bl" | "br";

// Crop & straighten editor: aspect controls, the SVG overlay geometry
// (output-frame coordinate space, matching the overlay's viewBox), and the
// move/resize/rotate drag state machine. The caller owns the `crop` state
// (it is part of the per-image snapshot) and the render bridge — it writes
// `cropBBox`/`cropRenderScale` when it renders the editor window.
export function useCropEditor(opts: {
  crop: CropState;          // reactive
  srcW: Ref<number>;
  srcH: Ref<number>;
  fitScale: Ref<number>;
  onDragEnd: () => void;    // commit pending history when a drag finishes
}) {
  const { crop, srcW, srcH, fitScale } = opts;

  const cropAspect = ref<string>(DEFAULT_ASPECT);
  // Crop-editor render window (output-frame px) + the canvas scale used to draw
  // it, kept so the overlay can map between screen, output-frame and crop-box
  // space. Written by the caller's editor render.
  const cropBBox = reactive<Rect>({ x: 0, y: 0, w: 1, h: 1 });
  const cropRenderScale = ref(1);
  const cropOverlayRef = ref<SVGSVGElement | null>(null);

  function currentImageDims(): [number, number] {
    return imageDims(srcW.value, srcH.value, crop.orientation);
  }

  function setCrop(patch: Partial<CropState>): void {
    Object.assign(crop, patch);
  }

  function resetCrop(): void {
    Object.assign(crop, defaultCrop());
    cropAspect.value = "free";
  }

  // ── Controls (aspect / straighten / rotate / flip) ──

  const lockedRatio = computed(() => resolveAspectRatio(cropAspect.value, srcW.value, srcH.value, crop));

  // Custom aspect ("Enter Custom…", Lightroom-style). The two numbers live in the
  // aspect key itself ("custom:16:10") so they persist/undo with the snapshot.
  const customAspect = computed(() => parseCustomAspect(cropAspect.value));

  function selectAspect(key: string): void {
    if (key === "custom") {
      // Seed the custom inputs from the current lock (or the box shape for free).
      const [iw, ih] = currentImageDims();
      const r = lockedRatio.value ?? (crop.w * iw) / (crop.h * ih);
      const [fw, fh] = ratioToFraction(Math.max(r, 1 / r));
      key = customAspectKey(fw, fh);
    }
    cropAspect.value = key;
    const ratio = resolveAspectRatio(key, srcW.value, srcH.value, crop);
    if (ratio != null) Object.assign(crop, applyAspectRatio(crop, ratio, srcW.value, srcH.value));
  }

  function setCustomAspect(w: number, h: number): void {
    if (!Number.isFinite(w) || !Number.isFinite(h) || w <= 0 || h <= 0) return;
    selectAspect(customAspectKey(w, h));
  }

  // Swap the crop box between landscape/portrait (Lightroom's X). The aspect key
  // is orientation-agnostic, so swapping the box's pixel dims is enough: the
  // locked ratio re-resolves to follow the new orientation.
  function swapAspect(): void {
    const [iw, ih] = currentImageDims();
    if (Math.abs(crop.w * iw - crop.h * ih) < 0.5) return; // square box: nothing to swap
    const next = constrainCrop({ ...crop, w: (crop.h * ih) / iw, h: (crop.w * iw) / ih }, srcW.value, srcH.value);
    Object.assign(crop, next);
  }

  function setAngle(v: number): void {
    const angle = clamp(v, -45, 45);
    Object.assign(crop, constrainCrop({ ...crop, angle }, srcW.value, srcH.value));
  }

  function rotateCrop(dir: 1 | -1): void {
    Object.assign(crop, constrainCrop(rotate90(crop, dir), srcW.value, srcH.value));
  }

  function flipCropH(): void {
    Object.assign(crop, constrainCrop({ ...crop, flipH: !crop.flipH, cx: 1 - crop.cx, angle: -crop.angle }, srcW.value, srcH.value));
  }
  function flipCropV(): void {
    Object.assign(crop, constrainCrop({ ...crop, flipV: !crop.flipV, cy: 1 - crop.cy, angle: -crop.angle }, srcW.value, srcH.value));
  }

  // ── Overlay geometry ──

  const cropBoxRect = computed<Rect>(() => {
    const [iw, ih] = currentImageDims();
    const Wc = crop.w * iw, Hc = crop.h * ih;
    return { x: crop.cx * iw - Wc / 2, y: crop.cy * ih - Hc / 2, w: Wc, h: Hc };
  });

  // Guide overlay inside the crop box (O cycles, Lightroom-style).
  const CROP_GUIDES = ["thirds", "golden", "diagonal", "grid", "off"] as const;
  type CropGuide = (typeof CROP_GUIDES)[number];
  const cropGuide = ref<CropGuide>("thirds");

  function cycleCropGuide(): void {
    const i = CROP_GUIDES.indexOf(cropGuide.value);
    cropGuide.value = CROP_GUIDES[(i + 1) % CROP_GUIDES.length];
  }

  // Guide lines in output-frame coords: fractional v/h lines plus box diagonals.
  const cropGuideLines = computed(() => {
    const r = cropBoxRect.value;
    const at = (fs: number[]) => ({
      v: fs.map((f) => r.x + r.w * f),
      h: fs.map((f) => r.y + r.h * f),
      diag: false,
    });
    switch (cropGuide.value) {
      case "thirds": return at([1 / 3, 2 / 3]);
      case "golden": return at([0.382, 0.618]);
      case "grid": return at([0.25, 0.5, 0.75]);
      case "diagonal": return { v: [], h: [], diag: true };
      default: return { v: [], h: [], diag: false };
    }
  });

  const CROP_HANDLES: { key: CropHandle; fx: number; fy: number; cursor: string }[] = [
    { key: "tl", fx: 0, fy: 0, cursor: "nwse-resize" }, { key: "t", fx: 0.5, fy: 0, cursor: "ns-resize" }, { key: "tr", fx: 1, fy: 0, cursor: "nesw-resize" },
    { key: "l", fx: 0, fy: 0.5, cursor: "ew-resize" }, { key: "r", fx: 1, fy: 0.5, cursor: "ew-resize" },
    { key: "bl", fx: 0, fy: 1, cursor: "nesw-resize" }, { key: "b", fx: 0.5, fy: 1, cursor: "ns-resize" }, { key: "br", fx: 1, fy: 1, cursor: "nwse-resize" },
  ];

  // Output-frame units per on-screen pixel — keeps overlay strokes/handles a
  // constant size regardless of the editor's fit scale.
  const ofPerScreen = computed(() => {
    const s = cropRenderScale.value * fitScale.value;
    return s > 0 ? 1 / s : 1;
  });

  // SVG viewBox for the overlay (output-frame coords, matching the rendered bbox).
  const cropViewBox = computed(() => `${cropBBox.x} ${cropBBox.y} ${cropBBox.w} ${cropBBox.h}`);

  // Dim everything outside the crop box: full-bbox rect with the crop box punched
  // out via the evenodd fill rule.
  const cropDimPath = computed(() => {
    const B = cropBBox, r = cropBoxRect.value;
    return `M${B.x},${B.y}H${B.x + B.w}V${B.y + B.h}H${B.x}Z`
         + `M${r.x},${r.y}V${r.y + r.h}H${r.x + r.w}V${r.y}Z`;
  });

  function cropHandlePos(h: { fx: number; fy: number }): { x: number; y: number } {
    const r = cropBoxRect.value;
    return { x: r.x + h.fx * r.w, y: r.y + h.fy * r.h };
  }

  // ── Drag state machine ──

  type CropDrag =
    | { mode: "move"; startX: number; startY: number; cx: number; cy: number }
    | { mode: "resize"; handle: CropHandle; l: number; t: number; r: number; b: number; startRatio: number }
    | { mode: "rotate"; startPointerDeg: number; startAngle: number };
  let cropDrag: CropDrag | null = null;

  function overlayPoint(e: MouseEvent): { x: number; y: number } {
    const svg = cropOverlayRef.value;
    if (!svg) return { x: 0, y: 0 };
    const r = svg.getBoundingClientRect();
    const fx = (e.clientX - r.left) / r.width;
    const fy = (e.clientY - r.top) / r.height;
    return { x: cropBBox.x + fx * cropBBox.w, y: cropBBox.y + fy * cropBBox.h };
  }

  function onCropHandleDown(e: MouseEvent, handle: CropHandle): void {
    e.preventDefault();
    e.stopPropagation();
    const r = cropBoxRect.value;
    cropDrag = { mode: "resize", handle, l: r.x, t: r.y, r: r.x + r.w, b: r.y + r.h, startRatio: r.w / r.h };
    attachCropDrag();
  }

  function onCropOverlayDown(e: MouseEvent): void {
    if (e.button !== 0) return;
    const p = overlayPoint(e);
    const r = cropBoxRect.value;
    const inside = p.x >= r.x && p.x <= r.x + r.w && p.y >= r.y && p.y <= r.y + r.h;
    if (inside) {
      cropDrag = { mode: "move", startX: p.x, startY: p.y, cx: crop.cx, cy: crop.cy };
    } else {
      // Drag in the margin to straighten (rotate the image), Lightroom-style.
      const [iw, ih] = currentImageDims();
      const deg = (Math.atan2(p.y - crop.cy * ih, p.x - crop.cx * iw) * 180) / Math.PI;
      cropDrag = { mode: "rotate", startPointerDeg: deg, startAngle: crop.angle };
    }
    attachCropDrag();
  }

  function attachCropDrag(): void {
    window.addEventListener("mousemove", onCropDragMove);
    window.addEventListener("mouseup", onCropDragUp);
  }

  function onCropDragMove(e: MouseEvent): void {
    if (!cropDrag) return;
    const p = overlayPoint(e);
    const [iw, ih] = currentImageDims();

    if (cropDrag.mode === "move") {
      const dx = (p.x - cropDrag.startX) / iw;
      const dy = (p.y - cropDrag.startY) / ih;
      moveCropTo(cropDrag.cx + dx, cropDrag.cy + dy);
    } else if (cropDrag.mode === "rotate") {
      const deg = (Math.atan2(p.y - crop.cy * ih, p.x - crop.cx * iw) * 180) / Math.PI;
      setAngle(cropDrag.startAngle + (deg - cropDrag.startPointerDeg));
    } else {
      resizeCropTo(p.x, p.y, cropDrag, e.shiftKey);
    }
  }

  function onCropDragUp(): void {
    cropDrag = null;
    window.removeEventListener("mousemove", onCropDragMove);
    window.removeEventListener("mouseup", onCropDragUp);
    opts.onDragEnd();
  }

  // Best-effort axis-clamped move that lets the box slide along an image edge.
  function moveCropTo(ncx: number, ncy: number): void {
    const [iw, ih] = currentImageDims();
    const ok = (cx: number, cy: number): boolean => cornersInsideImage({ ...crop, cx, cy }, iw, ih);
    const solve = (from: number, to: number, test: (v: number) => boolean): number => {
      if (test(to)) return to;
      let lo = from, hi = to;
      for (let i = 0; i < 20; i++) { const m = (lo + hi) / 2; if (test(m)) lo = m; else hi = m; }
      return lo;
    };
    let x = ncx, y = ncy;
    if (!ok(x, crop.cy)) x = solve(crop.cx, x, (v) => ok(v, crop.cy));
    if (!ok(x, y)) y = solve(crop.cy, y, (v) => ok(x, v));
    setCrop({ cx: x, cy: y });
  }

  function resizeCropTo(qx: number, qy: number, d: { handle: CropHandle; l: number; t: number; r: number; b: number; startRatio: number }, shift = false): void {
    const [iw, ih] = currentImageDims();
    const MIN = Math.max(24, 0.05 * Math.min(iw, ih));
    let { l, t, r, b } = d;
    const hasL = d.handle.includes("l"), hasR = d.handle.includes("r");
    const hasT = d.handle.includes("t"), hasB = d.handle.includes("b");
    if (hasL) l = Math.min(qx, r - MIN);
    if (hasR) r = Math.max(qx, l + MIN);
    if (hasT) t = Math.min(qy, b - MIN);
    if (hasB) b = Math.max(qy, t + MIN);

    // Shift temporarily locks the box's shape at drag start (Lightroom-style).
    const ratio = lockedRatio.value ?? (shift ? d.startRatio : null);
    if (ratio != null) {
      const corner = (hasL || hasR) && (hasT || hasB);
      if (corner) {
        const h = (r - l) / ratio;
        if (hasT) t = b - h; else b = t + h;
      } else if (hasL || hasR) {
        const h = (r - l) / ratio, cy = (t + b) / 2;
        t = cy - h / 2; b = cy + h / 2;
      } else {
        const w = (b - t) * ratio, cx = (l + r) / 2;
        l = cx - w / 2; r = cx + w / 2;
      }
    }

    // Constrain to the image. At angle 0 clamp edges exactly; otherwise reject if
    // the rotated box would leave the image (the handle stops at the boundary).
    if (Math.abs(crop.angle) < 1e-3 && ratio == null) {
      l = Math.max(0, l); t = Math.max(0, t); r = Math.min(iw, r); b = Math.min(ih, b);
    }
    const cand: CropState = { ...crop, cx: (l + r) / 2 / iw, cy: (t + b) / 2 / ih, w: (r - l) / iw, h: (b - t) / ih };
    if (cornersInsideImage(cand, iw, ih)) Object.assign(crop, cand);
  }

  return {
    cropAspect, cropBBox, cropRenderScale, cropOverlayRef,
    currentImageDims, resetCrop,
    lockedRatio, customAspect, selectAspect, setCustomAspect, swapAspect,
    setAngle, rotateCrop, flipCropH, flipCropV,
    cropGuide, cycleCropGuide, cropGuideLines,
    cropBoxRect, CROP_HANDLES, ofPerScreen, cropViewBox, cropDimPath, cropHandlePos,
    onCropHandleDown, onCropOverlayDown,
  };
}
