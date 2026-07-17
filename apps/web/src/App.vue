<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { PipelineRenderer, type EditParams } from "./rendering/pipeline-renderer";
import { renderHistogram } from "./rendering/histogram";
import {
  curveToLUT, buildToneCurveLUT, defaultToneCurve, normalizeToneCurve,
  type BasicAdjust, type ToneCurve,
} from "./rendering/curve";
import {
  defaultCrop, cloneCrop, isDefaultCrop, imageDims, buildCropTransform,
  cropOutputRect, cropOutputSize, straightenedBBox,
  applyAspectRatio, resolveAspectFraction, cropOutputSizeForAspect,
  ASPECT_PRESETS,
  type CropState,
} from "./rendering/crop";
import { type PersistedEdit } from "./persistence";
import { trackFill, formatBytes, clamp } from "./ui";
import SliderRow from "./components/SliderRow.vue";
import Filmstrip from "./components/Filmstrip.vue";
import { useViewport } from "./composables/useViewport";
import { useHistory } from "./composables/useHistory";
import { useToneCurve } from "./composables/useToneCurve";
import { useCropEditor, DEFAULT_ASPECT } from "./composables/useCropEditor";
import { useLibrary } from "./composables/useLibrary";

// ── types ──

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

// Derived from defaultRecipe so the two can't drift (double-click reset and
// isEdited both compare against these).
const SLIDER_DEFAULTS: Record<string, number> = { ...defaultRecipe() };

// ── state ──

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
const defaultDenoise = () => ({ enabled: false, model: "wavelet", amount: 100 });
const denoise = reactive(defaultDenoise());
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

const defaultGrading = () => ({
  shH: 0, shS: 0,
  mdH: 0, mdS: 0,
  hlH: 0, hlS: 0,
  blend: 50,
  balance: 0,
});
const grading = reactive(defaultGrading());

const GRADING_BANDS = [
  { band: "sh", label: "Shadows", hueKey: "shH", satKey: "shS" },
  { band: "md", label: "Midtones", hueKey: "mdH", satKey: "mdS" },
  { band: "hl", label: "Highlights", hueKey: "hlH", satKey: "hlS" },
] as const;

// View settings (not part of the per-image recipe): tone-mapping look + display gamut.
const viewSettings = reactive({ viewTransform: 0, displayGamut: 0 });

// ── Crop & Straighten ──
//
// Part of the per-image edit (snapshot/history/persistence). The crop editor
// renders the full straightened image with an overlay; committing just switches
// the display back to the cropped output. All geometry lives in crop.ts.
const crop = reactive<CropState>(defaultCrop());
const cropMode = ref(false);

const {
  zoom, pan, fitScale, viewportRef, isPanning,
  displayTransform, zoomPercent,
  recomputeFit, startPan, doPan, stopPan,
  onWheel, zoomIn, zoomOut, fitView, zoomToFull, onDoubleClick,
} = useViewport({ imageW, imageH, srcW, srcH, srcFullW, srcFullH, cropMode });
const {
  cropAspect, cropBBox, cropRenderScale, cropOverlayRef,
  currentImageDims, resetCrop,
  lockedRatio, customAspect, selectAspect, setCustomAspect, swapAspect,
  setAngle, rotateCrop, flipCropH, flipCropV,
  cycleCropGuide, cropGuideLines,
  cropBoxRect, CROP_HANDLES, ofPerScreen, cropViewBox, cropDimPath, cropHandlePos,
  onCropHandleDown, onCropOverlayDown,
} = useCropEditor({
  crop, srcW, srcH, fitScale,
  onDragEnd: () => flushPendingHistory(),
});

const WORKSPACE_BG: [number, number, number] = [0.07, 0.07, 0.08];
const CROP_EDITOR_MAX = 1800; // cap the editor preview's long edge (px)

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

const {
  toneCurve, curveChannel, curveActive, curveCanvas,
  CURVE_TABS, PARAM_REGIONS, presetNames,
  renderCurveCanvas, setCurveChannel, resetCurve, applyCurvePreset,
  paramValue, setParam,
  onCurveMouseDown, onCurveHover, onCurveLeave, onCurveDoubleClick,
} = useToneCurve({
  onApply: () => applyCurveLUT(),
  onReset: () => { bakeCurveLUT(); drawWebGL(); }, // immediate, no RAF-batching for reset
});

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

