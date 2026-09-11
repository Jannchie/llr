import { describe, expect, it } from "vitest";
import { gradingTint } from "../grading";
import {
  ADJUST_KEYS, GROUP_STRIDE, MASK_COMPS, MASK_GLSL, MASK_GROUPS, MASK_PRESETS, MASK_TEMP_PER_UNIT, MASK_USE,
  componentWeight, defaultAdjust, defaultComponent, groupWeight, imgFromTex, packMasks, presetGroup,
  type MaskComponent, type MaskGroup, type MaskPresetName,
} from "../masks";
import { computeWbMatrix } from "../color-spaces";
import { HSL_CENTERS } from "../hsl-bands";
import { defaultCrop, imageNormToTexcoord, type CropState, type Orientation } from "../crop";

const W = 6000, H = 4000, ASPECT = H / W;
const WB = { temperature: 6500, tint: 0 };
const GREY: [number, number, number] = [0.6, 0, 0];
const BLUE: [number, number, number] = [0.6, 0.1 * Math.cos(HSL_CENTERS[5]), 0.1 * Math.sin(HSL_CENTERS[5])]; // Oklab, on the HSL blue centre

function group(components: MaskComponent[], adjust: Partial<MaskGroup["adjust"]> = { exposure: 1 }, extra: Partial<MaskGroup> = {}): MaskGroup {
  return { id: "g", enabled: true, invert: false, components, adjust: { ...defaultAdjust(), ...adjust }, ...extra };
}
const lum = (lo: number, hi: number, featherLo = 10, featherHi = 10): MaskComponent =>
  ({ type: "luminance", op: "add", invert: false, lo, hi, featherLo, featherHi });

/** Rows of group `n` as a plain array. */
function rows(packed: Float32Array, n: number): number[] {
  return Array.from(packed.subarray(n * GROUP_STRIDE * 4, (n + 1) * GROUP_STRIDE * 4));
}

