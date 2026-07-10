import { describe, expect, it } from "vitest";
import {
  applyAspectRatio, buildCropTransform, cloneCrop, constrainCrop, cornersInsideImage,
  cropOutputRect, cropOutputSize, cropOutputSizeForAspect, customAspectKey, defaultCrop,
  imageDims, isDefaultCrop, parseCustomAspect, ratioToFraction, resolveAspectFraction,
  resolveAspectRatio, rotate90, straightenedBBox, type CropState,
} from "../crop";

const SRC_W = 6000;
const SRC_H = 4000;

/** Apply the column-major mat3 from buildCropTransform to an output-frame point p. */
function applyXform(m: Float32Array, px: number, py: number): [number, number] {
  return [
    m[0] * px + m[3] * py + m[6],
    m[1] * px + m[4] * py + m[7],
  ];
}

describe("imageDims", () => {
  it("keeps dims upright at 0/180 and swaps at 90/270", () => {
    expect(imageDims(SRC_W, SRC_H, 0)).toEqual([SRC_W, SRC_H]);
    expect(imageDims(SRC_W, SRC_H, 180)).toEqual([SRC_W, SRC_H]);
    expect(imageDims(SRC_W, SRC_H, 90)).toEqual([SRC_H, SRC_W]);
    expect(imageDims(SRC_W, SRC_H, 270)).toEqual([SRC_H, SRC_W]);
  });
});

describe("defaultCrop", () => {
  it("is recognised as the identity crop", () => {
    expect(isDefaultCrop(defaultCrop())).toBe(true);
    expect(isDefaultCrop({ ...defaultCrop(), angle: 3 })).toBe(false);
    expect(isDefaultCrop({ ...defaultCrop(), flipH: true })).toBe(false);
  });
});

describe("buildCropTransform", () => {
  it("is the FLIP_Y identity for the default crop", () => {
    const c = defaultCrop();
    const rect = cropOutputRect(c, SRC_W, SRC_H);
    const m = buildCropTransform(c, SRC_W, SRC_H, rect);
    // Output-frame (0,0) = image top-left = texcoord (0,1) under the FLIP_Y upload.
    expect(applyXform(m, 0, 0)[0]).toBeCloseTo(0, 6);
    expect(applyXform(m, 0, 0)[1]).toBeCloseTo(1, 6);
    expect(applyXform(m, 1, 1)[0]).toBeCloseTo(1, 6);
    expect(applyXform(m, 1, 1)[1]).toBeCloseTo(0, 6);
  });

  it("mirrors horizontally under flipH", () => {
    const c: CropState = { ...defaultCrop(), flipH: true };
    const rect = cropOutputRect(c, SRC_W, SRC_H);
    const m = buildCropTransform(c, SRC_W, SRC_H, rect);
    expect(applyXform(m, 0, 0)[0]).toBeCloseTo(1, 6); // left edge samples the right
    expect(applyXform(m, 1, 0)[0]).toBeCloseTo(0, 6);
    expect(applyXform(m, 0, 0)[1]).toBeCloseTo(1, 6); // vertical untouched
  });

  it("keeps texcoords inside [0,1] for a constrained rotated crop", () => {
    const c = constrainCrop({ ...defaultCrop(), angle: 10 }, SRC_W, SRC_H);
    const rect = cropOutputRect(c, SRC_W, SRC_H);
    const m = buildCropTransform(c, SRC_W, SRC_H, rect);
    for (const [px, py] of [[0, 0], [1, 0], [0, 1], [1, 1], [0.5, 0.5]] as const) {
      const [u, v] = applyXform(m, px, py);
      expect(u).toBeGreaterThanOrEqual(-1e-3);
      expect(u).toBeLessThanOrEqual(1 + 1e-3);
      expect(v).toBeGreaterThanOrEqual(-1e-3);
      expect(v).toBeLessThanOrEqual(1 + 1e-3);
    }
  });
});

