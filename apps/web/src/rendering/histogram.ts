/**
 * Histogram rendering.
 *
 * The bins are produced by `PipelineRenderer.readHistogram()` (a full-image GPU
 * scatter pass read back as bin counts), so they always match the shader output
 * exactly — there is no CPU-side mirror of the pipeline to keep in sync.
 */

export interface HistogramBins {
  r: Uint32Array; // 256 bins
  g: Uint32Array;
  b: Uint32Array;
  l: Uint32Array; // luminance
}

/** Vertical-axis compression for the bin counts. */
export type HistogramScale = "linear" | "sqrt" | "log";

export interface HistogramOptions {
  /**
   * How bin counts map to bar height. Photo bin counts span a huge dynamic
   * range (a flat sky can be 50× any other tone), so a linear axis lets one
   * spike crush everything else flat. Default "sqrt" — a gentle compression
   * that reveals shadow/highlight structure without over-amplifying the noise
   * floor the way "log" does.
   */
  scale?: HistogramScale;
}

// A channel's edge bin (pure black / pure white) must exceed this fraction of
// the total sample count before its clipping indicator lights. A handful of
// legitimately black or specular-white pixels exists in almost every frame; an
// absolute `> 0` test pins the warning permanently on and destroys its signal.
const CLIP_FRACTION = 0.0002; // 0.02% of pixels
const CLIP_MIN_PX = 3;

// Additive channel colours. Drawn with the "lighter" composite op so overlap
// behaves like real light: R+G→yellow, all three→white (i.e. neutral tones).
const COL_R = "#e1373c";
const COL_G = "#46be55";
const COL_B = "#466eeb";

/**
 * Robust max: ignore the clipping bins (0 and 255). A pure-black or blown-out
 * background piles an enormous count there that would otherwise flatten every
 * visible tone. Edge bars are drawn clamped to the top instead of setting the
 * scale.
 */
export function robustMax(bins: HistogramBins): number {
  let maxCount = 1;
  for (let i = 1; i < 255; i++) {
    maxCount = Math.max(maxCount, bins.r[i], bins.g[i], bins.b[i], bins.l[i]);
  }
  return maxCount;
}

/** Bin count → [0, 1] bar height for the given vertical-axis compression. */
export function binScaler(scale: HistogramScale, maxCount: number): (c: number) => number {
  const logDenom = Math.log1p(maxCount);
  return (c: number): number => {
    if (c <= 0) return 0;
    if (scale === "log") return Math.min(1, Math.log1p(c) / logDenom);
    const v = Math.min(1, c / maxCount);
    return scale === "sqrt" ? Math.sqrt(v) : v;
  };
}

/**
 * Clipping-indicator colours for the two edges, or null when the railed pixel
 * count stays below threshold. The colour encodes *which* channels rail
 * (white = all three, yellow = R+G, …), so it distinguishes overall over/
 * under-exposure from a single channel saturating.
 */
export function clipIndicators(bins: HistogramBins): { shadow: string | null; highlight: string | null } {
  const total = bins.l.reduce((s, v) => s + v, 0);
  const thr = Math.max(CLIP_MIN_PX, total * CLIP_FRACTION);
  const color = (rc: number, gc: number, bc: number): string | null => {
    const r = rc > thr, g = gc > thr, b = bc > thr;
    if (!r && !g && !b) return null;
    return `rgb(${r ? 255 : 0}, ${g ? 255 : 0}, ${b ? 255 : 0})`;
  };
  return {
    shadow: color(bins.r[0], bins.g[0], bins.b[0]),
    highlight: color(bins.r[255], bins.g[255], bins.b[255]),
  };
}

/** What the assistant's `measure` tool reports, in display-encoded 0..255. */
export interface HistogramMeasure {
  luminance: { p1: number; p5: number; p50: number; p95: number; p99: number; mean: number };
  clipped: { shadows_pct: number; highlights_pct: number; shadow_channels: string | null; highlight_channels: string | null };
}

// Which channels rail at an edge bin, named the way the clipping indicator
// colours them ("R+G" is the yellow triangle).
function railedChannels(bins: HistogramBins, i: 0 | 255, thr: number): string | null {
  const names = (["r", "g", "b"] as const).filter(c => bins[c][i] > thr).map(c => c.toUpperCase());
  return names.length ? names.join("+") : null;
}

/**
 * Luminance percentiles and clipped fractions from the bins alone. Percentiles
 * walk the cumulative count; the clip threshold is clipIndicators' so the two
 * never disagree about whether a frame clips.
 */
export function measureHistogram(bins: HistogramBins): HistogramMeasure {
  const total = bins.l.reduce((s, v) => s + v, 0);
  const pct = (n: number) => Math.round((n / Math.max(1, total)) * 1000) / 10;
  let acc = 0, sum = 0;
  const targets = [0.01, 0.05, 0.5, 0.95, 0.99];
  const p: number[] = [];
  for (let i = 0; i < 256; i++) {
    acc += bins.l[i];
    sum += bins.l[i] * i;
    while (p.length < targets.length && acc >= targets[p.length] * total && total > 0) p.push(i);
  }
  while (p.length < targets.length) p.push(0);
  const thr = Math.max(CLIP_MIN_PX, total * CLIP_FRACTION);
  return {
    luminance: { p1: p[0], p5: p[1], p50: p[2], p95: p[3], p99: p[4], mean: total ? Math.round(sum / total) : 0 },
    clipped: {
      shadows_pct: pct(bins.l[0]), highlights_pct: pct(bins.l[255]),
      shadow_channels: railedChannels(bins, 0, thr), highlight_channels: railedChannels(bins, 255, thr),
    },
  };
}

