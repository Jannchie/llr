/**
 * CPU-side histogram computation for the image pipeline.
 *
 * Replicates the same operations as the WebGL fragment shader
 * so the histogram reflects the current edit state without
 * requiring a GPU read-back.  Uses pixel sampling for speed.
 */

const LUMA_R = 0.2126;
const LUMA_G = 0.7152;
const LUMA_B = 0.0722;
const PIVOT = 0.18;

export interface HistogramBins {
  r: Uint32Array; // 256 bins
  g: Uint32Array;
  b: Uint32Array;
  l: Uint32Array; // luminance
}

export interface PipelineParams {
  exposure: number;
  contrast: number; // shader uses 1 + contrast/100
  saturation: number; // shader uses 1 + saturation/100
  temperature: number;
  tint: number;
  highlights: number; // fraction, shader uses highlights/100
  shadows: number; // fraction
  whites: number; // fraction
  blacks: number; // fraction
  vibrance: number; // shader uses 1 + vibrance/100
  clarity: number; // added: 0 = off, clamped [-100, 100], shader uses clarity/100
  dehaze: number;  // added: 0 = off, clamped [-100, 100], shader uses dehaze/100
}

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

function smoothstep(edge0: number, edge1: number, x: number): number {
  const t = clamp((x - edge0) / (edge1 - edge0), 0, 1);
  return t * t * (3 - 2 * t);
}

function kelvinToRGB(K: number): [number, number, number] {
  const eff = 13000 - K;
  let r: number, b: number;
  if (eff < 6500) {
    r = 1 + ((6500 - eff) / 6500) * 0.82;
    b = 1 - ((6500 - eff) / 6500) * 0.72;
  } else {
    r = 1 - ((eff - 6500) / 5500) * 0.42;
    b = 1 + ((eff - 6500) / 5500) * 0.92;
  }
  return [clamp(r, 0.2, 5), 1, clamp(b, 0.2, 5)];
}

function linearToSRGB(c: number): number {
  return c <= 0.0031308 ? c * 12.92 : 1.055 * Math.pow(c, 1 / 2.4) - 0.055;
}

function srgbToBin(c: number): number {
  // Map sRGB [0, 1] to bin index [0, 255]
  return clamp(Math.round(c * 255), 0, 255);
}

/**
 * Compute a 256-bin histogram from linear float RGB data
 * by running the full image pipeline for each sampled pixel.
 *
 * @param pixels  Float32Array of RGBRGB… linear float values
 * @param width   Image width in pixels
 * @param height  Image height in pixels
 * @param params  Current edit parameters
 * @param maxSamples  Maximum number of pixels to process (default 60_000)
 */
