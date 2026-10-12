import { computed, ref, type Ref } from "vue";
import { cropOutputRect, imageDims, imagePxToOutFrame, outFrameToImagePx, type CropState } from "../rendering/crop";
import { eraseLines, eraseReach, strokeToView, viewToStroke, type EraseLine, type LensWarp } from "../rendering/erase";

// The erase brush on the canvas. Drawn on an SVG that shares the crop overlay's
// placement (like useMaskEditor), so a pointer position is an output-frame
// point; a finished stroke is taken from there to the decode's own frame
// (rendering/erase.ts viewToStroke) and handed to `onStroke`. Stored strokes
// come back the other way to be drawn, so they stay on the pixels they cover
// through any later crop, rotation or flip.
type Stroke = EraseLine & { id: string; more?: EraseLine[]; grow?: number; feather?: number };

export function useEraseEditor(opts: {
  crop: CropState;                       // reactive
  srcW: Ref<number>;
  srcH: Ref<number>;
  // reactive, or a getter over reactive state; a stroke's `more` lines are drawn with it
  strokes: readonly Stroke[] | (() => readonly Stroke[]);
  brush: Ref<number>;                    // brush radius in screen px
  ofPerScreen: Ref<number>;              // output-frame units per screen px
  lens: () => LensWarp;
  onStroke: (points: [number, number][], radius: number) => void;
}) {
  const { crop, srcW, srcH } = opts;
  const overlayRef = ref<SVGSVGElement | null>(null);

  type Pt = [number, number];
  const dims = (): [number, number] => imageDims(srcW.value, srcH.value, crop.orientation);
  const viewRect = computed(() => { const [iw, ih] = dims(); return cropOutputRect(crop, iw, ih); });
  const viewBox = computed(() => { const r = viewRect.value; return `${r.x} ${r.y} ${r.w} ${r.h}`; });
  const longEdge = () => Math.max(srcW.value, srcH.value) || 1;

  function overlayPoint(e: PointerEvent): Pt {
    const svg = overlayRef.value, r = viewRect.value;
    if (!svg) return [r.x, r.y];
    const b = svg.getBoundingClientRect();
    return [r.x + ((e.clientX - b.left) / b.width) * r.w, r.y + ((e.clientY - b.top) / b.height) * r.h];
  }

  const pathOf = (pts: Pt[]): string =>
    pts.length ? `M${pts[0][0]} ${pts[0][1]}${pts.length === 1 ? "l0 0" : pts.slice(1).map(([x, y]) => `L${x} ${y}`).join("")}` : "";

  // Stored strokes as output-frame paths, one per line (`id` is the stroke's, so
  // a hover lights all of its lines). A line's width is in source px, which are
  // image px, which are output-frame px (crop and straighten do not scale).
  // `reachWidth` is the same line widened by the stroke's grow and feather:
  // how far the fill may touch, which is wider than what was brushed.
  const paths = computed(() => {
    if (!srcW.value) return [];
    const [iw, ih] = dims();
    const lens = opts.lens();
    const list = typeof opts.strokes === "function" ? opts.strokes() : opts.strokes;
    return list.flatMap(s => eraseLines(s).map((line, i) => ({
      id: s.id,
      key: `${s.id}:${i}`,
      width: 2 * line.radius * longEdge(),
      reachWidth: 2 * (line.radius + eraseReach(s)) * longEdge(),
      d: pathOf(line.points.map(([u, v]) => {
        const [x, y] = strokeToView(u, v, crop, lens);
        return imagePxToOutFrame(crop, iw, ih, x * iw, y * ih);
      })),
    })));
  });

  // The stroke in progress, in output-frame points.
  const live = ref<Pt[]>([]);
  const livePath = computed(() => pathOf(live.value));
  const brushOf = () => opts.brush.value * opts.ofPerScreen.value;
  const cursor = ref<{ x: number; y: number } | null>(null);
  const cursorR = computed(() => brushOf());
  let drawing = false;

  function onPointerDown(e: PointerEvent): void {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    (e.currentTarget as Element).setPointerCapture(e.pointerId);
    drawing = true;
    live.value = [overlayPoint(e)];
  }

  function onPointerMove(e: PointerEvent): void {
    const p = overlayPoint(e);
    cursor.value = { x: p[0], y: p[1] };
    if (!drawing) return;
    // A point every quarter radius traces the brush faithfully; denser only
    // grows the request (and the worker's stroke key) for nothing.
    const last = live.value[live.value.length - 1];
    if (Math.hypot(p[0] - last[0], p[1] - last[1]) >= brushOf() / 4) live.value = [...live.value, p];
  }

  function onPointerUp(e: PointerEvent): void {
    if (!drawing) return;
    drawing = false;
    const p = overlayPoint(e);
    const pts = [...live.value, p];
    live.value = [];
    const [iw, ih] = dims();
    const lens = opts.lens();
    const stroke = pts.map(([ox, oy]) => {
      const [x, y] = outFrameToImagePx(crop, iw, ih, ox, oy);
      return viewToStroke(x / iw, y / ih, crop, lens);
    });
    opts.onStroke(stroke, brushOf() / longEdge());
  }

  function onPointerLeave(): void { if (!drawing) cursor.value = null; }

  return { overlayRef, viewBox, paths, livePath, cursor, cursorR, onPointerDown, onPointerMove, onPointerUp, onPointerLeave };
}