describe("rotate90", () => {
  it("cycles orientation and returns to the original after four turns", () => {
    let c: CropState = { ...defaultCrop(), cx: 0.3, cy: 0.6, w: 0.5, h: 0.4 };
    const original = cloneCrop(c);
    const seen: number[] = [];
    for (let i = 0; i < 4; i++) {
      c = rotate90(c, 1);
      seen.push(c.orientation);
    }
    expect(seen).toEqual([90, 180, 270, 0]);
    expect(c.cx).toBeCloseTo(original.cx, 9);
    expect(c.cy).toBeCloseTo(original.cy, 9);
    expect(c.w).toBeCloseTo(original.w, 9);
    expect(c.h).toBeCloseTo(original.h, 9);
  });

  it("one clockwise turn undoes one counter-clockwise turn", () => {
    const c: CropState = { ...defaultCrop(), cx: 0.3, cy: 0.6, w: 0.5, h: 0.4, orientation: 90 };
    const roundTrip = rotate90(rotate90(c, 1), -1);
    expect(roundTrip).toEqual(c);
  });
});

describe("constrainCrop", () => {
  it("returns a crop whose corners are inside the image at any angle", () => {
    for (const angle of [-45, -20, -5, 5, 20, 45]) {
      const c = constrainCrop({ ...defaultCrop(), angle }, SRC_W, SRC_H);
      const [iw, ih] = imageDims(SRC_W, SRC_H, c.orientation);
      expect(cornersInsideImage(c, iw, ih)).toBe(true);
      expect(c.w).toBeGreaterThan(0);
      expect(c.h).toBeGreaterThan(0);
    }
  });

  it("leaves an already-valid crop untouched", () => {
    const c: CropState = { ...defaultCrop(), cx: 0.5, cy: 0.5, w: 0.5, h: 0.5, angle: 5 };
    const [iw, ih] = imageDims(SRC_W, SRC_H, 0);
    expect(cornersInsideImage(c, iw, ih)).toBe(true);
    expect(constrainCrop(c, SRC_W, SRC_H)).toEqual(c);
  });

  it("recovers an off-image center", () => {
    const c = constrainCrop({ ...defaultCrop(), cx: 1.4, cy: -0.2, w: 0.4, h: 0.4 }, SRC_W, SRC_H);
    const [iw, ih] = imageDims(SRC_W, SRC_H, c.orientation);
    expect(cornersInsideImage(c, iw, ih)).toBe(true);
  });
});

describe("applyAspectRatio", () => {
  it("produces the requested pixel ratio and stays inside the image", () => {
    for (const ratio of [1, 3 / 2, 16 / 9, 4 / 5]) {
      const c = applyAspectRatio(defaultCrop(), ratio, SRC_W, SRC_H);
      const [iw, ih] = imageDims(SRC_W, SRC_H, c.orientation);
      expect((c.w * iw) / (c.h * ih)).toBeCloseTo(ratio, 3);
      expect(cornersInsideImage(c, iw, ih)).toBe(true);
    }
  });
});

describe("resolveAspectRatio", () => {
  it("orients presets to the crop box (landscape box → w/h > 1)", () => {
    const landscape = defaultCrop(); // 6000×4000 full-frame box
    expect(resolveAspectRatio("4:3", SRC_W, SRC_H, landscape)).toBeCloseTo(4 / 3, 9);
    const portrait: CropState = { ...defaultCrop(), w: 0.4, h: 0.9 };
    expect(resolveAspectRatio("4:3", SRC_W, SRC_H, portrait)).toBeCloseTo(3 / 4, 9);
  });

  it("resolves Original to the image ratio, oriented to the box", () => {
    expect(resolveAspectRatio("orig", SRC_W, SRC_H, defaultCrop())).toBeCloseTo(SRC_W / SRC_H, 9);
    const portrait: CropState = { ...defaultCrop(), w: 0.3, h: 0.8 };
    expect(resolveAspectRatio("orig", SRC_W, SRC_H, portrait)).toBeCloseTo(SRC_H / SRC_W, 9);
    // 90°-rotated image: image space is 4000×6000, default box is portrait.
    const rotated: CropState = { ...defaultCrop(), orientation: 90 };
    expect(resolveAspectRatio("orig", SRC_W, SRC_H, rotated)).toBeCloseTo(SRC_H / SRC_W, 9);
  });

  it("returns null for free and unknown keys", () => {
    expect(resolveAspectRatio("free", SRC_W, SRC_H, defaultCrop())).toBeNull();
    expect(resolveAspectRatio("nope", SRC_W, SRC_H, defaultCrop())).toBeNull();
  });

  it("parses custom keys and rejects malformed ones", () => {
    expect(resolveAspectRatio(customAspectKey(16, 10), SRC_W, SRC_H, defaultCrop())).toBeCloseTo(1.6, 9);
    expect(parseCustomAspect("custom:8.5:11")).toEqual([8.5, 11]);
    expect(parseCustomAspect("custom:0:3")).toBeNull();
    expect(parseCustomAspect("custom:a:b")).toBeNull();
    expect(parseCustomAspect("4:3")).toBeNull();
    expect(resolveAspectRatio("custom:-1:2", SRC_W, SRC_H, defaultCrop())).toBeNull();
  });
});

