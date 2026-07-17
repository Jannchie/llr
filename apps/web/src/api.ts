/**
 * Network layer for the LLR API: the /render-linear binary protocol and the
 * base URL. Kept out of components so the wire format has one owner.
 */

import type { LinearPixels } from "./rendering/pipeline-renderer";

export const API = (import.meta.env.VITE_API_URL as string | undefined) ?? "/api";

export type ColorProfileMeta = { profileToneCurve?: [number, number][] | null };

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
