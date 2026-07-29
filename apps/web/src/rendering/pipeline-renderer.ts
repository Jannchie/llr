/**
 * Single-pass WebGL2 pipeline renderer.
 *
 * All per-pixel operations are fused into one shader.
 * Renders directly to canvas — no FBO ping-pong needed.
 */

import {
  SONY_POST_PROGRAMS, type SonyPostProgramName,
  MASK_BLUR_SHADER, MASK_DOWNSAMPLE_SHADER, MASK_VERTEX_SHADER, PASSES, VERTEX_SHADER,
} from "./passes";
import {
  SPICA_CODE_COUNT, SPICA_LUT, SPICA_TABLE_COUNT, SPICA_TAP_COUNT, SPICA_WEIGHTS,
} from "./spica-tables";
import { LENS_IDENTITY, LENS_KNOTS, lensFillScale } from "./lens";
import { computeWbMatrix } from "./color-spaces";
import { LUT_SIZE, buildToneCurveLUT, defaultToneCurve } from "./curve";
import { LOG2_MID } from "./tonal-model";
import type { HistogramBins } from "./histogram";

// Contrast and Blacks are not here: they are display-referred and baked into
// the tone-curve LUT (curve.ts BasicAdjust), not shader uniforms.
export interface EditParams {
  exposure: number; saturation: number;
  temperature: number; tint: number;
  highlights: number; shadows: number;
  vibrance: number; clarity: number; dehaze: number;
  // HSL (8 ranges, each [-1, 1])
  hslH: number[]; hslS: number[]; hslL: number[];
  // Color Grading: per-wheel linear-ProPhoto tint multipliers built by
  // grading.ts gradingTint() ([1,1,1] = identity); blend [0,1], balance [-1,1].
  gradShTint: [number, number, number];
  gradMdTint: [number, number, number];
  gradHlTint: [number, number, number];
  gradBlend: number; gradBalance: number;
  // Display gamut: 0 = sRGB, 1 = P3.
  displayGamut: number;
  // Lens corrections: canonical 16-knot factor tables with the slider amounts
  // already mixed in (all-1 = identity). Built in App.vue from
  // colorProfile.lensCorr via lens.ts. The fill scale is not passed: it depends
  // on the frame's aspect ratio (lens.ts lensFillScale), which only the
  // renderer knows, so it is derived here from lensDist and the texture dims.
  lensDist: number[]; lensVig: number[];
  // Whether to apply the fitted camera-match HueSatMap (1) or show Adobe's
  // uncorrected rendering (0). A parameter rather than a per-image upload
  // because it is a user toggle, and it no longer costs a decode to flip.
  cameraMatch: number;
}

const HSL_ZERO = [0, 0, 0, 0, 0, 0, 0, 0];

/**
 * Scene-linear RGB payload for uploadImage: float32, or IEEE half floats
 * packed in a Uint16Array (the worker's wire format — uploaded as RGB16F).
 */
export type LinearPixels = Float32Array | Uint16Array;

/**
 * A camera profile's tone curve, baked onto the shader's LUT grid. `srgbBasis`
 * says which primaries the per-channel curve is defined on — sRGB/Rec.709 for
 * Sony's MainGamma, the ProPhoto working space for a DCP's own curve. null when
 * the profile carries no curve.
 *
 * `chroma` rides along because Sony's RGB2YCC runs immediately after the curve,
 * on the curve's own output and in the curve's own basis — four cross terms and
 * four gains, plus YGamma's pivot and contrast, which the engine runs between
 * the two chroma halves and which carry the shot's Fade setting (worker
 * sony/chroma.py). Absent for profiles with no such stage, which is every DCP.
 */
export type ProfileChroma = {
  cross: number[];
  gain: number[];
  lumaPivot: number;
  lumaContrast: number;
  // `gain` arrives already divided by this; the shader multiplies it back after
  // the clamp, which is the shot's Saturation setting.
  saturation: number;
  // Sepia's toning stage, absent for the nine looks that do not tone.
  sepia?: SepiaToning | null;
};
/** Weighted sum of the encoded RGB, then one curve per channel. */
export type SepiaToning = { weights: number[]; lut: number[][] };
/**
 * Sony's DRO, as a gain against log luminance. `lut` is sampled uniformly over
 * [0, logCeiling]; a pixel enters that scale at log2(luma * lumaWhite), so
 * normalised white lands on the top. Null when the shot has no DRO to apply —
 * which includes the strength being zero, since there is then nothing to send.
 */
export type ProfileDro = {
  lut: number[];
  logCeiling: number;
  lumaWhite: number;
  /**
   * The engine's bilateral grid. When present the tone curve is indexed by the
   * *local* log mean sliced out of it — which is what DRO is — instead of by
   * the pixel's own luminance, and `gridLumaWhite` replaces `lumaWhite` because
   * the two paths deliberately put white in different places (see sony/dro.py).
   */
  grid?: ProfileDroGrid | null;
  gridLumaWhite?: number;
};
/** num/den flattened row-major over (ny, nx, bins); `uv` is a 2x3 affine map. */
export type ProfileDroGrid = {
  nx: number; ny: number; bins: number;
  num: number[]; den: number[]; uv: number[];
};
/**
 * Sony's in-camera Clarity, as a post-pass on the finished frame. Every number
 * is the body's own calibration (worker sony/clarity.py); `gain` of zero means
 * the setting is off and the whole chain is skipped.
 */
export type ProfileClarity = {
  gain: number;
  downsample: number;
  edgeThreshold: number;
  centerMix: number;
  rolloffKnee: number;
};
/**
 * Sony's in-camera sharpening, the stage immediately before Clarity. `amount`
 * already folds both camera settings and this shot's own calibration together
 * (worker sony/sharpness.py). Unlike Clarity's, the kernel and its dead zone
 * are the operator's own and live in the shader — only this scale travels.
 *
 * Zero is not a camera position: the body has no "off" for sharpening, and the
 * engine only multiplies the stage out when the SharpnessRange tag is missing
 * or off its ladder. It still means "skip the stage" here.
 */
export type ProfileSharpen = {
  amount: number;
  // The two ladder positions the body recorded, as rendered — for the panel,
  // not for the shader. `amount` is a high-pass scale on a 14-bit plane and
  // means nothing on screen; these are what the camera's own menu showed.
  // `range` is -1 when the tag was unreadable, which is the only way either
  // stage ends up off.
  level?: number;
  range?: number;
};
/**
 * Sony's Spica, the fine half of sharpening, which the engine runs between
 * sharpening and Clarity. `amount` is how much of the filtered value survives
 * its blend and `isoGain` scales the detail before it; both come from the
 * worker (sony/spica.py). The classifier, the 100 weight tables and the three
 * gain curves are the operator's own and live in the shader beside sharpening's
 * kernel.
 *
 * `amount` can exceed 1: the two stages are weighted against each other, and
 * the low end of the SharpnessRange ladder sends more than the whole effect
 * here. Zero means the same thing it does for sharpening — nothing readable to
 * reproduce, not a camera position.
 */
export type ProfileSpica = {
  amount: number;
  isoGain: number;
};
/** A texture with the framebuffer that renders into it (see makeRenderTarget). */
type RenderTarget = { tex: WebGLTexture; fbo: WebGLFramebuffer };
/** One offscreen program, with its uniform locations resolved once. */
type PostProgram = { prog: WebGLProgram; u: Record<string, WebGLUniformLocation | null> };
/**
 * The scene both stages read, plus Clarity's base layer. `base` is null when
 * only sharpening is on — nothing builds a blur layer then, and the compose
 * skips it. Its grid size travels *inside* it rather than beside it, so there
 * is no way to read the dimensions of a base layer that does not exist.
 */
type SonyPostTargets = {
  scene: RenderTarget;
  base: { pair: [RenderTarget, RenderTarget]; w: number; h: number } | null;
  // The one extra full-resolution buffer Spica needs. Spica reads its
  // neighbours, so it cannot run in the same pass as sharpening — the
  // sharpened frame has to exist first — and it cannot write into the texture
  // it is reading. One buffer is enough for both: the chain alternates between
  // this and `scene`, so whichever it wrote last is the one the compose reads.
  // Null when Spica is off, which is when nothing needs an intermediate.
  mid: RenderTarget | null;
  // Render texels per source pixel, capped at 1. Sharpening and Spica step in
  // scene texels, so at a reduced preview scale their kernel reaches across
  // several sensor pixels instead of three and they hit far harder than the
  // camera does; `detailScale` is what corrects for that. See runSonyPost.
  detailScale: number;
};
export type ProfileCurve =
  | {
    lut: Float32Array; srgbBasis: boolean; chroma?: ProfileChroma | null;
    dro?: ProfileDro | null; clarity?: ProfileClarity | null;
    sharpen?: ProfileSharpen | null; spica?: ProfileSpica | null;
  }
  | null;

/**
 * One of a DCP's HueSatMaps, as the worker ships it (dcp.py table_payload):
 * `data` is base64 float16 in the table's own (val, hue, sat, 3) order — which
 * is already the row-major order texImage3D wants for a sat×hue×val texture —
 * holding (hue shift in turns, saturation scale, value scale) per grid point.
 */
export type DcpHueSatTable = {
  dimensions: [number, number, number];   // hue, sat, val counts
  encoding: string;                       // "sRGB" | "Linear"
  data: string;
};

/**
 * The three tables a DCP render can carry, in the order they apply. Adobe's own
 * two come off the profile; `cameraMatch` is the table fitted against the body's
 * JPEG rendering and is the one the Camera Match toggle governs — the toggle is
 * a uniform now, so switching it costs a redraw rather than a decode.
 */
