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

export const LOG2_MID = Math.log2(0.18); // middle gray in log2 luminance
export const LX_WHITE = -LOG2_MID;       // diffuse white, stops above middle gray

export const EXPO_KNEE = -1.0; // shoulder knee: 1 stop below diffuse white
export const EXPO_P = 1.5;     // shoulder span: luminance ceiling at KNEE+P

// Region gains (per slider sign) and smoothstep edges (stops from middle gray).
export const HI_GAIN_POS = 0.9;
export const HI_GAIN_NEG = 1.3;
export const SH_GAIN_POS = 1.8;
export const SH_GAIN_NEG = 1.1;
export const WH_GAIN = 1.35;
export const HI_EDGE0 = -0.5;
export const HI_EDGE1 = 2.5;
export const SH_EDGE0 = -3.5;
export const SH_EDGE1 = 0.5;
export const WH_EDGE0 = -1.5;

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

export interface TonalParams {
  exposure: number;   // EV
  highlights: number; // -1..1
  shadows: number;    // -1..1
  whites: number;     // -1..1
}

/**
 * Scene luminance in → scene luminance out. `maskLx` is the blurred
 * neighborhood log-luma in stops from middle gray (shifted for WB/exposure);
 * omit it for the per-pixel fallback path (u_hasMask = 0).
 *
 * Highlights responds to max(pixel, neighborhood), Shadows to min — small
 * bright/dark features stay responsive while region interiors move with
 * their region (local contrast preserved).
 *
 * Only *negative* Whites acts here (highlight-headroom recovery). Positive
 * Whites is a display-referred white-point scale in curve.ts basicCurve —
 * see the note there.
 */
export function tonalLuma(Y: number, p: TonalParams, maskLx?: number): number {
  const l = Math.log2(Math.max(Y, 1e-6));
  let lOut = p.exposure > 0
    ? l + expoShoulder(l + p.exposure) - expoShoulder(l)
    : l + p.exposure;
  const pixLx = lOut - LOG2_MID;
  const lm = maskLx === undefined ? pixLx : maskLx;
  const wHi = smoothstep(HI_EDGE0, HI_EDGE1, Math.max(pixLx, lm));
  const wSh = 1 - smoothstep(SH_EDGE0, SH_EDGE1, Math.min(pixLx, lm));
  const wWh = smoothstep(WH_EDGE0, LX_WHITE, pixLx);
  lOut += (p.highlights >= 0 ? HI_GAIN_POS : HI_GAIN_NEG) * p.highlights * wHi
        + (p.shadows >= 0 ? SH_GAIN_POS : SH_GAIN_NEG) * p.shadows * wSh
        + WH_GAIN * Math.min(p.whites, 0) * wWh;
  return 2 ** lOut;
}

// --- GLSL emission ---

/** Format a number as a GLSL float literal. */
const glf = (x: number): string => (Number.isInteger(x) ? x.toFixed(1) : x.toString());

/**
 * GLSL chunk: tonal constants + the exposure shoulder, generated from the TS
 * values above. Inject once near the top of the fragment shader so the GLSL
 * and the TS mirror (tested against the calibration wedge) cannot drift.
 */
export const TONAL_GLSL = `
const float LOG2_MID  = ${glf(LOG2_MID)};  // log2(0.18): middle gray in log2 luminance
const float LX_WHITE  = ${glf(LX_WHITE)};  // diffuse white, stops above middle gray
const float EXPO_KNEE = ${glf(EXPO_KNEE)}; // shoulder knee: 1 stop below diffuse white
const float EXPO_P    = ${glf(EXPO_P)};    // shoulder span: luminance ceiling at KNEE+P
const float HI_GAIN_POS = ${glf(HI_GAIN_POS)};
const float HI_GAIN_NEG = ${glf(HI_GAIN_NEG)};
const float SH_GAIN_POS = ${glf(SH_GAIN_POS)};
const float SH_GAIN_NEG = ${glf(SH_GAIN_NEG)};
const float WH_GAIN     = ${glf(WH_GAIN)};
const float HI_EDGE0 = ${glf(HI_EDGE0)};
const float HI_EDGE1 = ${glf(HI_EDGE1)};
const float SH_EDGE0 = ${glf(SH_EDGE0)};
const float SH_EDGE1 = ${glf(SH_EDGE1)};
const float WH_EDGE0 = ${glf(WH_EDGE0)};

// Soft highlight shoulder in log2 luminance: identity below the knee, slope
// decaying to 0 above it (ceiling at EXPO_KNEE + EXPO_P). Monotone, C1.
float expoShoulder(float x) {
  return x <= EXPO_KNEE ? x
       : EXPO_KNEE + EXPO_P * (1.0 - exp(-(x - EXPO_KNEE) / EXPO_P));
}
`;
