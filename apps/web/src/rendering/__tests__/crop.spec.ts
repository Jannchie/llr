import { describe, expect, it } from "vitest";
import {
  applyAspectRatio, buildCropTransform, cloneCrop, constrainCrop, cornersInsideImage,
  cropOutputRect, cropOutputSize, defaultCrop, imageDims, isDefaultCrop, rotate90,
  straightenedBBox, type CropState,
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
