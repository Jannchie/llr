/**
 * Camera match: what the body's own JPEG does on top of the whole Sony chain,
 * as a small display-referred correction in CIELAB. The worker ships the
 * table fitted for this body and Creative Look (`profileCameraMatch`, from
 * sony/data/camera_match.json — ARW + in-camera JPEG pairs of one ILCE-7CM2);
 * the shader (passes.ts CAMERA_MATCH_SHADER) runs it as the last stage of the
 * post chain, on the finished sRGB frame, which is exactly the frame the fit
 * measured against.
 *
 * The reference is sony_repro/tools/camera_match_fit.py `apply()`, and this
 * file is its tested mirror — the GLSL is a transcription of these functions,
 * and camera-match.spec.ts pins both to the numbers the worker's test
 * (tests/test_camera_match.py) regenerates from the reference itself:
 *
 *     L' = L + dL(L, C)   a 21 x 19 grid over L* 0..100 and C* 0..90, step 5
 *     C' = C * cr(L, C)   the same grid, both sampled at the *original* L, C
 *     h' = h + hs(h)      24 sectors of 15 degrees, centres 7.5, 22.5, ...
 *
 * with the reference's rules: bilinear inside the grid and held at its edges
 * (a C* past 90 reads the last column), linear between sector centres and
 * periodic on the hue circle (np.interp on the tiled axis). Two dimensions
 * because the residual is not one number per lightness: near neutrals the
 * chain sits more saturated than the camera, the saturated colours less, and
 * a band over L* alone averaged the two. sRGB <-> Lab is the D65 pair in
 * docs/readme/tools/quant.py, the one every dE00 number in this repo was
 * measured with.
 */

/** Grid rows (L* axis) and columns (C* axis) of dL and cr; hue sectors of hs. */
export const CAMERA_MATCH_L_STEPS = 21;
export const CAMERA_MATCH_C_STEPS = 19;
export const CAMERA_MATCH_H_SECTORS = 24;
/** The grid pitch, in L* and C*: row i is L* = 5 i, column j is C* = 5 j. */
export const CAMERA_MATCH_L_PITCH = 5;
export const CAMERA_MATCH_C_PITCH = 5;
/** Centre of sector 0 and the sector pitch, in degrees. */
export const CAMERA_MATCH_H_ORIGIN = 7.5;
export const CAMERA_MATCH_H_PITCH = 15;

/**
 * `profileCameraMatch` as the worker sends it (sony/profile.py
 * camera_match_table): `dL` and `cr` are [L* step][C* step], `hs` the sectors.
 */
export interface ProfileCameraMatch {
  dL: number[][];
  cr: number[][];
  hs: number[];
}

/** Validate the worker's table; null for anything not the fit's shape. */
export function parseCameraMatch(meta: unknown): ProfileCameraMatch | null {
  const m = meta as ProfileCameraMatch | null | undefined;
  if (!m || !Array.isArray(m.dL) || !Array.isArray(m.cr) || !Array.isArray(m.hs)) return null;
  const grid = (g: unknown[]): number[][] | null => {
    if (g.length !== CAMERA_MATCH_L_STEPS) return null;
    const rows: number[][] = [];
    for (const row of g) {
      if (!Array.isArray(row) || row.length !== CAMERA_MATCH_C_STEPS || !row.every(Number.isFinite)) return null;
      rows.push([...row]);
    }
    return rows;
  };
  const dL = grid(m.dL), cr = grid(m.cr);
  if (!dL || !cr) return null;
  if (m.hs.length !== CAMERA_MATCH_H_SECTORS || !m.hs.every(Number.isFinite)) return null;
  return { dL, cr, hs: [...m.hs] };
}

/**
 * A grid table at (L*, C*) — GLSL mirror. Bilinear between grid points and
 * held at the edges: the reference clamps the fractional index to the grid,
 * so anything past the last row or column reads that row or column.
 */
export function cameraMatchBilinear(table: readonly (readonly number[])[], L: number, C: number): number {
  const nl = CAMERA_MATCH_L_STEPS, nc = CAMERA_MATCH_C_STEPS;
  const fl = Math.min(Math.max(L / CAMERA_MATCH_L_PITCH, 0), nl - 1);
  const fc = Math.min(Math.max(C / CAMERA_MATCH_C_PITCH, 0), nc - 1);
  const il = Math.min(Math.floor(fl), nl - 2), ic = Math.min(Math.floor(fc), nc - 2);
  const tl = fl - il, tc = fc - ic;
  const r0 = table[il], r1 = table[il + 1];
  return (1 - tl) * ((1 - tc) * r0[ic] + tc * r0[ic + 1]) + tl * ((1 - tc) * r1[ic] + tc * r1[ic + 1]);
}

/**
 * The sector table at hue h (degrees, any value), periodic: the last sector
 * interpolates into the first across 360, as the fit's tiled np.interp does.
 */
export function cameraMatchInterpHue(table: readonly number[], h: number): number {
  const n = table.length;
  const raw = (h - CAMERA_MATCH_H_ORIGIN) / CAMERA_MATCH_H_PITCH;
  const t = ((raw % n) + n) % n;
  const i = Math.min(Math.floor(t), n - 1);
  return table[i] + (table[(i + 1) % n] - table[i]) * (t - i);
}