export function computeHistogram(
  pixels: Float32Array,
  width: number,
  height: number,
  params: PipelineParams,
  maxSamples: number = 60_000,
): HistogramBins {
  const bins: HistogramBins = {
    r: new Uint32Array(256),
    g: new Uint32Array(256),
    b: new Uint32Array(256),
    l: new Uint32Array(256),
  };

  const totalPixels = width * height;
  if (totalPixels === 0 || pixels.length === 0) return bins;

  // Sampling step to stay under maxSamples
  const step = Math.max(1, Math.ceil(Math.sqrt(totalPixels / maxSamples)));

  // Pre-compute WB factors
  const [wbR, wbG, wbB] = kelvinToRGB(params.temperature);
  const tintFactor = params.tint / 100;
  const rAdj = 1 + tintFactor * 0.35;
  const bAdj = 1 + tintFactor * 0.35;
  const gAdj = 1 - Math.abs(tintFactor) * 0.35;
  const wbScaleR = wbR * clamp(rAdj, 0.2, 5);
  const wbScaleG = wbG * clamp(gAdj, 0.2, 5);
  const wbScaleB = wbB * clamp(bAdj, 0.2, 5);

  const exposureMul = Math.pow(2, params.exposure);
  const hStrength = params.highlights * 0.35;
  const sStrength = params.shadows * 0.50;
  const wStrength = params.whites * 1.6;  // stops-based
  const bStrength = params.blacks * 1.8;  // stops-based

  const contrastAmount = params.contrast - 1; /* [-1, 1] */
  const contrastGamma = 1 + contrastAmount * 0.6;
  const useContrast = Math.abs(contrastAmount) > 1e-6;

  const vibranceStr = params.vibrance - 1;
  const useVibrance = Math.abs(vibranceStr) > 1e-6;

  const clarityStr = (params.clarity / 100) * 0.45;
  const dehazeStr = (params.dehaze / 100) * 0.35;
  const dehazeSatBoost = 1 + (params.dehaze / 100) * 0.25;

  for (let iy = 0; iy < height; iy += step) {
    const rowBase = iy * width * 3;
    for (let ix = 0; ix < width; ix += step) {
      const base = rowBase + ix * 3;
      let r = pixels[base];
      let g = pixels[base + 1];
      let b = pixels[base + 2];

      // --- White Balance ---
      r *= wbScaleR;
      g *= wbScaleG;
      b *= wbScaleB;

      // --- Exposure ---
      r *= exposureMul;
      g *= exposureMul;
      b *= exposureMul;

      // --- Tone (smoothstep masks, multiplicative) ---
      const lum = r * LUMA_R + g * LUMA_G + b * LUMA_B;
      const sMask = 1 - smoothstep(0.05, 0.50, lum);
      const hMask = smoothstep(0.50, 0.95, lum);
      const wMask_ = smoothstep(0.60, 0.92, lum);
      const bMask = 1 - smoothstep(0.08, 0.40, lum);
      r *= 1 + sStrength * sMask;
      g *= 1 + sStrength * sMask;
      b *= 1 + sStrength * sMask;
      r *= 1 + hStrength * hMask;
      g *= 1 + hStrength * hMask;
      b *= 1 + hStrength * hMask;
      // Whites: highlight exposure boost
      const wBoost = Math.pow(2, wStrength);
      r = r * (1 - wMask_) + r * wBoost * wMask_;
      g = g * (1 - wMask_) + g * wBoost * wMask_;
      b = b * (1 - wMask_) + b * wBoost * wMask_;
      // Blacks: shadow exposure shift
      const bShift = Math.pow(2, -bStrength);
      r = r * (1 - bMask) + r * bShift * bMask;
      g = g * (1 - bMask) + g * bShift * bMask;
      b = b * (1 - bMask) + b * bShift * bMask;
      r = Math.max(0, r); g = Math.max(0, g); b = Math.max(0, b);

      // --- Contrast (power-law S-curve anchored at 18% gray) ---
      if (useContrast) {
        r = PIVOT * Math.pow(r / PIVOT, contrastGamma);
        g = PIVOT * Math.pow(g / PIVOT, contrastGamma);
        b = PIVOT * Math.pow(b / PIVOT, contrastGamma);
        r = Math.max(0, r); g = Math.max(0, g); b = Math.max(0, b);
      }

      // --- Clarity (mid-tone contrast, bell-curve mask) ---
      if (Math.abs(clarityStr) > 1e-6) {
        const lum2 = r * LUMA_R + g * LUMA_G + b * LUMA_B;
        const midMask = smoothstep(0.05, 0.45, lum2) * (1 - smoothstep(0.55, 0.95, lum2));
        r = Math.max(0, r + (r - 0.5) * clarityStr * midMask);
        g = Math.max(0, g + (g - 0.5) * clarityStr * midMask);
        b = Math.max(0, b + (b - 0.5) * clarityStr * midMask);
      }

      // --- Dehaze (global contrast + saturation boost) ---
      if (Math.abs(dehazeStr) > 1e-6) {
        r = Math.max(0, r + (r - 0.5) * dehazeStr);
        g = Math.max(0, g + (g - 0.5) * dehazeStr);
        b = Math.max(0, b + (b - 0.5) * dehazeStr);
        const ld = r * LUMA_R + g * LUMA_G + b * LUMA_B;
        const cr2 = r - ld, cg2 = g - ld, cb2 = b - ld;
        r = Math.max(0, ld + cr2 * dehazeSatBoost);
        g = Math.max(0, ld + cg2 * dehazeSatBoost);
        b = Math.max(0, ld + cb2 * dehazeSatBoost);
      }

      // --- Vibrance + Saturation ---
      const grey = r * LUMA_R + g * LUMA_G + b * LUMA_B;
      let cr = r - grey, cg = g - grey, cb = b - grey;

      if (useVibrance) {
        const maxChroma = Math.max(Math.abs(cr), Math.abs(cg), Math.abs(cb));
        const mutedMask = clamp(1 - maxChroma * 2.5, 0, 1);
        const vibMul = 1 + vibranceStr * mutedMask;
        cr *= vibMul;
        cg *= vibMul;
        cb *= vibMul;
      }
      cr *= params.saturation;
      cg *= params.saturation;
      cb *= params.saturation;

      r = Math.max(0, grey + cr);
      g = Math.max(0, grey + cg);
      b = Math.max(0, grey + cb);

      // --- Gamma (Linear → sRGB) ---
      const sr = linearToSRGB(r);
      const sg = linearToSRGB(g);
      const sb = linearToSRGB(b);
      const sl = 0.2126 * sr + 0.7152 * sg + 0.0722 * sb;

      // Fill bins
      bins.r[srgbToBin(sr)]++;
      bins.g[srgbToBin(sg)]++;
      bins.b[srgbToBin(sb)]++;
      bins.l[srgbToBin(sl)]++;
    }
  }

  return bins;
}

