/**
 * Generative erase strokes: what the user brushed out, and the coordinate maps
 * between where the brush went on screen and where that lands in the decode.
 *
 * The fill itself is the worker's (worker erase.py): it patches the scene-linear
 * frame before it reaches the GPU, so every edit downstream treats filled pixels
 * like any other. That makes a stroke a property of the *source* pixels, so it is
 * stored in source-normalised coordinates (y down, the decode's own orientation)
 * — the frame the worker patches — not in the view's oriented image space the
 * gradient masks use. Rotating, flipping or cropping afterwards leaves the fill on
 * the object it removed. The lens correction is undone on the way in for the same
 * reason: the shader displays recorded pixel lensUV(p) at p, so the brush at p
 * covered recorded pixels around lensUV(p).
 */

import { imageNormToTexcoord, sourceNormToImageNorm, type CropState } from "./crop";
import { LENS_IDENTITY, lensCaFillScale, lensFillScale, lensInterp } from "./lens";

/** One polyline in source-norm coordinates, radius in units of the frame's long edge. */
export type EraseLine = { points: [number, number][]; radius: number };

/** One brushed region, filled as one: its first line, plus any further lines
 * (`more`) brushed into the same region. `id` is for the panel's list only. */
export type EraseStroke = EraseLine & { id: string; mode?: "heal"; more?: EraseLine[] };

export const ERASE_MAX_STROKES = 64; // the API's and the worker's limits
export const ERASE_MAX_POINTS = 4096;
export const ERASE_MAX_LINES = 32;   // lines in one stroke, the first included

const cloneLine = (l: EraseLine): EraseLine => ({ radius: l.radius, points: l.points.map(([u, v]) => [u, v] as [number, number]) });

/** Every line of a stroke, first one first. */
export function eraseLines(s: EraseLine & { more?: EraseLine[] }): EraseLine[] {
  return [s, ...(s.more ?? [])];
}

export function cloneErase(strokes: readonly EraseStroke[]): EraseStroke[] {
  return strokes.map(s => ({ id: s.id, ...cloneLine(s), ...(s.mode ? { mode: s.mode } : {}),
    ...(s.more?.length ? { more: s.more.map(cloneLine) } : {}) }));
}

/** What render-linear takes: the strokes without their ids, so renaming nothing
 * and reordering nothing cannot change the worker's cache key. A one-line
 * stroke goes out exactly as it did before strokes could have more. */
export function eraseWire(strokes: readonly EraseStroke[]): { points: [number, number][]; radius: number; mode?: "heal"; more?: EraseLine[] }[] {
  return strokes.map(s => ({
    points: s.points, radius: s.radius,
    ...(s.mode === "heal" ? { mode: "heal" as const } : {}),
    ...(s.more?.length ? { more: s.more.map(l => ({ points: l.points, radius: l.radius })) } : {}),
  }));
}

/** The shader's lens warp, as the renderer sets it up (pipeline-renderer
 * setUniforms): tables, fill scale, and the radius normalisation. */
export type LensWarp = { dist: readonly number[]; scale: number; normX: number; normY: number };

export function lensWarp(
  dist: readonly number[] | undefined, caR: readonly number[] | undefined, caB: readonly number[] | undefined,
  texW: number, texH: number,
): LensWarp {
  const d = dist ?? LENS_IDENTITY;
  const diag = Math.hypot(texW, texH) || 1;
  const ca = !!(caR && caB);
  const scale = lensFillScale(d, Math.min(texW, texH) / diag) * (ca ? lensCaFillScale(caR, caB) : 1);
  return { dist: d, scale, normX: (2 * texW) / diag, normY: (2 * texH) / diag };
}

/** Displayed position -> the recorded position the shader fetches there (passes.ts lensUV). */
export function lensForward(w: LensWarp, u: number, v: number): [number, number] {
  const dx = (u - 0.5) * w.normX * w.scale, dy = (v - 0.5) * w.normY * w.scale;
  const f = lensInterp(w.dist, Math.hypot(dx, dy));
  return [0.5 + (dx * f) / w.normX, 0.5 + (dy * f) / w.normY];
}

/** Recorded position -> where it is displayed. Fixed point: f is smooth and near 1. */
export function lensInverse(w: LensWarp, u: number, v: number): [number, number] {
  let pu = u, pv = v;
  for (let i = 0; i < 8; i++) {
    const dx = (pu - 0.5) * w.normX * w.scale, dy = (pv - 0.5) * w.normY * w.scale;
    const k = w.scale * lensInterp(w.dist, Math.hypot(dx, dy));
    pu = 0.5 + (u - 0.5) / k;
    pv = 0.5 + (v - 0.5) / k;
  }
  return [pu, pv];
}

/** Oriented image-norm (the view's space, before perspective) -> source-norm. */
function imageNormToSourceNorm(ix: number, iy: number, c: CropState): [number, number] {
  const [su, t] = imageNormToTexcoord(ix, iy, c);
  return [su, 1 - t];
}

/** View point (image-norm) -> stored stroke point (recorded source-norm). */
export function viewToStroke(ix: number, iy: number, c: CropState, w: LensWarp): [number, number] {
  const [su, sv] = imageNormToSourceNorm(ix, iy, c);
  return lensForward(w, su, sv);
}

/** Stored stroke point -> view point (image-norm), for drawing it back. */
export function strokeToView(u: number, v: number, c: CropState, w: LensWarp): [number, number] {
  const [su, sv] = lensInverse(w, u, v);
  return sourceNormToImageNorm(su, sv, c);
}
