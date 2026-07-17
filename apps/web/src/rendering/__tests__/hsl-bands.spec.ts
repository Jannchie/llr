import { describe, expect, it } from "vitest";
import { HSL_CENTERS, HSL_RIGHT_GAP, hslBandWeight } from "../hsl-bands";

describe("HSL band weights", () => {
  it("gaps are positive and cover the full hue circle", () => {
    for (const g of HSL_RIGHT_GAP) expect(g).toBeGreaterThan(0);
    const total = HSL_RIGHT_GAP.reduce((a, b) => a + b, 0);
    expect(total).toBeCloseTo(Math.PI * 2, 6);
  });

  it("each band peaks at 1 on its own centre and 0 on its neighbours'", () => {
    for (let k = 0; k < 8; k++) {
      expect(hslBandWeight(k, HSL_CENTERS[k])).toBeCloseTo(1, 9);
      expect(hslBandWeight(k, HSL_CENTERS[(k + 1) % 8])).toBeCloseTo(0, 9);
      expect(hslBandWeight(k, HSL_CENTERS[(k + 7) % 8])).toBeCloseTo(0, 9);
    }
  });

  it("forms a partition of unity over the whole hue circle", () => {
    // The old fixed 0.7 rad half-width dipped to ~0.27 midway between aqua and
    // blue and summed to ~1.4 between overlapping bands; the triangular
    // weights interpolate exactly everywhere.
    for (let i = 0; i < 720; i++) {
      const h = -Math.PI + (i / 720) * Math.PI * 2;
      let sum = 0;
      for (let k = 0; k < 8; k++) sum += hslBandWeight(k, h);
      expect(sum).toBeCloseTo(1, 6);
    }
  });
});
