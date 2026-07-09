/**
 * Single-pass WebGL2 pipeline renderer.
 *
 * All per-pixel operations are fused into one shader.
 * Renders directly to canvas — no FBO ping-pong needed.
 */

import { MASK_BLUR_SHADER, MASK_DOWNSAMPLE_SHADER, MASK_VERTEX_SHADER, PASSES, VERTEX_SHADER } from "./passes";
import { computeWbGain, PROPHOTO_Y } from "./color-spaces";
import type { HistogramBins } from "./histogram";

// Contrast and Blacks are not here: they are display-referred and baked into
// the tone-curve LUT (curve.ts BasicAdjust), not shader uniforms.
export interface EditParams {
  exposure: number; saturation: number;
  temperature: number; tint: number;
  highlights: number; shadows: number; whites: number;
  vibrance: number; clarity: number; dehaze: number;
  // HSL (8 ranges, each [-1, 1])
  hslH: number[]; hslS: number[]; hslL: number[];
  // Color Grading (all [-1, 1] except blend [0, 1])
  gradShH: number; gradShS: number;
  gradMdH: number; gradMdS: number;
  gradHlH: number; gradHlS: number;
  gradBlend: number; gradBalance: number;
  // View transform: 0 = Lightroom-style, 1 = AgX. Display gamut: 0 = sRGB, 1 = P3.
  viewTransform: number; displayGamut: number;
}

const HSL_ZERO = [0, 0, 0, 0, 0, 0, 0, 0];

// Long-edge cap for the processed image the histogram is binned from. Each
// update re-runs the full pipeline shader at this size, so the cap trades bin
// smoothness/clipping accuracy against per-update GPU cost. The GPU path scatters
// on-device (16 KB read-back regardless of size) so it can afford a denser sample
// than the CPU path (which loops over every pixel in JS after a full read-back).
const HISTO_LONG_GPU = 512;
const HISTO_LONG_CPU = 256;

// Long-edge cap for the blurred log-luminance mask (Highlights/Shadows
// locality). Fixed regardless of source size so the blur is a constant
// fraction of the frame and preview/export masks match by construction.
const MASK_LONG = 256;

export const DEFAULT_PARAMS: EditParams = {
  exposure: 0, saturation: 1,
  temperature: 6500, tint: 0,
  highlights: 0, shadows: 0, whites: 0,
  vibrance: 1, clarity: 0, dehaze: 0,
  hslH: [...HSL_ZERO], hslS: [...HSL_ZERO], hslL: [...HSL_ZERO],
  gradShH: 0, gradShS: 0, gradMdH: 0, gradMdS: 0,
  gradHlH: 0, gradHlS: 0, gradBlend: 0, gradBalance: 0,
  viewTransform: 0, displayGamut: 0,
};

export class PipelineRenderer {
  private gl: WebGL2RenderingContext;
  private program: WebGLProgram;
  private uniforms: Record<string, WebGLUniformLocation | null> = {};
  private vao: WebGLVertexArrayObject;
  private sourceTex: WebGLTexture | null = null;
  private curveLutTex: WebGLTexture | null = null;
  private profileLutTex: WebGLTexture | null = null;
  private hasProfileCurve = false;
  private texWidth = 0;
  private texHeight = 0;
  // Output (canvas / render) dimensions — equal to the texture dims for an
  // un-cropped frame, but the crop box / straighten bbox otherwise.
  private outWidth = 0;
  private outHeight = 0;
  // Preview render scale (0–1): the canvas drawing buffer is rendered at this
  // fraction of the logical output size and CSS-upscaled to fit. 1 = full res;
  // off-screen / export renderers never shrink it.
  private previewScale = 1;
  // Affine output→source-texcoord map (crop / straighten / flip / rotate) and
  // the workspace fill used for out-of-image areas in the crop editor.
  private texXform: Float32Array = new Float32Array([1, 0, 0, 0, -1, 0, 0, 1, 1]); // identity (full frame)
  private bgColor: [number, number, number] = [0.08, 0.08, 0.09];
  private lastParams: EditParams = DEFAULT_PARAMS;
  // Offscreen target holding the processed, display-encoded image the histogram
  // is computed from (RGBA8).
  private histoFbo: WebGLFramebuffer | null = null;
  private histoTex: WebGLTexture | null = null;
  private histoW = 0;
  private histoH = 0;
  // GPU histogram: a 256×4 float accumulation target (rows = R,G,B,L) plus the
  // scatter program that bins every pixel into it. Built lazily on first use.
  private histoBinFbo: WebGLFramebuffer | null = null;
  private histoBinTex: WebGLTexture | null = null;
  private histoProgram: WebGLProgram | null = null;
  private histoVao: WebGLVertexArrayObject | null = null;
  private histoUniforms: {
    u_src: WebGLUniformLocation | null;
    u_srcW: WebGLUniformLocation | null;
  } | null = null;
  /** Whether a float render target we can additively blend into is available. */
  private histoGpuSupported = false;
  // Blurred log2-luminance mask (see MASK_* shaders): built once per
  // uploadImage; per-frame WB/exposure shifts are applied additively in the
  // main shader via u_maskShift. Null when unsupported -> u_hasMask = 0 and
  // the shader falls back to per-pixel region weights.
  private maskTex: WebGLTexture | null = null;
  private maskProgDown: WebGLProgram | null = null;
  private maskProgBlur: WebGLProgram | null = null;
  private maskBlurDir: WebGLUniformLocation | null = null;
  private maskVao: WebGLVertexArrayObject | null = null;
  /** Whether R16F render targets are available for the mask pre-pass. */
  private maskSupported = false;
  /** Whether the browser exposes a wide-gamut drawing buffer. */
  readonly p3Supported: boolean = false;

