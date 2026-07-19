/**
 * Single source of truth for the shader's exposure + tonal-region math — the
 * log-luminance block in passes.ts PROCESS_SHADER. The GLSL side receives these
 * constants and the shoulder function via TONAL_GLSL (injected into the shader
 * source), so the two implementations cannot drift; the vitest calibration
 * harness (__tests__/tonal-response.spec.ts) runs the TS mirror over a
 * synthetic step wedge to pin the slider response to the PV2012-style
 * acceptance targets.
 *
 * Like the parametric-curve approximation in curve.ts, the constants
 * approximate ACR PV2012's response; the exported XMP carries the raw slider
 * values so Lightroom applies its own exact interpretation.
 */
import { glslFloat, srgbDecode, srgbEncode } from "./color-spaces";

export const LOG2_MID = Math.log2(0.18); // middle gray in log2 luminance

export const EXPO_KNEE = -1.0; // shoulder knee: 1 stop below diffuse white
export const EXPO_P = 1.5;     // shoulder span: luminance ceiling at KNEE+P

/**
 * Highlights / Shadows: one invertible compressor, weighted by neighborhood.
 *
 * Both sliders drive a single operator whose strength and direction come from
 * the local tone, instead of each adding its own windowed gain in log space.
 * That older model needed a hand-tuned smoothstep window per slider per sign,
 * and every one of them was a place for the response to go wrong: too wide and
 * Highlights became a second exposure slider, too flat at the bottom and
 * Shadows lifted the blacks into fog.
 *
 * The operator, on a normalized perceptual axis x in [0,1]:
 *
 *     lift(x) = (1 - e^(-c*x)) / (1 - e^(-c))      c = 2^(K*|amt| + 1) - 2
 *     drop(x) = -ln(1 - x*(1 - e^(-c))) / c        (its exact inverse)
 *
 * Three properties fall out of the shape itself, none of them tuned:
 *   - Both endpoints are fixed, so black stays black and the ceiling stays put
 *     however hard either slider is pushed. No floor taper, no clamping.
 *   - The branches are exact inverses, so +50 then -50 returns the original
 *     value (pinned in the calibration harness).
 *   - Monotone at every amount, so the tone order cannot invert.
 *
 * The axis is normalized to TONE_HEAD, not to diffuse white. Pinning at white
 * would leave Highlights inert exactly where it is reached for, and its
 * darkening branch — whose slope at the fixed point exceeds 1 — would *brighten*
 * everything above it. Putting the fixed point above the scene highlights leaves
 * diffuse white mid-axis where the operator pulls it down, which is what keeps
 * highlight recovery working on real RAW headroom.
 *
 * The constants are jointly constrained, not free. Because the *amount* is
 * itself a function of tone, the composite map can invert even though the
 * operator is monotone at any fixed amount: where a pixel equals its blurred
 * neighborhood — a smooth sky gradient — a rising weight can darken faster than
 * the input brightens, reversing the tone order. The calibration harness sweeps
 * that degenerate case across every slider pair and pins the dip at zero; these
 * values are the best fit to the target EV response that stays there. Raising
 * TONE_K to 1.25 or TONE_N past ~2.2 reintroduces it.
 */
export const TONE_HEAD = 2.5; // scene luminance the operator holds fixed
export const TONE_K = 1.0;    // compressor strength (Polarr's own value)
export const TONE_N_HI = 2.0; // weight = p^N, p = perceptual position of the
export const TONE_N_SH = 2.0; //   neighborhood; keeps each slider off the far end

// Clarity: local mid-tone contrast on log2 luminance. The detail amplitude
// d = pixel − blurred neighborhood (stops); the Gaussian window suppresses
// amplification where |d| is large — an edge, not texture — which is the
// halo guard from the local-laplacian remap family (LR PV2012's Clarity).
export const CLAR_GAIN = 0.4;   // added stops per stop of detail at ±100
export const CLAR_SIGMA = 1.2;  // detail window width (stops)
export const CLAR_MID0 = 2.0;   // midtone weight: full within ±2 stops of mid gray
export const CLAR_MID1 = 4.0;   //   fading to zero by ±4 stops

/** Soft highlight shoulder in log2 luminance: identity below the knee, slope decaying to 0 above. */
export function expoShoulder(x: number): number {
  return x <= EXPO_KNEE
    ? x
    : EXPO_KNEE + EXPO_P * (1 - Math.exp(-(x - EXPO_KNEE) / EXPO_P));
}

