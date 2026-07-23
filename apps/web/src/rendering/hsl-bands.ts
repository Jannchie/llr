/**
 * HSL Color Mixer band geometry — single source for the shader (HSL_GLSL) and
 * the TS mirror used by tests. Band weights form a triangular partition of
 * unity over the Oklab hue circle: each band's weight falls linearly to zero
 * exactly at its neighbours' centres, so between two adjacent bands the two
 * weights sum to 1. Adjustments interpolate with no dead zones (a fixed
 * half-width left the wide aqua–blue gap at ~27% response) and no overshoot
 * where fixed-width bands used to overlap.
 */

import { glslFloat } from "./color-spaces";

/** Band hue centres (Oklab radians): Red, Orange, Yellow, Green, Aqua, Blue, Purple, Magenta. */
export const HSL_CENTERS: readonly number[] = [0.5101, 0.9210, 1.9160, 2.4873, -2.8833, -1.6745, -1.1558, -0.5523];

const TWO_PI = Math.PI * 2;

/** Wrap an angle difference to [-π, π]. */
const wrapAngle = (a: number): number => a - TWO_PI * Math.floor(a / TWO_PI + 0.5);

/** Gap from band k's centre to the next centre, counter-clockwise (always > 0). */
export const HSL_RIGHT_GAP: readonly number[] = HSL_CENTERS.map((c, k) => {
  const d = HSL_CENTERS[(k + 1) % HSL_CENTERS.length] - c;
  return d <= 0 ? d + TWO_PI : d;
});

/** Triangular band weight for band k at Oklab hue h (radians). */
export function hslBandWeight(k: number, h: number): number {
  const d = wrapAngle(h - HSL_CENTERS[k]);
  const gap = d >= 0 ? HSL_RIGHT_GAP[k] : HSL_RIGHT_GAP[(k + HSL_CENTERS.length - 1) % HSL_CENTERS.length];
  return Math.max(0, 1 - Math.abs(d) / gap);
}

/** Triangular window around a hue centre — TS mirror of the GLSL hueWindow. */
export function hueWindow(h: number, center: number, halfWidth: number): number {
  return Math.max(0, 1 - Math.abs(wrapAngle(h - center)) / halfWidth);
}

// Vibrance skin-tone protection window (heuristic bounds, not calibrated
// data): an orange hue wedge over the low-to-moderate chroma range skin
// occupies. Very saturated oranges (fruit, sunsets) sit past the chroma
// window and keep the full vibrance effect.
export const SKIN_HUE = 0.96;      // Oklab radians (~55°), skin hue centre
export const SKIN_HUE_HALF = 0.55; // triangular half-width
export const SKIN_C0 = 0.02;       // chroma window: fade in…
export const SKIN_C1 = 0.06;
export const SKIN_C2 = 0.16;       // …and back out
export const SKIN_C3 = 0.30;
export const SKIN_DAMP = 0.7;      // fraction of the vibrance term removed

const glArr = (a: readonly number[]): string => a.map(glslFloat).join(", ");

/**
 * GLSL chunk: band centres/gaps, the weight function, and the skin window,
 * generated from the TS values above so the shader and the tested mirror
 * cannot drift.
 */
export const HSL_GLSL = `
const float HSL_CENTERS[8] = float[8](${glArr(HSL_CENTERS)});
const float HSL_RGAP[8] = float[8](${glArr(HSL_RIGHT_GAP)});
const float SKIN_HUE = ${glslFloat(SKIN_HUE)};
const float SKIN_HUE_HALF = ${glslFloat(SKIN_HUE_HALF)};
const float SKIN_C0 = ${glslFloat(SKIN_C0)};
const float SKIN_C1 = ${glslFloat(SKIN_C1)};
const float SKIN_C2 = ${glslFloat(SKIN_C2)};
const float SKIN_C3 = ${glslFloat(SKIN_C3)};
const float SKIN_DAMP = ${glslFloat(SKIN_DAMP)};
const float HSL_TWO_PI = ${glslFloat(TWO_PI)};
// Wrap an angle difference to [-π, π] (mirrors wrapAngle in hsl-bands.ts).
// Exact, and three ops against the ~25 of an atan(sin, cos) round-trip — this
// runs eight times per pixel inside the band loop.
float wrapAngle(float a) { return a - HSL_TWO_PI * floor(a / HSL_TWO_PI + 0.5); }
// Triangular partition-of-unity band weight (see hsl-bands.ts).
float hslBandWeight(int k, float h) {
  float d = wrapAngle(h - HSL_CENTERS[k]);
  float gap = d >= 0.0 ? HSL_RGAP[k] : HSL_RGAP[(k + 7) % 8];
  return max(0.0, 1.0 - abs(d) / gap);
}
// Triangular window around a hue centre (mirrors hueWindow in hsl-bands.ts).
float hueWindow(float h, float center, float halfWidth) {
  return max(0.0, 1.0 - abs(wrapAngle(h - center)) / halfWidth);
}
`;
