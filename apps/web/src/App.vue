<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { PipelineRenderer, parseDcpTables, type EditParams, type ProfileCurve, type ViewWindow } from "./rendering/pipeline-renderer";
import {
  curveToLUT, buildToneCurveLUT, defaultToneCurve, normalizeToneCurve,
  DEFAULT_BASIC, sameBasic,
  type BasicAdjust, type ToneCurve,
} from "./rendering/curve";
import {
  defaultCrop, cloneCrop, isDefaultCrop, imageDims, buildCropTransform,
  cropOutputRect, cropOutputSize, straightenedBBox, cssRecomposeMatrix,
  applyAspectRatio, resolveAspectFraction, cropOutputSizeForAspect,
  ASPECT_PRESETS,
  type AspectPreset, type CropState,
} from "./rendering/crop";
import { API, fetchLinear, fetchLookProfile, type ColorProfileMeta, type LookTweaks, type LookTweakKey } from "./api";
import { type PersistedEdit } from "./persistence";
import { gradingTint, gradingHueDeg } from "./rendering/grading";
import { parseLensCorr, mixLensTable, LENS_IDENTITY, type LensCorr } from "./rendering/lens";
import { trackFill, formatBytes, clamp, IMPORT_ACCEPT, IMPORT_FORMAT_HINT } from "./ui";
import { t, locale, setLocale, LOCALES } from "./i18n";
import SliderRow from "./components/SliderRow.vue";
import SelectMenu from "./components/SelectMenu.vue";
import Filmstrip from "./components/Filmstrip.vue";
import { useViewport } from "./composables/useViewport";
import { useHistory } from "./composables/useHistory";
import { useToneCurve } from "./composables/useToneCurve";
import { useCropEditor, DEFAULT_ASPECT } from "./composables/useCropEditor";
import { useLibrary } from "./composables/useLibrary";
import { useHistogram } from "./composables/useHistogram";
import { useExport, type ExportPlan } from "./composables/useExport";

// ── types ──

type RecipeKey = "exposure"|"contrast"|"highlights"|"shadows"|"whites"|"blacks"|"vibrance"|"saturation"|"temperature"|"tint"|"clarity"|"dehaze"|"lensDistortion"|"lensVignetting";
type Recipe = Record<RecipeKey, number>;
// Labels are not stored: a slider's caption is always `slider.<key>` and a
// group's is `panel.<title>`, so the catalog can't drift from the controls.
type SliderSpec = { key: RecipeKey; min: number; max: number; step: number };
// needsLensCorr: the group's controls do nothing but blend the shot's own
// correction tables toward identity, so they only mean something when the file
// carried a pair (see lensCorrAvailable).
type SliderGroup = { title: "tone" | "presence" | "color" | "lens"; items: SliderSpec[]; needsLensCorr?: boolean };

// Distortion correction defaults to fully applied (mirrorless glass is designed
// around it — uncorrected geometry reads as broken). Vignetting stays off by
// default: natural falloff is often part of the look, so brightening the
// corners is an opt-in, not a baseline.
const defaultRecipe = (): Recipe => ({
  exposure: 0, contrast: 0, highlights: 0, shadows: 0,
  whites: 0, blacks: 0, vibrance: 0, saturation: 0,
  temperature: 6500, tint: 0, clarity: 0, dehaze: 0,
  lensDistortion: 100, lensVignetting: 0,
});

const groups: SliderGroup[] = [
  { title: "tone", items: [
    { key: "exposure", min: -5, max: 5, step: 0.1 },
    { key: "contrast", min: -100, max: 100, step: 1 },
    { key: "highlights", min: -100, max: 100, step: 1 },
    { key: "shadows", min: -100, max: 100, step: 1 },
    { key: "whites", min: -100, max: 100, step: 1 },
    { key: "blacks", min: -100, max: 100, step: 1 },
  ]},
  { title: "presence", items: [
    { key: "clarity", min: -100, max: 100, step: 1 },
    { key: "dehaze", min: -100, max: 100, step: 1 },
  ]},
  { title: "color", items: [
    { key: "temperature", min: 2000, max: 12000, step: 50 },
    { key: "tint", min: -100, max: 100, step: 1 },
    { key: "vibrance", min: -100, max: 100, step: 1 },
    { key: "saturation", min: -100, max: 100, step: 1 },
  ]},
  { title: "lens", needsLensCorr: true, items: [
    { key: "lensDistortion", min: 0, max: 100, step: 1 },
    { key: "lensVignetting", min: 0, max: 100, step: 1 },
  ]},
];

// The in-camera Creative Look tweaks, in the order the camera's own menu lists
// them. These are not the Tone panel's sliders under another name — they drive
// Sony's own stages (the look's tone curve, YGamma, RGB2YCC, and Clarity's blur
// chain), which is why they live with the look instead of with our edits.
//
// Only the order is ours. Each one's range is the engine's and arrives with the
// profile (lookRanges), the way DRO's level ladder does: Clarity, for one, has
// no negative side at all — the engine clamps it at zero — and a slider that
// let it go there would be offering stops that all render the same.
const LOOK_TWEAK_ORDER = ["contrast", "highlights", "shadows", "fade", "saturation", "clarity"] as const;

function aspectLabel(a: AspectPreset): string {
  return a.labelKey ? t(a.labelKey) : a.label;
}

// "custom" is not a preset — it is the entry that reveals the w × h boxes, and
// a user-entered ratio selects it back through customAspect.
const aspectOptions = computed(() => [
  ...ASPECT_PRESETS.map(a => ({ value: a.key, label: aspectLabel(a) })),
  { value: "custom", label: t("crop.custom") },
]);

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
// Which colour engine renders camera RGB into the working space. "standard" is
// Adobe's chain through a DCP; "sony" reproduces Imaging Edge from calibration
// the body wrote into the RAW (worker sony/profile.py) and needs no profile
// files — but only Sony RAWs carry it, so the worker falls back on its own.
// Baked into linear.bin, so switching re-decodes like dcpCode.
type ProfileId = "standard" | "sony";
const profileId = ref<ProfileId>("standard");
// The engine the last decode actually used (see loadSource) and, on the Sony
// path, which Creative Look's tone curve it applied.
const activeProfileKind = ref<string | null>(null);
// The Creative Look tweaks the body recorded for this shot, and the ones in
// force. `null` means "as shot" — the panel shows the camera's own numbers and
// keeps following them, which is also where a double-click resets a slider to.
// Both are null until a decode reports a Sony rendering.
const lookAsShot = ref<LookTweaks | null>(null);
const look = ref<LookTweaks | null>(null);
// What the engine will render each tweak over, straight from the profile. Empty
// until a Sony decode reports one, which is also when the panel appears; a
// tweak the worker sent no range for gets no slider, since there would be
// nothing to say about how far it goes.
const lookRanges = ref<Partial<Record<LookTweakKey, [number, number]>>>({});
const lookSliders = computed(() => LOOK_TWEAK_ORDER.flatMap(key => {
  const range = lookRanges.value[key];
  return range ? [{ key, min: range[0], max: range[1] }] : [];
}));
// The Creative Look picker. Every ARW carries the calibration for all of the
// body's looks, not just the one that was selected when the shutter fired, so
// switching is a choice this client gets to make — and it costs no pixels: the
// looks share the body's one hue-segmented matrix, so only the tone curve and
// the chroma terms differ, and both ride in the profile. `null` means "as shot".
// The list comes from the file rather than a constant, so a body shipping looks
// this build has never heard of (FL2, FL3) still fills the picker.
const lookAsShotStyle = ref("");
const availableLooks = ref<string[]>([]);
// Set by the worker when the chosen look is not in this RAW at all — the body
// predates it, so its curve and chroma came from a donor. Its own answer rather
// than one derived here: the worker is what decides where a calibration is read
// from, and only it knows whether the donor was actually used.
const lookBorrowed = ref(false);
// The body's own sharpening, for the panel to report rather than to control:
// it is a camera setting, so nothing here can move it. Both ladder positions
// plus whether the fine half (Spica) is doing anything — the two stages split
// one control, and at the top of the range the split leaves nothing for the
// fine end, which is worth saying rather than showing a stage that is off.
const sharpening = ref<{ level: number; range: number; fine: boolean } | null>(null);
const lookStyle = ref<string | null>(null);
const effectiveLookStyle = computed(() => lookStyle.value ?? lookAsShotStyle.value);
const lookStyleEdited = computed(() =>
  !!lookStyle.value && !!lookAsShotStyle.value && lookStyle.value !== lookAsShotStyle.value);
// A type-level floor, not a state the panel can reach: everything that reads
// effectiveLook is gated on lookAsShot. Derived from LOOK_TWEAK_ORDER so the
// key list is written once (as SLIDER_DEFAULTS is, and for the same reason).
const ZERO_LOOK = Object.fromEntries(LOOK_TWEAK_ORDER.map(key => [key, 0])) as LookTweaks;
const effectiveLook = computed<LookTweaks>(() => look.value ?? lookAsShot.value ?? ZERO_LOOK);
// One reset for the whole panel: the look and its six tweaks are one setting
// as far as the camera is concerned, and resetting to "as shot" means both.
function resetLook(): void {
  look.value = null;
  lookStyle.value = null;
  droStrength.value = null;
  droLevel.value = DRO_AUTO;
  void reloadLookProfile();
}

function setLookStyle(style: string): void {
  lookStyle.value = style;
  void reloadLookProfile();
}