  constructor(private canvas: HTMLCanvasElement) {
    const gl = canvas.getContext("webgl2", {
      premultipliedAlpha: false, alpha: false, antialias: false,
    }) as WebGL2RenderingContext | null;
    if (!gl) throw new Error("WebGL2 not available");
    this.gl = gl;
    this.p3Supported = "drawingBufferColorSpace" in gl;
    // Full-image GPU histogram needs a float colour buffer we can additively
    // blend into. Calling getExtension also enables it. Where unavailable,
    // readHistogram() falls back to the CPU read-back path.
    const floatRenderable = !!gl.getExtension("EXT_color_buffer_float");
    this.histoGpuSupported = floatRenderable && !!gl.getExtension("EXT_float_blend");
    this.maskSupported = floatRenderable;

    const pass = PASSES[0];
    this.program = this.compileProgram(pass.fsSource);
    for (const name of pass.uniforms) this.uniforms[name] = gl.getUniformLocation(this.program, name);
    this.uniforms["u_input"] = gl.getUniformLocation(this.program, "u_input");
    this.uniforms["u_curve_lut"] = gl.getUniformLocation(this.program, "u_curve_lut");
    this.uniforms["u_profile_lut"] = gl.getUniformLocation(this.program, "u_profile_lut");
    this.uniforms["u_mask_lum"] = gl.getUniformLocation(this.program, "u_mask_lum");
    this.vao = this.createFullScreenQuad();

    // Upload identity LUTs as defaults (user tone curve + DCP profile curve).
    const identity = new Float32Array(2048);
    for (let i = 0; i < 2048; i++) identity[i] = i / 2047;
    const identityRGB = new Float32Array(2048 * 3);
    for (let i = 0; i < 2048; i++) {
      const v = i / 2047;
      identityRGB[i * 3] = v; identityRGB[i * 3 + 1] = v; identityRGB[i * 3 + 2] = v;
    }
    this.uploadCurveLUT(identityRGB);
    this.profileLutTex = this.makeLutTexture(identity);

    const info = gl.getExtension("WEBGL_debug_renderer_info");
    if (info) console.log("[pipeline] GPU:", gl.getParameter(info.UNMASKED_RENDERER_WEBGL));
  }