describe("packMasks layout", () => {
  it("writes the header, the scaled deltas and the component rows where the shader reads them", () => {
    const g = group([lum(20, 80), { ...defaultComponent("radial"), op: "intersect", invert: true }],
      { exposure: 0.5, highlights: -30, shadows: 25, clarity: 10, dehaze: 5, saturation: 40, vibrance: -20, hue: 50 }, { invert: true });
    const p = packMasks([g], defaultCrop(), W, H, WB);
    expect(p.maskGroups).toBe(1);
    expect(p.imgAspect).toBeCloseTo(ASPECT);
    const r = rows(p.masks, 0);
    expect(r.slice(0, 4)).toEqual([2, 1, 0, 0]);
    expect(r.slice(4, 8).map(v => +v.toFixed(6))).toEqual([0.5, -0.3, 0.25, 0.1]);
    expect(r.slice(8, 12).map(v => +v.toFixed(6))).toEqual([0.05, 0.4, -0.2, 0.25]);
    expect(r.slice(12, 24)).toEqual(Array(12).fill(0)); // no WB delta
    // component 0: luminance, add, no invert; params /100
    expect(r.slice(24, 28)).toEqual([0, 0, 0, 0]);
    expect(r.slice(28, 32).map(v => +v.toFixed(6))).toEqual([0.2, 0.8, 0.1, 0.1]);
    // component 1: radial, intersect, invert; angle in radians
    expect(r.slice(36, 40)).toEqual([3, 2, 1, 0]);
    expect(r[44]).toBeCloseTo(0.5); // feather
    expect(r[45]).toBe(0);          // angle 0
    expect(p.maskUse).toBe(MASK_USE.exposure | MASK_USE.tonal | MASK_USE.clarity | MASK_USE.dehaze | MASK_USE.color);
  });

  it("drops groups that cannot change a pixel, keeps the previewed one", () => {
    const off = group([lum(0, 50)], { exposure: 1 }, { id: "off", enabled: false });
    const empty = group([], { exposure: 1 }, { id: "empty" });
    const zero = group([lum(0, 50)], {}, { id: "zero" });
    const live = group([lum(0, 50)], { dehaze: 20 }, { id: "live" });
    expect(packMasks([off, empty, zero, live], defaultCrop(), W, H, WB)).toMatchObject({ maskGroups: 1, maskUse: MASK_USE.dehaze, maskPreview: -1 });
    const p = packMasks([off, empty, zero, live], defaultCrop(), W, H, WB, "zero");
    expect(p.maskGroups).toBe(2);
    expect(p.maskPreview).toBe(0);            // packed index, not list index
    expect(rows(p.masks, 0).slice(4, 12)).toEqual(Array(8).fill(0));
    expect(packMasks([live], defaultCrop(), W, H, WB, "live").maskPreview).toBe(0);
    expect(packMasks([live], defaultCrop(), W, H, WB, "off").maskPreview).toBe(-1);
  });

  it("caps at MASK_GROUPS groups and MASK_COMPS components", () => {
    const many = Array.from({ length: MASK_GROUPS + 2 }, (_, i) => group([lum(0, 50)], { exposure: 1 }, { id: `g${i}` }));
    expect(packMasks(many, defaultCrop(), W, H, WB).maskGroups).toBe(MASK_GROUPS);
    const g = group(Array(MASK_COMPS + 2).fill(lum(0, 50)));
    expect(rows(packMasks([g], defaultCrop(), W, H, WB).masks, 0)[0]).toBe(MASK_COMPS);
  });

  it("packs ΔWB = M(T + ΔT, tint + Δt) − M0 column-major", () => {
    const wb = { temperature: 5000, tint: 10 };
    const g = group([lum(0, 50)], { temperature: -25, tint: 5 });
    const r = rows(packMasks([g], defaultCrop(), W, H, wb).masks, 0);
    const m0 = computeWbMatrix(5000, 10), mg = computeWbMatrix(5000 - 25 * MASK_TEMP_PER_UNIT, 15);
    for (let col = 0; col < 3; col++) for (let row = 0; row < 3; row++) {
      expect(r[12 + col * 4 + row]).toBeCloseTo(mg[row][col] - m0[row][col], 6);
    }
    expect(r[15]).toBe(0);
    // At the reference white the base is the identity, not computeWbMatrix(6500, 0)'s float noise.
    const r2 = rows(packMasks([g], defaultCrop(), W, H, WB).masks, 0);
    const mg2 = computeWbMatrix(6500 - 25 * MASK_TEMP_PER_UNIT, 5);
    expect(r2[12]).toBeCloseTo(mg2[0][0] - 1, 6);
  });

  it("packs the colour cast as tint − 1 in the ΔWB rows' .w, zero without strength", () => {
    const g = group([lum(0, 50)], { tintHue: 30, tintSat: 60 });
    const r = rows(packMasks([g], defaultCrop(), W, H, WB).masks, 0);
    const tint = gradingTint(30, 0.6);
    for (let col = 0; col < 3; col++) {
      expect(r[15 + col * 4]).toBeCloseTo(tint[col] - 1, 6);
      expect(r[12 + col * 4 + col]).toBe(0);   // no WB delta alongside
    }
    // Hue alone is a choice, not an adjustment: the group packs nothing.
    const idle = group([lum(0, 50)], { tintHue: 30 });
    expect(packMasks([idle], defaultCrop(), W, H, WB).maskGroups).toBe(0);
  });

  it("never emits a zero-width smoothstep", () => {
    const g = group([lum(0, 100, 0, 0), { ...defaultComponent("radial"), rx: 0, ry: 0, feather: 0 }, { ...defaultComponent("linear"), x1: 0.5, y1: 0.15 }]);
    const r = rows(packMasks([g], defaultCrop(), W, H, WB).masks, 0);
    expect(r[30]).toBeGreaterThan(0); expect(r[31]).toBeGreaterThan(0);           // feathers
    expect(r[42]).toBeGreaterThan(0); expect(r[43]).toBeGreaterThan(0); expect(r[44]).toBeGreaterThan(0);
    expect([r[52], r[53]]).not.toEqual([r[50], r[51]]);                             // linear direction
  });
});

