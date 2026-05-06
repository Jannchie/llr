/**
 * Tone curve: interactive point-curve with Catmull-Rom spline.
 *
 * Renders an editable curve on a 2D canvas and produces a 256-entry
 * LUT (look-up table) for the WebGL shader.
 */

export interface CurvePoint {
  x: number; // 0-1 input
  y: number; // 0-1 output
}

/** Generate a 256-entry LUT from control points via Catmull-Rom spline. */
export function curveToLUT(points: CurvePoint[]): Float32Array {
  const lut = new Float32Array(2048);
  if (points.length < 2) {
    // Identity
    for (let i = 0; i < 2048; i++) lut[i] = i / 2047;
    return lut;
  }
  // Sort by x
  const sorted = [...points].sort((a, b) => a.x - b.x);
  // Ensure endpoints
  if (sorted[0].x > 0) sorted.unshift({ x: 0, y: 0 });
  if (sorted[sorted.length - 1].x < 1) sorted.push({ x: 1, y: 1 });
  // Evaluate spline at each LUT entry
  for (let i = 0; i < 2048; i++) {
    const t = i / 2047;
    lut[i] = evaluateSpline(sorted, t);
  }
  return lut;
}

/** Default identity curve: two endpoints. */
export function defaultCurve(): CurvePoint[] {
  return [{ x: 0, y: 0 }, { x: 1, y: 1 }];
}

// --- rendering ---

const GRID_COLOR = "#1a1a1a";
const CURVE_COLOR = "#a0b8c8";
const POINT_COLOR = "#d0d0d0";
const POINT_ACTIVE_COLOR = "#fff";
const POINT_RADIUS = 5;

export function renderCurve(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  points: CurvePoint[],
  activeIndex: number = -1,
): void {
  ctx.clearRect(0, 0, w, h);

  // Background
  ctx.fillStyle = "#0a0a0a";
  ctx.fillRect(0, 0, w, h);

  // Grid (quarter-tones)
  ctx.strokeStyle = GRID_COLOR;
  ctx.lineWidth = 0.5;
  for (let i = 1; i < 4; i++) {
    const p = i / 4;
    ctx.beginPath();
    ctx.moveTo(p * w, 0);
    ctx.lineTo(p * w, h);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(0, (1 - p) * h);
    ctx.lineTo(w, (1 - p) * h);
    ctx.stroke();
  }

  // Spline curve
  const sorted = [...points].sort((a, b) => a.x - b.x);
  const toX = (v: number) => v * w;
  const toY = (v: number) => (1 - v) * h;

  ctx.strokeStyle = CURVE_COLOR;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(toX(0), toY(evaluateSpline(sorted, 0)));
  for (let i = 1; i <= 100; i++) {
    const t = i / 100;
    ctx.lineTo(toX(t), toY(evaluateSpline(sorted, t)));
  }
  ctx.stroke();

  // Points
  for (let i = 0; i < points.length; i++) {
    const p = points[i];
    ctx.beginPath();
    ctx.arc(toX(p.x), toY(p.y), i === activeIndex ? POINT_RADIUS : POINT_RADIUS - 1, 0, Math.PI * 2);
    ctx.fillStyle = i === activeIndex ? POINT_ACTIVE_COLOR : POINT_COLOR;
    ctx.fill();
    ctx.strokeStyle = "#000";
    ctx.lineWidth = 1;
    ctx.stroke();
  }
}

/** Find the index of a point near (mx, my) in canvas pixel coords, or -1. */
export function hitTest(points: CurvePoint[], w: number, h: number, mx: number, my: number): number {
  const radius = POINT_RADIUS + 3;
  for (let i = 0; i < points.length; i++) {
    const dx = points[i].x * w - mx;
    const dy = (1 - points[i].y) * h - my;
    if (dx * dx + dy * dy < radius * radius) return i;
  }
  return -1;
}

// --- spline internals ---

function evaluateSpline(points: CurvePoint[], t: number): number {
  if (points.length === 0) return t;
  if (points.length === 1) return points[0].y;
  // Clamp t to point range
  const t0 = points[0].x;
  const t1 = points[points.length - 1].x;
  if (t <= t0) return points[0].y;
  if (t >= t1) return points[points.length - 1].y;

  // Find segment
  let seg = 0;
  for (let i = 0; i < points.length - 1; i++) {
    if (t >= points[i].x && t <= points[i + 1].x) { seg = i; break; }
  }

  // Catmull-Rom: need p0..p3
  const p0 = points[Math.max(seg - 1, 0)];
  const p1 = points[seg];
  const p2 = points[seg + 1];
  const p3 = points[Math.min(seg + 2, points.length - 1)];

  // Normalize t within segment
  const segT = (t - p1.x) / ((p2.x - p1.x) || 1e-6);

  return catmullRom(p0.y, p1.y, p2.y, p3.y, segT);
}

function catmullRom(p0: number, p1: number, p2: number, p3: number, t: number): number {
  const t2 = t * t;
  const t3 = t2 * t;
  return 0.5 * (
    (2 * p1) +
    (-p0 + p2) * t +
    (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2 +
    (-p0 + 3 * p1 - 3 * p2 + p3) * t3
  );
}
