/**
 * Calibration harness for the PV2012-style tonal model: runs a synthetic gray
 * step wedge through the TS mirror (tonal-model.ts, scene-referred sliders)
 * and the display-referred basic curve (curve.ts), asserting the acceptance
 * targets the constants were tuned against. Run with `pnpm test`; to dump the
 * per-slider response tables for recalibration:
 *   PRINT_TABLES=1 npx vitest run --disable-console-intercept
 */
import { describe, expect, it } from "vitest";
import { CLAR_MID1, CLAR_SIGMA, LOG2_MID, TONE_HEAD, clarityShift, expoShoulder, toneOp, tonalLuma, type TonalParams } from "../tonal-model";
import { DEFAULT_BASIC, basicCurve, buildToneCurveLUT, defaultToneCurve, srgbDecode, srgbEncode, type BasicAdjust } from "../curve";

// basicCurve is linear-in/linear-out but acts on the perceptual axis, so the
// thresholds below are stated where they are meant: on screen.
const onScreen = (e: number, basic: BasicAdjust): number =>
  srgbEncode(basicCurve(srgbDecode(e), basic));

/** On-screen displacement basic `b` applies at on-screen value `e`. */
const disp = (e: number, b: BasicAdjust): number => onScreen(e, b) - e;

const ZERO: TonalParams = { exposure: 0, highlights: 0, shadows: 0 };
const P = (over: Partial<TonalParams>): TonalParams => ({ ...ZERO, ...over });
const B = (over: Partial<BasicAdjust>): BasicAdjust => ({ ...DEFAULT_BASIC, ...over });

/** EV shift the tonal model applies at scene luminance Y. */
const evShift = (Y: number, p: TonalParams): number => Math.log2(tonalLuma(Y, p) / Y);

// Wedge probes (scene-linear luminance): deep shadow lx=-4, mid gray, diffuse white.
const DEEP = 0.18 * 2 ** -4;
const MID = 0.18;
const WHITE = 1.0;

// lx = -6 … +4 in half stops.
const WEDGE = Array.from({ length: 21 }, (_, i) => 0.18 * 2 ** (-6 + i * 0.5));

describe("identity", () => {
  it("all-zero params leave the wedge untouched", () => {
    for (const Y of WEDGE) expect(tonalLuma(Y, ZERO)).toBeCloseTo(Y, 6);
  });
  it("zero basic adjust is the identity curve", () => {
    for (const x of [0, 0.12, 0.435, 0.8, 1]) {
      expect(basicCurve(x, DEFAULT_BASIC)).toBeCloseTo(x, 9);
    }
  });
});

describe("exposure", () => {
  it("is exact gain below the shoulder knee", () => {
    expect(evShift(DEEP, P({ exposure: 2 }))).toBeCloseTo(2, 3);
    expect(evShift(MID, P({ exposure: 2 }))).toBeCloseTo(2, 0.5);
  });
  it("negative exposure is a pure gain everywhere", () => {
    for (const Y of WEDGE) expect(evShift(Y, P({ exposure: -2 }))).toBeCloseTo(-2, 6);
  });
  it("rolls brights off instead of walling: Y=0.25 at +2 EV stays below clip", () => {
    expect(tonalLuma(0.25, P({ exposure: 2 }))).toBeLessThan(1);
    expect(tonalLuma(0.25, P({ exposure: 2 }))).toBeGreaterThan(0.7);
  });
  it("shoulder is identity below the knee and monotone in E", () => {
    expect(expoShoulder(-3)).toBeCloseTo(-3, 9);
    let prev = -Infinity;
    for (let e = 0; e <= 5; e += 0.25) {
      const v = tonalLuma(MID, P({ exposure: e }));
      expect(v).toBeGreaterThan(prev);
      prev = v;
    }
  });
});

