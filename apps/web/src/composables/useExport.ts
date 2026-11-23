import { ref, type Ref } from "vue";
import { PipelineRenderer, parseDcpTables, type EditParams, type ProfileCurve } from "../rendering/pipeline-renderer";
import { API, fetchLinear, type DenoisePayload, type LinearMeta, type LinearRequest } from "../api";
import { t } from "../i18n";
import type { RelightGeometry } from "../rendering/relight";

/**
 * Everything the export mechanics need, frozen at click time. The full-res
 * decode takes seconds and the filmstrip stays clickable, so every value is
 * captured up front — deriving it all from one snapshot also guarantees the
 * rendered pixels and the embedded XMP cannot drift apart.
 */
export interface ExportPlan {
  sourceId: string;
  filename: string;
  /** The full edit snapshot, embedded server-side as llr:* XMP + JSON. */
  settings: unknown;
  /** Exclude GPS/serials/owner/maker notes from the copied EXIF (opt-in). */
  stripPrivate: boolean;
  dcpCode: string | undefined;
  /** Which colour engine renders camera RGB — must match the preview's. */
  profileId: string;
  /** Exactly what `denoisePayload` returns — see DenoisePayload for why the
   * shape is declared once rather than repeated here. */
  denoise: DenoisePayload;
  /** Generative erase strokes, as render-linear takes them (rendering/erase.ts
   * eraseWire). The worker fills them into the decode, so the export has to ask
   * for the same ones the preview showed. */
  erase: { points: [number, number][]; radius: number }[];
  /** Relight geometry for the rig in `params` (resolution-free, so the
   * preview's serves the export); null when the edit adds no light. */
  relightGeometry: RelightGeometry | null;
  /** Anything else the renderer needs per image before it draws (mask layers:
   * AI mattes, rasterised brushes) — called once the full-res frame is up. */
  prepare?: (renderer: PipelineRenderer, params: Partial<EditParams>) => void;
  /** The Creative Look, its tweaks and DRO (strength and level) the decode is
   * asked for — the same four the preview's request carried (Sony path only;
   * undefined renders what the body did). */
  lookRequest: Pick<LinearRequest, "look" | "style" | "dro" | "droLevel">;
  /** Sony's "advanced colour reproduction" (ZcTask3DLut). Render-time, so it
   * rides the plan rather than the decode — but the export builds its own
   * renderer, so it has to be handed over explicitly or the exported file would
   * silently disagree with the preview. */
  sonyAdvancedColour: boolean;
  /** Camera match, the post chain's last stage. Render-time and per-image like
   * the switch above, and handed over for the same reason. */
  sonyCameraMatch: boolean;
  /** The render params, or how to make them from the decode (a photo that is
   * not open: its lens tables and size are only known once it is decoded). */
  params: Partial<EditParams> | ((meta: LinearMeta) => Partial<EditParams>);
  /** Decode size; absent = full resolution (an export). */
  decode?: { halfSize: boolean; maxSize: number };
  curveLUT: Float32Array;
  profileLUT: (meta: LinearMeta) => ProfileCurve;
  /** Crop-aware output dims + source transform for the full-res frame. */
  output: (meta: LinearMeta) => { width: number; height: number; texXform: Float32Array };
  background: [number, number, number];
}

/**
 * Decode `plan`'s photo and render its frozen edit off-screen: the whole of an
 * export but the download, and — with `maxEdge` — a small rendering of a photo
 * that is not open (the edited previews a paste onto a selection refreshes).
 */
