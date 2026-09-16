/**
 * Lens corrections from the RAW's per-shot metadata (Sony-style radial splines,
 * mapped vendor-neutral by the worker).
 *
 * The worker delivers factor tables sampled on knots in radius normalised to
 * the frame's half-diagonal (corner = 1). The shader consumes fixed 16-entry
 * tables on the canonical grid knot[i] = i / LENS_KNOT_SPAN — the layout Sony
 * writes (worker sony_lens_corrections, where the spacing was measured), so
 * resampling is an identity for ARW files. Semantics:
 *
 *   distortion[i]  sampling factor: a pixel at corrected radius r fetches the
 *                  recorded frame at r * f(r)  (>1 = pincushion correction)
 *   vignetting[i]  linear-light gain at recorded radius r
 *
 * lensInterp() here is the tested mirror of the GLSL interpolation in
 * passes.ts. The last knot sits at r = 15 / 15.2 = 0.987, short of the corner,
 * and the fill scale can ask past 1 for barrel — both extend the last segment
 * linearly rather than clamping, which would leave the corners under-corrected
 * by a few pixels at the long end of a zoom.
 */

export const LENS_KNOTS = 16;
/** Knot i sits at radius i / LENS_KNOT_SPAN; the worker's SONY_KNOT_SPAN. */
export const LENS_KNOT_SPAN = 15.2;
/** How far past the last knot the linear extension is trusted (in knots). */
const LENS_EXTRAP_KNOTS = 1;

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
  // Lateral chromatic aberration: the red and blue planes' own sampling
  // factors, green being the reference (Sony's ChromaticAberrationCorrParams
  // through p*2^-21+1, knots as above). Absent when the body's switch was off
  // or the file carries none. Measured against Edit.exe's own geometric
  // stage on an ILCE-7CM2 frame (sony_repro/notes/ycc-frame-luma-nr.md 5):
  // the red-green and blue-green radial shifts follow these tables to within
  // 0.03 px out to the corner.
  caR?: number[];
  caB?: number[];
}

export const LENS_IDENTITY: readonly number[] = Object.freeze(new Array<number>(LENS_KNOTS).fill(1));

const knotR = (i: number): number => i / LENS_KNOT_SPAN;

/**
 * Evaluate a canonical 16-entry table at normalised radius r (GLSL mirror).
 * Linear between knots; past the last knot the last segment continues, up to
 * LENS_EXTRAP_KNOTS beyond it, then holds.
 */
export function lensInterp(table: readonly number[], r: number): number {
  const t = Math.min(Math.max(r * LENS_KNOT_SPAN, 0), LENS_KNOTS - 1 + LENS_EXTRAP_KNOTS);
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
  const out: LensCorr = { distortion: resample(m.distortion), vignetting: resample(m.vignetting) };
  const ca = (values: unknown): number[] | null =>
    Array.isArray(values) && values.length === n && values.every(Number.isFinite) ? resample(values as number[]) : null;
  const caR = ca(m.caR);
  const caB = ca(m.caB);
  if (caR && caB) { out.caR = caR; out.caB = caB; }
  return out;
}

/**
 * The fill scale lateral CA adds: Edit.exe scales every plane so that the
 * plane reaching furthest — blue, usually — stays inside the recorded frame,
 * and it takes the *largest factor anywhere in the table* rather than the
 * factor at the border. Read off the engine's geometric stage on the frame
 * above: green's own resampling came out at 1/(1 + max(caB)) = 0.99939
 * (-6.1e-4; measured -6.0e-4 +/- 0.5e-4 from the shift field), where the
 * border-bound alternative, 1/(1 + caB(corner)), would be -2.4e-4. The
 * difference is invisible geometrically (a pixel at the corner) and is the
 * whole reason it matters: every plane, green included, is resampled at a
 * drifting sub-pixel phase, and bilinear resampling at random phase averages
 * grain down by about a third (measured 0.66-0.74x on the engine's own
 * tiles) — which is where Edit's exports get most of their extra smoothness
 * over a decode that samples on the pixel grid.
 */
export function lensCaFillScale(caR: readonly number[] | undefined, caB: readonly number[] | undefined): number {
  const peak = Math.max(1, ...(caR ?? []), ...(caB ?? []));
  return 1 / peak;
}

/** Blend a factor table toward identity by the slider amount (0..1). */
export function mixLensTable(table: readonly number[], amount: number): number[] {
  return table.map(v => 1 + (v - 1) * amount);
}

/**
 * Fill scale: pre-scale corrected coordinates by s so that no point on the
 * output frame's border samples outside the recorded frame. For a border point
 * at radius r_p that means s * f(s * r_p) <= 1, and the binding constraint is
 * whichever border point solves it smallest.
 *
 * Which point binds follows the sign of the distortion, so BOTH directions
 * matter and neither can be skipped:
 *
 *   pincushion (f rises with r)  -> the corner binds, s < 1, the frame crops in
 *   barrel     (f falls with r)  -> the SHORT EDGE midpoint binds, s > 1
 *
 * The barrel branch is easy to miss: sampling never leaves the frame diagonally,
 * so anchoring at the corner looks safe — but it pushes the short edge past the
 * recorded border, and the camera does not. Measured against 5 in-camera JPEGs
 * (FE 50-150mm F2 GM, 50..104mm, both directions) this rule lands within 1.5e-4
 * of the scale Sony actually applied, which is the noise floor of the
 * measurement; anchoring at the corner alone was off by 1.1% at 50mm.
 *
 * `shortEdge` is that midpoint's radius, min(w, h) / hypot(w, h) — a property of
 * the frame's aspect ratio, which is why the caller has to supply it.
 *
 * Fixed-point iteration: f is smooth and near 1, so a handful of steps converge
 * far past float precision. The border is sampled rather than reasoned about so
 * a non-monotonic table cannot slip past the binding point.
 */
const FILL_SAMPLES = 16;   // border points tested, short edge to corner
const FILL_ITERS = 16;     // fixed-point steps per point

export function lensFillScale(distortion: readonly number[], shortEdge: number): number {
  let out = Infinity;
  for (let k = 0; k <= FILL_SAMPLES; k++) {
    const anchor = shortEdge + ((1 - shortEdge) * k) / FILL_SAMPLES;
    let s = 1;
    for (let i = 0; i < FILL_ITERS; i++) s = 1 / lensInterp(distortion, s * anchor);
    out = Math.min(out, s);
  }
  return out;
}
