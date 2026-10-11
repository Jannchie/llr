/**
 * Brush masks: painted strokes rasterised into the aux array's brush layer
 * (pipeline-renderer AUX_LAYER.brush), one brush component per channel.
 *
 * A stroke lives in source-norm (y down) like an erase stroke, so it stays on
 * the pixels it covered through any crop or rotation; it is rasterised at the
 * aux layer's own size, which is fixed per image, so a preview and its export
 * read the same matte.
 */

export type BrushStroke = {
  points: [number, number][];
  radius: number;   // in units of the frame's long edge
  feather: number;  // 0..1, softness as a fraction of the radius
  flow: number;     // 0..1, how much one stroke adds
  erase?: boolean;  // takes the mask away instead
};

export const BRUSH_SLOTS = 4;

type Canvas2D = OffscreenCanvasRenderingContext2D;

function drawStroke(ctx: Canvas2D, s: BrushStroke, w: number, h: number): void {
  const r = Math.max(0.5, s.radius * Math.max(w, h));
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.lineWidth = 2 * r;
  ctx.strokeStyle = "#fff";
  ctx.fillStyle = "#fff";
  const pts = s.points.map(([u, v]) => [u * w, v * h] as const);
  if (pts.length === 1) {
    ctx.beginPath();
    ctx.arc(pts[0]![0], pts[0]![1], r, 0, Math.PI * 2);
    ctx.fill();
    return;
  }
  ctx.beginPath();
  ctx.moveTo(pts[0]![0], pts[0]![1]);
  for (const [x, y] of pts.slice(1)) ctx.lineTo(x, y);
  ctx.stroke();
}

/**
 * The brush layer for up to BRUSH_SLOTS brush components: RGBA8, rows top-down
 * (source y down), channel k = component k's coverage.
 */
export function rasterBrushes(brushes: readonly (readonly BrushStroke[])[], w: number, h: number): Uint8Array {
  const out = new Uint8Array(w * h * 4);
  const chan = new OffscreenCanvas(w, h);
  const cctx = chan.getContext("2d")!;
  const tmp = new OffscreenCanvas(w, h);
  const tctx = tmp.getContext("2d")!;
  brushes.slice(0, BRUSH_SLOTS).forEach((strokes, k) => {
    cctx.clearRect(0, 0, w, h);
    for (const s of strokes) {
      tctx.clearRect(0, 0, w, h);
      drawStroke(tctx, s, w, h);
      const r = s.radius * Math.max(w, h);
      cctx.save();
      cctx.globalAlpha = Math.min(1, Math.max(0.02, s.flow));
      cctx.globalCompositeOperation = s.erase ? "destination-out" : "source-over";
      const blur = s.feather * r * 0.6;
      if (blur >= 0.5) cctx.filter = `blur(${blur.toFixed(1)}px)`;
      cctx.drawImage(tmp, 0, 0);
      cctx.restore();
    }
    const px = cctx.getImageData(0, 0, w, h).data;
    for (let i = 0; i < w * h; i++) out[4 * i + k] = px[4 * i + 3]!;
  });
  return out;
}

/**
 * Whether `prev` (a copy taken when the layer was last drawn) still holds
 * exactly the strokes of `next`. A laid stroke is never edited, only added,
 * removed or replaced (undo restores copies), so identity says it all — and
 * costs nothing on the frames where nothing changed.
 */
export function sameBrushes(prev: readonly (readonly BrushStroke[])[], next: readonly (readonly BrushStroke[])[]): boolean {
  return prev.length === next.length
    && prev.every((p, i) => p.length === next[i]!.length && p.every((s, j) => s === next[i]![j]));
}
