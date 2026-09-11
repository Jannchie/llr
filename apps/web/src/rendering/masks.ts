/**
 * Masking / local adjustments (docs/masking.md). A group is up to MASK_COMPS
 * analytic components — luminance range, colour range, linear or radial
 * gradient, each add / subtract / intersect with an invert — and a set of
 * local slider deltas. Every weight is computed per pixel inside
 * PROCESS_SHADER from a std140 uniform block that packMasks fills; the shader
 * then blends the *parameters* (p + Σ w·Δp), not the results, so the existing
 * blocks run once.
 *
 * Same pattern as tonal-model.ts: this file is the tested TS mirror and the
 * GLSL (MASK_GLSL) is generated from the same constants, so the two cannot
 * drift.
 */

import { computeWbMatrix, smoothstep, type Mat3 } from "./color-spaces";
import { gradingTint } from "./grading";
import { HSL_CENTERS, HSL_SEL_L_FLOOR, SKIN_HUE, SKIN_HUE_HALF, hueWindow } from "./hsl-bands";
import { imageDims, sourceNormToImageNorm, type CropState } from "./crop";

export type MaskOp = "add" | "subtract" | "intersect";
type CompBase = { op: MaskOp; invert: boolean };
export type MaskComponent =
  // lo/hi/feather on the 0..100 perceptual (sRGB-encoded luminance) axis.
  | (CompBase & { type: "luminance"; lo: number; hi: number; featherLo: number; featherHi: number })
  // Oklab hue centre / half-width in radians; chroma gate on the C/L ratio.
  | (CompBase & { type: "color"; hue: number; hueWidth: number; chromaLo: number; chromaHi: number })
  // Oriented image-norm (crop.ts guides space). w = 1 at (x0,y0), 0 at (x1,y1).
  | (CompBase & { type: "linear"; x0: number; y0: number; x1: number; y1: number })
  // Centre / semi-axes in image-norm (rx=ry=0.5 inscribes the frame), angle in
  // degrees, feather as a fraction of the radius.
  | (CompBase & { type: "radial"; cx: number; cy: number; rx: number; ry: number; angle: number; feather: number });
export type MaskType = MaskComponent["type"];

/** exposure in EV, the rest -100..100. contrast/blacks are reserved (§3.3), not applied. */
export type MaskAdjust = {
  exposure: number; temperature: number; tint: number; saturation: number; vibrance: number;
  highlights: number; shadows: number; clarity: number; dehaze: number; hue: number;
  // A colour cast for the selection (Lightroom's local "Color"): wheel hue in
  // degrees and strength 0..100, built into a tint by grading.ts gradingTint.
  tintHue: number; tintSat: number;
  contrast?: number; blacks?: number;
};
export type MaskGroup = {
  id: string; name?: string; enabled: boolean; invert: boolean;
  components: MaskComponent[]; adjust: MaskAdjust;
};

export const MASK_GROUPS = 8;
export const MASK_COMPS = 4;
/** vec4 rows per group: hdr + adjA + adjB + (ΔWB column | tint−1)×3 + MASK_COMPS×3. */
export const GROUP_STRIDE = 6 + MASK_COMPS * 3;
const ROWS = MASK_GROUPS * GROUP_STRIDE;
export const MASK_UBO_BYTES = ROWS * 16;

const TYPE_CODE: Record<MaskType, number> = { luminance: 0, color: 1, linear: 2, radial: 3 };
const OP_CODE: Record<MaskOp, number> = { add: 0, subtract: 1, intersect: 2 };

// Which shader blocks any packed group touches — OR'd into the existing skip
// gates so an untouched frame still skips them (bit layout shared with GLSL).
export const MASK_USE = { exposure: 1, tonal: 2, clarity: 4, dehaze: 8, color: 16 } as const;

// Slider units. Temperature: ±100 ↦ ±4000 K, linear in Kelvin like the global
// slider. Hue: ±100 ↦ ±0.5 rad, the HSL mixer's scale.
export const MASK_TEMP_PER_UNIT = 40;
export const MASK_HUE_RAD = 0.5;
// Chroma gates for the colour selections, on the HSL mixer's C/L ratio. The
// mixer's own window (0.012–0.03) is set low so desaturated colour still takes
// a slider; a *selection* needs the opposite bias — near-neutral shadows must
// fall out or the matte speckles with sensor noise (DSC01157's bokeh) and a
// warm-lit white plate half-joins a skin mask (DSC04568). Foliage reads ~0.04.
export const MASK_COLOR_C0 = 0.03;
export const MASK_COLOR_C1 = 0.08;
export const MASK_SKIN_C0 = 0.04;
export const MASK_SKIN_C1 = 0.10;
// GLSL smoothstep is undefined at e0 == e1, so a zero feather / radius / span
// is packed as this instead: a step the width of a float ulp on the axis.
const EPS = 1e-3;