export async function renderPlan(plan: ExportPlan, opts: { maxEdge?: number; quality?: number } = {}): Promise<Blob> {
  let renderer: PipelineRenderer | null = null;
  try {
    // Full resolution for an export (no half-size, no cap); purpose keeps
    // this one-off decode out of the worker's caches.
    const lin = await fetchLinear({
      sourceId: plan.sourceId, halfSize: plan.decode?.halfSize ?? false, maxSize: plan.decode?.maxSize ?? 0, purpose: "export",
      profileId: plan.profileId, dcpCode: plan.dcpCode, denoise: plan.denoise,
      ...plan.lookRequest, erase: plan.erase,
    });
    if (!lin) throw new Error(t("error.exportSourceGone"));
    const { meta, pixels } = lin;
    const params = typeof plan.params === "function" ? plan.params(meta) : plan.params;

    renderer = new PipelineRenderer(document.createElement("canvas"));
    renderer.uploadImage(pixels, meta.width, meta.height);
    renderer.uploadCurveLUT(plan.curveLUT);
    renderer.uploadProfileCurveLUT(plan.profileLUT(meta));
    // The DCP HueSatMaps are the shader's job now, so this off-screen renderer
    // needs them as much as the preview does — without them the export would
    // come out unprofiled while the preview looked right.
    renderer.uploadDcpTables(parseDcpTables(meta.colorProfile));
    // Awaited, unlike the preview's fire-and-redraw: this renderer draws once
    // and is thrown away, so the table has to be resident before it does.
    await renderer.setSonyAdvancedColour(plan.sonyAdvancedColour);
    renderer.setSonyCameraMatch(plan.sonyCameraMatch);
    renderer.uploadRelightGeometry(plan.relightGeometry);
    plan.prepare?.(renderer, params);
    const out = plan.output(meta);
    const f = opts.maxEdge ? Math.min(1, opts.maxEdge / Math.max(out.width, out.height)) : 1;
    renderer.setOutput(Math.max(1, Math.round(out.width * f)), Math.max(1, Math.round(out.height * f)), out.texXform, plan.background);
    renderer.draw(params);
    // quality 1.0 also disables the browser encoder's 4:2:0 chroma subsampling
    return await renderer.toBlob("image/jpeg", opts.quality ?? 1.0);
  } finally {
    renderer?.destroy();
  }
}

/**
 * The export flow: decode full-resolution linear data, re-render it in an
 * off-screen WebGL pipeline with the frozen edit, embed the recipe as XMP
 * server-side, and download the result. How the edit snapshot maps to render
 * params stays with the caller (the `plan` callback).
 */
/** One photo of a batch: its name for the report, and how to freeze its edit when its turn comes. */
export interface ExportJob {
  name: string;
  plan: () => Promise<ExportPlan | null> | ExportPlan | null;
}

/** How a batch ended: what was written and where, what was not, and whether it was stopped. */
export interface BatchResult {
  saved: number;
  /** The folder the files went to; null when they were downloaded one by one. */
  folder: string | null;
  failed: string[];
  cancelled: boolean;
}

export function useExport(opts: {
  errorMessage: Ref<string | null>;
  /** Freeze the current edit into a plan; null = nothing to export. */
  plan: () => ExportPlan | null;
}) {
  const exporting = ref(false);
  // A batch in flight: how far it is. Null for a single export and when idle.
  const progress = ref<{ done: number; total: number } | null>(null);
  let stop = false;

  async function exportImage(): Promise<void> {
    if (exporting.value) return;
    const plan = opts.plan();
    if (!plan) return;
    exporting.value = true;
    opts.errorMessage.value = null;
    try {
      // Download the finished file
      downloadBlob(await finishedJpeg(plan), plan.filename);
    } catch (err) {
      // The toast only: `exporting` is this export's state, and the decode
      // state belongs to the photo on screen, not to this off-screen render.
      opts.errorMessage.value = err instanceof Error ? err.message : String(err);
      console.error("[export] failed:", err);
    } finally {
      exporting.value = false;
    }
  }

  /**
   * Export several photos one after another — each is a full-resolution decode
   * and render, so two at once would only fight over the worker and the GPU.
   * One that fails is reported and the rest carry on. Null = the folder picker
   * was dismissed, so nothing was started.
   */
  async function exportMany(jobs: readonly ExportJob[]): Promise<BatchResult | null> {
    if (exporting.value || !jobs.length) return null;
    // Before anything else: the picker needs the click that got us here.
    const sink = await openSink();
    if (!sink) return null;
    exporting.value = true;
    stop = false;
    opts.errorMessage.value = null;
    progress.value = { done: 0, total: jobs.length };
    const result: BatchResult = { saved: 0, folder: sink.folder, failed: [], cancelled: false };
    try {
      for (const job of jobs) {
        if (stop) { result.cancelled = true; break; }
        try {
          const plan = await job.plan();
          if (!plan) throw new Error(t("error.exportSourceGone"));
          await sink.write(await finishedJpeg(plan), plan.filename);
          result.saved += 1;
        } catch (err) {
          result.failed.push(job.name);
          console.error(`[export] ${job.name} failed:`, err);
        }
        progress.value = { done: progress.value.done + 1, total: jobs.length };
      }
    } finally {
      exporting.value = false;
      progress.value = null;
    }
    return result;
  }

  /** Stop a batch once the photo it is on is done. */
  function cancelExport(): void {
    stop = true;
  }

  return { exporting, progress, exportImage, exportMany, cancelExport };
}

