<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { PipelineRenderer, type EditParams } from "./rendering/pipeline-renderer";
import { renderHistogram } from "./rendering/histogram";
import { curveToLUT, defaultCurve, renderCurve, hitTest, type CurvePoint } from "./rendering/curve";

// ── types ──

type Source = { id: string; name: string; size: number; embeddedUrl: string; };
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
const exporting = ref(false);

let webglRenderer: PipelineRenderer | null = null;
const p3Supported = ref(false);
let rafId = 0;
let drawPending = false;

// ── Pan / Zoom state ──
const zoom = ref(0); // 0 = no image, 1 = fit to viewport
const pan = reactive({ x: 0, y: 0 });
const fitScale = ref(1);
const imageW = ref(0);
const imageH = ref(0);
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

// ── Tone Curve ──

const curvePoints = ref<CurvePoint[]>(defaultCurve());
const curveActive = ref(-1);
const curveCanvas = ref<HTMLCanvasElement | null>(null);

function applyCurveLUT(): void {
  const lut = curveToLUT(curvePoints.value);
  if (webglRenderer) webglRenderer.uploadCurveLUT(lut);
  scheduleWebGLDraw();
}

function resetCurve(): void {
  curvePoints.value = defaultCurve();
  curveActive.value = -1;
  renderCurveCanvas();
  const lut = curveToLUT(curvePoints.value);
  if (webglRenderer) webglRenderer.uploadCurveLUT(lut);
  drawWebGL(); // immediate, no RAF-batching for reset
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
  renderCurve(ctx, w, h, curvePoints.value, curveActive.value);
}

function onCurveMouseDown(e: MouseEvent): void {
  const cvs = curveCanvas.value;
  if (!cvs) return;
  const rect = cvs.getBoundingClientRect();
  const mx = e.clientX - rect.left;
  const my = e.clientY - rect.top;
  const dpr = window.devicePixelRatio || 1;
  const w = rect.width / dpr;
  const h = rect.height / dpr;
  const idx = hitTest(curvePoints.value, w, h, mx, my);
  if (idx >= 0) {
    curveActive.value = idx;
  } else {
    // Add new point
    const x = clamp(mx / w, 0, 1);
    const y = clamp(1 - my / h, 0, 1);
    const pts = [...curvePoints.value, { x, y }];
    pts.sort((a, b) => a.x - b.x);
    curvePoints.value = pts;
    curveActive.value = pts.findIndex(p => p.x === x && p.y === y);
    applyCurveLUT();
  }
  renderCurveCanvas();
  window.addEventListener("mousemove", onCurveMouseMove);
  window.addEventListener("mouseup", onCurveMouseUp);
}

function onCurveMouseMove(e: MouseEvent): void {
  if (curveActive.value < 0) return;
  const cvs = curveCanvas.value;
  if (!cvs) return;
  const rect = cvs.getBoundingClientRect();
  const mx = e.clientX - rect.left;
  const my = e.clientY - rect.top;
  const dpr2 = window.devicePixelRatio || 1;
  const cw = rect.width / dpr2;
  const ch = rect.height / dpr2;
  const pts = [...curvePoints.value];
  pts[curveActive.value] = {
    x: clamp(mx / cw, 0, 1),
    y: clamp(1 - my / ch, 0, 1),
  };
  pts.sort((a, b) => a.x - b.x);
  curvePoints.value = pts;
  curveActive.value = pts.findIndex(
    p => p.x === clamp(mx / cw, 0, 1) && p.y === clamp(1 - my / ch, 0, 1)
  );
  applyCurveLUT();
  renderCurveCanvas();
}

function onCurveMouseUp(): void {
  window.removeEventListener("mousemove", onCurveMouseMove);
  window.removeEventListener("mouseup", onCurveMouseUp);
}

function onCurveDoubleClick(e: MouseEvent): void {
  const cvs = curveCanvas.value;
  if (!cvs) return;
  const rect = cvs.getBoundingClientRect();
  const mx = e.clientX - rect.left;
  const my = e.clientY - rect.top;
  const dpr = window.devicePixelRatio || 1;
  const idx = hitTest(curvePoints.value, rect.width / dpr, rect.height / dpr, mx, my);
  if (idx > 0 && idx < curvePoints.value.length - 1) {
    // Delete point (keep endpoints)
    curvePoints.value = curvePoints.value.filter((_, i) => i !== idx);
    curveActive.value = -1;
    applyCurveLUT();
    renderCurveCanvas();
  }
}

