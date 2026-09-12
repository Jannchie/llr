import { describe, expect, it } from "vitest";
import {
  applyCameraMatch,
  applyCameraMatchLab,
  cameraMatchInterp,
  cameraMatchInterpHue,
  labToSrgb,
  parseCameraMatch,
  srgbToLab,
} from "../camera-match";
import type { ProfileCameraMatch } from "../camera-match";

/**
 * Pinned to the reference implementation, sony_repro/tools/camera_match_fit.py
 * apply(): the same Lab points through the same shipped ILCE-7CM2 tables give
 * the same numbers in apps/worker/tests/test_camera_match.py, which regenerates
 * them from the reference. The tables are copied from
 * apps/worker/src/llr_worker/sony/data/camera_match.json — if the fit is
 * re-run, both copies and the expectations move together.
 */

const FL: ProfileCameraMatch = {
  dL: [0.2167, 0.1505, -0.1381, -0.2949, -0.6854, -1.0674, -1.6454, -1.4759, -0.9132, -0.6274],
  cr: [0.9712, 0.9863, 0.9801, 0.9758, 0.9798, 0.9689, 0.9817, 0.9838, 0.9972, 1.0423],
  hs: [2.8857, -0.2237, -0.7519, 1.1028, -1.213, -1.1988, -0.7629, -1.5364, -1.3411, -4.0, -1.0642, 2.5388],
};
const POOLED: ProfileCameraMatch = {
  dL: [0.2382, 0.121, -0.275, -0.4847, -1.1816, -1.4789, -1.3133, -1.1024, -0.2027, 0.4929],
  cr: [0.9679, 0.9658, 0.9633, 0.9619, 0.9683, 0.9691, 0.9802, 0.9844, 0.9854, 0.9619],
  hs: [1.8095, -0.8041, -1.085, 1.1736, -1.0055, -0.6918, -0.1714, -1.5364, -1.3629, -0.9889, -1.0642, 2.5388],
};
const IN: ProfileCameraMatch = {
  dL: [0.5299, 0.4804, 0.3573, 0.2689, 0.1352, 0.0699, 0.1012, 0.6013, 1.9304, 3.0087],
  cr: [0.9679, 0.9335, 0.9497, 0.9559, 0.9521, 0.9647, 0.9716, 0.974, 0.9072, 0.9619],
  hs: [1.5354, -1.1183, -2.1287, 1.1232, -0.6682, -0.6918, -0.1714, -1.3429, -2.2815, -1.2453, -0.5366, 1.3596],
};

const LAB_POINTS = [
  [50.0, 20.0, 10.0],
  [12.0, -30.0, 25.0],
  [97.0, 5.0, -40.0],
  [3.0, 0.5, -0.2],
  [60.0, -25.0, -25.0],
  [85.0, 40.0, 60.0],
];
const FL_EXPECTED = [
  [49.1236, 19.19171, 10.31297],
  [12.17036, -28.928, 25.16101],
  [96.3726, 2.80141, -41.92296],
  [3.2167, 0.49122, -0.17956],
  [58.6436, -25.02748, -23.71999],
  [84.0868, 40.32846, 59.53601],
];

const close = (got: readonly number[], want: readonly number[], tol: number) => {
  for (let i = 0; i < want.length; i++) expect(got[i]).toBeCloseTo(want[i], -Math.log10(tol));
};

describe("applyCameraMatchLab", () => {
  it("matches camera_match_fit.apply() on the FL table", () => {
    LAB_POINTS.forEach((p, k) => close(applyCameraMatchLab(p, FL), FL_EXPECTED[k], 1e-4));
  });

  it("picks the table it is given, not always FL", () => {
    close(applyCameraMatchLab(LAB_POINTS[0], POOLED), [48.66975, 19.23652, 9.95721], 1e-4);
    close(applyCameraMatchLab(LAB_POINTS[0], IN), [50.10255, 19.08153, 9.75503], 1e-4);
  });

  it("leaves a neutral neutral", () => {
    const [L, a, b] = applyCameraMatchLab([40, 0, 0], FL);
    expect(a).toBe(0);
    expect(b).toBe(0);
    expect(L).toBeCloseTo(40 + cameraMatchInterp(FL.dL, 40), 10);
  });
});