describe("weights", () => {
  const P: [number, number] = [0.5, 0.5];
  const grid = Array.from({ length: 41 }, (_, i) => i / 40);

  it("stay in [0, 1] for every type, position and luminance", () => {
    const comps: MaskComponent[] = [
      lum(30, 70, 20, 20), lum(0, 100, 0, 0), defaultComponent("color"), defaultComponent("linear"),
      { ...defaultComponent("radial"), rx: 0.2, ry: 0.5, angle: 33, feather: 0.3 },
    ];
    for (const c of comps) for (const x of grid) for (const y of grid) {
      const w = groupWeight(group([c]), [x, y], x, [y, 0.1 * x - 0.05, 0.1 * y - 0.05], ASPECT);
      expect(w).toBeGreaterThanOrEqual(0); expect(w).toBeLessThanOrEqual(1);
    }
  });

  it("luminance: 1 inside the range, 0 well outside, monotone and continuous through the feather", () => {
    const g = group([lum(40, 60, 20, 20)]);
    const w = (l: number) => groupWeight(g, P, l, GREY, ASPECT);
    expect(w(0.5)).toBe(1); expect(w(0.4)).toBe(1); expect(w(0.6)).toBe(1);
    expect(w(0.1)).toBe(0); expect(w(0.9)).toBe(0);
    let prev = 0;
    for (const l of grid.filter(v => v <= 0.5)) { const cur = w(l); expect(cur).toBeGreaterThanOrEqual(prev); expect(cur - prev).toBeLessThan(0.2); prev = cur; }
    // zero feather is a hard edge, still bounded
    const hard = group([lum(0, 50, 0, 0)]);
    expect(groupWeight(hard, P, 0.49, GREY, ASPECT)).toBe(1);
    expect(groupWeight(hard, P, 0.52, GREY, ASPECT)).toBe(0);
    expect(groupWeight(hard, P, 0, GREY, ASPECT)).toBe(1);
  });

  it("color: selects the hue, rejects neutrals and the opposite hue", () => {
    const g = group([defaultComponent("color")]);
    expect(groupWeight(g, P, 0.5, BLUE, ASPECT)).toBeCloseTo(1, 3);
    expect(groupWeight(g, P, 0.5, GREY, ASPECT)).toBe(0);
    expect(groupWeight(g, P, 0.5, [0.6, 0.05, 0.15], ASPECT)).toBe(0);
  });

  it("linear: 1 at the start point, 0 at the end, monotone between, constant across", () => {
    const g = group([{ type: "linear", op: "add", invert: false, x0: 0.5, y0: 0.2, x1: 0.5, y1: 0.6 }]);
    const w = (x: number, y: number) => groupWeight(g, [x, y], 0.5, GREY, ASPECT);
    expect(w(0.5, 0.2)).toBe(1); expect(w(0.5, 0.05)).toBe(1);
    expect(w(0.5, 0.6)).toBe(0); expect(w(0.5, 0.9)).toBe(0);
    expect(w(0.5, 0.4)).toBeCloseTo(0.5);
    expect(w(0.1, 0.4)).toBeCloseTo(w(0.9, 0.4));
    let prev = 1;
    for (const y of grid) { const cur = w(0.5, y); expect(cur).toBeLessThanOrEqual(prev + 1e-12); prev = cur; }
  });

  it("radial: 1 at the centre, 0 outside, axes respect the aspect and the rotation", () => {
    const c: MaskComponent = { type: "radial", op: "add", invert: false, cx: 0.5, cy: 0.5, rx: 0.3, ry: 0.15, angle: 0, feather: 0.001 };
    const w = (x: number, y: number, comp = c) => groupWeight(group([comp]), [x, y], 0.5, GREY, ASPECT);
    expect(w(0.5, 0.5)).toBe(1);
    expect(w(0.5 + 0.29, 0.5)).toBe(1); expect(w(0.5 + 0.31, 0.5)).toBe(0);       // rx in x-norm
    expect(w(0.5, 0.5 + 0.14)).toBe(1); expect(w(0.5, 0.5 + 0.16)).toBe(0);       // ry in y-norm
    // A true 90° turn in equal-scale space: the x semi-axis becomes ry·(ih/iw)… in x units.
    const turned = { ...c, angle: 90 };
    const rxTurned = 0.15 * ASPECT;
    expect(w(0.5 + rxTurned * 0.95, 0.5, turned)).toBe(1); expect(w(0.5 + rxTurned * 1.05, 0.5, turned)).toBe(0);
    // Inverted vignette: 0 in the middle, 1 at the corners.
    const vig = presetGroup("vignette", "v");
    expect(groupWeight(vig, [0.5, 0.5], 0.5, GREY, ASPECT)).toBe(0);
    expect(groupWeight(vig, [0, 0], 0.5, GREY, ASPECT)).toBe(1);
  });

  it("combines add / subtract / intersect as §2.4 and applies the group invert last", () => {
    const a = lum(0, 50, 0, 0), b = { ...lum(25, 100, 0, 0), op: "intersect" as const };
    const wA = (l: number) => groupWeight(group([a]), P, l, GREY, ASPECT);
    const wB = (l: number) => groupWeight(group([{ ...b, op: "add" }]), P, l, GREY, ASPECT);
    for (const l of [0.1, 0.3, 0.7]) {
      expect(groupWeight(group([a, { ...b, op: "add" }]), P, l, GREY, ASPECT)).toBeCloseTo(1 - (1 - wA(l)) * (1 - wB(l)));
      expect(groupWeight(group([a, { ...b, op: "subtract" }]), P, l, GREY, ASPECT)).toBeCloseTo(wA(l) * (1 - wB(l)));
      expect(groupWeight(group([a, b]), P, l, GREY, ASPECT)).toBeCloseTo(wA(l) * wB(l));
      expect(groupWeight(group([a], {}, { invert: true }), P, l, GREY, ASPECT)).toBeCloseTo(1 - wA(l));
    }
  });

  it("componentWeight reads the packed rows the shader reads", () => {
    const c = { ...defaultComponent("radial"), angle: 40, invert: true };
    const r = rows(packMasks([group([c])], defaultCrop(), W, H, WB).masks, 0).slice(24, 36);
    for (const x of [0.2, 0.5, 0.8]) {
      expect(componentWeight(r, [x, 0.4], 0.5, GREY, ASPECT)).toBeCloseTo(groupWeight(group([c]), [x, 0.4], 0.5, GREY, ASPECT));
    }
  });
});