export function defaultAdjust(): MaskAdjust {
  return { exposure: 0, temperature: 0, tint: 0, saturation: 0, vibrance: 0, highlights: 0, shadows: 0, clarity: 0, dehaze: 0, hue: 0, tintHue: 0, tintSat: 0 };
}

export const ADJUST_KEYS = Object.keys(defaultAdjust()) as (keyof MaskAdjust)[];

function adjustIsZero(a: MaskAdjust): boolean {
  // tintHue alone selects a colour but applies nothing: strength is the gate.
  return ADJUST_KEYS.every(k => k === "tintHue" || !a[k]);
}

/** A fresh component of the given type, framed on the image centre. */
export function defaultComponent<T extends MaskType>(type: T): Extract<MaskComponent, { type: T }> {
  const base: CompBase = { op: "add", invert: false };
  const all: { [K in MaskType]: Extract<MaskComponent, { type: K }> } = {
    luminance: { ...base, type: "luminance", lo: 50, hi: 100, featherLo: 20, featherHi: 0 },
    color: { ...base, type: "color", hue: HSL_CENTERS[5], hueWidth: 0.6, chromaLo: MASK_COLOR_C0, chromaHi: MASK_COLOR_C1 },
    linear: { ...base, type: "linear", x0: 0.5, y0: 0.15, x1: 0.5, y1: 0.55 },
    radial: { ...base, type: "radial", cx: 0.5, cy: 0.5, rx: 0.35, ry: 0.35, angle: 0, feather: 0.5 },
  };
  return all[type];
}

// ── Presets ──

type Optional<T, K extends PropertyKey> = T extends unknown
  ? Omit<T, Extract<K, keyof T>> & Partial<Pick<T, Extract<K, keyof T>>> : never;
export type MaskPreset = { components: Optional<MaskComponent, "op" | "invert">[]; adjust?: Partial<MaskAdjust> };

/** Group templates; omitted fields take op "add", invert false, adjust 0. */
export const MASK_PRESETS = {
  highlights: { components: [{ type: "luminance", lo: 65, hi: 100, featherLo: 20, featherHi: 0 }] },
  shadows:    { components: [{ type: "luminance", lo: 0, hi: 35, featherLo: 0, featherHi: 20 }] },
  midtones:   { components: [{ type: "luminance", lo: 30, hi: 70, featherLo: 20, featherHi: 20 }] },
  // Blue sky only; a grey sky is a highlights or linear job.
  sky:        { components: [{ type: "color", hue: HSL_CENTERS[5], hueWidth: 0.9, chromaLo: MASK_COLOR_C0, chromaHi: MASK_COLOR_C1 },
                             { type: "luminance", op: "intersect", lo: 40, hi: 100, featherLo: 15, featherHi: 0 }] },
  skin:       { components: [{ type: "color", hue: SKIN_HUE, hueWidth: SKIN_HUE_HALF, chromaLo: MASK_SKIN_C0, chromaHi: MASK_SKIN_C1 }] },
  vignette:   { components: [{ type: "radial", cx: 0.5, cy: 0.5, rx: 0.55, ry: 0.55, angle: 0, feather: 0.6, invert: true }],
                adjust: { exposure: -0.7 } },
} satisfies Record<string, MaskPreset>;
export type MaskPresetName = keyof typeof MASK_PRESETS;

export function presetGroup(name: MaskPresetName, id: string): MaskGroup {
  const p: MaskPreset = MASK_PRESETS[name];
  return {
    id, name, enabled: true, invert: false,
    components: p.components.map(c => ({ op: "add", invert: false, ...c } as MaskComponent)),
    adjust: { ...defaultAdjust(), ...p.adjust },
  };
}

// ── Packing ──

/** The 3 vec4 rows of one component: h=(type, op, invert, ·), a, b. */
function componentRows(c: MaskComponent): number[] {
  const h = [TYPE_CODE[c.type], OP_CODE[c.op], c.invert ? 1 : 0, 0];
  switch (c.type) {
    case "luminance":
      return [...h, c.lo / 100, c.hi / 100, Math.max(c.featherLo / 100, EPS), Math.max(c.featherHi / 100, EPS), 0, 0, 0, 0];
    case "color":
      return [...h, c.hue, Math.max(c.hueWidth, EPS), c.chromaLo, Math.max(c.chromaHi, c.chromaLo + EPS), 0, 0, 0, 0];
    case "linear": {
      // Coincident points have no direction; give them the shortest one.
      const degenerate = c.x0 === c.x1 && c.y0 === c.y1;
      return [...h, c.x0, c.y0, c.x1, degenerate ? c.y1 + EPS : c.y1, 0, 0, 0, 0];
    }
    case "radial":
      return [...h, c.cx, c.cy, Math.max(c.rx, EPS), Math.max(c.ry, EPS),
        Math.max(Math.min(c.feather, 1), EPS), (c.angle * Math.PI) / 180, 0, 0];
  }
}

