import { describe, expect, it } from "vitest";
import { HSL_SEL_S0 } from "../hsl-bands";
import { HSAT_SOFT, hsatRangeToL0, hsatScale, hsatWeight } from "../highlight-sat";

const LO = hsatRangeToL0(50);

describe("highlight saturation weight", () => {
  it("range slider maps 0/50/100 onto L0 0.55/0.75/0.95", () => {
    expect(hsatRangeToL0(0)).toBeCloseTo(0.55, 9);
    expect(LO).toBeCloseTo(0.75, 9);
    expect(hsatRangeToL0(100)).toBeCloseTo(0.95, 9);
  });

  it("amount 0 is the identity for any input", () => {
    for (const [C, L] of [[0, 0], [0.05, 0.5], [0.2, 0.9], [0.3, 1]]) {
      expect(hsatScale(C, L, 0, LO)).toBe(1);
    }
  });

  it("is inert below the threshold and reaches the full amount above the ramp", () => {
    expect(hsatScale(0.2, LO, 0.8, LO)).toBe(1);
    expect(hsatScale(0.2, LO - 0.2, 0.8, LO)).toBe(1);
    expect(hsatScale(0.2, LO + HSAT_SOFT, 0.8, LO)).toBeCloseTo(1.8, 9);
    expect(hsatScale(0.2, 1, -1, LO)).toBeCloseTo(0, 9);
  });

  it("near-neutral gate: white and sensor-level chroma are left alone", () => {
    expect(hsatScale(0, 1, 1, LO)).toBe(1);
    expect(hsatScale(HSL_SEL_S0 * 0.9, 1, 1, LO)).toBe(1);
  });

  it("is non-decreasing and continuous along L", () => {
    let prev = hsatScale(0.2, 0, 1, LO);
    for (let i = 1; i <= 1000; i++) {
      const L = i / 1000;
      const cur = hsatScale(0.2, L, 1, LO);
      expect(cur).toBeGreaterThanOrEqual(prev);
      expect(cur - prev).toBeLessThan(0.02);
      prev = cur;
    }
    expect(hsatWeight(LO, LO)).toBe(0);
    expect(hsatWeight(LO + HSAT_SOFT, LO)).toBe(1);
  });
});
