/**
 * Crop & Straighten geometry — Lightroom-compatible recompose model.
 *
 * Coordinate spaces (all y-down):
 *   - source space:  the uploaded RAW texture, srcW × srcH pixels.
 *   - image space:   source with the 90° `orientation` step applied (so the user
 *                    edits an upright photo). iw × ih pixels.
 *   - output frame:  screen-aligned frame in which the crop box is axis-aligned
 *                    and the photo content is rotated by `angle` (straighten)
 *                    about the crop box center. Shares the pixel scale of image
 *                    space. The committed crop output == the crop box region of
 *                    this frame.
 *
 * Mirrors Adobe's crop model: a rectangle + a straighten angle, with the
 * rectangle kept inside the image at every angle. Flips and 90° rotations are
 * folded into the same output→source mapping so the whole recompose is a single
 * affine transform the WebGL pass can bake while sampling.
 */

export type Orientation = 0 | 90 | 180 | 270;

export type CropState = {
  /** Crop box center, normalized to image-space dims [0,1]. */
  cx: number;
  cy: number;
  /** Crop box size, normalized to image-space dims [0,1]. */
  w: number;
  h: number;
  /** Straighten angle in degrees (content rotation correction), ~[-45,45]. */
  angle: number;
  flipH: boolean;
  flipV: boolean;
  /** 90° rotation step applied to reach image space. */
  orientation: Orientation;
};

export function defaultCrop(): CropState {
  return { cx: 0.5, cy: 0.5, w: 1, h: 1, angle: 0, flipH: false, flipV: false, orientation: 0 };
}

export function cloneCrop(c: CropState): CropState {
  return { ...c };
}

const EPS = 1e-4;

/** Whether the crop has no effect (full frame, upright, no straighten/flip). */
export function isDefaultCrop(c: CropState): boolean {
  return (
    Math.abs(c.cx - 0.5) < EPS && Math.abs(c.cy - 0.5) < EPS &&
    Math.abs(c.w - 1) < EPS && Math.abs(c.h - 1) < EPS &&
    Math.abs(c.angle) < EPS && !c.flipH && !c.flipV && c.orientation === 0
  );
}

/** Image-space dims (source dims with the 90° orientation step applied). */
export function imageDims(srcW: number, srcH: number, orientation: Orientation): [number, number] {
  return orientation === 90 || orientation === 270 ? [srcH, srcW] : [srcW, srcH];
}

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

export type Rect = { x: number; y: number; w: number; h: number };

/** Crop box center & size in image-space pixels. */
function pixelBox(c: CropState, iw: number, ih: number): { cx: number; cy: number; w: number; h: number } {
  return { cx: c.cx * iw, cy: c.cy * ih, w: c.w * iw, h: c.h * ih };
}

/** Forward map: output-frame point (offset from image origin) → image-space point. */
function outFrameToImage(ox: number, oy: number, cx: number, cy: number, cos: number, sin: number): [number, number] {
  const dx = ox - cx;
  const dy = oy - cy;
  return [cx + (dx * cos - dy * sin), cy + (dx * sin + dy * cos)];
}

/** Image-space normalized (y-down) → source texcoord (FLIP_Y upload convention). */
function imageNormToTexcoord(ix: number, iy: number, c: CropState): [number, number] {
  if (c.flipH) ix = 1 - ix;
  if (c.flipV) iy = 1 - iy;
  let su: number, sv: number;
  switch (c.orientation) {
    case 90:  su = iy;     sv = 1 - ix; break;
    case 180: su = 1 - ix; sv = 1 - iy; break;
    case 270: su = 1 - iy; sv = ix;     break;
    default:  su = ix;     sv = iy;     break;
  }
  return [su, 1 - sv];
}

/** The 4 corners of the crop box, expressed in image space (clockwise from TL). */
export function cropCornersImage(c: CropState, iw: number, ih: number): Array<[number, number]> {
  const b = pixelBox(c, iw, ih);
  const a = (c.angle * Math.PI) / 180;
  const cos = Math.cos(a);
  const sin = Math.sin(a);
  const hw = b.w / 2;
  const hh = b.h / 2;
  return ([[-hw, -hh], [hw, -hh], [hw, hh], [-hw, hh]] as Array<[number, number]>).map(
    ([dx, dy]) => outFrameToImage(b.cx + dx, b.cy + dy, b.cx, b.cy, cos, sin),
  );
}