describe("highlights / shadows", () => {
  it("Highlights -100: ~-1.4 EV at diffuse white, rising monotonically from mid", () => {
    expect(evShift(WHITE, P({ highlights: -1 }))).toBeCloseTo(-1.38, 1);
    expect(evShift(0.72, P({ highlights: -1 }))).toBeCloseTo(-1.05, 1);
    expect(evShift(0.36, P({ highlights: -1 }))).toBeCloseTo(-0.59, 1);
    // The weight is the neighborhood's perceptual position, so mid gray does
    // move — ~0.3 EV. Sharpening the weight enough to leave it alone is exactly
    // what reintroduces the highlight inversion; this is the cost of the trade.
    expect(evShift(MID, P({ highlights: -1 }))).toBeCloseTo(-0.33, 1);
    expect(Math.abs(evShift(DEEP, P({ highlights: -1 })))).toBeLessThan(0.02);
  });
  it("Shadows +100: ~+1.2 EV in deep shadow, small at mid gray, ~0 at white", () => {
    expect(evShift(DEEP, P({ shadows: 1 }))).toBeCloseTo(1.22, 1);
    expect(evShift(MID, P({ shadows: 1 }))).toBeCloseTo(0.44, 1);
    expect(Math.abs(evShift(WHITE, P({ shadows: 1 })))).toBeLessThan(0.02);
  });
  it("the two branches are exact inverses (+a then -a round-trips)", () => {
    // The property the whole operator is built around: an endpoint-fixed
    // saturating curve paired with its own analytic inverse.
    for (const a of [0.25, 0.5, 1]) {
      for (const x of [0.05, 0.2, 0.5, 0.8, 0.99]) {
        expect(toneOp(toneOp(x, a, 1.5), -a, 1.5)).toBeCloseTo(x, 9);
        expect(toneOp(toneOp(x, -a, 1.0), a, 1.0)).toBeCloseTo(x, 9);
      }
    }
  });
  it("black stays black and the deep lift stays bounded (no fog floor)", () => {
    // The operator fixes both endpoints, so this needs no floor taper: zero
    // maps to zero exactly however hard Shadows is pushed, and the lift
    // approaches a finite asymptote (log2(c/g) ≈ 1.9 EV) rather than running
    // away into a fog pedestal.
    // Stated on the operator: tonalLuma's own log-domain floor (max(Y, 1e-6))
    // keeps it from returning a literal zero.
    for (const a of [-1, -0.5, 0.5, 1]) expect(toneOp(0, a, 1.5)).toBeCloseTo(0, 12);
    expect(tonalLuma(0, P({ shadows: 1 }))).toBeLessThan(1e-5);
    const at = (lx: number) => evShift(0.18 * 2 ** lx, P({ shadows: 1 }));
    for (const lx of [-4, -6, -8, -12]) expect(at(lx)).toBeLessThan(1.25);
    expect(at(-12)).toBeGreaterThan(1.1); // converged to the asymptote
  });
  it("the ceiling is fixed: neither slider moves scene TONE_HEAD", () => {
    for (const p of [P({ highlights: -1 }), P({ highlights: 1 }), P({ shadows: 1 }), P({ shadows: -1 })]) {
      expect(tonalLuma(TONE_HEAD, p)).toBeCloseTo(TONE_HEAD, 6);
    }
  });
  it("a bright neighborhood drags a dark pixel with it (locality)", () => {
    // Dark pixel (lx=-2) inside a bright region (mask lx=+2): Highlights -100
    // must pull it down although its own luma sits outside the window.
    const inBright = evShift(0.18 * 2 ** -2, { ...P({ highlights: -1 }) });
    const masked = Math.log2(tonalLuma(0.18 * 2 ** -2, P({ highlights: -1 }), 2) / (0.18 * 2 ** -2));
    expect(masked).toBeLessThan(inBright - 0.3);
  });
  it("a small bright feature in a dark neighborhood stays fully responsive", () => {
    // Bright pixel (lx≈+2.3) whose neighborhood is dark (mask lx=-3): the
    // pixel term of max() must keep Highlights working on it.
    const Y = 0.18 * 2 ** 2.3;
    const masked = Math.log2(tonalLuma(Y, P({ highlights: -1 }), -3) / Y);
    expect(masked).toBeLessThan(-1.0);
  });
  it("a bright speckle inside a dark region lifts with the region (shadows min)", () => {
    const Y = 0.18 * 2 ** 1; // brightish pixel, deep-shadow neighborhood
    const masked = Math.log2(tonalLuma(Y, P({ shadows: 1 }), -4) / Y);
    expect(masked).toBeGreaterThan(1.0);
  });
});

