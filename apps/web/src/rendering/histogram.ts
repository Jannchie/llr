/**
 * Histogram rendering.
 *
 * The bins are produced by `PipelineRenderer.readHistogram()` (a small offscreen
 * GPU pass read back as display-encoded pixels), so they always match the shader
 * output exactly — there is no CPU-side mirror of the pipeline to keep in sync.
 */

export interface HistogramBins {
  r: Uint32Array; // 256 bins
  g: Uint32Array;
  b: Uint32Array;
  l: Uint32Array; // luminance
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
  const drawChannel = (chan: Uint32Array, color: string, alpha: number) => {
    ctx.fillStyle = color;
    ctx.globalAlpha = alpha;
    ctx.beginPath();
    ctx.moveTo(0, h);

    for (let i = 0; i < 256; i++) {
      const barH = (chan[i] / maxCount) * h;
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