// Init curve canvas
watch(curveCanvas, (cvs) => {
  if (cvs) {
    // Observe resize to redraw
    const obs = new ResizeObserver(() => renderCurveCanvas());
    obs.observe(cvs);
  }
});

// ── History (undo / redo) ──

type Snapshot = {
  recipe: Recipe;
  hslHue: number[]; hslSat: number[]; hslLum: number[];
  grading: typeof grading;
  curve: CurvePoint[];
  dcp: string;
};

const MAX_HISTORY = 100;
const HISTORY_DEBOUNCE = 300;

const history = ref<Snapshot[]>([]);
const historyIndex = ref(-1);
const pendingDirty = ref(false);
let isRestoring = false;
let historyTimer = 0;

const canUndo = computed(() => historyIndex.value > 0 || pendingDirty.value);
const canRedo = computed(() => historyIndex.value < history.value.length - 1);

function captureSnapshot(): Snapshot {
  return {
    recipe: { ...recipe },
    hslHue: [...hslHue], hslSat: [...hslSat], hslLum: [...hslLum],
    grading: { ...grading },
    curve: curvePoints.value.map(p => ({ ...p })),
    dcp: dcpCode.value,
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

function applySnapshot(s: Snapshot): void {
  isRestoring = true;
  Object.assign(recipe, s.recipe);
  for (let i = 0; i < 8; i++) { hslHue[i] = s.hslHue[i]; hslSat[i] = s.hslSat[i]; hslLum[i] = s.hslLum[i]; }
  Object.assign(grading, s.grading);
  curvePoints.value = s.curve.map(p => ({ ...p }));
  curveActive.value = -1;
  dcpCode.value = s.dcp; // triggers re-decode if the DCP style differs
  if (webglRenderer) webglRenderer.uploadCurveLUT(curveToLUT(curvePoints.value));
  renderCurveCanvas();
  scheduleWebGLDraw();
  nextTick(() => { isRestoring = false; });
}

function undo(): void {
  flushPendingHistory();
  if (historyIndex.value <= 0) return;
  historyIndex.value--;
  applySnapshot(history.value[historyIndex.value]);
}

function redo(): void {
  flushPendingHistory();
  if (historyIndex.value >= history.value.length - 1) return;
  historyIndex.value++;
  applySnapshot(history.value[historyIndex.value]);
}

function initHistory(): void {
  history.value = [captureSnapshot()];
  historyIndex.value = 0;
  pendingDirty.value = false;
}
initHistory();

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

const zoomPercent = computed(() => {
  if (!imageW.value || !imageH.value) return 0;
  return Math.round(fitScale.value * zoom.value * 100);
});

// ── WebGL ──

function buildPipelineParams(): Partial<EditParams> {
  return {
    exposure: recipe.exposure,
    contrast: 1 + recipe.contrast / 100,
    saturation: 1 + recipe.saturation / 100,
    highlights: recipe.highlights / 100,
    shadows: recipe.shadows / 100,
    whites: recipe.whites / 100,
    blacks: recipe.blacks / 100,
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
function setProfileCurve(cp: ColorProfileMeta | undefined): void {
  profileCurveLUT = buildProfileLUT(cp);
  if (webglRenderer) webglRenderer.uploadProfileCurveLUT(profileCurveLUT);
}

// ── Histogram ──

let linearFloatData: Float32Array | null = null;
const histoCanvasRef = ref<HTMLCanvasElement | null>(null);

function updateHistogram(): void {
  const canvas = histoCanvasRef.value;
  if (!canvas || !linearFloatData || !imageW.value || !imageH.value) return;
  const rect = canvas.getBoundingClientRect();
  let w = rect.width;
  let h = rect.height;
  // If canvas not laid out, try parent dimensions; retry next frame as last resort
  if (w <= 0 || h <= 0) {
    const parent = canvas.parentElement;
    if (parent) { w = parent.clientWidth - 32; h = 80; }
    if (w <= 0) { requestAnimationFrame(() => updateHistogram()); return; }
  }
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  if (!webglRenderer) return;
  const bins = webglRenderer.readHistogram();
  renderHistogram(ctx, w, h, bins);
}

function scheduleHistogram(): void {
  updateHistogram();
  // Safety net: always retry next frame in case layout hadn't settled
  requestAnimationFrame(() => updateHistogram());
}

function scheduleWebGLDraw(): void {
  if (drawPending) return;
  drawPending = true;
  rafId = requestAnimationFrame(() => { drawPending = false; drawWebGL(); scheduleHistogram(); });
}

function drawWebGL(): void {
  if (!webglRenderer) return;
  webglRenderer.draw(buildPipelineParams());
}

function destroyWebGL(): void {
  if (rafId) { cancelAnimationFrame(rafId); rafId = 0; }
  drawPending = false;
  if (webglRenderer) { webglRenderer.destroy(); webglRenderer = null; }
}

// ── Pan / Zoom ──

function startPan(e: MouseEvent): void {
  if (e.button !== 0) return;
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
  if (!imageW.value || !imageH.value || !viewportRef.value) return;
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

function onDoubleClick(e: MouseEvent): void {
  if (!imageW.value || !imageH.value || !viewportRef.value) return;
  if (zoom.value === 1) {
    const newZoom = Math.min(50, 1 / fitScale.value);
    const rect = viewportRef.value.getBoundingClientRect();
    applyZoom(newZoom, e.clientX - rect.left, e.clientY - rect.top);
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

  if (!imageW.value || !imageH.value) return;
  if (e.ctrlKey || e.metaKey) {
    switch (e.key) {
      case '0': e.preventDefault(); fitView(); break;
      case '=': case '+': e.preventDefault(); zoomIn(); break;
      case '-': e.preventDefault(); zoomOut(); break;
    }
  }
}

// ── upload ──

watch(recipe, () => scheduleWebGLDraw(), { deep: true });
watch([hslHue, hslSat, hslLum], () => scheduleWebGLDraw(), { deep: true });
watch(grading, () => scheduleWebGLDraw(), { deep: true });
watch(viewSettings, () => scheduleWebGLDraw(), { deep: true });

// Record edit changes into undo/redo history (coalesced; suppressed during restore)
watch([recipe, hslHue, hslSat, hslLum, grading, curvePoints, dcpCode],
  () => scheduleHistoryCommit(), { deep: true });

// Re-decode when DCP code changes
let currentSourceId = "";
watch(dcpCode, async (newCode) => {
  if (!currentSourceId) { console.log("[dcp] skip, no source"); return; }
  console.log("[dcp] switching to:", newCode);
  status.value = "rendering";
  try {
    const linRes = await fetch(`${API}/render-linear`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ sourceId: currentSourceId, halfSize: true, maxSize: 1600, dcpCode: dcpCode.value }),
    });
    if (!linRes.ok) throw new Error(await linRes.text());
    const linMeta = await linRes.json() as { width: number; height: number; linearUrl: string; colorProfile?: ColorProfileMeta };
    const binRes = await fetch(linMeta.linearUrl);
    if (!binRes.ok) throw new Error("Failed to fetch");
    const linearFloat = new Float32Array(await binRes.arrayBuffer());
    // Update histogram data with new DCP rendering
    linearFloatData = linearFloat;
    imageW.value = linMeta.width;
    imageH.value = linMeta.height;
    if (viewportRef.value) {
      fitScale.value = Math.min(viewportRef.value.clientWidth / linMeta.width, viewportRef.value.clientHeight / linMeta.height);
    }
    if (canvasRef.value && webglRenderer) {
      webglRenderer.uploadImage(linearFloat, linMeta.width, linMeta.height);
      setProfileCurve(linMeta.colorProfile); // new DCP -> new tone curve
      drawWebGL();
      scheduleHistogram();
    }
  } catch (err) {
    console.warn("DCP reload failed:", err);
  }
  status.value = "idle";
});

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeyDown);
  resizeObs?.disconnect();
  destroyWebGL();
});