/** texcoord → oriented image-norm, as a column-major mat3 for u_imgFromTex. */
export function imgFromTex(c: CropState): Float32Array {
  // v_texCoord is (su, 1 - sv) — crop.ts imageNormToTexcoord's FLIP_Y — so undo
  // that, then the orientation/flip half, which is affine.
  const f = (tx: number, ty: number) => sourceNormToImageNorm(tx, 1 - ty, c);
  const o = f(0, 0), px = f(1, 0), py = f(0, 1);
  return new Float32Array([px[0] - o[0], px[1] - o[1], 0, py[0] - o[0], py[1] - o[1], 0, o[0], o[1], 1]);
}

export type PackedMasks = {
  masks: Float32Array; maskGroups: number; maskUse: number; maskPreview: number;
  imgFromTex: Float32Array; imgAspect: number;
};

/**
 * Fill the uniform block. Groups that cannot change a pixel — disabled, no
 * components, every delta zero — are dropped here rather than branched around
 * per pixel, except the one being previewed, which still has to produce a
 * weight. `wb` is the global temperature/tint the ΔWB matrices are relative
 * to; `previewId` is the group whose weight the overlay tints (null = off).
 */
export function packMasks(
  groups: readonly MaskGroup[], crop: CropState, srcW: number, srcH: number,
  wb: { temperature: number; tint: number }, previewId: string | null = null,
): PackedMasks {
  const [iw, ih] = imageDims(srcW, srcH, crop.orientation);
  const masks = new Float32Array(ROWS * 4);
  let n = 0, use = 0, preview = -1;
  const m0 = wb.temperature === 6500 && wb.tint === 0 ? null : computeWbMatrix(wb.temperature, wb.tint);
  for (const g of groups) {
    if (n >= MASK_GROUPS) break;
    const isPreview = g.id === previewId;
    if (!g.enabled || !g.components.length) continue;
    if (adjustIsZero(g.adjust) && !isPreview) continue;
    const a = g.adjust;
    const base = n * GROUP_STRIDE * 4;
    const comps = g.components.slice(0, MASK_COMPS);
    masks.set([comps.length, g.invert ? 1 : 0, 0, 0], base);
    masks.set([a.exposure, a.highlights / 100, a.shadows / 100, a.clarity / 100], base + 4);
    masks.set([a.dehaze / 100, a.saturation / 100, a.vibrance / 100, (a.hue / 100) * MASK_HUE_RAD], base + 8);
    if (a.temperature || a.tint || a.tintSat) {
      let d: Mat3 = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
      if (a.temperature || a.tint) {
        const t = Math.min(Math.max(wb.temperature + a.temperature * MASK_TEMP_PER_UNIT, 2000), 12000);
        d = deltaWb(computeWbMatrix(t, wb.tint + a.tint), m0);
      }
      // The colour cast rides the ΔWB rows' spare .w: tint − 1 per channel, so
      // an unset cast is the same zero as an unset matrix.
      const tint = a.tintSat ? gradingTint(a.tintHue, a.tintSat / 100) : [1, 1, 1];
      // Column-major: the shader rebuilds mat3(col0, col1, col2).
      for (let col = 0; col < 3; col++) masks.set([d[0][col], d[1][col], d[2][col], tint[col] - 1], base + 12 + col * 4);
    }
    comps.forEach((c, k) => masks.set(componentRows(c), base + 24 + k * 12));
    if (a.exposure) use |= MASK_USE.exposure;
    if (a.highlights || a.shadows) use |= MASK_USE.tonal;
    if (a.clarity) use |= MASK_USE.clarity;
    if (a.dehaze) use |= MASK_USE.dehaze;
    if (a.saturation || a.vibrance || a.hue) use |= MASK_USE.color;
    if (isPreview) preview = n;
    n++;
  }
  return { masks, maskGroups: n, maskUse: use, maskPreview: preview, imgFromTex: imgFromTex(crop), imgAspect: ih / iw };
}

