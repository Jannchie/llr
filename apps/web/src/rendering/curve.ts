/**
 * Tone curve — Lightroom-compatible.
 *
 * Two stacked curves, exactly like Lightroom's Tone Curve panel:
 *   1. Parametric curve  — region sliders (Highlights/Lights/Darks/Shadows)
 *      plus three draggable range splits.
 *   2. Point curve       — RGB master + independent Red / Green / Blue channels.
 *
 * The shader applies the result *per channel* (not luminance-driven), so an RGB
 * point curve behaves like Lightroom's (contrast pushes saturation). We bake the
 * whole stack into a single RGB LUT on the CPU:
 *     out_c = pointChannel_c( pointRGB( parametric( in_c ) ) )   for c in {R,G,B}
 */

import { glslFloat, srgbDecode, srgbEncode } from "./color-spaces";

// Re-exported: this module was their original home, and grading.ts plus the
// render tests import them from here.
export { srgbDecode, srgbEncode };

export interface CurvePoint {
  x: number; // 0-1 input
  y: number; // 0-1 output
}

/** Parametric (region) curve — mirrors Lightroom's crs:Parametric* fields. */
export interface ParametricCurve {
  highlights: number; // -100..100
  lights: number;     // -100..100
  darks: number;      // -100..100
  shadows: number;    // -100..100
  shadowSplit: number;    // 0-100 (Lightroom default 25)
  midtoneSplit: number;   // 0-100 (Lightroom default 50)
  highlightSplit: number; // 0-100 (Lightroom default 75)
}

/** The full tone curve: parametric + point curves (master + per-channel). */
export interface ToneCurve {
  rgb: CurvePoint[];
  red: CurvePoint[];
  green: CurvePoint[];
  blue: CurvePoint[];
  parametric: ParametricCurve;
}

/** Channels the editor can show. "parametric" is the region curve. */
export type ToneChannel = "parametric" | "rgb" | "red" | "green" | "blue";
export type PointChannel = "rgb" | "red" | "green" | "blue";

/**
 * Basic-panel adjustments baked into the display-referred curve chain
 * (ahead of the Tone Curve panel's parametric/point stages, as in Lightroom).
 */
export interface BasicAdjust {
  contrast: number; // -100..100
  blacks: number;   // -100..100
  /**
   * Only the positive half acts here. The camera profile's tone curve
   * asymptotes below 1.0 and flattens the top stops, so a scene-referred gain
   * can never push the white point to clip — blowing the whites has to happen
   * display-referred. Pulling them back is the opposite: display values above
   * white are already clamped, so recovery only exists scene-referred, and
   * negative Whites stays in the shader.
   */
  whites: number;   // -100..100
}

export const DEFAULT_BASIC: BasicAdjust = { contrast: 0, blacks: 0, whites: 0 };

export function isDefaultBasic(b: BasicAdjust): boolean {
  return b.contrast === 0 && b.blacks === 0 && b.whites <= 0;
}

/**
 * Entries in every baked LUT — the bake, the GPU texture and the shader's fetch
 * domain all derive from this one value (see LUT_GLSL).
 */
export const LUT_SIZE = 2048;

/**
 * GLSL chunk: the LUT sampling helper, generated from LUT_SIZE. Inject once near
 * the top of a fragment shader that fetches a baked LUT, so the shader's fetch
 * domain cannot drift from the table this module produces.
 */
export const LUT_GLSL = `
const float LUT_SIZE = ${glslFloat(LUT_SIZE)};

// Entry i holds the output for input i/(LUT_SIZE-1), so map x onto texel centers
// — sampling at x directly is off by up to half a texel across the range.
float lutCoord(float x) { return (x * (LUT_SIZE - 1.0) + 0.5) / LUT_SIZE; }
`;

const clamp = (v: number, lo: number, hi: number) => (v < lo ? lo : v > hi ? hi : v);
const clamp01 = (v: number) => (v < 0 ? 0 : v > 1 ? 1 : v);

// --- defaults ---

/** Default identity point curve: two endpoints. */
export function defaultCurve(): CurvePoint[] {
  return [{ x: 0, y: 0 }, { x: 1, y: 1 }];
}

