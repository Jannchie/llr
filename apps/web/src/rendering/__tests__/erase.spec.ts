import { describe, expect, it } from "vitest";
import { defaultCrop, type CropState } from "../crop";
import { cloneErase, eraseLines, eraseReach, eraseWire, lensForward, lensInverse, lensWarp, strokeToView, viewToStroke } from "../erase";
import { LENS_IDENTITY } from "../lens";

// The FE 50-150mm F2 GM at 150mm (lens.spec.ts SONY_DIST): a real pincushion.
const SONY_DIST = [0, 3, 12, 26, 48, 74, 107, 145, 190, 240, 295, 358, 427, 502, 586, 677].map(p => p * 2 ** -14 + 1);
const W = 6000, H = 4000;

describe("lens warp", () => {
  it("is the identity without correction tables", () => {
    const w = lensWarp(undefined, undefined, undefined, W, H);
    const [u, v] = lensForward(w, 0.13, 0.91);
    expect(u).toBeCloseTo(0.13, 12);
    expect(v).toBeCloseTo(0.91, 12);
    expect(w.scale).toBe(1);
  });

  it("mirrors the shader: the frame centre stays, the border fetches inside the recorded frame", () => {
    const w = lensWarp(SONY_DIST, undefined, undefined, W, H);
    const [cu, cv] = lensForward(w, 0.5, 0.5);
    expect(cu).toBeCloseTo(0.5, 12);
    expect(cv).toBeCloseTo(0.5, 12);
    // The fill scale exists so that no border point samples outside the frame.
    for (const [u, v] of [[0, 0], [1, 0], [0, 1], [1, 1], [0.5, 0], [0, 0.5]]) {
      const [fu, fv] = lensForward(w, u, v);
      expect(fu).toBeGreaterThanOrEqual(-1e-9);
      expect(fu).toBeLessThanOrEqual(1 + 1e-9);
      expect(fv).toBeGreaterThanOrEqual(-1e-9);
      expect(fv).toBeLessThanOrEqual(1 + 1e-9);
    }
  });

  it("inverts", () => {
    const w = lensWarp(SONY_DIST, undefined, undefined, W, H);
    for (const [u, v] of [[0.1, 0.2], [0.95, 0.05], [0.5, 0.99], [0.3, 0.7]]) {
      const [fu, fv] = lensForward(w, u, v);
      const [bu, bv] = lensInverse(w, fu, fv);
      expect(bu).toBeCloseTo(u, 9);
      expect(bv).toBeCloseTo(v, 9);
    }
  });
});

describe("view <-> stroke mapping", () => {
  const crops: CropState[] = [];
  for (const orientation of [0, 90, 180, 270] as const) {
    for (const flipH of [false, true]) crops.push({ ...defaultCrop(), orientation, flipH, flipV: !flipH && orientation === 90 });
  }

  it("round-trips through every orientation and flip, with and without the lens", () => {
    for (const dist of [LENS_IDENTITY, SONY_DIST]) {
      const w = lensWarp(dist, undefined, undefined, W, H);
      for (const c of crops) {
        for (const [x, y] of [[0.2, 0.3], [0.8, 0.1], [0.5, 0.5]]) {
          const [u, v] = viewToStroke(x, y, c, w);
          const [bx, by] = strokeToView(u, v, c, w);
          expect(bx).toBeCloseTo(x, 9);
          expect(by).toBeCloseTo(y, 9);
        }
      }
    }
  });

  it("lands a 90-degree view's top-left on the source's bottom-left", () => {
    // Rotated 90 clockwise for display, the view's top-left is the source's
    // bottom-left corner: the stroke stays on the pixels, not on the screen.
    const w = lensWarp(undefined, undefined, undefined, W, H);
    const [u, v] = viewToStroke(0, 0, { ...defaultCrop(), orientation: 90 }, w);
    expect(u).toBeCloseTo(0, 12);
    expect(v).toBeCloseTo(1, 12);
  });
});

describe("stroke data", () => {
  it("clones deeply and sends no ids", () => {
    const s = [{ id: "a", radius: 0.01, points: [[0.1, 0.2]] as [number, number][] }];
    const c = cloneErase(s);
    c[0].points[0][0] = 0.9;
    expect(s[0].points[0][0]).toBe(0.1);
    expect(eraseWire(s)).toEqual([{ points: [[0.1, 0.2]], radius: 0.01 }]);
  });

  it("carries a stroke's further lines, deep-cloned, and lists them after the first", () => {
    const s = [{ id: "a", radius: 0.01, points: [[0.1, 0.2]] as [number, number][],
      more: [{ radius: 0.02, points: [[0.3, 0.4]] as [number, number][] }] }];
    const c = cloneErase(s);
    c[0].more![0].points[0][0] = 0.9;
    expect(s[0].more[0].points[0][0]).toBe(0.3);
    expect(eraseLines(s[0]).map(l => l.radius)).toEqual([0.01, 0.02]);
    expect(eraseWire(s)).toEqual([{ points: [[0.1, 0.2]], radius: 0.01, more: [{ points: [[0.3, 0.4]], radius: 0.02 }] }]);
  });

  it("carries grow and feather only where the stroke has them", () => {
    const line = { radius: 0.01, points: [[0.1, 0.2]] as [number, number][] };
    const soft = { id: "s", ...line, grow: 0.2, feather: 0, more: [{ radius: 0.03, points: [[0.3, 0.4]] as [number, number][] }] };
    expect(cloneErase([soft])[0]).toMatchObject({ grow: 0.2, feather: 0 });
    expect(eraseWire([soft])[0]).toMatchObject({ grow: 0.2, feather: 0 });
    expect(eraseWire([{ id: "l", ...line }])[0]).toEqual({ points: [[0.1, 0.2]], radius: 0.01 });
    // Reach is scaled by the stroke's largest radius, not its first.
    expect(eraseReach(soft)).toBeCloseTo(0.2 * 0.03, 12);
    expect(eraseReach({ ...line })).toBe(0);
  });
});
