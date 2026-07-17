import { describe, expect, it } from "vitest";
import {
  defaultToneCurve,
  normalizeToneCurve,
  parametricToLUT,
  type CurvePoint,
} from "../curve";

describe("normalizeToneCurve", () => {
  it("returns pristine defaults for null/undefined", () => {
    expect(normalizeToneCurve(null)).toEqual(defaultToneCurve());
    expect(normalizeToneCurve(undefined)).toEqual(defaultToneCurve());
  });

  it("lifts a legacy flat point array into the RGB master channel", () => {
    const pts: CurvePoint[] = [{ x: 0, y: 0 }, { x: 0.5, y: 0.6 }, { x: 1, y: 1 }];
    const tc = normalizeToneCurve(pts);
    expect(tc.rgb).toEqual(pts);
    expect(tc.red).toEqual(defaultToneCurve().red);
    expect(tc.parametric).toEqual(defaultToneCurve().parametric);
  });

  it("drops NaN-poisoned points instead of letting them reach the LUT bake", () => {
    const tc = normalizeToneCurve({
      rgb: [{ x: 0, y: 0 }, { x: Number.NaN, y: 0.5 }, { x: 1, y: 1 }, null],
    });
    expect(tc.rgb).toEqual([{ x: 0, y: 0 }, { x: 1, y: 1 }]);
  });

  it("falls back to the identity when fewer than two valid points survive", () => {
    const tc = normalizeToneCurve({ rgb: [{ x: Number.NaN, y: 0 }, { x: 0.5, y: 0.5 }] });
    expect(tc.rgb).toEqual([{ x: 0, y: 0 }, { x: 1, y: 1 }]);
  });

  it("clamps out-of-range coordinates into [0, 1]", () => {
    const tc = normalizeToneCurve({ rgb: [{ x: -0.5, y: 2 }, { x: 1.5, y: -1 }] });
    expect(tc.rgb).toEqual([{ x: 0, y: 1 }, { x: 1, y: 0 }]);
  });

  it("merges a partial parametric over the defaults", () => {
    const tc = normalizeToneCurve({ parametric: { shadows: 40 } });
    expect(tc.parametric.shadows).toBe(40);
    expect(tc.parametric.midtoneSplit).toBe(50);
  });
});

describe("parametricToLUT", () => {
  const identity = parametricToLUT(defaultToneCurve().parametric);

  it("returns the identity ramp for all-zero region sliders", () => {
    expect(identity[0]).toBe(0);
    expect(identity[2047]).toBe(1);
    expect(identity[1024]).toBeCloseTo(1024 / 2047, 6);
  });

  it("keeps the endpoints anchored whatever the sliders do", () => {
    const lut = parametricToLUT({
      highlights: 100, lights: -80, darks: 60, shadows: -100,
      shadowSplit: 25, midtoneSplit: 50, highlightSplit: 75,
    });
    expect(lut[0]).toBeCloseTo(0, 4);
    expect(lut[2047]).toBeCloseTo(1, 4);
  });

  it("stays monotone non-decreasing even at slider extremes", () => {
    const lut = parametricToLUT({
      highlights: -100, lights: 100, darks: -100, shadows: 100,
      shadowSplit: 25, midtoneSplit: 50, highlightSplit: 75,
    });
    for (let i = 1; i < lut.length; i++) {
      expect(lut[i]).toBeGreaterThanOrEqual(lut[i - 1]);
    }
  });

  it("a shadows lift raises the dark region and leaves the top nearly alone", () => {
    const lut = parametricToLUT({
      highlights: 0, lights: 0, darks: 0, shadows: 60,
      shadowSplit: 25, midtoneSplit: 50, highlightSplit: 75,
    });
    const darkIdx = Math.round(0.125 * 2047);
    const brightIdx = Math.round(0.95 * 2047);
    expect(lut[darkIdx]).toBeGreaterThan(identity[darkIdx] + 0.02);
    expect(Math.abs(lut[brightIdx] - identity[brightIdx])).toBeLessThan(0.01);
  });

  it("sorts degenerate split points instead of producing NaNs", () => {
    const lut = parametricToLUT({
      highlights: 50, lights: 0, darks: 0, shadows: 0,
      shadowSplit: 90, midtoneSplit: 10, highlightSplit: 50, // unsorted splits
    });
    for (const v of lut) expect(Number.isFinite(v)).toBe(true);
  });
});