/**
 * Render histogram bins to a 2D canvas.
 */
export function renderHistogram(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  bins: HistogramBins,
): void {
  // Find max count for scaling
  let maxCount = 1;
  for (let i = 0; i < 256; i++) {
    maxCount = Math.max(maxCount, bins.r[i], bins.g[i], bins.b[i], bins.l[i]);
  }

  const barW = w / 256;

  // Background
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = '#0c0c0c';
  ctx.fillRect(0, 0, w, h);

  // Grid lines (quarter-tones)
  ctx.strokeStyle = '#1a1a1a';
  ctx.lineWidth = 0.5;
  for (const x of [0.25, 0.5, 0.75]) {
    const px = x * w;
    ctx.beginPath();
    ctx.moveTo(px, 0);
    ctx.lineTo(px, h);
    ctx.stroke();
  }

  // Draw RGB channels as filled overlapping semi-transparent areas
  const drawChannel = (bins: Uint32Array, color: string, alpha: number) => {
    ctx.fillStyle = color;
    ctx.globalAlpha = alpha;
    ctx.beginPath();
    ctx.moveTo(0, h);

    for (let i = 0; i < 256; i++) {
      const barH = (bins[i] / maxCount) * h;
      ctx.lineTo(i * barW, h - barH);
    }
    ctx.lineTo(w, h);
    ctx.closePath();
    ctx.fill();
  };

  // Order: R, G, B so they overlap nicely
  drawChannel(bins.r, '#e05555', 0.6);
  drawChannel(bins.g, '#55b855', 0.6);
  drawChannel(bins.b, '#5577d5', 0.6);

  // Luminance as thin line on top
  ctx.globalAlpha = 0.9;
  ctx.strokeStyle = '#d8d8d8';
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let i = 0; i < 256; i++) {
    const barH = (bins.l[i] / maxCount) * h;
    ctx.lineTo(i * barW, h - barH);
  }
  ctx.stroke();

  // Clipping warnings
  const clipWarn = (edge: number) => {
    const leftWarn = bins.r[0] + bins.g[0] + bins.b[0];
    const rightWarn = bins.r[255] + bins.g[255] + bins.b[255];
    if (edge === 0 && leftWarn > 0) {
      // Shadow clipping triangle (top-left)
      ctx.globalAlpha = 0.9;
      ctx.fillStyle = '#4488cc';
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.lineTo(10, 0);
      ctx.lineTo(0, 10);
      ctx.closePath();
      ctx.fill();
    }
    if (edge === 1 && rightWarn > 0) {
      // Highlight clipping triangle (top-right)
      ctx.globalAlpha = 0.9;
      ctx.fillStyle = '#cc4444';
      ctx.beginPath();
      ctx.moveTo(w, 0);
      ctx.lineTo(w - 10, 0);
      ctx.lineTo(w, 10);
      ctx.closePath();
      ctx.fill();
    }
  };
  clipWarn(0);
  clipWarn(1);

  ctx.globalAlpha = 1;
}
