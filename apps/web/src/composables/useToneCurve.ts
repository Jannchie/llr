import { onBeforeUnmount, ref, watch } from "vue";
import {
  defaultToneCurve, renderToneCurve, hitTest, hitTestSplit, regionForX,
  CURVE_PRESETS, type CurvePoint, type ToneCurve, type ToneChannel, type PointChannel,
} from "../rendering/curve";
import { clamp } from "../ui";

// Tone-curve panel state + canvas interaction (Lightroom-compatible:
// parametric regions/splits + RGB/R/G/B point curves). Owns the curve value,
// the channel tabs, and the whole drag state machine; the caller supplies how
// an edit reaches the GPU (`onApply` rebakes the LUT and schedules a draw,
// `onReset` rebakes and draws immediately).
export function useToneCurve(opts: {
  onApply: () => void;
  onReset: () => void;
}) {
  const toneCurve = ref<ToneCurve>(defaultToneCurve());
  const curveChannel = ref<ToneChannel>("parametric");
  const curveActive = ref(-1);
  const curveHover = ref(-1); // hovered parametric region (0=shadows..3=highlights), -1 = none
  const curveCanvas = ref<HTMLCanvasElement | null>(null);

  const CURVE_TABS: { key: ToneChannel; label: string }[] = [
    { key: "parametric", label: "Param" },
    { key: "rgb", label: "RGB" },
    { key: "red", label: "R" },
    { key: "green", label: "G" },
    { key: "blue", label: "B" },
  ];
  const PARAM_REGIONS: { key: "highlights" | "lights" | "darks" | "shadows"; label: string }[] = [
    { key: "highlights", label: "Highlights" },
    { key: "lights", label: "Lights" },
    { key: "darks", label: "Darks" },
    { key: "shadows", label: "Shadows" },
  ];
  const REGION_BY_INDEX: ("shadows" | "darks" | "lights" | "highlights")[] = ["shadows", "darks", "lights", "highlights"];
  const presetNames = Object.keys(CURVE_PRESETS);

  function setCurveChannel(ch: ToneChannel): void {
    curveChannel.value = ch;
    curveActive.value = -1;
    curveHover.value = -1;
    renderCurveCanvas();
  }

  function resetCurve(): void {
    toneCurve.value = defaultToneCurve();
    curveActive.value = -1;
    renderCurveCanvas();
    opts.onReset();
  }

  function applyCurvePreset(name: string): void {
    const preset = CURVE_PRESETS[name];
    if (!preset) return;
    toneCurve.value = { ...toneCurve.value, rgb: preset.map(p => ({ ...p })) };
    curveChannel.value = "rgb";
    curveActive.value = -1;
    opts.onApply();
    renderCurveCanvas();
  }

  // Parametric slider bridge (v-model for the region/split inputs).
  function paramValue(key: keyof ToneCurve["parametric"]): number {
    return toneCurve.value.parametric[key];
  }
  function setParam(key: keyof ToneCurve["parametric"], v: number): void {
    toneCurve.value = {
      ...toneCurve.value,
      parametric: { ...toneCurve.value.parametric, [key]: v },
    };
    opts.onApply();
    renderCurveCanvas();
  }

  function renderCurveCanvas(): void {
    const cvs = curveCanvas.value;
    if (!cvs) return;
    const dpr = window.devicePixelRatio || 1;
    const w = cvs.clientWidth;
    const h = cvs.clientHeight;
    cvs.width = w * dpr;
    cvs.height = h * dpr;
    const ctx = cvs.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    renderToneCurve(ctx, w, h, toneCurve.value, curveChannel.value, curveActive.value, curveHover.value);
  }

  // --- pointer interaction ---

  type CurveDrag =
    | { mode: "point"; channel: PointChannel; index: number }
    | { mode: "split"; index: number }
    | { mode: "region"; key: "shadows" | "darks" | "lights" | "highlights"; startMy: number; startVal: number; h: number };
  let curveDrag: CurveDrag | null = null;

  function curveCoords(e: MouseEvent): { mx: number; my: number; w: number; h: number } | null {
    const cvs = curveCanvas.value;
    if (!cvs) return null;
    const rect = cvs.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    return { mx: e.clientX - rect.left, my: e.clientY - rect.top, w: rect.width / dpr, h: rect.height / dpr };
  }

  function setChannelPoints(ch: PointChannel, pts: CurvePoint[]): void {
    toneCurve.value = { ...toneCurve.value, [ch]: pts };
  }

  function onCurveMouseDown(e: MouseEvent): void {
    const c = curveCoords(e);
    if (!c) return;
    const { mx, my, w, h } = c;

    if (curveChannel.value === "parametric") {
      const param = toneCurve.value.parametric;
      const split = hitTestSplit(param, w, h, mx, my);
      if (split >= 0) {
        curveDrag = { mode: "split", index: split };
      } else {
        const region = REGION_BY_INDEX[regionForX(param, clamp(mx / w, 0, 1))];
        curveDrag = { mode: "region", key: region, startMy: my, startVal: param[region], h };
      }
    } else {
      const ch = curveChannel.value as PointChannel;
      const pts = toneCurve.value[ch];
      const idx = hitTest(pts, w, h, mx, my);
      if (idx >= 0) {
        curveActive.value = idx;
        curveDrag = { mode: "point", channel: ch, index: idx };
      } else {
        // Insert a new point, keeping x-order.
        const x = clamp(mx / w, 0, 1);
        const y = clamp(1 - my / h, 0, 1);
        const next = [...pts, { x, y }].sort((a, b) => a.x - b.x);
        const index = next.findIndex(p => p.x === x && p.y === y);
        setChannelPoints(ch, next);
        curveActive.value = index;
        curveDrag = { mode: "point", channel: ch, index };
        opts.onApply();
      }
    }
    renderCurveCanvas();
    window.addEventListener("mousemove", onCurveMouseMove);
    window.addEventListener("mouseup", onCurveMouseUp);
  }

  function onCurveMouseMove(e: MouseEvent): void {
    if (!curveDrag) return;
    const c = curveCoords(e);
    if (!c) return;
    const { mx, my, w, h } = c;

    if (curveDrag.mode === "point") {
      const ch = curveDrag.channel;
      const pts = [...toneCurve.value[ch]];
      const i = curveDrag.index;
      const last = pts.length - 1;
      let x: number;
      if (i === 0) x = 0;                       // first endpoint pinned to x=0
      else if (i === last) x = 1;               // last endpoint pinned to x=1
      else {
        const loX = pts[i - 1].x + 1e-3;
        const hiX = pts[i + 1].x - 1e-3;
        x = clamp(mx / w, loX, hiX);            // keep order, index stays stable
      }
      pts[i] = { x, y: clamp(1 - my / h, 0, 1) };
      setChannelPoints(ch, pts);
      opts.onApply();
    } else if (curveDrag.mode === "split") {
      const p = toneCurve.value.parametric;
      const keys = ["shadowSplit", "midtoneSplit", "highlightSplit"] as const;
      const vals = [p.shadowSplit, p.midtoneSplit, p.highlightSplit];
      const lo = curveDrag.index > 0 ? vals[curveDrag.index - 1] + 4 : 4;
      const hi = curveDrag.index < 2 ? vals[curveDrag.index + 1] - 4 : 96;
      setParam(keys[curveDrag.index], Math.round(clamp((mx / w) * 100, lo, hi)));
      return; // setParam already re-rendered
    } else {
      // region: vertical drag adjusts the region slider (full height ≈ 150 units)
      const delta = ((curveDrag.startMy - my) / curveDrag.h) * 150;
      setParam(curveDrag.key, Math.round(clamp(curveDrag.startVal + delta, -100, 100)));
      return;
    }
    renderCurveCanvas();
  }

  function onCurveMouseUp(): void {
    curveDrag = null;
    window.removeEventListener("mousemove", onCurveMouseMove);
    window.removeEventListener("mouseup", onCurveMouseUp);
  }

  function onCurveDoubleClick(e: MouseEvent): void {
    const c = curveCoords(e);
    if (!c) return;
    const { mx, my, w, h } = c;
    if (curveChannel.value === "parametric") {
      // Reset the region under the cursor to 0.
      const region = REGION_BY_INDEX[regionForX(toneCurve.value.parametric, clamp(mx / w, 0, 1))];
      setParam(region, 0);
      return;
    }
    const ch = curveChannel.value as PointChannel;
    const pts = toneCurve.value[ch];
    const idx = hitTest(pts, w, h, mx, my);
    if (idx > 0 && idx < pts.length - 1) {
      setChannelPoints(ch, pts.filter((_, i) => i !== idx));
      curveActive.value = -1;
      opts.onApply();
      renderCurveCanvas();
    }
  }

  // Hover (parametric only): highlight the tonal range under the cursor that a drag
  // would adjust, mirroring Lightroom's region preview.
  function onCurveHover(e: MouseEvent): void {
    if (curveDrag) return; // during a drag the affected region is fixed; don't re-pick it
    if (curveChannel.value !== "parametric") {
      if (curveHover.value !== -1) { curveHover.value = -1; renderCurveCanvas(); }
      return;
    }
    const c = curveCoords(e);
    if (!c) return;
    const region = regionForX(toneCurve.value.parametric, clamp(c.mx / c.w, 0, 1));
    if (region !== curveHover.value) { curveHover.value = region; renderCurveCanvas(); }
  }

  function onCurveLeave(): void {
    if (curveDrag) return; // keep the band while a region drag is in flight (cursor may exit)
    if (curveHover.value !== -1) { curveHover.value = -1; renderCurveCanvas(); }
  }

  // Init curve canvas. The canvas is torn down/recreated when the panel toggles
  // (crop mode, image switch), so disconnect the previous observer or each
  // round-trip leaks one.
  let curveResizeObs: ResizeObserver | null = null;
  watch(curveCanvas, (cvs) => {
    curveResizeObs?.disconnect();
    curveResizeObs = null;
    if (cvs) {
      curveResizeObs = new ResizeObserver(() => renderCurveCanvas());
      curveResizeObs.observe(cvs);
    }
  });
  onBeforeUnmount(() => {
    curveResizeObs?.disconnect();
    curveResizeObs = null;
    onCurveMouseUp(); // drop any in-flight drag's window listeners
  });

  return {
    toneCurve, curveChannel, curveActive, curveHover, curveCanvas,
    CURVE_TABS, PARAM_REGIONS, presetNames,
    renderCurveCanvas, setCurveChannel, resetCurve, applyCurvePreset,
    paramValue, setParam,
    onCurveMouseDown, onCurveHover, onCurveLeave, onCurveDoubleClick,
  };
}
