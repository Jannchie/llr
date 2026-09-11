import { computed, ref, type Ref } from "vue";
import { cropOutputRect, imageDims, imagePxToOutFrame, outFrameToImagePx, type CropState } from "../rendering/crop";
import type { MaskComponent, MaskGroup } from "../rendering/masks";

// On-canvas handles for the selected group's gradients. Geometry is stored in
// oriented image-norm (rendering/masks.ts) and drawn on an SVG that shares the
// crop overlay's placement, so every point goes image px -> output frame
// (imagePxToOutFrame) to draw and back (outFrameToImagePx) on drag — under a
// perspective that makes the radial outline a sampled polygon, which is exact,
// rather than an <ellipse>, which would not be.
export function useMaskEditor(opts: {
  crop: CropState;                       // reactive
  srcW: Ref<number>;
  srcH: Ref<number>;
  group: Ref<MaskGroup | null>;          // the selected group
  ofPerScreen: Ref<number>;              // output-frame units per screen px (handle sizes)
  onDragEnd: () => void;
}) {
  const { crop, srcW, srcH, group } = opts;
  const overlayRef = ref<SVGSVGElement | null>(null);

  const dims = (): [number, number] => imageDims(srcW.value, srcH.value, crop.orientation);
  // The committed view's window: what the canvas shows in normal mode.
  const viewRect = computed(() => { const [iw, ih] = dims(); return cropOutputRect(crop, iw, ih); });
  const viewBox = computed(() => { const r = viewRect.value; return `${r.x} ${r.y} ${r.w} ${r.h}`; });

  type Pt = [number, number];
  const toOut = (x: number, y: number): Pt => { const [iw, ih] = dims(); return imagePxToOutFrame(crop, iw, ih, x * iw, y * ih); };
  const toImg = (ox: number, oy: number): Pt => {
    const [iw, ih] = dims();
    const [x, y] = outFrameToImagePx(crop, iw, ih, ox, oy);
    return [x / iw, y / ih];
  };

  // A radial's ellipse in image px: centre + R(angle)·(rx·iw cos t, ry·ih sin t).
  // The shader rotates the *query* by [c s; -s c] before dividing by the axes,
  // so the outline is that matrix's inverse applied to the axis-aligned ellipse.
  function radialPoint(c: Extract<MaskComponent, { type: "radial" }>, t: number, scale = 1): Pt {
    const [iw, ih] = dims();
    const a = (c.angle * Math.PI) / 180, cs = Math.cos(a), sn = Math.sin(a);
    const X = c.rx * iw * Math.cos(t) * scale, Y = c.ry * ih * Math.sin(t) * scale;
    return [c.cx * iw + cs * X - sn * Y, c.cy * ih + sn * X + cs * Y];
  }
  const pxToOut = ([x, y]: Pt): Pt => { const [iw, ih] = dims(); return imagePxToOutFrame(crop, iw, ih, x, y); };
  const poly = (pts: Pt[]): string => pts.map(([x, y]) => `${x},${y}`).join(" ");

  type LinearShape = { kind: "linear"; index: number; p0: Pt; p1: Pt; ref0: [Pt, Pt]; ref1: [Pt, Pt] };
  type RadialShape = { kind: "radial"; index: number; centre: Pt; outline: string; inner: string; axes: Pt[]; rotate: Pt };
  const shapes = computed<(LinearShape | RadialShape)[]>(() => {
    const g = group.value;
    if (!g || !srcW.value) return [];
    const [iw, ih] = dims();
    const out: (LinearShape | RadialShape)[] = [];
    g.components.forEach((c, index) => {
      if (c.type === "linear") {
        // Reference lines: the 100% and 0% isolines, perpendicular to the
        // gradient in image px, long enough to cross any frame.
        const dx = (c.x1 - c.x0) * iw, dy = (c.y1 - c.y0) * ih, len = Math.hypot(dx, dy) || 1;
        const L = Math.hypot(iw, ih), px = (-dy / len) * L, py = (dx / len) * L;
        const ref = (x: number, y: number): [Pt, Pt] => [pxToOut([x * iw - px, y * ih - py]), pxToOut([x * iw + px, y * ih + py])];
        out.push({ kind: "linear", index, p0: toOut(c.x0, c.y0), p1: toOut(c.x1, c.y1), ref0: ref(c.x0, c.y0), ref1: ref(c.x1, c.y1) });
      } else if (c.type === "radial") {
        const N = 48;
        const ring = (scale: number) => poly(Array.from({ length: N }, (_, i) => pxToOut(radialPoint(c, (i / N) * 2 * Math.PI, scale))));
        out.push({
          kind: "radial", index,
          centre: toOut(c.cx, c.cy),
          outline: ring(1),
          inner: ring(1 - c.feather),
          axes: [0, Math.PI / 2, Math.PI, 1.5 * Math.PI].map(t => pxToOut(radialPoint(c, t))),
          rotate: pxToOut(radialPoint(c, 0, 1.18)),
        });
      }
    });
    return out;
  });

  // ── Drag ──

  type Drag =
    | { mode: "linearEnd"; index: number; end: 0 | 1 }
    | { mode: "linearMove"; index: number; start: Pt; x0: number; y0: number; x1: number; y1: number }
    | { mode: "radialCentre"; index: number }
    | { mode: "radialAxis"; index: number; axis: 0 | 1 | 2 | 3 }
    | { mode: "radialRotate"; index: number };
  let drag: Drag | null = null;

  function overlayPoint(e: MouseEvent): Pt {
    const svg = overlayRef.value;
    const r = viewRect.value;
    if (!svg) return [r.x, r.y];
    const b = svg.getBoundingClientRect();
    return [r.x + ((e.clientX - b.left) / b.width) * r.w, r.y + ((e.clientY - b.top) / b.height) * r.h];
  }

  function begin(e: MouseEvent, d: Drag): void {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    drag = d;
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }
  const onLinearEndDown = (e: MouseEvent, index: number, end: 0 | 1) => begin(e, { mode: "linearEnd", index, end });
  const onLinearLineDown = (e: MouseEvent, index: number) => {
    const c = group.value?.components[index];
    if (c?.type !== "linear") return;
    begin(e, { mode: "linearMove", index, start: toImg(...overlayPoint(e)), x0: c.x0, y0: c.y0, x1: c.x1, y1: c.y1 });
  };
  const onRadialCentreDown = (e: MouseEvent, index: number) => begin(e, { mode: "radialCentre", index });
  const onRadialAxisDown = (e: MouseEvent, index: number, axis: 0 | 1 | 2 | 3) => begin(e, { mode: "radialAxis", index, axis });
  const onRadialRotateDown = (e: MouseEvent, index: number) => begin(e, { mode: "radialRotate", index });

  const clamp01 = (v: number) => Math.min(Math.max(v, 0), 1);

  function onMove(e: MouseEvent): void {
    const c = drag && group.value?.components[drag.index];
    if (!drag || !c) return;
    const [x, y] = toImg(...overlayPoint(e));
    if (drag.mode === "linearEnd" && c.type === "linear") {
      if (drag.end === 0) { c.x0 = x; c.y0 = y; } else { c.x1 = x; c.y1 = y; }
    } else if (drag.mode === "linearMove" && c.type === "linear") {
      const dx = x - drag.start[0], dy = y - drag.start[1];
      c.x0 = drag.x0 + dx; c.y0 = drag.y0 + dy; c.x1 = drag.x1 + dx; c.y1 = drag.y1 + dy;
    } else if (drag.mode === "radialCentre" && c.type === "radial") {
      c.cx = clamp01(x); c.cy = clamp01(y);
    } else if (c.type === "radial") {
      // Pointer relative to the centre, in image px, un-rotated into the
      // ellipse's own frame (the shader's [c s; -s c]).
      const [iw, ih] = dims();
      const px = (x - c.cx) * iw, py = (y - c.cy) * ih;
      if (drag.mode === "radialRotate") {
        c.angle = Math.round((Math.atan2(py, px) * 180) / Math.PI * 10) / 10;
      } else if (drag.mode === "radialAxis") {
        const a = (c.angle * Math.PI) / 180, cs = Math.cos(a), sn = Math.sin(a);
        const qx = cs * px + sn * py, qy = -sn * px + cs * py;
        if (drag.axis % 2 === 0) c.rx = Math.max(Math.abs(qx) / iw, 0.02);
        else c.ry = Math.max(Math.abs(qy) / ih, 0.02);
      }
    }
  }

  function onUp(): void {
    drag = null;
    window.removeEventListener("mousemove", onMove);
    window.removeEventListener("mouseup", onUp);
    opts.onDragEnd();
  }

  return {
    overlayRef, viewBox, shapes,
    onLinearEndDown, onLinearLineDown, onRadialCentreDown, onRadialAxisDown, onRadialRotateDown,
  };
}
