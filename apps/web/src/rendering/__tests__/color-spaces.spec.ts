import { describe, expect, it } from "vitest";
import { computeWbGain, mulMat3, P3_Y, PROPHOTO_TO_P3, PROPHOTO_TO_SRGB, PROPHOTO_Y, REC709_Y } from "../color-spaces";

describe("computeWbGain", () => {
  it("is the unit gain at the 6500K / 0 reference", () => {
    const g = computeWbGain(6500, 0);
    expect(g[0]).toBeCloseTo(1, 6);
    expect(g[1]).toBeCloseTo(1, 6);
    expect(g[2]).toBeCloseTo(1, 6);
  });

  it("higher temperature warms (more red, less blue), green stays normalised", () => {
    const g = computeWbGain(9000, 0);
    expect(g[0]).toBeGreaterThan(1);
    expect(g[2]).toBeLessThan(1);
    expect(g[1]).toBeCloseTo(1, 6);
  });

  it("lower temperature cools (less red, more blue)", () => {
    const g = computeWbGain(4000, 0);
    expect(g[0]).toBeLessThan(1);
    expect(g[2]).toBeGreaterThan(1);
  });

  it("positive tint shifts toward magenta (green gain drops)", () => {
    const g = computeWbGain(6500, 50);
    expect(g[1]).toBeLessThan(1);
    expect(g[0]).toBeGreaterThan(g[1]);
    expect(g[2]).toBeGreaterThan(g[1]);
  });
});

describe("luminance weights", () => {
  it("each set sums to ~1 (Y of the gamut's white)", () => {
    for (const w of [PROPHOTO_Y, REC709_Y, P3_Y]) {
      expect(w[0] + w[1] + w[2]).toBeCloseTo(1, 3);
    }
  });

  it("display-gamut conversions preserve the luminance of gray", () => {
    // ProPhoto gray → sRGB/P3 stays gray, and its luminance under the matching
    // weights equals the original ProPhoto luminance.
    const gray: [number, number, number] = [0.18, 0.18, 0.18];
    const ppY = PROPHOTO_Y[0] * 0.18 + PROPHOTO_Y[1] * 0.18 + PROPHOTO_Y[2] * 0.18;
    const srgb = mulMat3(PROPHOTO_TO_SRGB, gray);
    const p3 = mulMat3(PROPHOTO_TO_P3, gray);
    const srgbY = REC709_Y[0] * srgb[0] + REC709_Y[1] * srgb[1] + REC709_Y[2] * srgb[2];
    const p3Y = P3_Y[0] * p3[0] + P3_Y[1] * p3[1] + P3_Y[2] * p3[2];
    expect(srgbY).toBeCloseTo(ppY, 3);
    expect(p3Y).toBeCloseTo(ppY, 3);
  });
});
