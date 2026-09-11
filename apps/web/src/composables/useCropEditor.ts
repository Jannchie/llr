import { computed, reactive, ref, type Ref } from "vue";
import {
  imageDims, constrainCrop, applyAspectRatio, resolveAspectRatio,
  rotate90, cornersInsideImage, customAspectKey, parseCustomAspect, ratioToFraction,
  defaultCrop, straightenAngle, cropGuideShapes, CROP_GUIDES,
  type CropState, type Rect, type CropGuide,
} from "../rendering/crop";
import { clamp } from "../ui";

// Aspect lock for the crop box. Defaults to the image's own ratio; part of
// the per-image snapshot so it survives image switches / undo / persistence.
export const DEFAULT_ASPECT = "orig";

export type CropHandle = "l" | "r" | "t" | "b" | "tl" | "tr" | "bl" | "br";

// Crop & straighten editor: aspect controls, the SVG overlay geometry
// (output-frame coordinate space, matching the overlay's viewBox), and the
// move/resize/rotate/straighten drag state machine. The caller owns the `crop`
// state (it is part of the per-image snapshot) and the render bridge — it
// writes `cropBBox` when it renders the editor window.
export function useCropEditor(opts: {
  crop: CropState;          // reactive
  srcW: Ref<number>;
  srcH: Ref<number>;
  fitScale: Ref<number>;
  zoom: Ref<number>;
  onDragEnd: () => void;    // commit pending history when a drag finishes
}) {
  const { crop, srcW, srcH, fitScale, zoom } = opts;

  const cropAspect = ref<string>(DEFAULT_ASPECT);
  // Crop-editor render window (output-frame px), kept so the overlay can map
  // between screen, output-frame and crop-box space. Written by the caller's
  // editor render.
  const cropBBox = reactive<Rect>({ x: 0, y: 0, w: 1, h: 1 });
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

  // Guide overlay inside the crop box (O cycles, Shift+O mirrors the
  // asymmetric ones, Lightroom-style). Remembered across sessions: a guide is
  // a viewing preference, not part of the edit.
  const cropGuide = ref<CropGuide>(
    (CROP_GUIDES as readonly string[]).includes(localStorage.getItem("llr.cropGuide") ?? "")
      ? (localStorage.getItem("llr.cropGuide") as CropGuide) : "thirds");
  const cropGuideVariant = ref(0);

  function setCropGuide(g: CropGuide): void {
    cropGuide.value = g;
    localStorage.setItem("llr.cropGuide", g);
  }
  function cycleCropGuide(): void {
    setCropGuide(CROP_GUIDES[(CROP_GUIDES.indexOf(cropGuide.value) + 1) % CROP_GUIDES.length]);
  }
  function cycleCropGuideVariant(): void {
    cropGuideVariant.value = (cropGuideVariant.value + 1) % 4;
  }

  const cropGuideShapesView = computed(() => cropGuideShapes(cropGuide.value, cropBoxRect.value, cropGuideVariant.value));

  // While the angle is being dragged, a fine grid over the box makes lines in
  // the photo easy to align against (Lightroom does the same).
  const isRotating = ref(false);
  const rotateGridLines = computed<Array<[number, number, number, number]>>(() => {
    if (!isRotating.value) return [];
    const r = cropBoxRect.value;
    const step = Math.max(r.w, r.h) / 24;
    const out: Array<[number, number, number, number]> = [];
    for (let x = r.x + step; x < r.x + r.w - step / 2; x += step) out.push([x, r.y, x, r.y + r.h]);
    for (let y = r.y + step; y < r.y + r.h - step / 2; y += step) out.push([r.x, y, r.x + r.w, y]);
    return out;
  });

  const CROP_HANDLES: { key: CropHandle; fx: number; fy: number; cursor: string }[] = [
    { key: "tl", fx: 0, fy: 0, cursor: "nwse-resize" }, { key: "t", fx: 0.5, fy: 0, cursor: "ns-resize" }, { key: "tr", fx: 1, fy: 0, cursor: "nesw-resize" },
    { key: "l", fx: 0, fy: 0.5, cursor: "ew-resize" }, { key: "r", fx: 1, fy: 0.5, cursor: "ew-resize" },
    { key: "bl", fx: 0, fy: 1, cursor: "nesw-resize" }, { key: "b", fx: 0.5, fy: 1, cursor: "ns-resize" }, { key: "br", fx: 1, fy: 1, cursor: "nwse-resize" },
  ];

  // Output-frame units per on-screen pixel — keeps overlay strokes/handles a
  // constant size regardless of the editor's fit scale and zoom.
  const ofPerScreen = computed(() => {
    const s = fitScale.value * zoom.value;
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
    | { mode: "rotate"; startPointerDeg: number; startAngle: number }
    | { mode: "straighten" };
  let cropDrag: CropDrag | null = null;

  // Straighten tool: armed by the panel button (or Ctrl/Cmd-drag), the next
  // drag draws a line on the photo and the angle levels it. One-shot, like
  // Lightroom's ruler. `straightenLine` is the line being drawn, output-frame px.
  const straightenTool = ref(false);
  const straightenLine = reactive({ x1: 0, y1: 0, x2: 0, y2: 0, active: false });
  function toggleStraightenTool(): void { straightenTool.value = !straightenTool.value; }
  // What the on-canvas readout shows: the angle the line being drawn would set,
  // else the current one.
  const readoutAngle = computed(() => straightenLine.active
    ? straightenAngle(crop.angle, straightenLine.x2 - straightenLine.x1, straightenLine.y2 - straightenLine.y1)
    : crop.angle);

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
    if (straightenTool.value || e.ctrlKey || e.metaKey) {
      e.preventDefault();
      Object.assign(straightenLine, { x1: p.x, y1: p.y, x2: p.x, y2: p.y, active: true });
      cropDrag = { mode: "straighten" };
      isRotating.value = true;
    } else if (inside) {
      cropDrag = { mode: "move", startX: p.x, startY: p.y, cx: crop.cx, cy: crop.cy };
    } else {
      // Drag in the margin to straighten (rotate the image), Lightroom-style.
      const [iw, ih] = currentImageDims();
      const deg = (Math.atan2(p.y - crop.cy * ih, p.x - crop.cx * iw) * 180) / Math.PI;
      cropDrag = { mode: "rotate", startPointerDeg: deg, startAngle: crop.angle };
      isRotating.value = true;
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
      // Wrap the sweep into [-180, 180): crossing atan2's ±180° seam (the left
      // margin) must not read as a full turn.
      const sweep = ((deg - cropDrag.startPointerDeg + 540) % 360) - 180;
      setAngle(cropDrag.startAngle + sweep);
    } else if (cropDrag.mode === "straighten") {
      straightenLine.x2 = p.x;
      straightenLine.y2 = p.y;
    } else {
      resizeCropTo(p.x, p.y, cropDrag, e.shiftKey);
    }
  }

  function onCropDragUp(): void {
    if (cropDrag?.mode === "straighten") {
      const dx = straightenLine.x2 - straightenLine.x1, dy = straightenLine.y2 - straightenLine.y1;
      // Shorter than a few screen px is a click, not a line: keep the angle.
      if (Math.hypot(dx, dy) > 6 * ofPerScreen.value) setAngle(straightenAngle(crop.angle, dx, dy));
      straightenLine.active = false;
      straightenTool.value = false;
    }
    isRotating.value = false;
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

    // Constrain to the image. At angle 0 clamp edges exactly; otherwise a box
    // that would leave the image lands on the boundary: bisect between the
    // current (valid) box and the target, so a fast drag that overshoots does
    // not stall wherever the last in-bounds mousemove happened to be.
    if (Math.abs(crop.angle) < 1e-3 && ratio == null) {
      l = Math.max(0, l); t = Math.max(0, t); r = Math.min(iw, r); b = Math.min(ih, b);
    }
    const f = cropBoxRect.value;
    const at = (m: number): CropState => {
      const L = f.x + (l - f.x) * m, T = f.y + (t - f.y) * m;
      const R = f.x + f.w + (r - f.x - f.w) * m, B = f.y + f.h + (b - f.y - f.h) * m;
      return { ...crop, cx: (L + R) / 2 / iw, cy: (T + B) / 2 / ih, w: (R - L) / iw, h: (B - T) / ih };
    };
    let m = 1;
    if (!cornersInsideImage(at(1), iw, ih)) {
      let lo = 0, hi = 1;
      for (let i = 0; i < 20; i++) { const mid = (lo + hi) / 2; if (cornersInsideImage(at(mid), iw, ih)) lo = mid; else hi = mid; }
      m = lo;
    }
    Object.assign(crop, at(m));
  }

  return {
    cropAspect, cropBBox, cropOverlayRef,
    currentImageDims, resetCrop,
    lockedRatio, customAspect, selectAspect, setCustomAspect, swapAspect,
    setAngle, rotateCrop, flipCropH, flipCropV,
    cropGuide, setCropGuide, cycleCropGuide, cycleCropGuideVariant, cropGuideShapes: cropGuideShapesView,
    isRotating, rotateGridLines, straightenTool, toggleStraightenTool, straightenLine, readoutAngle,
    cropBoxRect, CROP_HANDLES, ofPerScreen, cropViewBox, cropDimPath, cropHandlePos,
    onCropHandleDown, onCropOverlayDown,
  };
}