let isRestoring = false;
// When true, the dcpCode watcher skips its re-decode — used while we load a
// source explicitly (switching images / restoring) to avoid a double decode.
let suppressDcpReload = false;

function defaultSnapshot(): Snapshot {
  return {
    recipe: defaultRecipe(),
    hslHue: [0, 0, 0, 0, 0, 0, 0, 0],
    hslSat: [0, 0, 0, 0, 0, 0, 0, 0],
    hslLum: [0, 0, 0, 0, 0, 0, 0, 0],
    grading: defaultGrading(),
    curve: defaultToneCurve(),
    crop: defaultCrop(),
    aspect: DEFAULT_ASPECT,
    dcp: "",
    denoise: defaultDenoise(),
  };
}

const {
  history, historyIndex, pendingDirty, canUndo, canRedo,
  scheduleCommit: scheduleHistoryCommit, flushPending: flushPendingHistory,
  undo, redo, init: initHistory,
} = useHistory<Snapshot>({
  capture: () => captureSnapshot(),
  apply: (s) => applySnapshot(s),
  suspended: () => isRestoring,
  onCommitted: () => schedulePersist(),
});

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
  // Full re-render, not just a redraw: the snapshot may carry a different crop,
  // and the crop watcher that would re-render it is suspended while isRestoring.
  scheduleCropRender();
  nextTick(() => { isRestoring = false; });
}

initHistory();

// ── Per-image edits + persistence ──
//
// Each imported source owns an independent edit (snapshot + undo history). The
// live reactive state (recipe/hsl/grading/curve/dcp + history) always mirrors
// the active image; switching images saves the outgoing one here and loads the
// incoming one back.

type ImageEdit = PersistedEdit<Snapshot>;

// Push a stored edit (null = defaults) into the live reactive state.
// Does not decode/draw — the library pairs this with loadSource().
function applyStoredEdit(e: ImageEdit | null): void {
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

const {
  sources, activeId, activeSource,
  persistNow, schedulePersist,
  thumbSrc, markInvalid,
  selectSource, removeSource,
  restoreSession, loadThumbCache, uploadFiles,
} = useLibrary<Snapshot, typeof viewSettings>({
  api: API,
  status, errorMessage, cropMode,
  captureEdit: () => ({ snapshot: captureSnapshot(), history: history.value.slice(), historyIndex: historyIndex.value }),
  defaultEdit: () => { const snap = defaultSnapshot(); return { snapshot: snap, history: [snap], historyIndex: 0 }; },
  loadEdit: (e) => applyStoredEdit(e),
  loadPixels: (id, o) => loadSource(id, o),
  flushPendingHistory: () => flushPendingHistory(),
  // A pending denoise reload belongs to the outgoing image; firing it after the
  // switch would re-decode the new image a second time.
  beforeActivate: () => { if (denoiseReloadTimer) { clearTimeout(denoiseReloadTimer); denoiseReloadTimer = 0; } },
  onEmptied: () => {
    currentSourceId = "";
    hasLinearData = false;
    srcW.value = 0;
    srcH.value = 0;
    timing.value = null;
    destroyWebGL({ keepContext: true }); // frees the removed image's GPU texture
    status.value = "idle";
    errorMessage.value = null;
  },
  isRestoring: () => isRestoring,
  sessionExtras: { get: () => ({ ...viewSettings }), apply: (v) => Object.assign(viewSettings, v) },
});

// Denoise params for the render-linear request. amount is normalised to 0..1;
// disabled (or amount 0) tells the worker to skip inference entirely.
function denoisePayload(): { enabled: boolean; model: string; amount: number } {
  return { enabled: denoise.enabled, model: denoise.model, amount: denoise.amount / 100 };
}

type LinearMeta = { width: number; height: number; fullWidth: number | null; fullHeight: number | null; colorProfile: ColorProfileMeta | null };

// Decode linear data via /render-linear. The response carries the pixels
// directly: [u32 header length][JSON header, padded so the pixels stay 4-byte
// aligned][float32 linear RGB]. Returns null on 404 (source evicted server-side).
async function fetchLinear(body: Record<string, unknown>): Promise<{ meta: LinearMeta; pixels: Float32Array } | null> {
  const res = await fetch(`${API}/render-linear`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(await res.text());
  const buf = await res.arrayBuffer();
  const headerLen = new DataView(buf).getUint32(0);
  const meta = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, headerLen))) as LinearMeta;
  return { meta, pixels: new Float32Array(buf, 4 + headerLen) };
}