export function defaultParametric(): ParametricCurve {
  return {
    highlights: 0, lights: 0, darks: 0, shadows: 0,
    shadowSplit: 25, midtoneSplit: 50, highlightSplit: 75,
  };
}

export function defaultToneCurve(): ToneCurve {
  return {
    rgb: defaultCurve(), red: defaultCurve(), green: defaultCurve(), blue: defaultCurve(),
    parametric: defaultParametric(),
  };
}

/** Lightroom point-curve presets (applied to the RGB master channel). */
export const CURVE_PRESETS: Record<string, CurvePoint[]> = {
  Linear: [{ x: 0, y: 0 }, { x: 1, y: 1 }],
  "Medium Contrast": [
    { x: 0, y: 0 }, { x: 0.25, y: 0.219 }, { x: 0.5, y: 0.5 }, { x: 0.75, y: 0.781 }, { x: 1, y: 1 },
  ],
  "Strong Contrast": [
    { x: 0, y: 0 }, { x: 0.25, y: 0.188 }, { x: 0.5, y: 0.5 }, { x: 0.75, y: 0.812 }, { x: 1, y: 1 },
  ],
};

/** Coerce arbitrary/legacy stored data into a valid ToneCurve. */
export function normalizeToneCurve(raw: unknown): ToneCurve {
  const base = defaultToneCurve();
  if (!raw) return base;
  // Legacy persisted edits stored the curve as a flat CurvePoint[] (RGB only).
  if (Array.isArray(raw)) return { ...base, rgb: validPoints(raw) };
  const o = raw as Partial<ToneCurve>;
  return {
    rgb: validPoints(o.rgb),
    red: validPoints(o.red),
    green: validPoints(o.green),
    blue: validPoints(o.blue),
    parametric: { ...base.parametric, ...(o.parametric ?? {}) },
  };
}

function validPoints(pts: unknown): CurvePoint[] {
  if (!Array.isArray(pts) || pts.length < 2) return defaultCurve();
  // isFinite, not typeof: a NaN from corrupt persisted data passes typeof and
  // would poison the whole baked LUT.
  const out = pts
    .filter((p): p is CurvePoint => p != null && Number.isFinite((p as CurvePoint).x) && Number.isFinite((p as CurvePoint).y))
    .map(p => ({ x: clamp01(p.x), y: clamp01(p.y) }));
  return out.length >= 2 ? out : defaultCurve();
}

export function pointsForChannel(tc: ToneCurve, ch: PointChannel): CurvePoint[] {
  return tc[ch];
}

/** True if a point channel is the identity (two diagonal endpoints). */
export function isIdentityPoints(pts: CurvePoint[]): boolean {
  if (pts.length !== 2) return false;
  const a = pts[0], b = pts[1];
  return Math.abs(a.x) < 1e-4 && Math.abs(a.y) < 1e-4 && Math.abs(b.x - 1) < 1e-4 && Math.abs(b.y - 1) < 1e-4;
}

export function isDefaultParametric(p: ParametricCurve): boolean {
  return p.highlights === 0 && p.lights === 0 && p.darks === 0 && p.shadows === 0;
}

// --- LUT generation ---

/** Generate a 2048-entry single-channel LUT from control points (monotone Hermite). */
export function curveToLUT(points: CurvePoint[]): Float32Array {
  const lut = new Float32Array(LUT_SIZE);
  if (points.length < 2) {
    for (let i = 0; i < LUT_SIZE; i++) lut[i] = i / (LUT_SIZE - 1);
    return lut;
  }
  const sorted = [...points].sort((a, b) => a.x - b.x);
  if (sorted[0].x > 0) sorted.unshift({ x: 0, y: 0 });
  if (sorted[sorted.length - 1].x < 1) sorted.push({ x: 1, y: 1 });
  const n = sorted.length;
  const first = sorted[0];
  const last = sorted[n - 1];
  // Tangents are a property of the curve, not the sample: compute them once
  // instead of per LUT entry (evaluating per entry redid the full O(n)
  // Fritsch-Carlson pass 2048 times per channel).
  const m = n > 2 ? splineTangents(sorted) : null;
  let seg = 0; // samples are in ascending x — advance the segment cursor, no search
  for (let i = 0; i < LUT_SIZE; i++) {
    const t = i / (LUT_SIZE - 1);
    let y: number;
    if (t <= first.x) y = first.y;
    else if (t >= last.x) y = last.y;
    else if (!m) {
      y = first.y + ((last.y - first.y) * (t - first.x)) / ((last.x - first.x) || 1e-6);
    } else {
      while (seg < n - 2 && t > sorted[seg + 1].x) seg++;
      y = hermiteAt(sorted, m, seg, t);
    }
    lut[i] = clamp01(y);
  }
  return lut;
}

