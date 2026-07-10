<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, shallowRef, watch } from "vue";
import { PipelineRenderer, type EditParams } from "./rendering/pipeline-renderer";
import { renderHistogram } from "./rendering/histogram";
import {
  curveToLUT, buildToneCurveLUT, defaultToneCurve, normalizeToneCurve,
  renderToneCurve, hitTest, hitTestSplit, regionForX,
  CURVE_PRESETS, type BasicAdjust, type CurvePoint, type ToneCurve, type ToneChannel, type PointChannel,
} from "./rendering/curve";
import {
  defaultCrop, cloneCrop, isDefaultCrop, imageDims, buildCropTransform,
  cropOutputRect, cropOutputSize, straightenedBBox, constrainCrop,
  applyAspectRatio, resolveAspectRatio, resolveAspectFraction, cropOutputSizeForAspect,
  rotate90, cornersInsideImage,
  ASPECT_PRESETS, customAspectKey, parseCustomAspect, ratioToFraction,
  type CropState, type Rect,
} from "./rendering/crop";
import {
  loadState, saveState, loadThumbs, saveThumbs, generateThumb,
  type PersistedEdit, type PersistedState,
} from "./persistence";

// ── types ──

// `invalid` is frontend-only (set when the server can no longer decode the
// source, e.g. tmp/sessions was cleared); never persisted.
type Source = { id: string; name: string; size: number; embeddedUrl: string; invalid?: boolean; };
type RecipeKey = "exposure"|"contrast"|"highlights"|"shadows"|"whites"|"blacks"|"vibrance"|"saturation"|"temperature"|"tint"|"clarity"|"dehaze";
type Recipe = Record<RecipeKey, number>;
type SliderSpec = { key: RecipeKey; label: string; min: number; max: number; step: number };
type SliderGroup = { title: string; items: SliderSpec[] };

const API = (import.meta.env.VITE_API_URL as string | undefined) ?? "/api";

const defaultRecipe = (): Recipe => ({
  exposure: 0, contrast: 0, highlights: 0, shadows: 0,
  whites: 0, blacks: 0, vibrance: 0, saturation: 0,
  temperature: 6500, tint: 0, clarity: 0, dehaze: 0,
});

const groups: SliderGroup[] = [
  { title: "Tone", items: [
    { key: "exposure", label: "Exposure", min: -5, max: 5, step: 0.1 },
    { key: "contrast", label: "Contrast", min: -100, max: 100, step: 1 },
    { key: "highlights", label: "Highlights", min: -100, max: 100, step: 1 },
    { key: "shadows", label: "Shadows", min: -100, max: 100, step: 1 },
    { key: "whites", label: "Whites", min: -100, max: 100, step: 1 },
    { key: "blacks", label: "Blacks", min: -100, max: 100, step: 1 },
  ]},
  { title: "Presence", items: [
    { key: "clarity", label: "Clarity", min: -100, max: 100, step: 1 },
    { key: "dehaze", label: "Dehaze", min: -100, max: 100, step: 1 },
  ]},
  { title: "Color", items: [
    { key: "temperature", label: "Temp", min: 2000, max: 12000, step: 50 },
    { key: "tint", label: "Tint", min: -100, max: 100, step: 1 },
    { key: "vibrance", label: "Vibrance", min: -100, max: 100, step: 1 },
    { key: "saturation", label: "Saturation", min: -100, max: 100, step: 1 },
  ]},
];

const SLIDER_DEFAULTS: Record<string, number> = {
  exposure: 0, contrast: 0, highlights: 0, shadows: 0,
  whites: 0, blacks: 0, clarity: 0, dehaze: 0,
  temperature: 6500, tint: 0, vibrance: 0, saturation: 0,
};

// ── state ──

const sources = ref<Source[]>([]);
const activeId = ref<string | null>(null);
const recipe = reactive<Recipe>(defaultRecipe());
const status = ref<"idle"|"uploading"|"rendering"|"error">("idle");
const errorMessage = ref<string | null>(null);
const isDragging = ref(false);
const fileInput = ref<HTMLInputElement | null>(null);
const canvasRef = ref<HTMLCanvasElement | null>(null);
const timing = ref<number | null>(null);
const dcpCode = ref("");  // empty = auto-detect
// AI RAW denoise. Applied in the worker on the Bayer mosaic before demosaic, so
// changing it re-decodes linear.bin (like dcpCode) rather than re-running the
// WebGL shader. amount is 0..100 (normalised to 0..1 for the API).
const denoise = reactive({ enabled: false, model: "wavelet", amount: 100 });
const denoiseBusy = ref(false);
const exporting = ref(false);
// Hold-to-compare: while true we draw the unedited original (baseline params +
// identity tone curve) so the before/after is easy to eyeball; release restores
// the live edit. crop/denoise/DCP are baked into linear.bin, so they stay applied.
const showOriginal = ref(false);
let currentSourceId = "";  // server id of the image currently in the renderer

let webglRenderer: PipelineRenderer | null = null;
const p3Supported = ref(false);
let rafId = 0;
let drawPending = false;
// Histogram updates are throttled and always deferred off the synchronous
// draw/decode path: the read-back stalls the main thread, and at 60fps it would
// recompute far more often than anyone can read. ~11 Hz with a trailing update
// keeps it responsive without taxing slider drags or inflating decode timing.
let histoTimer = 0;
let histoLast = 0;
const HISTO_MIN_MS = 90;

// ── Pan / Zoom state ──
const zoom = ref(0); // 0 = no image, 1 = fit to viewport
const pan = reactive({ x: 0, y: 0 });
const fitScale = ref(1);
// imageW/imageH are the *displayed output* dims (the crop result in normal mode,
// or the straighten bounding box in the crop editor). srcW/srcH are the decoded
// source texture dims that the crop transform maps from.
const imageW = ref(0);
const imageH = ref(0);
const srcW = ref(0);
const srcH = ref(0);
// Full-resolution source dims (before the preview's half-size/max-size
// downscale), reported by the worker so zoom % can be relative to the original.
const srcFullW = ref(0);
const srcFullH = ref(0);
const viewportRef = ref<HTMLDivElement | null>(null);
const isPanning = ref(false);
let panStartX = 0;
let panStartY = 0;
let panStartPanX = 0;
let panStartPanY = 0;
let resizeObs: ResizeObserver | null = null;

// ── HSL & Color Grading state ──

const HSL_RANGES = [
  { name: "Red",     color: "#e04040" },
  { name: "Orange",  color: "#e08040" },
  { name: "Yellow",  color: "#c0b030" },
  { name: "Green",   color: "#40b040" },
  { name: "Aqua",    color: "#40a0a0" },
  { name: "Blue",    color: "#4060d0" },
  { name: "Purple",  color: "#8040c0" },
  { name: "Magenta", color: "#c04090" },
];

const hslHue = reactive([0, 0, 0, 0, 0, 0, 0, 0]);
const hslSat = reactive([0, 0, 0, 0, 0, 0, 0, 0]);
const hslLum = reactive([0, 0, 0, 0, 0, 0, 0, 0]);
const hslTab = ref<"hue"|"sat"|"lum">("hue");

const grading = reactive({
  shH: 0, shS: 0,
  mdH: 0, mdS: 0,
  hlH: 0, hlS: 0,
  blend: 50,
  balance: 0,
});

// View settings (not part of the per-image recipe): tone-mapping look + display gamut.
const viewSettings = reactive({ viewTransform: 0, displayGamut: 0 });

// ── Crop & Straighten ──
//
// Part of the per-image edit (snapshot/history/persistence). The crop editor
// renders the full straightened image with an overlay; committing just switches
// the display back to the cropped output. All geometry lives in crop.ts.
const crop = reactive<CropState>(defaultCrop());
const cropMode = ref(false);
// Aspect lock for the crop box. Defaults to the image's own ratio; part of
// the per-image snapshot so it survives image switches / undo / persistence.
const DEFAULT_ASPECT = "orig";
const cropAspect = ref<string>(DEFAULT_ASPECT);
// Crop-editor render window (output-frame px) + the canvas scale used to draw it,
// kept so the overlay can map between screen, output-frame and crop-box space.
const cropBBox = reactive<Rect>({ x: 0, y: 0, w: 1, h: 1 });
const cropRenderScale = ref(1);
const cropOverlayRef = ref<SVGSVGElement | null>(null);
const WORKSPACE_BG: [number, number, number] = [0.07, 0.07, 0.08];
const CROP_EDITOR_MAX = 1800; // cap the editor preview's long edge (px)

function setCrop(patch: Partial<CropState>): void {
  Object.assign(crop, patch);
}
function currentImageDims(): [number, number] {
  return imageDims(srcW.value, srcH.value, crop.orientation);
}

function hslValue(i: number): number {
  if (hslTab.value === "hue") return hslHue[i];
  if (hslTab.value === "sat") return hslSat[i];
  return hslLum[i];
}
function setHsl(i: number, v: number): void {
  if (hslTab.value === "hue") hslHue[i] = v;
  else if (hslTab.value === "sat") hslSat[i] = v;
  else hslLum[i] = v;
}
function gradingColor(key: string): string {
  const g = grading as Record<string, number>;
  const h = g[key + "H"] ?? 0;
  const s = g[key + "S"] ?? 0;
  // Convert hue [-180,180] + sat [0,100] to CSS hsl
  const hueDeg = ((h % 360) + 360) % 360;
  return `hsl(${hueDeg}, ${s}%, 50%)`;
}
function resetHslGrading(): void {
  for (let i = 0; i < 8; i++) { hslHue[i] = 0; hslSat[i] = 0; hslLum[i] = 0; }
  grading.shH = 0; grading.shS = 0;
  grading.mdH = 0; grading.mdS = 0;
  grading.hlH = 0; grading.hlS = 0;
  grading.blend = 50;
  grading.balance = 0;
}