/**
 * Region means (top/middle/bottom thirds of the luminance) and mean chroma
 * ((max-min)/255, so 0 is grey and 1 a pure primary) from a small RGBA
 * read-back, top-down rows.
 */
export function measurePixels(rgba: Uint8ClampedArray | Uint8Array, w: number, h: number): { regions: { top: number; middle: number; bottom: number }; chroma_mean: number } {
  const sums = [0, 0, 0], counts = [0, 0, 0];
  let chroma = 0;
  for (let y = 0; y < h; y++) {
    const band = Math.min(2, Math.floor((y * 3) / h));
    for (let x = 0; x < w; x++) {
      const i = (y * w + x) * 4;
      const r = rgba[i], g = rgba[i + 1], b = rgba[i + 2];
      sums[band] += 0.2126 * r + 0.7152 * g + 0.0722 * b;
      counts[band]++;
      chroma += (Math.max(r, g, b) - Math.min(r, g, b)) / 255;
    }
  }
  const mean = (i: number) => counts[i] ? Math.round(sums[i] / counts[i]) : 0;
  const n = w * h;
  return { regions: { top: mean(0), middle: mean(1), bottom: mean(2) }, chroma_mean: n ? Math.round((chroma / n) * 100) / 100 : 0 };
}

// The clipping indicator in words, for a reader who gets numbers instead of
// the histogram plot. Only speaks when a threshold trips, so silence means
// "nothing to fix here".
export function measureHint(m: HistogramMeasure): string | undefined {
  const hints: string[] = [];
  if (m.clipped.highlights_pct >= 0.5) hints.push(`highlights clip${m.clipped.highlight_channels ? ` in ${m.clipped.highlight_channels}` : ""}: lower highlights/whites or exposure`);
  if (m.clipped.shadows_pct >= 0.5) hints.push(`shadows clip${m.clipped.shadow_channels ? ` in ${m.clipped.shadow_channels}` : ""}: raise shadows/blacks or exposure`);
  if (m.luminance.p50 < 50) hints.push("median is dark; the picture may read as underexposed");
  else if (m.luminance.p50 > 200) hints.push("median is bright; the picture may read as washed out");
  return hints.length ? hints.join("; ") : undefined;
}

/**
 * Render histogram bins to a 2D canvas.
 */
export function renderHistogram(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  bins: HistogramBins,
  opts: HistogramOptions = {},
): void {
  const norm = binScaler(opts.scale ?? "sqrt", robustMax(bins));

  const barW = w / 256;

  // Background
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#0c0c0c";
  ctx.fillRect(0, 0, w, h);

  // Grid lines (quarter-tones)
  ctx.strokeStyle = "#1a1a1a";
  ctx.lineWidth = 0.5;
  for (const x of [0.25, 0.5, 0.75]) {
    const px = x * w;
    ctx.beginPath();
    ctx.moveTo(px, 0);
    ctx.lineTo(px, h);
    ctx.stroke();
  }

  // RGB channels as filled areas with additive ("lighter") compositing, so the
  // overlaps read as the colours real light would produce.
  ctx.globalCompositeOperation = "lighter";
  const drawChannel = (chan: Uint32Array, color: string) => {
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.moveTo(0, h);
    for (let i = 0; i < 256; i++) {
      ctx.lineTo(i * barW, h - norm(chan[i]) * h);
    }
    ctx.lineTo(w, h);
    ctx.closePath();
    ctx.fill();
  };
  drawChannel(bins.r, COL_R);
  drawChannel(bins.g, COL_G);
  drawChannel(bins.b, COL_B);
  ctx.globalCompositeOperation = "source-over";

  // Luminance as a thin line on top
  ctx.globalAlpha = 0.85;
  ctx.strokeStyle = "#d8d8d8";
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let i = 0; i < 256; i++) {
    ctx.lineTo(i * barW, h - norm(bins.l[i]) * h);
  }
  ctx.stroke();
  ctx.globalAlpha = 1;

  // Clipping warnings — thresholded and channel-aware (see clipIndicators).
  const { shadow, highlight } = clipIndicators(bins);
  if (shadow) {
    ctx.fillStyle = shadow;
    ctx.beginPath();
    ctx.moveTo(0, 0);
    ctx.lineTo(10, 0);
    ctx.lineTo(0, 10);
    ctx.closePath();
    ctx.fill();
  }
  if (highlight) {
    ctx.fillStyle = highlight;
    ctx.beginPath();
    ctx.moveTo(w, 0);
    ctx.lineTo(w - 10, 0);
    ctx.lineTo(w, 10);
    ctx.closePath();
    ctx.fill();
  }
}
