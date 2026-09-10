import { describe, expect, it } from "vitest";
import { MARBLE_SLIDER_AUTO, marbleBlendAmount, marbleSliderParams, marbleUniforms }
  from "../sony-marble";
import type { MarbleCalib } from "../sony-marble";

/**
 * The two numbers Marble's chroma cleanup takes from outside the shader, pinned
 * to the worker's bit-exact reference (apps/worker/tests/test_marble.py, which
 * pins them in turn to the engine's own buffers). These are integer functions
 * with truncation toward zero in them: a port that divided in floating point
 * would agree at the default and drift by whole threshold steps at the ends,
 * which is exactly what these values catch.
 */

/** ILCE-7CM2, calib+0x10a0 (marble.py CALIB_7CM2); identical on every body dumped. */
const CALIB_7CM2: MarbleCalib = {
  p30: 8192, p34: -4, p38: 982, p3c: -1, p40: 158, p44: -1, p48: 187,
  base_4c: 652, base_50: 652, base_54: 128, base_58: 128,
  lo1: 30, hi1: 40, lo2: 30, hi2: 40, strength: 205,
};

describe("marbleSliderParams", () => {
  it("is the calibration itself at Auto", () => {
    const p = marbleSliderParams(CALIB_7CM2, MARBLE_SLIDER_AUTO);
    expect(p.p4c).toBe(652);
    expect(p.p50).toBe(652);
    expect(p.p54).toBe(128);
    expect(p.p58).toBe(128);
    expect(p.esi).toBe(5);
  });

  it("shrinks the thresholds and keeps more of the centre below Auto", () => {
    const p = marbleSliderParams(CALIB_7CM2, 0);
    expect(p.p4c).toBe(391);
    expect(p.p54).toBe(179);
    expect(p.esi).toBe(3);
  });

  it("grows them 8/5 a step above Auto", () => {
    const p = marbleSliderParams(CALIB_7CM2, 10);
    expect(p.p4c).toBe(2738);
    expect(p.p54).toBe(77);
    expect(p.esi).toBe(7);
  });

  it("passes the thresholds the slider does not reach through untouched", () => {
    for (const s of [0, 3, 5, 7, 10]) {
      const p = marbleSliderParams(CALIB_7CM2, s);
      expect([p.p30, p.p34, p.p38, p.p3c, p.p40, p.p44, p.p48])
        .toEqual([8192, -4, 982, -1, 158, -1, 187]);
      expect([p.lo1, p.hi1, p.lo2, p.hi2, p.strength]).toEqual([30, 40, 30, 40, 205]);
    }
  });

  it("defaults to Auto", () => {
    expect(marbleSliderParams(CALIB_7CM2)).toEqual(marbleSliderParams(CALIB_7CM2, MARBLE_SLIDER_AUTO));
  });
});

describe("marbleBlendAmount", () => {
  it("rises from a half at ISO 100 to the whole filter at 1600", () => {
    expect(marbleBlendAmount(2000, 5)).toBe(1);
    expect(marbleBlendAmount(1600, 5)).toBe(1);
    expect(marbleBlendAmount(100, 5)).toBe(0.5);
    expect(marbleBlendAmount(800, 5)).toBeCloseTo(0.73333, 4);
  });

  it("is ramped towards the whole filter by the slider above Auto", () => {
    expect(marbleBlendAmount(100, 10)).toBeCloseTo(0.7, 6);
    expect(marbleBlendAmount(1600, 10)).toBe(1);
  });

  it("leaves the ISO curve alone at and below Auto", () => {
    expect(marbleBlendAmount(100, 0)).toBe(marbleBlendAmount(100, 5));
    expect(marbleBlendAmount(800, 4)).toBe(marbleBlendAmount(800, 5));
  });
});

describe("marbleUniforms", () => {
  const profile = { iso: 2000, amountAuto: 1, calib: CALIB_7CM2 };

  it("packs the thresholds in the order the shaders declare them", () => {
    const u = marbleUniforms(profile, MARBLE_SLIDER_AUTO);
    expect(u.thrY).toEqual([8192, -4, 982]);
    expect(u.thrC1).toEqual([-1, 158, 652]);
    expect(u.thrC2).toEqual([-1, 187, 652]);
  });

  it("turns the blur mix into the fraction of the centre that survives", () => {
    expect(marbleUniforms(profile, 5).centreMix).toEqual([128 / 256, 128 / 256]);
    expect(marbleUniforms(profile, 0).centreMix).toEqual([179 / 256, 179 / 256]);
  });

  it("carries the protect term as the engine's reciprocal, not a division", () => {
    // round(32768 / (40 - 30)) = 3277, which is not 32768/10.
    const u = marbleUniforms(profile, MARBLE_SLIDER_AUTO);
    expect(u.protect).toEqual([30 * 256, 3277, 30 * 256, 3277]);
    expect(u.strength).toBe(205);
  });

  it("takes its amount from the ISO and the slider together", () => {
    expect(marbleUniforms(profile, 5).amount).toBe(1);
    expect(marbleUniforms({ ...profile, iso: 100 }, 5).amount).toBe(0.5);
    expect(marbleUniforms({ ...profile, iso: 100 }, 10).amount).toBeCloseTo(0.7, 6);
  });
});