// ── Tone Curve (Lightroom-compatible: parametric + RGB/R/G/B point curves) ──

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

const isPointChannel = (ch: ToneChannel): ch is PointChannel => ch !== "parametric";

// Which rows/groups hold a non-default value. Drives the brightened row text
// and the accent dot on a group header, so edits are visible without opening
// or reading every panel.
const isEdited = (key: RecipeKey): boolean => recipe[key] !== SLIDER_DEFAULTS[key];
const groupEdited = (group: SliderGroup): boolean => group.items.some(s => isEdited(s.key));
const hslEdited = computed(() =>
  hslHue.some(v => v !== 0) || hslSat.some(v => v !== 0) || hslLum.some(v => v !== 0));
const gradingEdited = computed(() => Object.values(grading).some(v => v !== 0));

// Basic-panel values currently baked into the GPU curve LUT (Contrast, Blacks
// and positive Whites are display-referred stages of the LUT chain, not shader
// uniforms). null = the LUT holds the identity curve (hold-to-compare swapped
// it in).
let bakedBasic: BasicAdjust | null = { contrast: 0, blacks: 0, whites: 0 };

function currentBasic(): BasicAdjust {
  return { contrast: recipe.contrast, blacks: recipe.blacks, whites: recipe.whites };
}

function sameBasic(a: BasicAdjust, b: BasicAdjust): boolean {
  // Negative Whites is a shader uniform, not part of the bake — clamp both
  // sides so dragging it below zero doesn't rebake the LUT every frame.
  return a.contrast === b.contrast && a.blacks === b.blacks
    && Math.max(a.whites, 0) === Math.max(b.whites, 0);
}

/** Rebake + upload the curve LUT with the live tone curve and Basic values. */
function bakeCurveLUT(): void {
  if (!webglRenderer) return;
  const basic = currentBasic();
  webglRenderer.uploadCurveLUT(buildToneCurveLUT(toneCurve.value, basic));
  bakedBasic = basic;
}

function applyCurveLUT(): void {
  bakeCurveLUT();
  scheduleWebGLDraw();
}

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
  bakeCurveLUT();
  drawWebGL(); // immediate, no RAF-batching for reset
}

function applyCurvePreset(name: string): void {
  const preset = CURVE_PRESETS[name];
  if (!preset) return;
  toneCurve.value = { ...toneCurve.value, rgb: preset.map(p => ({ ...p })) };
  curveChannel.value = "rgb";
  curveActive.value = -1;
  applyCurveLUT();
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
  applyCurveLUT();
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
      applyCurveLUT();
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
    applyCurveLUT();
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
    applyCurveLUT();
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

// ── History (undo / redo) ──

type Snapshot = {
  recipe: Recipe;
  hslHue: number[]; hslSat: number[]; hslLum: number[];
  grading: typeof grading;
  curve: ToneCurve;
  crop: CropState;
  aspect?: string;  // crop aspect-lock key; optional: absent in older persisted sessions
  dcp: string;
  denoise?: typeof denoise;  // optional: absent in pre-denoise persisted sessions
};

const defaultDenoise = (): typeof denoise => ({ enabled: false, model: "wavelet", amount: 100 });

const MAX_HISTORY = 100;
const HISTORY_DEBOUNCE = 300;

// shallowRef: snapshots are immutable once captured and only length/index are
// read reactively — deep-proxying up to 100 snapshots per push is pure cost.
const history = shallowRef<Snapshot[]>([]);
const historyIndex = ref(-1);
const pendingDirty = ref(false);
let isRestoring = false;
// When true, the dcpCode watcher skips its re-decode — used while we load a
// source explicitly (switching images / restoring) to avoid a double decode.
let suppressDcpReload = false;
let historyTimer = 0;

function defaultSnapshot(): Snapshot {
  return {
    recipe: defaultRecipe(),
    hslHue: [0, 0, 0, 0, 0, 0, 0, 0],
    hslSat: [0, 0, 0, 0, 0, 0, 0, 0],
    hslLum: [0, 0, 0, 0, 0, 0, 0, 0],
    grading: { shH: 0, shS: 0, mdH: 0, mdS: 0, hlH: 0, hlS: 0, blend: 50, balance: 0 } as typeof grading,
    curve: defaultToneCurve(),
    crop: defaultCrop(),
    aspect: DEFAULT_ASPECT,
    dcp: "",
    denoise: defaultDenoise(),
  };
}

const canUndo = computed(() => historyIndex.value > 0 || pendingDirty.value);
const canRedo = computed(() => historyIndex.value < history.value.length - 1);

function captureSnapshot(): Snapshot {
  return {
    recipe: { ...recipe },
    hslHue: [...hslHue], hslSat: [...hslSat], hslLum: [...hslLum],
    grading: { ...grading },
    curve: normalizeToneCurve(toneCurve.value),
    crop: cloneCrop(crop),
    aspect: cropAspect.value,
    dcp: dcpCode.value,
    denoise: { ...denoise },
  };
}

function snapshotsEqual(a: Snapshot, b: Snapshot): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

// Commit the current edit state as a new history entry, dropping any redo branch.
function commitHistory(): void {
  pendingDirty.value = false;
  const snap = captureSnapshot();
  const cur = history.value[historyIndex.value];
  if (cur && snapshotsEqual(cur, snap)) return;
  const next = history.value.slice(0, historyIndex.value + 1);
  next.push(snap);
  if (next.length > MAX_HISTORY) next.shift();
  history.value = next;
  historyIndex.value = next.length - 1;
}

// Coalesce rapid edits (slider/curve drags) into one entry, committed after a quiet period.
function scheduleHistoryCommit(): void {
  if (isRestoring) return;
  pendingDirty.value = true;
  if (historyTimer) clearTimeout(historyTimer);
  historyTimer = window.setTimeout(() => { historyTimer = 0; commitHistory(); }, HISTORY_DEBOUNCE);
}

function flushPendingHistory(): void {
  if (historyTimer) { clearTimeout(historyTimer); historyTimer = 0; }
  if (pendingDirty.value) commitHistory();
}

// Push a snapshot into the live reactive edit state (no draw scheduling — the
// caller decides whether to redraw or re-decode).
function setEditState(s: Snapshot): void {
  Object.assign(recipe, s.recipe);
  for (let i = 0; i < 8; i++) { hslHue[i] = s.hslHue[i]; hslSat[i] = s.hslSat[i]; hslLum[i] = s.hslLum[i]; }
  Object.assign(grading, s.grading);
  toneCurve.value = normalizeToneCurve(s.curve);
  curveActive.value = -1;
  Object.assign(crop, s.crop ? cloneCrop(s.crop) : defaultCrop());
  // Older snapshots have no aspect: fall back to "free" so a legacy crop box
  // that doesn't match the new default lock isn't reshaped by the next drag.
  cropAspect.value = s.aspect ?? "free";
  Object.assign(denoise, s.denoise ?? defaultDenoise());
  dcpCode.value = s.dcp;
  // Rebake with the snapshot's Basic values (bakeCurveLUT also syncs bakedBasic).
  // Uploading a curve-only LUT here would leave a stale bakedBasic: if the
  // snapshot's Contrast/Blacks happen to equal it, drawWebGL's sameBasic check
  // skips the rebake and the GPU keeps rendering without them.
  if (webglRenderer) bakeCurveLUT();
  renderCurveCanvas();
}

function applySnapshot(s: Snapshot): void {
  isRestoring = true;
  setEditState(s); // setting dcpCode here may trigger a re-decode (intended for undo/redo)
  scheduleWebGLDraw();
  nextTick(() => { isRestoring = false; });
}

function undo(): void {
  flushPendingHistory();
  if (historyIndex.value <= 0) return;
  historyIndex.value--;
  applySnapshot(history.value[historyIndex.value]);
  nextTick(() => schedulePersist());
}

function redo(): void {
  flushPendingHistory();
  if (historyIndex.value >= history.value.length - 1) return;
  historyIndex.value++;
  applySnapshot(history.value[historyIndex.value]);
  nextTick(() => schedulePersist());
}

function initHistory(): void {
  history.value = [captureSnapshot()];
  historyIndex.value = 0;
  pendingDirty.value = false;
}
initHistory();

// ── Per-image edits + persistence ──
//
// Each imported source owns an independent edit (snapshot + undo history). The
// live reactive state (recipe/hsl/grading/curve/dcp + history) always mirrors
// the active image; switching images saves the outgoing one here and loads the
// incoming one back.

type ImageEdit = PersistedEdit<Snapshot>;
const edits = new Map<string, ImageEdit>();

const thumbs = reactive<Record<string, string>>({});

let persistTimer = 0;
const PERSIST_DEBOUNCE = 600;

// Copy the current live edit (snapshot + history) into the map under `id`.
function syncLiveToMap(id: string | null): void {
  if (!id) return;
  edits.set(id, {
    snapshot: captureSnapshot(),
    history: history.value.slice(),
    historyIndex: historyIndex.value,
  });
}

// Load an image's edit into the live reactive state (defaults if none stored).
// Does not decode/draw — the caller pairs this with loadSource().
function loadEditFromMap(id: string): void {
  const e = edits.get(id);
  isRestoring = true;
  suppressDcpReload = true;
  if (e) {
    setEditState(e.snapshot);
    history.value = e.history.map(s => ({ ...s }));
    historyIndex.value = Math.min(Math.max(0, e.historyIndex), history.value.length - 1);
  } else {
    setEditState(defaultSnapshot());
    history.value = [captureSnapshot()];
    historyIndex.value = 0;
  }
  pendingDirty.value = false;
  nextTick(() => { isRestoring = false; suppressDcpReload = false; });
}

function persistNow(): void {
  syncLiveToMap(activeId.value);
  const editsObj: Record<string, ImageEdit> = {};
  for (const [id, e] of edits) editsObj[id] = e;
  const state: PersistedState<Snapshot, typeof viewSettings> = {
    version: 1,
    activeId: activeId.value,
    viewSettings: { ...viewSettings },
    sources: sources.value.map(s => ({ id: s.id, name: s.name, size: s.size, embeddedUrl: s.embeddedUrl })),
    edits: editsObj,
  };
  void saveState(state);
}

function schedulePersist(): void {
  if (isRestoring) return;
  if (persistTimer) clearTimeout(persistTimer);
  persistTimer = window.setTimeout(() => { persistTimer = 0; persistNow(); }, PERSIST_DEBOUNCE);
}

// Resolve a possibly-relative API url to something <img>/fetch can use.
function resolveUrl(u: string): string { return isAbsoluteUrl(u) ? u : `${API}${u}`; }

// Thumbnail shown in the filmstrip / preview fallback: prefer the locally
// cached copy (survives server eviction), else the live server preview.
function thumbSrc(s: Source): string {
  return thumbs[s.id] ?? (s.embeddedUrl ? resolveUrl(s.embeddedUrl) : "");
}

async function cacheThumb(s: Source): Promise<void> {
  if (thumbs[s.id] || !s.embeddedUrl) return;
  const data = await generateThumb(resolveUrl(s.embeddedUrl));
  if (data) { thumbs[s.id] = data; void saveThumbs({ ...thumbs }); }
}

function markInvalid(id: string): void {
  const s = sources.value.find(x => x.id === id);
  if (s) s.invalid = true;
  status.value = "error";
  errorMessage.value = "源文件已失效（服务器缓存可能已被清理），请重新导入这张图片。";
}

// Denoise params for the render-linear request. amount is normalised to 0..1;
// disabled (or amount 0) tells the worker to skip inference entirely.
function denoisePayload(): { enabled: boolean; model: string; amount: number } {
  return { enabled: denoise.enabled, model: denoise.model, amount: denoise.amount / 100 };
}

// Decode `id`'s linear data and render it into the (reused) WebGL pipeline.
// Returns false if the source can no longer be decoded server-side.
async function loadSource(id: string, opts: { resetView?: boolean } = {}): Promise<boolean> {
  const src = sources.value.find(s => s.id === id);
  if (!src) return false;
  currentSourceId = id;
  status.value = "rendering";
  errorMessage.value = null;
  timing.value = null; // stale timing would mask the live status in the footer
  const t0 = performance.now();
  try {
    const linRes = await fetch(`${API}/render-linear`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ sourceId: id, halfSize: false, maxSize: 2560, dcpCode: dcpCode.value, denoise: denoisePayload() }),
    });
    if (linRes.status === 404) { markInvalid(id); return false; }
    if (!linRes.ok) throw new Error(await linRes.text());
    const linMeta = await linRes.json() as { width: number; height: number; fullWidth?: number; fullHeight?: number; linearUrl: string; colorProfile?: ColorProfileMeta };
    const binRes = await fetch(resolveUrl(linMeta.linearUrl));
    if (binRes.status === 404) { markInvalid(id); return false; }
    if (!binRes.ok) throw new Error("Failed to fetch linear data");
    const linearFloat = new Float32Array(await binRes.arrayBuffer());

    hasLinearData = true;
    profileCurveLUT = buildProfileLUT(linMeta.colorProfile);
    srcW.value = linMeta.width;
    srcH.value = linMeta.height;
    srcFullW.value = linMeta.fullWidth ?? linMeta.width;
    srcFullH.value = linMeta.fullHeight ?? linMeta.height;
    if (opts.resetView) { zoom.value = 1; pan.x = 0; pan.y = 0; }

    await nextTick();
    if (!canvasRef.value) return false;
    if (!webglRenderer) {
      webglRenderer = new PipelineRenderer(canvasRef.value);
      p3Supported.value = webglRenderer.p3Supported;
    }
    webglRenderer.uploadImage(linearFloat, linMeta.width, linMeta.height);
    bakeCurveLUT();
    webglRenderer.uploadProfileCurveLUT(profileCurveLUT);
    // Apply the current crop/straighten (sets output dims, fit, draws, histogram).
    applyCropRender();
    src.invalid = false;
    status.value = "idle";
    timing.value = Math.round(performance.now() - t0);
    return true;
  } catch (err) {
    status.value = "error";
    errorMessage.value = err instanceof Error ? err.message : String(err);
    return false;
  }
}