// Auto-load sample file on startup
onMounted(async () => {
  window.addEventListener('keydown', onKeyDown);
  resizeObs = new ResizeObserver(() => {
    if (imageW.value && imageH.value && viewportRef.value) {
      fitScale.value = Math.min(viewportRef.value.clientWidth / imageW.value, viewportRef.value.clientHeight / imageH.value);
    }
  });
  if (viewportRef.value) resizeObs.observe(viewportRef.value);

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

async function uploadFiles(files: File[]): Promise<void> {
  if (!files.length) return;
  status.value = "uploading";
  errorMessage.value = null;
  destroyWebGL();

  for (const file of files) {
    const t0 = performance.now();
    const formData = new FormData();
    formData.append("file", file);
    try {
      // Upload
      const res = await fetch(`${API}/sources`, { method: "POST", body: formData });
      if (!res.ok) throw new Error(await res.text());
      const source = await res.json() as Source;
      currentSourceId = source.id;
      sources.value = [...sources.value, source];
      activeId.value = source.id;
      status.value = "rendering";
      await nextTick();

      // Decode + render linear
      const linRes = await fetch(`${API}/render-linear`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ sourceId: source.id, halfSize: true, maxSize: 1600, dcpCode: dcpCode.value }),
      });
      if (!linRes.ok) throw new Error(await linRes.text());
      const linMeta = await linRes.json() as { width: number; height: number; linearUrl: string; colorProfile?: ColorProfileMeta };

      // Fetch binary float32
      const binRes = await fetch(linMeta.linearUrl);
      if (!binRes.ok) throw new Error("Failed to fetch linear data");
      const buf = await binRes.arrayBuffer();
      const linearFloat = new Float32Array(buf);
      const width = linMeta.width;
      const height = linMeta.height;
      console.log("[upload] bin:", buf.byteLength, "B, float32:", linearFloat.length, "expect:", width*height*3);
      timing.value = Math.round(performance.now() - t0);
      linearFloatData = linearFloat;
      profileCurveLUT = buildProfileLUT(linMeta.colorProfile);

      // Store image dimensions for pan/zoom
      imageW.value = width;
      imageH.value = height;
      if (viewportRef.value) {
        fitScale.value = Math.min(viewportRef.value.clientWidth / width, viewportRef.value.clientHeight / height);
      }
      zoom.value = 1;
      pan.x = 0;
      pan.y = 0;

      await nextTick();
      if (canvasRef.value) {
        destroyWebGL();
        try {
          webglRenderer = new PipelineRenderer(canvasRef.value);
          p3Supported.value = webglRenderer.p3Supported;
          webglRenderer.uploadImage(linearFloat, width, height);
          webglRenderer.uploadCurveLUT(curveToLUT(curvePoints.value));
          webglRenderer.uploadProfileCurveLUT(profileCurveLUT);
          drawWebGL();
          scheduleHistogram();
        } catch (err) {
          console.error("[pipeline] init failed:", err);
          status.value = "error";
          errorMessage.value = err instanceof Error ? err.message : String(err);
          return;
        }
      }
    } catch (err) {
      status.value = "error";
      errorMessage.value = err instanceof Error ? err.message : String(err);
      return;
    }
  }
  status.value = "idle";
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
      body: JSON.stringify({ sourceId: currentSourceId, halfSize: false, maxSize: 0, dcpCode: dcpCode.value }),
    });
    if (!linRes.ok) throw new Error(await linRes.text());
    const linMeta = await linRes.json() as { width: number; height: number; linearUrl: string; colorProfile?: ColorProfileMeta };
    const binRes = await fetch(linMeta.linearUrl);
    if (!binRes.ok) throw new Error("Failed to fetch full-resolution data");
    const linear = new Float32Array(await binRes.arrayBuffer());

    // 2. Render full-res off-screen with the current edit params, read back as JPEG
    renderer = new PipelineRenderer(document.createElement("canvas"));
    renderer.uploadImage(linear, linMeta.width, linMeta.height);
    renderer.uploadCurveLUT(curveToLUT(curvePoints.value));
    renderer.uploadProfileCurveLUT(buildProfileLUT(linMeta.colorProfile));
    renderer.draw(buildPipelineParams());
    const blob = await renderer.toBlob("image/jpeg", 0.92);

    // 3. Embed edit settings (LLR JSON + Adobe crs) into the JPEG server-side
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
</script>