/** Whether the (possibly rotated) crop box lies fully within the image rect. */
export function cornersInsideImage(c: CropState, iw: number, ih: number, eps = 0.75): boolean {
  return cropCornersImage(c, iw, ih).every(([x, y]) => x >= -eps && x <= iw + eps && y >= -eps && y <= ih + eps);
}

/**
 * Build the affine matrix mapping the output quad → source texcoords for the
 * WebGL pass. `outRect` is the rendered window in output-frame pixels (offset
 * from the image origin): the crop box itself for the committed view, or the
 * straightened image's bounding box for the crop editor.
 *
 * Returned as a column-major mat3 (length 9). The shader evaluates
 * `texcoord = M * vec3(p, 1)` with `p = (uv.x, 1 - uv.y)`.
 */
export function buildCropTransform(c: CropState, srcW: number, srcH: number, outRect: Rect): Float32Array {
  const [iw, ih] = imageDims(srcW, srcH, c.orientation);
  const b = pixelBox(c, iw, ih);
  const a = (c.angle * Math.PI) / 180;
  const cos = Math.cos(a);
  const sin = Math.sin(a);

  // Evaluate the full output→texcoord map at p = (0,0),(1,0),(0,1); the map is
  // affine so three samples fully determine it.
  const sample = (px: number, py: number): [number, number] => {
    const ox = outRect.x + px * outRect.w;
    const oy = outRect.y + py * outRect.h;
    const [imx, imy] = outFrameToImage(ox, oy, b.cx, b.cy, cos, sin);
    return imageNormToTexcoord(imx / iw, imy / ih, c);
  };
  const t00 = sample(0, 0);
  const t10 = sample(1, 0);
  const t01 = sample(0, 1);
  const a00 = t10[0] - t00[0];
  const a10 = t10[1] - t00[1];
  const a01 = t01[0] - t00[0];
  const a11 = t01[1] - t00[1];
  // column-major: col0, col1, col2(translation)
  return new Float32Array([a00, a10, 0, a01, a11, 0, t00[0], t00[1], 1]);
}

/** Committed crop output rect (the crop box) in output-frame pixels. */
export function cropOutputRect(c: CropState, iw: number, ih: number): Rect {
  const b = pixelBox(c, iw, ih);
  return { x: b.cx - b.w / 2, y: b.cy - b.h / 2, w: b.w, h: b.h };
}

/** Pixel size of the committed crop output. */
export function cropOutputSize(c: CropState, srcW: number, srcH: number): [number, number] {
  const [iw, ih] = imageDims(srcW, srcH, c.orientation);
  return [Math.max(1, Math.round(c.w * iw)), Math.max(1, Math.round(c.h * ih))];
}

/**
 * Bounding box (output-frame pixels) of the straightened image, padded a little,
 * used as the render window for the crop editor so the user sees beyond the crop.
 */
export function straightenedBBox(c: CropState, iw: number, ih: number, pad = 0.04): Rect {
  const b = pixelBox(c, iw, ih);
  const a = (c.angle * Math.PI) / 180;
  const cos = Math.cos(a);
  const sin = Math.sin(a);
  // Map each image corner into the output frame (inverse content rotation).
  const corners: Array<[number, number]> = [[0, 0], [iw, 0], [iw, ih], [0, ih]];
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const [mx, my] of corners) {
    const dx = mx - b.cx;
    const dy = my - b.cy;
    const ox = b.cx + (dx * cos + dy * sin);
    const oy = b.cy + (-dx * sin + dy * cos);
    minX = Math.min(minX, ox); minY = Math.min(minY, oy);
    maxX = Math.max(maxX, ox); maxY = Math.max(maxY, oy);
  }
  const padX = (maxX - minX) * pad;
  const padY = (maxY - minY) * pad;
  return { x: minX - padX, y: minY - padY, w: maxX - minX + 2 * padX, h: maxY - minY + 2 * padY };
}

// ── Constraints ──────────────────────────────────────────────────────────────

/**
 * Largest scale s∈(0,1] such that the crop box, scaled about its center, fits
 * inside the image at the current angle. Found by bisection (robust for any
 * angle/aspect). Returns 1 if it already fits.
 */
function maxScaleInside(c: CropState, iw: number, ih: number): number {
  if (cornersInsideImage(c, iw, ih)) return 1;
  let lo = 0;
  let hi = 1;
  for (let i = 0; i < 24; i++) {
    const mid = (lo + hi) / 2;
    if (cornersInsideImage({ ...c, w: c.w * mid, h: c.h * mid }, iw, ih)) lo = mid;
    else hi = mid;
  }
  return lo;
}