// ── DRO ──
//
// Sony's Dynamic Range Optimizer, on the same free path as the look: it is one
// gain per pixel, and a scalar gain commutes with the colour matrix, so the
// shader applies it to pixels that are already decoded. 1 is what the camera
// itself did; the ceiling matches the API's clampDroStrength.
const DRO_MAX = 2;
// True for any Sony RAW: Auto needs a curve in the file, but the manual levels
// come from Edit.exe's own presets and so work even on a frame that has none.
const droAvailable = ref(false);
// What the body applied: 1 when it used DRO, 0 when it did not. The reset
// target, and the value in force until someone moves the control.
const droAsShot = ref(0);
const droStrength = ref<number | null>(null);
const effectiveDro = computed(() => droStrength.value ?? droAsShot.value);
// The engine's own encoding: -1 is Auto (this shot's own curve), 0..99 picks one
// of the ten built-in presets. The ladder of offered levels comes from the
// worker rather than being repeated here, so the buttons cannot drift from the
// curves it actually renders.
const DRO_AUTO = -1;
const droLevel = ref(DRO_AUTO);
const droLevels = ref<number[]>([]);
// Off / Auto / a level. Off is a strength of zero whatever the level says,
// which is how the worker resolves the same three states.
const droMode = computed<"off" | "auto" | "level">(() =>
  effectiveDro.value <= 0 ? "off" : droLevel.value < 0 ? "auto" : "level");
const droEdited = computed(() =>
  droLevel.value >= 0
  || (droStrength.value !== null && droStrength.value !== droAsShot.value));

// The panel is "edited" if any of its three parts moved: the look, its tweaks,
// or DRO. One flag, because one reset button clears all three.
const lookEdited = computed(() =>
  lookStyleEdited.value
  || droEdited.value
  || (!!look.value && !!lookAsShot.value
    && LOOK_TWEAK_ORDER.some(key => look.value![key] !== lookAsShot.value![key])));

function setDro(value: number): void {
  droStrength.value = clamp(value, 0, DRO_MAX);
  void reloadLookProfile();
}

// Off keeps the level it was on so switching back does not silently land on
// Auto; the strength is what carries "off", exactly as on the worker side.
// Choosing Auto or a level from off has to restore a strength, and 1 is the
// only defensible one — it is what the camera itself would have applied.
function setDroMode(mode: "off" | "auto" | "level", level?: number): void {
  if (mode === "off") {
    droStrength.value = 0;
  } else {
    if (effectiveDro.value <= 0) droStrength.value = 1;
    droLevel.value = mode === "auto" ? DRO_AUTO : (level ?? droLevels.value[0] ?? 0);
  }
  void reloadLookProfile();
}

// Sony's own menu names, for both the Creative Look picker and the DCP style
// picker. Untranslated in every locale, and shown code-first exactly as the
// camera prints them ("ST Standard"), so the control reads the same as the
// body's menu — the code is what the RAW stores and what a Sony shooter knows
// the look by; the word is the reminder.
const STYLE_NAMES: Record<string, string> = {
  ST: "Standard", PT: "Portrait", LD: "Landscape", VV: "Vivid", VV2: "Vivid 2",
  NT: "Neutral", FL: "Film", IN: "Instant", SH: "Soft High-key",
  BW: "Black & White", SE: "Sepia", FL2: "Film 2", FL3: "Film 3",
};

// A look this build has never heard of still works — it just shows the bare
// code Sony wrote, which is the whole reason the list is read from the file.
function styleLabel(code: string): string {
  return STYLE_NAMES[code] ? `${code} ${STYLE_NAMES[code]}` : code;
}

const lookOptions = computed(() => availableLooks.value.map(code => ({
  value: code,
  label: styleLabel(code) + (code === lookAsShotStyle.value ? t("look.asShotSuffix") : ""),
})));

function setLookTweak(key: LookTweakKey, value: number): void {
  look.value = { ...effectiveLook.value, [key]: value };
}
const usingDcp = computed(() => activeProfileKind.value === "dcp");
// False for already-rendered sources (JPEG/PNG/TIFF), which the worker decodes
// into the same linear ProPhoto working space but which carry no mosaic, no
// camera profile and no embedded preview. Gates the controls that need those.
const isRawSource = ref(true);
// True when this shot brought lens correction splines of its own. Not every RAW
// does — adapted or manual glass writes none — and the Lens panel is nothing
// but a blend of those tables, so this is what says the panel has a job.
const lensCorrAvailable = ref(false);
// Fitted camera-match table (see worker fit_profile.py): pulls the DCP render
// toward the camera's own JPEG. On by default — it is the point of the profile —
// but toggleable to compare against Adobe's uncorrected look. Only meaningful
// when a table exists for this body+style (hasCameraMatch), else the row hides.
const cameraMatch = ref(true);
const hasCameraMatch = ref(false);
// AI RAW denoise. Applied in the worker on the Bayer mosaic before demosaic, so
// changing it re-decodes linear.bin (like dcpCode) rather than re-running the
// WebGL shader. amount is 0..100 (normalised to 0..1 for the API).
// edge and chroma ride Edit.exe's own 0..100 scale with 50 neutral, and go to
// the API unscaled — unlike amount. Edge sets how much fine detail survives
// (the camera's own value at 50); chroma scales the colour-noise threshold.
const defaultDenoise = () => ({
  enabled: false, model: "wavelet", amount: 100, edge: 50, chroma: 50,
});
const denoise = reactive(defaultDenoise());
const denoiseBusy = ref(false);
// Hold-to-compare: while true we draw the unedited original (baseline params +
// identity tone curve) so the before/after is easy to eyeball; release restores
// the live edit. crop/denoise/DCP are baked into linear.bin, so they stay applied.
const showOriginal = ref(false);
// Hold-to-compare against the camera's own JPEG (the preview embedded in the
// RAW). Unlike showOriginal this overlays a real <img> instead of re-rendering:
// the camera JPEG went through the manufacturer's tone pipeline, so it's a
// genuinely different reference from the neutral baseline render.
const showEmbedded = ref(false);
let currentSourceId = "";  // server id of the image currently in the renderer

let webglRenderer: PipelineRenderer | null = null;
const p3Supported = ref(false);
let rafId = 0;
let drawPending = false;

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
  { key: "red",     color: "#e04040" },
  { key: "orange",  color: "#e08040" },
  { key: "yellow",  color: "#c0b030" },
  { key: "green",   color: "#40b040" },
  { key: "aqua",    color: "#40a0a0" },
  { key: "blue",    color: "#4060d0" },
  { key: "purple",  color: "#8040c0" },
  { key: "magenta", color: "#c04090" },
] as const;

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
  { band: "sh", hueKey: "shH", satKey: "shS" },
  { band: "md", hueKey: "mdH", satKey: "mdS" },
  { band: "hl", hueKey: "hlH", satKey: "hlS" },
] as const;

// View settings (not part of the per-image recipe): display gamut + EXIF policy.
// exportStripPrivate: exports copy the RAW's full EXIF by default; 1 opts into
// stripping GPS/serials/owner/maker notes for exports meant to be shared.
const viewSettings = reactive({ displayGamut: 0, exportStripPrivate: 0 });

// ── Crop & Straighten ──
//
// Part of the per-image edit (snapshot/history/persistence). The crop editor
// renders the full straightened image with an overlay; committing just switches
// the display back to the cropped output. All geometry lives in crop.ts.
const crop = reactive<CropState>(defaultCrop());
const cropMode = ref(false);

// The sub-rectangle of the frame the canvas currently covers (output-frame px);
// null = the whole frame. See updateRenderWindow for why it exists.
const renderWindow = ref<ViewWindow | null>(null);

const {
  zoom, pan, fitScale, viewportRef, isPanning,
  displayTransform, canvasTransform, zoomPercent, visibleWindow,
  recomputeFit, startPan, doPan, stopPan,
  onWheel, zoomIn, zoomOut, fitView, zoomToFull, onDoubleClick,
} = useViewport({
  imageW, imageH, srcW, srcH, srcFullW, srcFullH, cropMode,
  renderOrigin: renderWindow,
});
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
  return `hsl(${gradingHueDeg(h)}, ${s}%, 50%)`;
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
const gradingEdited = computed(() => {
  const d = defaultGrading() as Record<string, number>;
  return Object.entries(grading).some(([k, v]) => v !== d[k]);
});

// Basic-panel values currently baked into the GPU curve LUT (Contrast, Blacks
// and Whites are all display-referred stages of the LUT chain, not shader
// uniforms). null = the LUT holds the identity curve (hold-to-compare swapped
// it in).
let bakedBasic: BasicAdjust | null = DEFAULT_BASIC;

function currentBasic(r: Recipe = recipe): BasicAdjust {
  return { contrast: r.contrast, blacks: r.blacks, whites: r.whites };
}

/** Rebake + upload the curve LUT with the live tone curve and Basic values. */
function bakeCurveLUT(): void {
  if (!webglRenderer) return;
  // While hold-to-compare is on, the GPU LUT must stay identity: a rebake from a
  // mid-hold re-decode or reset would otherwise leak Contrast/Blacks/the curve
  // into the 'before' view, with nothing to restore it until the key is released.
  if (showOriginal.value) {
    webglRenderer.uploadCurveLUT(IDENTITY_CURVE_LUT);
    bakedBasic = null;
    return;
  }
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
  profile?: ProfileId;  // colour engine; optional: absent in pre-Sony persisted sessions
  denoise?: typeof denoise;  // optional: absent in pre-denoise persisted sessions
  // Creative Look tweaks; null (or absent, in older sessions) means as shot.
  look?: LookTweaks | null;
  // Which Creative Look to render, when it is not the body's own. Same "null
  // means as shot" convention, and likewise absent in older sessions.
  lookStyle?: string | null;
  // DRO strength, on the same convention: null is what the camera applied.
  dro?: number | null;
  // Which DRO curve: -1 for the shot's own (Auto), 0..99 for a built-in preset.
  droLevel?: number | null;
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
    profile: "standard",
    denoise: defaultDenoise(),
    look: null,
    lookStyle: null,
    dro: null,
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
    profile: profileId.value,
    denoise: { ...denoise },
    look: look.value ? { ...look.value } : null,
    lookStyle: lookStyle.value,
    dro: droStrength.value,
    droLevel: droLevel.value,
  };
}