// Decode `id`'s linear data and render it into the (reused) WebGL pipeline.
// Returns false if the source can no longer be decoded server-side.
// Concurrent calls can overlap (rapid filmstrip clicks, a dcp/denoise reload
// racing a switch); only the newest call may touch renderer/UI state after an
// await, otherwise the slower decode would land last and show stale pixels.
let loadSeq = 0;
async function loadSource(id: string, opts: { resetView?: boolean } = {}): Promise<boolean> {
  const src = sources.value.find(s => s.id === id);
  if (!src) return false;
  currentSourceId = id;
  const seq = ++loadSeq;
  const stale = () => seq !== loadSeq;
  status.value = "rendering";
  errorMessage.value = null;
  timing.value = null; // stale timing would mask the live status in the footer
  const t0 = performance.now();
  try {
    const lin = await fetchLinear({ sourceId: id, halfSize: false, maxSize: 2560, dcpCode: dcpCode.value, denoise: denoisePayload() });
    if (stale()) return false;
    if (!lin) { markInvalid(id); return false; }
    const { meta: linMeta, pixels: linearFloat } = lin;

    hasLinearData = true;
    profileCurveLUT = buildProfileLUT(linMeta.colorProfile);
    srcW.value = linMeta.width;
    srcH.value = linMeta.height;
    srcFullW.value = linMeta.fullWidth ?? linMeta.width;
    srcFullH.value = linMeta.fullHeight ?? linMeta.height;
    if (opts.resetView) { zoom.value = 1; pan.x = 0; pan.y = 0; }

    await nextTick();
    if (stale()) return false;
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
    if (stale()) return false; // a newer load owns status/errorMessage now
    status.value = "error";
    errorMessage.value = err instanceof Error ? err.message : String(err);
    return false;
  }
}

let denoiseReloadTimer = 0; // debounce for the denoise watcher below

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

function buildProfileLUT(cp: ColorProfileMeta | null | undefined): Float32Array | null {
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

// The histogram redraws at ~11 Hz during slider drags; reading
// getBoundingClientRect there forces a layout each time, so track the CSS size
// with a ResizeObserver instead (same pattern as the curve canvas above).
const histoSize = { w: 0, h: 0 };
let histoResizeObs: ResizeObserver | null = null;
watch(histoCanvasRef, (canvas) => {
  histoResizeObs?.disconnect();
  histoResizeObs = null;
  histoSize.w = 0;
  histoSize.h = 0;
  if (canvas) {
    histoResizeObs = new ResizeObserver((entries) => {
      const r = entries[entries.length - 1]?.contentRect;
      if (!r) return;
      histoSize.w = r.width;
      histoSize.h = r.height;
      scheduleHistogram();
    });
    histoResizeObs.observe(canvas);
  }
});

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
  let w = histoSize.w;
  let h = histoSize.h;
  // Not observed/laid out yet: fall back to a one-off layout read; the
  // ResizeObserver reschedules once the canvas gets its real size.
  if (w <= 0 || h <= 0) {
    const rect = canvas.getBoundingClientRect();
    w = rect.width;
    h = rect.height;
    if (w <= 0 || h <= 0) return;
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

// keepContext: release GL objects but keep the canvas's context usable, so the
// persistent preview canvas can host a new renderer later (a canvas whose
// context was lost via destroy() can never get another one).
function destroyWebGL(opts: { keepContext?: boolean } = {}): void {
  if (rafId) { cancelAnimationFrame(rafId); rafId = 0; }
  if (histoTimer) { clearTimeout(histoTimer); histoTimer = 0; }
  drawPending = false;
  if (webglRenderer) {
    if (opts.keepContext) webglRenderer.release();
    else webglRenderer.destroy();
    webglRenderer = null;
  }
}

// ── Crop rendering ──
//
// Normal view: render the crop box region (cropped output dims). Crop editor:
// render the full straightened image's bounding box so the user sees beyond the
// crop, with the overlay drawn on top.

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
  histoResizeObs?.disconnect();
  histoResizeObs = null;
  destroyWebGL();
});

