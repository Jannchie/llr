import { describe, expect, it } from "vitest";
import { HSL_CENTERS, HSL_LUM_L1, HSL_RIGHT_GAP, hslBandWeight, hslLumFade, hslSelection, hslSelectionL, hueWindow, SKIN_HUE, SKIN_HUE_HALF } from "../hsl-bands";

/** Chroma-over-lightness of ordinary foliage on this repo's sample frame. */
const ORDINARY_FOLIAGE_C_OVER_L = 0.04;

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

  it("hueWindow peaks at its centre, hits zero at the half-width, wraps at ±π", () => {
    expect(hueWindow(SKIN_HUE, SKIN_HUE, SKIN_HUE_HALF)).toBeCloseTo(1, 9);
    expect(hueWindow(SKIN_HUE + SKIN_HUE_HALF, SKIN_HUE, SKIN_HUE_HALF)).toBeCloseTo(0, 9);
    expect(hueWindow(SKIN_HUE + Math.PI, SKIN_HUE, SKIN_HUE_HALF)).toBe(0);
    // Wrap-around: a centre near +π sees hues just past -π as neighbours.
    expect(hueWindow(-Math.PI + 0.1, Math.PI - 0.1, 0.4)).toBeCloseTo(0.5, 9);
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

  it("band weights flatten at their centre so a hue ramp shows no crease", () => {
    // The bare triangle had a |d| kink at every centre. Smoothstepping leaves
    // the peak locally flat: a small hue step either side barely moves the
    // weight, where the triangle moved it linearly.
    for (let k = 0; k < 8; k++) {
      const eps = HSL_RIGHT_GAP[k] * 0.02;
      expect(1 - hslBandWeight(k, HSL_CENTERS[k] + eps)).toBeLessThan(0.01);
    }
  });
});

describe("HSL selection gate", () => {
  it("is inert on near-neutral pixels and full on saturated ones", () => {
    expect(hslSelection(0.002, 0.5)).toBe(0);   // grey with sensor noise
    expect(hslSelection(0.25, 0.63)).toBe(1);   // saturated red
    expect(hslSelection(0.09, 0.70)).toBe(1);   // sky blue
  });

  it("demands proportionally more chroma in shadows, where the noise is", () => {
    // Same absolute chroma reads as coloured in a midtone and as noise in a
    // deep shadow — the L divisor is what separates the two.
    const C = 0.012;
    expect(hslSelection(C, 0.35)).toBeGreaterThan(hslSelection(C, 0.8));
  });

  it("never exceeds unity or goes negative", () => {
    for (const L of [0.05, 0.35, 1.0]) {
      for (let i = 0; i <= 100; i++) {
        const C = (i / 100) * 0.4;
        for (const s of [hslSelection(C, L), hslSelectionL(C, L), hslLumFade(C)]) {
          expect(s).toBeGreaterThanOrEqual(0);
          expect(s).toBeLessThanOrEqual(1);
        }
      }
    }
  });

  it("gates Luminance harder than Hue/Saturation, but not on real colour", () => {
    // L moves brightness directly, so noise scattering across bands is far more
    // visible there than an equal-size hue or chroma wobble.
    for (let i = 1; i < 40; i++) {
      const C = i * 0.002;
      expect(hslSelectionL(C, 0.5)).toBeLessThanOrEqual(hslSelection(C, 0.5) + 1e-9);
    }
    // Desaturated-but-real colour must still get the full Luminance move — an
    // earlier squared-gate version cost it ~40%.
    expect(hslSelectionL(ORDINARY_FOLIAGE_C_OVER_L * 0.5, 0.5)).toBe(1);
    expect(hslSelectionL(0.25, 0.63)).toBe(1);
  });

  it("fades Luminance out over the deepest stop", () => {
    // The L adjustment is an additive Oklab-L offset, so an unfaded step is a
    // far larger relative move in near-black than in a midtone.
    expect(hslLumFade(0)).toBe(0);
    expect(hslLumFade(0.02)).toBeLessThan(0.05);
    expect(hslLumFade(HSL_LUM_L1)).toBe(1);
  });
});