/**
 * Build the parametric region curve as a 2048-entry LUT.
 *
 * Trade-off: Adobe's exact parametric algorithm is proprietary. This is a
 * faithful approximation — region-centred Gaussian bumps bounded by the split
 * points, re-anchored at the endpoints and forced monotone. The *exported* XMP
 * carries the raw Parametric* values, so Lightroom reproduces the curve exactly;
 * this LUT only drives the in-app preview.
 */
export function parametricToLUT(p: ParametricCurve): Float32Array {
  const lut = new Float32Array(LUT_SIZE);
  // Identity fast path.
  if (isDefaultParametric(p)) {
    for (let i = 0; i < LUT_SIZE; i++) lut[i] = i / (LUT_SIZE - 1);
    return lut;
  }
  const splits = [p.shadowSplit, p.midtoneSplit, p.highlightSplit]
    .map(s => clamp(s / 100, 0.02, 0.98))
    .sort((a, b) => a - b);
  const [s1, s2, s3] = splits;
  // Region centres: shadows | darks | lights | highlights
  const centers = [s1 * 0.5, (s1 + s2) / 2, (s2 + s3) / 2, (s3 + 1) / 2];
  const amps = [p.shadows, p.darks, p.lights, p.highlights].map(v => clamp(v / 100, -1, 1));
  const sigmas = centers.map((c, k) => {
    const prev = k > 0 ? centers[k - 1] : 0;
    const next = k < 3 ? centers[k + 1] : 1;
    return clamp(Math.min(c - prev, next - c) * 0.85, 0.05, 0.3);
  });
  const AMPLITUDE = 0.22; // ±100 slider ≈ ±0.22 output at the region peak
  const disp = (x: number): number => {
    let d = 0;
    for (let k = 0; k < 4; k++) {
      const z = (x - centers[k]) / sigmas[k];
      d += amps[k] * Math.exp(-0.5 * z * z);
    }
    return AMPLITUDE * d;
  };
  const d0 = disp(0), d1 = disp(1);
  for (let i = 0; i < LUT_SIZE; i++) {
    const x = i / (LUT_SIZE - 1);
    // Subtract the endpoint ramp so y(0)=0 and y(1)=1 stay anchored.
    lut[i] = clamp01(x + disp(x) - (d0 * (1 - x) + d1 * x));
  }
  // Force monotone non-decreasing to avoid tonal inversions at extremes.
  for (let i = 1; i < LUT_SIZE; i++) if (lut[i] < lut[i - 1]) lut[i] = lut[i - 1];
  return lut;
}


/**
 * Display-referred Basic-panel curve: Contrast S-curve, then the two endpoint
 * remaps (Whites blows, Blacks crushes). The endpoint remaps run last so each
 * clip point lands exactly where its slider puts it, whatever the contrast
 * shape. Like the parametric approximation above, the constants approximate
 * ACR PV2012's response (the exported XMP carries the raw slider values, so
 * Lightroom applies its own exact interpretation).
 *
 * The shader samples this LUT with *display-linear* values (the sRGB encode
 * comes last), but Lightroom's Basic sliders act on the perceptual axis —
 * middle gray at ~0.46, not the ~0.22 it occupies in display-linear. So the
 * stage runs on the encoded axis and hands back a linear value: the pivot and
 * the endpoint thresholds below all mean what they say on screen.
 */