// Push a snapshot into the live reactive edit state (no draw scheduling — the
// caller decides whether to redraw or re-decode).
function setEditState(s: Snapshot): void {
  // Layer over defaults: snapshots persisted before a recipe key existed (e.g.
  // the lens corrections) must reset it, not inherit the previous image's value.
  Object.assign(recipe, defaultRecipe(), s.recipe);
  for (let i = 0; i < 8; i++) { hslHue[i] = s.hslHue[i]; hslSat[i] = s.hslSat[i]; hslLum[i] = s.hslLum[i]; }
  Object.assign(grading, s.grading);
  toneCurve.value = normalizeToneCurve(s.curve);
  curveActive.value = -1;
  Object.assign(crop, s.crop ? cloneCrop(s.crop) : defaultCrop());
  // Older snapshots have no aspect: fall back to "free" so a legacy crop box
  // that doesn't match the new default lock isn't reshaped by the next drag.
  cropAspect.value = s.aspect ?? "free";
  Object.assign(denoise, s.denoise ?? defaultDenoise());
  look.value = s.look ? { ...s.look } : null;
  lookStyle.value = s.lookStyle ?? null;
  droStrength.value = s.dro ?? null;
  // Snapshots taken before manual levels existed carry none, and Auto is what
  // they were rendered with.
  droLevel.value = s.droLevel ?? DRO_AUTO;
  dcpCode.value = s.dcp;
  profileId.value = s.profile ?? "standard";
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
  thumbSrc, resolveUrl, markInvalid,
  selectSource, removeSource,
  restoreSession, loadThumbCache, releaseThumbs, uploadFiles,
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
    lensCorr = null;
    lensCorrAvailable.value = false;
    isRawSource.value = true;
    hasCameraMatch.value = false;
    srcW.value = 0;
    srcH.value = 0;
    timing.value = null;
    destroyWebGL({ keepContext: true }); // frees the removed image's GPU texture
    status.value = "idle";
    errorMessage.value = null;
  },
  isRestoring: () => isRestoring,
  // Restore only keys this build still has: a stored session outlives the
  // settings it was written with (it carried a view-transform choice until the
  // AgX look was dropped), and a blind assign would revive dead state.
  sessionExtras: {
    get: () => ({ ...viewSettings }),
    apply: (v) => {
      for (const k of Object.keys(viewSettings) as (keyof typeof viewSettings)[]) {
        if (typeof v[k] === "number") viewSettings[k] = v[k];
      }
    },
  },
});

// Styles this camera actually ships, from the worker. Empty for a body we have
// no profiles for, which hides the picker rather than offering dead options.
const dcpStyles = ref<string[]>([]);
const dcpOptions = computed(() => dcpStyles.value.map(code => ({ value: code, label: styleLabel(code) })));

const engineOptions = computed<{ value: ProfileId; label: string }[]>(() => [
  { value: "standard", label: t("engine.adobe") },
  { value: "sony", label: t("engine.sony") },
]);
const gamutOptions = computed(() => [
  { value: 0, label: "sRGB" },
  { value: 1, label: t("display.p3") },
]);
const exifOptions = computed(() => [
  { value: 0, label: t("exif.full") },
  { value: 1, label: t("exif.private") },
]);

// Mirror the worker's choice into the picker. There is no "auto" entry: the
// style the metadata resolved to is simply the selected one, so the control
// always reads as what is actually applied. Assigning dcpCode here must not
// re-trigger the decode watcher — this *is* the result of that decode.
function applyDcpSelection(selection: ColorProfileMeta["selection"]): void {
  dcpStyles.value = selection?.availableCodes ?? [];
  const matched = selection?.matchedCode ?? "";
  if (matched === dcpCode.value) return;
  suppressDcpReload = true;
  dcpCode.value = matched;
  void nextTick(() => { suppressDcpReload = false; });
}

// The Lens group is driven entirely by the per-shot tables in the file's maker
// notes, and both of its sliders only blend those toward identity — so without
// a pair they move nothing at all. Gated on the tables themselves rather than
// on "is a RAW": a RAW shot on adapted glass carries none either.
const visibleGroups = computed(() =>
  groups.filter(g => !g.needsLensCorr || lensCorrAvailable.value));

// Full-res camera JPEG for the embedded-preview compare. Bound to the overlay
// <img> whenever a source is active, so the browser has it fetched before the
// first hold (thumbSrc may serve a 320px cache — too small to compare against).
// A rendered source's "embedded preview" is just the file itself, so comparing
// against it would show no difference — the mode only means something for RAW.
const embeddedSrc = computed(() =>
  isRawSource.value && activeSource.value?.embeddedUrl ? resolveUrl(activeSource.value.embeddedUrl) : "");

// The camera JPEG is the untouched full frame, so it has to go through the same
// recompose the render did — otherwise the hold jumps to a different framing and
// compares two different pictures. Only the committed view needs this: the
// compare is disabled inside the crop editor.
const embeddedTransform = computed(() => {
  if (!srcW.value || !srcH.value || !imageW.value || !imageH.value) return "";
  const [iw, ih] = imageDims(srcW.value, srcH.value, crop.orientation);
  return cssRecomposeMatrix(
    crop, srcW.value, srcH.value, cropOutputRect(crop, iw, ih), imageW.value, imageH.value);
});

