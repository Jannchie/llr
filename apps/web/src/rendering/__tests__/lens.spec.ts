import { describe, expect, it } from "vitest";
import { LENS_IDENTITY, LENS_KNOTS, lensFillScale, lensInterp, mixLensTable, parseLensCorr } from "../lens";

// Worker output for samples/DSC01157.ARW (FE 50-150mm F2 GM @150mm) — Sony
// int16 tables through the fixed-point maps (distortion p*2^-14+1, vignetting
// 1/2^(0.5-2^(p*2^-13-1))), knots (i+0.5)/16. A real pincushion + falloff case.
const SONY_DIST_RAW = [0, 3, 12, 26, 48, 74, 107, 145, 190, 240, 295, 358, 427, 502, 586, 677];
const SONY_VIG_RAW = [0, 96, 96, 128, 352, 960, 1792, 2816, 3968, 5088, 6208, 7360, 8512, 9632, 10688, 11456];
const KNOTS = Array.from({ length: 16 }, (_, i) => (i + 0.5) / 16);
const SONY_DIST = SONY_DIST_RAW.map(p => p * 2 ** -14 + 1);
const SONY_VIG = SONY_VIG_RAW.map(p => 1 / 2 ** (0.5 - 2 ** (p * 2 ** -13 - 1)));
const sonyMeta = { knots: KNOTS, distortion: SONY_DIST, vignetting: SONY_VIG };

// Same lens at 50mm (10960725/DSC02976.ARW) — barrel, the direction the fill
// scale used to skip. 3:2, so the short edge's midpoint sits at 2/sqrt(13).
const BARREL_RAW = [0, -3, -10, -22, -40, -61, -87, -118, -153, -192, -236, -285, -339, -396, -459, -527];
const BARREL = BARREL_RAW.map(p => p * 2 ** -14 + 1);
// And at 104mm (DSC02961) — pincushion, measured the same way as the barrel one.
const PINCUSHION_RAW = [0, 2, 7, 17, 31, 47, 67, 92, 119, 149, 183, 219, 260, 302, 345, 390];
const PINCUSHION = PINCUSHION_RAW.map(p => p * 2 ** -14 + 1);
const SHORT_EDGE_3x2 = 2 / Math.hypot(3, 2);

describe("lensInterp", () => {
  it("hits knot values exactly at knot positions", () => {
    for (let i = 0; i < LENS_KNOTS; i++) {
      expect(lensInterp(SONY_DIST, (i + 0.5) / 16)).toBeCloseTo(SONY_DIST[i], 12);
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
    expect(corr.distortion[0]).toBeCloseTo(1 + 0.1 * (0.5 / 16), 12);
    expect(corr.distortion[15]).toBeCloseTo(1 + 0.1 * (15.5 / 16), 12);
    expect(corr.vignetting[7]).toBeCloseTo(1 + (7.5 / 16), 12);
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
  it("is 1 for an identity table", () => {
    expect(lensFillScale([...LENS_IDENTITY], SHORT_EDGE_3x2)).toBeCloseTo(1, 12);
  });

  it("anchors pincushion at the corner: s*f(s)=1, and s < 1", () => {
    const s = lensFillScale(SONY_DIST, SHORT_EDGE_3x2);
    expect(s).toBeLessThan(1);
    expect(s * lensInterp(SONY_DIST, s)).toBeCloseTo(1, 9);
  });

  it("anchors barrel at the short edge, and s > 1", () => {
    const s = lensFillScale(BARREL, SHORT_EDGE_3x2);
    expect(s).toBeGreaterThan(1);
    expect(s * lensInterp(BARREL, s * SHORT_EDGE_3x2)).toBeCloseTo(1, 9);
    // Anchoring at the corner instead would overshoot and push the short edge
    // past the recorded border — the bug this replaced.
    expect(s).toBeLessThan(1 / lensInterp(BARREL, 1));
  });

  it("matches the scale Sony applied, measured off the in-camera JPEG", () => {
    // Radial displacement between the in-camera JPEG and the uncorrected decode,
    // fitted for the scale (residual rms ~4e-4, the block-matching noise floor).
    // DSC02976 @50mm measures 1.0107, DSC02961 @104mm measures 0.9769.
    expect(lensFillScale(BARREL, SHORT_EDGE_3x2)).toBeCloseTo(1.0107, 3);
    expect(lensFillScale(PINCUSHION, SHORT_EDGE_3x2)).toBeCloseTo(0.9769, 3);
  });

  it("keeps every border point of the output frame inside the recorded frame", () => {
    for (const table of [SONY_DIST, BARREL, PINCUSHION]) {
      const s = lensFillScale(table, SHORT_EDGE_3x2);
      for (let r = SHORT_EDGE_3x2; r <= 1.0001; r += 0.005) {
        expect(s * lensInterp(table, s * Math.min(r, 1))).toBeLessThanOrEqual(1 + 1e-6);
      }
    }
  });
});
