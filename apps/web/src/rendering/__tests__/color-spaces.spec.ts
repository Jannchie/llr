import { describe, expect, it } from "vitest";
import { computeWbMatrix, mulMat3, P3_Y, PROPHOTO_TO_P3, PROPHOTO_TO_SRGB, PROPHOTO_Y, REC709_Y } from "../color-spaces";

const ppLuma = (v: readonly [number, number, number]): number =>
  PROPHOTO_Y[0] * v[0] + PROPHOTO_Y[1] * v[1] + PROPHOTO_Y[2] * v[2];

/** How the matrix moves white — the WB "gain" in the old diagonal sense. */
const whiteGain = (temp: number, tint: number): [number, number, number] =>
  mulMat3(computeWbMatrix(temp, tint), [1, 1, 1]);

describe("computeWbMatrix", () => {
  it("is the identity at the 6500K / 0 reference", () => {
    const m = computeWbMatrix(6500, 0);
    for (let r = 0; r < 3; r++) {
      for (let cIdx = 0; cIdx < 3; cIdx++) {
        expect(m[r][cIdx]).toBeCloseTo(r === cIdx ? 1 : 0, 6);
      }
    }
  });

  it("higher temperature warms (more red, less blue)", () => {
    const g = whiteGain(9000, 0);
    expect(g[0]).toBeGreaterThan(1);
    expect(g[2]).toBeLessThan(1);
  });

  it("lower temperature cools (less red, more blue)", () => {
    const g = whiteGain(4000, 0);
    expect(g[0]).toBeLessThan(1);
    expect(g[2]).toBeGreaterThan(1);
  });

  it("positive tint renders magenta (green drops), negative renders green", () => {
    const magenta = whiteGain(6500, 50);
    expect(magenta[1]).toBeLessThan(1);
    expect(magenta[0]).toBeGreaterThan(magenta[1]);
    expect(magenta[2]).toBeGreaterThan(magenta[1]);
    const green = whiteGain(6500, -50);
    expect(green[1]).toBeGreaterThan(1);
  });

  it("preserves white's luminance across the temp and tint range", () => {
    for (const [t, tn] of [[2500, 0], [4000, 0], [9000, 0], [20000, 0], [6500, 100], [6500, -100], [3200, 60]] as const) {
      expect(ppLuma(whiteGain(t, tn))).toBeCloseTo(1, 6);
    }
  });

  it("is continuous across the Planck/daylight cross-fade (3700–4300 K)", () => {
    // Steps inside the cross-fade must stay within the loci's natural slope
    // (~0.035 per 25 K at the blackbody end) — no jump where they meet.
    let prev = whiteGain(3600, 0);
    for (let k = 3625; k <= 4400; k += 25) {
      const g = whiteGain(k, 0);
      for (let i = 0; i < 3; i++) expect(Math.abs(g[i] - prev[i])).toBeLessThan(0.04);
      prev = g;
    }
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
