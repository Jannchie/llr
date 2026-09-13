import { describe, expect, it } from "vitest";
import {
  applyAspectRatio, buildCropTransform, cloneCrop, constrainCrop, cornersInsideImage,
  cropOutputRect, cropOutputSize, cropOutputSizeForAspect, cssRecomposeMatrix, customAspectKey, defaultCrop, recomposeMatrix,
  imageDims, isDefaultCrop, parseCustomAspect, ratioToFraction, resolveAspectFraction,
  resolveAspectRatio, rotate90, straightenedBBox, straightenAngle, cropGuideShapes, cropCornersImage, CROP_GUIDES,
  defaultTransform, transformMatrix, guidedHomography, flipCrop, outFrameToImagePx, imagePxToOutFrame,
  type CropState, type Transform,
} from "../crop";

const SRC_W = 6000;
const SRC_H = 4000;

const XF: Transform = { vertical: 40, horizontal: -20, aspect: 30, scale: 110, offsetX: 10, offsetY: -5, guides: [] };

/** Apply the column-major mat3 from buildCropTransform to an output-frame point p. */
function applyXform(m: Float32Array, px: number, py: number): [number, number] {
  const w = m[2] * px + m[5] * py + m[8];
  return [
    (m[0] * px + m[3] * py + m[6]) / w,
    (m[1] * px + m[4] * py + m[7]) / w,
  ];
}

