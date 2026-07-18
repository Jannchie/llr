import { describe, expect, it } from "vitest";
import { LENS_IDENTITY, LENS_KNOTS, lensFillScale, lensInterp, mixLensTable, parseLensCorr } from "../lens";

// Worker output for samples/DSC01157.ARW (FE 50-150mm F2 GM @150mm) — Sony
// int16 tables through the fixed-point maps (distortion p*2^-14+1, vignetting
// 1/2^(0.5-2^(p*2^-13-1))), knots (i+0.5)/15. A real pincushion + falloff case.
const SONY_DIST_RAW = [0, 3, 12, 26, 48, 74, 107, 145, 190, 240, 295, 358, 427, 502, 586, 677];
const SONY_VIG_RAW = [0, 96, 96, 128, 352, 960, 1792, 2816, 3968, 5088, 6208, 7360, 8512, 9632, 10688, 11456];
const KNOTS = Array.from({ length: 16 }, (_, i) => (i + 0.5) / 15);
const SONY_DIST = SONY_DIST_RAW.map(p => p * 2 ** -14 + 1);
const SONY_VIG = SONY_VIG_RAW.map(p => 1 / 2 ** (0.5 - 2 ** (p * 2 ** -13 - 1)));
const sonyMeta = { knots: KNOTS, distortion: SONY_DIST, vignetting: SONY_VIG };

describe("lensInterp", () => {
  it("hits knot values exactly at knot positions", () => {
    for (let i = 0; i < LENS_KNOTS; i++) {
      expect(lensInterp(SONY_DIST, (i + 0.5) / 15)).toBeCloseTo(SONY_DIST[i], 12);
    }
  });

  it("clamps to the nearest knot outside the knot range", () => {
    expect(lensInterp(SONY_DIST, 0)).toBe(SONY_DIST[0]);
    expect(lensInterp(SONY_DIST, 2)).toBe(SONY_DIST[15]);
  });

  it("interpolates linearly between knots", () => {
    const mid = (KNOTS[3] + KNOTS[4]) / 2;
    expect(lensInterp(SONY_DIST, mid)).toBeCloseTo((SONY_DIST[3] + SONY_DIST[4]) / 2, 12);
  });
});

describe("parseLensCorr", () => {
  it("is an identity resample when the source already sits on canonical knots", () => {
    const corr = parseLensCorr(sonyMeta)!;
    for (let i = 0; i < LENS_KNOTS; i++) {
      expect(corr.distortion[i]).toBeCloseTo(SONY_DIST[i], 12);
      expect(corr.vignetting[i]).toBeCloseTo(SONY_VIG[i], 12);
    }
  });

  it("resamples a coarser knot layout onto the canonical grid", () => {
    // 2 knots spanning [0, 1]: values interpolate linearly in radius.
    const corr = parseLensCorr({ knots: [0, 1], distortion: [1, 1.1], vignetting: [1, 2] })!;
    expect(corr.distortion[0]).toBeCloseTo(1 + 0.1 * (0.5 / 15), 12);
    expect(corr.distortion[15]).toBeCloseTo(1.1, 12); // 15.5/15 clamps to the outer knot
    expect(corr.vignetting[7]).toBeCloseTo(1 + (7.5 / 15), 12);
  });

  it("rejects malformed metadata", () => {
    expect(parseLensCorr(null)).toBeNull();
    expect(parseLensCorr({})).toBeNull();
    expect(parseLensCorr({ knots: [0, 1], distortion: [1], vignetting: [1, 1] })).toBeNull();
    expect(parseLensCorr({ knots: [0, 1], distortion: [1, Number.NaN], vignetting: [1, 1] })).toBeNull();
  });
});

describe("mixLensTable", () => {
  it("is identity at 0 and the full table at 1", () => {
    expect(mixLensTable(SONY_VIG, 0)).toEqual([...LENS_IDENTITY]);
    expect(mixLensTable(SONY_VIG, 1)).toEqual(SONY_VIG);
  });

  it("halves the correction at 0.5", () => {
    const half = mixLensTable(SONY_DIST, 0.5);
    expect(half[15]).toBeCloseTo(1 + (SONY_DIST[15] - 1) / 2, 12);
  });
});

describe("lensFillScale", () => {
  it("returns 1 for identity and barrel (factor <= 1) tables", () => {
    expect(lensFillScale([...LENS_IDENTITY])).toBe(1);
    expect(lensFillScale(new Array(16).fill(0.98))).toBe(1);
  });

  it("solves s*f(s)=1 for pincushion correction", () => {
    const s = lensFillScale(SONY_DIST);
    expect(s).toBeLessThan(1);
    expect(s * lensInterp(SONY_DIST, s)).toBeCloseTo(1, 9);
  });

  it("keeps every point of the output frame inside the recorded frame", () => {
    const s = lensFillScale(SONY_DIST);
    for (let r = 0; r <= 1.0001; r += 0.01) {
      const rs = Math.min(r, 1) * s;
      expect(rs * lensInterp(SONY_DIST, rs)).toBeLessThanOrEqual(1 + 1e-9);
    }
  });
});