  uploadImage(pixels: Float32Array, width: number, height: number): void {
    const gl = this.gl;
    this.texWidth = width; this.texHeight = height;
    if (this.sourceTex) gl.deleteTexture(this.sourceTex);
    const tex = gl.createTexture()!;
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB32F, width, height, 0, gl.RGB, gl.FLOAT, pixels);
    // LINEAR for smooth straighten/crop resampling (NEAREST is identical at 1:1).
    const filter = gl.getExtension("OES_texture_float_linear") ? gl.LINEAR : gl.NEAREST;
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, filter);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, filter);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    this.sourceTex = tex;
    // Default to a 1:1, un-cropped output; callers override via setOutput().
    this.outWidth = width; this.outHeight = height;
    this.texXform = new Float32Array([1, 0, 0, 0, -1, 0, 0, 1, 1]);
    this.canvas.width = width; this.canvas.height = height;
    this.buildLumaMask();
  }

  /**
   * Build the blurred log2-luminance mask for the uploaded image: downsample
   * to ≤MASK_LONG long edge, then a separable Gaussian, into an R16F texture
   * in source UV space. The single bilinear downsample tap aliases on large
   * sources; the σ=8 px blur washes it out, and the mask is built once per
   * upload so there is no temporal shimmer.
   */
  private buildLumaMask(): void {
    const gl = this.gl;
    if (this.maskTex) { gl.deleteTexture(this.maskTex); this.maskTex = null; }
    if (!this.maskSupported || !this.sourceTex) return;
    if (!this.ensureMaskPrograms()) return;
    const long = Math.max(this.texWidth, this.texHeight) || 1;
    const scale = Math.min(1, MASK_LONG / long);
    const w = Math.max(1, Math.round(this.texWidth * scale));
    const h = Math.max(1, Math.round(this.texHeight * scale));

    const makeTarget = (): { tex: WebGLTexture; fbo: WebGLFramebuffer } => {
      const t = gl.createTexture()!;
      gl.bindTexture(gl.TEXTURE_2D, t);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.R16F, w, h, 0, gl.RED, gl.FLOAT, null);
      // R16F is texture-filterable in core WebGL2 (unlike R32F).
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      const f = gl.createFramebuffer()!;
      gl.bindFramebuffer(gl.FRAMEBUFFER, f);
      gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, t, 0);
      return { tex: t, fbo: f };
    };
    const a = makeTarget();
    const complete = gl.checkFramebufferStatus(gl.FRAMEBUFFER) === gl.FRAMEBUFFER_COMPLETE;
    if (!complete) {
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      gl.deleteTexture(a.tex); gl.deleteFramebuffer(a.fbo);
      this.maskSupported = false; // permanently: u_hasMask=0 -> per-pixel fallback
      return;
    }
    const b = makeTarget();

    gl.viewport(0, 0, w, h);
    gl.bindVertexArray(this.maskVao);
    gl.activeTexture(gl.TEXTURE0);
    const run = (prog: WebGLProgram, src: WebGLTexture, dst: WebGLFramebuffer, dir?: [number, number]) => {
      gl.bindFramebuffer(gl.FRAMEBUFFER, dst);
      gl.useProgram(prog);
      gl.bindTexture(gl.TEXTURE_2D, src);
      gl.uniform1i(gl.getUniformLocation(prog, "u_input"), 0);
      if (dir) gl.uniform2f(this.maskBlurDir, dir[0], dir[1]);
      gl.drawArrays(gl.TRIANGLES, 0, 6);
    };
    run(this.maskProgDown!, this.sourceTex, a.fbo);              // log2 luma
    run(this.maskProgBlur!, a.tex, b.fbo, [1 / w, 0]);           // blur X
    run(this.maskProgBlur!, b.tex, a.fbo, [0, 1 / h]);           // blur Y
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);

    // Only the finished texture outlives the build.
    gl.deleteFramebuffer(a.fbo);
    gl.deleteFramebuffer(b.fbo);
    gl.deleteTexture(b.tex);
    this.maskTex = a.tex;
  }

  /** Lazily compile the mask downsample/blur programs and their quad VAO. */
  private ensureMaskPrograms(): boolean {
    const gl = this.gl;
    if (this.maskProgDown) return true;
    try {
      this.maskProgDown = this.compileProgramVS(MASK_VERTEX_SHADER, MASK_DOWNSAMPLE_SHADER);
      this.maskProgBlur = this.compileProgramVS(MASK_VERTEX_SHADER, MASK_BLUR_SHADER);
    } catch (err) {
      console.warn("[pipeline] mask programs unavailable:", err);
      this.maskSupported = false;
      return false;
    }
    this.maskBlurDir = gl.getUniformLocation(this.maskProgBlur, "u_dir");
    this.maskVao = this.makeQuadVao(0); // MASK_VERTEX_SHADER: layout(location = 0)
    return true;
  }

  /**
   * Set the rendered output window: canvas dims, the affine output→source-texcoord
   * map (built in crop.ts), and the workspace fill colour for out-of-image areas.
   * Pass identity dims/transform to render the full frame 1:1.
   */
  setOutput(width: number, height: number, texXform: Float32Array, bg?: [number, number, number]): void {
    this.outWidth = Math.max(1, Math.round(width));
    this.outHeight = Math.max(1, Math.round(height));
    this.texXform = texXform;
    if (bg) this.bgColor = bg;
    this.canvas.width = this.outWidth;
    this.canvas.height = this.outHeight;
  }

  /**
   * Set the preview render scale (0–1): the fraction of the logical output
   * resolution the canvas drawing buffer is rendered at. The caller sizes this to
   * the preview's on-screen device-pixel footprint so the GPU never shades more
   * fragments than are displayed. Applied on the next draw(). Does not affect the
   * histogram (binned from a separate source downscale) or export (a 1.0 renderer).
   */
  setPreviewScale(scale: number): void {
    this.previewScale = Math.min(1, Math.max(0.05, scale));
  }

  private makeLutTexture(lut: Float32Array, channels: 1 | 3 = 1): WebGLTexture {
    const gl = this.gl;
    const tex = gl.createTexture()!;
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 4);
    if (channels === 3) {
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB32F, 2048, 1, 0, gl.RGB, gl.FLOAT, lut);
    } else {
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.R32F, 2048, 1, 0, gl.RED, gl.FLOAT, lut);
    }
    // Prefer LINEAR for smooth interpolation; fall back to NEAREST if float-linear not available
    const filter = gl.getExtension("OES_texture_float_linear") ? gl.LINEAR : gl.NEAREST;
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, filter);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, filter);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    return tex;
  }

  /** Upload an interleaved RGB tone-curve LUT (length 2048*3, per-channel). */
  uploadCurveLUT(lut: Float32Array): void {
    if (this.curveLutTex) this.gl.deleteTexture(this.curveLutTex);
    this.curveLutTex = this.makeLutTexture(lut, 3);
  }

  /**
   * Upload the DCP profile tone curve as a per-channel LUT applied in the
   * Lightroom-style view transform (the camera's display rendering). Pass null
   * to disable it (e.g. no DCP / monochrome profile) and fall back to identity.
   */
  uploadProfileCurveLUT(lut: Float32Array | null): void {
    const gl = this.gl;
    if (this.profileLutTex) gl.deleteTexture(this.profileLutTex);
    const identity = new Float32Array(2048);
    for (let i = 0; i < 2048; i++) identity[i] = i / 2047;
    this.profileLutTex = this.makeLutTexture(lut ?? identity);
    this.hasProfileCurve = lut != null;
  }

  draw(params: Partial<EditParams> = {}): void {
    if (!this.sourceTex) return;
    const p = { ...DEFAULT_PARAMS, ...params };
    if (!this.p3Supported) p.displayGamut = 0; // fall back to sRGB
    this.lastParams = p;
    // Declare what gamut the drawing buffer holds so the browser colour-manages it.
    const gl = this.gl as WebGL2RenderingContext & { drawingBufferColorSpace?: string };
    if (this.p3Supported) gl.drawingBufferColorSpace = p.displayGamut === 1 ? "display-p3" : "srgb";
    // Render the drawing buffer at preview resolution (≤ logical output). The image
    // is shown fit-to-viewport, so shading the full decoded frame every time a
    // slider moves wastes fragments that are never seen. Histogram/export stay
    // full-res (they don't read the canvas / run at scale 1). Only assign
    // canvas.width when it changes — the assignment reallocates and clears it.
    const cw = Math.max(1, Math.round(this.outWidth * this.previewScale));
    const ch = Math.max(1, Math.round(this.outHeight * this.previewScale));
    if (this.canvas.width !== cw) this.canvas.width = cw;
    if (this.canvas.height !== ch) this.canvas.height = ch;
    this.renderPass(null, cw, ch, p);
  }

  /** Render the current source + params into `fbo` (null = canvas) at w×h. */
  private renderPass(fbo: WebGLFramebuffer | null, w: number, h: number, p: EditParams): void {
    const gl = this.gl;
    if (!this.sourceTex) return;
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
    gl.viewport(0, 0, w, h);
    gl.useProgram(this.program);
    // Crop / straighten transform + out-of-image fill.
    const xfLoc = this.uniforms["u_texXform"];
    if (xfLoc) gl.uniformMatrix3fv(xfLoc, false, this.texXform);
    const bgLoc = this.uniforms["u_bgColor"];
    if (bgLoc) gl.uniform3f(bgLoc, this.bgColor[0], this.bgColor[1], this.bgColor[2]);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, this.sourceTex);
    gl.uniform1i(this.uniforms["u_input"], 0);
    // Bind curve LUT to texture unit 1, profile-curve LUT to unit 2
    if (this.curveLutTex) {
      gl.activeTexture(gl.TEXTURE1);
      gl.bindTexture(gl.TEXTURE_2D, this.curveLutTex);
      gl.uniform1i(this.uniforms["u_curve_lut"], 1);
    }
    if (this.profileLutTex) {
      gl.activeTexture(gl.TEXTURE2);
      gl.bindTexture(gl.TEXTURE_2D, this.profileLutTex);
      gl.uniform1i(this.uniforms["u_profile_lut"], 2);
    }
    if (this.maskTex) {
      gl.activeTexture(gl.TEXTURE3);
      gl.bindTexture(gl.TEXTURE_2D, this.maskTex);
      gl.uniform1i(this.uniforms["u_mask_lum"], 3);
    }
    gl.activeTexture(gl.TEXTURE0);
    this.setUniforms(p);
    gl.bindVertexArray(this.vao);
    gl.drawArrays(gl.TRIANGLES, 0, 6);
  }

  /**
   * Bin a 256-entry histogram (R, G, B, luma) from the processed, display-encoded
   * output. Whatever the shader does, the histogram reflects it exactly — there
   * is no JS mirror of the pipeline.
   *
   * The GPU path scatters every pixel of a 1024px-capped render into a float
   * accumulation target, so the only read-back is 256×4 counts. It both removes
   * the large per-frame read-back stall and fixes the accuracy of the CPU path:
   * that path point-samples a 256px copy, and any bilinear filtering there would
   * average neighbours, narrowing the distribution and hiding clipping.
   */
  readHistogram(): HistogramBins {
    return this.histoGpuSupported ? this.readHistogramGPU() : this.readHistogramCPU();
  }

  /** CPU fallback: render a small copy, read it back, and bin it in JS. */
  private readHistogramCPU(): HistogramBins {
    const gl = this.gl;
    const bins: HistogramBins = {
      r: new Uint32Array(256), g: new Uint32Array(256),
      b: new Uint32Array(256), l: new Uint32Array(256),
    };
    if (!this.sourceTex || !this.outWidth || !this.outHeight) return bins;

    const { w, h } = this.ensureHistoFbo(HISTO_LONG_CPU);
    // Point-sample the source (NEAREST) so the downscale doesn't average
    // neighbours — averaging narrows the distribution and hides clipping.
    gl.bindTexture(gl.TEXTURE_2D, this.sourceTex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    this.renderPass(this.histoFbo, w, h, this.lastParams);
    const srcFilter = gl.getExtension("OES_texture_float_linear") ? gl.LINEAR : gl.NEAREST;
    gl.bindTexture(gl.TEXTURE_2D, this.sourceTex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, srcFilter);
    const n = w * h;
    const buf = new Uint8Array(n * 4);
    gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, buf);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);

    for (let i = 0; i < n; i++) {
      const r = buf[i * 4], g = buf[i * 4 + 1], b = buf[i * 4 + 2];
      bins.r[r]++; bins.g[g]++; bins.b[b]++;
      // Rec.709 luma of the display-encoded values, matching Lightroom's scope.
      bins.l[Math.min(255, (0.2126 * r + 0.7152 * g + 0.0722 * b) | 0)]++;
    }
    return bins;
  }

  /** GPU path: scatter every pixel into a 256×4 float bin texture. */
  private readHistogramGPU(): HistogramBins {
    const gl = this.gl;
    const bins: HistogramBins = {
      r: new Uint32Array(256), g: new Uint32Array(256),
      b: new Uint32Array(256), l: new Uint32Array(256),
    };
    if (!this.sourceTex || !this.outWidth || !this.outHeight) return bins;

    // 1. Render the processed, display-encoded image into an RGBA8 FBO. Point-
    //    sample the source (NEAREST minification) so the downscale doesn't
    //    average neighbours — averaging pulls extremes toward the mean, which
    //    narrows the distribution and under-reports clipping.
    const { w, h } = this.ensureHistoFbo(HISTO_LONG_GPU);
    gl.bindTexture(gl.TEXTURE_2D, this.sourceTex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    this.renderPass(this.histoFbo, w, h, this.lastParams);
    const srcFilter = gl.getExtension("OES_texture_float_linear") ? gl.LINEAR : gl.NEAREST;
    gl.bindTexture(gl.TEXTURE_2D, this.sourceTex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, srcFilter);

    // 2. Scatter every pixel into 256 bins × 4 rows (R,G,B,L) via additive float
    //    blending — a full-image histogram computed entirely on the GPU.
    if (!this.ensureHistoBin()) return this.readHistogramCPU(); // float FBO incomplete
    gl.bindFramebuffer(gl.FRAMEBUFFER, this.histoBinFbo);
    gl.viewport(0, 0, 256, 4);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.useProgram(this.histoProgram!);
    gl.bindVertexArray(this.histoVao!);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, this.histoTex);
    gl.uniform1i(this.histoUniforms!.u_src, 0);
    gl.uniform1i(this.histoUniforms!.u_srcW, w);
    gl.enable(gl.BLEND);
    gl.blendEquation(gl.FUNC_ADD);
    gl.blendFunc(gl.ONE, gl.ONE);
    gl.drawArrays(gl.POINTS, 0, w * h * 4);
    gl.disable(gl.BLEND);

    // 3. Read back the 256×4 counts (16 KB) and unpack the red channel.
    const buf = new Float32Array(256 * 4 * 4);
    gl.readPixels(0, 0, 256, 4, gl.RGBA, gl.FLOAT, buf);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    const rows = [bins.r, bins.g, bins.b, bins.l];
    for (let ch = 0; ch < 4; ch++) {
      const row = rows[ch];
      const base = ch * 256 * 4;
      for (let bin = 0; bin < 256; bin++) row[bin] = buf[base + bin * 4] >>> 0;
    }
    return bins;
  }

  /** (Re)create the RGBA8 FBO the histogram is binned from, capped to `longCap`. */
  private ensureHistoFbo(longCap: number): { w: number; h: number } {
    const gl = this.gl;
    const long = Math.max(this.outWidth, this.outHeight) || 1;
    const scale = Math.min(1, longCap / long);
    const w = Math.max(1, Math.round(this.outWidth * scale));
    const h = Math.max(1, Math.round(this.outHeight * scale));
    if (this.histoFbo && this.histoW === w && this.histoH === h) return { w, h };
    if (this.histoTex) gl.deleteTexture(this.histoTex);
    if (this.histoFbo) gl.deleteFramebuffer(this.histoFbo);
    const tex = gl.createTexture()!;
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    const fbo = gl.createFramebuffer()!;
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    this.histoTex = tex;
    this.histoFbo = fbo;
    this.histoW = w;
    this.histoH = h;
    return { w, h };
  }

  /**
   * Lazily build the 256×4 RGBA32F accumulation target and the scatter program.
   * Returns false (and permanently disables the GPU path) if the float FBO is
   * not framebuffer-complete on this device.
   */
  private ensureHistoBin(): boolean {
    const gl = this.gl;
    if (this.histoBinFbo) return true;
    const tex = gl.createTexture()!;
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32F, 256, 4, 0, gl.RGBA, gl.FLOAT, null);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    const fbo = gl.createFramebuffer()!;
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
    const ok = gl.checkFramebufferStatus(gl.FRAMEBUFFER) === gl.FRAMEBUFFER_COMPLETE;
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    if (!ok) {
      gl.deleteTexture(tex);
      gl.deleteFramebuffer(fbo);
      this.histoGpuSupported = false;
      return false;
    }
    this.histoBinTex = tex;
    this.histoBinFbo = fbo;

    // One point per (pixel, channel). gl_VertexID derives both, the vertex shader
    // fetches the pixel and positions the point at its bin column / channel row.
    const vs = `#version 300 es
precision highp float;
uniform sampler2D u_src;
uniform int u_srcW;
void main() {
  int vid = gl_VertexID;
  int pix = vid >> 2;          // pixel index
  int ch  = vid & 3;           // 0=R 1=G 2=B 3=Luma
  ivec2 coord = ivec2(pix % u_srcW, pix / u_srcW);
  vec3 rgb = texelFetch(u_src, coord, 0).rgb;
  float v = (ch == 0) ? rgb.r
          : (ch == 1) ? rgb.g
          : (ch == 2) ? rgb.b
          : dot(rgb, vec3(0.2126, 0.7152, 0.0722)); // Rec.709 luma, display-encoded
  int bin = clamp(int(v * 255.0 + 0.5), 0, 255);
  gl_Position = vec4((float(bin) + 0.5) / 128.0 - 1.0,
                     (float(ch) + 0.5) / 2.0 - 1.0, 0.0, 1.0);
  gl_PointSize = 1.0;
}`;
    const fs = `#version 300 es
precision highp float;
out vec4 o;
void main() { o = vec4(1.0, 0.0, 0.0, 0.0); } // each point adds 1 to its bin`;
    this.histoProgram = this.compileProgramVS(vs, fs);
    this.histoUniforms = {
      u_src: gl.getUniformLocation(this.histoProgram, "u_src"),
      u_srcW: gl.getUniformLocation(this.histoProgram, "u_srcW"),
    };
    this.histoVao = gl.createVertexArray(); // attribute-less: positions come from gl_VertexID
    return true;
  }

  /**
   * Read the currently-drawn frame back into an encoded image Blob.
   * Call right after draw(): reads the WebGL back buffer (origin bottom-left,
   * so rows are flipped to top-down), composites onto a 2D canvas and encodes.
   */
  async toBlob(type = "image/jpeg", quality = 0.92): Promise<Blob> {
    const gl = this.gl;
    // Read the actual drawing-buffer size (= outWidth/outHeight for a 1.0-scale
    // export renderer; smaller for a downscaled preview).
    const w = this.canvas.width;
    const h = this.canvas.height;
    if (!w || !h) throw new Error("nothing to read back");
    // The back buffer holds values in the gamut chosen for the last draw(); tag the
    // read-back canvas with the same colour space so the export matches the preview.
    const colorSpace: PredefinedColorSpace =
      this.lastParams.displayGamut === 1 && this.p3Supported ? "display-p3" : "srgb";
    const raw = new Uint8Array(w * h * 4);
    gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, raw);
    // Flip vertically (GL framebuffer origin is bottom-left)
    const flipped = new Uint8ClampedArray(w * h * 4);
    const rowBytes = w * 4;
    for (let y = 0; y < h; y++) {
      const src = (h - 1 - y) * rowBytes;
      flipped.set(raw.subarray(src, src + rowBytes), y * rowBytes);
    }
    const cvs = document.createElement("canvas");
    cvs.width = w;
    cvs.height = h;
    const ctx = cvs.getContext("2d", { colorSpace });
    if (!ctx) throw new Error("2D context unavailable");
    ctx.putImageData(new ImageData(flipped, w, h, { colorSpace }), 0, 0);
    return await new Promise<Blob>((res, rej) =>
      cvs.toBlob((b) => (b ? res(b) : rej(new Error("toBlob failed"))), type, quality));
  }

  destroy(): void {
    const gl = this.gl;
    if (this.sourceTex) gl.deleteTexture(this.sourceTex);
    if (this.curveLutTex) gl.deleteTexture(this.curveLutTex);
    if (this.profileLutTex) gl.deleteTexture(this.profileLutTex);
    if (this.histoTex) gl.deleteTexture(this.histoTex);
    if (this.histoFbo) gl.deleteFramebuffer(this.histoFbo);
    if (this.histoBinTex) gl.deleteTexture(this.histoBinTex);
    if (this.histoBinFbo) gl.deleteFramebuffer(this.histoBinFbo);
    if (this.histoProgram) gl.deleteProgram(this.histoProgram);
    if (this.histoVao) gl.deleteVertexArray(this.histoVao);
    if (this.maskTex) gl.deleteTexture(this.maskTex);
    if (this.maskProgDown) gl.deleteProgram(this.maskProgDown);
    if (this.maskProgBlur) gl.deleteProgram(this.maskProgBlur);
    if (this.maskVao) gl.deleteVertexArray(this.maskVao);
    gl.deleteProgram(this.program);
    gl.deleteVertexArray(this.vao);
  }

  private setUniforms(p: EditParams): void {
    const gl = this.gl;
    const s = (n: string, v: number) => { const l = this.uniforms[n]; if (l) gl.uniform1f(l, v); };
    const i = (n: string, v: number) => { const l = this.uniforms[n]; if (l) gl.uniform1i(l, v); };
    // White balance: relative temp/tint -> linear-ProPhoto gain (unit at 6500/0).
    const wb = computeWbGain(p.temperature, p.tint);
    const wbLoc = this.uniforms["u_wbGain"];
    if (wbLoc) gl.uniform3f(wbLoc, wb[0], wb[1], wb[2]);
    i("u_viewTransform", p.viewTransform);
    i("u_displayGamut", p.displayGamut);
    i("u_hasProfileCurve", this.hasProfileCurve ? 1 : 0);
    s("u_exposure", p.exposure); s("u_highlights", p.highlights);
    s("u_shadows", p.shadows); s("u_whites", p.whites);
    s("u_vibrance", p.vibrance); s("u_saturation", p.saturation);
    s("u_clarity", p.clarity / 100);
    s("u_dehaze", p.dehaze / 100);
    // Activity flags let the shader skip its two costliest blocks (luma region/
    // contrast and the Oklab HSL mixer) when they are at their identity defaults.
    // Positive Whites is display-referred (baked into the curve LUT), so only
    // its negative half engages the shader's tonal block.
    const tonalActive = p.highlights !== 0 || p.shadows !== 0 || p.whites < 0;
    const hslActive = (p.hslH?.some((v) => v !== 0) ?? false)
      || (p.hslS?.some((v) => v !== 0) ?? false)
      || (p.hslL?.some((v) => v !== 0) ?? false);
    i("u_tonalActive", tonalActive ? 1 : 0);
    i("u_hslActive", hslActive ? 1 : 0);
    // The blurred log-luma mask is static per image; WB and exposure reach it
    // as an additive log2 shift (scalar WB luma gain + the linear part of
    // exposure — the shoulder is ignored, which only makes the mask read
    // slightly bright inside compressed highlights, softening a soft weight).
    i("u_hasMask", this.maskTex ? 1 : 0);
    const wbLuma = PROPHOTO_Y[0] * wb[0] + PROPHOTO_Y[1] * wb[1] + PROPHOTO_Y[2] * wb[2];
    s("u_maskShift", Math.log2(Math.max(wbLuma, 1e-6)) + p.exposure - Math.log2(0.18));
    // HSL
    for (let band = 0; band < 8; band++) {
      s(`u_hsl_h[${band}]`, p.hslH?.[band] ?? 0);
      s(`u_hsl_s[${band}]`, p.hslS?.[band] ?? 0);
      s(`u_hsl_l[${band}]`, p.hslL?.[band] ?? 0);
    }
    // Color Grading
    s("u_grad_sh_h", p.gradShH ?? 0); s("u_grad_sh_s", p.gradShS ?? 0);
    s("u_grad_md_h", p.gradMdH ?? 0); s("u_grad_md_s", p.gradMdS ?? 0);
    s("u_grad_hl_h", p.gradHlH ?? 0); s("u_grad_hl_s", p.gradHlS ?? 0);
    s("u_grad_blend", p.gradBlend ?? 0); s("u_grad_balance", p.gradBalance ?? 0);
  }

  private compileProgram(fsSource: string): WebGLProgram {
    return this.compileProgramVS(VERTEX_SHADER, fsSource);
  }

  private compileProgramVS(vsSource: string, fsSource: string): WebGLProgram {
    const gl = this.gl;
    const prog = gl.createProgram()!;
    const vs = this.compile(gl.VERTEX_SHADER, vsSource);
    const fs = this.compile(gl.FRAGMENT_SHADER, fsSource);
    gl.attachShader(prog, vs); gl.attachShader(prog, fs);
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS))
      throw new Error(`Program link: ${gl.getProgramInfoLog(prog)}`);
    gl.deleteShader(vs); gl.deleteShader(fs);
    return prog;
  }

  private compile(type: number, src: string): WebGLShader {
    const gl = this.gl;
    const s = gl.createShader(type)!;
    gl.shaderSource(s, src); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS))
      throw new Error(`Shader ${type === gl.VERTEX_SHADER ? "VS" : "FS"}: ${gl.getShaderInfoLog(s)}`);
    return s;
  }

  private createFullScreenQuad(): WebGLVertexArrayObject {
    return this.makeQuadVao(this.gl.getAttribLocation(this.program, "a_position"));
  }

  private makeQuadVao(attribLoc: number): WebGLVertexArrayObject {
    const gl = this.gl;
    const vao = gl.createVertexArray()!;
    gl.bindVertexArray(vao);
    const buf = gl.createBuffer()!;
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]), gl.STATIC_DRAW);
    gl.enableVertexAttribArray(attribLoc);
    gl.vertexAttribPointer(attribLoc, 2, gl.FLOAT, false, 0, 0);
    return vao;
  }
}
