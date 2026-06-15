/**
 * Single-pass WebGL2 pipeline renderer.
 *
 * All per-pixel operations are fused into one shader.
 * Renders directly to canvas — no FBO ping-pong needed.
 */

import { PASSES, VERTEX_SHADER } from "./passes";
import { computeWbGain } from "./color-spaces";
import type { HistogramBins } from "./histogram";

export interface EditParams {
  exposure: number; contrast: number; saturation: number;
  temperature: number; tint: number;
  highlights: number; shadows: number; whites: number; blacks: number;
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

export const DEFAULT_PARAMS: EditParams = {
  exposure: 0, contrast: 1, saturation: 1,
  temperature: 6500, tint: 0,
  highlights: 0, shadows: 0, whites: 0, blacks: 0,
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
  private lastParams: EditParams = DEFAULT_PARAMS;
  // Small offscreen target for histogram read-back (display-encoded pixels).
  private histoFbo: WebGLFramebuffer | null = null;
  private histoTex: WebGLTexture | null = null;
  private histoW = 0;
  private histoH = 0;
  /** Whether the browser exposes a wide-gamut drawing buffer. */
  readonly p3Supported: boolean = false;

  constructor(private canvas: HTMLCanvasElement) {
    const gl = canvas.getContext("webgl2", {
      premultipliedAlpha: false, alpha: false, antialias: false,
    }) as WebGL2RenderingContext | null;
    if (!gl) throw new Error("WebGL2 not available");
    this.gl = gl;
    this.p3Supported = "drawingBufferColorSpace" in gl;

    const pass = PASSES[0];
    this.program = this.compileProgram(pass.fsSource);
    for (const name of pass.uniforms) this.uniforms[name] = gl.getUniformLocation(this.program, name);
    this.uniforms["u_input"] = gl.getUniformLocation(this.program, "u_input");
    this.uniforms["u_curve_lut"] = gl.getUniformLocation(this.program, "u_curve_lut");
    this.uniforms["u_profile_lut"] = gl.getUniformLocation(this.program, "u_profile_lut");
    this.vao = this.createFullScreenQuad();

    // Upload identity LUTs as defaults (user tone curve + DCP profile curve).
    const identity = new Float32Array(2048);
    for (let i = 0; i < 2048; i++) identity[i] = i / 2047;
    this.uploadCurveLUT(identity);
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
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    this.sourceTex = tex;
    this.canvas.width = width; this.canvas.height = height;
  }

  private makeLutTexture(lut: Float32Array): WebGLTexture {
    const gl = this.gl;
    const tex = gl.createTexture()!;
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 4);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.R32F, 2048, 1, 0, gl.RED, gl.FLOAT, lut);
    // Prefer LINEAR for smooth interpolation; fall back to NEAREST if float-linear not available
    const filter = gl.getExtension("OES_texture_float_linear") ? gl.LINEAR : gl.NEAREST;
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, filter);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, filter);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    return tex;
  }

  /** Upload a 2048-entry Float32Array as the user tone-curve LUT (luminance-driven). */
  uploadCurveLUT(lut: Float32Array): void {
    if (this.curveLutTex) this.gl.deleteTexture(this.curveLutTex);
    this.curveLutTex = this.makeLutTexture(lut);
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
    this.renderPass(null, this.texWidth, this.texHeight, p);
  }

  /** Render the current source + params into `fbo` (null = canvas) at w×h. */
  private renderPass(fbo: WebGLFramebuffer | null, w: number, h: number, p: EditParams): void {
    const gl = this.gl;
    if (!this.sourceTex) return;
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
    gl.viewport(0, 0, w, h);
    gl.useProgram(this.program);
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
    gl.activeTexture(gl.TEXTURE0);
    this.setUniforms(p);
    gl.bindVertexArray(this.vao);
    gl.drawArrays(gl.TRIANGLES, 0, 6);
  }

  /**
   * Render a downscaled copy with the last-drawn params into an offscreen FBO,
   * read it back, and bin a 256-entry histogram from the display-encoded pixels.
   * This replaces the brittle JS mirror of the shader pipeline: whatever the
   * shader does, the histogram reflects it exactly.
   */
  readHistogram(): HistogramBins {
    const gl = this.gl;
    const bins: HistogramBins = {
      r: new Uint32Array(256), g: new Uint32Array(256),
      b: new Uint32Array(256), l: new Uint32Array(256),
    };
    if (!this.sourceTex || !this.texWidth || !this.texHeight) return bins;

    this.ensureHistoFbo();
    this.renderPass(this.histoFbo, this.histoW, this.histoH, this.lastParams);
    const n = this.histoW * this.histoH;
    const buf = new Uint8Array(n * 4);
    gl.readPixels(0, 0, this.histoW, this.histoH, gl.RGBA, gl.UNSIGNED_BYTE, buf);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);

    for (let i = 0; i < n; i++) {
      const r = buf[i * 4], g = buf[i * 4 + 1], b = buf[i * 4 + 2];
      bins.r[r]++; bins.g[g]++; bins.b[b]++;
      // Rec.709 luma of the display-encoded values, matching Lightroom's scope.
      bins.l[Math.min(255, (0.2126 * r + 0.7152 * g + 0.0722 * b) | 0)]++;
    }
    return bins;
  }

  private ensureHistoFbo(): void {
    const gl = this.gl;
    const long = Math.max(this.texWidth, this.texHeight) || 1;
    const scale = Math.min(1, 256 / long);
    const w = Math.max(1, Math.round(this.texWidth * scale));
    const h = Math.max(1, Math.round(this.texHeight * scale));
    if (this.histoFbo && this.histoW === w && this.histoH === h) return;
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
  }

  /**
   * Read the currently-drawn frame back into an encoded image Blob.
   * Call right after draw(): reads the WebGL back buffer (origin bottom-left,
   * so rows are flipped to top-down), composites onto a 2D canvas and encodes.
   */
  async toBlob(type = "image/jpeg", quality = 0.92): Promise<Blob> {
    const gl = this.gl;
    const w = this.texWidth;
    const h = this.texHeight;
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
    s("u_shadows", p.shadows); s("u_whites", p.whites); s("u_blacks", p.blacks);
    s("u_contrast", p.contrast); s("u_vibrance", p.vibrance); s("u_saturation", p.saturation);
    s("u_clarity", p.clarity / 100);
    s("u_dehaze", p.dehaze / 100);
    // HSL
    for (let i = 0; i < 8; i++) {
      s(`u_hsl_h[${i}]`, p.hslH?.[i] ?? 0);
      s(`u_hsl_s[${i}]`, p.hslS?.[i] ?? 0);
      s(`u_hsl_l[${i}]`, p.hslL?.[i] ?? 0);
    }
    // Color Grading
    s("u_grad_sh_h", p.gradShH ?? 0); s("u_grad_sh_s", p.gradShS ?? 0);
    s("u_grad_md_h", p.gradMdH ?? 0); s("u_grad_md_s", p.gradMdS ?? 0);
    s("u_grad_hl_h", p.gradHlH ?? 0); s("u_grad_hl_s", p.gradHlS ?? 0);
    s("u_grad_blend", p.gradBlend ?? 0); s("u_grad_balance", p.gradBalance ?? 0);
  }

  private compileProgram(fsSource: string): WebGLProgram {
    const gl = this.gl;
    const prog = gl.createProgram()!;
    const vs = this.compile(gl.VERTEX_SHADER, VERTEX_SHADER);
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
    const gl = this.gl;
    const vao = gl.createVertexArray()!;
    gl.bindVertexArray(vao);
    const buf = gl.createBuffer()!;
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]), gl.STATIC_DRAW);
    const a = gl.getAttribLocation(this.program, "a_position");
    gl.enableVertexAttribArray(a);
    gl.vertexAttribPointer(a, 2, gl.FLOAT, false, 0, 0);
    return vao;
  }
}
