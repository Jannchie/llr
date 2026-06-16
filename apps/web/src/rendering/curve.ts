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

const LUT_SIZE = 2048;

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
  const out = pts
    .filter((p): p is CurvePoint => p != null && typeof (p as CurvePoint).x === "number" && typeof (p as CurvePoint).y === "number")
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
  for (let i = 0; i < LUT_SIZE; i++) {
    lut[i] = clamp01(evaluateSpline(sorted, i / (LUT_SIZE - 1)));
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

/** Linear-sample a 2048-entry LUT at x in [0,1]. */
function sampleLUT(lut: Float32Array, x: number): number {
  const t = clamp01(x) * (LUT_SIZE - 1);
  const i0 = Math.floor(t);
  const i1 = Math.min(LUT_SIZE - 1, i0 + 1);
  return lut[i0] + (lut[i1] - lut[i0]) * (t - i0);
}

/**
 * Bake the full Lightroom curve stack into one interleaved RGB LUT
 * (length 2048*3). Each entry i holds the output for input i/(2047), per channel:
 *   out_c = pointChannel_c( pointRGB( parametric( x ) ) )
 */
export function buildToneCurveLUT(tc: ToneCurve): Float32Array {
  const param = parametricToLUT(tc.parametric);
  const rgb = curveToLUT(tc.rgb);
  const red = curveToLUT(tc.red);
  const green = curveToLUT(tc.green);
  const blue = curveToLUT(tc.blue);
  const out = new Float32Array(LUT_SIZE * 3);
  for (let i = 0; i < LUT_SIZE; i++) {
    const master = sampleLUT(rgb, sampleLUT(param, i / (LUT_SIZE - 1)));
    out[i * 3 + 0] = sampleLUT(red, master);
    out[i * 3 + 1] = sampleLUT(green, master);
    out[i * 3 + 2] = sampleLUT(blue, master);
  }
  return out;
}

// --- rendering ---

const GRID_COLOR = "#1f1f1f";
const POINT_RADIUS = 5;
const SPLIT_RADIUS = 6;

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
    const pMax: ParametricCurve = { ...tc.parametric };
    const pMin: ParametricCurve = { ...tc.parametric };
    pMax[key] = 100;
    pMin[key] = -100;
    const lutMax = parametricToLUT(pMax);
    const lutMin = parametricToLUT(pMin);
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

function evaluateSpline(points: CurvePoint[], t: number): number {
  const n = points.length;
  if (n === 0) return t;
  if (n === 1) return points[0].y;
  const t0 = points[0].x, t1 = points[n - 1].x;
  if (t <= t0) return points[0].y;
  if (t >= t1) return points[n - 1].y;

  if (n === 2) {
    const f = (t - t0) / ((t1 - t0) || 1e-6);
    return points[0].y + (points[1].y - points[0].y) * f;
  }

  // --- Fritsch-Carlson monotone cubic Hermite spline ---
  const sec: number[] = [];
  for (let i = 0; i < n - 1; i++) {
    const dx = points[i + 1].x - points[i].x;
    sec.push(dx > 1e-9 ? (points[i + 1].y - points[i].y) / dx : 0);
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
  let seg = 0;
  for (let i = 0; i < n - 1; i++) {
    if (t >= points[i].x && t <= points[i + 1].x) { seg = i; break; }
  }
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