export function basicCurve(x: number, basic: BasicAdjust): number {
  let y = srgbEncode(clamp01(x));
  const cv = clamp(basic.contrast / 100, -1, 1);
  if (cv !== 0) {
    // Power-pair S-curve: C1 at the pivot with slope 1.45^cv, endpoints pinned.
    // k>1 gives an S (deeper toe/shoulder); k<1 the flattening inverse.
    const P = 0.435; // ≈ middle gray on the encoded axis
    const k = Math.pow(1.45, cv);
    y = y <= P
      ? P * Math.pow(y / P, k)
      : 1 - (1 - P) * Math.pow((1 - y) / (1 - P), k);
  }
  const w = clamp(basic.whites / 100, 0, 1); // negative Whites lives in the shader
  if (w > 0) {
    // White-point scale — the exact mirror of the Blacks crush below
    // (x/(1-t) is (x-t)/(1-t) reflected through x→1-x). +100 clips everything
    // above 0.85 on screen; black point untouched. Pitched a little stronger
    // than the crush, matching how Whites outweighs Blacks in Lightroom.
    const t = 0.15 * w;
    y = Math.min(y / (1 - t), 1);
  }
  const b = clamp(basic.blacks / 100, -1, 1);
  if (b < 0) {
    // Black-point crush: remap [t,1] → [0,1]; -100 clips everything below 0.12.
    const t = 0.12 * -b;
    y = Math.max((y - t) / (1 - t), 0);
  } else if (b > 0) {
    // Fog lift — gentler than the crush, white point untouched.
    const t = 0.08 * b;
    y = t + y * (1 - t);
  }
  return srgbDecode(clamp01(y));
}

/** Linear-sample a 2048-entry LUT at x in [0,1]. */
function sampleLUT(lut: Float32Array, x: number): number {
  const t = clamp01(x) * (LUT_SIZE - 1);
  const i0 = Math.floor(t);
  const i1 = Math.min(LUT_SIZE - 1, i0 + 1);
  return lut[i0] + (lut[i1] - lut[i0]) * (t - i0);
}

/**
 * Bake the full Lightroom curve stack into one interleaved RGBA LUT
 * (length 2048*4). Entry i holds, for input i/2047:
 *   .a       — the master stack: basic (Contrast/Blacks/Whites) → parametric
 *              → RGB master curve. The shader applies it film-like (max/min
 *              channels through the curve, middle channel re-interpolated), so
 *              it must stay a single scalar curve rather than being pre-composed
 *              per channel.
 *   .r/.g/.b — the per-channel point curves, applied per channel after the
 *              master (their input is the master's output).
 */
export function buildToneCurveLUT(tc: ToneCurve, basic: BasicAdjust = DEFAULT_BASIC): Float32Array {
  const param = parametricToLUT(tc.parametric);
  const rgb = curveToLUT(tc.rgb);
  const red = curveToLUT(tc.red);
  const green = curveToLUT(tc.green);
  const blue = curveToLUT(tc.blue);
  const applyBasic = !isDefaultBasic(basic);
  const out = new Float32Array(LUT_SIZE * 4);
  for (let i = 0; i < LUT_SIZE; i++) {
    const x = i / (LUT_SIZE - 1);
    const xb = applyBasic ? basicCurve(x, basic) : x;
    out[i * 4 + 0] = red[i];
    out[i * 4 + 1] = green[i];
    out[i * 4 + 2] = blue[i];
    out[i * 4 + 3] = sampleLUT(rgb, sampleLUT(param, xb));
  }
  return out;
}

// --- rendering ---

const GRID_COLOR = "#1f1f1f";
const POINT_RADIUS = 5;
const SPLIT_RADIUS = 6;

let hoverEnvelopeCache: { key: string; max: Float32Array; min: Float32Array } | null = null;

const CHANNEL_COLOR: Record<ToneChannel, string> = {
  parametric: "#c8cdd4",
  rgb: "#c8cdd4",
  red: "#e8645b",
  green: "#5fcf6b",
  blue: "#5b8def",
};

