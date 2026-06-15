/**
 * Colour-space constants and GLSL helpers for the scene-referred pipeline.
 *
 * Working space = linear ProPhoto (D50) — the same space Lightroom/ACR edit in.
 * All matrices are row-major (so `M[i]` is row i); `mulMat3` and the generated
 * GLSL `mat3` literals both compute `M · v`. The numbers are produced and
 * round-trip-verified by tmp/compute_color_matrices.py — keep them in sync.
 */

export type Mat3 = readonly [
  readonly [number, number, number],
  readonly [number, number, number],
  readonly [number, number, number],
];

// linear ProPhoto(D50) <-> Oklab LMS (pre-multiplied through XYZ-D50→D65→Oklab M1)
export const PROPHOTO_LINEAR_TO_LMS: Mat3 = [
  [0.71538716, 0.35280861, -0.06826407],
  [0.27443421, 0.66782899, 0.05775600],
  [0.10983819, 0.18630312, 0.70419469],
];
export const LMS_TO_PROPHOTO_LINEAR: Mat3 = [
  [1.73857650, -0.98809992, 0.24957729],
  [-0.70716948, 1.93436371, -0.22720334],
  [-0.08408785, -0.35763815, 1.44124286],
];
// Oklab l'm's' <-> Lab
export const OKLAB_M2: Mat3 = [
  [0.21045426, 0.79361779, -0.00407205],
  [1.97799850, -2.42859221, 0.45059371],
  [0.02590404, 0.78277177, -0.80867577],
];
export const OKLAB_M2_INV: Mat3 = [
  [1.00000000, 0.39633779, 0.21580376],
  [1.00000001, -0.10556134, -0.06385417],
  [1.00000005, -0.08948418, -1.29148554],
];
// linear ProPhoto(D50) -> display linear primaries
export const PROPHOTO_TO_SRGB: Mat3 = [
  [2.03407576, -0.72733405, -0.30674175],
  [-0.22881313, 1.23173007, -0.00291686],
  [-0.00856977, -0.15328662, 1.16185641],
];
export const PROPHOTO_TO_P3: Mat3 = [
  [1.63259411, -0.37962593, -0.25284145],
  [-0.15368251, 1.16666920, -0.01300361],
  [0.01038671, -0.06279176, 1.05218759],
];
// AgX: linear ProPhoto -> AgX inset basis, and outset back to Rec.709
export const AGX_INSET_FROM_PROPHOTO: Mat3 = [
  [1.69504067, -0.52829863, -0.16660567],
  [-0.11558474, 1.03911435, 0.07643346],
  [0.06071460, -0.06897309, 1.00821074],
];
export const AGX_OUTSET: Mat3 = [
  [1.19687901, -0.09802088, -0.09902974],
  [-0.05289685, 1.15190313, -0.09896118],
  [-0.05297164, -0.09804345, 1.15107367],
];
// linear sRGB/Rec.709 -> linear ProPhoto(D50) (AgX outputs Rec.709 -> back to working)
export const SRGB_TO_PROPHOTO: Mat3 = [
  [0.52934593, 0.33007277, 0.14058129],
  [0.09837427, 0.87346103, 0.02816463],
  [0.01688318, 0.11767249, 0.86544430],
];
// Rec.709 display-linear -> Display-P3 display-linear (for AgX P3 output)
export const REC709_TO_P3: Mat3 = [
  [0.82259286, 0.17753392, -0.00000003],
  [0.03319948, 0.96678351, 0.00000002],
  [0.01708534, 0.07239575, 0.91030142],
];
// ProPhoto(D50) luminance weights (Y row of ProPhoto->XYZ-D50)
export const PROPHOTO_Y: readonly [number, number, number] = [0.28804020, 0.71187410, 0.00008570];

// XYZ(D50) -> linear ProPhoto(D50), for white-balance gain computation.
export const XYZ_D50_TO_PROPHOTO: Mat3 = [
  [1.34594337, -0.25560752, -0.05111183],
  [-0.54459882, 1.50816730, 0.02053511],
  [0.00000000, 0.00000000, 1.21181275],
];

/** Row-major M · v. */
export function mulMat3(m: Mat3, v: readonly [number, number, number]): [number, number, number] {
  return [
    m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
    m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
    m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
  ];
}

// --- White balance (relative temp/tint -> linear-ProPhoto gain) ---

/** Planckian locus chromaticity (Kim et al. 2002), valid ~1667–25000 K. */
function planckianXY(kelvin: number): [number, number] {
  const t = Math.min(Math.max(kelvin, 1667), 25000);
  const t2 = t * t;
  const t3 = t2 * t;
  const x = t <= 4000
    ? -0.2661239e9 / t3 - 0.2343589e6 / t2 + 0.8776956e3 / t + 0.179910
    : -3.0258469e9 / t3 + 2.1070379e6 / t2 + 0.2226347e3 / t + 0.240390;
  const x2 = x * x;
  const x3 = x2 * x;
  const y = t <= 2222
    ? -1.1063814 * x3 - 1.34811020 * x2 + 2.18555832 * x - 0.20219683
    : t <= 4000
      ? -0.9549476 * x3 - 1.37418593 * x2 + 2.09137015 * x - 0.16748867
      : 3.0817580 * x3 - 5.87338670 * x2 + 3.75112997 * x - 0.37001483;
  return [x, y];
}