// Switch the active image: stash the current edit, load the target's edit + pixels.
async function selectSource(id: string): Promise<void> {
  if (id === activeId.value) return;
  flushPendingHistory();
  cropMode.value = false; // leave the crop editor when switching images
  syncLiveToMap(activeId.value);
  activeId.value = id;
  loadEditFromMap(id);
  await loadSource(id, { resetView: true });
  schedulePersist();
}

// Remove an image from the library: drop its edit, thumbnail and server-side
// cached copy. The original file on the user's disk is never touched — imports
// only ever copy bytes into the server cache.
async function removeSource(id: string): Promise<void> {
  const idx = sources.value.findIndex(s => s.id === id);
  if (idx < 0) return;
  sources.value = sources.value.filter(s => s.id !== id);
  edits.delete(id);
  if (thumbs[id]) { delete thumbs[id]; void saveThumbs({ ...thumbs }); }
  void fetch(`${API}/sources/${id}`, { method: "DELETE" }).catch(() => {});
  if (id === activeId.value) {
    cropMode.value = false;
    const next = sources.value[Math.min(idx, sources.value.length - 1)];
    if (next) {
      activeId.value = next.id;
      loadEditFromMap(next.id);
      await loadSource(next.id, { resetView: true });
    } else {
      // Keep webglRenderer alive: its canvas context is single-use (destroy()
      // loses it for good), so the next import reuses it. The canvas is hidden
      // via the activeSource gate in the template.
      activeId.value = null;
      currentSourceId = "";
      hasLinearData = false;
      loadEditFromMap(id); // id is gone from the map -> resets the live edit to defaults
      status.value = "idle";
      errorMessage.value = null;
    }
  }
  persistNow();
}

const activeSource = computed(() => sources.value.find(s => s.id === activeId.value) ?? null);

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

// ── WebGL ──

function buildPipelineParams(): Partial<EditParams> {
  return {
    exposure: recipe.exposure,
    saturation: 1 + recipe.saturation / 100,
    highlights: recipe.highlights / 100,
    shadows: recipe.shadows / 100,
    whites: recipe.whites / 100,
    vibrance: 1 + recipe.vibrance / 100,
    clarity: recipe.clarity,
    dehaze: recipe.dehaze,
    temperature: recipe.temperature,
    tint: recipe.tint,
    hslH: hslHue.map(v => v / 100),
    hslS: hslSat.map(v => v / 100),
    hslL: hslLum.map(v => v / 100),
    gradShH: grading.shH / 180, gradShS: grading.shS / 100,
    gradMdH: grading.mdH / 180, gradMdS: grading.mdS / 100,
    gradHlH: grading.hlH / 180, gradHlS: grading.hlS / 100,
    gradBlend: grading.blend / 100,
    gradBalance: grading.balance / 100,
    viewTransform: viewSettings.viewTransform,
    displayGamut: viewSettings.displayGamut,
  };
}

// ── DCP profile tone curve (camera display rendering, applied in the view transform) ──

type ColorProfileMeta = { profileToneCurve?: [number, number][] | null };
let profileCurveLUT: Float32Array | null = null;

function buildProfileLUT(cp: ColorProfileMeta | undefined): Float32Array | null {
  const pts = cp?.profileToneCurve;
  return (pts && pts.length >= 2) ? curveToLUT(pts.map(([x, y]) => ({ x, y }))) : null;
}

// ── Histogram ──

// Whether the renderer holds decoded pixels (histogram guard). The decoded
// Float32Array itself lives on the GPU after upload — keeping a JS reference
// here would pin ~50 MB per image for nothing.
let hasLinearData = false;
let histoBusy = false;
const histoCanvasRef = ref<HTMLCanvasElement | null>(null);

// In the crop editor the canvas renders a padded straighten bbox whose
// out-of-image fill would be binned as real pixels — hand the histogram the
// tight crop box instead (it re-renders offscreen with its own transform).
function cropHistogramView(): { width: number; height: number; texXform: Float32Array } {
  const [iw, ih] = currentImageDims();
  const rect = cropOutputRect(crop, iw, ih);
  const [ow, oh] = cropOutputSize(crop, srcW.value, srcH.value);
  return { width: ow, height: oh, texXform: buildCropTransform(crop, srcW.value, srcH.value, rect) };
}

async function updateHistogram(): Promise<void> {
  const canvas = histoCanvasRef.value;
  if (!canvas || !hasLinearData || !imageW.value || !imageH.value || !webglRenderer) return;
  if (histoBusy) { scheduleHistogram(); return; } // a read is in flight; retry after it
  const rect = canvas.getBoundingClientRect();
  let w = rect.width;
  let h = rect.height;
  // If canvas not laid out, try parent dimensions; retry next frame as last resort
  if (w <= 0 || h <= 0) {
    const parent = canvas.parentElement;
    if (parent) { w = parent.clientWidth - 32; h = 80; }
    if (w <= 0) { requestAnimationFrame(() => void updateHistogram()); return; }
  }
  histoBusy = true;
  try {
    const bins = await webglRenderer.readHistogram(cropMode.value ? cropHistogramView() : undefined);
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
  } finally {
    histoBusy = false;
  }
}