export function renderToneCurve(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  tc: ToneCurve,
  channel: ToneChannel,
  activeIndex = -1,
  hoverRegion = -1,
): void {
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#0a0a0a";
  ctx.fillRect(0, 0, w, h);

  // Grid (quarter-tones)
  ctx.strokeStyle = GRID_COLOR;
  ctx.lineWidth = 0.5;
  for (let i = 1; i < 4; i++) {
    const p = i / 4;
    ctx.beginPath(); ctx.moveTo(p * w, 0); ctx.lineTo(p * w, h); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(0, (1 - p) * h); ctx.lineTo(w, (1 - p) * h); ctx.stroke();
  }
  // Diagonal reference
  ctx.strokeStyle = "#262626";
  ctx.beginPath(); ctx.moveTo(0, h); ctx.lineTo(w, 0); ctx.stroke();

  const toX = (v: number) => v * w;
  const toY = (v: number) => (1 - v) * h;
  const color = CHANNEL_COLOR[channel];

  // Parametric hover: shade the envelope between the hovered region's slider at its
  // max (+100) and min (-100), every other region left as-is — i.e. the full range
  // that region can move the curve, the way Lightroom previews it on hover.
  if (channel === "parametric" && hoverRegion >= 0) {
    const key = REGION_KEYS[Math.max(0, Math.min(3, hoverRegion))];
    // Memoise the two envelope LUTs: the hover repaints on every mousemove, and
    // neither bound changes until the region or the parametric values do.
    const cacheKey = `${key}|${JSON.stringify(tc.parametric)}`;
    if (!hoverEnvelopeCache || hoverEnvelopeCache.key !== cacheKey) {
      const pMax: ParametricCurve = { ...tc.parametric };
      const pMin: ParametricCurve = { ...tc.parametric };
      pMax[key] = 100;
      pMin[key] = -100;
      hoverEnvelopeCache = { key: cacheKey, max: parametricToLUT(pMax), min: parametricToLUT(pMin) };
    }
    const { max: lutMax, min: lutMin } = hoverEnvelopeCache;
    // Fill between the bounding curves (max forward, min back).
    ctx.beginPath();
    for (let i = 0; i <= 128; i++) {
      const t = i / 128, x = toX(t), y = toY(sampleLUT(lutMax, t));
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    for (let i = 128; i >= 0; i--) {
      const t = i / 128;
      ctx.lineTo(toX(t), toY(sampleLUT(lutMin, t)));
    }
    ctx.closePath();
    ctx.fillStyle = "rgba(94,148,255,0.16)";
    ctx.fill();
    // Faint min/max boundary curves.
    ctx.strokeStyle = "rgba(120,170,255,0.40)";
    ctx.lineWidth = 1;
    for (const lut of [lutMax, lutMin]) {
      ctx.beginPath();
      for (let i = 0; i <= 128; i++) {
        const t = i / 128, x = toX(t), y = toY(sampleLUT(lut, t));
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.stroke();
    }
  }

  // The curve line. Build the channel LUT once, then sample it per point: building
  // a fresh LUT per sample would rebuild the entire 2048-entry table 129× per
  // repaint (and re-run the parametric Gaussians/monotone pass each time), which
  // is pure waste during a drag.
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  const lineLUT = channel === "parametric" ? parametricToLUT(tc.parametric) : curveToLUT(tc[channel]);
  for (let i = 0; i <= 128; i++) {
    const t = i / 128;
    const y = sampleLUT(lineLUT, t);
    if (i === 0) ctx.moveTo(toX(t), toY(y)); else ctx.lineTo(toX(t), toY(y));
  }
  ctx.stroke();

  if (channel === "parametric") {
    renderParametricHandles(ctx, w, h, tc.parametric);
    return;
  }

  // Point handles
  const pts = tc[channel];
  for (let i = 0; i < pts.length; i++) {
    const p = pts[i];
    ctx.beginPath();
    ctx.arc(toX(p.x), toY(p.y), i === activeIndex ? POINT_RADIUS + 1 : POINT_RADIUS, 0, Math.PI * 2);
    ctx.fillStyle = i === activeIndex ? "#fff" : color;
    ctx.fill();
    ctx.strokeStyle = "#000";
    ctx.lineWidth = 1;
    ctx.stroke();
  }
}

function renderParametricHandles(ctx: CanvasRenderingContext2D, w: number, h: number, p: ParametricCurve): void {
  const xs = [p.shadowSplit, p.midtoneSplit, p.highlightSplit].map(s => (s / 100) * w);
  for (const x of xs) {
    // Vertical guide
    ctx.strokeStyle = "rgba(255,255,255,0.12)";
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
    // Triangle handle at the bottom edge
    ctx.fillStyle = "#9aa0a6";
    ctx.beginPath();
    ctx.moveTo(x, h - SPLIT_RADIUS * 1.6);
    ctx.lineTo(x - SPLIT_RADIUS, h);
    ctx.lineTo(x + SPLIT_RADIUS, h);
    ctx.closePath();
    ctx.fill();
  }
}

/** Find the index of a point near (mx, my) in canvas pixel coords, or -1. */
export function hitTest(points: CurvePoint[], w: number, h: number, mx: number, my: number): number {
  const radius = POINT_RADIUS + 4;
  for (let i = 0; i < points.length; i++) {
    const dx = points[i].x * w - mx;
    const dy = (1 - points[i].y) * h - my;
    if (dx * dx + dy * dy < radius * radius) return i;
  }
  return -1;
}

/**
 * Hit-test the three parametric split handles (bottom edge). Returns 0/1/2 for
 * shadow/midtone/highlight split, or -1.
 */
export function hitTestSplit(p: ParametricCurve, w: number, h: number, mx: number, my: number): number {
  if (my < h - SPLIT_RADIUS * 3) return -1; // only the bottom strip grabs splits
  const xs = [p.shadowSplit, p.midtoneSplit, p.highlightSplit].map(s => (s / 100) * w);
  let best = -1, bestDist = SPLIT_RADIUS * 2.5;
  for (let i = 0; i < 3; i++) {
    const d = Math.abs(xs[i] - mx);
    if (d < bestDist) { bestDist = d; best = i; }
  }
  return best;
}

/** Which parametric region (0=shadows..3=highlights) input x falls into. */
export function regionForX(p: ParametricCurve, x: number): number {
  const s1 = p.shadowSplit / 100, s2 = p.midtoneSplit / 100, s3 = p.highlightSplit / 100;
  // Region boundaries at the midpoints between split centres, matching the LUT.
  const c0 = s1 * 0.5, c1 = (s1 + s2) / 2, c2 = (s2 + s3) / 2;
  const b01 = (c0 + c1) / 2, b12 = (c1 + c2) / 2, b23 = (c2 + (s3 + 1) / 2) / 2;
  if (x < b01) return 0;
  if (x < b12) return 1;
  if (x < b23) return 2;
  return 3;
}

/** Parametric region index (0..3) -> ParametricCurve amplitude key. */
const REGION_KEYS = ["shadows", "darks", "lights", "highlights"] as const;

// --- spline internals ---

/** Fritsch-Carlson monotone tangents, one per knot (n >= 3). */
function splineTangents(points: CurvePoint[]): number[] {
  const n = points.length;
  const sec: number[] = new Array(n - 1);
  for (let i = 0; i < n - 1; i++) {
    const dx = points[i + 1].x - points[i].x;
    sec[i] = dx > 1e-9 ? (points[i + 1].y - points[i].y) / dx : 0;
  }
  const m: number[] = new Array(n).fill(0);
  m[0] = sec[0];
  m[n - 1] = sec[n - 2];
  for (let i = 1; i < n - 1; i++) {
    const s0 = sec[i - 1], s1 = sec[i];
    if (s0 * s1 <= 0) { m[i] = 0; continue; }
    const w0 = 2 * s1 + s0;
    const w1 = s1 + 2 * s0;
    // Weighted-harmonic-mean tangent (Fritsch-Carlson). Guard on the divisor, not
    // on (w0+w1): for a locally *decreasing* segment both secants are negative, so
    // w0+w1 < 0 and a ">1e-9" test would wrongly flatten the tangent there.
    const denom = w0 * s0 + w1 * s1;
    m[i] = Math.abs(denom) > 1e-9 ? (s0 * s1 * (w0 + w1)) / denom : 0;
  }
  return m;
}

/** Cubic Hermite on segment `seg` at absolute x `t`, with precomputed tangents. */
function hermiteAt(points: CurvePoint[], m: number[], seg: number, t: number): number {
  const x0 = points[seg].x, y0 = points[seg].y;
  const x1 = points[seg + 1].x, y1 = points[seg + 1].y;
  const dx = x1 - x0;
  if (dx < 1e-9) return y0;
  const m0 = m[seg] * dx;
  const m1 = m[seg + 1] * dx;
  const hh = (t - x0) / dx;
  const h2 = hh * hh;
  const h3 = h2 * hh;
  return y0 * (2 * h3 - 3 * h2 + 1)
       + y1 * (-2 * h3 + 3 * h2)
       + m0 * (h3 - 2 * h2 + hh)
       + m1 * (h3 - h2);
}