// Denoise params for the render-linear request. amount is normalised to 0..1;
// disabled (or amount 0) tells the worker to skip inference entirely.
//
// `amount` therefore means two different things depending on where it is read.
// This payload is 0..1 (render-linear, clamped again in protocol.ts). The
// `denoise` state itself, and the Snapshot that persists it, are 0..100 — and
// XMP export reads *that* one, not this, so it writes 0..100 too (cli.py's
// daemon_export). Both ends are consistent today only because export passes
// `settings.denoise` rather than this function's output. Swapping one for the
// other silently changes the number by 100x, so keep them apart.
// edge and chroma are *not* rescaled: 0..100 is the wire scale for those.
function denoisePayload(d: typeof denoise = denoise): {
  enabled: boolean; model: string; amount: number; edge: number; chroma: number;
} {
  return {
    enabled: d.enabled, model: d.model, amount: d.amount / 100,
    edge: d.edge, chroma: d.chroma,
  };
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
    // look is sent only when it overrides the shot's own settings; without it
    // the worker renders what the body recorded, which is what a fresh import
    // wants and what the panel is then populated from.
    // Full sensor resolution, same as the export path: a capped preview is
    // visibly soft the moment the view is zoomed past fit. The per-frame cost of
    // that does not follow the decode — computePreviewScale sizes the drawing
    // buffer by on-screen device pixels, so a fit view shades the same number of
    // fragments it always did. What it does cost is transfer and VRAM (~140 MB
    // for 24 MP, ~360 MB for 61 MP, as float16).
    const lin = await fetchLinear({ sourceId: id, halfSize: false, maxSize: 0, profileId: profileId.value, dcpCode: dcpCode.value, denoise: denoisePayload(), look: look.value ?? undefined, style: lookStyle.value ?? undefined, dro: droStrength.value ?? undefined, droLevel: droLevel.value });
    if (stale()) return false;
    if (!lin) { markInvalid(id); return false; }
    const { meta: linMeta, pixels: linearFloat } = lin;

    hasLinearData = true;
    profileCurveLUT = buildProfileLUT(linMeta.colorProfile);
    lensCorr = parseLensCorr(linMeta.colorProfile?.lensCorr);
    // Whether this shot brought correction tables at all, which is what decides
    // if the Lens group is worth offering. Reactive because lensCorr itself is
    // not — it is read by the render path, not by the template.
    lensCorrAvailable.value = lensCorr !== null;
    isRawSource.value = linMeta.colorProfile?.kind !== "rendered-image";
    // What the worker *actually* rendered with, which is not always what was
    // asked for: "sony" falls back to the DCP path on a RAW without Sony's
    // calibration. The DCP-only controls follow this, not profileId.
    activeProfileKind.value = linMeta.colorProfile?.kind ?? null;
    // Reported by every Sony decode, so the panel shows the shot's own tweaks
    // the moment it appears rather than a row of zeros. Null on the DCP path,
    // which hides the panel — those stages are Sony's, not ours.
    lookAsShot.value = linMeta.colorProfile?.lookAsShot ?? null;
    // The ranges those numbers move over, on the same terms: the engine's, so
    // no slider here can offer a value the worker would clamp back.
    lookRanges.value = linMeta.colorProfile?.lookRanges ?? {};
    // The picker's own two facts. lookAsShotStyle is the reset target and comes
    // from the body; availableLooks is empty on a decode that reported none, in
    // which case the picker hides and the sliders still work.
    lookAsShotStyle.value = linMeta.colorProfile?.lookAsShotStyle
      ?? linMeta.colorProfile?.creativeLook ?? "";
    availableLooks.value = linMeta.colorProfile?.availableLooks ?? [];
    lookBorrowed.value = linMeta.colorProfile?.lookBorrowed === true;
    sharpening.value = readSharpening(linMeta.colorProfile);
    // What the body did with DRO, and whether there is a curve to scale at all.
    // Both are the worker's answer: only it reads the RAW.
    droAvailable.value = linMeta.colorProfile?.droAvailable === true;
    droAsShot.value = linMeta.colorProfile?.droAsShot ?? 0;
    // The offered ladder is the worker's, not ours. Empty simply leaves the
    // level buttons out, which is the right answer for a build that has none.
    droLevels.value = linMeta.colorProfile?.droLevels ?? [];
    applyDcpSelection(linMeta.colorProfile?.selection);
    // Availability is independent of the toggle, so the control stays visible
    // after the user switches the match off (which drops the applied cameraMatch).
    hasCameraMatch.value = linMeta.colorProfile?.cameraMatchAvailable === true;
    srcW.value = linMeta.width;
    srcH.value = linMeta.height;
    srcFullW.value = linMeta.fullWidth ?? linMeta.width;
    srcFullH.value = linMeta.fullHeight ?? linMeta.height;
    if (opts.resetView) { zoom.value = 1; pan.x = 0; pan.y = 0; }

    await nextTick();
    if (stale()) return false;
    if (!canvasRef.value) return false;
    if (!webglRenderer) {
      // WebGL2 missing is a whole-app condition, not a per-image decode error:
      // surface the dedicated unsupported state instead of an error toast.
      try {
        webglRenderer = new PipelineRenderer(canvasRef.value);
      } catch (err) {
        webglUnsupported.value = true;
        throw err;
      }
      p3Supported.value = webglRenderer.p3Supported;
    }
    webglRenderer.uploadImage(linearFloat, linMeta.width, linMeta.height);
    bakeCurveLUT();
    webglRenderer.uploadProfileCurveLUT(profileCurveLUT);
    webglRenderer.uploadDcpTables(parseDcpTables(linMeta.colorProfile));
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

// A moved Creative Look slider needs no pixels: the six tweaks reshape the tone
// curve, the chroma terms and Clarity's gain that the shader applies, and the
// decoded frame is already on the GPU. So this re-fetches the profile alone
// (~75 kB, most of it the tone curve) and re-uploads the LUT, instead of
// re-decoding like dcp/denoise do — the frame it replaces is tens of megabytes.
// Sequenced, not debounced: the request is cheap and a drag should track.
let lookProfileSeq = 0;
async function reloadLookProfile(): Promise<void> {
  if (!currentSourceId || !lookAsShot.value) return;
  const seq = ++lookProfileSeq;
  try {
    const profile = await fetchLookProfile(
      currentSourceId, effectiveLook.value, lookStyle.value ?? undefined,
      droStrength.value ?? undefined, droLevel.value);
    // A newer slider position (or a different image) owns the renderer now.
    if (seq !== lookProfileSeq || !profile || !webglRenderer) return;
    lookBorrowed.value = profile.lookBorrowed === true;
    sharpening.value = readSharpening(profile);
    profileCurveLUT = buildProfileLUT(profile);
    webglRenderer.uploadProfileCurveLUT(profileCurveLUT);
    scheduleWebGLDraw();
  } catch (err) {
    if (seq !== lookProfileSeq) return;
    status.value = "error";
    errorMessage.value = err instanceof Error ? err.message : String(err);
  }
}

// ── WebGL ──

// Live reactive state by default; pass a Snapshot to derive the params from a
// frozen edit instead (export). viewSettings stays live either way — it is
// view-only state and not part of a per-image snapshot.
function buildPipelineParams(s?: Snapshot): Partial<EditParams> {
  const r = s?.recipe ?? recipe;
  const [hue, sat, lum] = s ? [s.hslHue, s.hslSat, s.hslLum] : [hslHue, hslSat, hslLum];
  const g = s?.grading ?? grading;
  // Per-shot lens tables with the slider amounts mixed in. The renderer derives
  // the fill scale from these, so easing the slider eases the scale with it.
  const lensDist = lensCorr ? mixLensTable(lensCorr.distortion, (r.lensDistortion ?? 100) / 100) : [...LENS_IDENTITY];
  const lensVig = lensCorr ? mixLensTable(lensCorr.vignetting, (r.lensVignetting ?? 100) / 100) : [...LENS_IDENTITY];
  return {
    lensDist, lensVig,
    exposure: r.exposure,
    saturation: 1 + r.saturation / 100,
    highlights: r.highlights / 100,
    shadows: r.shadows / 100,
    vibrance: 1 + r.vibrance / 100,
    clarity: r.clarity,
    dehaze: r.dehaze,
    temperature: r.temperature,
    tint: r.tint,
    hslH: hue.map(v => v / 100),
    hslS: sat.map(v => v / 100),
    hslL: lum.map(v => v / 100),
    gradShTint: gradingTint(g.shH, g.shS / 100),
    gradMdTint: gradingTint(g.mdH, g.mdS / 100),
    gradHlTint: gradingTint(g.hlH, g.hlS / 100),
    gradBlend: g.blend / 100,
    gradBalance: g.balance / 100,
    displayGamut: viewSettings.displayGamut,
    // Not read off the snapshot: the match is a global profile setting, like the
    // display gamut, not a per-image edit that undo should travel with.
    cameraMatch: cameraMatch.value ? 1 : 0,
  };
}

// ── Camera profile tone curve (the camera's display rendering, applied in the view transform) ──

let profileCurveLUT: ProfileCurve = null;

// Per-image lens correction tables (canonical 16-knot grid), parsed from the
// decode response in loadSource. null = the RAW carries no correction data.
let lensCorr: LensCorr | null = null;

function buildProfileLUT(cp: ColorProfileMeta | null | undefined): ProfileCurve {
  const pts = cp?.profileToneCurve;
  if (!pts || pts.length < 2) return null;
  // A per-channel curve is basis-dependent, and the basis belongs to the profile:
  // Sony's MainGamma runs on the body's own near-Rec.709 primaries, a DCP's curve
  // in the ProPhoto working space. The shader rotates accordingly.
  // Sony's RGB2YCC follows the curve immediately and in the same basis, so it
  // travels with it — that keeps preview and export on one path.
  const cross = cp?.profileChromaCross, gain = cp?.profileChromaGain;
  return {
    lut: curveToLUT(pts.map(([x, y]) => ({ x, y }))),
    srgbBasis: cp?.kind === "sony",
    chroma: cross?.length === 4 && gain?.length === 4
      ? {
          cross, gain,
          // Fade 0 — every shot that never touched the slider — is pivot 0 and
          // contrast 1, which is also the right fallback for an older response.
          lumaPivot: cp?.profileLumaPivot ?? 0,
          lumaContrast: cp?.profileLumaContrast ?? 1,
          saturation: cp?.profileChromaSaturation ?? 1,
          sepia: cp?.profileSepia ?? null,
        }
      : null,
    // DRO. Absent whenever there is nothing to apply — the worker sends no
    // table at strength zero, so this is null both for a shot without DRO and
    // for one where it has been turned off.
    dro: cp?.profileDroGain?.length
      ? {
          lut: cp.profileDroGain,
          logCeiling: cp.droLogCeiling ?? 1,
          lumaWhite: cp.droLumaWhite ?? 1,
          // The bilateral grid, when the worker could build one. Its presence
          // switches the shader from indexing the curve by each pixel to
          // indexing it by the local mean, which is what DRO actually is.
          grid: cp.profileDroGrid ?? null,
          gridLumaWhite: cp.droGridLumaWhite ?? cp.droLumaWhite ?? 1,
        }
      : null,
    // Clarity, likewise absent when there is nothing to do — the worker sends a
    // zero gain for a shot that never touched the setting.
    clarity: cp?.profileClarity?.gain ? cp.profileClarity : null,
    // Sharpening, on the same terms. It runs just before Clarity and shares its
    // compose pass. Zero means there was no readable setting to reproduce, not
    // that the camera had sharpening off — it has no such position.
    sharpen: cp?.profileSharpness?.amount ? cp.profileSharpness : null,
    // Spica, between the two. Sony splits one control across it and sharpening
    // with complementary weights, so a shot can have this on with sharpening
    // barely doing anything, or the reverse — each is gated on its own amount.
    spica: cp?.profileSpica?.amount ? cp.profileSpica : null,
    // Marble's other half, the chroma cleanup — on for the same reason the three
    // above are, that the engine runs it and reproducing the engine means
    // running it. Unlike them it has no per-shot amount to gate on: Marble runs
    // unconditionally, so the gate is just "is this a Sony render at all", which
    // profileSpica/profileSharpness being present is what says. A JPEG or a DCP
    // render has no Marble to reproduce.
    //
    // The largest single correction in the chain: llr's colour-difference noise
    // measured 6.59x Edit's without it and 1.31x with, on three frames, with
    // luma untouched (sony_repro/notes/measured-chroma-gap.md 2.8, 2.9). 1.31 is
    // the fast form this actually runs; the exact reference is 1.35, so the
    // approximation costs nothing here — box-averaging the moments happens to
    // land slightly closer than the reference's own wider box.
    chromaNr: cp?.profileSpica || cp?.profileSharpness ? 1 : 0,
  };
}

/**
 * What the panel says about the body's sharpening, or null when there is
 * nothing to say — an unreadable SharpnessRange, which is the only thing that
 * turns the coarse stage off. `range` of -1 never reaches here for that reason.
 */
function readSharpening(cp: ColorProfileMeta | null | undefined) {
  const sharpen = cp?.profileSharpness;
  if (!sharpen?.amount || sharpen.range === undefined || sharpen.level === undefined) return null;
  return {
    level: sharpen.level,
    range: sharpen.range,
    fine: !!cp?.profileSpica?.amount,
  };
}

// ── Histogram ──

// Whether the renderer holds decoded pixels (histogram guard). The decoded
// pixel array itself lives on the GPU after upload — keeping a JS reference
// here would pin ~50 MB per image for nothing.
let hasLinearData = false;

// In the crop editor the canvas renders a padded straighten bbox whose
// out-of-image fill would be binned as real pixels — hand the histogram the
// tight crop box instead (it re-renders offscreen with its own transform).
function cropHistogramView(): { width: number; height: number; texXform: Float32Array } {
  const [iw, ih] = currentImageDims();
  const rect = cropOutputRect(crop, iw, ih);
  const [ow, oh] = cropOutputSize(crop, srcW.value, srcH.value);
  return { width: ow, height: oh, texXform: buildCropTransform(crop, srcW.value, srcH.value, rect) };
}

// Histogram updates are throttled and always deferred off the synchronous
// draw/decode path: the read-back stalls the main thread, and at 60fps it would
// recompute far more often than anyone can read. ~11 Hz with a trailing update
// keeps it responsive without taxing slider drags or inflating decode timing.
const histogram = useHistogram({
  renderer: () => webglRenderer,
  ready: () => hasLinearData && !!imageW.value && !!imageH.value,
  view: () => (cropMode.value ? cropHistogramView() : undefined),
});
const histoCanvasRef = histogram.canvasRef;
const scheduleHistogram = histogram.schedule;

function scheduleWebGLDraw(): void {
  if (drawPending) return;
  drawPending = true;
  rafId = requestAnimationFrame(() => { drawPending = false; drawWebGL(); scheduleHistogram(); });
}

// Edit-free baseline for hold-to-compare: keep view-only settings (display
// gamut) but drop every per-image adjustment. The matching identity tone curve is
// swapped in by the showOriginal watcher (the curve lives in a GPU LUT, not params).
const IDENTITY_CURVE_LUT = buildToneCurveLUT(defaultToneCurve());
function baselineParams(): Partial<EditParams> {
  // Camera match survives the hold for the same reason the gamut does: it is
  // part of how this camera is rendered, not an edit being compared against.
  return { displayGamut: viewSettings.displayGamut, cameraMatch: cameraMatch.value ? 1 : 0 };
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

// The canvas's CSS box: the render window when there is one, the whole frame
// otherwise. Its transform carries the matching origin offset.
const canvasBoxStyle = computed(() => {
  const w = renderWindow.value;
  return {
    transform: canvasTransform.value,
    width: `${w ? w.w : imageW.value}px`,
    height: `${w ? w.h : imageH.value}px`,
  };
});

// How far beyond the viewport the render window reaches, as a fraction of the
// visible size on each side. Pans within it are pure CSS transform, exactly as
// they were before the window existed; the cost is shading that much extra.
const WINDOW_SLACK = 0.25;
// Rebuild once the window is this many times wider/taller than it needs to be.
// Without it the window would only ever grow: zooming in shrinks the visible
// region monotonically, every smaller region is still contained by the old
// window, and it would sit at whatever size the first zoom step produced. Must
// exceed 1 + 2*WINDOW_SLACK or a freshly built window would re-trigger at once.
const WINDOW_SHRINK = 2;

/**
 * Keep the render window covering what the viewport can see, and report whether
 * it moved (i.e. whether a redraw is owed).
 *
 * Zoomed in, the canvas covers only the visible part of the frame instead of all
 * of it. previewScale cannot do this on its own: it is capped at 1 so as never
 * to supersample the source, which at 1:1 means a drawing buffer the size of the
 * whole frame — 33 MP of fragments for the ~1 MP a viewport can show. Frame cost
 * is very nearly linear in fragment count, so this is that ratio, saved.
 *
 * Returns false for a pan that stays inside the current window: those pixels are
 * already on the canvas and CSS moves it, which is what keeps panning smooth.
 */
function updateRenderWindow(): boolean {
  const cur = renderWindow.value;
  // The crop editor already renders a size-capped bbox, and its overlay geometry
  // assumes a canvas covering that whole box.
  const visible = cropMode.value ? null : visibleWindow(0);
  if (!visible) {
    if (!cur) return false;
    renderWindow.value = null;
    return true;
  }
  const covered = cur
    && cur.x <= visible.x && cur.y <= visible.y
    && cur.x + cur.w >= visible.x + visible.w
    && cur.y + cur.h >= visible.y + visible.h;
  const oversized = cur
    && (cur.w > visible.w * WINDOW_SHRINK || cur.h > visible.h * WINDOW_SHRINK);
  if (covered && !oversized) return false;
  renderWindow.value = visibleWindow(WINDOW_SLACK);
  return true;
}

function drawWebGL(): void {
  if (!webglRenderer) return;
  // Contrast/Blacks/Whites all live in the curve LUT bake, not shader uniforms.
  // Draws are rAF-coalesced (scheduleWebGLDraw), so this rebakes at most once
  // per frame during a slider drag (sub-millisecond on the CPU).
  if (!showOriginal.value && (bakedBasic === null || !sameBasic(bakedBasic, currentBasic()))) {
    bakeCurveLUT();
  }
  updateRenderWindow();
  webglRenderer.setViewWindow(renderWindow.value);
  webglRenderer.setPreviewScale(computePreviewScale());
  webglRenderer.draw(showOriginal.value ? baselineParams() : buildPipelineParams());
}

function startCompare(): void { if (activeSource.value && !cropMode.value) showOriginal.value = true; }
function endCompare(): void { showOriginal.value = false; }
function startCompareEmbedded(): void {
  if (activeSource.value && !cropMode.value && embeddedSrc.value) showEmbedded.value = true;
}
function endCompareEmbedded(): void { showEmbedded.value = false; }

// ── WebGL context loss ──
//
// A GPU reset, mobile tab backgrounding, or context-slot eviction kills every
// GL object. preventDefault() opts into the browser's restorable path; on
// restore the renderer is rebuilt from scratch and the active image reloaded
// (deleting the dead handles via release() is a safe no-op on a lost context).
const webglUnsupported = ref(false);

function onContextLost(e: Event): void {
  e.preventDefault();
  destroyWebGL({ keepContext: true });
  status.value = "error";
  errorMessage.value = t("error.contextLost");
}

function onContextRestored(): void {
  errorMessage.value = null;
  status.value = "idle";
  if (currentSourceId) void loadSource(currentSourceId, { resetView: false });
}

// keepContext: release GL objects but keep the canvas's context usable, so the
// persistent preview canvas can host a new renderer later (a canvas whose
// context was lost via destroy() can never get another one).
function destroyWebGL(opts: { keepContext?: boolean } = {}): void {
  if (rafId) { cancelAnimationFrame(rafId); rafId = 0; }
  histogram.cancel();
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
  // The frame just changed size, so any window from the previous one is stated
  // in coordinates that no longer mean the same thing. drawWebGL rebuilds it.
  renderWindow.value = null;
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
  renderWindow.value = null;   // same reason as renderNormal
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
  // A compare hold can't be released once the crop editor owns the keys/view.
  showOriginal.value = false;
  showEmbedded.value = false;
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

  // Shift+Backslash ("|") holds the camera-JPEG view (the RAW's embedded preview).
  if (!inEditableText && e.key === "|" && activeSource.value && !cropMode.value) {
    e.preventDefault();
    if (embeddedSrc.value) showEmbedded.value = true;
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
  // Releasing Shift before the key makes the keyup report "\" instead of "|",
  // so either key ends the camera-JPEG hold.
  if (e.key === "\\" || e.key === "|") showEmbedded.value = false;
}

// Alt-Tab (or Cmd+backslash) mid-hold sends the keyup to the newly focused
// window, so onKeyUp never fires and the 'before' view would stick on forever.
function onWindowBlur(): void {
  showOriginal.value = false;
  showEmbedded.value = false;
}

// ── upload ──

// Auto-redraw on edit. Suppressed during restore/switch so we don't flash the
// previous image with the new params before loadSource() uploads the pixels.
// Hold-to-compare swaps the tone-curve LUT (a GPU texture, not a draw param) for
// identity while previewing the original, restoring the live curve on release.
watch(showOriginal, () => {
  // bakeCurveLUT owns the rule: it swaps in identity while showOriginal is on
  // and restores the live curve when it clears, so the watcher only has to bake.
  if (!webglRenderer) return;
  bakeCurveLUT();
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

// Panning is a CSS transform and normally costs no redraw at all. Zoomed in the
// canvas only covers the visible part of the frame (updateRenderWindow), so a
// pan that reaches the edge of that window has to re-render — but only then,
// which is what the window's slack is for.
watch([() => pan.x, () => pan.y], () => {
  if (!webglRenderer || cropMode.value) return;
  if (updateRenderWindow()) scheduleWebGLDraw();
});

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
watch([toneCurve, dcpCode, profileId, denoise, cropAspect, look],
  () => { scheduleHistoryCommit(); schedulePersist(); }, { deep: true });

// The Creative Look sliders. Not a re-decode: reloadLookProfile swaps the
// profile LUT under the pixels already on the GPU. Suppressed only while a
// source load is in flight (that request carries the same tweaks itself) —
// undo/redo must go through here, exactly as it does for dcpCode.
watch(look, () => {
  if (suppressDcpReload) return;
  void reloadLookProfile();
}, { deep: true });

// Re-decode when the user changes the DCP style or the colour engine (keeps the
// current view). Suppressed while a source load is already handling the decode.
watch([dcpCode, profileId], async () => {
  if (suppressDcpReload || !currentSourceId) return;
  await loadSource(currentSourceId, { resetView: false });
});

// The camera-match table is a HueSatMap, and every HueSatMap now travels to the
// shader rather than being baked into linear.bin — so this is a redraw, not a
// re-decode. It used to cost a full RAW round-trip.
watch(cameraMatch, () => {
  if (!currentSourceId) return;
  scheduleWebGLDraw();
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
  window.removeEventListener('blur', onWindowBlur);
  window.removeEventListener('beforeunload', persistOnUnload);
  document.removeEventListener('visibilitychange', persistOnHidden);
  canvasRef.value?.removeEventListener('webglcontextlost', onContextLost);
  canvasRef.value?.removeEventListener('webglcontextrestored', onContextRestored);
  resizeObs?.disconnect();
  histogram.dispose();
  releaseThumbs();
  destroyWebGL();
});

onMounted(async () => {
  window.addEventListener('keydown', onKeyDown);
  window.addEventListener('keyup', onKeyUp);
  window.addEventListener('blur', onWindowBlur);
  window.addEventListener('beforeunload', persistOnUnload);
  document.addEventListener('visibilitychange', persistOnHidden);
  canvasRef.value?.addEventListener('webglcontextlost', onContextLost);
  canvasRef.value?.addEventListener('webglcontextrestored', onContextRestored);
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

// A drag only means "import" when it actually carries files. Dragging a
// filmstrip thumbnail — or any text/link — fires dragover on the shell too, and
// without this gate the full-screen import overlay flashes on every such drag.
function onDragOver(e: DragEvent): void {
  e.preventDefault();
  if (e.dataTransfer?.types.includes("Files")) isDragging.value = true;
}

// dragleave bubbles from every child the pointer crosses on its way across the
// shell; only a null relatedTarget means the drag really left the window.
function onDragLeave(e: DragEvent): void {
  if (!e.relatedTarget) isDragging.value = false;
}

async function onDrop(e: DragEvent): Promise<void> {
  e.preventDefault();
  isDragging.value = false;
  const files = e.dataTransfer?.files;
  if (!files?.length) return;
  await uploadFiles(Array.from(files));
}

function resetRecipe(): void { Object.assign(recipe, defaultRecipe()); resetHslGrading(); }

// ── Export ──

function exportFilename(): string {
  const name = activeSource.value?.name ?? "export";
  return `${name.replace(/\.[^.]+$/, "")}.jpg`;
}

// Freeze the edit state into an export plan: the full-res decode takes seconds
// and the filmstrip stays clickable, so everything past an await must read from
// this one snapshot — deriving it all from `settings` also guarantees the
// rendered pixels and the embedded XMP cannot drift apart. The fetch/render/
// embed/download mechanics live in useExport.
function buildExportPlan(): ExportPlan | null {
  if (!currentSourceId || !activeSource.value) return null;
  const settings = captureSnapshot();
  const cropSnap = settings.crop;
  return {
    sourceId: currentSourceId,
    filename: exportFilename(),
    settings,
    stripPrivate: viewSettings.exportStripPrivate === 1,
    dcpCode: settings.dcp,
    profileId: settings.profile ?? "standard",
    denoise: denoisePayload(settings.denoise),
    // Absent means as shot, same as everywhere else — the full-res decode then
    // rebuilds the profile the preview was showing (plan.profileLUT).
    look: settings.look ?? undefined,
    lookStyle: settings.lookStyle ?? undefined,
    dro: settings.dro ?? undefined,
    params: buildPipelineParams(settings),
    curveLUT: buildToneCurveLUT(settings.curve, currentBasic(settings.recipe)),
    profileLUT: (meta) => buildProfileLUT(meta.colorProfile),
    output: (meta) => {
      const [iw, ih] = imageDims(meta.width, meta.height, cropSnap.orientation);
      const rect = cropOutputRect(cropSnap, iw, ih);
      // Snap the output dims to the locked aspect so e.g. a 4:3 crop exports at
      // an exact 4:3 pixel size instead of each axis rounding independently.
      const fraction = resolveAspectFraction(settings.aspect ?? "free", meta.width, meta.height, cropSnap);
      const [width, height] = cropOutputSizeForAspect(cropSnap, meta.width, meta.height, fraction);
      return { width, height, texXform: buildCropTransform(cropSnap, meta.width, meta.height, rect) };
    },
    background: WORKSPACE_BG,
  };
}

const { exporting, exportImage } = useExport({ status, errorMessage, plan: buildExportPlan });

// White balance reads as a colour axis, not an amount: an accent fill growing
// from the left would say "this is set" on a slider sitting at its default.
// Neutral is placed where the default value puts the thumb (6500K -> 45%).
const WB_TRACK: Partial<Record<RecipeKey, string>> = {
  temperature: "linear-gradient(to right, #3f7dff, #f2ead9 45%, #ffb648)",
  tint: "linear-gradient(to right, #4fc26a, #d8d8d8 50%, #d264d8)",
};

// Lightroom-style scroll-to-nudge: scrolling over the *selected* range slider
// steps the value by one `step` (Shift ×10) instead of scrolling the panel.
// Applied via event delegation on the settings rail so every slider — base, HSL,
// grading, denoise, crop angle — gets it without per-input wiring.
//
// Hover alone must not be enough: the rail is a tall scrolling column of sliders,
// so a plain wheel gesture aimed at the panel would land on whatever slider
// happened to be under the pointer and silently edit the photo. Two guards:
// the slider has to hold focus (a click selects it), and ownership is decided
// once per gesture — a scroll that starts on the panel keeps scrolling the panel
// even as sliders slide beneath the pointer.
const WHEEL_GESTURE_GAP_MS = 200;

type WheelAdjustHandlers = {
  onWheel: (e: WheelEvent) => void;
  onPointerDown: (e: PointerEvent) => void;
};

const rangeUnder = (e: Event): HTMLInputElement | null =>
  ((e.target as HTMLElement | null)?.closest?.('input[type="range"]') ?? null) as HTMLInputElement | null;

const vWheelAdjust = {
  mounted(el: HTMLElement) {
    let gestureEnd = 0;
    let owner: HTMLInputElement | null = null;
    // Selecting by click is the other half of the rule, so it lives here too —
    // and a mousedown does not focus a range input in Safari, so the UA cannot
    // be trusted to do it.
    const onPointerDown = (e: PointerEvent) => rangeUnder(e)?.focus();
    const onWheel = (e: WheelEvent) => {
      const input = rangeUnder(e);
      const selected = !!input && !input.disabled && document.activeElement === input;
      if (e.timeStamp > gestureEnd) owner = selected ? input : null;
      gestureEnd = e.timeStamp + WHEEL_GESTURE_GAP_MS;
      if (!owner || owner !== input) return; // let the rail scroll
      e.preventDefault();
      const step = Number(owner.step) || 1;
      const min = Number(owner.min);
      const max = Number(owner.max);
      const cur = Number(owner.value);
      const mult = e.shiftKey ? 10 : 1;
      const dir = e.deltaY < 0 ? 1 : -1; // scroll up → increase
      let next = clamp(cur + dir * step * mult, min, max);
      const decimals = (String(step).split(".")[1] || "").length;
      if (decimals) next = Number(next.toFixed(decimals));
      if (next === cur) return;
      owner.value = String(next);
      owner.dispatchEvent(new Event("input", { bubbles: true }));
      owner.dispatchEvent(new Event("change", { bubbles: true }));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    el.addEventListener("pointerdown", onPointerDown);
    (el as unknown as { _wheelAdjust?: WheelAdjustHandlers })._wheelAdjust = { onWheel, onPointerDown };
  },
  unmounted(el: HTMLElement) {
    const h = (el as unknown as { _wheelAdjust?: WheelAdjustHandlers })._wheelAdjust;
    if (!h) return;
    el.removeEventListener("wheel", h.onWheel);
    el.removeEventListener("pointerdown", h.onPointerDown);
  },
};
</script>

<template>
  <div class="app" :class="{ 'is-drag': isDragging, 'no-filmstrip': !sources.length }"
    @dragover="onDragOver"
    @dragleave="onDragLeave"
    @drop="onDrop">
    <header class="topbar">
      <div class="brand">
        <span class="brand-mark" aria-hidden="true" />
        <span class="brand-name">LLR</span>
      </div>
      <div class="topbar-actions">
        <button class="icon-btn" :disabled="!canUndo" @click="undo" :title="t('action.undo')" :aria-label="t('aria.undo')">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M3 9h11a5 5 0 0 1 0 10H8" />
            <path d="M7 5L3 9l4 4" />
          </svg>
        </button>
        <button class="icon-btn" :disabled="!canRedo" @click="redo" :title="t('action.redo')" :aria-label="t('aria.redo')">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 9H10a5 5 0 0 0 0 10h6" />
            <path d="M17 5l4 4-4 4" />
          </svg>
        </button>
        <button class="icon-btn" :class="{ 'is-on': cropMode }" :disabled="!activeSource" @click="toggleCropMode"
          :title="t('action.crop')" :aria-label="t('aria.crop')">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M6 2v14a2 2 0 0 0 2 2h14" />
            <path d="M2 6h14a2 2 0 0 1 2 2v14" />
          </svg>
        </button>
        <button class="icon-btn" :class="{ 'is-on': showOriginal }" :disabled="!activeSource || cropMode"
          @mousedown="startCompare" @mouseup="endCompare" @mouseleave="endCompare"
          @touchstart.prevent="startCompare" @touchend.prevent="endCompare" @touchcancel="endCompare"
          :title="t('action.compareOriginal')" :aria-label="t('aria.compareOriginal')">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="5" width="18" height="14" rx="2" />
            <path d="M12 5v14" />
          </svg>
        </button>
        <button class="icon-btn" :class="{ 'is-on': showEmbedded }" :disabled="!activeSource || cropMode || !embeddedSrc"
          @mousedown="startCompareEmbedded" @mouseup="endCompareEmbedded" @mouseleave="endCompareEmbedded"
          @touchstart.prevent="startCompareEmbedded" @touchend.prevent="endCompareEmbedded" @touchcancel="endCompareEmbedded"
          :title="t('action.compareJpeg')" :aria-label="t('aria.compareJpeg')">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M20 19H4a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2h3l2-2.5h6L17 7h3a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2z" />
            <circle cx="12" cy="13" r="3.5" />
          </svg>
        </button>
      </div>
      <div class="meta-summary">
        <template v-if="activeSource">
          <span>{{ activeSource.name }}</span>
          <span class="meta-empty">{{ formatBytes(activeSource.size) }}</span>
        </template>
        <span v-else class="meta-empty">{{ t('meta.noImage') }}</span>
      </div>
      <button class="export-btn" type="button" :disabled="!activeSource || exporting" @click="exportImage">
        <svg v-if="!exporting" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 3v12" />
          <path d="M8 11l4 4 4-4" />
          <path d="M5 21h14" />
        </svg>
        <span class="export-spinner" v-else aria-hidden="true" />
        <span>{{ exporting ? t('action.exporting') : t('action.export') }}</span>
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
            <p class="dropzone-title">{{ t('dropzone.title') }}</p>
            <p class="dropzone-sub">{{ t('dropzone.sub') }}</p>
            <p class="dropzone-hint">{{ IMPORT_FORMAT_HINT }}</p>
          </div>
        </div>
        <div v-show="activeSource && status === 'rendering' && !webglRenderer" class="preview-loading">
          <span class="spinner spinner-lg" aria-hidden="true" />
          <span>{{ t('status.decoding') }}</span>
        </div>
        <div v-show="status === 'uploading' && !activeSource" class="preview-loading">
          <span class="spinner spinner-lg" aria-hidden="true" />
          <span>{{ t('status.importing') }}</span>
        </div>
        <!-- Sized and placed by the render window, which zoomed in covers only
             the visible part of the frame; the compare overlays below stay
             full-frame and so keep displayTransform. -->
        <canvas v-show="webglRenderer != null && activeSource && !activeSource.invalid" ref="canvasRef" class="preview" :style="canvasBoxStyle" />
        <!-- Camera-JPEG compare: opaque overlay in the canvas's exact box, with
             the full-frame JPEG placed inside it through the edit's own crop /
             straighten / flip so both sides show the same framing. The src stays
             bound while a source is active so the JPEG is already fetched when
             the hold starts. -->
        <div v-show="showEmbedded && webglRenderer != null && activeSource && !activeSource.invalid"
          class="preview compare-embedded"
          :style="{ transform: displayTransform, width: imageW + 'px', height: imageH + 'px' }">
          <img :style="{ transform: embeddedTransform, width: srcW + 'px', height: srcH + 'px' }"
            :src="embeddedSrc || undefined" :alt="t('aria.cameraJpeg')" />
        </div>
        <div v-show="activeSource && webglRenderer && (status === 'rendering' || status === 'uploading')"
          class="viewport-busy" aria-live="polite">
          <span class="spinner" aria-hidden="true" />
          <span>{{ status === 'uploading' ? t('status.importing') : t('status.decoding') }}</span>
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
        <img v-show="activeSource && !activeSource.invalid && !webglRenderer && status !== 'rendering'" class="preview" :style="{ transform: displayTransform }" :src="activeSource ? thumbSrc(activeSource) : ''" :alt="t('aria.preview')" />
        <div v-if="activeSource?.invalid" class="invalid-state">
          <img v-if="activeSource && thumbSrc(activeSource)" :src="thumbSrc(activeSource)" :alt="activeSource.name" />
          <p class="invalid-title">{{ t('invalid.missingTitle') }}</p>
          <p class="invalid-sub">{{ t('invalid.missingSub') }}</p>
        </div>
        <div v-if="webglUnsupported" class="invalid-state">
          <p class="invalid-title">{{ t('invalid.webglTitle') }}</p>
          <p class="invalid-sub">{{ t('invalid.webglSub') }}</p>
        </div>
      </div>

      <footer class="status" v-show="activeSource">
        <div class="status-left">
          <div class="status-cell">
            <span class="status-label">{{ t('status.label') }}</span>
            <span class="status-value" :data-state="status">{{ timing ? t('status.decodedIn', { ms: timing }) : status }}</span>
          </div>
        </div>
        <div class="status-right">
          <div class="status-cell zoom-cell" v-if="activeSource">
            <button class="zoom-btn" @click="zoomOut" :disabled="zoom <= 0.1">−</button>
            <span class="zoom-percent">{{ zoomPercent }}%</span>
            <button class="zoom-btn" @click="zoomIn" :disabled="zoom >= 50">+</button>
            <button class="zoom-btn" @click="fitView">{{ t('zoom.fit') }}</button>
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
          <span>{{ t('panel.crop') }}</span>
          <button class="ghost" type="button" @click="resetCrop">{{ t('common.reset') }}</button>
        </header>
        <div class="control-row">
          <label class="control-label">{{ t('crop.aspect') }}</label>
          <div class="crop-aspect">
            <SelectMenu :model-value="customAspect ? 'custom' : cropAspect" :options="aspectOptions"
              :aria-label="t('crop.aspect')" @update:model-value="selectAspect" />
            <button class="icon-mini" type="button" :title="t('crop.swap')" @click="swapAspect" :aria-label="t('aria.swapAspect')">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M16 3l4 4-4 4" /><path d="M20 7H8a4 4 0 0 0-4 4" />
                <path d="M8 21l-4-4 4-4" /><path d="M4 17h12a4 4 0 0 0 4-4" />
              </svg>
            </button>
          </div>
        </div>
        <div class="control-row" v-if="customAspect">
          <label class="control-label">{{ t('crop.ratio') }}</label>
          <div class="crop-custom">
            <input class="slider-number" type="number" min="0.1" step="0.1" :value="customAspect[0]"
              @change="setCustomAspect(($event.target as HTMLInputElement).valueAsNumber, customAspect![1])" />
            <span class="crop-custom-x">×</span>
            <input class="slider-number" type="number" min="0.1" step="0.1" :value="customAspect[1]"
              @change="setCustomAspect(customAspect![0], ($event.target as HTMLInputElement).valueAsNumber)" />
          </div>
        </div>
        <SliderRow :model-value="Number(crop.angle.toFixed(1))" @update:model-value="setAngle"
          :label="t('crop.angle')" :min="-45" :max="45" :step="0.1" />
        <div class="crop-buttons">
          <button type="button" class="crop-tool" :title="t('crop.rotateLeft')" @click="rotateCrop(-1)">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M3 12a9 9 0 1 0 3-6.7L3 8" /><path d="M3 3v5h5" />
            </svg>
          </button>
          <button type="button" class="crop-tool" :title="t('crop.rotateRight')" @click="rotateCrop(1)">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M21 12a9 9 0 1 1-3-6.7L21 8" /><path d="M21 3v5h-5" />
            </svg>
          </button>
          <button type="button" class="crop-tool" :class="{ 'is-on': crop.flipH }" :title="t('crop.flipH')" @click="flipCropH">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 3v18" /><path d="M8 7l-4 5 4 5" /><path d="M16 7l4 5-4 5" />
            </svg>
          </button>
          <button type="button" class="crop-tool" :class="{ 'is-on': crop.flipV }" :title="t('crop.flipV')" @click="flipCropV">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M3 12h18" /><path d="M7 8l5-4 5 4" /><path d="M7 16l5 4 5-4" />
            </svg>
          </button>
        </div>
        <button type="button" class="crop-done" @click="exitCropMode">{{ t('crop.done') }}</button>
      </section>

      <section class="panel" v-show="!cropMode">
        <header class="panel-head">
          <span>{{ t('panel.settings') }}</span>
          <button class="ghost" type="button" @click="resetRecipe">{{ t('common.reset') }}</button>
        </header>
        <div class="control-row">
          <label class="control-label">{{ t('settings.language') }}</label>
          <SelectMenu :model-value="locale" :options="LOCALES" :aria-label="t('settings.language')"
            @update:model-value="setLocale" />
        </div>
        <div class="control-row" v-if="activeSource && isRawSource">
          <label class="control-label" :title="t('settings.engineHint')">{{ t('settings.engine') }}</label>
          <SelectMenu v-model="profileId" :options="engineOptions" :aria-label="t('settings.engine')" />
        </div>
        <!-- The look itself is picked in the Creative Look panel, next to the
             tweaks it belongs with and the reset that returns both to as-shot. -->
        <p class="control-note" v-if="activeSource && isRawSource && profileId === 'sony' && usingDcp">
          {{ t('engine.sonyUnavailable') }}
        </p>
        <div class="control-row" v-if="activeSource && isRawSource && usingDcp && dcpStyles.length">
          <label class="control-label">{{ t('settings.dcp') }}</label>
          <SelectMenu v-model="dcpCode" :options="dcpOptions" :aria-label="t('settings.dcp')" />
        </div>
        <div class="control-row" v-if="activeSource && isRawSource && usingDcp && hasCameraMatch">
          <label class="control-label" for="camera-match" :title="t('settings.cameraMatchHint')">{{ t('settings.cameraMatch') }}</label>
          <label class="switch">
            <input id="camera-match" type="checkbox" v-model="cameraMatch" />
            <span class="switch-track"><span class="switch-thumb" /></span>
          </label>
        </div>
        <div class="control-row" v-if="activeSource && p3Supported">
          <label class="control-label">{{ t('settings.display') }}</label>
          <SelectMenu v-model="viewSettings.displayGamut" :options="gamutOptions"
            :aria-label="t('settings.display')" />
        </div>
        <div class="control-row" v-if="activeSource">
          <label class="control-label">{{ t('settings.exportExif') }}</label>
          <SelectMenu v-model="viewSettings.exportStripPrivate" :options="exifOptions" :title="t('exif.hint')"
            :aria-label="t('settings.exportExif')" />
        </div>
      </section>
      <!-- The shot's in-camera Creative Look tweaks. Its own panel because these
           are Sony's stages, not ours: they start at what the body recorded and
           reset back to it, which is why they cannot share the Tone panel. -->
      <section class="panel" v-if="lookAsShot && !cropMode">
        <header class="panel-head">
          <span class="panel-title">
            {{ t('panel.creativeLook') }}
            <span v-if="lookEdited" class="panel-dot" aria-hidden="true" />
          </span>
          <button class="ghost" type="button" :disabled="!lookEdited" @click="resetLook">
            {{ t('common.reset') }}
          </button>
        </header>
        <!-- Every ARW carries all of the body's looks, so this switches between
             calibrations already in the file. No re-decode: they share the one
             hue-segmented matrix, so only the curve and chroma terms change. -->
        <div v-if="availableLooks.length > 1" class="control-row">
          <label class="control-label" for="look-style">{{ t('settings.creativeLook') }}</label>
          <SelectMenu id="look-style" :model-value="effectiveLookStyle" :options="lookOptions"
            @update:model-value="setLookStyle" />
        </div>
        <!-- Say so when the look is not in the RAW. The curve is exact; the
             chroma is another body's, and that is worth admitting on screen. -->
        <p class="control-note" v-if="lookBorrowed">{{ t('look.borrowedHint') }}</p>
        <!-- Sharpening is a camera setting, not one of the six tweaks, so it is
             reported rather than offered: there is no slider here that could
             move it. The two ladder positions are what the body's own menu
             showed; the note says which halves of the effect are running. -->
        <div class="control-row" v-if="sharpening">
          <span class="control-label">{{ t('look.sharpening') }}</span>
          <span class="control-value">
            {{ t('look.sharpeningValue', { level: `+${sharpening.level}`, range: `+${sharpening.range}` }) }}
          </span>
        </div>
        <p class="control-note" v-if="sharpening">
          {{ t(sharpening.fine ? 'look.sharpeningHint' : 'look.sharpeningCoarseOnly') }}
        </p>
        <SliderRow v-for="spec in lookSliders" :key="spec.key"
          :model-value="effectiveLook[spec.key]" @update:model-value="setLookTweak(spec.key, $event)"
          :label="t(`lookSlider.${spec.key}`)" :input-id="`look-${spec.key}`"
          :min="spec.min" :max="spec.max" :step="1"
          :reset-value="lookAsShot[spec.key]" show-modified />
        <!-- DRO. Belongs here rather than in Tone: it is the camera's own curve
             for this frame, not a grade of ours, and it resets to what the body
             did like everything else in this panel. -->
        <template v-if="droAvailable">
          <div class="dro-modes" role="group" :aria-label="t('look.droModeLabel')">
            <button type="button" class="dro-mode" :class="{ on: droMode === 'off' }"
              :aria-pressed="droMode === 'off'" @click="setDroMode('off')">
              {{ t('look.droOff') }}
            </button>
            <button type="button" class="dro-mode" :class="{ on: droMode === 'auto' }"
              :aria-pressed="droMode === 'auto'" @click="setDroMode('auto')">
              {{ t('look.droAuto') }}
            </button>
            <button v-for="(lv, i) in droLevels" :key="lv" type="button" class="dro-mode"
              :class="{ on: droMode === 'level' && droLevel === lv }"
              :aria-pressed="droMode === 'level' && droLevel === lv"
              @click="setDroMode('level', lv)">
              {{ t('look.droLevel', { n: i + 1 }) }}
            </button>
          </div>
          <!-- Strength only qualifies Auto: a level already *is* the amount, and
               scaling one would land between two curves Sony never ships. -->
          <SliderRow v-if="droMode === 'auto'"
            :model-value="effectiveDro" @update:model-value="setDro($event)"
            :label="t('look.droStrength')" input-id="look-dro"
            :min="0" :max="DRO_MAX" :step="0.05"
            :reset-value="droAsShot" show-modified />
          <p class="control-note" v-if="droMode === 'auto'">{{ t('look.droAutoHint') }}</p>
          <p class="control-note" v-else-if="droMode === 'level'">{{ t('look.droLevelHint') }}</p>
        </template>
      </section>
      <section v-for="group in visibleGroups" :key="group.title" class="panel" v-show="!cropMode">
        <header class="panel-head">
          <span class="panel-title">
            {{ t(`panel.${group.title}`) }}
            <span v-if="groupEdited(group)" class="panel-dot" aria-hidden="true" />
          </span>
        </header>
        <SliderRow v-for="spec in group.items" :key="spec.key"
          v-model="recipe[spec.key]" :label="t(`slider.${spec.key}`)" :input-id="`s-${spec.key}`"
          :min="spec.min" :max="spec.max" :step="spec.step"
          :reset-value="SLIDER_DEFAULTS[spec.key]" :track="WB_TRACK[spec.key]" show-modified />
      </section>

      <section class="panel" v-if="activeSource && !cropMode && isRawSource">
        <header class="panel-head">
          <span>{{ t('panel.detail') }}</span>
          <span v-if="denoiseBusy" class="panel-hint">{{ t('detail.denoising') }}</span>
        </header>
        <div class="control-row">
          <label class="control-label" for="denoise-on">{{ t('detail.denoise') }}</label>
          <label class="switch">
            <input id="denoise-on" type="checkbox" v-model="denoise.enabled" />
            <span class="switch-track"><span class="switch-thumb" /></span>
          </label>
        </div>
        <SliderRow v-show="denoise.enabled" v-model="denoise.amount" style="margin-top: 6px;"
          :label="t('detail.amount')" input-id="denoise-amount" :min="0" :max="100" :reset-value="100" />
        <!-- 50 is neutral for both: the camera's own detail-restore value, and
             the tuned chroma threshold. Reset therefore goes to 50, not 100. -->
        <SliderRow v-show="denoise.enabled" v-model="denoise.chroma" style="margin-top: 6px;"
          :label="t('detail.chromaNr')" input-id="denoise-chroma" :min="0" :max="100" :reset-value="50" />
        <SliderRow v-show="denoise.enabled" v-model="denoise.edge" style="margin-top: 6px;"
          :label="t('detail.edgeNr')" input-id="denoise-edge" :min="0" :max="100" :reset-value="50" />
      </section>

      <section class="panel" v-if="activeSource && !cropMode">
        <header class="panel-head">
          <span class="panel-title">
            {{ t('panel.hsl') }}
            <span v-if="hslEdited" class="panel-dot" aria-hidden="true" />
          </span>
        </header>
        <div class="hsl-tabs">
          <button :class="{ active: hslTab === 'hue' }" @click="hslTab = 'hue'">H</button>
          <button :class="{ active: hslTab === 'sat' }" @click="hslTab = 'sat'">S</button>
          <button :class="{ active: hslTab === 'lum' }" @click="hslTab = 'lum'">L</button>
        </div>
        <SliderRow v-for="(range, i) in HSL_RANGES" :key="range.key"
          :model-value="hslValue(i)" @update:model-value="v => setHsl(i, v)"
          :label="t(`hsl.${range.key}`)" :dot-color="range.color" row-class="hsl-row" number-class="hsl-number"
          :min="-100" :max="100" show-modified />
      </section>

      <section class="panel" v-if="activeSource && !cropMode">
        <header class="panel-head">
          <span class="panel-title">
            {{ t('panel.grading') }}
            <span v-if="gradingEdited" class="panel-dot" aria-hidden="true" />
          </span>
        </header>
        <div v-for="g in GRADING_BANDS" :key="g.band" class="grading-group">
          <div class="grading-header">
            <span class="grading-dot" :style="{ background: gradingColor(g.band) }" />
            <span>{{ t(`grading.${g.band}`) }}</span>
          </div>
          <SliderRow v-model="grading[g.hueKey]" :label="t('grading.h')" row-class="grading-row" :min="-180" :max="180" />
          <SliderRow v-model="grading[g.satKey]" :label="t('grading.s')" row-class="grading-row" :min="0" :max="100" />
        </div>
        <SliderRow v-model="grading.blend" :label="t('grading.blend')" :min="0" :max="100" :reset-value="50" />
        <SliderRow v-model="grading.balance" :label="t('grading.balance')" :min="-100" :max="100" />
      </section>

      <section class="panel" v-if="activeSource && !cropMode">
        <header class="panel-head">
          <span>{{ t('panel.curve') }}</span>
          <button class="ghost" type="button" @click="resetCurve">{{ t('common.reset') }}</button>
        </header>
        <div class="curve-tabs">
          <button v-for="tab in CURVE_TABS" :key="tab" type="button"
            :class="['curve-tab', `curve-tab--${tab}`, { active: curveChannel === tab }]"
            @click="setCurveChannel(tab)">{{ t(`curve.${tab}`) }}</button>
        </div>
        <canvas ref="curveCanvas" class="curve-canvas"
          @mousedown="onCurveMouseDown"
          @mousemove="onCurveHover"
          @mouseleave="onCurveLeave"
          @dblclick="onCurveDoubleClick" />

        <!-- Parametric region + split sliders -->
        <div v-if="curveChannel === 'parametric'" class="curve-params">
          <SliderRow v-for="r in PARAM_REGIONS" :key="r"
            :model-value="paramValue(r)" @update:model-value="v => setParam(r, v)"
            :label="t(`curveRegion.${r}`)" :min="-100" :max="100" />
          <div class="curve-splits">
            <span class="curve-splits-label">{{ t('curve.splits') }}</span>
            <input type="range" min="4" max="96" step="1" :title="t('slider.hint')"
              :value="paramValue('shadowSplit')"
              :style="{ '--track': trackFill(paramValue('shadowSplit'), 0, 100) }"
              @input="setParam('shadowSplit', Math.min(($event.target as HTMLInputElement).valueAsNumber, paramValue('midtoneSplit') - 4))" />
            <input type="range" min="4" max="96" step="1" :title="t('slider.hint')"
              :value="paramValue('midtoneSplit')"
              :style="{ '--track': trackFill(paramValue('midtoneSplit'), 0, 100) }"
              @input="setParam('midtoneSplit', Math.min(Math.max(($event.target as HTMLInputElement).valueAsNumber, paramValue('shadowSplit') + 4), paramValue('highlightSplit') - 4))" />
            <input type="range" min="4" max="96" step="1" :title="t('slider.hint')"
              :value="paramValue('highlightSplit')"
              :style="{ '--track': trackFill(paramValue('highlightSplit'), 0, 100) }"
              @input="setParam('highlightSplit', Math.max(($event.target as HTMLInputElement).valueAsNumber, paramValue('midtoneSplit') + 4))" />
          </div>
        </div>

        <!-- Point-curve presets (applied to the RGB master channel) -->
        <div v-else class="curve-presets">
          <button v-for="name in presetNames" :key="name" type="button"
            class="curve-preset" @click="applyCurvePreset(name)">{{ t(`curvePreset.${name}`) }}</button>
        </div>
      </section>
    </aside>

    <Filmstrip v-if="sources.length" :sources="sources" :active-id="activeId"
      :rendering="status === 'rendering'" :thumb-src="thumbSrc"
      @select="selectSource" @remove="removeSource" @import="pickFiles" />

    <input ref="fileInput" type="file" :accept="IMPORT_ACCEPT" hidden multiple @change="onFileChange" />
    <transition name="fade"><div v-if="isDragging" class="drag-overlay">{{ t('drag.overlay') }}</div></transition>
    <transition name="fade">
      <div v-if="errorMessage && !activeSource?.invalid" class="error-toast" @click="errorMessage = null" :title="t('toast.dismiss')">
        {{ errorMessage }}
      </div>
    </transition>
  </div>
</template>
