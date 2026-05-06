/**
 * Multi-pass WebGL2 pipeline renderer.
 *
 * Passes run sequentially via ping-pong FBOs:
 *   src → [FBO A] → Pass 1 → [FBO B] → Pass 2 → [FBO A] → … → canvas
 */

import { PASSES, VERTEX_SHADER } from "./passes";

export interface EditParams {
  exposure: number; contrast: number; saturation: number;
  temperature: number; tint: number;
  highlights: number; shadows: number; whites: number; blacks: number;
  vibrance: number;
}

export const DEFAULT_PARAMS: EditParams = {
  exposure: 0, contrast: 1, saturation: 1,
  temperature: 6500, tint: 0,
  highlights: 0, shadows: 0, whites: 0, blacks: 0,
  vibrance: 1,
};

export interface PassMask {
  wb: boolean; exposure: boolean; tone: boolean;
  contrast: boolean; vibsat: boolean; gamma: boolean;
}

interface FboPair { tex: WebGLTexture; fbo: WebGLFramebuffer; }
interface CompiledPass { program: WebGLProgram; uniforms: Record<string, WebGLUniformLocation | null>; }

export class PipelineRenderer {
  private gl: WebGL2RenderingContext;
  private vao: WebGLVertexArrayObject;
  private sourceTex: WebGLTexture | null = null;
  private fboA: FboPair | null = null;
  private fboB: FboPair | null = null;
  private compiledPasses: CompiledPass[] = [];
  private fboFormat: number;
  private texWidth = 0;
  private texHeight = 0;

  constructor(private canvas: HTMLCanvasElement) {
    const gl = canvas.getContext("webgl2", {
      premultipliedAlpha: false, alpha: false, antialias: false, colorSpace: "srgb",
    }) as WebGL2RenderingContext | null;
    if (!gl) throw new Error("WebGL2 not available");
    this.gl = gl;
    for (const pass of PASSES) this.compiledPasses.push(this.compilePass(pass));
    this.vao = this.createFullScreenQuad();
    this.fboFormat = this.detectFboFormat();
    gl.clearColor(0.07, 0.07, 0.07, 1);
    const info = gl.getExtension("WEBGL_debug_renderer_info");
    if (info) console.log("[pipeline] GPU:", gl.getParameter(info.UNMASKED_RENDERER_WEBGL));
  }

  private detectFboFormat(): number {
    const gl = this.gl;
    if (this.testFormat(gl.RGBA16F)) return gl.RGBA16F;
    if (gl.getExtension("EXT_color_buffer_half_float") && this.testFormat(gl.RGBA16F)) return gl.RGBA16F;
    if (gl.getExtension("EXT_color_buffer_float") && this.testFormat(gl.RGBA32F)) return gl.RGBA32F;
    return gl.RGBA8;
  }