describe("whites (display-referred endpoint control, both halves)", () => {
  it("-100 pulls the top back: peak -0.10 at y≈0.87, ~0 by mid gray", () => {
    const b = B({ whites: -100 });
    expect(disp(0.87, b)).toBeCloseTo(-0.10, 1);
    expect(disp(0.87, b)).toBeLessThan(disp(0.5, b));
    expect(Math.abs(disp(0.25, b))).toBeLessThan(0.01);
    expect(basicCurve(0, b)).toBe(0);
    expect(basicCurve(1, b)).toBeCloseTo(1, 6);
  });
  it("+100 moves the clip point: everything above 0.85 on screen clips", () => {
    const b = B({ whites: 100 });
    expect(onScreen(0.87, b)).toBeCloseTo(1, 6);
    expect(onScreen(0.80, b)).toBeLessThan(1);
    expect(basicCurve(0, b)).toBe(0); // black point untouched
  });
  it("+100 displaces as a bump peaked in the upper mids, zero at black", () => {
    // What makes Whites an endpoint control is not that the midtones sit still
    // — measured against ACR they move ~0.21 at +100 — but that the
    // displacement decays to nothing at the *opposite* end while its own end
    // saturates. An affine stretch y/(1-t) has neither property.
    const b = B({ whites: 100 });
    expect(disp(0.05, b)).toBeLessThan(0.01);            // deep shadows untouched
    expect(disp(0.65, b)).toBeGreaterThan(disp(0.35, b));      // ...peaking in the upper mids
    expect(disp(0.65, b)).toBeGreaterThan(disp(0.95, b));
    expect(disp(0.65, b)).toBeCloseTo(0.25, 1);
  });
  it("+100 lifts brights far more than mids (endpoint, not exposure)", () => {
    const b = B({ whites: 100 });
    const liftBright = onScreen(0.75, b) - 0.75;
    const liftShadow = onScreen(0.15, b) - 0.15;
    expect(liftBright).toBeGreaterThan(0.12);
    expect(liftShadow).toBeLessThan(liftBright * 0.25);
  });
  it("is the mirror of the Blacks crush: each end moves, the far end is pinned", () => {
    // Both remaps are weighted toward their own endpoint (the weight pairs are
    // reflections through y -> 1-y), so each slider owns one end of the range
    // and leaves the other exactly alone.
    const w = B({ whites: 100 });
    const b = B({ blacks: -100 });
    expect(basicCurve(0, w)).toBe(0);
    expect(basicCurve(1, b)).toBeCloseTo(1, 9);
    expect(onScreen(0.9, w) - 0.9).toBeGreaterThan(0.09);   // whites owns the top
    expect(0.1 - onScreen(0.1, b)).toBeGreaterThan(0.09);   // blacks owns the bottom
    // ...and each fades out toward the end it does not own.
    expect(onScreen(0.05, w) - 0.05).toBeLessThan(0.01);
    expect(0.95 - onScreen(0.95, b)).toBeLessThan(0.01);
  });
});

