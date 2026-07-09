/**
 * TS mirror of the shader's exposure + tonal-region math — the log-luminance
 * block in passes.ts PROCESS_SHADER. Keep the two in lockstep: the vitest
 * calibration harness (__tests__/tonal-response.spec.ts) runs this mirror over
 * a synthetic step wedge to pin the slider response to the PV2012-style
 * acceptance targets, and the GLSL is written to read line-for-line like this.
 *
 * Like the parametric-curve approximation in curve.ts, the constants
 * approximate ACR PV2012's response; the exported XMP carries the raw slider
 * values so Lightroom applies its own exact interpretation.
 */

export const LOG2_MID = Math.log2(0.18); // middle gray in log2 luminance
export const LX_WHITE = -LOG2_MID;       // diffuse white, stops above middle gray

const EXPO_KNEE = -1.0; // shoulder knee: 1 stop below diffuse white
const EXPO_P = 1.5;     // shoulder span: luminance ceiling at KNEE+P

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
  const wHi = smoothstep(-0.5, 2.5, Math.max(pixLx, lm));
  const wSh = 1 - smoothstep(-3.5, 0.5, Math.min(pixLx, lm));
  const wWh = smoothstep(-1.5, LX_WHITE, pixLx);
  lOut += (p.highlights >= 0 ? 0.9 : 1.3) * p.highlights * wHi
        + (p.shadows >= 0 ? 1.8 : 1.1) * p.shadows * wSh
        + 1.35 * Math.min(p.whites, 0) * wWh;
  return 2 ** lOut;
}