  private testFormat(fmt: number): boolean {
    const gl = this.gl;
    const t = gl.createTexture()!;
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.texImage2D(gl.TEXTURE_2D, 0, fmt, 4, 4, 0, gl.RGBA, gl.FLOAT, null);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    const f = gl.createFramebuffer()!;
    gl.bindFramebuffer(gl.FRAMEBUFFER, f);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, t, 0);
    const ok = gl.checkFramebufferStatus(gl.FRAMEBUFFER) === gl.FRAMEBUFFER_COMPLETE;
    gl.deleteFramebuffer(f); gl.deleteTexture(t);
    return ok;
  }

  uploadImage(pixels: Float32Array, width: number, height: number): void {
    const gl = this.gl;
    this.texWidth = width; this.texHeight = height;
    if (this.sourceTex) gl.deleteTexture(this.sourceTex);
    this.destroyFbos();
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

  draw(params: Partial<EditParams> = {}): void {
    const gl = this.gl;
    if (!this.sourceTex) return;
    const p = { ...DEFAULT_PARAMS, ...params };
    console.log("[pipeline] draw p:", JSON.stringify(p));
    this.ensureFbos();
    if (!this.fboA || !this.fboB) { console.warn("[pipeline] no FBOs, skip"); return; }

    const passes = PASSES.filter((_, i) => PASSES[i].name !== "gamma" || true);
    let readTex = this.sourceTex;
    for (let i = 0; i < passes.length; i++) {
      const isLast = i === passes.length - 1;
      if (isLast) { gl.bindFramebuffer(gl.FRAMEBUFFER, null); }
      else { gl.bindFramebuffer(gl.FRAMEBUFFER, (i % 2 === 0 ? this.fboA : this.fboB)!.fbo); }
      gl.viewport(0, 0, this.texWidth, this.texHeight);
      const c = this.compiledPasses[i];
      gl.useProgram(c.program);
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, readTex);
      gl.uniform1i(c.uniforms["u_input"], 0);
      this.setUniforms(c, p);
      gl.bindVertexArray(this.vao);
      gl.drawArrays(gl.TRIANGLES, 0, 6);
      if (!isLast) readTex = (i % 2 === 0 ? this.fboA : this.fboB)!.tex;
    }
  }

  destroy(): void {
    const gl = this.gl;
    if (this.sourceTex) gl.deleteTexture(this.sourceTex);
    this.destroyFbos();
    for (const p of this.compiledPasses) gl.deleteProgram(p.program);
    gl.deleteVertexArray(this.vao);
  }

  private ensureFbos(): void {
    if (this.fboA && this.fboB) return;
    try {
      if (!this.fboA) this.fboA = this.makeFbo();
      if (!this.fboB) this.fboB = this.makeFbo();
    } catch { this.destroyFbos(); }
  }

  private makeFbo(): FboPair {
    const gl = this.gl;
    const fmt = this.fboFormat;
    const isRgba = fmt !== gl.RGB32F;
    const type = fmt === gl.RGBA8 ? gl.UNSIGNED_BYTE : gl.FLOAT;
    const tex = gl.createTexture()!;
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
    gl.texImage2D(gl.TEXTURE_2D, 0, fmt, this.texWidth, this.texHeight, 0, isRgba ? gl.RGBA : gl.RGB, type, null);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    const fbo = gl.createFramebuffer()!;
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
    if (gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE)
      throw new Error("FBO incomplete");
    return { tex, fbo };
  }

  private destroyFbos(): void {
    const gl = this.gl;
    for (const f of [this.fboA, this.fboB]) {
      if (f) { gl.deleteTexture(f.tex); gl.deleteFramebuffer(f.fbo); }
    }
    this.fboA = null; this.fboB = null;
  }

  private setUniforms(c: CompiledPass, p: EditParams): void {
    const gl = this.gl;
    const s = (n: string, v: number) => { const l = c.uniforms[n]; if (l) gl.uniform1f(l, v); };
    s("u_temperature", p.temperature); s("u_tint", p.tint);
    s("u_exposure", p.exposure); s("u_highlights", p.highlights);
    s("u_shadows", p.shadows); s("u_whites", p.whites); s("u_blacks", p.blacks);
    s("u_contrast", p.contrast); s("u_vibrance", p.vibrance); s("u_saturation", p.saturation);
  }

  private compilePass(def: typeof PASSES[number]): CompiledPass {
    const gl = this.gl;
    const prog = gl.createProgram()!;
    const vs = this.compile(gl.VERTEX_SHADER, VERTEX_SHADER);
    const fs = this.compile(gl.FRAGMENT_SHADER, def.fsSource);
    gl.attachShader(prog, vs); gl.attachShader(prog, fs);
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(`[${def.name}] ${gl.getProgramInfoLog(prog)}`);
    gl.deleteShader(vs); gl.deleteShader(fs);
    const uniforms: Record<string, WebGLUniformLocation | null> = {};
    uniforms["u_input"] = gl.getUniformLocation(prog, "u_input");
    for (const n of def.uniforms) uniforms[n] = gl.getUniformLocation(prog, n);
    return { program: prog, uniforms };
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
    const p = this.compiledPasses[0]?.program;
    if (p) {
      const a = gl.getAttribLocation(p, "a_position");
      gl.enableVertexAttribArray(a); gl.vertexAttribPointer(a, 2, gl.FLOAT, false, 0, 0);
    }
    return vao;
  }
}
