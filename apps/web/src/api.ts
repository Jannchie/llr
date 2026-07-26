/**
 * Network layer for the LLR API: the /render-linear binary protocol and the
 * base URL. Kept out of components so the wire format has one owner.
 */

import type { LinearPixels } from "./rendering/pipeline-renderer";

export const API = (import.meta.env.VITE_API_URL as string | undefined) ?? "/api";

export type ColorProfileMeta = {
  // How the worker produced the linear data: a DCP profile, Sony's own rendering
  // reproduced from calibration inside the RAW, LibRaw's matrix fallback, or
  // "rendered-image" for a decoded JPEG/PNG/TIFF, which carries no mosaic,
  // camera profile or lens correction data.
  kind?: "dcp" | "sony" | "libraw-matrix" | "rendered-image";
  // Sony path only: the Creative Look whose tone curve was applied.
  creativeLook?: string;
  // How the DCP was picked, and which styles this camera body actually ships.
  // The style picker is built from these, so it can only offer profiles that
  // resolve — there is no "auto" entry, the matched code is simply selected.
  selection?: { matchedCode?: string | null; availableCodes?: string[] } | null;
  profileToneCurve?: [number, number][] | null;
  // Sony's RGB2YCC, applied right after that curve: four cross terms and four
  // gains, each pair indexed by the sign of a chroma difference. Sony only.
  profileChromaCross?: number[] | null;
  profileChromaGain?: number[] | null;
  // A fitted camera-match table layered on the DCP (present only when applied);
  // cameraMatchAvailable reports whether one exists regardless of the toggle.
  cameraMatch?: unknown;
  cameraMatchAvailable?: boolean;
  // Per-shot lens correction splines from the RAW's maker notes (vendor-neutral
  // factor tables; see rendering/lens.ts parseLensCorr for the shape).
  lensCorr?: unknown;
};

export type LinearMeta = {
  width: number; height: number; fullWidth: number | null; fullHeight: number | null;
  colorProfile: ColorProfileMeta | null; dtype?: string;
};

// Decode linear data via /render-linear. The response carries the pixels
// directly: [u32 header length][JSON header, padded so the pixels stay 4-byte
// aligned][linear RGB in header.dtype]. float16 payloads (half the bytes of
// float32) stay as Uint16Array and upload straight into an RGB16F texture.
// Returns null on 404 (source evicted server-side).
export async function fetchLinear(body: Record<string, unknown>): Promise<{ meta: LinearMeta; pixels: LinearPixels } | null> {
  const res = await fetch(`${API}/render-linear`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(await res.text());
  const buf = await res.arrayBuffer();
  const headerLen = new DataView(buf).getUint32(0);
  const meta = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, headerLen))) as LinearMeta;
  const pixels = meta.dtype === "float16"
    ? new Uint16Array(buf, 4 + headerLen)
    : new Float32Array(buf, 4 + headerLen);
  return { meta, pixels };
}
