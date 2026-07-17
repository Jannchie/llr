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

/**
 * Clamp a normalized box center on one axis, given the box's normalized
 * half-extent on it. An axis with no slack left (the box spans it) has exactly
 * one legal center, so don't let float noise in `half` push the box out.
 */
function clampCenter(v: number, half: number): number {
  return half >= 0.5 ? 0.5 : clamp(v, half, 1 - half);
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
 * Return a valid crop: translate the center back so the (possibly rotated) box
 * fits, shrinking about the image center only when the box is too large to fit
 * at any position. Used after straighten/rotate changes and as a safety net.
 */
export function constrainCrop(c: CropState, srcW: number, srcH: number): CropState {
  const [iw, ih] = imageDims(srcW, srcH, c.orientation);
  const out: CropState = { ...c };
  // Half-extents (image px) of the rotated box's axis-aligned bounds; the
  // corners are inside the image iff the center keeps these margins.
  const a = (c.angle * Math.PI) / 180;
  const cos = Math.abs(Math.cos(a));
  const sin = Math.abs(Math.sin(a));
  const halfExtents = (): [number, number] => [
    (out.w * iw * cos + out.h * ih * sin) / 2,
    (out.w * iw * sin + out.h * ih * cos) / 2,
  ];
  let [hx, hy] = halfExtents();
  if (hx * 2 > iw || hy * 2 > ih) {
    // Too large to fit at any position: search the scale from the image center
    // (the only place an oversized box can fit), then hand the shrunken box back
    // to the translation clamp so the axis that had room keeps its position.
    const s = maxScaleInside({ ...out, cx: 0.5, cy: 0.5 }, iw, ih);
    out.w *= s;
    out.h *= s;
    [hx, hy] = halfExtents();
  }
  out.cx = clampCenter(out.cx, hx / iw);
  out.cy = clampCenter(out.cy, hy / ih);
  return out;
}

// ── Aspect ratios ────────────────────────────────────────────────────────────

export type AspectPreset = { key: string; label: string; ratio: number | null };

// Lightroom-style orientation-agnostic presets: each ratio is listed once and
// stored as long/short (≥ 1); the resolved pixel ratio follows the crop box's
// current landscape/portrait orientation (swap with the X button/key).
// null = free, 0 = the image's own ratio ("Original").
export const ASPECT_PRESETS: AspectPreset[] = [
  { key: "free", label: "Free", ratio: null },
  { key: "orig", label: "Original", ratio: 0 },
  { key: "1:1", label: "1 × 1", ratio: 1 },
  { key: "4:5", label: "4 × 5 / 8 × 10", ratio: 5 / 4 },
  { key: "8.5:11", label: "8.5 × 11", ratio: 11 / 8.5 },
  { key: "5:7", label: "5 × 7", ratio: 7 / 5 },
  { key: "4:3", label: "4 × 3", ratio: 4 / 3 },
  { key: "2:3", label: "2 × 3 / 4 × 6", ratio: 3 / 2 },
  { key: "16:10", label: "16 × 10", ratio: 16 / 10 },
  { key: "16:9", label: "16 × 9", ratio: 16 / 9 },
  { key: "1:2", label: "1 × 2", ratio: 2 },
  { key: "65:24", label: "65 × 24 (XPan)", ratio: 65 / 24 },
];

/** Prefix for user-entered ratios; full keys look like "custom:16:10". */
export const CUSTOM_ASPECT_PREFIX = "custom:";

export function customAspectKey(w: number, h: number): string {
  return `${CUSTOM_ASPECT_PREFIX}${w}:${h}`;
}

/** Parse a "custom:w:h" key into [w, h]; null if malformed/degenerate. */
export function parseCustomAspect(key: string): [number, number] | null {
  if (!key.startsWith(CUSTOM_ASPECT_PREFIX)) return null;
  const parts = key.slice(CUSTOM_ASPECT_PREFIX.length).split(":");
  if (parts.length !== 2) return null;
  const w = Number(parts[0]);
  const h = Number(parts[1]);
  if (!Number.isFinite(w) || !Number.isFinite(h) || w <= 0 || h <= 0) return null;
  return [w, h];
}

/** Reduce a (possibly decimal, e.g. 8.5:11) w:h pair to an integer fraction. */
function toIntFraction(w: number, h: number): [number, number] | null {
  if (!Number.isFinite(w) || !Number.isFinite(h) || w <= 0 || h <= 0) return null;
  let scale = 1;
  while (scale < 1e6 && (!Number.isInteger(w * scale) || !Number.isInteger(h * scale))) scale *= 10;
  const p = Math.round(w * scale);
  const q = Math.round(h * scale);
  const g = gcd(p, q);
  return [p / g, q / g];
}

/**
 * Resolve an aspect key (preset or "custom:w:h") to a reduced integer w:h
 * fraction, oriented to match the crop box's current landscape/portrait
 * orientation; null means free. The exact fraction lets exports snap their
 * pixel dimensions to the true ratio instead of rounding each axis.
 */
export function resolveAspectFraction(key: string, srcW: number, srcH: number, c: CropState): [number, number] | null {
  const [iw, ih] = imageDims(srcW, srcH, c.orientation);
  let base: [number, number] | null;
  const custom = parseCustomAspect(key);
  if (custom) {
    base = toIntFraction(custom[0], custom[1]);
  } else {
    const preset = ASPECT_PRESETS.find((p) => p.key === key);
    if (!preset || preset.ratio === null) return null;
    if (preset.ratio === 0) {
      base = toIntFraction(iw, ih);
    } else {
      const parts = key.split(":");
      base = toIntFraction(Number(parts[0]), Number(parts[1]));
    }
  }
  if (!base) return null;
  let [p, q] = base;
  if (p < q) [p, q] = [q, p]; // long:short
  const landscape = c.w * iw >= c.h * ih;
  return landscape ? [p, q] : [q, p];
}

/**
 * Resolve an aspect key to a concrete pixel ratio (w/h) oriented to match the
 * crop box's current landscape/portrait orientation; null means free.
 */
export function resolveAspectRatio(key: string, srcW: number, srcH: number, c: CropState): number | null {
  const f = resolveAspectFraction(key, srcW, srcH, c);
  return f ? f[0] / f[1] : null;
}

/**
 * Committed output size snapped to an exact integer multiple of the locked
 * aspect fraction (e.g. 4:3 → 6228×4671, never 6229×4672). Falls back to the
 * nominal rounded size for free crops or when the box doesn't actually match
 * the fraction (legacy state), where snapping would distort.
 */
export function cropOutputSizeForAspect(
  c: CropState, srcW: number, srcH: number, fraction: [number, number] | null,
): [number, number] {
  const [ow, oh] = cropOutputSize(c, srcW, srcH);
  if (!fraction) return [ow, oh];
  const [p, q] = fraction;
  if (Math.abs((ow / oh) / (p / q) - 1) > 0.01) return [ow, oh];
  const k = Math.max(1, Math.min(Math.round(ow / p), Math.round(oh / q)));
  return [p * k, q * k];
}

/**
 * Approximate a pixel ratio as a small "w:h" fraction (denominator ≤ 20),
 * used to seed the custom-aspect inputs from the current crop box.
 */
export function ratioToFraction(ratio: number): [number, number] {
  let best: [number, number] = [1, 1];
  let bestErr = Infinity;
  for (let den = 1; den <= 20; den++) {
    const num = Math.max(1, Math.round(ratio * den));
    const err = Math.abs(num / den - ratio);
    if (err < bestErr - 1e-12) { bestErr = err; best = [num, den]; }
  }
  const g = gcd(best[0], best[1]);
  return [best[0] / g, best[1] / g];
}

function gcd(a: number, b: number): number {
  while (b) { const t = a % b; a = b; b = t; }
  return a;
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
  // Image space is F(R_orient(source)): a single mirror conjugates the
  // orientation step into its inverse, so a lone flip reverses the turn the user
  // sees. Two flips are a 180° turn, which commutes — direction is unaffected.
  const d = c.flipH !== c.flipV ? -dir : dir;
  const order: Orientation[] = [0, 90, 180, 270];
  const idx = order.indexOf(c.orientation);
  const next = order[(idx + (d === 1 ? 1 : 3)) % 4];
  // Swapping orientation between portrait/landscape swaps image dims, so swap
  // the normalized box dims too and rotate the center to track the same region.
  // The box lives in image space (what the user sees turning), so it follows
  // `dir` even where the orientation step had to be conjugated.
  return {
    ...c,
    orientation: next,
    w: c.h,
    h: c.w,
    cx: dir === 1 ? 1 - c.cy : c.cy,
    cy: dir === 1 ? c.cx : 1 - c.cx,
  };
}
