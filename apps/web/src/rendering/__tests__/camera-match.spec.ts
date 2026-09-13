import { describe, expect, it } from "vitest";
import shippedJson from "../../../../worker/src/llr_worker/sony/data/camera_match.json?raw";
import {
  CAMERA_MATCH_C_STEPS,
  CAMERA_MATCH_H_SECTORS,
  CAMERA_MATCH_L_STEPS,
  applyCameraMatch,
  applyCameraMatchLab,
  cameraMatchBilinear,
  cameraMatchGridTexels,
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
 * them from the reference. The tables are read straight from the worker's
 * data file rather than copied — 2 x 21 x 19 numbers per look is past what a
 * copy should carry — so the two tests can only ever see the same table.
 */
const SHIPPED = JSON.parse(shippedJson)["ILCE-7CM2"] as Record<string, unknown>;
const table = (look: string): ProfileCameraMatch => {
  const t = parseCameraMatch(SHIPPED[look]);
  if (!t) throw new Error(`camera_match.json: no ${look} table`);
  return t;
};
const FL = table("FL"), POOLED = table("*"), IN = table("IN");

const LAB_POINTS = [
  [50.0, 20.0, 10.0],
  [12.0, -30.0, 25.0],
  [97.0, 5.0, -40.0],
  [3.0, 0.5, -0.2],
  [60.0, -25.0, -25.0],
  [85.0, 40.0, 60.0],
  [55.0, 80.0, 60.0], // C* = 100, past the grid's last column
  [50.0, 30.0, -3.0], // hue 354.3, past the last sector centre
];
const FL_EXPECTED = [
  [48.57621, 18.86754, 9.93076],
  [11.74036, -29.05936, 25.01010],
  [94.35281, 2.72001, -39.98884],
  [2.81830, 0.44942, -0.18345],
  [58.30801, -25.54183, -23.41175],
  [83.68644, 40.44031, 58.41276],
  [54.77280, 80.65485, 61.22404],
  [48.57830, 29.14034, -2.41781],
];

const close = (got: readonly number[], want: readonly number[], tol: number) => {
  for (let i = 0; i < want.length; i++) expect(got[i]).toBeCloseTo(want[i], -Math.log10(tol));
};

describe("the shipped table", () => {
  it("is on the grid the mirror and the shader assume", () => {
    expect(SHIPPED.lAxis).toEqual(Array.from({ length: CAMERA_MATCH_L_STEPS }, (_, i) => 5 * i));
    expect(SHIPPED.cAxis).toEqual(Array.from({ length: CAMERA_MATCH_C_STEPS }, (_, i) => 5 * i));
    expect(SHIPPED.hAxis).toEqual(Array.from({ length: CAMERA_MATCH_H_SECTORS }, (_, i) => 7.5 + 15 * i));
  });
});

describe("applyCameraMatchLab", () => {
  it("matches camera_match_fit.apply() on the FL table", () => {
    LAB_POINTS.forEach((p, k) => close(applyCameraMatchLab(p, FL), FL_EXPECTED[k], 1e-4));
  });

  it("picks the table it is given, not always FL", () => {
    close(applyCameraMatchLab(LAB_POINTS[0], POOLED), [48.25873, 18.95516, 9.65470], 1e-4);
    close(applyCameraMatchLab(LAB_POINTS[0], IN), [49.47538, 18.57796, 9.36519], 1e-4);
  });

  it("leaves a neutral neutral", () => {
    const [L, a, b] = applyCameraMatchLab([40, 0, 0], FL);
    expect(a).toBe(0);
    expect(b).toBe(0);
    expect(L).toBeCloseTo(40 + cameraMatchBilinear(FL.dL, 40, 0), 10);
  });
});

describe("cameraMatchBilinear", () => {
  // value = 100 * row + column, so any sample reads back its own coordinates.
  const ramp = Array.from({ length: CAMERA_MATCH_L_STEPS }, (_, i) =>
    Array.from({ length: CAMERA_MATCH_C_STEPS }, (_, j) => 100 * i + j));
  it("is the grid value at a grid point and bilinear between", () => {
    expect(cameraMatchBilinear(ramp, 0, 0)).toBe(0);
    expect(cameraMatchBilinear(ramp, 50, 30)).toBe(1006);
    expect(cameraMatchBilinear(ramp, 100, 90)).toBe(2018);
    expect(cameraMatchBilinear(ramp, 52.5, 31)).toBeCloseTo(1050 + 6.2, 10);
  });
  it("holds the edge past the grid (the reference clamps the index)", () => {
    expect(cameraMatchBilinear(ramp, 50, 120)).toBe(1018);
    expect(cameraMatchBilinear(ramp, 130, 30)).toBe(2006);
    expect(cameraMatchBilinear(ramp, -3, -3)).toBe(0);
  });
  it("indexes rows by L* and columns by C*, not the other way round", () => {
    expect(cameraMatchBilinear(ramp, 5, 10)).toBe(102);
    expect(cameraMatchBilinear(ramp, 10, 5)).toBe(201);
  });
});

describe("cameraMatchInterpHue", () => {
  const ramp = Array.from({ length: CAMERA_MATCH_H_SECTORS }, (_, i) => i);
  it("is the sector value at a centre", () => {
    expect(cameraMatchInterpHue(ramp, 7.5)).toBe(0);
    expect(cameraMatchInterpHue(ramp, 352.5)).toBe(23);
    expect(cameraMatchInterpHue(ramp, 30)).toBeCloseTo(1.5, 12);
  });
  it("wraps across 360: the last sector interpolates into the first", () => {
    // Midway between 352.5 (value 23) and 7.5 (value 0) is 360 == 0.
    expect(cameraMatchInterpHue(ramp, 0)).toBeCloseTo(11.5, 12);
    expect(cameraMatchInterpHue(ramp, 360)).toBeCloseTo(11.5, 12);
    expect(cameraMatchInterpHue(ramp, 2.5)).toBeCloseTo(23 + (0 - 23) * (2 / 3), 12);
    expect(cameraMatchInterpHue(ramp, -7.5)).toBe(23);
  });
});

describe("cameraMatchGridTexels", () => {
  it("lays the grid out C* along x, L* along y, dL in R and cr in G", () => {
    const texels = cameraMatchGridTexels(FL);
    expect(texels.length).toBe(CAMERA_MATCH_L_STEPS * CAMERA_MATCH_C_STEPS * 2);
    const i = 7, j = 3;
    const k = (i * CAMERA_MATCH_C_STEPS + j) * 2;
    expect(texels[k]).toBeCloseTo(FL.dL[i][j], 6);
    expect(texels[k + 1]).toBeCloseTo(FL.cr[i][j], 6);
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
    expect(after[0] - before[0]).toBeCloseTo(cameraMatchBilinear(FL.dL, before[0], Math.hypot(before[1], before[2])), 5);
  });
});

describe("parseCameraMatch", () => {
  it("accepts the worker's shape and rejects anything else", () => {
    expect(parseCameraMatch(FL)).toEqual(FL);
    expect(parseCameraMatch(null)).toBeNull();
    expect(parseCameraMatch({ dL: FL.dL, cr: FL.cr })).toBeNull();
    expect(parseCameraMatch({ ...FL, hs: FL.hs.slice(1) })).toBeNull();
    expect(parseCameraMatch({ ...FL, dL: FL.dL.slice(1) })).toBeNull();
    expect(parseCameraMatch({ ...FL, cr: [...FL.cr.slice(0, -1), FL.cr[0].slice(1)] })).toBeNull();
    expect(parseCameraMatch({ ...FL, dL: [...FL.dL.slice(0, -1), [...FL.dL[0].slice(1), NaN]] })).toBeNull();
    // The previous fit's one-dimensional bands are not this shape.
    expect(parseCameraMatch({ dL: FL.dL[0], cr: FL.cr[0], hs: FL.hs })).toBeNull();
  });
});
