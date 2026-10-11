import { computed, ref, type Ref } from "vue";
import { cropOutputRect, imageDims, imagePxToOutFrame, outFrameToImagePx, type CropState } from "../rendering/crop";
import { strokeToView, viewToStroke, type LensWarp } from "../rendering/erase";
import type { RelightLight } from "../rendering/relight";

// The relight rig on the canvas: one handle per light at its aim point. Same
// overlay placement and coordinate round trip as the erase brush
// (useEraseEditor): a pointer is an output-frame point, a light is stored in
// the decode's own source-norm frame, so it stays on the surface it was aimed at
// through any later crop, rotation or flip. A click on empty image adds a
// light there; dragging a handle re-aims it.
export function useRelightEditor(opts: {
  crop: CropState;                       // reactive
  srcW: Ref<number>;
  srcH: Ref<number>;
  lights: RelightLight[];                // reactive
  selected: Ref<string | null>;
  ofPerScreen: Ref<number>;
  lens: () => LensWarp;
  onAdd: (u: number, v: number) => void;
  onDragEnd: () => void;
}) {
  const { crop, srcW, srcH } = opts;
  const overlayRef = ref<SVGSVGElement | null>(null);

  type Pt = [number, number];
  const dims = (): [number, number] => imageDims(srcW.value, srcH.value, crop.orientation);
  const viewRect = computed(() => { const [iw, ih] = dims(); return cropOutputRect(crop, iw, ih); });
  const viewBox = computed(() => { const r = viewRect.value; return `${r.x} ${r.y} ${r.w} ${r.h}`; });

  function overlayPoint(e: PointerEvent): Pt {
    const svg = overlayRef.value, r = viewRect.value;
    if (!svg) return [r.x, r.y];
    const b = svg.getBoundingClientRect();
    return [r.x + ((e.clientX - b.left) / b.width) * r.w, r.y + ((e.clientY - b.top) / b.height) * r.h];
  }

  function toSource(p: Pt): Pt {
    const [iw, ih] = dims();
    const [x, y] = outFrameToImagePx(crop, iw, ih, p[0], p[1]);
    return viewToStroke(x / iw, y / ih, crop, opts.lens());
  }

  const handles = computed(() => {
    if (!srcW.value) return [];
    const [iw, ih] = dims();
    const lens = opts.lens();
    return opts.lights.map(l => {
      const [x, y] = strokeToView(l.u, l.v, crop, lens);
      const [ox, oy] = imagePxToOutFrame(crop, iw, ih, x * iw, y * ih);
      return { id: l.id, x: ox, y: oy, color: l.color, behind: l.lift < 0 };
    });
  });

  let dragging: string | null = null;

  function onHandleDown(e: PointerEvent, id: string): void {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    overlayRef.value?.setPointerCapture(e.pointerId);
    opts.selected.value = id;
    dragging = id;
  }

  function onPointerDown(e: PointerEvent): void {
    if (e.button !== 0) return;
    e.preventDefault();
    const [u, v] = toSource(overlayPoint(e));
    if (u < 0 || u > 1 || v < 0 || v > 1) return;
    opts.onAdd(u, v);
  }

  function onPointerMove(e: PointerEvent): void {
    if (!dragging) return;
    const l = opts.lights.find(x => x.id === dragging);
    if (!l) return;
    const [u, v] = toSource(overlayPoint(e));
    l.u = Math.min(1, Math.max(0, u));
    l.v = Math.min(1, Math.max(0, v));
  }

  function onPointerUp(): void {
    if (!dragging) return;
    dragging = null;
    opts.onDragEnd();
  }

  return { overlayRef, viewBox, handles, onHandleDown, onPointerDown, onPointerMove, onPointerUp };
}

// One point on the image in source-norm, shown as a marker and, while
// `picking`, set by a click — the lens blur's focus.
export function useFocusPicker(opts: {
  crop: CropState;
  srcW: Ref<number>;
  srcH: Ref<number>;
  picking: Ref<boolean>;
  focus: () => [number, number];
  lens: () => LensWarp;
  onPick: (u: number, v: number) => void;
}) {
  const { crop, srcW, srcH } = opts;
  const overlayRef = ref<SVGSVGElement | null>(null);
  const dims = (): [number, number] => imageDims(srcW.value, srcH.value, crop.orientation);
  const viewRect = computed(() => { const [iw, ih] = dims(); return cropOutputRect(crop, iw, ih); });
  const viewBox = computed(() => { const r = viewRect.value; return `${r.x} ${r.y} ${r.w} ${r.h}`; });
  const marker = computed(() => {
    if (!srcW.value) return null;
    const [iw, ih] = dims();
    const [u, v] = opts.focus();
    const [x, y] = strokeToView(u, v, crop, opts.lens());
    const [ox, oy] = imagePxToOutFrame(crop, iw, ih, x * iw, y * ih);
    return { x: ox, y: oy };
  });
  function onPointerDown(e: PointerEvent): void {
    if (e.button !== 0 || !opts.picking.value) return;
    e.preventDefault();
    const svg = overlayRef.value, r = viewRect.value;
    if (!svg) return;
    const b = svg.getBoundingClientRect();
    const ox = r.x + ((e.clientX - b.left) / b.width) * r.w, oy = r.y + ((e.clientY - b.top) / b.height) * r.h;
    const [iw, ih] = dims();
    const [x, y] = outFrameToImagePx(crop, iw, ih, ox, oy);
    const [u, v] = viewToStroke(x / iw, y / ih, crop, opts.lens());
    if (u >= 0 && u <= 1 && v >= 0 && v <= 1) opts.onPick(u, v);
  }
  return { overlayRef, viewBox, marker, onPointerDown };
}
