/**
 * Relight: point lights added to the scene-linear frame, shaded on a monocular
 * geometry estimate (worker relight.py: MoGe-2 normals + depth).
 *
 * The geometry is the only thing the worker makes; the light is the shader's
 * (passes.ts RELIGHT_GLSL), placed right after white balance, so a drag is a
 * redraw and exposure, curves and masks all act on the added light as on any
 * other. Adding light in scene-linear is ΔI = A·ΔE: the albedo A is estimated
 * per pixel as the pixel over its blurred neighbourhood (a Retinex proxy read
 * off the renderer's existing log-luma mask), so dark surfaces stay dark and
 * shadowed ones still catch the light.
 *
 * A light is stored like an erase stroke: its aim point in source-normalised
 * coordinates (y down, the decode's own frame), so it stays on the surface it
 * was put on through any later crop, rotation or flip.
 */

import { SRGB_TO_PROPHOTO, halfToFloat, mulMat3, ppLuma, srgbDecode } from "./color-spaces";

export type RelightLight = {
  id: string;
  u: number; v: number;   // aim point, source-norm, y down
  lift: number;           // how far the light stands off the aim point, toward the camera, as a fraction of its depth (< 0: behind it)
  ev: number;             // intensity: 0 adds as much light at the aim point as is already there
  color: string;          // #rrggbb, sRGB
  // Point lamp (default), spot (a cone toward the aim point, `cone` its
  // half-angle in degrees) or sun (parallel light from the lamp's direction).
  kind?: LightKind;
  cone?: number;
};
export type LightKind = "point" | "spot" | "sun";
export const LIGHT_KINDS: LightKind[] = ["point", "spot", "sun"];

export type RelightState = {
  lights: RelightLight[];
  ambient: number;        // EV applied to the existing light (<= 0 dims the scene before the lights add)
  wrap: number;           // 0..1, how far light wraps past the terminator (softness)
  falloff: number;        // 0..1, 1 = inverse-square, 0 = no distance falloff
  shadows: number;        // 0..1, screen-space shadow strength
};

export const RELIGHT_MAX_LIGHTS = 4;

export function defaultRelight(): RelightState {
  return { lights: [], ambient: 0, wrap: 0.3, falloff: 1, shadows: 0.8 };
}

export function cloneRelight(r: RelightState | undefined): RelightState {
  const d = defaultRelight();
  if (!r) return d;
  return { ...d, ...r, lights: (r.lights ?? []).map(l => ({ ...l })) };
}

export function relightActive(r: RelightState): boolean {
  return r.lights.length > 0 || r.ambient !== 0;
}

/** The scene's depth span: near/far as the 2nd/98th percentile and the median, in metres. */
export function geometryStats(geo: RelightGeometry): { near: number; far: number; median: number } {
  const n = geo.width * geo.height;
  const step = Math.max(1, Math.floor(n / 40000));
  const z: number[] = [];
  for (let i = 0; i < n; i += step) {
    const v = halfToFloat(geo.data[4 * i + 3]!);
    if (Number.isFinite(v) && v > 0) z.push(v);
  }
  if (!z.length) return { near: 1, far: 10, median: 3 };
  z.sort((a, b) => a - b);
  const q = (f: number) => z[Math.min(z.length - 1, Math.floor(f * z.length))]!;
  const near = q(0.02), far = Math.max(q(0.98), near * 1.01);
  return { near, far, median: q(0.5) };
}

/** The worker's answer: RGBA half floats (normal xyz, depth), the ray slopes. */
export type RelightGeometry = {
  width: number; height: number;
  k: [number, number];
  data: Uint16Array;
};

// The light's colour is picked on screen, so it is read as sRGB and normalised
// to unit luminance: the colour is the hue, ev is the amount.
export function lightRadiance(l: RelightLight): [number, number, number] {
  const m = /^#?([0-9a-f]{6})$/i.exec(l.color);
  const n = m ? parseInt(m[1]!, 16) : 0xffffff;
  const p = mulMat3(SRGB_TO_PROPHOTO, [srgbDecode(((n >> 16) & 255) / 255), srgbDecode(((n >> 8) & 255) / 255), srgbDecode((n & 255) / 255)]);
  const g = 2 ** l.ev / Math.max(ppLuma(p), 1e-4);
  return [p[0] * g, p[1] * g, p[2] * g];
}

/** EditParams fields for the renderer. Lights land in texcoord space (y up). */
export function packRelight(r: RelightState | null): {
  relightActive: number; relightCount: number; relightPos: Float32Array; relightCol: Float32Array;
  relightAmbient: number; relightWrap: number; relightFalloff: number; relightShadows: number;
} {
  const pos = new Float32Array(4 * RELIGHT_MAX_LIGHTS);
  const col = new Float32Array(4 * RELIGHT_MAX_LIGHTS);
  const lights = r ? r.lights.slice(0, RELIGHT_MAX_LIGHTS) : [];
  lights.forEach((l, i) => {
    // Never exactly on the surface: the light's reference distance is |lift|·z.
    const lift = Math.sign(l.lift || 1) * Math.max(Math.abs(l.lift), 0.02);
    pos.set([l.u, 1 - l.v, lift, LIGHT_KINDS.indexOf(l.kind ?? "point")], 4 * i);
    col.set([...lightRadiance(l), Math.cos(((l.cone ?? 25) * Math.PI) / 180)], 4 * i);
  });
  const active = !!r && relightActive(r);
  return {
    relightActive: active ? 1 : 0, relightCount: lights.length,
    relightPos: pos, relightCol: col,
    relightAmbient: active ? 2 ** r!.ambient : 1,
    relightWrap: r?.wrap ?? 0, relightFalloff: r?.falloff ?? 1, relightShadows: r?.shadows ?? 0,
  };
}
