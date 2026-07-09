/**
 * Calibration harness for the PV2012-style tonal model: runs a synthetic gray
 * step wedge through the TS mirror (tonal-model.ts, scene-referred sliders)
 * and the display-referred basic curve (curve.ts), asserting the acceptance
 * targets the constants were tuned against. Run with `pnpm test`; to dump the
 * per-slider response tables for recalibration:
 *   PRINT_TABLES=1 npx vitest run --disable-console-intercept
 */
import { describe, expect, it } from "vitest";
import { LOG2_MID, LX_WHITE, expoShoulder, tonalLuma, type TonalParams } from "../tonal-model";
import { basicCurve, buildToneCurveLUT, defaultToneCurve, srgbDecode, srgbEncode } from "../curve";

// basicCurve is linear-in/linear-out but acts on the perceptual axis, so the
// thresholds below are stated where they are meant: on screen.
const onScreen = (e: number, basic: { contrast: number; blacks: number; whites: number }): number =>
  srgbEncode(basicCurve(srgbDecode(e), basic));

const ZERO: TonalParams = { exposure: 0, highlights: 0, shadows: 0, whites: 0 };
const P = (over: Partial<TonalParams>): TonalParams => ({ ...ZERO, ...over });

/** EV shift the tonal model applies at scene luminance Y. */
const evShift = (Y: number, p: TonalParams): number => Math.log2(tonalLuma(Y, p) / Y);

// Wedge probes (scene-linear luminance): deep shadow lx=-4, mid gray, diffuse white.
const DEEP = 0.18 * 2 ** -4;
const MID = 0.18;
const WHITE = 1.0;

// lx = -6 … +4 in half stops.
const WEDGE = Array.from({ length: 21 }, (_, i) => 0.18 * 2 ** (-6 + i * 0.5));

const EV_TOL = 0.15;