function deltaWb(mg: Mat3, m0: Mat3 | null): Mat3 {
  const b: Mat3 = m0 ?? [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
  const d = (r: number, c: number) => mg[r][c] - b[r][c];
  return [[d(0, 0), d(0, 1), d(0, 2)], [d(1, 0), d(1, 1), d(1, 2)], [d(2, 0), d(2, 1), d(2, 2)]];
}

// ── TS mirror of the weight maths ──

/**
 * One component's weight from its packed rows (mirrors maskComponent in
 * MASK_GLSL). pImg is oriented image-norm, pLum the 0..1 perceptual luminance,
 * lab the selection colour in Oklab, aspect = ih/iw.
 */
export function componentWeight(
  rows: ArrayLike<number>, pImg: readonly [number, number], pLum: number,
  lab: readonly [number, number, number], aspect: number,
): number {
  const type = rows[0], inv = rows[2] > 0.5;
  const a = [rows[4], rows[5], rows[6], rows[7]], b = [rows[8], rows[9]];
  let w: number;
  if (type === 0) {
    w = smoothstep(a[0] - a[2], a[0], pLum) * (1 - smoothstep(a[1], a[1] + a[3], pLum));
  } else if (type === 1) {
    const ratio = Math.hypot(lab[1], lab[2]) / Math.max(lab[0], HSL_SEL_L_FLOOR);
    w = smoothstep(0, 1, hueWindow(Math.atan2(lab[2], lab[1]), a[0], a[1])) * smoothstep(a[2], a[3], ratio);
  } else {
    let qx = pImg[0] - a[0], qy = (pImg[1] - a[1]) * aspect;
    if (type === 2) {
      const dx = a[2] - a[0], dy = (a[3] - a[1]) * aspect;
      w = 1 - smoothstep(0, 1, (qx * dx + qy * dy) / (dx * dx + dy * dy));
    } else {
      const c = Math.cos(b[1]), s = Math.sin(b[1]);
      [qx, qy] = [(c * qx + s * qy) / a[2], (-s * qx + c * qy) / (a[3] * aspect)];
      w = 1 - smoothstep(1 - b[0], 1, Math.hypot(qx, qy));
    }
  }
  return inv ? 1 - w : w;
}

/** A group's weight: components combined in order, then the group invert (§2.4). */
export function groupWeight(
  g: MaskGroup, pImg: readonly [number, number], pLum: number,
  lab: readonly [number, number, number], aspect: number,
): number {
  let w = 0;
  g.components.slice(0, MASK_COMPS).forEach((c, k) => {
    const wc = componentWeight(componentRows(c), pImg, pLum, lab, aspect);
    w = k === 0 ? wc : c.op === "add" ? 1 - (1 - w) * (1 - wc) : c.op === "subtract" ? w * (1 - wc) : w * wc;
  });
  return g.invert ? 1 - w : w;
}

// ── GLSL emission ──

/**
 * The uniform block and the component weight. Needs hueWindow /
 * hslChromaRatio (HSL_GLSL) and u_imgAspect declared before it.
 */
export const MASK_GLSL = `
const int MASK_STRIDE = ${GROUP_STRIDE};
const int MASK_USE_EXPOSURE = ${MASK_USE.exposure};
const int MASK_USE_TONAL = ${MASK_USE.tonal};
const int MASK_USE_CLARITY = ${MASK_USE.clarity};
const int MASK_USE_DEHAZE = ${MASK_USE.dehaze};
const int MASK_USE_COLOR = ${MASK_USE.color};
// Packed by masks.ts packMasks: MASK_STRIDE vec4 rows per group — hdr
// (count, invert), adjA (expo, hi, sh, clar), adjB (haze, sat, vib, hue),
// three ΔWB columns, then 3 rows per component.
layout(std140) uniform Masks { vec4 m[${ROWS}]; };

// One component's weight (mirrors componentWeight in masks.ts). base is its
// first row: h = (type, op, invert, ·), then a and b.
float maskComponent(int base, vec2 pImg, float pLum, vec3 labSel) {
  vec4 h = m[base], a = m[base + 1], b = m[base + 2];
  int type = int(h.x); float w;
  if (type == 0) w = smoothstep(a.x - a.z, a.x, pLum) * (1.0 - smoothstep(a.y, a.y + a.w, pLum));
  else if (type == 1) w = smoothstep(0.0, 1.0, hueWindow(atan(labSel.z, labSel.y), a.x, a.y))
                        * smoothstep(a.z, a.w, hslChromaRatio(length(labSel.yz), labSel.x));
  else {
    vec2 q = (pImg - a.xy) * vec2(1.0, u_imgAspect);                      // equal-scale units (iw = 1)
    if (type == 2) { vec2 d = (a.zw - a.xy) * vec2(1.0, u_imgAspect);
                     w = 1.0 - smoothstep(0.0, 1.0, dot(q, d) / dot(d, d)); }
    else { float c = cos(b.y), s = sin(b.y);
           q = vec2(c * q.x + s * q.y, -s * q.x + c * q.y) / (a.zw * vec2(1.0, u_imgAspect));
           w = 1.0 - smoothstep(1.0 - b.x, 1.0, length(q)); }
  }
  return h.z > 0.5 ? 1.0 - w : w;
}
`;
