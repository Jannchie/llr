import { describe, expect, it } from "vitest";
import { gradingHueToTurns, gradingHueDeg } from "../grading";

// Mirror of hsvToRgb in passes.ts. The GLSL takes hue in TURNS (fract(h) * 6),
// so this pins the conversion the render params and the panel swatch share:
// grading hue sliders are in degrees over the full circle, so the mapping to
// turns divides by 360. Both sides go through ../grading, so a regression there
// (e.g. back to /180) fails these assertions.
function hsvToRgb(h: number, s: number): [number, number, number] {
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

// The hue angle (deg) a fully-saturated RGB triple sits at — the same angle the
// panel swatch's `hsl(hueDeg, s%, 50%)` names.
function rgbToHueDeg([r, g, b]: [number, number, number]): number {
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const d = max - min;
  if (d === 0) return 0;
  let h: number;
  if (max === r) h = ((g - b) / d) % 6;
  else if (max === g) h = (b - r) / d + 2;
  else h = (r - g) / d + 4;
  h *= 60;
  return ((h % 360) + 360) % 360;
}

describe("color grading hue: slider degrees → shader turns", () => {
  it("renders the hue its panel swatch shows", () => {
    for (const deg of [0, 60, 120, 180, -90]) {
      const rendered = rgbToHueDeg(hsvToRgb(gradingHueToTurns(deg), 1));
      expect(rendered).toBeCloseTo(gradingHueDeg(deg), 4);
    }
  });

  it("H=60 is yellow, not green", () => {
    // The /180 bug sent 60° in as 0.333 turns → fract*6 = 2.0 → pure green.
    expect(hsvToRgb(gradingHueToTurns(60), 1)).toEqual([1, 1, 0]);
  });

  it("H=180 is cyan, not red", () => {
    // /180 gave exactly 1.0 turns, and fract(1.0) === 0 → wrapped back to red.
    expect(hsvToRgb(gradingHueToTurns(180), 1)).toEqual([0, 1, 1]);
  });

  it("the negative half of the slider covers the other half of the circle", () => {
    // fract(-0.5) === fract(0.5), so under /180 the -90 and +90 stops rendered
    // an identical colour instead of opposing ones.
    const neg = rgbToHueDeg(hsvToRgb(gradingHueToTurns(-90), 1));
    const pos = rgbToHueDeg(hsvToRgb(gradingHueToTurns(90), 1));
    expect(neg).toBeCloseTo(270, 4);
    expect(pos).toBeCloseTo(90, 4);
  });
});