/**
 * Return a valid crop: clamp the center so the box can fit, then shrink about
 * the center until the (possibly rotated) box is inside the image. Used after
 * straighten/rotate changes and as a safety net.
 */
export function constrainCrop(c: CropState, srcW: number, srcH: number): CropState {
  const [iw, ih] = imageDims(srcW, srcH, c.orientation);
  let out: CropState = { ...c };
  // Pull the center toward the image center first so a fit is reachable.
  out.cx = clamp(out.cx, 0, 1);
  out.cy = clamp(out.cy, 0, 1);
  let s = maxScaleInside(out, iw, ih);
  if (s < 1) {
    out = { ...out, w: out.w * s, h: out.h * s };
    // Nudge center toward 0.5 and retry once for off-center boxes.
    if (!cornersInsideImage(out, iw, ih)) {
      out.cx = 0.5; out.cy = 0.5;
      s = maxScaleInside(out, iw, ih);
      out = { ...out, w: out.w * s, h: out.h * s };
    }
  }
  return out;
}

// ── Aspect ratios ────────────────────────────────────────────────────────────

export type AspectPreset = { key: string; label: string; ratio: number | null };

// ratio = output width / height in real pixels. null = free, "orig" handled live.
export const ASPECT_PRESETS: AspectPreset[] = [
  { key: "free", label: "Free", ratio: null },
  { key: "orig", label: "Original", ratio: 0 },
  { key: "1:1", label: "1 × 1", ratio: 1 },
  { key: "2:3", label: "2 × 3", ratio: 2 / 3 },
  { key: "3:2", label: "3 × 2", ratio: 3 / 2 },
  { key: "4:5", label: "4 × 5", ratio: 4 / 5 },
  { key: "5:4", label: "5 × 4", ratio: 5 / 4 },
  { key: "3:4", label: "3 × 4", ratio: 3 / 4 },
  { key: "4:3", label: "4 × 3", ratio: 4 / 3 },
  { key: "5:7", label: "5 × 7", ratio: 5 / 7 },
  { key: "7:5", label: "7 × 5", ratio: 7 / 5 },
  { key: "9:16", label: "9 × 16", ratio: 9 / 16 },
  { key: "16:9", label: "16 × 9", ratio: 16 / 9 },
];

/** Resolve a preset key to a concrete pixel ratio (w/h); null means free. */
export function resolveAspectRatio(key: string, srcW: number, srcH: number, orientation: Orientation): number | null {
  const preset = ASPECT_PRESETS.find((p) => p.key === key);
  if (!preset) return null;
  if (preset.ratio === null) return null;
  if (preset.ratio === 0) {
    const [iw, ih] = imageDims(srcW, srcH, orientation);
    return iw / ih;
  }
  return preset.ratio;
}

/**
 * Fit the largest crop box of the given pixel ratio centered at the box's
 * current center, inside the image at the current angle. Used when the user
 * picks an aspect preset.
 */
export function applyAspectRatio(c: CropState, ratio: number, srcW: number, srcH: number): CropState {
  const [iw, ih] = imageDims(srcW, srcH, c.orientation);
  // Normalized w,h for the target pixel ratio: (w*iw)/(h*ih) = ratio.
  // Start from a unit box and scale to fit.
  let wN = 1;
  let hN = 1;
  // ratio = (wN*iw)/(hN*ih) -> wN/hN = ratio*ih/iw
  const k = (ratio * ih) / iw; // wN = k * hN
  if (k >= 1) { wN = 1; hN = 1 / k; } else { hN = 1; wN = k; }
  let out: CropState = { ...c, w: wN, h: hN };
  const s = maxScaleInside(out, iw, ih);
  out = { ...out, w: wN * s, h: hN * s };
  return constrainCrop(out, srcW, srcH);
}

/** Rotate the orientation by ±90°, swapping crop box dims to keep the framing. */
export function rotate90(c: CropState, dir: 1 | -1): CropState {
  const order: Orientation[] = [0, 90, 180, 270];
  const idx = order.indexOf(c.orientation);
  const next = order[(idx + (dir === 1 ? 1 : 3)) % 4];
  // Swapping orientation between portrait/landscape swaps image dims, so swap
  // the normalized box dims too and rotate the center to track the same region.
  return {
    ...c,
    orientation: next,
    w: c.h,
    h: c.w,
    cx: dir === 1 ? 1 - c.cy : c.cy,
    cy: dir === 1 ? c.cx : 1 - c.cx,
  };
}
