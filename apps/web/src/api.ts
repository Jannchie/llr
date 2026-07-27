/**
 * Network layer for the LLR API: the /render-linear binary protocol and the
 * base URL. Kept out of components so the wire format has one owner.
 */

import type { LinearPixels } from "./rendering/pipeline-renderer";

export const API = (import.meta.env.VITE_API_URL as string | undefined) ?? "/api";

// The in-camera Creative Look tweaks, on Sony's own scales: Highlights,
// Shadows, Contrast and Saturation run -9..+9, Fade 0..9. Sony only.
export type LookTweaks = {
  highlights: number; shadows: number; contrast: number; fade: number; saturation: number;
};

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
  // YGamma, run between the two chroma halves: the shot's Fade setting, as a
  // pivot on the luma scale and a contrast about it. Sony only.
  profileLumaPivot?: number | null;
  profileLumaContrast?: number | null;
  // The Saturation slider: profileChromaGain is already divided by it, and the
  // shader multiplies the chroma back after the clamp. Sony only.
  profileChromaSaturation?: number | null;
  // Sepia's toning stage: a weighted sum of the encoded RGB through one curve
  // per channel. Null for every look but Sepia.
  profileSepia?: { weights: number[]; lut: number[][] } | null;
  // What the tone curve and chroma terms above were built with, and what the
  // body itself recorded. The Creative Look panel starts at lookAsShot, which
  // is also what a double-click resets a slider to.
  lookTweaks?: LookTweaks | null;
  lookAsShot?: LookTweaks | null;
  // The DCP's HueSatMaps, each with its samples inline (worker dcp.py
  // table_payload). The worker no longer applies them — they are 3D LUTs, which
  // is one GPU fetch and thirty-odd whole-array numpy passes — so these are what
  // the shader runs. See pipeline-renderer parseDcpTables for the shape.
  profileHueSatMap?: unknown;
  profileLookTable?: unknown;
  // The table fitted against this body's own JPEG rendering. Always sent when
  // one exists, since the toggle that governs it is a shader uniform now;
  // cameraMatchAvailable says whether the control is worth offering at all.
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

// Re-derive the colour profile for a different set of Creative Look tweaks.
// None of the five reaches a pixel — they reshape the tone curve and the chroma
// terms the shader applies — so a moved slider costs this instead of pulling
// the whole frame back through /render-linear. Null means the shot has no Sony
// rendering to re-derive, and the caller keeps the profile it already has.
export async function fetchLookProfile(sourceId: string, look: Partial<LookTweaks>): Promise<ColorProfileMeta | null> {
  const res = await fetch(`${API}/look-profile`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ sourceId, look }),
  });
  if (!res.ok) throw new Error(await res.text());
  return ((await res.json()) as { colorProfile: ColorProfileMeta | null }).colorProfile;
}