describe("identity", () => {
  it("all-zero params leave the wedge untouched", () => {
    for (const Y of WEDGE) expect(tonalLuma(Y, ZERO)).toBeCloseTo(Y, 6);
  });
  it("zero basic adjust is the identity curve", () => {
    for (const x of [0, 0.12, 0.435, 0.8, 1]) {
      expect(basicCurve(x, { contrast: 0, blacks: 0, whites: 0 })).toBeCloseTo(x, 9);
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
  it("Highlights -100: ~-1.3 EV at diffuse white, ~-0.1 EV at mid gray, ~0 in deep shadow", () => {
    expect(evShift(WHITE, P({ highlights: -1 }))).toBeGreaterThan(-1.45);
    expect(evShift(WHITE, P({ highlights: -1 }))).toBeLessThan(-1.1);
    expect(Math.abs(evShift(MID, P({ highlights: -1 })))).toBeLessThan(EV_TOL);
    expect(Math.abs(evShift(DEEP, P({ highlights: -1 })))).toBeLessThan(0.02);
  });
  it("Shadows +100: ~+1.8 EV in deep shadow, small at mid gray, ~0 at white", () => {
    expect(evShift(DEEP, P({ shadows: 1 }))).toBeCloseTo(1.8, 1);
    expect(Math.abs(evShift(MID, P({ shadows: 1 })))).toBeLessThan(EV_TOL);
    expect(Math.abs(evShift(WHITE, P({ shadows: 1 })))).toBeLessThan(0.02);
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
    expect(masked).toBeGreaterThan(1.5);
  });
});

describe("whites (endpoint control, split across domains)", () => {
  // Negative half is scene-referred (headroom recovery); positive half is the
  // display-referred white-point scale in basicCurve. A scene-referred gain
  // cannot move the clip point: the profile tone curve asymptotes below 1.0.
  it("-100: -1.35 EV at diffuse white, recovers scene Y≈2.5 below the clamp", () => {
    expect(evShift(WHITE, P({ whites: -1 }))).toBeCloseTo(-1.35, 2);
    expect(tonalLuma(2.5, P({ whites: -1 }))).toBeLessThan(1);
  });
  it("+100 is inert in the scene-referred block", () => {
    for (const Y of WEDGE) expect(tonalLuma(Y, P({ whites: 1 }))).toBeCloseTo(Y, 6);
  });
  it("+100 moves the clip point: everything above 0.85 on screen clips", () => {
    const b = { contrast: 0, blacks: 0, whites: 100 };
    expect(onScreen(0.87, b)).toBeCloseTo(1, 6);
    expect(onScreen(0.80, b)).toBeLessThan(1);
    expect(onScreen(0.80, b)).toBeCloseTo(0.8 / 0.85, 4);
    expect(basicCurve(0, b)).toBe(0); // black point untouched
  });
  it("+100 lifts brights far more than mids (endpoint, not exposure)", () => {
    const b = { contrast: 0, blacks: 0, whites: 100 };
    const liftBright = onScreen(0.75, b) - 0.75;
    const liftMid = onScreen(0.46, b) - 0.46;
    expect(liftBright).toBeGreaterThan(0.12);
    expect(liftMid).toBeLessThan(liftBright * 0.7);
  });
  it("is the exact mirror of the Blacks crush", () => {
    // x/(1-t) is (x-t)/(1-t) reflected through x -> 1-x.
    const w = { contrast: 0, blacks: 0, whites: 100 };
    const b = { contrast: 0, blacks: -100, whites: 0 };
    for (const e of [0.1, 0.5, 0.9]) {
      expect(onScreen(e, w)).toBeCloseTo(Math.min(e / 0.85, 1), 5);
      expect(onScreen(1 - e, b)).toBeCloseTo(Math.max((1 - e - 0.12) / 0.88, 0), 5);
    }
  });
});

describe("basic curve (display-referred contrast + blacks)", () => {
  it("acts on the perceptual axis, not display-linear", () => {
    // Middle gray sits at ~0.46 on screen but ~0.18 in display-linear (the
    // LUT's own axis). Contrast must pivot at the former: a symmetric slider
    // pair leaves mid gray where it was.
    const up = onScreen(0.46, { contrast: 100, blacks: 0, whites: 0 });
    const down = onScreen(0.46, { contrast: -100, blacks: 0, whites: 0 });
    expect(Math.abs(up - 0.46)).toBeLessThan(0.03);
    expect(Math.abs(down - 0.46)).toBeLessThan(0.03);
    // Were the pivot applied in display-linear, 0.435 linear = 0.70 on screen,
    // and mid gray would be dragged well down by +contrast.
    expect(up).toBeGreaterThan(0.40);
  });
  it("Blacks -100 crushes below 0.12 on screen and pins white", () => {
    const b = { contrast: 0, blacks: -100, whites: 0 };
    expect(onScreen(0.11, b)).toBe(0);
    expect(onScreen(0.13, b)).toBeGreaterThan(0);
    expect(basicCurve(1, b)).toBeCloseTo(1, 9);
    expect(onScreen(0.435, b)).toBeCloseTo(0.358, 2);
  });
  it("Blacks +100 lifts to a fog floor of 0.08 on screen and pins white", () => {
    const b = { contrast: 0, blacks: 100, whites: 0 };
    expect(onScreen(0, b)).toBeCloseTo(0.08, 6);
    expect(onScreen(0.435, b)).toBeCloseTo(0.48, 2);
    expect(basicCurve(1, b)).toBeCloseTo(1, 9);
  });
  it("Contrast ±100: pivot slope ×1.45 / ÷1.45, endpoints pinned", () => {
    const h = 1e-4;
    const slope = (cv: number) =>
      (onScreen(0.435 + h, { contrast: cv, blacks: 0, whites: 0 })
        - onScreen(0.435 - h, { contrast: cv, blacks: 0, whites: 0 })) / (2 * h);
    expect(slope(100)).toBeCloseTo(1.45, 2);
    expect(slope(-100)).toBeCloseTo(1 / 1.45, 2);
    for (const cv of [100, -100]) {
      expect(basicCurve(0, { contrast: cv, blacks: 0, whites: 0 })).toBeCloseTo(0, 9);
      expect(basicCurve(1, { contrast: cv, blacks: 0, whites: 0 })).toBeCloseTo(1, 9);
    }
  });
  it("bakes into the LUT chain head", () => {
    const lut = buildToneCurveLUT(defaultToneCurve(), { contrast: 0, blacks: -100, whites: 0 });
    const crushed = srgbDecode(0.10); // below the 0.12 on-screen crush point
    expect(lut[Math.round(crushed * 2047) * 3]).toBe(0);
    expect(lut[2047 * 3]).toBeCloseTo(1, 5); // white pinned
  });
  it("the identity bake is unchanged by the gamma round-trip", () => {
    const lut = buildToneCurveLUT(defaultToneCurve(), { contrast: 0, blacks: 0, whites: 0 });
    for (const i of [0, 512, 1024, 2047]) expect(lut[i * 3]).toBeCloseTo(i / 2047, 5);
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
    ["whites +1", P({ whites: 1 })],
    ["whites -1", P({ whites: -1 })],
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
      { contrast: 100, blacks: 0, whites: 0 }, { contrast: -100, blacks: 0, whites: 0 },
      { contrast: 0, blacks: -100, whites: 0 }, { contrast: 0, blacks: 100, whites: 0 },
      { contrast: 100, blacks: -100, whites: 0 }, { contrast: -100, blacks: 100, whites: 0 },
      { contrast: 0, blacks: 0, whites: 100 }, { contrast: 100, blacks: -100, whites: 100 },
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
        ["whites +1", P({ whites: 1 })],
        ["whites -1", P({ whites: -1 })],
      ];
      console.table(WEDGE.map((Y) => {
        const lx = (Math.log2(Y) - LOG2_MID).toFixed(1);
        const row: Record<string, string> = { lx, Y: Y.toFixed(4) };
        for (const [name, p] of sliders) row[name] = evShift(Y, p).toFixed(2);
        return row;
      }));
      console.table([0, 0.05, 0.12, 0.25, 0.435, 0.7, 0.9, 1].map((x) => ({
        x,
        "contrast +100": basicCurve(x, { contrast: 100, blacks: 0, whites: 0 }).toFixed(3),
        "contrast -100": basicCurve(x, { contrast: -100, blacks: 0, whites: 0 }).toFixed(3),
        "blacks -100": basicCurve(x, { contrast: 0, blacks: -100, whites: 0 }).toFixed(3),
        "blacks +100": basicCurve(x, { contrast: 0, blacks: 100, whites: 0 }).toFixed(3),
        "whites +100": basicCurve(x, { contrast: 0, blacks: 0, whites: 100 }).toFixed(3),
      })));
      expect(LX_WHITE).toBeGreaterThan(0);
    });
  });
}
