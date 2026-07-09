import { describe, expect, it } from "vitest";
import {
  basicCurve, buildToneCurveLUT, curveToLUT, defaultParametric, defaultToneCurve,
  parametricToLUT, srgbDecode, srgbEncode, type CurvePoint,
} from "../curve";

const LUT_SIZE = 2048;

function sample(lut: Float32Array, x: number): number {
  const t = Math.min(1, Math.max(0, x)) * (LUT_SIZE - 1);
  const i0 = Math.floor(t);
  const i1 = Math.min(LUT_SIZE - 1, i0 + 1);
  return lut[i0] + (lut[i1] - lut[i0]) * (t - i0);
}

function expectNonDecreasing(lut: Float32Array): void {
  for (let i = 1; i < lut.length; i++) {
    expect(lut[i]).toBeGreaterThanOrEqual(lut[i - 1] - 1e-9);
  }
}

describe("curveToLUT", () => {
  it("two diagonal endpoints produce the identity", () => {
    const lut = curveToLUT([{ x: 0, y: 0 }, { x: 1, y: 1 }]);
    for (const x of [0, 0.1, 0.5, 0.9, 1]) expect(sample(lut, x)).toBeCloseTo(x, 4);
  });

  it("passes through its control points", () => {
    const pts: CurvePoint[] = [
      { x: 0, y: 0 }, { x: 0.25, y: 0.219 }, { x: 0.5, y: 0.5 }, { x: 0.75, y: 0.781 }, { x: 1, y: 1 },
    ];
    const lut = curveToLUT(pts);
    for (const p of pts) expect(sample(lut, p.x)).toBeCloseTo(p.y, 3);
  });

  it("is monotone for monotone control points (Fritsch-Carlson)", () => {
    const lut = curveToLUT([
      { x: 0, y: 0 }, { x: 0.2, y: 0.05 }, { x: 0.4, y: 0.6 }, { x: 0.6, y: 0.65 }, { x: 1, y: 1 },
    ]);
    expectNonDecreasing(lut);
  });

  it("handles a locally decreasing segment without flattening or NaN", () => {
    // Regression for the Fritsch-Carlson divisor guard: both secants negative
    // means w0+w1 < 0; guarding on (w0+w1) > eps instead of |denom| flattened
    // the tangent and kinked the curve here.
    const pts: CurvePoint[] = [{ x: 0, y: 0 }, { x: 0.3, y: 0.8 }, { x: 0.5, y: 0.5 }, { x: 0.7, y: 0.2 }, { x: 1, y: 1 }];
    const lut = curveToLUT(pts);
    for (let i = 0; i < lut.length; i++) {
      expect(Number.isFinite(lut[i])).toBe(true);
      expect(lut[i]).toBeGreaterThanOrEqual(0);
      expect(lut[i]).toBeLessThanOrEqual(1);
    }
    for (const p of pts) expect(sample(lut, p.x)).toBeCloseTo(p.y, 2);
    // The descent between the inner points must actually descend (no flat plateau).
    expect(sample(lut, 0.5)).toBeGreaterThan(sample(lut, 0.7));
  });

  it("anchors unanchored endpoints at (0,0) and (1,1)", () => {
    const lut = curveToLUT([{ x: 0.4, y: 0.2 }, { x: 0.6, y: 0.8 }]);
    expect(lut[0]).toBeCloseTo(0, 6);
    expect(lut[LUT_SIZE - 1]).toBeCloseTo(1, 6);
  });
});

describe("parametricToLUT", () => {
  it("defaults produce the identity", () => {
    const lut = parametricToLUT(defaultParametric());
    for (const x of [0, 0.25, 0.5, 0.75, 1]) expect(sample(lut, x)).toBeCloseTo(x, 5);
  });

  it("positive lights raise the midtones, endpoints stay anchored", () => {
    const lut = parametricToLUT({ ...defaultParametric(), lights: 60 });
    expect(sample(lut, 0.55)).toBeGreaterThan(0.55);
    expect(lut[0]).toBeCloseTo(0, 4);
    expect(lut[LUT_SIZE - 1]).toBeCloseTo(1, 4);
    expectNonDecreasing(lut);
  });
});

describe("basicCurve", () => {
  const ident = { contrast: 0, blacks: 0, whites: 0 };

  it("all-zero adjustments are the identity", () => {
    for (const x of [0, 0.18, 0.5, 0.9, 1]) expect(basicCurve(x, ident)).toBeCloseTo(x, 6);
  });

  it("blacks -100 crushes everything below the 0.12 encoded threshold", () => {
    const xBelow = srgbDecode(0.1);
    expect(basicCurve(xBelow, { ...ident, blacks: -100 })).toBe(0);
    expect(basicCurve(0.5, { ...ident, blacks: -100 })).toBeLessThan(0.5);
  });

  it("whites +100 clips everything above the 0.85 encoded threshold", () => {
    const xAbove = srgbDecode(0.9);
    expect(basicCurve(xAbove, { ...ident, whites: 100 })).toBe(1);
  });

  it("positive contrast steepens around the pivot, endpoints pinned", () => {
    const b = { ...ident, contrast: 60 };
    expect(basicCurve(0, b)).toBeCloseTo(0, 6);
    expect(basicCurve(1, b)).toBeCloseTo(1, 6);
    expect(basicCurve(srgbDecode(0.2), b)).toBeLessThan(srgbDecode(0.2));
    expect(basicCurve(srgbDecode(0.8), b)).toBeGreaterThan(srgbDecode(0.8));
  });
});

describe("buildToneCurveLUT", () => {
  it("defaults produce an identity RGB LUT", () => {
    const lut = buildToneCurveLUT(defaultToneCurve());
    // Entry i holds the output for input i/(LUT_SIZE-1).
    for (const i of [0, 512, 1024, 1536, LUT_SIZE - 1]) {
      const x = i / (LUT_SIZE - 1);
      expect(lut[i * 3 + 0]).toBeCloseTo(x, 4);
      expect(lut[i * 3 + 1]).toBeCloseTo(x, 4);
      expect(lut[i * 3 + 2]).toBeCloseTo(x, 4);
    }
  });

  it("bakes Basic contrast into every channel", () => {
    const lut = buildToneCurveLUT(defaultToneCurve(), { contrast: 100, blacks: 0, whites: 0 });
    const dark = srgbDecode(0.2);
    const i = Math.round(dark * (LUT_SIZE - 1));
    expect(lut[i * 3]).toBeLessThan(dark);
  });
});

describe("srgb transfer", () => {
  it("encode/decode round-trip", () => {
    for (const x of [0, 0.0031308, 0.18, 0.5, 1]) {
      expect(srgbDecode(srgbEncode(x))).toBeCloseTo(x, 6);
    }
  });
});