describe("imgFromTex", () => {
  const apply = (m: Float32Array, x: number, y: number): [number, number] =>
    [m[0] * x + m[3] * y + m[6], m[1] * x + m[4] * y + m[7]];

  it("inverts imageNormToTexcoord for every orientation and flip", () => {
    for (const orientation of [0, 90, 180, 270] as Orientation[]) for (const flipH of [false, true]) for (const flipV of [false, true]) {
      const c: CropState = { ...defaultCrop(), orientation, flipH, flipV };
      for (const [ix, iy] of [[0, 0], [1, 0], [0.3, 0.8], [1, 1]]) {
        const [tx, ty] = imageNormToTexcoord(ix, iy, c);
        const [bx, by] = apply(imgFromTex(c), tx, ty);
        expect(bx).toBeCloseTo(ix, 6); expect(by).toBeCloseTo(iy, 6);
      }
    }
  });

  it("is a bare y-flip (FLIP_Y upload) for an upright frame and ignores the crop box", () => {
    const m = imgFromTex({ ...defaultCrop(), cx: 0.3, cy: 0.6, w: 0.4, h: 0.5, angle: 7 });
    expect(Array.from(m)).toEqual([1, 0, 0, 0, -1, 0, 0, 1, 1]);
  });
});

describe("presets and GLSL", () => {
  it("every preset builds a valid group with zeroed adjust defaults", () => {
    for (const name of Object.keys(MASK_PRESETS) as MaskPresetName[]) {
      const g = presetGroup(name, "x");
      expect(g.components.length).toBeGreaterThan(0);
      expect(g.components.length).toBeLessThanOrEqual(MASK_COMPS);
      for (const c of g.components) { expect(["add", "subtract", "intersect"]).toContain(c.op); expect(typeof c.invert).toBe("boolean"); }
      for (const k of ADJUST_KEYS) expect(typeof g.adjust[k]).toBe("number");
      expect(g.adjust.exposure).toBe(name === "vignette" ? -0.7 : 0);
    }
  });

  it("emits the block at the packed stride and the use bits", () => {
    expect(MASK_GLSL).toContain(`vec4 m[${MASK_GROUPS * GROUP_STRIDE}]`);
    expect(MASK_GLSL).toContain(`const int MASK_STRIDE = ${GROUP_STRIDE};`);
    expect(MASK_GLSL).toContain(`MASK_USE_COLOR = ${MASK_USE.color}`);
  });
});
