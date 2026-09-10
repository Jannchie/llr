import { ref, type Ref } from "vue";
import { PipelineRenderer, parseDcpTables, type EditParams, type ProfileCurve } from "../rendering/pipeline-renderer";
import { API, fetchLinear, type DenoisePayload, type LinearMeta, type LookTweaks } from "../api";
import { t } from "../i18n";

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
  /** Creative Look tweaks; undefined renders the shot's own (Sony path only). */
  look: LookTweaks | undefined;
  /** Which Creative Look; undefined renders the body's own (Sony path only). */
  lookStyle: string | undefined;
  /** DRO strength; undefined applies whatever the body did (Sony path only). */
  dro: number | undefined;
  /** Sony's "advanced colour reproduction" (ZcTask3DLut). Render-time, so it
   * rides the plan rather than the decode — but the export builds its own
   * renderer, so it has to be handed over explicitly or the exported file would
   * silently disagree with the preview. */
  sonyAdvancedColour: boolean;
  params: Partial<EditParams>;
  curveLUT: Float32Array;
  profileLUT: (meta: LinearMeta) => ProfileCurve;
  /** Crop-aware output dims + source transform for the full-res frame. */
  output: (meta: LinearMeta) => { width: number; height: number; texXform: Float32Array };
  background: [number, number, number];
}

/**
 * The export flow: decode full-resolution linear data, re-render it in an
 * off-screen WebGL pipeline with the frozen edit, embed the recipe as XMP
 * server-side, and download the result. How the edit snapshot maps to render
 * params stays with the caller (the `plan` callback).
 */
export function useExport(opts: {
  status: Ref<"idle" | "uploading" | "rendering" | "error">;
  errorMessage: Ref<string | null>;
  /** Freeze the current edit into a plan; null = nothing to export. */
  plan: () => ExportPlan | null;
}) {
  const exporting = ref(false);

  async function exportImage(): Promise<void> {
    if (exporting.value) return;
    const plan = opts.plan();
    if (!plan) return;
    exporting.value = true;
    opts.errorMessage.value = null;
    opts.status.value = "rendering";
    let renderer: PipelineRenderer | null = null;
    try {
      // 1. Decode full-resolution linear data (no half-size / no max-size cap).
      // Same dimensions the preview asks for; purpose is what keeps this one-off
      // decode out of the worker's caches.
      const lin = await fetchLinear({
        sourceId: plan.sourceId, halfSize: false, maxSize: 0, purpose: "export",
        profileId: plan.profileId, dcpCode: plan.dcpCode, denoise: plan.denoise,
        look: plan.look, style: plan.lookStyle, dro: plan.dro,
      });
      if (!lin) throw new Error(t("error.exportSourceGone"));
      const { meta, pixels } = lin;

      // 2. Render full-res off-screen with the frozen edit, read back as JPEG
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
      const out = plan.output(meta);
      renderer.setOutput(out.width, out.height, out.texXform, plan.background);
      renderer.draw(plan.params);
      // quality 1.0 also disables the browser encoder's 4:2:0 chroma subsampling
      const blob = await renderer.toBlob("image/jpeg", 1.0);

      // 3. Embed edit settings (llr:* XMP + lossless LLR JSON) into the JPEG server-side
      const fd = new FormData();
      fd.append("file", blob, "export.jpg");
      fd.append("meta", JSON.stringify({ sourceId: plan.sourceId, settings: plan.settings, stripPrivate: plan.stripPrivate }));
      const exRes = await fetch(`${API}/export`, { method: "POST", body: fd });
      if (!exRes.ok) throw new Error(await exRes.text());

      // 4. Download the finished file
      downloadBlob(await exRes.blob(), plan.filename);
      // Don't stomp the status of a decode the user started mid-export.
      if (opts.status.value === "rendering") opts.status.value = "idle";
    } catch (err) {
      // Same ownership rule as the success path: status may belong to a decode
      // the user started mid-export; the error banner is enough on its own.
      if (opts.status.value === "rendering") opts.status.value = "error";
      opts.errorMessage.value = err instanceof Error ? err.message : String(err);
      console.error("[export] failed:", err);
    } finally {
      renderer?.destroy();
      exporting.value = false;
    }
  }

  return { exporting, exportImage };
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