describe("basic curve (display-referred contrast + blacks)", () => {
  it("acts on the perceptual axis, not display-linear", () => {
    // Middle gray sits at ~0.46 on screen but ~0.18 in display-linear (the
    // LUT's own axis). Contrast must pivot at the former: a symmetric slider
    // pair leaves mid gray where it was.
    const up = onScreen(0.46, B({ contrast: 100 }));
    const down = onScreen(0.46, B({ contrast: -100 }));
    expect(Math.abs(up - 0.46)).toBeLessThan(0.03);
    expect(Math.abs(down - 0.46)).toBeLessThan(0.03);
    // Were the pivot applied in display-linear, 0.435 linear = 0.70 on screen,
    // and mid gray would be dragged well down by +contrast.
    expect(up).toBeGreaterThan(0.40);
  });
  it("Blacks -100 crushes below 0.235 on screen and pins white", () => {
    const b = B({ blacks: -100 });
    expect(onScreen(0.22, b)).toBe(0);
    expect(onScreen(0.30, b)).toBeGreaterThan(0);
    expect(basicCurve(1, b)).toBeCloseTo(1, 9);
    expect(onScreen(0.435, b)).toBeCloseTo(0.279, 2);
    expect(0.95 - onScreen(0.95, b)).toBeLessThan(0.01); // fades out by white
  });
  it("Blacks +100 lifts the shadows without a flat fog offset", () => {
    // A constant lift (t + y(1-t)) floods pure black to t and flattens the
    // deepest tones against each other. This peaks in the shadows proper and
    // returns to zero at both ends, so black stays black and white is pinned.
    const b = B({ blacks: 100 });
    expect(onScreen(0, b)).toBe(0);
    expect(onScreen(0.27, b) - 0.27).toBeCloseTo(0.085, 2);
    expect(onScreen(0.435, b)).toBeCloseTo(0.506, 2);
    expect(basicCurve(1, b)).toBeCloseTo(1, 9);
  });
  it("Contrast ±100: overlay slope 1+d at the pivot, endpoints pinned", () => {
    const h = 1e-4;
    const slope = (cv: number) =>
      (onScreen(0.435 + h, { contrast: cv, blacks: 0, whites: 0 })
        - onScreen(0.435 - h, { contrast: cv, blacks: 0, whites: 0 })) / (2 * h);
    // Overlay's slope at its pivot is 1+d, with d halved on the negative side.
    expect(slope(100)).toBeCloseTo(2.0, 2);
    expect(slope(-100)).toBeCloseTo(0.5, 2);
    for (const cv of [100, -100]) {
      expect(basicCurve(0, { contrast: cv, blacks: 0, whites: 0 })).toBeCloseTo(0, 9);
      expect(basicCurve(1, { contrast: cv, blacks: 0, whites: 0 })).toBeCloseTo(1, 9);
    }
  });
  it("bakes into the LUT master channel (.a)", () => {
    const lut = buildToneCurveLUT(defaultToneCurve(), B({ blacks: -100 }));
    const crushed = srgbDecode(0.10); // below the 0.12 on-screen crush point
    expect(lut[Math.round(crushed * 2047) * 4 + 3]).toBe(0);
    expect(lut[2047 * 4 + 3]).toBeCloseTo(1, 5); // white pinned
    // Per-channel point curves stay identity — Basic never leaks into them.
    for (const i of [0, 1024, 2047]) expect(lut[i * 4]).toBeCloseTo(i / 2047, 5);
  });
  it("the identity bake is unchanged by the gamma round-trip", () => {
    const lut = buildToneCurveLUT(defaultToneCurve(), DEFAULT_BASIC);
    for (const i of [0, 512, 1024, 2047]) {
      expect(lut[i * 4]).toBeCloseTo(i / 2047, 5);
      expect(lut[i * 4 + 3]).toBeCloseTo(i / 2047, 5);
    }
  });
});

describe("monotonicity (no tone inversion at single-slider extremes)", () => {
  const cases: Array<[string, TonalParams]> = [
    ["exposure +5", P({ exposure: 5 })],
    ["exposure -5", P({ exposure: -5 })],
    ["highlights ±1", P({ highlights: -1 })],
    ["highlights +1", P({ highlights: 1 })],
    ["shadows +1", P({ shadows: 1 })],
    ["shadows -1", P({ shadows: -1 })],
  ];
  for (const [name, p] of cases) {
    it(name, () => {
      let prev = -Infinity;
      for (const Y of WEDGE) {
        const v = tonalLuma(Y, p);
        expect(v).toBeGreaterThanOrEqual(prev);
        prev = v;
      }
    });
  }
  it("basicCurve extremes are monotone non-decreasing", () => {
    for (const basic of [
      B({ contrast: 100 }), B({ contrast: -100 }),
      B({ blacks: -100 }), B({ blacks: 100 }),
      B({ contrast: 100, blacks: -100 }), B({ contrast: -100, blacks: 100 }),
      B({ whites: 100 }), B({ contrast: 100, blacks: -100, whites: 100 }),
    ]) {
      let prev = -Infinity;
      for (let i = 0; i <= 512; i++) {
        const v = basicCurve(i / 512, basic);
        expect(v).toBeGreaterThanOrEqual(prev);
        prev = v;
      }
    }
  });
});

// Response tables for eyeball calibration (globalThis dance: the app tsconfig
// has no node types, and this only ever runs under vitest).
const PRINT_TABLES = (globalThis as { process?: { env: Record<string, string | undefined> } }).process?.env.PRINT_TABLES;
if (PRINT_TABLES) {
  describe("response tables", () => {
    it("prints per-slider EV shifts across the wedge", () => {
      const sliders: Array<[string, TonalParams]> = [
        ["exposure +2", P({ exposure: 2 })],
        ["highlights -1", P({ highlights: -1 })],
        ["highlights +1", P({ highlights: 1 })],
        ["shadows +1", P({ shadows: 1 })],
        ["shadows -1", P({ shadows: -1 })],
      ];
      console.table(WEDGE.map((Y) => {
        const lx = (Math.log2(Y) - LOG2_MID).toFixed(1);
        const row: Record<string, string> = { lx, Y: Y.toFixed(4) };
        for (const [name, p] of sliders) row[name] = evShift(Y, p).toFixed(2);
        return row;
      }));
      console.table([0, 0.05, 0.12, 0.25, 0.435, 0.7, 0.9, 1].map((x) => ({
        x,
        "contrast +100": basicCurve(x, B({ contrast: 100 })).toFixed(3),
        "contrast -100": basicCurve(x, B({ contrast: -100 })).toFixed(3),
        "blacks -100": basicCurve(x, B({ blacks: -100 })).toFixed(3),
        "blacks +100": basicCurve(x, B({ blacks: 100 })).toFixed(3),
        "whites +100": basicCurve(x, B({ whites: 100 })).toFixed(3),
      })));
    });
  });
}