describe("resolveAspectFraction / cropOutputSizeForAspect", () => {
  const FULL_W = 7008;
  const FULL_H = 4672; // exact 3:2 camera crop

  it("reduces preset keys to integer fractions, oriented to the box", () => {
    expect(resolveAspectFraction("4:3", FULL_W, FULL_H, defaultCrop())).toEqual([4, 3]);
    expect(resolveAspectFraction("16:10", FULL_W, FULL_H, defaultCrop())).toEqual([8, 5]);
    expect(resolveAspectFraction("8.5:11", FULL_W, FULL_H, defaultCrop())).toEqual([22, 17]);
    const portrait: CropState = { ...defaultCrop(), w: 0.4, h: 0.9 };
    expect(resolveAspectFraction("4:3", FULL_W, FULL_H, portrait)).toEqual([3, 4]);
    expect(resolveAspectFraction("orig", FULL_W, FULL_H, defaultCrop())).toEqual([3, 2]);
    expect(resolveAspectFraction("free", FULL_W, FULL_H, defaultCrop())).toBeNull();
  });

  it("snaps the export size to an exact multiple of the fraction", () => {
    // Largest centered 4:3 box on the 3:2 frame: nominal 6229×4672 rounds to
    // the exact pair 6228×4671.
    const c = applyAspectRatio(defaultCrop(), 4 / 3, FULL_W, FULL_H);
    const [ow, oh] = cropOutputSizeForAspect(c, FULL_W, FULL_H, [4, 3]);
    expect([ow, oh]).toEqual([6228, 4671]);
    expect((ow / 4) % 1).toBe(0);
    expect(oh).toBe((ow / 4) * 3);
  });

  it("keeps the full frame exact when the image already matches the ratio", () => {
    const c = applyAspectRatio(defaultCrop(), 3 / 2, FULL_W, FULL_H);
    expect(cropOutputSizeForAspect(c, FULL_W, FULL_H, [3, 2])).toEqual([FULL_W, FULL_H]);
  });

  it("refuses to snap when the box doesn't match the fraction", () => {
    const wide: CropState = { ...defaultCrop(), w: 1, h: 0.5 }; // 3:1 box
    expect(cropOutputSizeForAspect(wide, FULL_W, FULL_H, [4, 3]))
      .toEqual(cropOutputSize(wide, FULL_W, FULL_H));
  });
});

describe("ratioToFraction", () => {
  it("recovers simple fractions from pixel ratios", () => {
    expect(ratioToFraction(3 / 2)).toEqual([3, 2]);
    expect(ratioToFraction(16 / 9)).toEqual([16, 9]);
    expect(ratioToFraction(1)).toEqual([1, 1]);
    // Near-miss ratios snap to the closest small fraction.
    expect(ratioToFraction(1.334)).toEqual([4, 3]);
  });
});

describe("cropOutputSize / straightenedBBox", () => {
  it("output size follows the normalized box", () => {
    const c: CropState = { ...defaultCrop(), w: 0.5, h: 0.25 };
    expect(cropOutputSize(c, SRC_W, SRC_H)).toEqual([3000, 1000]);
  });

  it("bbox contains the crop box", () => {
    const c = constrainCrop({ ...defaultCrop(), angle: 15 }, SRC_W, SRC_H);
    const [iw, ih] = imageDims(SRC_W, SRC_H, c.orientation);
    const bbox = straightenedBBox(c, iw, ih);
    const rect = cropOutputRect(c, iw, ih);
    expect(rect.x).toBeGreaterThanOrEqual(bbox.x);
    expect(rect.y).toBeGreaterThanOrEqual(bbox.y);
    expect(rect.x + rect.w).toBeLessThanOrEqual(bbox.x + bbox.w);
    expect(rect.y + rect.h).toBeLessThanOrEqual(bbox.y + bbox.h);
  });
});