export type DcpTables = {
  hueSatMap: DcpHueSatTable | null;
  lookTable: DcpHueSatTable | null;
  cameraMatch: DcpHueSatTable | null;
};

/**
 * Pull the three HueSatMaps out of a decode's colour profile. Returns null when
 * the render carries none — Sony's engine, or a already-rendered JPEG — which is
 * what switches all three off in the shader.
 *
 * Tolerant by design: a table missing its samples (an older worker, which sent
 * only the dimensions) is dropped rather than thrown on, so the image still
 * renders, just without that stage.
 */
export function parseDcpTables(meta: {
  profileHueSatMap?: unknown; profileLookTable?: unknown; cameraMatch?: unknown;
} | null | undefined): DcpTables | null {
  const one = (raw: unknown): DcpHueSatTable | null => {
    if (!raw || typeof raw !== "object") return null;
    const t = raw as Record<string, unknown>;
    const dims = t["dimensions"];
    if (typeof t["data"] !== "string" || !Array.isArray(dims) || dims.length !== 3) return null;
    if (!dims.every(n => typeof n === "number" && n >= 1)) return null;
    return {
      dimensions: dims as [number, number, number],
      encoding: typeof t["encoding"] === "string" ? t["encoding"] : "Linear",
      data: t["data"],
    };
  };
  const tables: DcpTables = {
    hueSatMap: one(meta?.profileHueSatMap),
    lookTable: one(meta?.profileLookTable),
    cameraMatch: one(meta?.cameraMatch),
  };
  return tables.hueSatMap || tables.lookTable || tables.cameraMatch ? tables : null;
}

/** Shader slot each table binds to, in application order. */
type DcpSlot = "hsm" | "look" | "match";
const DCP_SLOTS: readonly DcpSlot[] = ["hsm", "look", "match"];
const DCP_SAMPLER: Record<DcpSlot, string> = {
  hsm: "u_dcp_hsm", look: "u_dcp_look", match: "u_dcp_match",
};
const DCP_DIMS_UNIFORM: Record<DcpSlot, string> = {
  hsm: "u_dcpHsmDims", look: "u_dcpLookDims", match: "u_dcpMatchDims",
};
// Texture units 0–6 are taken (source, curve, profile curve, mask, sepia, DRO
// LUT, DRO grid); these follow on.
const DCP_TEX_UNIT: Record<DcpSlot, number> = { hsm: 7, look: 8, match: 9 };

/**
 * base64 IEEE half floats -> the Uint16Array texImage3D takes for HALF_FLOAT.
 * The bytes stay untouched: numpy wrote them little-endian and every platform
 * that runs a browser reads them back the same way.
 */
function decodeHalfFloats(b64: string): Uint16Array {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Uint16Array(bytes.buffer);
}

/**
 * Override for the histogram's render window: logical output dims plus the
 * output→source-texcoord transform (see setOutput). Lets the crop editor bin
 * the tight crop box while the canvas shows the padded straighten bbox.
 */
export interface HistogramView {
  width: number;
  height: number;
  texXform: Float32Array;
}

/** A sub-rectangle of the logical output frame, in output-frame pixels. */
export interface ViewWindow { x: number; y: number; w: number; h: number }

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
  highlights: 0, shadows: 0,
  vibrance: 1, clarity: 0, dehaze: 0,
  hslH: [...HSL_ZERO], hslS: [...HSL_ZERO], hslL: [...HSL_ZERO],
  gradShTint: [1, 1, 1], gradMdTint: [1, 1, 1], gradHlTint: [1, 1, 1],
  gradBlend: 0, gradBalance: 0,
  displayGamut: 0,
  lensDist: [...LENS_IDENTITY], lensVig: [...LENS_IDENTITY],
  cameraMatch: 1,
};

export class PipelineRenderer {
  private gl: WebGL2RenderingContext;
  private program: WebGLProgram;
  private uniforms: Record<string, WebGLUniformLocation | null> = {};
  private vao: WebGLVertexArrayObject;
  private sourceTex: WebGLTexture | null = null;
  // The min/mag filter uploadImage settled on for sourceTex. The histogram read
  // forces NEAREST minification and must put *this* back, not re-derive it:
  // whether LINEAR is even legal depends on the uploaded pixel format.
  private sourceFilter = 0;
  private curveLutTex: WebGLTexture | null = null;
  private profileLutTex: WebGLTexture | null = null;
  private hasProfileCurve = false;
  // Whether that curve is defined on sRGB/Rec.709 primaries (Sony's MainGamma)
  // rather than the ProPhoto working space (a DCP's own curve).
  private profileCurveSrgb = false;
  // Sony's RGB2YCC terms, applied right after the curve. null = no such stage.
  private profileChroma: ProfileChroma | null = null;
  private sepiaLutTex: WebGLTexture | null = null;
  private sepiaActive = false;
  // Sony's DRO gain table. null = nothing to apply, which is the common case.
  private droLutTex: WebGLTexture | null = null;
  private droScale: [number, number] = [1, 1];
  private droActive = false;
  // The bilateral grid, as one RG32F texture of (nx*bins) x ny. Sampled with
  // texelFetch and interpolated by hand: num and den have to be interpolated
  // separately and divided at the end, so hardware trilinear on their ratio
  // would be the wrong answer, and float-linear filtering is an extension.
  private droGridTex: WebGLTexture | null = null;
  private droGridDims: [number, number, number] = [0, 0, 0];
  private droGridUV: number[] = [0, 0, 0, 0, 0, 0];
  // The DCP HueSatMaps, as 3D LUTs. Each sampler always has a texture bound —
  // an unbound sampler3D is undefined behaviour on some drivers even when the
  // fetch is branched around — so a missing table gets the 1×1×1 identity and
  // is skipped by its dims (hueCount 0) instead.
  private dcpTableTex: Record<DcpSlot, WebGLTexture | null> = { hsm: null, look: null, match: null };
  private dcpTableDims: Record<DcpSlot, [number, number, number, number]> = {
    hsm: [0, 0, 0, 0], look: [0, 0, 0, 0], match: [0, 0, 0, 0],
  };
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
  // Sub-rectangle of the output frame the canvas covers; null = the whole frame.
  // Only the canvas honours it — histogram and export always see everything.
  private viewWindow: ViewWindow | null = null;
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
  /** Whether R16F render targets are available for the mask pre-pass. */
  private maskSupported = false;
  /** The shared full-screen quad for every offscreen pass; see unitQuadVao. */
  private quadVao0: WebGLVertexArrayObject | null = null;
  // Sony's two post stages on the finished frame: sharpening and then Clarity,
  // which is the order the engine runs them in. Clarity is named apart from
  // EditParams.clarity, this editor's own presence slider and an unrelated
  // number — renderPass has both in scope. Both null (or zero) means renderPass
  // draws straight to its target as before.
  private sonyClarity: ProfileClarity | null = null;
  private sonySharpen: ProfileSharpen | null = null;
  private sonySpica: ProfileSpica | null = null;
  // Spica's two constant tables, uploaded once and shared by every draw. They
  // are the operator's shape rather than anything per-shot, so unlike the other
  // stages' numbers they are textures instead of uniforms — 2500 weights will
  // not fit in a uniform array.
  private spicaWeightTex: WebGLTexture | null = null;
  private spicaLutTex: WebGLTexture | null = null;
  // Compiled lazily and separately: `compose` runs for either stage, the other
  // three are Clarity's alone. A shot with Clarity off — the common case, since
  // the camera's default is 0 — never builds them, and a driver that chokes on
  // one of them cannot take sharpening down with it.
  private sonyPostProgs = new Map<SonyPostProgramName, PostProgram>();
  // The scene target is keyed by output size and the base pair by their own,
  // because the two move independently: the histogram renders at its own
  // dimensions between preview draws, and dragging a crop handle changes the
  // output size every frame while the base grid stays put. Each holds two
  // entries so the preview and the histogram stop evicting each other.
  private sonySceneTargets = new Map<string, RenderTarget>();
  private sonyBaseTargets = new Map<string, [RenderTarget, RenderTarget]>();
  // Spica's intermediate, keyed the same way as the scene it alternates with.
  private sonyMidTargets = new Map<string, RenderTarget>();
  /** Cleared once a compile or a framebuffer check fails, so it is never retried. */
  private sonyPostSupported = false;
  /** Whether float textures are LINEAR-filterable (OES_texture_float_linear). */
  private floatLinear = false;
  // Async histogram read-back: persistent PIXEL_PACK buffer + the in-flight
  // read (only one at a time; concurrent callers share it).
  private histoPbo: WebGLBuffer | null = null;
  private histoPending: Promise<HistogramBins> | null = null;
  private quadBuffers: WebGLBuffer[] = [];
  private destroyed = false;
  /** Whether the uploaded curve LUT differs from the identity (see uploadCurveLUT). */
  private curveActive = false;
  // WB matrix cache: setUniforms runs every draw but temp/tint only change
  // while their sliders move — skip the locus/Bradford math otherwise. Stored
  // row-major; uniformMatrix3fv transposes on upload.
  private wbTemp = Number.NaN;
  private wbTint = Number.NaN;
  private readonly wbMat = new Float32Array(9);
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
    // Same gate as the mask: without float render targets Clarity's base layer
    // has nowhere to live, and sharpening has no 16-bit scene to read. Seeding
    // it here means a device that cannot do this never compiles the four
    // programs to find out.
    this.sonyPostSupported = floatRenderable;
    this.floatLinear = !!gl.getExtension("OES_texture_float_linear");