describe("clarity (local mid-tone contrast)", () => {
  it("is zero when the pixel matches its neighborhood", () => {
    for (const lx of [-3, 0, 2]) expect(clarityShift(lx, lx, 1)).toBe(0);
  });

  it("amplifies detail in the slider's direction and scales with it", () => {
    // Pixel half a stop above its neighborhood, mid-gray region.
    expect(clarityShift(0.5, 0, 1)).toBeGreaterThan(0);
    expect(clarityShift(-0.5, 0, 1)).toBeLessThan(0);
    // Negative clarity softens: pushes the pixel toward the neighborhood.
    expect(clarityShift(0.5, 0, -1)).toBeLessThan(0);
    expect(Math.abs(clarityShift(0.5, 0, 0.5))).toBeCloseTo(Math.abs(clarityShift(0.5, 0, 1)) / 2, 9);
  });

  it("suppresses large edges (halo guard)", () => {
    // Per added stop of detail, a 4σ edge must get far less gain than texture.
    const texture = clarityShift(0.5, 0, 1) / 0.5;
    const edge = clarityShift(4 * CLAR_SIGMA, 0, 1) / (4 * CLAR_SIGMA);
    expect(edge).toBeLessThan(texture * 0.05);
  });

  it("fades out away from the midtones", () => {
    expect(clarityShift(0.5, CLAR_MID1 + 1, 1)).toBeCloseTo(0, 12);
    expect(clarityShift(0.5, -(CLAR_MID1 + 1), 1)).toBeCloseTo(0, 12);
  });
});

describe("monotonicity in the degenerate case (smooth gradients)", () => {
  // Where a pixel equals its blurred neighborhood — a clear sky, any smooth
  // ramp — the amount and the value move together, so the composite map is not
  // monotone just because the operator is. This is the sweep that has to hold:
  // it is what pins TONE_K/TONE_N and what forced Whites out of the shader.
  it("no tone inversion across every slider pair at any exposure", () => {
    const S = [-1, -0.5, 0, 0.5, 1];
    for (const highlights of S) {
      for (const shadows of S) {
        for (const exposure of [-2, -1, 0, 1, 2]) {
          // Assert only on failure: an expect() per sample makes the sweep
          // (125 combos x 1500 samples) time out.
          let peak = -Infinity;
          let bad = "";
          for (let i = 0; i <= 1500 && !bad; i++) {
            const Y = 1e-5 * 10 ** ((i / 1500) * 6.5); // 1e-5 .. ~30
            const v = tonalLuma(Y, { exposure, highlights, shadows });
            if (!Number.isFinite(v) || v < peak - 1e-9) {
              bad = `hi=${highlights} sh=${shadows} ev=${exposure} Y=${Y}: ${v} after peak ${peak}`;
            }
            peak = Math.max(peak, v);
          }
          expect(bad).toBe("");
        }
      }
    }
  });
});

describe("basicCurve monotonicity (display-referred half)", () => {
  // The Whites/Blacks bumps and the overlay contrast all reshape the same
  // range, and the Whites clip point is solved for rather than clamped — so
  // sweep the combinations densely enough to catch a reversal a coarse grid
  // would step over.
  it("holds at 4k samples for every slider combination", () => {
    for (const whites of [-100, -40, 0, 40, 100]) {
      for (const blacks of [-100, -40, 0, 40, 100]) {
        for (const contrast of [-100, -50, 0, 50, 100]) {
          let prev = -1;
          let bad = "";
          for (let i = 0; i <= 4000 && !bad; i++) {
            const v = basicCurve(i / 4000, { contrast, blacks, whites });
            if (!(v >= prev - 1e-12)) bad = `w=${whites} b=${blacks} c=${contrast} at ${i / 4000}: ${v} < ${prev}`;
            prev = v;
          }
          expect(bad).toBe("");
        }
      }
    }
  });
});