function smoothstep(e0: number, e1: number, x: number): number {
  const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0)));
  return t * t * (3 - 2 * t);
}

/**
 * The compressor above, on x in [0,1]. `amt` is signed: positive lifts,
 * negative applies the exact inverse. Mirrors toneOp in TONAL_GLSL.
 */
export function toneOp(x: number, amt: number, k: number): number {
  // Below this the curve is the identity to within float noise, and c -> 0
  // would divide by zero.
  if (Math.abs(amt) < 1e-6) return x;
  const c = 2 ** (k * Math.abs(amt) + 1) - 2;
  const g = 1 - Math.exp(-c);
  return amt > 0
    ? (1 - Math.exp(-c * x)) / g
    : -Math.log(Math.max(1 - x * g, 1e-12)) / c;
}

/**
 * Highlights/Shadows applied to scene luminance Y. `Yhi`/`Ysh` are the scene
 * luminances the two weights read — the brighter and darker of the pixel and
 * its neighborhood (see tonalLuma); the operator itself acts on the pixel.
 *
 * Taking the weights from opposite sides of that pair, rather than from the
 * neighborhood alone, is what keeps a small bright feature in a dark frame — a
 * window, a specular — responsive to Highlights instead of inert.
 */
export function toneRegions(Y: number, highlights: number, shadows: number, Yhi: number, Ysh: number): number {
  // One amount, one pass. Chaining a call per slider stacks two compressions on
  // the same range and reintroduces the inversion the constants are chosen to
  // avoid; summing first keeps it to a single monotone map.
  const pHi = srgbEncode(Math.min(Yhi, 1));
  const pSh = srgbEncode(Math.min(Ysh, 1));
  const amt = highlights * pHi ** TONE_N_HI + shadows * (1 - pSh) ** TONE_N_SH;
  const u = Math.min(Y / TONE_HEAD, 1);
  if (Math.abs(amt) < 1e-6) return u * TONE_HEAD;
  return srgbDecode(Math.min(Math.max(toneOp(srgbEncode(u), amt, TONE_K), 0), 1)) * TONE_HEAD;
}

export interface TonalParams {
  exposure: number;   // EV
  highlights: number; // -1..1
  shadows: number;    // -1..1
}

/**
 * Scene luminance in → scene luminance out. `maskLx` is the blurred
 * neighborhood log-luma in stops from middle gray (shifted for WB/exposure);
 * omit it for the per-pixel fallback path (u_hasMask = 0).
 *
 * Highlights and Shadows are weighted by the neighborhood alone (see the
 * operator note above), so a region moves as a region and local contrast
 * inside it is preserved.
 *
 * Whites plays no part here — both halves of it are display-referred, in
 * curve.ts basicCurve. It used to hold a scene-referred log gain for its
 * negative half, but an additive shift and an endpoint-fixed compression acting
 * on the same range are structurally incompatible: stacked, they invert the
 * tone order in smooth highlights badly enough to be visible (a ~40% dip on
 * Highlights -100 with Whites -100, a routine pairing). Recovery did not need
 * it — Highlights -100 already pulls diffuse white down ~1.4 EV, which is the
 * same rescue of above-white scene values.
 */
export function tonalLuma(Y: number, p: TonalParams, maskLx?: number): number {
  const l = Math.log2(Math.max(Y, 1e-6));
  const lOut = p.exposure > 0
    ? l + expoShoulder(l + p.exposure) - expoShoulder(l)
    : l + p.exposure;
  if (p.highlights === 0 && p.shadows === 0) return 2 ** lOut;
  const Y2 = 2 ** lOut;
  // The neighborhood in the same linear units. exp2 is monotone, so max/min of
  // the two log values and of the two linear values pick the same side.
  const Ym = maskLx === undefined ? Y2 : 2 ** (maskLx + LOG2_MID);
  return toneRegions(Y2, p.highlights, p.shadows, Math.max(Y2, Ym), Math.min(Y2, Ym));
}

/**
 * Clarity's log2-luminance shift. `pixLx`/`maskLx` are the pixel and blurred
 * neighborhood in stops from middle gray; `clarity` is -1..1. Mirrors the
 * GLSL clarityShift in TONAL_GLSL below.
 */
