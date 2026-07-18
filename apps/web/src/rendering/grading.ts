// Color Grading wheels: hue in degrees on the -180..180 sliders, saturation in
// [0, 1]. This file is the single source for how those become a tint — the
// render uniforms and the panel swatch both derive from it.
//
// The tint is a *display* (sRGB) colour: the swatch shows `hsl(deg, s%, 50%)`,
// so the wheel must render exactly that hue. It is linearized and taken to
// ProPhoto on the CPU (it only depends on uniforms), and the shader multiplies
// it in. Building the tint directly in ProPhoto primaries — the old behaviour —
// broke the cool half of every wheel: ProPhoto blue carries ~0.0001 of ProPhoto
// luma, so the shader's luminance renormalization blew a blue tint up by ×10⁴
// and the gamut map collapsed the result to white. Routed through sRGB, a tint's
// ProPhoto luma equals its display luma, bounded below by blue's 0.0722.
//
// Hue conversion: the HSV helper takes hue in TURNS (fract(h) * 6), so degrees
// divide by the full circle, not the half: dividing by 180 once sent 60° in as
// 0.333 turns (pure green) instead of 0.167 (yellow), and collapsed +90 and
// -90 onto the same colour because fract(-0.5) === fract(0.5).

import { SRGB_TO_PROPHOTO } from "./color-spaces";
import { srgbDecode } from "./curve";

export function gradingHueToTurns(deg: number): number {
  return deg / 360;
}

export function gradingHueDeg(deg: number): number {
  return ((deg % 360) + 360) % 360;
}

/** HSV → sRGB at V=1: hue in turns, `s` in [0,1]. Identity (white) at s=0. */
export function gradingHsv(h: number, s: number): [number, number, number] {
  h = (h - Math.floor(h)) * 6;
  const c = s;
  const x = c * (1 - Math.abs((h % 2) - 1));
  let rgb: [number, number, number];
  if (h < 1)      rgb = [c, x, 0];
  else if (h < 2) rgb = [x, c, 0];
  else if (h < 3) rgb = [0, c, x];
  else if (h < 4) rgb = [0, x, c];
  else if (h < 5) rgb = [x, 0, c];
  else            rgb = [c, 0, x];
  return [rgb[0] + (1 - c), rgb[1] + (1 - c), rgb[2] + (1 - c)];
}

/** Wheel tint as linear ProPhoto, ready for the shader's multiply. */
export function gradingTint(hueDeg: number, sat: number): [number, number, number] {
  const g = gradingHsv(gradingHueToTurns(hueDeg), sat);
  const lin = [srgbDecode(g[0]), srgbDecode(g[1]), srgbDecode(g[2])];
  const M = SRGB_TO_PROPHOTO;
  return [
    M[0][0] * lin[0] + M[0][1] * lin[1] + M[0][2] * lin[2],
    M[1][0] * lin[0] + M[1][1] * lin[1] + M[1][2] * lin[2],
    M[2][0] * lin[0] + M[2][1] * lin[1] + M[2][2] * lin[2],
  ];
}