/** Source texcoord shown at output-normalized (px,py) of the committed crop. */
function probe(c: CropState, px: number, py: number): [number, number] {
  const [iw, ih] = imageDims(SRC_W, SRC_H, c.orientation);
  return applyXform(buildCropTransform(c, SRC_W, SRC_H, cropOutputRect(c, iw, ih)), px, py);
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

describe("cssRecomposeMatrix", () => {
  /** Parse "matrix3d(…)" (column-major) and apply it to a source-space px point. */
  function applyCss(css: string, x: number, y: number): [number, number] {
    const m = css.slice("matrix3d(".length, -1).split(",").map(Number);
    const w = m[3] * x + m[7] * y + m[15];
    return [(m[0] * x + m[4] * y + m[12]) / w, (m[1] * x + m[5] * y + m[13]) / w];
  }

  // Every recompose the compare overlay can meet: 90° steps, flips, straighten,
  // and an off-center box.
  const CASES: Array<[string, CropState]> = [
    ["default", defaultCrop()],
    ["orientation 90", { ...defaultCrop(), orientation: 90 }],
    ["orientation 270 + flipV", { ...defaultCrop(), orientation: 270, flipV: true }],
    ["flipH", { ...defaultCrop(), flipH: true }],
    ["off-center box", { ...defaultCrop(), cx: 0.4, cy: 0.55, w: 0.5, h: 0.3 }],
    ["straightened", constrainCrop({ ...defaultCrop(), angle: 8, w: 0.7, h: 0.6 }, SRC_W, SRC_H)],
    ["everything", constrainCrop(
      { cx: 0.45, cy: 0.5, w: 0.6, h: 0.5, angle: -6, flipH: true, flipV: false, orientation: 90, xf: defaultTransform() },
      SRC_W, SRC_H)],
    ["perspective", constrainCrop({ ...defaultCrop(), angle: 3, orientation: 270, flipV: true, xf: XF }, SRC_W, SRC_H)],
  ];

  it.each(CASES)("inverts buildCropTransform for %s", (_name, c) => {
    const [iw, ih] = imageDims(SRC_W, SRC_H, c.orientation);
    const rect = cropOutputRect(c, iw, ih);
    const [ow, oh] = cropOutputSize(c, SRC_W, SRC_H);
    const css = cssRecomposeMatrix(c, SRC_W, SRC_H, rect, ow, oh);
    for (const [px, py] of [[0, 0], [1, 0], [0, 1], [1, 1], [0.5, 0.5], [0.25, 0.75]] as const) {
      // Where the render samples this output point, back in source pixels
      // (undoing the FLIP_Y upload convention)...
      const [u, v] = probe(c, px, py);
      const [bx, by] = applyCss(css, u * SRC_W, (1 - v) * SRC_H);
      // ...must land on the same output point once the CSS matrix places it.
      expect(bx / ow).toBeCloseTo(px, 4);
      expect(by / oh).toBeCloseTo(py, 4);
    }
  });

  it.each(CASES)("is the CSS form of the row-major recomposeMatrix for %s", (_name, c) => {
    // The histogram bins the camera JPEG through the raw matrix on a 2D canvas
    // (an affine setTransform), so its first two rows must be what the CSS
    // overlay is placed with — the same point must land in the same place.
    const [iw, ih] = imageDims(SRC_W, SRC_H, c.orientation);
    const rect = cropOutputRect(c, iw, ih);
    const [ow, oh] = cropOutputSize(c, SRC_W, SRC_H);
    const m = recomposeMatrix(c, SRC_W, SRC_H, rect, ow, oh);
    const css = cssRecomposeMatrix(c, SRC_W, SRC_H, rect, ow, oh);
    expect(m).toHaveLength(9);
    for (const [x, y] of [[0, 0], [SRC_W, 0], [SRC_W / 3, SRC_H * 0.8]]) {
      const w = m[6] * x + m[7] * y + m[8];
      const [cx, cy] = applyCss(css, x, y);
      expect((m[0] * x + m[1] * y + m[2]) / w).toBeCloseTo(cx, 6);
      expect((m[3] * x + m[4] * y + m[5]) / w).toBeCloseTo(cy, 6);
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

  const FLIPS = [
    { flipH: false, flipV: false }, { flipH: true, flipV: false },
    { flipH: false, flipV: true }, { flipH: true, flipV: true },
  ];

  it("turns the displayed content the asked-for way under every flip", () => {
    for (const flips of FLIPS) {
      for (const dir of [1, -1] as const) {
        const c: CropState = { ...defaultCrop(), ...flips };
        const turned = rotate90(c, dir);
        for (const [px, py] of [[0, 0], [1, 0], [0, 1], [1, 1], [0.25, 0.75]] as const) {
          // Turning the display by `dir` sends the old point (ox,oy) to
          // (1-oy,ox) clockwise / (oy,1-ox) counter-clockwise; both views must
          // show the same source pixel there.
          const [ox, oy] = dir === 1 ? [py, 1 - px] : [1 - py, px];
          const [eu, ev] = probe(c, ox, oy);
          const [u, v] = probe(turned, px, py);
          expect(u).toBeCloseTo(eu, 6);
          expect(v).toBeCloseTo(ev, 6);
        }
      }
    }
  });

  it("keeps an off-center crop box on the same region under every flip", () => {
    for (const flips of FLIPS) {
      for (const dir of [1, -1] as const) {
        const c: CropState = { ...defaultCrop(), ...flips, cx: 0.2, cy: 0.2, w: 0.4, h: 0.3 };
        const turned = rotate90(c, dir);
        for (const [px, py] of [[0, 0], [1, 0], [0, 1], [1, 1], [0.25, 0.75]] as const) {
          const [ox, oy] = dir === 1 ? [py, 1 - px] : [1 - py, px];
          const [eu, ev] = probe(c, ox, oy);
          const [u, v] = probe(turned, px, py);
          expect(u).toBeCloseTo(eu, 6);
          expect(v).toBeCloseTo(ev, 6);
        }
      }
    }
  });

  it("conjugates the orientation step, but not the box, under a single mirror", () => {
    const c: CropState = { ...defaultCrop(), flipH: true, cx: 0.2, cy: 0.2, w: 0.4, h: 0.3 };
    const turned = rotate90(c, 1);
    expect(turned.orientation).toBe(270);
    expect(turned.cx).toBeCloseTo(0.8, 9);
    expect(turned.cy).toBeCloseTo(0.2, 9);
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

  it("recovers an off-image center by translating, not shrinking", () => {
    const c = constrainCrop({ ...defaultCrop(), cx: 1.4, cy: -0.2, w: 0.4, h: 0.4 }, SRC_W, SRC_H);
    const [iw, ih] = imageDims(SRC_W, SRC_H, c.orientation);
    expect(cornersInsideImage(c, iw, ih)).toBe(true);
    expect(c.w).toBeCloseTo(0.4, 9);
    expect(c.h).toBeCloseTo(0.4, 9);
    expect(c.cx).toBeCloseTo(0.8, 9);
    expect(c.cy).toBeCloseTo(0.2, 9);
  });

  it("translates a box whose center sits on the image edge", () => {
    const c = constrainCrop({ ...defaultCrop(), cx: 0, cy: 1, w: 0.4, h: 0.4 }, SRC_W, SRC_H);
    expect(c.w).toBeCloseTo(0.4, 9);
    expect(c.h).toBeCloseTo(0.4, 9);
    expect(c.cx).toBeCloseTo(0.2, 9);
    expect(c.cy).toBeCloseTo(0.8, 9);
  });

  it("keeps the roomy axis in place while shrinking for the overflowing one", () => {
    // A full-height box: any nonzero angle makes it too tall, but x has room.
    for (const angle of [0.5, 5, 20]) {
      const c = constrainCrop({ ...defaultCrop(), cx: 0.25, cy: 0.5, w: 0.25, h: 1, angle }, SRC_W, SRC_H);
      const [iw, ih] = imageDims(SRC_W, SRC_H, c.orientation);
      expect(cornersInsideImage(c, iw, ih)).toBe(true);
      expect(c.cx).toBeCloseTo(0.25, 2);
      expect(c.h).toBeLessThan(1);
    }
  });

  it("keeps a rotated edge-hugging box at full size", () => {
    const c = constrainCrop({ ...defaultCrop(), cx: 0.05, cy: 0.5, w: 0.4, h: 0.4, angle: 10 }, SRC_W, SRC_H);
    const [iw, ih] = imageDims(SRC_W, SRC_H, c.orientation);
    expect(cornersInsideImage(c, iw, ih)).toBe(true);
    expect(c.w).toBeCloseTo(0.4, 9);
    expect(c.h).toBeCloseTo(0.4, 9);
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

describe("straightenAngle", () => {
  it("levels a line by adding its output-frame tilt to the angle", () => {
    // outFrameToImage maps output direction (1,0) to image direction `angle`
    // (cropCornersImage TL→TR), so an image line at φ shows at φ − angle.
    const phi = 8;
    const c: CropState = { ...defaultCrop(), angle: phi };
    const [tl, tr] = cropCornersImage(c, SRC_W, SRC_H);
    expect((Math.atan2(tr[1] - tl[1], tr[0] - tl[0]) * 180) / Math.PI).toBeCloseTo(phi, 6);
    const shown = ((phi - 3) * Math.PI) / 180;
    expect(straightenAngle(3, Math.cos(shown), Math.sin(shown))).toBeCloseTo(phi, 6);
  });

  it("makes a near-vertical line plumb rather than level", () => {
    expect(straightenAngle(0, 10, -100)).toBeCloseTo(5.71, 2);
    expect(straightenAngle(0, 10, 100)).toBeCloseTo(-5.71, 2);
    expect(straightenAngle(0, -100, 10)).toBeCloseTo(-5.71, 2);
  });
});

describe("cropGuideShapes", () => {
  const r = { x: 100, y: 50, w: 900, h: 600 };
  const inside = ([x1, y1, x2, y2]: [number, number, number, number]): boolean =>
    [x1, x2].every((x) => x >= r.x - 1e-6 && x <= r.x + r.w + 1e-6) &&
    [y1, y2].every((y) => y >= r.y - 1e-6 && y <= r.y + r.h + 1e-6);

  it("keeps every guide line inside the box for every variant", () => {
    for (const kind of CROP_GUIDES) {
      for (let v = 0; v < 4; v++) {
        const g = cropGuideShapes(kind, r, v);
        expect(g.lines.every(inside)).toBe(true);
        expect(g.lines.length > 0 || g.paths.length > 0).toBe(kind !== "off");
      }
    }
    expect(cropGuideShapes("off", r).lines).toHaveLength(0);
  });

  it("draws the spiral as one chain of quarter arcs", () => {
    const { paths } = cropGuideShapes("spiral", r);
    expect(paths).toHaveLength(1);
    const arcs = paths[0].match(/A/g) ?? [];
    expect(arcs.length).toBeGreaterThanOrEqual(8);
    expect(paths[0]).toMatch(/^M[\d.]+,[\d.]+A/);
    // Arc endpoints stay inside the box.
    const nums = paths[0].match(/,(\d+\.?\d*),(\d+\.?\d*)(?=A|$)/g) ?? [];
    expect(nums.length).toBeGreaterThan(0);
    for (const s of nums) {
      const [x, y] = s.slice(1).split(",").map(Number);
      expect(x).toBeGreaterThanOrEqual(r.x - 1e-6);
      expect(x).toBeLessThanOrEqual(r.x + r.w + 1e-6);
      expect(y).toBeGreaterThanOrEqual(r.y - 1e-6);
      expect(y).toBeLessThanOrEqual(r.y + r.h + 1e-6);
    }
  });

  it("mirrors the triangle with the variant", () => {
    const a = cropGuideShapes("triangle", r, 0).lines[0];
    const b = cropGuideShapes("triangle", r, 1).lines[0];
    expect(a).toEqual([r.x, r.y, r.x + r.w, r.y + r.h]);
    expect(b).toEqual([r.x + r.w, r.y, r.x, r.y + r.h]);
  });
});

describe("transform", () => {
  const iw = SRC_W, ih = SRC_H;

  it("is the identity without any slider or guide", () => {
    expect(transformMatrix(defaultTransform(), iw, ih)).toEqual([1, 0, 0, 0, 1, 0, 0, 0, 1]);
  });

  it("keeps a constrained crop sampling inside the source", () => {
    const c = constrainCrop({ ...defaultCrop(), angle: 4, xf: XF }, SRC_W, SRC_H);
    expect(cornersInsideImage(c, iw, ih)).toBe(true);
    expect(c.w).toBeLessThan(1);
    for (const [px, py] of [[0, 0], [1, 0], [0, 1], [1, 1], [0.5, 0.5]] as const) {
      const [u, v] = probe(c, px, py);
      expect(u).toBeGreaterThanOrEqual(-1e-3); expect(u).toBeLessThanOrEqual(1 + 1e-3);
      expect(v).toBeGreaterThanOrEqual(-1e-3); expect(v).toBeLessThanOrEqual(1 + 1e-3);
    }
  });

  it("outFrame ↔ image px round-trip", () => {
    const c = { ...defaultCrop(), angle: 7, cx: 0.4, xf: XF };
    const [ox, oy] = imagePxToOutFrame(c, iw, ih, 1234, 567);
    const [x, y] = outFrameToImagePx(c, iw, ih, ox, oy);
    expect(x).toBeCloseTo(1234, 6);
    expect(y).toBeCloseTo(567, 6);
  });

  it("survives four 90° turns and a double flip", () => {
    const xf: Transform = { ...XF, guides: [[0.1, 0.2, 0.3, 0.9], [0.8, 0.1, 0.7, 0.9]] };
    // 1 - (1 - y) is not y in floats: compare rounded.
    const tidy = (t: Transform) => JSON.parse(JSON.stringify(t, (_k, v) => (typeof v === "number" ? Number(v.toFixed(9)) : v)));
    let c: CropState = { ...defaultCrop(), xf };
    for (let i = 0; i < 4; i++) c = rotate90(c, 1);
    expect(tidy(c.xf)).toEqual(tidy(xf));
    c = flipCrop(flipCrop(c, "h"), "h");
    c = flipCrop(flipCrop(c, "v"), "v");
    expect(tidy(c.xf)).toEqual(tidy(xf));
    expect(isDefaultCrop(c)).toBe(false);
  });

  it("a 90° turn shows the same picture, turned", () => {
    // A point on the transformed image, before and after the turn, must be the
    // same source pixel: the conjugated transform is the same map in the new frame.
    const c: CropState = constrainCrop({ ...defaultCrop(), w: 0.5, h: 0.5, xf: { ...XF, guides: [[0.1, 0.2, 0.3, 0.9], [0.8, 0.1, 0.7, 0.9]] } }, SRC_W, SRC_H);
    const r = rotate90(c, 1);
    const a = probe(c, 0.3, 0.8);
    const b = probe(r, 0.2, 0.3); // (px,py) → turned clockwise: (1-py, px)
    expect(b[0]).toBeCloseTo(a[0], 4);
    expect(b[1]).toBeCloseTo(a[1], 4);
  });
});

describe("guidedHomography", () => {
  const iw = 3000, ih = 2000;
  // A rectified world seen through a known keystone: warp true verticals /
  // horizontals through P, hand them to the solver, and check it undoes it.
  const P = [1, 0, 0, 0, 1, 0, 0.15, -0.3, 1];
  const warp = (x: number, y: number): [number, number] => {
    const n = Math.max(iw, ih) / 2;
    const u = (x * iw - iw / 2) / n, t = (y * ih - ih / 2) / n;
    const w = P[6] * u + P[7] * t + 1;
    return [((u / w) * n + iw / 2) / iw, ((t / w) * n + ih / 2) / ih];
  };
  const line = (x1: number, y1: number, x2: number, y2: number): [number, number, number, number] => [...warp(x1, y1), ...warp(x2, y2)];
  const direction = (G: number[], l: [number, number, number, number]): [number, number] => {
    const n = Math.max(iw, ih) / 2;
    const to = (x: number, y: number) => {
      const u = (x * iw - iw / 2) / n, t = (y * ih - ih / 2) / n;
      const w = G[6] * u + G[7] * t + G[8];
      return [(G[0] * u + G[1] * t + G[2]) / w, (G[3] * u + G[4] * t + G[5]) / w];
    };
    const a = to(l[0], l[1]), b = to(l[2], l[3]);
    const L = Math.hypot(b[0] - a[0], b[1] - a[1]);
    return [(b[0] - a[0]) / L, (b[1] - a[1]) / L];
  };

  it("makes two verticals plumb", () => {
    const guides = [line(0.2, 0.1, 0.2, 0.9), line(0.8, 0.15, 0.8, 0.85)];
    const G = guidedHomography(guides, iw, ih);
    for (const l of guides) expect(Math.abs(direction(G, l)[0])).toBeLessThan(1e-6);
    // A third, unseen vertical is plumb too: the whole perspective was solved, not just the lines.
    expect(Math.abs(direction(G, line(0.5, 0.2, 0.5, 0.7))[0])).toBeLessThan(1e-6);
  });

  it("makes verticals plumb and horizontals level together", () => {
    const guides = [line(0.2, 0.1, 0.2, 0.9), line(0.8, 0.15, 0.8, 0.85), line(0.1, 0.3, 0.9, 0.3), line(0.15, 0.7, 0.85, 0.7)];
    const G = guidedHomography(guides, iw, ih);
    expect(Math.abs(direction(G, guides[0])[0])).toBeLessThan(1e-6);
    expect(Math.abs(direction(G, guides[1])[0])).toBeLessThan(1e-6);
    expect(Math.abs(direction(G, guides[2])[1])).toBeLessThan(1e-6);
    expect(Math.abs(direction(G, guides[3])[1])).toBeLessThan(1e-6);
    expect(Math.abs(direction(G, line(0.3, 0.5, 0.6, 0.5))[1])).toBeLessThan(1e-6);
  });

  it("a single line only rotates", () => {
    const G = guidedHomography([[0.1, 0.5, 0.9, 0.6]], iw, ih);
    expect(G[6]).toBe(0); expect(G[7]).toBe(0);
    expect(Math.abs(direction(G, [0.1, 0.5, 0.9, 0.6])[1])).toBeLessThan(1e-9);
  });
});