onMounted(async () => {
  window.addEventListener('keydown', onKeyDown);
  window.addEventListener('keyup', onKeyUp);
  window.addEventListener('beforeunload', persistOnUnload);
  document.addEventListener('visibilitychange', persistOnHidden);
  resizeObs = new ResizeObserver(() => recomputeFit());
  if (viewportRef.value) resizeObs.observe(viewportRef.value);

  await loadThumbCache();

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
    const lin = await fetchLinear({ sourceId: currentSourceId, halfSize: false, maxSize: 0, dcpCode: dcpCode.value, denoise: denoisePayload() });
    if (!lin) throw new Error("Source is no longer available server-side");
    const { meta: linMeta, pixels: linear } = lin;

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
    // quality 1.0 also disables the browser encoder's 4:2:0 chroma subsampling
    const blob = await renderer.toBlob("image/jpeg", 1.0);

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

// White balance reads as a colour axis, not an amount: an accent fill growing
// from the left would say "this is set" on a slider sitting at its default.
// Neutral is placed where the default value puts the thumb (6500K -> 45%).
const WB_TRACK: Partial<Record<RecipeKey, string>> = {
  temperature: "linear-gradient(to right, #3f7dff, #f2ead9 45%, #ffb648)",
  tint: "linear-gradient(to right, #4fc26a, #d8d8d8 50%, #d264d8)",
};

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
        <SliderRow :model-value="Number(crop.angle.toFixed(1))" @update:model-value="setAngle"
          label="Angle" :min="-45" :max="45" :step="0.1" />
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
        <SliderRow v-for="spec in group.items" :key="spec.key"
          v-model="recipe[spec.key]" :label="spec.label" :input-id="`s-${spec.key}`"
          :min="spec.min" :max="spec.max" :step="spec.step"
          :reset-value="SLIDER_DEFAULTS[spec.key]" :track="WB_TRACK[spec.key]" show-modified />
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
        <SliderRow v-show="denoise.enabled" v-model="denoise.amount" style="margin-top: 12px;"
          label="Amount" input-id="denoise-amount" :min="0" :max="100" :reset-value="100" />
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
        <SliderRow v-for="(range, i) in HSL_RANGES" :key="range.name"
          :model-value="hslValue(i)" @update:model-value="v => setHsl(i, v)"
          :label="range.name" :dot-color="range.color" row-class="hsl-row" number-class="hsl-number"
          :min="-100" :max="100" show-modified />
      </section>

      <section class="panel" v-if="activeSource && !cropMode">
        <header class="panel-head">
          <span class="panel-title">
            Color Grading
            <span v-if="gradingEdited" class="panel-dot" aria-hidden="true" />
          </span>
        </header>
        <div v-for="g in GRADING_BANDS" :key="g.band" class="grading-group">
          <div class="grading-header">
            <span class="grading-dot" :style="{ background: gradingColor(g.band) }" />
            <span>{{ g.label }}</span>
          </div>
          <SliderRow v-model="grading[g.hueKey]" label="H" row-class="grading-row" :min="-180" :max="180" />
          <SliderRow v-model="grading[g.satKey]" label="S" row-class="grading-row" :min="0" :max="100" />
        </div>
        <SliderRow v-model="grading.blend" label="Blend" :min="0" :max="100" :reset-value="50" />
        <SliderRow v-model="grading.balance" label="Balance" :min="-100" :max="100" />
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
          <SliderRow v-for="r in PARAM_REGIONS" :key="r.key"
            :model-value="paramValue(r.key)" @update:model-value="v => setParam(r.key, v)"
            :label="r.label" :min="-100" :max="100" />
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

    <Filmstrip v-if="sources.length" :sources="sources" :active-id="activeId"
      :rendering="status === 'rendering'" :thumb-src="thumbSrc"
      @select="selectSource" @remove="removeSource" @import="pickFiles" />

    <input ref="fileInput" type="file" accept=".arw,.dng,.cr2,.cr3,.nef,.raf,.rw2,.orf,.tif,.tiff,.jpg,.jpeg,.png" hidden multiple @change="onFileChange" />
    <transition name="fade"><div v-if="isDragging" class="drag-overlay">Drop to import</div></transition>
    <transition name="fade">
      <div v-if="errorMessage && !activeSource?.invalid" class="error-toast" @click="errorMessage = null" title="点击关闭">
        {{ errorMessage }}
      </div>
    </transition>
  </div>
</template>
