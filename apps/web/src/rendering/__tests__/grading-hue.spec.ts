import { describe, expect, it } from "vitest";
import { gradingHsv, gradingHueToTurns, gradingHueDeg, gradingTint } from "../grading";
import { PROPHOTO_TO_SRGB, ppLuma } from "../color-spaces";
import { srgbEncode } from "../curve";
import { GRAD_RENORM_CAP } from "../passes";

// The hue angle (deg) an RGB triple sits at — the same angle the panel
// swatch's `hsl(hueDeg, s%, 50%)` names.
function rgbToHueDeg([r, g, b]: readonly number[]): number {
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
      const rendered = rgbToHueDeg(gradingHsv(gradingHueToTurns(deg), 1));
      expect(rendered).toBeCloseTo(gradingHueDeg(deg), 4);
    }
  });

  it("H=60 is yellow, not green", () => {
    // The /180 bug sent 60° in as 0.333 turns → fract*6 = 2.0 → pure green.
    expect(gradingHsv(gradingHueToTurns(60), 1)).toEqual([1, 1, 0]);
  });

  it("H=180 is cyan, not red", () => {
    // /180 gave exactly 1.0 turns, and fract(1.0) === 0 → wrapped back to red.
    expect(gradingHsv(gradingHueToTurns(180), 1)).toEqual([0, 1, 1]);
  });

  it("the negative half of the slider covers the other half of the circle", () => {
    // fract(-0.5) === fract(0.5), so under /180 the -90 and +90 stops rendered
    // an identical colour instead of opposing ones.
    const neg = rgbToHueDeg(gradingHsv(gradingHueToTurns(-90), 1));
    const pos = rgbToHueDeg(gradingHsv(gradingHueToTurns(90), 1));
    expect(neg).toBeCloseTo(270, 4);
    expect(pos).toBeCloseTo(90, 4);
  });
});

describe("color grading tint: display-referred, cool half survives", () => {
  it("is the identity multiplier at S=0", () => {
    for (const deg of [0, 45, -120, 180]) {
      for (const ch of gradingTint(deg, 0)) expect(ch).toBeCloseTo(1, 6);
    }
  });

  it("keeps luminance-renorm gain bounded on every hue at full saturation", () => {
    // The old ProPhoto-primaries tint made this ~11700 for blue (ProPhoto blue
    // carries ~0.0001 of ProPhoto luma): the renorm exploded, the gamut map
    // collapsed the pixel to white, and cool grades read as "does nothing".
    // Display-referred, the worst hue (sRGB blue) still carries its display
    // luma of 0.0722 → gain ≤ ~16.5, which the shader further caps.
    for (let deg = -180; deg <= 180; deg += 5) {
      const gain = 1 / ppLuma(gradingTint(deg, 1));
      expect(gain).toBeLessThan(17);
      expect(gain).toBeGreaterThanOrEqual(1 - 1e-6);
    }
  });

  it("the orange–yellow warm axis never reaches the shader's renorm cap", () => {
    // Guarantees the cap only reshapes saturated cool / deep-primary grades —
    // classic warm edits render exactly as if it didn't exist.
    for (const deg of [20, 30, 40, 55]) {
      for (let s = 0; s <= 1.001; s += 0.05) {
        expect(1 / ppLuma(gradingTint(deg, s))).toBeLessThan(GRAD_RENORM_CAP);
      }
    }
  });

  it("round-trips to the exact swatch colour through the display transform", () => {
    // The tint is defined as a display colour: taking it back through
    // ProPhoto→sRGB and the sRGB transfer must reproduce the encoded HSV
    // triple the panel swatch shows, channel for channel.
    const M = PROPHOTO_TO_SRGB;
    for (const deg of [30, 100, -120, -60]) {
      for (const s of [0.4, 1]) {
        const t = gradingTint(deg, s);
        const lin = M.map((row) => row[0] * t[0] + row[1] * t[1] + row[2] * t[2]);
        const swatch = gradingHsv(gradingHueToTurns(deg), s);
        lin.forEach((ch, k) => expect(srgbEncode(ch)).toBeCloseTo(swatch[k], 5));
      }
    }
  });
});