// sRGB (D65) <-> CIELAB, the pair in docs/readme/tools/quant.py srgb_to_lab.
export const SRGB_TO_XYZ: readonly number[] = Object.freeze([
  0.4124564, 0.3575761, 0.1804375,
  0.2126729, 0.7151522, 0.0721750,
  0.0193339, 0.1191920, 0.9503041,
]);
export const LAB_WHITE: readonly number[] = Object.freeze([0.95047, 1.0, 1.08883]);
/** The inverse of SRGB_TO_XYZ, computed rather than typed so the two cannot drift. */
export const XYZ_TO_SRGB: readonly number[] = Object.freeze(invert3(SRGB_TO_XYZ));

function invert3(m: readonly number[]): number[] {
  const [a, b, c, d, e, f, g, h, i] = m;
  const A = e * i - f * h, B = -(d * i - f * g), C = d * h - e * g;
  const det = a * A + b * B + c * C;
  return [
    A / det, -(b * i - c * h) / det, (b * f - c * e) / det,
    B / det, (a * i - c * g) / det, -(a * f - c * d) / det,
    C / det, -(a * h - b * g) / det, (a * e - b * d) / det,
  ];
}

const LAB_EPS = 0.008856;
const LAB_KAPPA = 7.787;
const LAB_OFFSET = 16 / 116;

function srgbEotf(u: number): number {
  return u <= 0.04045 ? u / 12.92 : Math.pow((u + 0.055) / 1.055, 2.4);
}
function srgbOetf(u: number): number {
  return u <= 0.0031308 ? u * 12.92 : 1.055 * Math.pow(u, 1 / 2.4) - 0.055;
}
function labF(t: number): number {
  return t > LAB_EPS ? Math.cbrt(t) : LAB_KAPPA * t + LAB_OFFSET;
}
function labFInv(f: number): number {
  const c = f * f * f;
  return c > LAB_EPS ? c : (f - LAB_OFFSET) / LAB_KAPPA;
}

/** Display sRGB (0..1) -> CIELAB (D65). */
export function srgbToLab(rgb: readonly number[]): [number, number, number] {
  const lin = [srgbEotf(rgb[0]), srgbEotf(rgb[1]), srgbEotf(rgb[2])];
  const M = SRGB_TO_XYZ;
  const f = [0, 1, 2].map(r =>
    labF((M[3 * r] * lin[0] + M[3 * r + 1] * lin[1] + M[3 * r + 2] * lin[2]) / LAB_WHITE[r]));
  return [116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])];
}

/** CIELAB (D65) -> display sRGB, clamped to 0..1. */
export function labToSrgb(lab: readonly number[]): [number, number, number] {
  const fy = (lab[0] + 16) / 116;
  const f = [fy + lab[1] / 500, fy, fy - lab[2] / 200];
  const xyz = f.map((v, r) => labFInv(v) * LAB_WHITE[r]);
  const M = XYZ_TO_SRGB;
  const lin = [0, 1, 2].map(r =>
    Math.min(Math.max(M[3 * r] * xyz[0] + M[3 * r + 1] * xyz[1] + M[3 * r + 2] * xyz[2], 0), 1));
  return [srgbOetf(lin[0]), srgbOetf(lin[1]), srgbOetf(lin[2])];
}

/** The correction in Lab — camera_match_fit.py apply(), one point at a time. */
export function applyCameraMatchLab(lab: readonly number[], t: ProfileCameraMatch): [number, number, number] {
  const [L, a, b] = lab;
  const C = Math.hypot(a, b);
  // atan2(0, 0) is 0 in numpy and undefined in GLSL; the hue of a neutral is
  // moot either way, because its chroma stays zero.
  const h = C > 0 ? (((Math.atan2(b, a) * 180) / Math.PI) % 360 + 360) % 360 : 0;
  const L2 = L + cameraMatchBilinear(t.dL, L, C);
  const C2 = C * cameraMatchBilinear(t.cr, L, C);
  const h2 = ((h + cameraMatchInterpHue(t.hs, h)) * Math.PI) / 180;
  return [L2, C2 * Math.cos(h2), C2 * Math.sin(h2)];
}

/** The whole stage on one display pixel: sRGB -> Lab -> correction -> sRGB. */
export function applyCameraMatch(rgb: readonly number[], t: ProfileCameraMatch): [number, number, number] {
  return labToSrgb(applyCameraMatchLab(srgbToLab(rgb), t));
}

/**
 * The grid as the shader's texture: one texel per grid point, C* along x
 * (19 wide) and L* along y (21 high), R = dL and G = cr, so a texelFetch at
 * (j, i) is table[i][j]. Two channels in one texture because both surfaces are
 * read at the same point.
 */
export function cameraMatchGridTexels(t: ProfileCameraMatch): Float32Array {
  const out = new Float32Array(CAMERA_MATCH_L_STEPS * CAMERA_MATCH_C_STEPS * 2);
  for (let i = 0; i < CAMERA_MATCH_L_STEPS; i++) {
    for (let j = 0; j < CAMERA_MATCH_C_STEPS; j++) {
      const k = (i * CAMERA_MATCH_C_STEPS + j) * 2;
      out[k] = t.dL[i][j];
      out[k + 1] = t.cr[i][j];
    }
  }
  return out;
}
