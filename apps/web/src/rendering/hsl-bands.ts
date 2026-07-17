/**
 * HSL Color Mixer band geometry — single source for the shader (HSL_GLSL) and
 * the TS mirror used by tests. Band weights form a triangular partition of
 * unity over the Oklab hue circle: each band's weight falls linearly to zero
 * exactly at its neighbours' centres, so between two adjacent bands the two
 * weights sum to 1. Adjustments interpolate with no dead zones (a fixed
 * half-width left the wide aqua–blue gap at ~27% response) and no overshoot
 * where fixed-width bands used to overlap.
 */

/** Band hue centres (Oklab radians): Red, Orange, Yellow, Green, Aqua, Blue, Purple, Magenta. */
export const HSL_CENTERS: readonly number[] = [0.5101, 0.9210, 1.9160, 2.4873, -2.8833, -1.6745, -1.1558, -0.5523];

const TWO_PI = Math.PI * 2;

/** Wrap an angle difference to [-π, π]. */
const wrapAngle = (a: number): number => Math.atan2(Math.sin(a), Math.cos(a));

/** Gap from band k's centre to the next centre, counter-clockwise (always > 0). */
export const HSL_RIGHT_GAP: readonly number[] = HSL_CENTERS.map((c, k) => {
  const d = (HSL_CENTERS[(k + 1) % HSL_CENTERS.length] - c) % TWO_PI;
  return d <= 0 ? d + TWO_PI : d;
});

/** Triangular band weight for band k at Oklab hue h (radians). */
export function hslBandWeight(k: number, h: number): number {
  const d = wrapAngle(h - HSL_CENTERS[k]);
  const gap = d >= 0 ? HSL_RIGHT_GAP[k] : HSL_RIGHT_GAP[(k + HSL_CENTERS.length - 1) % HSL_CENTERS.length];
  return Math.max(0, 1 - Math.abs(d) / gap);
}

const glf = (x: number): string => (Number.isInteger(x) ? x.toFixed(1) : x.toString());
const glArr = (a: readonly number[]): string => a.map(glf).join(", ");

/**
 * GLSL chunk: band centres/gaps and the weight function, generated from the
 * TS values above so the shader and the tested mirror cannot drift.
 */
export const HSL_GLSL = `
const float HSL_CENTERS[8] = float[8](${glArr(HSL_CENTERS)});
const float HSL_RGAP[8] = float[8](${glArr(HSL_RIGHT_GAP)});
// Triangular partition-of-unity band weight (see hsl-bands.ts).
float hslBandWeight(int k, float h) {
  float d = h - HSL_CENTERS[k];
  d = atan(sin(d), cos(d));
  float gap = d >= 0.0 ? HSL_RGAP[k] : HSL_RGAP[(k + 7) % 8];
  return max(0.0, 1.0 - abs(d) / gap);
}
`;