function scheduleHistogram(): void {
  if (histoTimer) return; // a trailing update is already pending
  const wait = Math.max(0, HISTO_MIN_MS - (performance.now() - histoLast));
  histoTimer = window.setTimeout(() => {
    histoTimer = 0;
    histoLast = performance.now();
    void updateHistogram();
  }, wait);
}

function scheduleWebGLDraw(): void {
  if (drawPending) return;
  drawPending = true;
  rafId = requestAnimationFrame(() => { drawPending = false; drawWebGL(); scheduleHistogram(); });
}

// Edit-free baseline for hold-to-compare: keep view-only settings (look / display
// gamut) but drop every per-image adjustment. The matching identity tone curve is
// swapped in by the showOriginal watcher (the curve lives in a GPU LUT, not params).
const IDENTITY_CURVE_LUT = buildToneCurveLUT(defaultToneCurve());
function baselineParams(): Partial<EditParams> {
  return { viewTransform: viewSettings.viewTransform, displayGamut: viewSettings.displayGamut };
}

// On-screen device-pixel footprint of the preview, as a fraction of the image's
// logical (decoded-preview) resolution. The canvas is rendered at this scale and
// CSS-upscaled to fit, so the GPU shades ~one fragment per visible device pixel
// instead of the full decoded frame on every edit. Capped at 1 (never supersample
// past the decoded source); the guard keeps full res until the fit is measured.
function computePreviewScale(): number {
  const displayScale = fitScale.value * zoom.value; // decoded-preview px → screen CSS px
  if (!displayScale || !Number.isFinite(displayScale)) return 1;
  const dpr = window.devicePixelRatio || 1;
  return Math.min(1, displayScale * dpr);
}

function drawWebGL(): void {
  if (!webglRenderer) return;
  // Contrast/Blacks/+Whites live in the curve LUT bake, not shader uniforms.
  // Draws are rAF-coalesced (scheduleWebGLDraw), so this rebakes at most once
  // per frame during a slider drag (sub-millisecond on the CPU).
  if (!showOriginal.value && (bakedBasic === null || !sameBasic(bakedBasic, currentBasic()))) {
    bakeCurveLUT();
  }
  webglRenderer.setPreviewScale(computePreviewScale());
  webglRenderer.draw(showOriginal.value ? baselineParams() : buildPipelineParams());
}

function startCompare(): void { if (activeSource.value && !cropMode.value) showOriginal.value = true; }
function endCompare(): void { showOriginal.value = false; }

function destroyWebGL(): void {
  if (rafId) { cancelAnimationFrame(rafId); rafId = 0; }
  if (histoTimer) { clearTimeout(histoTimer); histoTimer = 0; }
  drawPending = false;
  if (webglRenderer) { webglRenderer.destroy(); webglRenderer = null; }
}

// ── Crop rendering ──
//
// Normal view: render the crop box region (cropped output dims). Crop editor:
// render the full straightened image's bounding box so the user sees beyond the
// crop, with the overlay drawn on top.

function recomputeFit(): void {
  const vp = viewportRef.value;
  if (!vp || !imageW.value || !imageH.value) return;
  const margin = cropMode.value ? 0.86 : 1; // leave room for crop handles
  fitScale.value = Math.min(vp.clientWidth / imageW.value, vp.clientHeight / imageH.value) * margin;
}

function renderNormal(): void {
  if (!webglRenderer || !srcW.value || !srcH.value) return;
  const [iw, ih] = currentImageDims();
  const rect = cropOutputRect(crop, iw, ih);
  const [ow, oh] = cropOutputSize(crop, srcW.value, srcH.value);
  webglRenderer.setOutput(ow, oh, buildCropTransform(crop, srcW.value, srcH.value, rect), WORKSPACE_BG);
  imageW.value = ow;
  imageH.value = oh;
  recomputeFit();
  drawWebGL();
  scheduleHistogram();
}

function renderCropEditor(): void {
  if (!webglRenderer || !srcW.value || !srcH.value) return;
  const [iw, ih] = currentImageDims();
  const bbox = straightenedBBox(crop, iw, ih);
  Object.assign(cropBBox, bbox);
  const long = Math.max(bbox.w, bbox.h) || 1;
  const rs = Math.min(1, CROP_EDITOR_MAX / long);
  cropRenderScale.value = rs;
  const cw = Math.max(1, Math.round(bbox.w * rs));
  const ch = Math.max(1, Math.round(bbox.h * rs));
  webglRenderer.setOutput(cw, ch, buildCropTransform(crop, srcW.value, srcH.value, bbox), WORKSPACE_BG);
  imageW.value = cw;
  imageH.value = ch;
  recomputeFit();
  drawWebGL();
  scheduleHistogram();
}

function applyCropRender(): void {
  if (cropMode.value) renderCropEditor(); else renderNormal();
}

function enterCropMode(): void {
  if (!activeSource.value || cropMode.value) return;
  flushPendingHistory();
  cropMode.value = true;
  zoom.value = 1; pan.x = 0; pan.y = 0;
  // Untouched image with an aspect lock (e.g. the 4:3 default): propose the
  // largest centered box of that ratio, so the lock and the box agree.
  if (isDefaultCrop(crop)) {
    const ratio = lockedRatio.value;
    if (ratio != null) Object.assign(crop, applyAspectRatio(crop, ratio, srcW.value, srcH.value));
  }
  nextTick(renderCropEditor);
}

function exitCropMode(): void {
  if (!cropMode.value) return;
  flushPendingHistory();
  cropMode.value = false;
  zoom.value = 1; pan.x = 0; pan.y = 0;
  nextTick(renderNormal);
}

function toggleCropMode(): void {
  if (cropMode.value) exitCropMode(); else enterCropMode();
}

function resetCrop(): void {
  Object.assign(crop, defaultCrop());
  cropAspect.value = "free";
}

// ── Crop controls (aspect / straighten / rotate / flip) ──

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

// ── Crop overlay (output-frame coordinate space, matches the SVG viewBox) ──

type CropHandle = "l" | "r" | "t" | "b" | "tl" | "tr" | "bl" | "br";

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

