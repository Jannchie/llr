<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { PipelineRenderer, type EditParams } from "./rendering/pipeline-renderer";

// ── types ──

type Source = { id: string; name: string; size: number; embeddedUrl: string; };
type RecipeKey = "exposure"|"contrast"|"highlights"|"shadows"|"whites"|"blacks"|"vibrance"|"saturation"|"temperature"|"tint";
type Recipe = Record<RecipeKey, number>;
type SliderSpec = { key: RecipeKey; label: string; min: number; max: number; step: number };
type SliderGroup = { title: string; items: SliderSpec[] };

const API = (import.meta.env.VITE_API_URL as string | undefined) ?? "/api";

const defaultRecipe = (): Recipe => ({
  exposure: 0, contrast: 0, highlights: 0, shadows: 0,
  whites: 0, blacks: 0, vibrance: 0, saturation: 0,
  temperature: 6500, tint: 0,
});

const groups: SliderGroup[] = [
  { title: "Light", items: [
    { key: "exposure", label: "Exposure", min: -5, max: 5, step: 0.1 },
    { key: "contrast", label: "Contrast", min: -100, max: 100, step: 1 },
    { key: "highlights", label: "Highlights", min: -100, max: 100, step: 1 },
    { key: "shadows", label: "Shadows", min: -100, max: 100, step: 1 },
    { key: "whites", label: "Whites", min: -100, max: 100, step: 1 },
    { key: "blacks", label: "Blacks", min: -100, max: 100, step: 1 },
  ]},
  { title: "Color", items: [
    { key: "temperature", label: "Temp", min: 2000, max: 12000, step: 50 },
    { key: "tint", label: "Tint", min: -100, max: 100, step: 1 },
    { key: "vibrance", label: "Vibrance", min: -100, max: 100, step: 1 },
    { key: "saturation", label: "Saturation", min: -100, max: 100, step: 1 },
  ]},
];

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

let webglRenderer: PipelineRenderer | null = null;
let rafId = 0;
let drawPending = false;

const activeSource = computed(() => sources.value.find(s => s.id === activeId.value) ?? null);

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
    temperature: recipe.temperature,
    tint: recipe.tint,
  };
}

function scheduleWebGLDraw(): void {
  if (drawPending) return;
  drawPending = true;
  rafId = requestAnimationFrame(() => { drawPending = false; drawWebGL(); });
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

// ── upload ──

watch(recipe, () => scheduleWebGLDraw(), { deep: true });

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
    const linMeta = await linRes.json() as { width: number; height: number; linearUrl: string };
    const binRes = await fetch(linMeta.linearUrl);
    if (!binRes.ok) throw new Error("Failed to fetch");
    const linearFloat = new Float32Array(await binRes.arrayBuffer());
    if (canvasRef.value && webglRenderer) {
      webglRenderer.uploadImage(linearFloat, linMeta.width, linMeta.height);
      drawWebGL();
    }
  } catch (err) {
    console.warn("DCP reload failed:", err);
  }
  status.value = "idle";
});

onBeforeUnmount(() => destroyWebGL());

// Auto-load sample file on startup
onMounted(async () => {
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
      const linMeta = await linRes.json() as { width: number; height: number; linearUrl: string };

      // Fetch binary float32
      const binRes = await fetch(linMeta.linearUrl);
      if (!binRes.ok) throw new Error("Failed to fetch linear data");
      const buf = await binRes.arrayBuffer();
      const linearFloat = new Float32Array(buf);
      const width = linMeta.width;
      const height = linMeta.height;
      console.log("[upload] bin:", buf.byteLength, "B, float32:", linearFloat.length, "expect:", width*height*3);
      timing.value = Math.round(performance.now() - t0);

      await nextTick();
      if (canvasRef.value) {
        destroyWebGL();
        try {
          webglRenderer = new PipelineRenderer(canvasRef.value);
          webglRenderer.uploadImage(linearFloat, width, height);
          drawWebGL();
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

function resetRecipe(): void { Object.assign(recipe, defaultRecipe()); }
function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n/1024).toFixed(0)} KB`;
  return `${(n/1048576).toFixed(1)} MB`;
}
function isAbsoluteUrl(u: string): boolean {
  return u.startsWith("blob:") || u.startsWith("data:") || u.startsWith("http");
}
</script>

<template>
  <div class="app" :class="{ 'is-drag': isDragging }"
    @dragover.prevent="isDragging = true"
    @dragleave.prevent="isDragging = false"
    @drop="onDrop">
    <header class="topbar">
      <div class="brand">LLR</div>
      <div class="meta-summary">
        <span v-if="activeSource">{{ activeSource.name }}</span>
      </div>
    </header>

    <main class="center">
      <div class="viewport">
        <div class="dropzone" v-show="!activeSource" @click="pickFiles">
          <div class="dropzone-inner">
            <div class="dropzone-rule" />
            <p class="dropzone-title">Drop a RAW file or click to import</p>
            <p class="dropzone-hint">.ARW · .DNG · .CR3 · .NEF · .RAF · .RW2 · .ORF</p>
            <div class="dropzone-rule" />
          </div>
        </div>
        <div v-show="activeSource && status === 'rendering' && !webglRenderer" class="preview-loading">
          <span>Decoding… {{ timing ? `${timing}ms` : '' }}</span>
        </div>
        <canvas v-show="webglRenderer != null" ref="canvasRef" class="preview" />
        <img v-show="activeSource && !webglRenderer && status !== 'rendering'" class="preview" :src="activeSource?.embeddedUrl ? (isAbsoluteUrl(activeSource.embeddedUrl) ? activeSource.embeddedUrl : `${API}${activeSource.embeddedUrl}`) : ''" alt="preview" />
      </div>

      <footer class="status">
        <div class="status-cell">
          <span class="status-label">Status</span>
          <span class="status-value" :data-state="status">{{ timing ? `Decoded in ${timing}ms` : status }}</span>
        </div>
        <div class="status-cell">
          <span class="status-label">Pipeline</span>
          <span class="status-value">backend(rawpy) + WebGL</span>
        </div>
      </footer>
    </main>

    <aside class="right">
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
      </section>
      <section v-for="group in groups" :key="group.title" class="panel">
        <header class="panel-head">{{ group.title }}</header>
        <div v-for="spec in group.items" :key="spec.key" class="slider">
          <label :for="`s-${spec.key}`">{{ spec.label }}</label>
          <input :id="`s-${spec.key}`" v-model.number="recipe[spec.key]" type="range" :min="spec.min" :max="spec.max" :step="spec.step" />
          <input v-model.number="recipe[spec.key]" class="slider-number" type="number" :min="spec.min" :max="spec.max" :step="spec.step" :aria-label="spec.label" />

        </div>
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