const WB_REFERENCE_K = 6500;

/**
 * White-balance gain in linear ProPhoto. The backend already applied the camera
 * (as-shot) WB, so temp/tint are *relative* nudges: temperature == 6500 K and
 * tint == 0 yield unit gain (1,1,1). Higher temperature -> warmer (more red, less
 * blue), matching Lightroom's slider direction. von Kries-style ratio in ProPhoto;
 * an approximation (a few ΔE off Lightroom at temperature extremes), see the plan.
 */
export function computeWbGain(temperature: number, tint: number): [number, number, number] {
  const toProPhoto = (xy: [number, number]): [number, number, number] => {
    const [x, y] = xy;
    const yy = Math.max(y, 1e-4);
    return mulMat3(XYZ_D50_TO_PROPHOTO, [x / yy, 1, (1 - x - y) / yy]);
  };
  const ref = toProPhoto(planckianXY(WB_REFERENCE_K));
  const tgt = toProPhoto(planckianXY(temperature));
  // ref/tgt so a warmer (lower-x, higher-K is bluer) target *adds* warmth.
  let gain: [number, number, number] = [ref[0] / tgt[0], ref[1] / tgt[1], ref[2] / tgt[2]];
  const g = gain[1] || 1; // normalise on green to preserve overall brightness
  gain = [gain[0] / g, 1, gain[2] / g];
  // Tint: green<->magenta axis. tint > 0 = magenta (reduce green).
  const t = (tint / 100) * 0.30;
  return [gain[0] * (1 + t * 0.5), gain[1] * (1 - t), gain[2] * (1 + t * 0.5)];
}

// --- GLSL emission (column-major literal so `NAME * v` == row-major M · v) ---

function f(x: number): string {
  return Number.isInteger(x) ? x.toFixed(1) : x.toString();
}

function glslMat3(name: string, m: Mat3): string {
  const col = (j: number) => `${f(m[0][j])}, ${f(m[1][j])}, ${f(m[2][j])}`;
  return `const mat3 ${name} = mat3(${col(0)}, ${col(1)}, ${col(2)});`;
}

const MATRICES: ReadonlyArray<readonly [string, Mat3]> = [
  ["PROPHOTO_LINEAR_TO_LMS", PROPHOTO_LINEAR_TO_LMS],
  ["LMS_TO_PROPHOTO_LINEAR", LMS_TO_PROPHOTO_LINEAR],
  ["OKLAB_M2", OKLAB_M2],
  ["OKLAB_M2_INV", OKLAB_M2_INV],
  ["PROPHOTO_TO_SRGB", PROPHOTO_TO_SRGB],
  ["PROPHOTO_TO_P3", PROPHOTO_TO_P3],
  ["SRGB_TO_PROPHOTO", SRGB_TO_PROPHOTO],
  ["AGX_INSET_FROM_PROPHOTO", AGX_INSET_FROM_PROPHOTO],
  ["AGX_OUTSET", AGX_OUTSET],
  ["REC709_TO_P3", REC709_TO_P3],
];

/**
 * GLSL chunk: colour-space matrices + Oklab/transfer/gamut helpers.
 * Inject once near the top of a fragment shader.
 */
export const COLOR_GLSL = `
${MATRICES.map(([n, m]) => glslMat3(n, m)).join("\n")}
const vec3 PROPHOTO_Y = vec3(${f(PROPHOTO_Y[0])}, ${f(PROPHOTO_Y[1])}, ${f(PROPHOTO_Y[2])});

float ppLuma(vec3 c) { return dot(c, PROPHOTO_Y); }

float cbrt(float x) { return sign(x) * pow(abs(x), 1.0 / 3.0); }

// linear ProPhoto(D50) -> Oklab
vec3 proPhotoToOklab(vec3 c) {
  vec3 lms = PROPHOTO_LINEAR_TO_LMS * max(c, 0.0);
  vec3 lms_ = vec3(cbrt(lms.x), cbrt(lms.y), cbrt(lms.z));
  return OKLAB_M2 * lms_;
}

// Oklab -> linear ProPhoto(D50)
vec3 oklabToProPhoto(vec3 lab) {
  vec3 lms_ = OKLAB_M2_INV * lab;
  vec3 lms = lms_ * lms_ * lms_;
  return LMS_TO_PROPHOTO_LINEAR * lms;
}

// Oklab <-> OkLCh (chroma/hue)
vec3 oklabToOklch(vec3 lab) {
  return vec3(lab.x, length(lab.yz), atan(lab.z, lab.y));
}
vec3 oklchToOklab(vec3 lch) {
  return vec3(lch.x, lch.y * cos(lch.z), lch.y * sin(lch.z));
}

// sRGB / Display-P3 share the sRGB transfer function
float srgbEncode(float c) {
  c = clamp(c, 0.0, 1.0);
  return c <= 0.0031308 ? c * 12.92 : 1.055 * pow(c, 1.0 / 2.4) - 0.055;
}
vec3 srgbEncode(vec3 c) { return vec3(srgbEncode(c.r), srgbEncode(c.g), srgbEncode(c.b)); }
`;