function overlayPxPerScreen(): number {
  const svg = cropOverlayRef.value;
  if (!svg) return 1;
  const r = svg.getBoundingClientRect();
  return r.width ? cropBBox.w / r.width : 1; // output-frame px per screen px
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
  flushPendingHistory();
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

// ── Pan / Zoom ──

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

function onKeyDown(e: KeyboardEvent): void {
  const ae = document.activeElement as HTMLElement | null;
  const inEditableText = !!ae && (ae.tagName === "TEXTAREA" ||
    (ae.tagName === "INPUT" && !["range", "checkbox", "radio", "button", "submit"].includes((ae as HTMLInputElement).type)));

  // Undo / redo — work regardless of whether an image is loaded
  if ((e.ctrlKey || e.metaKey) && !inEditableText) {
    const k = e.key.toLowerCase();
    if (k === "z") { e.preventDefault(); if (e.shiftKey) redo(); else undo(); return; }
    if (k === "y") { e.preventDefault(); redo(); return; }
  }

  // Crop tool: R toggles, Esc / Enter commit & exit, X swaps orientation,
  // O cycles the guide overlay (Lightroom-style).
  if (!inEditableText && activeSource.value && !e.ctrlKey && !e.metaKey && !e.altKey) {
    if (e.key === "r" || e.key === "R") { e.preventDefault(); toggleCropMode(); return; }
    if (cropMode.value && (e.key === "Escape" || e.key === "Enter")) { e.preventDefault(); exitCropMode(); return; }
    if (cropMode.value && (e.key === "x" || e.key === "X")) { e.preventDefault(); swapAspect(); return; }
    if (cropMode.value && (e.key === "o" || e.key === "O")) { e.preventDefault(); cycleCropGuide(); return; }
  }

  // Backslash holds the "before" view; release (onKeyUp) restores the edit.
  if (!inEditableText && e.key === "\\" && activeSource.value && !cropMode.value) {
    e.preventDefault();
    showOriginal.value = true; // watcher guards against redundant redraws on repeat
    return;
  }

  if (!imageW.value || !imageH.value) return;
  if (cropMode.value) return; // crop editor owns the view; no pan/zoom shortcuts
  if (e.ctrlKey || e.metaKey) {
    switch (e.key) {
      case '0': e.preventDefault(); fitView(); break;
      case '1': e.preventDefault(); zoomToFull(); break;
      case '=': case '+': e.preventDefault(); zoomIn(); break;
      case '-': e.preventDefault(); zoomOut(); break;
    }
  }
}

function onKeyUp(e: KeyboardEvent): void {
  if (e.key === "\\") showOriginal.value = false; // release the hold-to-compare view
}

// ── upload ──

// Auto-redraw on edit. Suppressed during restore/switch so we don't flash the
// previous image with the new params before loadSource() uploads the pixels.
// Hold-to-compare swaps the tone-curve LUT (a GPU texture, not a draw param) for
// identity while previewing the original, restoring the live curve on release.
watch(showOriginal, (v) => {
  if (!webglRenderer) return;
  if (v) {
    webglRenderer.uploadCurveLUT(IDENTITY_CURVE_LUT);
    bakedBasic = null; // GPU LUT no longer matches the live bake
  } else {
    bakeCurveLUT();
  }
  scheduleWebGLDraw();
});

// One deep watcher per object, doing draw + history + persist together: a
// second deep watcher over the same objects would re-traverse them on every
// slider input for no benefit (history/persist scheduling self-guards on
// isRestoring and is debounced).
watch([recipe, hslHue, hslSat, hslLum, grading], () => {
  if (!isRestoring) scheduleWebGLDraw();
  scheduleHistoryCommit();
  schedulePersist();
}, { deep: true });
watch(viewSettings, () => { scheduleWebGLDraw(); schedulePersist(); }, { deep: true });

// Zoom/fit only move CSS pixels; the drawing buffer is rendered at the on-screen
// scale, so re-rasterise when it changes to stay crisp (zoom in) or shed fragments
// (zoom out / resize). rAF-batched, so wheel and resize bursts collapse to one draw.
watch([zoom, fitScale], () => { if (webglRenderer) scheduleWebGLDraw(); });

// Crop changes resize the output, so they re-render (not just redraw) the editor
// or the committed view. rAF-coalesced like scheduleWebGLDraw: crop drags emit
// mousemove faster than the display refreshes, and each render is a full
// setOutput + pipeline draw.
let cropRenderPending = false;
function scheduleCropRender(): void {
  if (cropRenderPending) return;
  cropRenderPending = true;
  requestAnimationFrame(() => { cropRenderPending = false; applyCropRender(); });
}
watch(crop, () => {
  if (!isRestoring) scheduleCropRender();
  scheduleHistoryCommit();
  schedulePersist();
}, { deep: true });

// History/persist for the edit state not covered above (redraws handled by
// their own paths: curve LUT bake, dcp/denoise re-decode; aspect is snapshot
// state but changes no pixels by itself).
watch([toneCurve, dcpCode, denoise, cropAspect],
  () => { scheduleHistoryCommit(); schedulePersist(); }, { deep: true });

// Re-decode when the user changes the DCP style (keeps the current view).
// Suppressed while a source load is already handling the decode.
watch(dcpCode, async () => {
  if (suppressDcpReload || !currentSourceId) return;
  await loadSource(currentSourceId, { resetView: false });
});

// Denoise is baked into linear.bin, so changes re-decode like dcpCode. Debounced
// because amount is a slider (the first decode runs inference; later ones hit the
// worker's cache and only re-blend). The amount slider is hidden while disabled,
// so a change here always alters the effective output.
let denoiseReloadTimer = 0;
watch(denoise, () => {
  if (suppressDcpReload || !currentSourceId) return;
  if (denoiseReloadTimer) clearTimeout(denoiseReloadTimer);
  denoiseReloadTimer = window.setTimeout(() => {
    denoiseReloadTimer = 0;
    denoiseBusy.value = true;
    void loadSource(currentSourceId, { resetView: false }).finally(() => { denoiseBusy.value = false; });
  }, 250);
}, { deep: true });

// Flush the latest edit on tab close. The IndexedDB write is async, so also
// flush whenever the tab goes hidden — that fires earlier and more reliably
// than beforeunload (mobile tab switches, window close).
function persistOnUnload(): void { persistNow(); }
function persistOnHidden(): void { if (document.visibilityState === "hidden") persistNow(); }

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeyDown);
  window.removeEventListener('keyup', onKeyUp);
  window.removeEventListener('beforeunload', persistOnUnload);
  document.removeEventListener('visibilitychange', persistOnHidden);
  resizeObs?.disconnect();
  curveResizeObs?.disconnect();
  curveResizeObs = null;
  destroyWebGL();
});

onMounted(async () => {
  window.addEventListener('keydown', onKeyDown);
  window.addEventListener('keyup', onKeyUp);
  window.addEventListener('beforeunload', persistOnUnload);
  document.addEventListener('visibilitychange', persistOnHidden);
  resizeObs = new ResizeObserver(() => recomputeFit());
  if (viewportRef.value) resizeObs.observe(viewportRef.value);

  Object.assign(thumbs, await loadThumbs());

  // Restore a previous session if one exists; otherwise auto-load the sample.
  if (await restoreSession()) return;
  try {
    const res = await fetch("/sample.arw");
    if (res.ok) {
      const blob = await res.blob();
      const file = new File([blob], "DSC01157.ARW", { type: blob.type });
      await uploadFiles([file]);
    }
  } catch (e) {
    console.warn("Auto-load sample failed:", e);
  }
});

// Rehydrate imported images + their edits from localStorage. Returns false if
// there's nothing to restore (so the caller falls back to the sample).
async function restoreSession(): Promise<boolean> {
  const persisted = await loadState<Snapshot, typeof viewSettings>();
  if (!persisted || !persisted.sources.length) return false;

  sources.value = persisted.sources.map(s => ({ ...s }));
  if (persisted.viewSettings) Object.assign(viewSettings, persisted.viewSettings);
  edits.clear();
  for (const [id, e] of Object.entries(persisted.edits)) edits.set(id, e);

  const targetId = persisted.activeId && sources.value.some(s => s.id === persisted.activeId)
    ? persisted.activeId
    : sources.value[0].id;
  activeId.value = targetId;
  loadEditFromMap(targetId);
  await loadSource(targetId, { resetView: true });

  // Backfill any thumbnails missing from the cache (e.g. first run after upgrade).
  for (const s of sources.value) if (!thumbs[s.id]) void cacheThumb(s);
  return true;
}

function pickFiles(): void { fileInput.value?.click(); }

async function onFileChange(e: Event): Promise<void> {
  const t = e.target as HTMLInputElement;
  if (!t.files) return;
  await uploadFiles(Array.from(t.files));
  t.value = "";
}

async function onDrop(e: DragEvent): Promise<void> {
  e.preventDefault();
  isDragging.value = false;
  if (!e.dataTransfer) return;
  await uploadFiles(Array.from(e.dataTransfer.files));
}

async function uploadFiles(files: File[]): Promise<void> {
  if (!files.length) return;
  status.value = "uploading";
  errorMessage.value = null;

  // Preserve the edit of the image we're leaving before importing new ones.
  flushPendingHistory();
  cropMode.value = false; // leave the crop editor when importing
  syncLiveToMap(activeId.value);

  let lastId: string | null = null;
  for (const file of files) {
    const formData = new FormData();
    formData.append("file", file);
    try {
      const res = await fetch(`${API}/sources`, { method: "POST", body: formData });
      if (!res.ok) throw new Error(await res.text());
      const source = await res.json() as Source;
      sources.value = [...sources.value, source];
      // Each new import starts from a fresh, independent edit.
      const snap = defaultSnapshot();
      edits.set(source.id, { snapshot: snap, history: [snap], historyIndex: 0 });
      void cacheThumb(source);
      lastId = source.id;
    } catch (err) {
      status.value = "error";
      errorMessage.value = err instanceof Error ? err.message : String(err);
      return;
    }
  }

  // Make the last imported image active and render it (loadSource sets the
  // final status to idle/error and records the decode timing).
  if (lastId) {
    activeId.value = lastId;
    loadEditFromMap(lastId);
    await loadSource(lastId, { resetView: true });
  } else {
    status.value = "idle";
  }
  persistNow();
}

function resetRecipe(): void { Object.assign(recipe, defaultRecipe()); resetHslGrading(); }

// ── Export ──

function exportFilename(): string {
  const name = activeSource.value?.name ?? "export";
  return `${name.replace(/\.[^.]+$/, "")}.jpg`;
}

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

