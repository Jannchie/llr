/**
 * Single-pass WebGL2 pipeline renderer.
 *
 * All per-pixel operations are fused into one shader.
 * Renders directly to canvas — no FBO ping-pong needed.
 */

import { PASSES, VERTEX_SHADER } from "./passes";

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
};

export class PipelineRenderer {
  private gl: WebGL2RenderingContext;
  private program: WebGLProgram;
  private uniforms: Record<string, WebGLUniformLocation | null> = {};
  private vao: WebGLVertexArrayObject;
  private sourceTex: WebGLTexture | null = null;
  private texWidth = 0;
  private texHeight = 0;

  constructor(private canvas: HTMLCanvasElement) {
    const gl = canvas.getContext("webgl2", {
      premultipliedAlpha: false, alpha: false, antialias: false, colorSpace: "srgb",
    }) as WebGL2RenderingContext | null;
    if (!gl) throw new Error("WebGL2 not available");
    this.gl = gl;

    const pass = PASSES[0];
    this.program = this.compileProgram(pass.fsSource);
    for (const name of pass.uniforms) this.uniforms[name] = gl.getUniformLocation(this.program, name);
    this.uniforms["u_input"] = gl.getUniformLocation(this.program, "u_input");
    this.vao = this.createFullScreenQuad();

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

  draw(params: Partial<EditParams> = {}): void {
    const gl = this.gl;
    if (!this.sourceTex) return;
    const p = { ...DEFAULT_PARAMS, ...params };

    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.viewport(0, 0, this.texWidth, this.texHeight);
    gl.useProgram(this.program);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, this.sourceTex);
    gl.uniform1i(this.uniforms["u_input"], 0);
    this.setUniforms(p);
    gl.bindVertexArray(this.vao);
    gl.drawArrays(gl.TRIANGLES, 0, 6);
  }

  destroy(): void {
    const gl = this.gl;
    if (this.sourceTex) gl.deleteTexture(this.sourceTex);
    gl.deleteProgram(this.program);
    gl.deleteVertexArray(this.vao);
  }

  private setUniforms(p: EditParams): void {
    const gl = this.gl;
    const s = (n: string, v: number) => { const l = this.uniforms[n]; if (l) gl.uniform1f(l, v); };
    s("u_temperature", p.temperature); s("u_tint", p.tint);
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