<template>
  <div class="app" :class="{ 'is-drag': isDragging }"
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
      </div>
      <div class="meta-summary">
        <span v-if="activeSource">{{ activeSource.name }}</span>
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
          <span>Decoding… {{ timing ? `${timing}ms` : '' }}</span>
        </div>
        <canvas v-show="webglRenderer != null" ref="canvasRef" class="preview" :style="{ transform: displayTransform }" />
        <img v-show="activeSource && !webglRenderer && status !== 'rendering'" class="preview" :style="{ transform: displayTransform }" :src="activeSource?.embeddedUrl ? (isAbsoluteUrl(activeSource.embeddedUrl) ? activeSource.embeddedUrl : `${API}${activeSource.embeddedUrl}`) : ''" alt="preview" />
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

    <aside class="right">
      <div class="histogram-wrap" v-show="activeSource">
        <canvas ref="histoCanvasRef" class="histogram" />
      </div>
      <section class="panel">
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
      <section v-for="group in groups" :key="group.title" class="panel">
        <header class="panel-head">{{ group.title }}</header>
        <div v-for="spec in group.items" :key="spec.key" class="slider">
          <label :for="`s-${spec.key}`">{{ spec.label }}</label>
          <input :id="`s-${spec.key}`" v-model.number="recipe[spec.key]" type="range"
            :min="spec.min" :max="spec.max" :step="spec.step"
            :style="{ '--track': trackFill(recipe[spec.key], spec.min, spec.max) }"
            @dblclick="recipe[spec.key] = SLIDER_DEFAULTS[spec.key]" title="Double-click to reset" />
          <input v-model.number="recipe[spec.key]" class="slider-number" type="number"
            :min="spec.min" :max="spec.max" :step="spec.step" :aria-label="spec.label" />
        </div>
      </section>

      <section class="panel" v-if="activeSource">
        <header class="panel-head">
          <span>HSL / Color</span>
        </header>
        <div class="hsl-tabs">
          <button :class="{ active: hslTab === 'hue' }" @click="hslTab = 'hue'">H</button>
          <button :class="{ active: hslTab === 'sat' }" @click="hslTab = 'sat'">S</button>
          <button :class="{ active: hslTab === 'lum' }" @click="hslTab = 'lum'">L</button>
        </div>
        <div v-for="(range, i) in HSL_RANGES" :key="range.name" class="hsl-row">
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

      <section class="panel" v-if="activeSource">
        <header class="panel-head">
          <span>Color Grading</span>
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

      <section class="panel" v-if="activeSource">
        <header class="panel-head">
          <span>Tone Curve</span>
          <button class="ghost" type="button" @click="resetCurve">Reset</button>
        </header>
        <canvas ref="curveCanvas" class="curve-canvas"
          @mousedown="onCurveMouseDown"
          @dblclick="onCurveDoubleClick" />
      </section>
    </aside>

    <footer class="filmstrip">
      <button class="filmstrip-import" type="button" @click="pickFiles">＋ Import</button>
      <div class="filmstrip-track">
        <button v-for="source in sources" :key="source.id" type="button"
          class="film-cell" :class="{ 'is-active': source.id === activeId }"
          @click="activeId = source.id; scheduleWebGLDraw()">
          <img v-if="source.embeddedUrl" :src="isAbsoluteUrl(source.embeddedUrl) ? source.embeddedUrl : `${API}${source.embeddedUrl}`" :alt="source.name" />
          <span class="film-name">{{ source.name }}</span>
          <span class="film-size">{{ formatBytes(source.size) }}</span>
        </button>
      </div>
    </footer>

    <input ref="fileInput" type="file" accept=".arw,.dng,.cr2,.cr3,.nef,.raf,.rw2,.orf,.tif,.tiff,.jpg,.jpeg,.png" hidden multiple @change="onFileChange" />
    <transition name="fade"><div v-if="isDragging" class="drag-overlay">Drop to import</div></transition>
  </div>
</template>