// Re-render at full resolution, read pixels back, embed edit settings as XMP, download.
async function exportImage(): Promise<void> {
  if (!currentSourceId || !activeSource.value || exporting.value) return;
  exporting.value = true;
  errorMessage.value = null;
  status.value = "rendering";
  let renderer: PipelineRenderer | null = null;
  try {
    // 1. Decode full-resolution linear data (no half-size / no max-size cap)
    const linRes = await fetch(`${API}/render-linear`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ sourceId: currentSourceId, halfSize: false, maxSize: 0, dcpCode: dcpCode.value, denoise: denoisePayload() }),
    });
    if (!linRes.ok) throw new Error(await linRes.text());
    const linMeta = await linRes.json() as { width: number; height: number; linearUrl: string; colorProfile?: ColorProfileMeta };
    const binRes = await fetch(resolveUrl(linMeta.linearUrl));
    if (!binRes.ok) throw new Error("Failed to fetch full-resolution data");
    const linear = new Float32Array(await binRes.arrayBuffer());

    // 2. Render full-res off-screen with the current edit params + crop, read back as JPEG
    renderer = new PipelineRenderer(document.createElement("canvas"));
    renderer.uploadImage(linear, linMeta.width, linMeta.height);
    renderer.uploadCurveLUT(buildToneCurveLUT(toneCurve.value, currentBasic()));
    renderer.uploadProfileCurveLUT(buildProfileLUT(linMeta.colorProfile));
    const [iw, ih] = imageDims(linMeta.width, linMeta.height, crop.orientation);
    const rect = cropOutputRect(crop, iw, ih);
    // Snap the output dims to the locked aspect so e.g. a 4:3 crop exports at
    // an exact 4:3 pixel size instead of each axis rounding independently.
    const fraction = resolveAspectFraction(cropAspect.value, linMeta.width, linMeta.height, crop);
    const [ow, oh] = cropOutputSizeForAspect(crop, linMeta.width, linMeta.height, fraction);
    renderer.setOutput(ow, oh, buildCropTransform(crop, linMeta.width, linMeta.height, rect), WORKSPACE_BG);
    renderer.draw(buildPipelineParams());
    const blob = await renderer.toBlob("image/jpeg", 0.92);

    // 3. Embed edit settings (llr:* XMP + lossless LLR JSON) into the JPEG server-side
    const fd = new FormData();
    fd.append("file", blob, "export.jpg");
    fd.append("meta", JSON.stringify({ sourceId: currentSourceId, settings: captureSnapshot() }));
    const exRes = await fetch(`${API}/export`, { method: "POST", body: fd });
    if (!exRes.ok) throw new Error(await exRes.text());

    // 4. Download the finished file
    downloadBlob(await exRes.blob(), exportFilename());
    status.value = "idle";
  } catch (err) {
    status.value = "error";
    errorMessage.value = err instanceof Error ? err.message : String(err);
    console.error("[export] failed:", err);
  } finally {
    renderer?.destroy();
    exporting.value = false;
  }
}
function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n/1024).toFixed(0)} KB`;
  return `${(n/1048576).toFixed(1)} MB`;
}
function isAbsoluteUrl(u: string): boolean {
  return u.startsWith("blob:") || u.startsWith("data:") || u.startsWith("http");
}

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

// Build a filled-track gradient for a range input. Bipolar sliders (min<0<max)
// fill from the center toward the thumb; unipolar fill from the left.
function trackFill(value: number, min: number, max: number): string {
  const p = clamp((value - min) / (max - min), 0, 1) * 100;
  const z = min < 0 && max > 0 ? (-min) / (max - min) * 100 : 0;
  const a = Math.min(p, z);
  const b = Math.max(p, z);
  return `linear-gradient(to right, var(--track-bg) ${a}%, var(--accent) ${a}%, var(--accent) ${b}%, var(--track-bg) ${b}%)`;
}

// White balance reads as a colour axis, not an amount: an accent fill growing
// from the left would say "this is set" on a slider sitting at its default.
// Neutral is placed where the default value puts the thumb (6500K -> 45%).
const WB_TRACK: Partial<Record<RecipeKey, string>> = {
  temperature: "linear-gradient(to right, #3f7dff, #f2ead9 45%, #ffb648)",
  tint: "linear-gradient(to right, #4fc26a, #d8d8d8 50%, #d264d8)",
};

function sliderTrack(spec: SliderSpec): string {
  return WB_TRACK[spec.key] ?? trackFill(recipe[spec.key], spec.min, spec.max);
}

// Lightroom-style scroll-to-nudge: hovering any range slider and scrolling steps
// the value by one `step` (Shift ×10), instead of scrolling the panel. Applied via
// event delegation on the settings rail so every slider — base, HSL, grading,
// denoise, crop angle — gets it without per-input wiring.
const vWheelAdjust = {
  mounted(el: HTMLElement) {
    const onWheel = (e: WheelEvent) => {
      const input = (e.target as HTMLElement | null)?.closest?.(
        'input[type="range"]',
      ) as HTMLInputElement | null;
      if (!input || input.disabled) return;
      e.preventDefault();
      const step = Number(input.step) || 1;
      const min = Number(input.min);
      const max = Number(input.max);
      const cur = Number(input.value);
      const mult = e.shiftKey ? 10 : 1;
      const dir = e.deltaY < 0 ? 1 : -1; // scroll up → increase
      let next = clamp(cur + dir * step * mult, min, max);
      const decimals = (String(step).split(".")[1] || "").length;
      if (decimals) next = Number(next.toFixed(decimals));
      if (next === cur) return;
      input.value = String(next);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    (el as unknown as { _wheelAdjust?: (e: WheelEvent) => void })._wheelAdjust = onWheel;
  },
  unmounted(el: HTMLElement) {
    const fn = (el as unknown as { _wheelAdjust?: (e: WheelEvent) => void })._wheelAdjust;
    if (fn) el.removeEventListener("wheel", fn);
  },
};
</script>

<template>
  <div class="app" :class="{ 'is-drag': isDragging, 'no-filmstrip': !sources.length }"
    @dragover.prevent="isDragging = true"
    @dragleave.prevent="isDragging = false"
    @drop="onDrop">
    <header class="topbar">
      <div class="brand">
        <span class="brand-mark" aria-hidden="true" />
        <span class="brand-name">LLR</span>
      </div>
      <div class="topbar-actions">
        <button class="icon-btn" :disabled="!canUndo" @click="undo" title="Undo (Ctrl+Z)" aria-label="Undo">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M3 9h11a5 5 0 0 1 0 10H8" />
            <path d="M7 5L3 9l4 4" />
          </svg>
        </button>
        <button class="icon-btn" :disabled="!canRedo" @click="redo" title="Redo (Ctrl+Shift+Z)" aria-label="Redo">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 9H10a5 5 0 0 0 0 10h6" />
            <path d="M17 5l4 4-4 4" />
          </svg>
        </button>
        <button class="icon-btn" :class="{ 'is-on': cropMode }" :disabled="!activeSource" @click="toggleCropMode"
          title="Crop & Straighten (R)" aria-label="Crop">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M6 2v14a2 2 0 0 0 2 2h14" />
            <path d="M2 6h14a2 2 0 0 1 2 2v14" />
          </svg>
        </button>
        <button class="icon-btn" :class="{ 'is-on': showOriginal }" :disabled="!activeSource || cropMode"
          @mousedown="startCompare" @mouseup="endCompare" @mouseleave="endCompare"
          @touchstart.prevent="startCompare" @touchend.prevent="endCompare" @touchcancel="endCompare"
          title="Hold to compare original ( \ )" aria-label="Compare with original">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="5" width="18" height="14" rx="2" />
            <path d="M12 5v14" />
          </svg>
        </button>
      </div>
      <div class="meta-summary">
        <template v-if="activeSource">
          <span>{{ activeSource.name }}</span>
          <span class="meta-empty">{{ formatBytes(activeSource.size) }}</span>
        </template>
        <span v-else class="meta-empty">No image loaded</span>
      </div>
      <button class="export-btn" type="button" :disabled="!activeSource || exporting" @click="exportImage">
        <svg v-if="!exporting" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 3v12" />
          <path d="M8 11l4 4 4-4" />
          <path d="M5 21h14" />
        </svg>
        <span class="export-spinner" v-else aria-hidden="true" />
        <span>{{ exporting ? 'Exporting…' : 'Export' }}</span>
      </button>
    </header>

    <main class="center">
      <div class="viewport" ref="viewportRef"
        @wheel="onWheel"
        @mousedown="startPan"
        @mousemove="doPan"
        @mouseup="stopPan"
        @mouseleave="stopPan"
        @dblclick="onDoubleClick"
        :class="{ 'is-grabbing': isPanning }">
        <div class="dropzone" v-show="!activeSource" @click="pickFiles">
          <div class="dropzone-inner">
            <svg class="dropzone-icon" viewBox="0 0 48 48" fill="none" aria-hidden="true">
              <rect x="6" y="10" width="36" height="28" rx="4" stroke="currentColor" stroke-width="2" />
              <circle cx="17" cy="20" r="3.5" stroke="currentColor" stroke-width="2" />
              <path d="M9 33l9-9 6 6 8-8 7 7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
            <p class="dropzone-title">Drop a RAW file to start</p>
            <p class="dropzone-sub">or click anywhere to browse</p>
            <p class="dropzone-hint">ARW · DNG · CR3 · NEF · RAF · RW2 · ORF</p>
          </div>
        </div>
        <div v-show="activeSource && status === 'rendering' && !webglRenderer" class="preview-loading">
          <span class="spinner spinner-lg" aria-hidden="true" />
          <span>Decoding…</span>
        </div>
        <div v-show="status === 'uploading' && !activeSource" class="preview-loading">
          <span class="spinner spinner-lg" aria-hidden="true" />
          <span>Importing…</span>
        </div>
        <canvas v-show="webglRenderer != null && activeSource && !activeSource.invalid" ref="canvasRef" class="preview" :style="{ transform: displayTransform, width: imageW + 'px', height: imageH + 'px' }" />
        <div v-show="activeSource && webglRenderer && (status === 'rendering' || status === 'uploading')"
          class="viewport-busy" aria-live="polite">
          <span class="spinner" aria-hidden="true" />
          <span>{{ status === 'uploading' ? 'Importing…' : 'Decoding…' }}</span>
        </div>
        <svg v-show="cropMode && webglRenderer != null" ref="cropOverlayRef" class="crop-overlay"
          :style="{ transform: displayTransform, width: imageW + 'px', height: imageH + 'px' }"
          :viewBox="cropViewBox" preserveAspectRatio="none"
          @mousedown="onCropOverlayDown">
          <!-- transparent catcher for move/rotate drags -->
          <rect class="crop-catch" :x="cropBBox.x" :y="cropBBox.y" :width="cropBBox.w" :height="cropBBox.h" />
          <!-- dim outside the crop -->
          <path class="crop-dim" :d="cropDimPath" fill-rule="evenodd" />
          <!-- guide overlay (O cycles: thirds / golden / diagonal / grid / off) -->
          <g class="crop-grid" :stroke-width="1 * ofPerScreen">
            <line v-for="(x, i) in cropGuideLines.v" :key="'v'+i" :x1="x" :y1="cropBoxRect.y" :x2="x" :y2="cropBoxRect.y + cropBoxRect.h" />
            <line v-for="(y, i) in cropGuideLines.h" :key="'h'+i" :x1="cropBoxRect.x" :y1="y" :x2="cropBoxRect.x + cropBoxRect.w" :y2="y" />
            <template v-if="cropGuideLines.diag">
              <line :x1="cropBoxRect.x" :y1="cropBoxRect.y" :x2="cropBoxRect.x + cropBoxRect.w" :y2="cropBoxRect.y + cropBoxRect.h" />
              <line :x1="cropBoxRect.x + cropBoxRect.w" :y1="cropBoxRect.y" :x2="cropBoxRect.x" :y2="cropBoxRect.y + cropBoxRect.h" />
            </template>
          </g>
          <!-- crop box border -->
          <rect class="crop-frame" :x="cropBoxRect.x" :y="cropBoxRect.y" :width="cropBoxRect.w" :height="cropBoxRect.h" :stroke-width="1.5 * ofPerScreen" />
          <!-- handles -->
          <rect v-for="h in CROP_HANDLES" :key="h.key" class="crop-handle"
            :x="cropHandlePos(h).x - 5.5 * ofPerScreen" :y="cropHandlePos(h).y - 5.5 * ofPerScreen"
            :width="11 * ofPerScreen" :height="11 * ofPerScreen"
            :style="{ cursor: h.cursor }"
            @mousedown="onCropHandleDown($event, h.key)" />
        </svg>
        <img v-show="activeSource && !activeSource.invalid && !webglRenderer && status !== 'rendering'" class="preview" :style="{ transform: displayTransform }" :src="activeSource ? thumbSrc(activeSource) : ''" alt="preview" />
        <div v-if="activeSource?.invalid" class="invalid-state">
          <img v-if="activeSource && thumbSrc(activeSource)" :src="thumbSrc(activeSource)" :alt="activeSource.name" />
          <p class="invalid-title">源文件已失效</p>
          <p class="invalid-sub">服务器缓存可能已被清理，请重新导入这张图片</p>
        </div>
      </div>

      <footer class="status" v-show="activeSource">
        <div class="status-left">
          <div class="status-cell">
            <span class="status-label">Status</span>
            <span class="status-value" :data-state="status">{{ timing ? `Decoded in ${timing}ms` : status }}</span>
          </div>
        </div>
        <div class="status-right">
          <div class="status-cell zoom-cell" v-if="activeSource">
            <button class="zoom-btn" @click="zoomOut" :disabled="zoom <= 0.1">−</button>
            <span class="zoom-percent">{{ zoomPercent }}%</span>
            <button class="zoom-btn" @click="zoomIn" :disabled="zoom >= 50">+</button>
            <button class="zoom-btn" @click="fitView">Fit</button>
          </div>
        </div>
      </footer>
    </main>

    <aside class="right" v-wheel-adjust>
      <div class="histogram-wrap" v-show="activeSource">
        <canvas ref="histoCanvasRef" class="histogram" />
      </div>

      <section class="panel crop-panel" v-if="activeSource && cropMode">
        <header class="panel-head">
          <span>Crop &amp; Straighten</span>
          <button class="ghost" type="button" @click="resetCrop">Reset</button>
        </header>
        <div class="control-row">
          <label class="control-label">Aspect</label>
          <div class="crop-aspect">
            <select class="control-select" :value="customAspect ? 'custom' : cropAspect"
              @change="selectAspect(($event.target as HTMLSelectElement).value)">
              <option v-for="a in ASPECT_PRESETS" :key="a.key" :value="a.key">{{ a.label }}</option>
              <option value="custom">Custom…</option>
            </select>
            <button class="icon-mini" type="button" title="Swap orientation (X)" @click="swapAspect" aria-label="Swap aspect">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M16 3l4 4-4 4" /><path d="M20 7H8a4 4 0 0 0-4 4" />
                <path d="M8 21l-4-4 4-4" /><path d="M4 17h12a4 4 0 0 0 4-4" />
              </svg>
            </button>
          </div>
        </div>
        <div class="control-row" v-if="customAspect">
          <label class="control-label">Ratio</label>
          <div class="crop-custom">
            <input class="slider-number" type="number" min="0.1" step="0.1" :value="customAspect[0]"
              @change="setCustomAspect(($event.target as HTMLInputElement).valueAsNumber, customAspect![1])" />
            <span class="crop-custom-x">×</span>
            <input class="slider-number" type="number" min="0.1" step="0.1" :value="customAspect[1]"
              @change="setCustomAspect(customAspect![0], ($event.target as HTMLInputElement).valueAsNumber)" />
          </div>
        </div>
        <div class="slider">
          <label>Angle</label>
          <input type="range" min="-45" max="45" step="0.1"
            :value="crop.angle"
            :style="{ '--track': trackFill(crop.angle, -45, 45) }"
            @input="setAngle(($event.target as HTMLInputElement).valueAsNumber)"
            @dblclick="setAngle(0)" title="Double-click to reset" />
          <input class="slider-number" type="number" min="-45" max="45" step="0.1"
            :value="Number(crop.angle.toFixed(1))"
            @input="setAngle(($event.target as HTMLInputElement).valueAsNumber)" />
        </div>
        <div class="crop-buttons">
          <button type="button" class="crop-tool" title="Rotate left 90°" @click="rotateCrop(-1)">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M3 12a9 9 0 1 0 3-6.7L3 8" /><path d="M3 3v5h5" />
            </svg>
          </button>
          <button type="button" class="crop-tool" title="Rotate right 90°" @click="rotateCrop(1)">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M21 12a9 9 0 1 1-3-6.7L21 8" /><path d="M21 3v5h-5" />
            </svg>
          </button>
          <button type="button" class="crop-tool" :class="{ 'is-on': crop.flipH }" title="Flip horizontal" @click="flipCropH">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 3v18" /><path d="M8 7l-4 5 4 5" /><path d="M16 7l4 5-4 5" />
            </svg>
          </button>
          <button type="button" class="crop-tool" :class="{ 'is-on': crop.flipV }" title="Flip vertical" @click="flipCropV">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M3 12h18" /><path d="M7 8l5-4 5 4" /><path d="M7 16l5 4 5-4" />
            </svg>
          </button>
        </div>
        <button type="button" class="crop-done" @click="exitCropMode">Done</button>
      </section>

      <section class="panel" v-show="!cropMode">
        <header class="panel-head">
          <span>Settings</span>
          <button class="ghost" type="button" @click="resetRecipe">Reset</button>
        </header>
        <div class="control-row" v-if="activeSource">
          <label class="control-label">DCP Style</label>
          <select v-model="dcpCode" class="control-select">
            <option value="">Auto (camera)</option>
            <option value="ST">Standard</option>
            <option value="FL">Film Look</option>
            <option value="VV">Vivid</option>
            <option value="VV2">Vivid 2</option>
            <option value="NT">Neutral</option>
            <option value="PT">Portrait</option>
            <option value="SH">Soft High</option>
            <option value="IN">Intense</option>
            <option value="BW">Black & White</option>
          </select>
        </div>
        <div class="control-row" v-if="activeSource">
          <label class="control-label">Look</label>
          <select v-model.number="viewSettings.viewTransform" class="control-select">
            <option :value="0">Lightroom-style</option>
            <option :value="1">AgX (filmic)</option>
          </select>
        </div>
        <div class="control-row" v-if="activeSource && p3Supported">
          <label class="control-label">Display</label>
          <select v-model.number="viewSettings.displayGamut" class="control-select">
            <option :value="0">sRGB</option>
            <option :value="1">Display-P3 (wide)</option>
          </select>
        </div>
      </section>
      <section v-for="group in groups" :key="group.title" class="panel" v-show="!cropMode">
        <header class="panel-head">
          <span class="panel-title">
            {{ group.title }}
            <span v-if="groupEdited(group)" class="panel-dot" aria-hidden="true" />
          </span>
        </header>
        <div v-for="spec in group.items" :key="spec.key" class="slider"
          :class="{ 'is-modified': isEdited(spec.key) }">
          <label :for="`s-${spec.key}`">{{ spec.label }}</label>
          <input :id="`s-${spec.key}`" v-model.number="recipe[spec.key]" type="range"
            :min="spec.min" :max="spec.max" :step="spec.step"
            :style="{ '--track': sliderTrack(spec) }"
            @dblclick="recipe[spec.key] = SLIDER_DEFAULTS[spec.key]" title="Double-click to reset" />
          <input v-model.number="recipe[spec.key]" class="slider-number" type="number"
            :min="spec.min" :max="spec.max" :step="spec.step" :aria-label="spec.label" />
        </div>
      </section>

      <section class="panel" v-if="activeSource && !cropMode">
        <header class="panel-head">
          <span>Detail</span>
          <span v-if="denoiseBusy" class="panel-hint">Denoising…</span>
        </header>
        <div class="control-row">
          <label class="control-label" for="denoise-on">AI Denoise</label>
          <label class="switch">
            <input id="denoise-on" type="checkbox" v-model="denoise.enabled" />
            <span class="switch-track"><span class="switch-thumb" /></span>
          </label>
        </div>
        <div v-show="denoise.enabled" class="slider" style="margin-top: 12px;">
          <label for="denoise-amount">Amount</label>
          <input id="denoise-amount" type="range" min="0" max="100" step="1"
            v-model.number="denoise.amount"
            :style="{ '--track': trackFill(denoise.amount, 0, 100) }"
            @dblclick="denoise.amount = 100" title="Double-click to reset" />
          <input v-model.number="denoise.amount" class="slider-number" type="number"
            min="0" max="100" step="1" aria-label="Denoise amount" />
        </div>
      </section>

      <section class="panel" v-if="activeSource && !cropMode">
        <header class="panel-head">
          <span class="panel-title">
            HSL / Color
            <span v-if="hslEdited" class="panel-dot" aria-hidden="true" />
          </span>
        </header>
        <div class="hsl-tabs">
          <button :class="{ active: hslTab === 'hue' }" @click="hslTab = 'hue'">H</button>
          <button :class="{ active: hslTab === 'sat' }" @click="hslTab = 'sat'">S</button>
          <button :class="{ active: hslTab === 'lum' }" @click="hslTab = 'lum'">L</button>
        </div>
        <div v-for="(range, i) in HSL_RANGES" :key="range.name" class="hsl-row"
          :class="{ 'is-modified': hslValue(i) !== 0 }">
          <span class="hsl-dot" :style="{ background: range.color }" />
          <span class="hsl-label">{{ range.name }}</span>
          <input type="range" min="-100" max="100" step="1"
            :value="hslValue(i)"
            :style="{ '--track': trackFill(hslValue(i), -100, 100) }"
            @input="setHsl(i, ($event.target as HTMLInputElement).valueAsNumber)"
            @dblclick="setHsl(i, 0)" title="Double-click to reset" />
          <input class="hsl-number" type="number" min="-100" max="100" step="1"
            :value="hslValue(i)"
            @input="setHsl(i, ($event.target as HTMLInputElement).valueAsNumber)" />
        </div>
      </section>

      <section class="panel" v-if="activeSource && !cropMode">
        <header class="panel-head">
          <span class="panel-title">
            Color Grading
            <span v-if="gradingEdited" class="panel-dot" aria-hidden="true" />
          </span>
        </header>
        <div class="grading-group">
          <div class="grading-header">
            <span class="grading-dot" :style="{ background: gradingColor('sh') }" />
            <span>Shadows</span>
          </div>
          <div class="grading-row">
            <label>H</label>
            <input type="range" min="-180" max="180" step="1" v-model.number="grading.shH"
              :style="{ '--track': trackFill(grading.shH, -180, 180) }"
              @dblclick="grading.shH = 0" title="Double-click to reset" />
            <input class="slider-number" type="number" min="-180" max="180" step="1" v-model.number="grading.shH" />
          </div>
          <div class="grading-row">
            <label>S</label>
            <input type="range" min="0" max="100" step="1" v-model.number="grading.shS"
              :style="{ '--track': trackFill(grading.shS, 0, 100) }"
              @dblclick="grading.shS = 0" title="Double-click to reset" />
            <input class="slider-number" type="number" min="0" max="100" step="1" v-model.number="grading.shS" />
          </div>
        </div>
        <div class="grading-group">
          <div class="grading-header">
            <span class="grading-dot" :style="{ background: gradingColor('md') }" />
            <span>Midtones</span>
          </div>
          <div class="grading-row">
            <label>H</label>
            <input type="range" min="-180" max="180" step="1" v-model.number="grading.mdH"
              :style="{ '--track': trackFill(grading.mdH, -180, 180) }"
              @dblclick="grading.mdH = 0" title="Double-click to reset" />
            <input class="slider-number" type="number" min="-180" max="180" step="1" v-model.number="grading.mdH" />
          </div>
          <div class="grading-row">
            <label>S</label>
            <input type="range" min="0" max="100" step="1" v-model.number="grading.mdS"
              :style="{ '--track': trackFill(grading.mdS, 0, 100) }"
              @dblclick="grading.mdS = 0" title="Double-click to reset" />
            <input class="slider-number" type="number" min="0" max="100" step="1" v-model.number="grading.mdS" />
          </div>
        </div>
        <div class="grading-group">
          <div class="grading-header">
            <span class="grading-dot" :style="{ background: gradingColor('hl') }" />
            <span>Highlights</span>
          </div>
          <div class="grading-row">
            <label>H</label>
            <input type="range" min="-180" max="180" step="1" v-model.number="grading.hlH"
              :style="{ '--track': trackFill(grading.hlH, -180, 180) }"
              @dblclick="grading.hlH = 0" title="Double-click to reset" />
            <input class="slider-number" type="number" min="-180" max="180" step="1" v-model.number="grading.hlH" />
          </div>
          <div class="grading-row">
            <label>S</label>
            <input type="range" min="0" max="100" step="1" v-model.number="grading.hlS"
              :style="{ '--track': trackFill(grading.hlS, 0, 100) }"
              @dblclick="grading.hlS = 0" title="Double-click to reset" />
            <input class="slider-number" type="number" min="0" max="100" step="1" v-model.number="grading.hlS" />
          </div>
        </div>
        <div class="slider">
          <label>Blend</label>
          <input type="range" min="0" max="100" step="1" v-model.number="grading.blend"
            :style="{ '--track': trackFill(grading.blend, 0, 100) }"
            @dblclick="grading.blend = 50" title="Double-click to reset" />
          <input class="slider-number" type="number" min="0" max="100" step="1" v-model.number="grading.blend" />
        </div>
        <div class="slider">
          <label>Balance</label>
          <input type="range" min="-100" max="100" step="1" v-model.number="grading.balance"
            :style="{ '--track': trackFill(grading.balance, -100, 100) }"
            @dblclick="grading.balance = 0" title="Double-click to reset" />
          <input class="slider-number" type="number" min="-100" max="100" step="1" v-model.number="grading.balance" />
        </div>
      </section>

      <section class="panel" v-if="activeSource && !cropMode">
        <header class="panel-head">
          <span>Tone Curve</span>
          <button class="ghost" type="button" @click="resetCurve">Reset</button>
        </header>
        <div class="curve-tabs">
          <button v-for="tab in CURVE_TABS" :key="tab.key" type="button"
            :class="['curve-tab', `curve-tab--${tab.key}`, { active: curveChannel === tab.key }]"
            @click="setCurveChannel(tab.key)">{{ tab.label }}</button>
        </div>
        <canvas ref="curveCanvas" class="curve-canvas"
          @mousedown="onCurveMouseDown"
          @mousemove="onCurveHover"
          @mouseleave="onCurveLeave"
          @dblclick="onCurveDoubleClick" />

        <!-- Parametric region + split sliders -->
        <div v-if="curveChannel === 'parametric'" class="curve-params">
          <div v-for="r in PARAM_REGIONS" :key="r.key" class="slider">
            <label>{{ r.label }}</label>
            <input type="range" min="-100" max="100" step="1"
              :value="paramValue(r.key)"
              :style="{ '--track': trackFill(paramValue(r.key), -100, 100) }"
              @input="setParam(r.key, ($event.target as HTMLInputElement).valueAsNumber)"
              @dblclick="setParam(r.key, 0)" title="Double-click to reset" />
            <input class="slider-number" type="number" min="-100" max="100" step="1"
              :value="paramValue(r.key)"
              @input="setParam(r.key, ($event.target as HTMLInputElement).valueAsNumber)" />
          </div>
          <div class="curve-splits">
            <span class="curve-splits-label">Range Splits</span>
            <input type="range" min="4" max="96" step="1"
              :value="paramValue('shadowSplit')"
              :style="{ '--track': trackFill(paramValue('shadowSplit'), 0, 100) }"
              @input="setParam('shadowSplit', Math.min(($event.target as HTMLInputElement).valueAsNumber, paramValue('midtoneSplit') - 4))" />
            <input type="range" min="4" max="96" step="1"
              :value="paramValue('midtoneSplit')"
              :style="{ '--track': trackFill(paramValue('midtoneSplit'), 0, 100) }"
              @input="setParam('midtoneSplit', Math.min(Math.max(($event.target as HTMLInputElement).valueAsNumber, paramValue('shadowSplit') + 4), paramValue('highlightSplit') - 4))" />
            <input type="range" min="4" max="96" step="1"
              :value="paramValue('highlightSplit')"
              :style="{ '--track': trackFill(paramValue('highlightSplit'), 0, 100) }"
              @input="setParam('highlightSplit', Math.max(($event.target as HTMLInputElement).valueAsNumber, paramValue('midtoneSplit') + 4))" />
          </div>
        </div>

        <!-- Point-curve presets (applied to the RGB master channel) -->
        <div v-else class="curve-presets">
          <button v-for="name in presetNames" :key="name" type="button"
            class="curve-preset" @click="applyCurvePreset(name)">{{ name }}</button>
        </div>
      </section>
    </aside>

    <footer class="filmstrip" v-if="sources.length">
      <button class="filmstrip-import" type="button" @click="pickFiles">＋ Import</button>
      <div class="filmstrip-track">
        <div v-for="source in sources" :key="source.id"
          class="film-cell" :class="{ 'is-active': source.id === activeId, 'is-invalid': source.invalid }">
          <button type="button" class="film-cell-main"
            :title="`${source.name} · ${formatBytes(source.size)}`"
            @click="selectSource(source.id)">
            <div class="film-thumb">
              <img v-if="thumbSrc(source)" :src="thumbSrc(source)" :alt="source.name" />
              <div v-else class="thumb-skeleton" aria-hidden="true" />
              <span v-if="source.id === activeId && status === 'rendering'" class="thumb-loading" aria-hidden="true">
                <span class="spinner" />
              </span>
              <span v-if="source.invalid" class="film-badge" title="源文件已失效，请重新导入">失效</span>
            </div>
            <span class="film-name">{{ source.name }}</span>
          </button>
          <button type="button" class="film-remove" title="从库中移除（不影响磁盘上的原始文件）"
            aria-label="从库中移除" @click="removeSource(source.id)">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>
      </div>
    </footer>

    <input ref="fileInput" type="file" accept=".arw,.dng,.cr2,.cr3,.nef,.raf,.rw2,.orf,.tif,.tiff,.jpg,.jpeg,.png" hidden multiple @change="onFileChange" />
    <transition name="fade"><div v-if="isDragging" class="drag-overlay">Drop to import</div></transition>
    <transition name="fade">
      <div v-if="errorMessage && !activeSource?.invalid" class="error-toast" @click="errorMessage = null" title="点击关闭">
        {{ errorMessage }}
      </div>
    </transition>
  </div>
</template>