    const pass = PASSES[0];
    this.program = this.compileProgram(pass.fsSource);
    for (const name of pass.uniforms) this.uniforms[name] = gl.getUniformLocation(this.program, name);
    this.uniforms["u_input"] = gl.getUniformLocation(this.program, "u_input");
    this.uniforms["u_curve_lut"] = gl.getUniformLocation(this.program, "u_curve_lut");
    this.uniforms["u_profile_lut"] = gl.getUniformLocation(this.program, "u_profile_lut");
    this.uniforms["u_dro_lut"] = gl.getUniformLocation(this.program, "u_dro_lut");
    this.uniforms["u_mask_lum"] = gl.getUniformLocation(this.program, "u_mask_lum");
    this.vao = this.createFullScreenQuad();

    // Upload identity LUTs as defaults (user tone curve + DCP profile curve).
    // The curve default comes from the real bake so the RGBA layout has a
    // single owner (curve.ts) instead of a hand-rolled copy here.
    const identity = new Float32Array(LUT_SIZE);
    for (let i = 0; i < LUT_SIZE; i++) identity[i] = i / (LUT_SIZE - 1);
    this.uploadCurveLUT(buildToneCurveLUT(defaultToneCurve()));
    this.profileLutTex = this.makeLutTexture(identity);

    const info = gl.getExtension("WEBGL_debug_renderer_info");
    if (info) console.log("[pipeline] GPU:", gl.getParameter(info.UNMASKED_RENDERER_WEBGL));
  }

  uploadImage(pixels: LinearPixels, width: number, height: number): void {
    const gl = this.gl;
    // texImage2D past MAX_TEXTURE_SIZE only raises GL_INVALID_VALUE: the draw
    // then "succeeds" against an empty texture and the export encodes black.
    // Preview and export both decode the full sensor resolution, so both can
    // reach this — a software fallback (blocklisted GPU) caps at 8192, under a
    // 61 MP frame's long edge. Fail loudly instead.
    const maxTex = gl.getParameter(gl.MAX_TEXTURE_SIZE) as number;
    if (width > maxTex || height > maxTex) {
      throw new Error(
        `Image is ${width}×${height}px, but this device's graphics limit is ${maxTex}px per side. `
        + "Enable hardware acceleration to raise it.");
    }
    this.texWidth = width; this.texHeight = height;
    if (this.sourceTex) gl.deleteTexture(this.sourceTex);
    const tex = gl.createTexture()!;
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
    // A Uint16Array carries IEEE half floats from the worker: upload as RGB16F
    // (odd widths make f16 rows 2-byte aligned, hence UNPACK_ALIGNMENT). Reset
    // both pixel-store params after — they are context-global and would
    // otherwise leak into every later texture upload (LUTs, histogram).
    const half = pixels instanceof Uint16Array;
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, half ? 2 : 4);
    if (half) {
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB16F, width, height, 0, gl.RGB, gl.HALF_FLOAT, pixels);
    } else {
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB32F, width, height, 0, gl.RGB, gl.FLOAT, pixels);
    }
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 4);
    // LINEAR for smooth straighten/crop resampling (NEAREST is identical at
    // 1:1). RGB16F is filterable in core WebGL2; RGB32F needs the extension.
    const filter = half || this.floatLinear ? gl.LINEAR : gl.NEAREST;
    this.sourceFilter = filter;
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, filter);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, filter);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    this.sourceTex = tex;
    // Default to a 1:1, un-cropped output; callers override via setOutput().
    // The drawing buffer itself is (re)sized in draw() — sizing it here would
    // allocate a transient full-resolution buffer that the next draw discards.
    this.outWidth = width; this.outHeight = height;
    this.texXform = new Float32Array([1, 0, 0, 0, -1, 0, 0, 1, 1]);
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

    // R16F is texture-filterable in core WebGL2 (unlike R32F).
    const a = this.makeRenderTarget(w, h, gl.R16F, gl.RED, gl.FLOAT);
    if (!a) {
      this.maskSupported = false; // permanently: u_hasMask=0 -> per-pixel fallback
      return;
    }
    const b = this.makeRenderTarget(w, h, gl.R16F, gl.RED, gl.FLOAT);
    if (!b) {
      gl.deleteTexture(a.tex); gl.deleteFramebuffer(a.fbo);
      this.maskSupported = false;
      return;
    }

    gl.viewport(0, 0, w, h);
    gl.bindVertexArray(this.unitQuadVao());
    const run = (prog: WebGLProgram, src: WebGLTexture, dst: WebGLFramebuffer, dir?: [number, number]) =>
      this.blitQuad(prog, src, dst, () => {
        gl.uniform1i(gl.getUniformLocation(prog, "u_input"), 0);
        if (dir) gl.uniform2f(this.maskBlurDir, dir[0], dir[1]);
      });
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
    // The drawing buffer is sized in draw() from outWidth × previewScale —
    // sizing it here would allocate a full-resolution buffer per crop change.
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

  /**
   * Restrict the canvas to a sub-rectangle of the logical output frame, in
   * output-frame pixels; null renders the whole frame.
   *
   * previewScale alone cannot keep a zoomed-in view cheap: it is capped at 1
   * (never supersample the source), so at 1:1 the canvas grows to the entire
   * frame — 33 MP of fragments for the ~1 MP the viewport can actually show.
   * This is the other half of that budget. The caller positions the canvas to
   * match, and gives the window some slack beyond the viewport so panning does
   * not re-render on every mouse move.
   *
   * Deliberately *not* folded into this.texXform: the histogram renders through
   * the same transform and must keep seeing the whole frame, so the window is
   * composed in draw() and nowhere else.
   */
  setViewWindow(win: ViewWindow | null): void {
    this.viewWindow = win;
  }

  /**
   * this.texXform ∘ (window -> full output frame). Both are affine, so this is
   * one multiply of the 2x3 parts.
   */
  private windowXform(win: ViewWindow): Float32Array {
    const t = this.texXform;
    const [a00, a10, , a01, a11, , tx, ty] = t;
    const sx = win.w / this.outWidth;
    const sy = win.h / this.outHeight;
    const ox = win.x / this.outWidth;
    const oy = win.y / this.outHeight;
    return new Float32Array([
      a00! * sx, a10! * sx, 0,
      a01! * sy, a11! * sy, 0,
      a00! * ox + a01! * oy + tx!, a10! * ox + a11! * oy + ty!, 1,
    ]);
  }

  /**
   * Write a LUT that is already on the shared grid: create the texture on the
   * first call, then re-upload in place. Every LUT here is `LUT_SIZE`×1, so
   * this is the whole difference between the four of them.
   */
  private writeLut(tex: WebGLTexture | null, data: Float32Array, channels: 1 | 4 = 1): WebGLTexture {
    const gl = this.gl;
    if (!tex) return this.makeLutTexture(data, channels);
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 4);
    gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, LUT_SIZE, 1, channels === 4 ? gl.RGBA : gl.RED, gl.FLOAT, data);
    return tex;
  }

  private makeLutTexture(lut: Float32Array, channels: 1 | 4 = 1): WebGLTexture {
    const gl = this.gl;
    const tex = gl.createTexture()!;
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 4);
    if (channels === 4) {
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32F, LUT_SIZE, 1, 0, gl.RGBA, gl.FLOAT, lut);
    } else {
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.R32F, LUT_SIZE, 1, 0, gl.RED, gl.FLOAT, lut);
    }
    // Prefer LINEAR for smooth interpolation; fall back to NEAREST if float-linear not available
    const filter = this.floatLinear ? gl.LINEAR : gl.NEAREST;
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, filter);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, filter);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    return tex;
  }

  /**
   * Upload an interleaved RGBA tone-curve LUT (length LUT_SIZE*4: per-channel
   * point curves in .rgb, the film-like master stack in .a — see
   * buildToneCurveLUT). The texture is created once and updated in place
   * (texSubImage2D): this is re-run every rAF while dragging Contrast/Blacks,
   * so delete+create would churn a fresh GPU allocation per frame.
   */
  uploadCurveLUT(lut: Float32Array): void {
    // Identity detection (one 8k-float scan per bake): the shader skips the
    // curve block's 5 LUT fetches per pixel when the bake does nothing — the
    // default-slider state and the hold-to-compare baseline.
    this.curveActive = false;
    for (let i = 0; i < LUT_SIZE; i++) {
      const x = i / (LUT_SIZE - 1);
      if (Math.abs(lut[i * 4] - x) > 1e-6 || Math.abs(lut[i * 4 + 1] - x) > 1e-6
        || Math.abs(lut[i * 4 + 2] - x) > 1e-6 || Math.abs(lut[i * 4 + 3] - x) > 1e-6) {
        this.curveActive = true;
        break;
      }
    }
    this.curveLutTex = this.writeLut(this.curveLutTex, lut, 4);
  }

  /**
   * Upload the camera profile tone curve as a per-channel LUT applied in the
   * Lightroom-style view transform (the camera's display rendering). Pass null
   * to disable it (e.g. no profile / monochrome profile) and fall back to identity.
   */
  uploadProfileCurveLUT(curve: ProfileCurve): void {
    let data = curve?.lut;
    if (!data) {
      data = new Float32Array(LUT_SIZE);
      for (let i = 0; i < LUT_SIZE; i++) data[i] = i / (LUT_SIZE - 1);
    }
    this.profileLutTex = this.writeLut(this.profileLutTex, data);
    this.hasProfileCurve = curve != null;
    this.profileCurveSrgb = curve?.srgbBasis ?? false;
    this.profileChroma = curve?.chroma ?? null;
    this.uploadSepiaLUT(curve?.chroma?.sepia ?? null);
    this.uploadDroLUT(curve?.dro ?? null);
    // Neither post stage needs a texture of its own — every constant is a
    // uniform, so a moved Clarity slider costs one uniform upload and a redraw.
    // Both are normalised to null when they would do nothing, which is what
    // renderPass and prepareSonyPost branch on.
    const clarity = curve?.clarity ?? null;
    this.sonyClarity = clarity && clarity.gain > 0 && clarity.downsample > 1 ? clarity : null;
    const sharpen = curve?.sharpen ?? null;
    this.sonySharpen = sharpen && sharpen.amount > 0 ? sharpen : null;
    const spica = curve?.spica ?? null;
    this.sonySpica = spica && spica.amount > 0 ? spica : null;
    this.uploadSonyPostUniforms();
  }

  /**
   * Upload a DCP's HueSatMaps as 3D LUTs. Pass null for a render that has none
   * (Sony's engine, a plain JPEG), which binds identities and switches all three
   * off. Costs one small upload per decoded image and nothing per frame — which
   * is the point: these used to be baked into the decode, so changing the DCP
   * style or the camera match meant re-reading the RAW.
   */
  uploadDcpTables(tables: DcpTables | null): void {
    const bySlot: Record<DcpSlot, DcpHueSatTable | null> = {
      hsm: tables?.hueSatMap ?? null,
      look: tables?.lookTable ?? null,
      match: tables?.cameraMatch ?? null,
    };
    for (const slot of DCP_SLOTS) {
      const table = bySlot[slot];
      this.dcpTableTex[slot] = this.writeHsvTable(this.dcpTableTex[slot], table);
      this.dcpTableDims[slot] = table
        ? [table.dimensions[0], table.dimensions[1], table.dimensions[2],
           table.encoding === "sRGB" ? 1 : 0]
        : [0, 0, 0, 0];
    }
  }

  /**
   * (Re)create one HueSatMap texture. sat × hue × val, matching the payload's
   * (val, hue, sat, 3) row-major order with no transpose. Hue wraps because the
   * table's hue axis is periodic — grid point i sits at i/hueCount, and REPEAT
   * is what closes the seam between the last point and the first. Saturation and
   * value are endpoint-aligned instead, so they clamp.
   */
  private writeHsvTable(prev: WebGLTexture | null, table: DcpHueSatTable | null): WebGLTexture {
    const gl = this.gl;
    if (prev) gl.deleteTexture(prev);
    const [hue, sat, val] = table?.dimensions ?? [1, 1, 1];
    // Identity delta: no hue shift, unit saturation and value scales.
    const data = table ? decodeHalfFloats(table.data) : new Uint16Array([0x0000, 0x3c00, 0x3c00]);
    const tex = gl.createTexture()!;
    gl.bindTexture(gl.TEXTURE_3D, tex);
    // f16 rows are 2-byte aligned; an odd saturation count would tear otherwise.
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 2);
    gl.texImage3D(gl.TEXTURE_3D, 0, gl.RGB16F, sat, hue, val, 0, gl.RGB, gl.HALF_FLOAT, data);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 4);
    gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);  // saturation
    gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_WRAP_T, gl.REPEAT);         // hue
    gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_WRAP_R, gl.CLAMP_TO_EDGE);  // value
    gl.bindTexture(gl.TEXTURE_3D, null);
    return tex;
  }

  /**
   * Sony's DRO gain table, resampled onto the shared LUT grid. The worker's
   * table is already uniform in log luminance, so this is a straight resample
   * and the shader's index is just the log mapped into [0, 1].
   */
  private uploadDroLUT(dro: ProfileDro | null): void {
    this.droActive = dro != null && dro.lut.length > 1;
    this.droGridDims = [0, 0, 0];
    if (!this.droActive || !dro) return;
    this.uploadDroGrid(dro.grid ?? null);
    // With a grid the curve is indexed by the local mean, which lives on the
    // engine's own luma scale; without one it is indexed by the pixel and the
    // fallback scale applies. Picking the wrong one shifts everything a stop.
    const gridOn = this.droGridDims[2] > 0;
    this.droScale = [dro.logCeiling, gridOn ? (dro.gridLumaWhite ?? dro.lumaWhite) : dro.lumaWhite];
    const knots = dro.lut;
    const data = new Float32Array(LUT_SIZE);
    for (let i = 0; i < LUT_SIZE; i++) {
      const t = (i / (LUT_SIZE - 1)) * (knots.length - 1);
      const lo = Math.min(Math.floor(t), knots.length - 2), f = t - lo;
      data[i] = knots[lo]! * (1 - f) + knots[lo + 1]! * f;
    }
    this.droLutTex = this.writeLut(this.droLutTex, data);
  }

  /**
   * The bilateral grid, laid out as (nx*bins) x ny with num in R and den in G.
   * Nearest filtering throughout — the shader does its own trilinear so that
   * num and den stay separate until the divide, which is what the engine does.
   */
  private uploadDroGrid(grid: ProfileDroGrid | null): void {
    const gl = this.gl;
    const want = grid && grid.nx > 0 && grid.ny > 0 && grid.bins > 0
      && grid.num.length === grid.nx * grid.ny * grid.bins
      && grid.den.length === grid.num.length && grid.uv.length === 6;
    if (!want || !grid) return;
    const width = grid.nx * grid.bins;
    const data = new Float32Array(width * grid.ny * 2);
    for (let y = 0; y < grid.ny; y++) {
      for (let x = 0; x < grid.nx; x++) {
        for (let b = 0; b < grid.bins; b++) {
          const src = (y * grid.nx + x) * grid.bins + b;
          const dst = (y * width + x * grid.bins + b) * 2;
          data[dst] = grid.num[src]!;
          data[dst + 1] = grid.den[src]!;
        }
      }
    }
    if (!this.droGridTex) this.droGridTex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, this.droGridTex);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 4);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RG32F, width, grid.ny, 0, gl.RG, gl.FLOAT, data);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    this.droGridDims = [grid.nx, grid.ny, grid.bins];
    this.droGridUV = [...grid.uv];
  }

  /**
   * Sepia's toning table: one curve per channel, indexed by a weighted sum of
   * the encoded RGB. Resampled from the worker's knots onto the shared LUT grid
   * and uploaded as RGBA so one texture fetch reads all three channels.
   */
  private uploadSepiaLUT(sepia: SepiaToning | null): void {
    this.sepiaActive = sepia != null;
    if (!sepia) return;
    const knots = sepia.lut;
    const data = new Float32Array(LUT_SIZE * 4);
    for (let i = 0; i < LUT_SIZE; i++) {
      const t = (i / (LUT_SIZE - 1)) * (knots.length - 1);
      const lo = Math.min(Math.floor(t), knots.length - 2), f = t - lo;
      for (let k = 0; k < 3; k++) {
        data[i * 4 + k] = knots[lo]![k]! * (1 - f) + knots[lo + 1]![k]! * f;
      }
      data[i * 4 + 3] = 1;
    }
    this.sepiaLutTex = this.writeLut(this.sepiaLutTex, data, 4);
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
    // When a view window is set the canvas covers only that part of the frame,
    // so both the buffer size and the transform come off the window.
    const win = this.viewWindow;
    const cw = Math.max(1, Math.round((win ? win.w : this.outWidth) * this.previewScale));
    const ch = Math.max(1, Math.round((win ? win.h : this.outHeight) * this.previewScale));
    if (this.canvas.width !== cw) this.canvas.width = cw;
    if (this.canvas.height !== ch) this.canvas.height = ch;
    this.renderPass(null, cw, ch, p, win ? this.windowXform(win) : undefined);
  }

  /**
   * Render the current source + params into `fbo` (null = canvas) at w×h.
   *
   * The single entry point for every path — preview, export and histogram all
   * land here — which is why Sony's post stages hang off it: they follow the
   * render wherever it goes, with no second place to keep in step.
   */
  private renderPass(fbo: WebGLFramebuffer | null, w: number, h: number, p: EditParams, texXform?: Float32Array): void {
    const gl = this.gl;
    if (!this.sourceTex) return;
    // With either post stage on, the main pass renders into a scene target and
    // the chain below composes from it into `fbo` instead.
    const xform = texXform ?? this.texXform;
    const post = this.sonyClarity || this.sonySharpen || this.sonySpica
      ? this.prepareSonyPost(w, h, xform)
      : null;
    gl.bindFramebuffer(gl.FRAMEBUFFER, post ? post.scene.fbo : fbo);
    gl.viewport(0, 0, w, h);
    gl.useProgram(this.program);
    // Crop / straighten transform + out-of-image fill.
    const xfLoc = this.uniforms["u_texXform"];
    if (xfLoc) gl.uniformMatrix3fv(xfLoc, false, xform);
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
    if (this.sepiaLutTex) {
      gl.activeTexture(gl.TEXTURE4);
      gl.bindTexture(gl.TEXTURE_2D, this.sepiaLutTex);
      gl.uniform1i(this.uniforms["u_sepia_lut"], 4);
    }
    if (this.droLutTex) {
      gl.activeTexture(gl.TEXTURE5);
      gl.bindTexture(gl.TEXTURE_2D, this.droLutTex);
      gl.uniform1i(this.uniforms["u_dro_lut"], 5);
    }
    if (this.droGridTex) {
      gl.activeTexture(gl.TEXTURE6);
      gl.bindTexture(gl.TEXTURE_2D, this.droGridTex);
      gl.uniform1i(this.uniforms["u_dro_grid"], 6);
    }
    // Bound unconditionally, unlike the 2D LUTs above: a sampler3D left pointing
    // at unit 0 would read the (2D) source texture, which is an incomplete-
    // texture error on some drivers even though the fetch is branched around.
    for (const slot of DCP_SLOTS) {
      const unit = DCP_TEX_UNIT[slot];
      gl.activeTexture(gl.TEXTURE0 + unit);
      gl.bindTexture(gl.TEXTURE_3D, this.dcpTableTex[slot]);
      gl.uniform1i(this.uniforms[DCP_SAMPLER[slot]], unit);
      gl.uniform4fv(this.uniforms[DCP_DIMS_UNIFORM[slot]], this.dcpTableDims[slot]);
    }
    gl.activeTexture(gl.TEXTURE0);
    this.setUniforms(p);
    gl.bindVertexArray(this.vao);
    gl.drawArrays(gl.TRIANGLES, 0, 6);
    if (post) this.runSonyPost(post, fbo, w, h);
  }

  /**
   * Render targets for Sony's post chain, plus the base grid Clarity was sized
   * for. Returns null — leaving renderPass to draw straight to its target — if
   * anything is missing.
   *
   * The base grid is a fraction of the *source*, not of `w`, so that the blur
   * radius stays the same fraction of the frame whatever resolution the frame
   * is rendered at; that is what makes a preview agree with its export, and is
   * the same rule buildLumaMask follows. It is capped at the scene's own size,
   * because a base layer denser than the scene it reduces is pure cost: the
   * histogram renders at 512 px and would otherwise still build the full
   * sensor's 1/8 grid.
   */
  private prepareSonyPost(w: number, h: number, xform: Float32Array): SonyPostTargets | null {
    if (!this.sonyPostSupported) return null;
    if (!this.sonyClarity && !this.sonySharpen && !this.sonySpica) return null;
    if (!this.postProgram("compose")) return null;

    const float = this.sceneNeedsFloat();
    const scene = this.cachedSceneTarget(w, h, float);
    if (!scene) return null;
    const zoom = Math.hypot(xform[0], xform[1]) || 1;
    const detailScale = Math.min(1, w / Math.max(1, this.texWidth * zoom));
    // Spica needs its programs, its tables and the intermediate all present; if
    // any of them will not build, the rest of the chain still runs without it
    // rather than the whole post pass disappearing.
    let mid: RenderTarget | null = null;
    if (this.sonySpica && this.postProgram("spica") && this.uploadSpicaTables()) {
      mid = this.cachedMidTarget(w, h, float);
    }
    if (this.sonySpica && !mid) this.sonySpica = null;
    if (!this.sonyClarity) return { scene, base: null, mid, detailScale };
    if (!this.postProgram("down") || !this.postProgram("edge") || !this.postProgram("blur")) return null;

    const down = this.sonyClarity.downsample;
    const bw = Math.max(1, Math.min(w, Math.round((this.texWidth * zoom) / down)));
    const bh = Math.max(1, Math.min(h, Math.round((this.texHeight * zoom) / down)));
    const pair = this.cachedBasePair(bw, bh);
    if (!pair) return null;
    return { scene, base: { pair, w: bw, h: bh }, mid, detailScale };
  }

  /**
   * Whether the scene target has to hold more than 8 bits per channel.
   *
   * Named for the reason rather than the stage, because the reason is what the
   * next stage has to check itself against: 8 bits is enough to *hold* a
   * display-encoded frame — everything downstream of it is 8-bit — but not to
   * run a threshold on. Sharpening's high-pass amplifies the scene by ~26x
   * before comparing it against a dead zone of 1024/16383, so 8-bit
   * quantisation alone would put noise at ~half that dead zone and let it
   * decide which pixels sharpen; 16F puts it at ~6%. Clarity has no such
   * problem — its detail term is neither amplified nor thresholded.
   *
   * Spica needs it for a different reason and needs it more: its classifier
   * compares neighbours against a range threshold of 8/16383, which 8-bit
   * quantisation cannot even represent — every pixel would read as flat.
   */
  private sceneNeedsFloat(): boolean {
    return this.sonySharpen !== null || this.sonySpica !== null;
  }

  /**
   * The scene target for one output size, keeping the last two alive.
   *
   * The precision is part of the key, not just the size: the same w×h at 16F
   * and at 8-bit are different resources, and handing back the wrong one would
   * either waste half the memory or silently sharpen 8-bit data.
   *
   * Two entries, because the preview and the histogram render at their own
   * sizes and would otherwise evict each other every frame. Reinserting on a
   * hit makes that a true LRU: with plain insertion order a crop drag (a new
   * preview size every mousemove) evicts the histogram's target on alternate
   * frames, re-allocating it for nothing.
   */
  private cachedSceneTarget(w: number, h: number, float: boolean): RenderTarget | null {
    const gl = this.gl;
    const cache = this.sonySceneTargets;
    const key = `${w}x${h}:${float ? "f" : "b"}`;
    const hit = cache.get(key);
    if (hit) {
      cache.delete(key);
      cache.set(key, hit);
      return hit;
    }
    const made = float
      ? this.makeRenderTarget(w, h, gl.RGBA16F, gl.RGBA, gl.FLOAT)
      : this.makeRenderTarget(w, h, gl.RGBA8, gl.RGBA, gl.UNSIGNED_BYTE);
    if (!made) {
      this.sonyPostSupported = false;   // permanently: fall back to no post pass
      return null;
    }
    this.evictOldest(cache, 1);
    cache.set(key, made);
    return made;
  }

  /**
   * Spica's intermediate, cached exactly like the scene target and in the same
   * format — the two alternate, so a mismatch would silently lose precision on
   * every other pass. Kept in its own cache rather than as a third scene entry
   * so that a preview and a histogram at different sizes cannot evict it.
   */
  private cachedMidTarget(w: number, h: number, float: boolean): RenderTarget | null {
    const gl = this.gl;
    const key = `${w}x${h}:${float ? "f" : "b"}`;
    const hit = this.sonyMidTargets.get(key);
    if (hit) {
      this.sonyMidTargets.delete(key);
      this.sonyMidTargets.set(key, hit);
      return hit;
    }
    const made = float
      ? this.makeRenderTarget(w, h, gl.RGBA16F, gl.RGBA, gl.FLOAT)
      : this.makeRenderTarget(w, h, gl.RGBA8, gl.RGBA, gl.UNSIGNED_BYTE);
    // Unlike the scene target, failing here is not fatal to the post chain:
    // prepareSonyPost drops Spica and the other stages carry on.
    if (!made) return null;
    this.evictOldest(this.sonyMidTargets, 1);
    this.sonyMidTargets.set(key, made);
    return made;
  }

  /**
   * Spica's weight tables and classifier LUT, uploaded once. False if they will
   * not build, which drops the stage rather than the whole chain.
   *
   * R32F sampled with NEAREST needs no extension in WebGL2 — only *filtering*
   * float textures does, and neither of these is ever filtered: both are
   * indexed by texelFetch with integers the shader computed.
   */
  private uploadSpicaTables(): boolean {
    if (this.spicaWeightTex && this.spicaLutTex) return true;
    const gl = this.gl;
    const make = (w: number, h: number, internal: number, format: number,
                  data: Float32Array): WebGLTexture | null => {
      const tex = gl.createTexture();
      if (!tex) return null;
      gl.bindTexture(gl.TEXTURE_2D, tex);
      gl.texImage2D(gl.TEXTURE_2D, 0, internal, w, h, 0, format, gl.FLOAT, data);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      return tex;
    };
    // One row per table, one texel per tap.
    const weights = make(SPICA_TAP_COUNT, SPICA_TABLE_COUNT, gl.R32F, gl.RED,
                         Float32Array.from(SPICA_WEIGHTS));
    // RGBA rather than RGB: three-channel float textures are not required to be
    // supported, and the fourth channel costs 1 kB in total.
    const lut = new Float32Array(SPICA_CODE_COUNT * 4);
    for (let i = 0; i < SPICA_CODE_COUNT; i++) {
      lut[i * 4] = SPICA_LUT[i * 3];
      lut[i * 4 + 1] = SPICA_LUT[i * 3 + 1];
      lut[i * 4 + 2] = SPICA_LUT[i * 3 + 2];
    }
    const lutTex = weights && make(SPICA_CODE_COUNT, 1, gl.RGBA32F, gl.RGBA, lut);
    if (!weights || !lutTex) {
      if (weights) gl.deleteTexture(weights);
      return false;
    }
    this.spicaWeightTex = weights;
    this.spicaLutTex = lutTex;
    return true;
  }

  /** The base layer's ping-pong pair, cached the same way. */
  private cachedBasePair(sw: number, sh: number): [RenderTarget, RenderTarget] | null {
    const key = `${sw}x${sh}`;
    const hit = this.sonyBaseTargets.get(key);
    if (hit) return hit;
    const gl = this.gl;
    // R16F, LINEAR-filterable in core WebGL2 — which is what lets the compose
    // step get its bilinear upsample for free.
    const a = this.makeRenderTarget(sw, sh, gl.R16F, gl.RED, gl.FLOAT);
    const b = a && this.makeRenderTarget(sw, sh, gl.R16F, gl.RED, gl.FLOAT);
    if (!a || !b) {
      if (a) { gl.deleteTexture(a.tex); gl.deleteFramebuffer(a.fbo); }
      this.sonyPostSupported = false;
      return null;
    }
    this.evictOldest(this.sonyBaseTargets, 1);
    this.sonyBaseTargets.set(key, [a, b]);
    return [a, b];
  }

  /** Free entries in insertion order until `keep` or fewer remain. */
  private evictOldest(cache: Map<string, RenderTarget | RenderTarget[]>, keep: number): void {
    const gl = this.gl;
    for (const [key, entry] of cache) {
      if (cache.size <= keep) break;
      for (const t of Array.isArray(entry) ? entry : [entry]) {
        gl.deleteTexture(t.tex);
        gl.deleteFramebuffer(t.fbo);
      }
      cache.delete(key);
    }
  }

  /**
   * Sony's post chain, from the rendered scene into `fbo`.
   *
   * The engine's order is `Sharpness -> Spica -> Clarity`, and this follows it.
   * Clarity's three small passes run first and only when it is on — downsample
   * to luma, edge-aware mean, Gaussian, which is the engine's own order
   * (sony_repro/PIPELINE.md 7.9.1); its base layer is deliberately built from
   * the *unsharpened* scene, so it has to be built before anything overwrites
   * it, and an 8x box mean has nothing left of a +-3 pixel high-pass anyway.
   *
   * With Spica off, the compose applies sharpening and Clarity in one pass, as
   * it always did. With Spica on it takes two, because Spica reads its
   * neighbours and so needs the sharpened frame to exist: compose writes
   * sharpening alone into `mid`, Spica reads that back into `scene`, and the
   * second compose applies Clarity to it. Alternating between the two buffers
   * is what keeps a pass from sampling the texture it is writing.
   *
   * Only the geometry uniforms and `u_sharpen` move here; the rest of the
   * per-shot constants are set once, when the profile lands (see
   * uploadSonyPostUniforms). `u_sharpen` cannot be one of them any more — the
   * same program runs twice with two different values for it.
   */
  private runSonyPost(t: SonyPostTargets, fbo: WebGLFramebuffer | null, w: number, h: number): void {
    const gl = this.gl;
    const compose = this.sonyPostProgs.get("compose")!;
    const { scene, base, mid } = t;

    gl.bindVertexArray(this.unitQuadVao());
    if (base) {
      const down = this.sonyPostProgs.get("down")!;
      const edge = this.sonyPostProgs.get("edge")!;
      const blur = this.sonyPostProgs.get("blur")!;
      const [a, b] = base.pair;
      const cell: [number, number] = [1 / base.w, 1 / base.h];
      gl.viewport(0, 0, base.w, base.h);
      this.blitQuad(down.prog, scene.tex, a.fbo, () => {
        gl.uniform2f(down.u["u_cell"]!, ...cell);
        // Taps per axis over one base cell, capped by how many scene texels the
        // cell actually spans — beyond that they read the same texel repeatedly.
        gl.uniform1i(down.u["u_taps"]!, Math.max(1, Math.min(4, Math.floor(w / base.w / 2))));
      });
      this.blitQuad(edge.prog, a.tex, b.fbo, () => gl.uniform2f(edge.u["u_texel"]!, ...cell));
      this.blitQuad(blur.prog, b.tex, a.fbo, () => gl.uniform2f(blur.u["u_texel"]!, ...cell));
    }

    gl.viewport(0, 0, w, h);

    // Which buffer the compose finally reads. Without Spica it is the scene
    // itself and nothing below runs.
    let src = scene;
    // Both kernels step in scene texels, so a preview rendered at a fraction of
    // the source reaches across that many more sensor pixels — and the camera's
    // deadzone, an absolute threshold on the high-pass, stops holding flat areas
    // back. Measured against the engine's own tiles (sharpen at full resolution
    // then downsample, versus downsample then sharpen) the preview came out
    // 2.9-3.5x too strong at 1/2, 8-13x at 1/4 and 24-49x at 1/8 — i.e. roughly
    // (1/scale)^1.75, which is what the exponent undoes. It is a fit, not a
    // derivation: the true ratio depends on how much fine structure the frame
    // holds, and it spans that 8-13 at a single scale. Exports render at source
    // resolution, where the scale is 1 and none of this applies.
    const detail = Math.pow(t.detailScale, 1.75);
    const sharpenAmount = (this.sonySharpen?.amount ?? 0) * detail;
    if (mid) {
      if (sharpenAmount > 0) {
        // Sharpening alone into `mid`: u_gain is already 0 unless Clarity is
        // on, and Clarity must not run here — it would be applied twice.
        this.blitQuad(compose.prog, src.tex, mid.fbo, () => {
          gl.uniform2f(compose.u["u_sceneTexel"]!, 1 / w, 1 / h);
          gl.uniform1f(compose.u["u_sharpen"]!, sharpenAmount);
          gl.uniform1f(compose.u["u_gain"]!, 0);
        });
        src = mid;
      }
      const spica = this.sonyPostProgs.get("spica")!;
      const dst = src === scene ? mid : scene;
      this.blitQuad(spica.prog, src.tex, dst.fbo, () => {
        gl.uniform2f(spica.u["u_sceneTexel"]!, 1 / w, 1 / h);
        // Same correction, and Spica needs it more: its range threshold and the
        // three curve breakpoints are all calibrated against the amplitude of a
        // sensor-pixel neighbourhood, so a stretched kernel lands them on the
        // wrong segment as well as overshooting. Set here rather than with the
        // other uniforms because only this scope knows the render scale.
        gl.uniform1f(spica.u["u_amount"]!, (this.sonySpica?.amount ?? 0) * detail);
        gl.activeTexture(gl.TEXTURE1);
        gl.bindTexture(gl.TEXTURE_2D, this.spicaWeightTex);
        gl.activeTexture(gl.TEXTURE2);
        gl.bindTexture(gl.TEXTURE_2D, this.spicaLutTex);
        gl.activeTexture(gl.TEXTURE0);
      });
      src = dst;
    }

    this.blitQuad(compose.prog, src.tex, fbo, () => {
      // The sharpen kernel steps in scene texels — three of them either way,
      // which is what the engine's three sensor pixels become here.
      gl.uniform2f(compose.u["u_sceneTexel"]!, 1 / w, 1 / h);
      // Zero when Spica ran, because the pass above already sharpened `src`.
      gl.uniform1f(compose.u["u_sharpen"]!, mid ? 0 : sharpenAmount);
      gl.uniform1f(compose.u["u_gain"]!, this.sonyClarity?.gain ?? 0);
      // Unit 1 always gets a complete texture, even with Clarity off: the
      // sampler is declared whatever the gain does, and leaving it pointing at
      // nothing is an incomplete-texture error on some drivers. The shader's
      // `u_gain > 0` branch means it is never actually read, which is also why
      // u_baseTexel is only worth setting when there is a base layer.
      gl.activeTexture(gl.TEXTURE1);
      gl.bindTexture(gl.TEXTURE_2D, base ? base.pair[0].tex : scene.tex);
      if (base) gl.uniform2f(compose.u["u_baseTexel"]!, 1 / base.w, 1 / base.h);
      gl.activeTexture(gl.TEXTURE0);
    });
  }

  /**
   * One post program, compiled and memoized on first use; null if it will not
   * build. Uniform locations are resolved here once, because runSonyPost is on
   * the per-frame path and looking them up by name per draw would be a
   * string-keyed driver query per uniform per frame — the same reason
   * this.uniforms and maskBlurDir exist.
   *
   * The name list comes from passes.ts beside the shader itself, so the two
   * cannot drift: a stale name yields a null location, and gl.uniform1f(null)
   * is a silent no-op that would just stop the stage running.
   */
  private postProgram(name: SonyPostProgramName): PostProgram | null {
    const gl = this.gl;
    const hit = this.sonyPostProgs.get(name);
    if (hit) return hit;
    const def = SONY_POST_PROGRAMS[name];
    let built: PostProgram;
    try {
      const prog = this.compileProgramVS(MASK_VERTEX_SHADER, def.fsSource);
      const u: Record<string, WebGLUniformLocation | null> = {};
      for (const n of def.uniforms) u[n] = gl.getUniformLocation(prog, n);
      built = { prog, u };
    } catch (err) {
      console.warn(`[pipeline] sony post program ${name} unavailable:`, err);
      this.sonyPostSupported = false;
      return null;
    }
    // Sampler bindings are per-program state, so they are set once here rather
    // than per draw. Every small pass reads its input on unit 0; compose also
    // takes the base layer on unit 1.
    gl.useProgram(built.prog);
    if (built.u["u_input"]) gl.uniform1i(built.u["u_input"], 0);
    if (built.u["u_scene"]) gl.uniform1i(built.u["u_scene"], 0);
    if (built.u["u_base"]) gl.uniform1i(built.u["u_base"], 1);
    this.sonyPostProgs.set(name, built);
    this.uploadSonyPostUniforms();
    return built;
  }

  /**
   * The per-shot constants for both stages, which change only when a new
   * profile lands. Each stage's gain is written even when the stage is off:
   * that zero is what the compose shader branches on, so it is exactly the case
   * that must not be skipped. The rest are only meaningful alongside a gain
   * that is non-zero, so they travel with their own stage.
   */
  private uploadSonyPostUniforms(): void {
    const gl = this.gl;
    const c = this.sonyClarity;
    const edge = this.sonyPostProgs.get("edge");
    const blur = this.sonyPostProgs.get("blur");
    if (c && edge) {
      gl.useProgram(edge.prog);
      gl.uniform1f(edge.u["u_threshold"]!, c.edgeThreshold);
    }
    if (c && blur) {
      gl.useProgram(blur.prog);
      gl.uniform1f(blur.u["u_centerMix"]!, c.centerMix);
    }
    const spica = this.sonyPostProgs.get("spica");
    if (spica) {
      gl.useProgram(spica.prog);
      gl.uniform1f(spica.u["u_amount"]!, this.sonySpica?.amount ?? 0);
      gl.uniform1f(spica.u["u_isoGain"]!, this.sonySpica?.isoGain ?? 1);
      // The samplers, bound once: the tables never move between draws.
      gl.uniform1i(spica.u["u_scene"]!, 0);
      gl.uniform1i(spica.u["u_weights"]!, 1);
      gl.uniform1i(spica.u["u_lut"]!, 2);
    }
    const compose = this.sonyPostProgs.get("compose");
    if (!compose) return;
    gl.useProgram(compose.prog);
    // u_gain and u_sharpen are set per draw instead — with Spica on, compose
    // runs twice and wants a different value for each of them each time.
    if (c) gl.uniform1f(compose.u["u_knee"]!, c.rolloffKnee);
  }

  /** Free every cached post-chain render target. */
  private releaseSonyPostTargets(): void {
    this.evictOldest(this.sonySceneTargets, 0);
    this.evictOldest(this.sonyBaseTargets, 0);
    this.evictOldest(this.sonyMidTargets, 0);
  }

  /**
   * Bin a 256-entry histogram (R, G, B, luma) from the processed, display-encoded
   * output. Whatever the shader does, the histogram reflects it exactly — there
   * is no JS mirror of the pipeline.
   *
   * The GPU path scatters every pixel of a HISTO_LONG_GPU-capped render into a
   * float accumulation target, so the only read-back is 256×4 counts, and that
   * read-back is asynchronous (PBO + fence): the returned promise resolves when
   * the GPU has finished, so the main thread never stalls on a sync while a
   * slider drag is redrawing. Only one read is in flight at a time; concurrent
   * callers share it.
   *
   * `view` overrides the histogram's render window (dims + output→texcoord
   * transform). The crop editor passes the tight crop box here: its canvas
   * renders a padded bounding box whose out-of-image fill would otherwise be
   * binned as real pixels.
   */
  readHistogram(view?: HistogramView): Promise<HistogramBins> {
    if (this.histoPending) return this.histoPending;
    const run = this.histoGpuSupported
      ? this.readHistogramGPU(view)
      : Promise.resolve(this.readHistogramCPU(view));
    this.histoPending = run.finally(() => { this.histoPending = null; });
    return this.histoPending;
  }

  /** CPU fallback: render a small copy, read it back (sync), and bin it in JS. */
  private readHistogramCPU(view?: HistogramView): HistogramBins {
    const gl = this.gl;
    const bins: HistogramBins = {
      r: new Uint32Array(256), g: new Uint32Array(256),
      b: new Uint32Array(256), l: new Uint32Array(256),
    };
    if (!this.sourceTex || !this.outWidth || !this.outHeight) return bins;

    const { w, h } = this.ensureHistoFbo(HISTO_LONG_CPU, view);
    // Point-sample the source (NEAREST) so the downscale doesn't average
    // neighbours — averaging narrows the distribution and hides clipping.
    gl.bindTexture(gl.TEXTURE_2D, this.sourceTex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    this.renderPass(this.histoFbo, w, h, this.lastParams, view?.texXform);
    gl.bindTexture(gl.TEXTURE_2D, this.sourceTex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, this.sourceFilter);
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
  private async readHistogramGPU(view?: HistogramView): Promise<HistogramBins> {
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
    const { w, h } = this.ensureHistoFbo(HISTO_LONG_GPU, view);
    gl.bindTexture(gl.TEXTURE_2D, this.sourceTex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    this.renderPass(this.histoFbo, w, h, this.lastParams, view?.texXform);
    gl.bindTexture(gl.TEXTURE_2D, this.sourceTex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, this.sourceFilter);

    // 2. Scatter every pixel into 256 bins × 4 rows (R,G,B,L) via additive float
    //    blending — a full-image histogram computed entirely on the GPU.
    if (!this.ensureHistoBin()) return this.readHistogramCPU(view); // float FBO incomplete
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

    // 3. Queue the 256×4 read-back (16 KB) into a PBO and wait on a fence, so
    //    the copy happens without forcing a full GPU sync on the main thread.
    // The PBO is allocated once — the size never changes across reads.
    if (!this.histoPbo) {
      this.histoPbo = gl.createBuffer()!;
      gl.bindBuffer(gl.PIXEL_PACK_BUFFER, this.histoPbo);
      gl.bufferData(gl.PIXEL_PACK_BUFFER, 256 * 4 * 4 * 4, gl.STREAM_READ);
    } else {
      gl.bindBuffer(gl.PIXEL_PACK_BUFFER, this.histoPbo);
    }
    gl.readPixels(0, 0, 256, 4, gl.RGBA, gl.FLOAT, 0);
    gl.bindBuffer(gl.PIXEL_PACK_BUFFER, null);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    const sync = gl.fenceSync(gl.SYNC_GPU_COMMANDS_COMPLETE, 0);
    if (sync) {
      gl.flush();
      await this.waitSync(sync);
      gl.deleteSync(sync);
    }
    if (this.destroyed || gl.isContextLost()) return bins;

    const buf = new Float32Array(256 * 4 * 4);
    gl.bindBuffer(gl.PIXEL_PACK_BUFFER, this.histoPbo);
    gl.getBufferSubData(gl.PIXEL_PACK_BUFFER, 0, buf);
    gl.bindBuffer(gl.PIXEL_PACK_BUFFER, null);
    const rows = [bins.r, bins.g, bins.b, bins.l];
    for (let ch = 0; ch < 4; ch++) {
      const row = rows[ch];
      const base = ch * 256 * 4;
      for (let bin = 0; bin < 256; bin++) row[bin] = buf[base + bin * 4] >>> 0;
    }
    return bins;
  }

  /** Resolve once the given fence signals, polling without blocking the GPU. */
  private waitSync(sync: WebGLSync): Promise<void> {
    const gl = this.gl;
    return new Promise((resolve) => {
      const check = (): void => {
        if (this.destroyed || gl.isContextLost()) { resolve(); return; }
        const res = gl.clientWaitSync(sync, 0, 0);
        if (res === gl.ALREADY_SIGNALED || res === gl.CONDITION_SATISFIED || res === gl.WAIT_FAILED) {
          resolve();
          return;
        }
        setTimeout(check, 4);
      };
      check();
    });
  }

  /** (Re)create the RGBA8 FBO the histogram is binned from, capped to `longCap`. */
  private ensureHistoFbo(longCap: number, view?: HistogramView): { w: number; h: number } {
    const gl = this.gl;
    const baseW = view?.width ?? this.outWidth;
    const baseH = view?.height ?? this.outHeight;
    const long = Math.max(baseW, baseH) || 1;
    const scale = Math.min(1, longCap / long);
    const w = Math.max(1, Math.round(baseW * scale));
    const h = Math.max(1, Math.round(baseH * scale));
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
    this.release();
    // Release the context itself. Deleting objects frees their memory, but the
    // context slot stays occupied until GC — and browsers cap live WebGL
    // contexts (~16), evicting the oldest when a repeatedly-created offscreen
    // export renderer pushes past the cap. That eviction can hit the main
    // preview, which has no context-restore path.
    this.gl.getExtension("WEBGL_lose_context")?.loseContext();
  }

  // Free every GL object but keep the context alive. A canvas only ever gets
  // one WebGL context, so the persistent preview canvas must use this (not
  // destroy()) to be able to host a new PipelineRenderer later.
  release(): void {
    const gl = this.gl;
    this.destroyed = true;
    if (this.sourceTex) gl.deleteTexture(this.sourceTex);
    if (this.curveLutTex) gl.deleteTexture(this.curveLutTex);
    if (this.profileLutTex) gl.deleteTexture(this.profileLutTex);
    if (this.sepiaLutTex) gl.deleteTexture(this.sepiaLutTex);
    for (const slot of DCP_SLOTS) {
      if (this.dcpTableTex[slot]) gl.deleteTexture(this.dcpTableTex[slot]);
      this.dcpTableTex[slot] = null;
    }
    if (this.histoTex) gl.deleteTexture(this.histoTex);
    if (this.histoFbo) gl.deleteFramebuffer(this.histoFbo);
    if (this.histoBinTex) gl.deleteTexture(this.histoBinTex);
    if (this.histoBinFbo) gl.deleteFramebuffer(this.histoBinFbo);
    if (this.histoProgram) gl.deleteProgram(this.histoProgram);
    if (this.histoVao) gl.deleteVertexArray(this.histoVao);
    if (this.histoPbo) gl.deleteBuffer(this.histoPbo);
    if (this.maskTex) gl.deleteTexture(this.maskTex);
    if (this.maskProgDown) gl.deleteProgram(this.maskProgDown);
    if (this.maskProgBlur) gl.deleteProgram(this.maskProgBlur);
    if (this.quadVao0) gl.deleteVertexArray(this.quadVao0);
    this.releaseSonyPostTargets();
    for (const { prog } of this.sonyPostProgs.values()) gl.deleteProgram(prog);
    this.sonyPostProgs.clear();
    for (const buf of this.quadBuffers) gl.deleteBuffer(buf);
    this.quadBuffers = [];
    gl.deleteProgram(this.program);
    gl.deleteVertexArray(this.vao);
  }

  private setUniforms(p: EditParams): void {
    const gl = this.gl;
    const s = (n: string, v: number) => { const l = this.uniforms[n]; if (l) gl.uniform1f(l, v); };
    const i = (n: string, v: number) => { const l = this.uniforms[n]; if (l) gl.uniform1i(l, v); };
    const v2 = (n: string, x: number, y: number) => { const l = this.uniforms[n]; if (l) gl.uniform2f(l, x, y); };
    // White balance: relative temp/tint -> linear-ProPhoto Bradford adaptation
    // matrix (identity at 6500/0), recomputed only when the sliders moved.
    const wbLoc = this.uniforms["u_wbMatrix"];
    if (wbLoc) {
      if (p.temperature !== this.wbTemp || p.tint !== this.wbTint) {
        this.wbTemp = p.temperature;
        this.wbTint = p.tint;
        const wb = computeWbMatrix(p.temperature, p.tint);
        for (let r = 0; r < 3; r++) {
          for (let col = 0; col < 3; col++) this.wbMat[r * 3 + col] = wb[r][col];
        }
      }
      gl.uniformMatrix3fv(wbLoc, true, this.wbMat); // transpose: row-major in
    }
    i("u_displayGamut", p.displayGamut);
    // Gated on the table existing as well as the toggle, so a body with no
    // fitted match renders the same either way instead of branching on nothing.
    i("u_dcpMatchActive", p.cameraMatch !== 0 && this.dcpTableDims.match[0] > 0 ? 1 : 0);
    i("u_hasProfileCurve", this.hasProfileCurve ? 1 : 0);
    i("u_profileCurveSrgb", this.profileCurveSrgb ? 1 : 0);
    const chroma = this.profileChroma;
    i("u_sonyChromaActive", chroma ? 1 : 0);
    i("u_sepiaActive", this.sepiaActive && this.sepiaLutTex ? 1 : 0);
    i("u_droActive", this.droActive && this.droLutTex ? 1 : 0);
    v2("u_droScale", this.droScale[0], this.droScale[1]);
    const [gnx, gny, gbins] = this.droGridDims;
    i("u_droGridActive", this.droGridTex && gbins > 0 ? 1 : 0);
    if (gbins > 0) {
      gl.uniform3f(this.uniforms["u_droGridDims"]!, gnx, gny, gbins);
      const uv = this.droGridUV;
      gl.uniform3f(this.uniforms["u_droGridU"]!, uv[0]!, uv[1]!, uv[2]!);
      gl.uniform3f(this.uniforms["u_droGridV"]!, uv[3]!, uv[4]!, uv[5]!);
    }
    if (chroma) {
      const c = chroma.cross, g = chroma.gain;
      gl.uniform4f(this.uniforms["u_sonyCross"]!, c[0], c[1], c[2], c[3]);
      gl.uniform4f(this.uniforms["u_sonyGain"]!, g[0], g[1], g[2], g[3]);
      gl.uniform2f(this.uniforms["u_sonyLuma"]!, chroma.lumaPivot, chroma.lumaContrast);
      gl.uniform1f(this.uniforms["u_sonySat"]!, chroma.saturation);
      const w = chroma.sepia?.weights;
      if (w) gl.uniform3f(this.uniforms["u_sepiaWeights"]!, w[0]!, w[1]!, w[2]!);
    }
    i("u_curveActive", this.curveActive ? 1 : 0);
    s("u_exposure", p.exposure); s("u_highlights", p.highlights);
    s("u_shadows", p.shadows);
    s("u_vibrance", p.vibrance); s("u_saturation", p.saturation);
    s("u_clarity", p.clarity / 100);
    s("u_dehaze", p.dehaze / 100);
    // Activity flags let the shader skip its two costliest blocks (luma region/
    // contrast and the Oklab HSL mixer) when they are at their identity defaults.
    // Whites is display-referred on both halves now (baked into the curve LUT),
    // so it no longer engages the shader's tonal block at all.
    const tonalActive = p.highlights !== 0 || p.shadows !== 0;
    const hslActive = (p.hslH?.some((v) => v !== 0) ?? false)
      || (p.hslS?.some((v) => v !== 0) ?? false)
      || (p.hslL?.some((v) => v !== 0) ?? false);
    i("u_tonalActive", tonalActive ? 1 : 0);
    i("u_hslActive", hslActive ? 1 : 0);
    // The blurred log-luma mask is static per image; exposure reaches it as an
    // additive log2 shift (the shoulder is ignored, which only makes the mask
    // read slightly bright inside compressed highlights, softening a soft
    // weight). The WB matrix is luminance-normalized, so it contributes no
    // shift of its own.
    i("u_hasMask", this.maskTex ? 1 : 0);
    s("u_maskShift", p.exposure - LOG2_MID);
    // HSL
    for (let band = 0; band < 8; band++) {
      s(`u_hsl_h[${band}]`, p.hslH?.[band] ?? 0);
      s(`u_hsl_s[${band}]`, p.hslS?.[band] ?? 0);
      s(`u_hsl_l[${band}]`, p.hslL?.[band] ?? 0);
    }
    // Color Grading
    const v3 = (n: string, v: readonly [number, number, number]) => {
      const l = this.uniforms[n]; if (l) gl.uniform3f(l, v[0], v[1], v[2]);
    };
    v3("u_grad_sh_tint", p.gradShTint ?? [1, 1, 1]);
    v3("u_grad_md_tint", p.gradMdTint ?? [1, 1, 1]);
    v3("u_grad_hl_tint", p.gradHlTint ?? [1, 1, 1]);
    s("u_grad_blend", p.gradBlend ?? 0); s("u_grad_balance", p.gradBalance ?? 0);
    // Lens corrections: skip the per-knot uploads entirely at identity — the
    // shader never reads the tables when u_lensActive is 0.
    const lensActive = (p.lensDist?.some((v) => v !== 1) ?? false)
      || (p.lensVig?.some((v) => v !== 1) ?? false);
    i("u_lensActive", lensActive ? 1 : 0);
    if (lensActive) {
      for (let k = 0; k < LENS_KNOTS; k++) {
        s(`u_lensDist[${k}]`, p.lensDist?.[k] ?? 1);
        s(`u_lensVig[${k}]`, p.lensVig?.[k] ?? 1);
      }
      const diag = Math.hypot(this.texWidth, this.texHeight) || 1;
      // The short edge's midpoint radius — the constraint that binds for barrel
      // correction, and the reason the fill scale is computed here rather than
      // handed in: it is a property of this texture's aspect ratio.
      const shortEdge = Math.min(this.texWidth, this.texHeight) / diag;
      s("u_lensScale", lensFillScale(p.lensDist ?? LENS_IDENTITY, shortEdge));
      const normLoc = this.uniforms["u_lensNorm"];
      if (normLoc) {
        gl.uniform2f(normLoc, (2 * this.texWidth) / diag, (2 * this.texHeight) / diag);
      }
    }
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

  /**
   * The full-screen quad for the offscreen passes, all of which share
   * MASK_VERTEX_SHADER and so bind a_position at location 0. Memoized rather
   * than built per subsystem: the mask and Clarity chains would otherwise hold
   * byte-identical VAOs, each with its own buffer to track and free.
   */
  private unitQuadVao(): WebGLVertexArrayObject {
    if (!this.quadVao0) this.quadVao0 = this.makeQuadVao(0);
    return this.quadVao0;
  }

  /**
   * A texture + framebuffer to render into, or null if the driver will not take
   * it. Every offscreen pass in this file wants the same thing (LINEAR,
   * clamped, no mips) and differs only in size and format.
   */
  private makeRenderTarget(
    w: number, h: number, internalFormat: number, format: number, type: number,
  ): RenderTarget | null {
    const gl = this.gl;
    const tex = gl.createTexture()!;
    gl.bindTexture(gl.TEXTURE_2D, tex);
    // internalFormat and type have to agree: RGBA8 takes UNSIGNED_BYTE, the
    // float formats take FLOAT. Passing FLOAT for an 8-bit target is an
    // INVALID_OPERATION, not a silent conversion.
    gl.texImage2D(gl.TEXTURE_2D, 0, internalFormat, w, h, 0, format, type, null);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    const fbo = gl.createFramebuffer()!;
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
    if (gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE) {
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      gl.deleteTexture(tex);
      gl.deleteFramebuffer(fbo);
      return null;
    }
    return { tex, fbo };
  }

  /**
   * One offscreen pass: draw `src` through `prog` into `dst`, with the source
   * on unit 0. `setExtra` receives the program so the caller can set whatever
   * else it needs; the caller is expected to have set the viewport already.
   */
  private blitQuad(
    prog: WebGLProgram, src: WebGLTexture, dst: WebGLFramebuffer | null,
    setExtra?: (prog: WebGLProgram) => void,
  ): void {
    const gl = this.gl;
    gl.bindFramebuffer(gl.FRAMEBUFFER, dst);
    gl.useProgram(prog);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, src);
    setExtra?.(prog);
    gl.drawArrays(gl.TRIANGLES, 0, 6);
  }

  private makeQuadVao(attribLoc: number): WebGLVertexArrayObject {
    const gl = this.gl;
    const vao = gl.createVertexArray()!;
    gl.bindVertexArray(vao);
    const buf = gl.createBuffer()!;
    this.quadBuffers.push(buf); // deleting a VAO doesn't cascade to its buffers
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]), gl.STATIC_DRAW);
    gl.enableVertexAttribArray(attribLoc);
    gl.vertexAttribPointer(attribLoc, 2, gl.FLOAT, false, 0, 0);
    return vao;
  }
}
