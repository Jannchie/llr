/**
 * Lens corrections from the RAW's per-shot metadata (Sony-style radial splines,
 * mapped vendor-neutral by the worker).
 *
 * The worker delivers factor tables sampled on knots in radius normalised to
 * the frame's half-diagonal (corner = 1). The shader consumes fixed 16-entry
 * tables on the canonical grid knot[i] = (i + 0.5) / 15 — the same layout Sony
 * writes, so resampling is an identity for ARW files. Semantics:
 *
 *   distortion[i]  sampling factor: a pixel at corrected radius r fetches the
 *                  recorded frame at r * f(r)  (>1 = pincushion correction)
 *   vignetting[i]  linear-light gain at recorded radius r
 *
 * lensInterp() here is the tested mirror of the GLSL interpolation in
 * passes.ts — both clamp to the nearest knot outside the knot range.
 */

export const LENS_KNOTS = 16;

/** Correction tables as delivered in colorProfile.lensCorr by the worker. */
export interface LensCorrMeta {
  knots: number[];
  distortion: number[];
  vignetting: number[];
  caR?: number[];
  caB?: number[];
}

/** Canonical per-image tables, resampled onto the fixed 16-knot grid. */
export interface LensCorr {
  distortion: number[];
  vignetting: number[];
}

export const LENS_IDENTITY: readonly number[] = Object.freeze(new Array<number>(LENS_KNOTS).fill(1));

const knotR = (i: number): number => (i + 0.5) / (LENS_KNOTS - 1);

/** Evaluate a canonical 16-entry table at normalised radius r (GLSL mirror). */
export function lensInterp(table: readonly number[], r: number): number {
  const t = Math.min(Math.max(r * (LENS_KNOTS - 1) - 0.5, 0), LENS_KNOTS - 1);
  const i = Math.min(Math.floor(t), LENS_KNOTS - 2);
  return table[i] + (table[i + 1] - table[i]) * (t - i);
}

/** Linear spline over arbitrary knots, clamped to the outer knot values. */
function splineAt(knots: number[], values: number[], r: number): number {
  if (r <= knots[0]) return values[0];
  for (let i = 1; i < knots.length; i++) {
    if (r <= knots[i]) {
      const t = (r - knots[i - 1]) / (knots[i] - knots[i - 1]);
      return values[i - 1] + (values[i] - values[i - 1]) * t;
    }
  }
  return values[values.length - 1];
}

/** Validate worker metadata and resample onto the canonical grid. */
export function parseLensCorr(meta: unknown): LensCorr | null {
  const m = meta as LensCorrMeta | null | undefined;
  if (!m || !Array.isArray(m.knots) || !Array.isArray(m.distortion) || !Array.isArray(m.vignetting)) return null;
  const n = m.knots.length;
  if (n < 2 || m.distortion.length !== n || m.vignetting.length !== n) return null;
  if (![...m.knots, ...m.distortion, ...m.vignetting].every(Number.isFinite)) return null;
  const resample = (values: number[]): number[] =>
    Array.from({ length: LENS_KNOTS }, (_, i) => splineAt(m.knots, values, knotR(i)));
  return { distortion: resample(m.distortion), vignetting: resample(m.vignetting) };
}

/** Blend a factor table toward identity by the slider amount (0..1). */
export function mixLensTable(table: readonly number[], amount: number): number[] {
  return table.map(v => 1 + (v - 1) * amount);
}

/**
 * Fill scale for pincushion correction. Sampling factor > 1 at the corner
 * would fetch outside the recorded frame, so pre-scale corrected coordinates
 * by s solving s * f(s) = 1 — the same slight FOV crop-in cameras apply.
 * Barrel correction (f <= 1) needs no scale. Fixed-point iteration: f is
 * smooth and near 1, so a handful of steps converge far past float precision.
 */
export function lensFillScale(distortion: readonly number[]): number {
  if (lensInterp(distortion, 1) <= 1) return 1;
  let s = 1;
  for (let i = 0; i < 12; i++) s = 1 / lensInterp(distortion, s);
  return s;
}