describe("cameraMatchInterp", () => {
  const ramp = Array.from({ length: 10 }, (_, i) => i);
  it("is the band value at a centre and linear between", () => {
    expect(cameraMatchInterp(ramp, 5)).toBe(0);
    expect(cameraMatchInterp(ramp, 95)).toBe(9);
    expect(cameraMatchInterp(ramp, 20)).toBeCloseTo(1.5, 12);
  });
  it("holds the end values past the outer centres (np.interp)", () => {
    expect(cameraMatchInterp(ramp, 0)).toBe(0);
    expect(cameraMatchInterp(ramp, -5)).toBe(0);
    expect(cameraMatchInterp(ramp, 100)).toBe(9);
    expect(cameraMatchInterp(ramp, 130)).toBe(9);
  });
});

describe("cameraMatchInterpHue", () => {
  const ramp = Array.from({ length: 12 }, (_, i) => i);
  it("is the sector value at a centre", () => {
    expect(cameraMatchInterpHue(ramp, 15)).toBe(0);
    expect(cameraMatchInterpHue(ramp, 345)).toBe(11);
  });
  it("wraps across 360: the last sector interpolates into the first", () => {
    // Midway between 345 (value 11) and 15 (value 0) is 360 == 0.
    expect(cameraMatchInterpHue(ramp, 0)).toBeCloseTo(5.5, 12);
    expect(cameraMatchInterpHue(ramp, 360)).toBeCloseTo(5.5, 12);
    expect(cameraMatchInterpHue(ramp, 5)).toBeCloseTo(11 + (0 - 11) * (2 / 3), 12);
    expect(cameraMatchInterpHue(ramp, -15)).toBe(11);
  });
});

describe("srgbToLab / labToSrgb", () => {
  // quant.py srgb_to_lab on the same 8-bit pixels.
  const PIXELS: [number[], number[]][] = [
    [[128, 64, 200], [41.88532, 53.52323, -60.35832]],
    [[10, 10, 10], [2.74174, 0, 0]],
    [[250, 240, 230], [95.31155, 1.67744, 6.02212]],
    [[30, 180, 90], [64.6873, -56.98087, 35.40803]],
  ];
  it("is quant.py's conversion", () => {
    for (const [px, lab] of PIXELS) close(srgbToLab(px.map(v => v / 255)), lab, 1e-4);
  });
  it("round-trips", () => {
    for (const [px] of PIXELS) close(labToSrgb(srgbToLab(px.map(v => v / 255))), px.map(v => v / 255), 1e-9);
  });
  it("clamps what the correction pushes out of gamut", () => {
    const out = labToSrgb([100, 80, 80]);
    expect(out.every(v => v >= 0 && v <= 1)).toBe(true);
  });
});

describe("applyCameraMatch", () => {
  it("moves a mid-tone pixel by the table's few L* and stays in range", () => {
    const px = [0.5, 0.4, 0.3];
    const out = applyCameraMatch(px, FL);
    expect(out.every(v => v >= 0 && v <= 1)).toBe(true);
    const before = srgbToLab(px), after = srgbToLab(out);
    expect(after[0] - before[0]).toBeCloseTo(cameraMatchInterp(FL.dL, before[0]), 5);
  });
});

describe("parseCameraMatch", () => {
  it("accepts the worker's shape and rejects anything else", () => {
    expect(parseCameraMatch(FL)).toEqual(FL);
    expect(parseCameraMatch(null)).toBeNull();
    expect(parseCameraMatch({ dL: FL.dL, cr: FL.cr })).toBeNull();
    expect(parseCameraMatch({ ...FL, hs: FL.hs.slice(1) })).toBeNull();
    expect(parseCameraMatch({ ...FL, dL: [...FL.dL.slice(1), NaN] })).toBeNull();
  });
});
