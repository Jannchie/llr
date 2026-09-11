/**
 * Highlight Saturation — Oklab chroma scaling weighted by lightness, applied
 * display-referred (after the tone-curve LUT, before Color Grading). Single
 * source for the shader constants and the TS mirror the tests exercise.
 *
 *   C_out = C_in · (1 + amount · w(L) · g(C, L))
 *
 * w is a smoothstep over Oklab L from L0 to L0 + HSAT_SOFT; g is the HSL
 * mixer's near-neutral gate, so chroma noise in bright areas is not amplified
 * into coloured grain and pure white (C = 0) is never tinted.
 */

import { glslFloat, smoothstep } from "./color-spaces";
import { hslSelection } from "./hsl-bands";

/** Width of the L ramp above L0. Constant, not a slider: two smoothsteps already give a seamless transition. */
export const HSAT_SOFT = 0.15;

/** Range slider 0..100 -> L0. 50 -> 0.75 (display-linear ≈ 0.5). */
export function hsatRangeToL0(range: number): number {
  return 0.55 + 0.4 * range / 100;
}

/** Lightness weight at Oklab L for threshold lo. */
export function hsatWeight(L: number, lo: number): number {
  return smoothstep(lo, lo + HSAT_SOFT, L);
}

/** Chroma multiplier at Oklab (C, L). amount in [-1, 1]. */
export function hsatScale(C: number, L: number, amount: number, lo: number): number {
  return 1 + amount * hsatWeight(L, lo) * hslSelection(C, L);
}

export const HSAT_GLSL = `
const float HSAT_SOFT = ${glslFloat(HSAT_SOFT)};
`;