/** Render `plan` at full resolution and have the server embed its edit (llr:* XMP + lossless LLR JSON) and the RAW's EXIF. */
async function finishedJpeg(plan: ExportPlan): Promise<Blob> {
  const blob = await renderPlan(plan);
  const fd = new FormData();
  fd.append("file", blob, "export.jpg");
  fd.append("meta", JSON.stringify({ sourceId: plan.sourceId, settings: plan.settings, stripPrivate: plan.stripPrivate }));
  const res = await fetch(`${API}/export`, { method: "POST", body: fd });
  if (!res.ok) throw new Error(await res.text());
  return res.blob();
}

/** `name`, or `name-2`, `name-3`… — the first that `taken` does not hold. */
export async function freeName(name: string, taken: (name: string) => boolean | Promise<boolean>): Promise<string> {
  const dot = name.lastIndexOf(".");
  const [stem, ext] = dot > 0 ? [name.slice(0, dot), name.slice(dot)] : [name, ""];
  let candidate = name;
  for (let n = 2; await taken(candidate); n += 1) candidate = `${stem}-${n}${ext}`;
  return candidate;
}

type Sink = { folder: string | null; write: (blob: Blob, filename: string) => Promise<void> };
type DirectoryPicker = (options?: { id?: string; mode?: "read" | "readwrite"; startIn?: string }) => Promise<FileSystemDirectoryHandle>;

/**
 * Where a batch goes: a folder the user picks, where the browser can write to
 * one (Chromium), else the downloads folder a file at a time. Either way a
 * name that is taken gets a number rather than overwriting what is there —
 * two photos of a selection can share a name, and so can a file from last time.
 */
async function openSink(): Promise<Sink | null> {
  const picker = (window as { showDirectoryPicker?: DirectoryPicker }).showDirectoryPicker;
  if (!picker) {
    const used = new Set<string>();
    return {
      folder: null,
      write: async (blob, filename) => {
        const name = await freeName(filename, n => used.has(n));
        used.add(name);
        downloadBlob(blob, name);
        // A burst of downloads gets dropped by the browser; give each its moment.
        await new Promise(resolve => setTimeout(resolve, 300));
      },
    };
  }
  let dir: FileSystemDirectoryHandle;
  try {
    dir = await picker.call(window, { id: "llr-export", mode: "readwrite", startIn: "pictures" });
  } catch (err) {
    if ((err as DOMException).name === "AbortError") return null;
    throw err;
  }
  const exists = (name: string) => dir.getFileHandle(name).then(() => true, () => false);
  return {
    folder: dir.name,
    write: async (blob, filename) => {
      const file = await dir.getFileHandle(await freeName(filename, exists), { create: true });
      const out = await file.createWritable();
      try {
        await out.write(blob);
      } finally {
        await out.close();
      }
    },
  };
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