export function clarityShift(pixLx: number, maskLx: number, clarity: number): number {
  const d = pixLx - maskLx;
  const amp = Math.exp(-(d * d) / (2 * CLAR_SIGMA * CLAR_SIGMA));
  const mid = 1 - smoothstep(CLAR_MID0, CLAR_MID1, Math.abs(maskLx));
  return clarity * CLAR_GAIN * d * amp * mid;
}

// --- GLSL emission ---

const glf = glslFloat;

/** Emit x^n as repeated multiplication for small integer n, else pow(). */
function powGlsl(x: string, n: number): string {
  return Number.isInteger(n) && n >= 2 && n <= 4
    ? Array(n).fill(x).join(" * ")
    : `pow(${x}, ${glf(n)})`;
}

/**
 * GLSL chunk: tonal constants + the exposure shoulder, generated from the TS
 * values above. Inject once near the top of the fragment shader so the GLSL
 * and the TS mirror (tested against the calibration wedge) cannot drift.
 */
export const TONAL_GLSL = `
const float LOG2_MID  = ${glf(LOG2_MID)};  // log2(0.18): middle gray in log2 luminance
const float EXPO_KNEE = ${glf(EXPO_KNEE)}; // shoulder knee: 1 stop below diffuse white
const float EXPO_P    = ${glf(EXPO_P)};    // shoulder span: luminance ceiling at KNEE+P
const float TONE_HEAD = ${glf(TONE_HEAD)};
const float TONE_K    = ${glf(TONE_K)};
const float TONE_N_HI = ${glf(TONE_N_HI)};
const float TONE_N_SH = ${glf(TONE_N_SH)};
const float CLAR_GAIN = ${glf(CLAR_GAIN)};
const float CLAR_SIGMA = ${glf(CLAR_SIGMA)};
const float CLAR_MID0 = ${glf(CLAR_MID0)};
const float CLAR_MID1 = ${glf(CLAR_MID1)};

// Soft highlight shoulder in log2 luminance: identity below the knee, slope
// decaying to 0 above it (ceiling at EXPO_KNEE + EXPO_P). Monotone, C1.
float expoShoulder(float x) {
  return x <= EXPO_KNEE ? x
       : EXPO_KNEE + EXPO_P * (1.0 - exp(-(x - EXPO_KNEE) / EXPO_P));
}

// The Highlights/Shadows compressor (mirrors toneOp in tonal-model.ts).
// Positive lifts, negative applies the exact inverse; both endpoints fixed.
float toneOp(float x, float amt, float k) {
  if (abs(amt) < 1e-6) return x; // identity, and c -> 0 would divide by zero
  float c = exp2(k * abs(amt) + 1.0) - 2.0;
  float g = 1.0 - exp(-c);
  return amt > 0.0
    ? (1.0 - exp(-c * x)) / g
    : -log(max(1.0 - x * g, 1e-12)) / c;
}

// Highlights/Shadows on scene luminance (mirrors toneRegions in tonal-model.ts).
float toneRegions(float Y, float highlights, float shadows, float Yhi, float Ysh) {
  float pHi = srgbEncode(min(Yhi, 1.0));
  float pSh = 1.0 - srgbEncode(min(Ysh, 1.0));
  float amt = highlights * ${powGlsl("pHi", TONE_N_HI)}
            + shadows * ${powGlsl("pSh", TONE_N_SH)};
  float u = min(Y / TONE_HEAD, 1.0);
  // Most pixels in a single-slider edit weigh ~0; leaving before the encode
  // saves both transfer-function evaluations on them.
  if (abs(amt) < 1e-6) return u * TONE_HEAD;
  return srgbDecode(clamp(toneOp(srgbEncode(u), amt, TONE_K), 0.0, 1.0)) * TONE_HEAD;
}

// Clarity's log2-luminance shift (mirrors clarityShift in tonal-model.ts).
float clarityShift(float pixLx, float maskLx, float clarity) {
  float d = pixLx - maskLx;
  float amp = exp(-d * d / (2.0 * CLAR_SIGMA * CLAR_SIGMA));
  float mid = 1.0 - smoothstep(CLAR_MID0, CLAR_MID1, abs(maskLx));
  return clarity * CLAR_GAIN * d * amp * mid;
}
`;
