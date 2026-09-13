/**
 * Single-pass WebGL2 shader for RAW image editing.
 *
 * All per-pixel operations (WB, exposure, tonal regions, vib/sat, gamma) are
 * fused into one shader to eliminate FBO ping-pong overhead. Contrast and
 * Blacks are display-referred and baked into the tone-curve LUT (curve.ts),
 * not applied here.
 */

import { COLOR_GLSL, PROPHOTO_Y, REC709_Y, glslFloat } from "./color-spaces";
import { LENS_KNOTS, LENS_KNOT_SPAN } from "./lens";
import {
  CAMERA_MATCH_C_PITCH, CAMERA_MATCH_C_STEPS,
  CAMERA_MATCH_H_ORIGIN, CAMERA_MATCH_H_PITCH, CAMERA_MATCH_H_SECTORS,
  CAMERA_MATCH_L_PITCH, CAMERA_MATCH_L_STEPS,
  LAB_WHITE, SRGB_TO_XYZ, XYZ_TO_SRGB,
} from "./camera-match";
import { LUT_GLSL } from "./curve";
import { HSL_GLSL } from "./hsl-bands";
import { TONAL_GLSL } from "./tonal-model";
import { MASK_GLSL } from "./masks";

// Color Grading region edges, on display luma. Balance slides both pairs by up
// to ±GRAD_BAL_SPAN. That span is capped at 0.15 because the graded colour is
// clamped to [0,1] before the block, so its luma is too: shift an edge pair any
// further and it leaves the axis entirely, making that wheel a silent no-op —
// silent because the Midtones weight (1-shW)(1-hlW) then expands over the
// vacated range and the user still sees a tint, from the wrong wheel.
export const GRAD_SH_EDGE0 = 0.15;
export const GRAD_SH_EDGE1 = 0.45;
export const GRAD_HL_EDGE0 = 0.55;
export const GRAD_HL_EDGE1 = 0.85;
export const GRAD_BAL_SPAN = Math.min(GRAD_SH_EDGE0, 1 - GRAD_HL_EDGE1);
// Ceiling on the luminance-renorm gain (lg/lt) after the tint multiply. The
// orange-yellow warm axis tops out around x2.7 and never hits it; saturated
// cool tints would otherwise demand x14+ (sRGB blue's display luma is 0.0722)
// and clip straight through the gamut map. Past the cap the wheel trades luma
// for chroma; deep primaries (red/magenta) brush it only above ~S=75.
export const GRAD_RENORM_CAP = 4;

export const VERTEX_SHADER = `#version 300 es
precision highp float;
in vec2 a_position;
out vec3 v_texCoordH;
// Homography output-quad -> source texcoords (crop / straighten / flip / rotate
// / perspective). Identity for an un-cropped frame; built on the CPU in
// crop.ts. Kept homogeneous here: the projective divide has to happen per
// fragment, and interpolating the homogeneous vector then dividing is exact.
uniform mat3 u_texXform;
void main() {
  vec2 uv = a_position * 0.5 + 0.5;
  vec2 p = vec2(uv.x, 1.0 - uv.y);          // output-frame coord, y-down
  v_texCoordH = u_texXform * vec3(p, 1.0);
  gl_Position = vec4(a_position, 0.0, 1.0);
}`;

export const PROCESS_SHADER = `#version 300 es
precision highp float;
// sampler3D has no default precision in GLSL ES 3.0 (unlike sampler2D), so
// omitting this is a compile error, not a silent downgrade.
precision highp sampler3D;
precision highp isampler3D;
// int defaults to *mediump* in a fragment shader, which the spec only
// guarantees to 16 bits. Sony's 3-D LUT reproduces the engine's integer
// trilinear weights and reaches 2^28 doing it, so it needs the full 32.
precision highp int;
in vec3 v_texCoordH;
out vec4 outColor;
uniform sampler2D u_input;
uniform mat3 u_wbMatrix;        // relative WB: Bradford adaptation in linear ProPhoto
uniform float u_exposure;
uniform int u_sonyLinearExposure;   // 1: Edit's plain 2^EV gain (Sony), 0: shouldered exposure
uniform float u_highlights;
uniform float u_shadows;
uniform float u_vibrance;
uniform float u_saturation;
uniform float u_clarity;
uniform float u_dehaze;
uniform int u_tonalActive;      // 1 if highlights or shadows is non-zero
// Blurred log2 source luminance (see MASK_* shaders below): drives the
// Highlights/Shadows region weights so a pixel moves with its *neighborhood*
// — local contrast survives, as in Lightroom. Built once per uploaded image;
// per-frame WB/exposure enter additively via u_maskShift (log2 space).
uniform sampler2D u_mask_lum;
uniform float u_maskShift;      // log2(luma(wbGain)) + exposure - log2(0.18)
uniform int u_hasMask;          // 0 -> fall back to per-pixel weights
uniform int u_hslActive;        // 1 if any HSL band adjustment is non-zero
// HSL Color Mixer (8 ranges × 3 adjustments)
uniform float u_hsl_h[8];
uniform float u_hsl_s[8];
uniform float u_hsl_l[8];
// Color Grading. Wheel tints arrive as linear-ProPhoto multipliers, built on
// the CPU in grading.ts from the display-referred wheel colour (identity =
// vec3(1) at S=0). Routing them through sRGB there is what keeps the luminance
// renorm below sane — see grading.ts.
uniform vec3 u_grad_sh_tint;
uniform vec3 u_grad_md_tint;
uniform vec3 u_grad_hl_tint;
uniform float u_grad_blend;
uniform float u_grad_balance;
// Tone Curve LUT (LUT_SIZE×1 RGBA texture) — per-channel point + parametric
// curves, applied display-referred. .r/.g/.b hold the baked R/G/B channel curves.
uniform sampler2D u_curve_lut;
uniform int u_curveActive;      // 0 when the baked LUT is the identity -> skip its 5 fetches
uniform sampler2D u_profile_lut; // camera profile tone curve (per-channel), display rendering
uniform int u_hasProfileCurve;   // 1 if a profile tone curve is available
uniform int u_profileCurveSrgb;  // 1 if that curve is defined on sRGB/Rec.709 primaries, not ProPhoto
// Sony RGB2YCC (sonyChroma below): four cross terms and four gains, both
// indexed by sign. Zero gains are how Black & White desaturates.
uniform int u_sonyChromaActive;
uniform vec4 u_sonyCross;
uniform vec4 u_sonyGain;
// YGamma, which runs between the two chroma halves: a table lookup, then the
// shot's Fade setting as a pivot and a contrast. Fade 0 is pivot 0, so the
// second half degenerates to a gain.
uniform vec2 u_sonyLuma;        // (pivot, contrast)
// The table, 16384 entries folded into a 128x128 R32F texture, values on the
// engine's own 0..16383 scale. Standard and Neutral carry a highlight knee here
// (slope 0.90625 above Y=8192); the other eight looks are near identity, which
// is why the stage passed for a plain gain until a Standard frame's highlights
// came out 2-3% bright and clipped (worker sony/chroma.py). Read with
// texelFetch and never with texture(): the engine's index is a truncated
// integer, and filtering across the knee would round off the one feature the
// table is carried for.
uniform int u_sonyLumaLutActive;
uniform sampler2D u_sonyLumaLut;
// The same table under 色彩复制 = 高级, and the contrast that goes with it.
// u_sonyLut3dActive drives these too: 高级 is one setting in Imaging Edge and it
// moves both halves of YGamma as well as adding the 3-D LUT below. The advanced
// contrast is 17280/16384 for *both* families — including the eight looks whose
// own entry is 1.0 — and the pivot is not swapped (worker sony/chroma.py
// luma_terms says why). Same 128x128 fold, same texelFetch rules as above.
uniform int u_sonyLumaLutAdvActive;
uniform int u_sonyLumaAdvForce;
uniform sampler2D u_sonyLumaLutAdv;
uniform float u_sonyLumaAdvContrast;
// ChromaSuppres, which the engine runs just *before* YGamma and indexes by the
// luma from before it: a flat 255/256 through the mid-tones, then a linear fade
// to zero above hiY. Engine units — hiY/loY on its 0..16383 luma scale, the
// slopes over 4096 (worker sony/chromasuppres.py).
uniform int u_sonyCSActive;
uniform vec4 u_sonyCS;          // (hiY, loY, slopeHi, slopeLo)
// The Saturation slider. u_sonyGain arrives already divided by it; this
// multiplies the chroma back after the clamp, exactly as the engine's separate
// ZcTaskSIMDHueSaturation stage does. The two nearly cancel — the clamp in
// between is the whole visible effect.
uniform float u_sonySat;
// Edit's 色相 slider, in radians: ZcTaskHueSaturation turns (Cb, Cr) by it,
// right after the saturation multiply, and snaps the result's angle to its
// 512-entry sine table (worker sony/chroma.py rotate_chroma). 0 is off.
uniform float u_sonyHue;
// Edit's 黑色/白色 sliders as YGamma applies them, between the table and the
// pivot line: y = (y - black) * scale (worker sony/chroma.py luma_levels).
uniform vec2 u_sonyLevels;      // (black, scale); (0, 1) is the identity
// Sony's ZcTask3DLut — Edit's 色彩复制 = 高级 ("advanced colour reproduction"),
// the one stage of this section the user chooses. Edit's own default is 标准,
// which is the stage absent, so this is off unless the switch is on. The table
// is static — one dump reproduces two bodies, two frames and all eleven
// Creative Looks bit-exactly — so it ships as an asset (public/sony-lut3d.bin)
// rather than arriving per file: 33x33x33 int16 triples, uploaded as an RGB16I
// 3-D texture and read with texelFetch, never filtered (worker sony/lut3d.py).
uniform int u_sonyLut3dActive;
uniform isampler3D u_sony_lut3d;
// Sepia's toning (the engine's ZcTaskEffect, which runs only for that look):
// throw the chroma away and map one weighted sum through a curve per channel.
uniform int u_sepiaActive;
uniform vec3 u_sepiaWeights;
uniform sampler2D u_sepia_lut;
// Sony's DRO (ZcTaskVatr): one gain per pixel against its log luminance, from
// the curve the camera wrote into this shot's RAW. It runs before everything
// else here because that is where the engine runs it — right after demosaic,
// ahead of the colour matrix. Applying it after the matrix instead is the same
// arithmetic: a scalar gain commutes with a linear transform, which is what
// lets the strength move without re-decoding (worker sony/dro.py).
uniform int u_droActive;
uniform sampler2D u_dro_lut;
uniform vec2 u_droScale;        // (log ceiling, luma at normalised white)
// The bilateral grid. DRO indexes its curve by the *local* log mean, not by the
// pixel — that is the whole of what makes it local — and the grid is where that
// mean comes from. Laid out (nx*bins) x ny with num in R and den in G; the two
// are interpolated separately and divided only at the end, because Mlog is not
// the interpolation of num/den. Without a grid the pixel's own log luminance
// stands in, which is the same curve applied globally.
uniform int u_droGridActive;
uniform sampler2D u_dro_grid;
uniform vec3 u_droGridDims;     // (nx, ny, bins)
uniform vec3 u_droGridU;        // cell u = x.x + x.y*imageX + x.z*imageY
uniform vec3 u_droGridV;
// --- DCP HueSatMaps (Adobe's ProfileHueSatMap / ProfileLookTable, plus the
// fitted camera match) --- Each is a 3D LUT of (hue shift in turns, saturation
// scale, value scale), laid out sat × hue × val, so the hardware's trilinear
// fetch *is* the interpolation dcp.py's sample_hsv_table did by hand. They ran
// in the worker until it became clear what that costs: thirty-odd whole-array
// numpy passes each, 25 s per table at 33 MP, versus one texture fetch here.
// Dims carry (hueCount, satCount, valCount, 1 if the table is sRGB-encoded);
// hueCount 0 means "no such table", and the sampler still has an identity LUT
// bound so the fetch is always legal.
uniform sampler3D u_dcp_hsm;
uniform sampler3D u_dcp_look;
uniform sampler3D u_dcp_match;
uniform vec4 u_dcpHsmDims;
uniform vec4 u_dcpLookDims;
uniform vec4 u_dcpMatchDims;
uniform int u_dcpMatchActive;   // the Camera Match toggle, now a uniform
uniform int u_displayGamut;     // 0 = sRGB, 1 = Display-P3
uniform vec3 u_bgColor;         // display-encoded fill for areas outside the image (crop editor)
// Lens corrections (per-shot radial tables from the RAW's metadata; lens.ts).
// Both tables sit on the canonical knot grid in radius normalised to the source
// half-diagonal. u_lensDist is the sampling factor toward the recorded frame
// (corrected r fetches r*f), u_lensVig the linear-light gain at the recorded
// radius. u_lensScale is the fill scale (lens.ts lensFillScale) — it goes below
// 1 for pincushion and above 1 for barrel, so it is not a crop-in either way.
// u_lensNorm = 2*(w,h)/diagonal so the frame corner lands at radius 1.
uniform int u_lensActive;
uniform float u_lensDist[${LENS_KNOTS}];
uniform float u_lensVig[${LENS_KNOTS}];
uniform float u_lensScale;
uniform vec2 u_lensNorm;
// Masks (masks.ts, docs/masking.md). The per-group data is the Masks uniform
// block declared in MASK_GLSL; these say how many groups are packed, which
// blocks any of them touch (MASK_USE_* bits, OR'd into the skip gates), and
// which packed group the red overlay shows (-1 = off). u_imgFromTex takes
// v_texCoord to oriented image-norm — the space the gradients are stored in,
// which is what makes them follow crop / rotate / flip — and u_imgAspect is
// ih/iw so distances in that space can be made isotropic.
uniform int u_maskGroups;
uniform int u_maskUse;
uniform int u_maskPreview;
uniform mat3 u_imgFromTex;
uniform float u_imgAspect;

${COLOR_GLSL}
${TONAL_GLSL}
${HSL_GLSL}
${LUT_GLSL}
${MASK_GLSL}

// ===== View transforms: scene-linear ProPhoto -> display-linear ProPhoto [0,1] =====

// The 3-D LUT's 1-D chroma warp, by its closed form. The engine holds
// int16[65537] here, indexed by a *signed* value, and
// sign(i) * min(trunc(32768 * (|i|/32768)^(2/3)), 32767) reproduces every one
// of those 65537 entries (worker sony/lut3d.py checks the formula against the
// dump). So it costs one pow() instead of a second texture. Evaluated in
// float32 rather than the worker's double, which moves the result by at most
// one of 65536 on 78 of the entries — a 1/2048 shift of one cell's fraction.
float sonyWarpCurve(float x) {
  return sign(x) * min(trunc(32768.0 * pow(abs(x) / 32768.0, 2.0 / 3.0)), 32767.0);
}

// One grid point of the 3-D LUT. public/sony-lut3d.bin is int16 triples in
// [iu][iv][iy] order with iy fastest (sony_repro/tools/make_lut3d_asset.py),
// uploaded with width = the Y axis, height = v, depth = u — so the fetch
// coordinate is (iy, iv, iu), and reversing it is a silent colour error rather
// than a crash.
ivec3 sonyLut3dPoint(int iu, int iv, int iy) {
  return texelFetch(u_sony_lut3d, ivec3(iy, iv, iu), 0).rgb;
}

// Sony's RGB2YCC — the stage that carries a Creative Look's saturation and hue.
// Runs on display-*encoded* values in the curve's own basis, which is exactly
// where the engine runs it (worker sony/chroma.py). The chroma differences are
// green differences, not luma ones; both the cross terms and the gains branch on
// sign, which is what makes the gain hue-dependent and adds the rotation.
// The return trip is plain BT.601 — all of the styling is in the forward half.
vec3 sonyChroma(vec3 s) {
  vec3 e = srgbEncode(clamp(s, 0.0, 1.0));
  float y = dot(e, vec3(2432.0, 4864.0, 896.0) / 8192.0);
  float u = e.r - e.g;
  float v = e.b - e.g;
  // Both cross terms read the *unmodified* u and v, and each looks at the
  // other's sign.
  float v2 = (u >= 0.0 ? u_sonyCross.y : u_sonyCross.w) * u + v;
  float u2 = (v >= 0.0 ? u_sonyCross.x : u_sonyCross.z) * v + u;
  float cr = clamp((u2 >= 0.0 ? u_sonyGain.y : u_sonyGain.w) * u2, -0.5, 0.5) * u_sonySat;
  float cb = clamp((v2 >= 0.0 ? u_sonyGain.x : u_sonyGain.z) * v2, -0.5, 0.5) * u_sonySat;
  // 色相: the chroma vector turned, its angle then snapped to steps of
  // 2*pi/512 — the engine reads its sine table at round(ang * 256 / pi).
  if (u_sonyHue != 0.0) {
    float mag = length(vec2(cb, cr));
    float ang = atan(cb, cr) + u_sonyHue;
    ang = floor(ang * (256.0 / 3.14159265358979) + 0.5) * (3.14159265358979 / 256.0);
    cb = mag * sin(ang);
    cr = mag * cos(ang);
  }
  // ChromaSuppres, which the engine runs between RGB2YCC and YGamma, on the
  // luma *before* YGamma (worker sony/chromasuppres.py has the derivation and
  // the measurement). f is 255 for the mid-tones -- a 255/256 chroma loss, not
  // identity -- and falls off linearly above hiY (and below loY, unused so far).
  if (u_sonyCSActive == 1) {
    float y16 = y * 16383.0;
    float f = 255.0;
    if (y16 > u_sonyCS.x) f = 255.0 - floor((y16 - u_sonyCS.x) * u_sonyCS.z / 4096.0);
    else if (y16 < u_sonyCS.y) f = 255.0 - floor((u_sonyCS.y - y16) * u_sonyCS.w / 4096.0);
    f = clamp(f, 0.0, 255.0) / 256.0;
    cr *= f;
    cb *= f;
  }
  // YGamma, which the engine runs here, between the two halves: it maps Y
  // through the look's table, pulls it toward a pivot and clips, and leaves
  // both chroma planes bit-identical (worker sony/chroma.py). The table is the
  // look's highlight shape; the pivot and contrast are the in-camera Fade
  // setting. ChromaSuppres above deliberately ran on the luma from before both.
  //
  // Under 高级 the whole stage runs off the other pair — table and contrast
  // both — so the switch below drives this as well as the 3-D LUT. Gated on the
  // advanced table having arrived: an older response carries only the standard
  // one, and rendering 高级's contrast against 标准's table would be neither.
  // Camera match asks for the advanced luma pair on its own (u_sonyLumaAdvForce):
  // it is the highlight roll-off the camera has and the standard table lacks,
  // without the 3-D LUT that pulls saturated colour down (pipeline-renderer).
  bool adv = u_sonyLumaLutAdvActive == 1 && (u_sonyLut3dActive == 1 || u_sonyLumaAdvForce == 1);
  int idx = int(clamp(floor(y * 16383.0), 0.0, 16383.0));
  if (adv) {
    y = texelFetch(u_sonyLumaLutAdv, ivec2(idx & 127, idx >> 7), 0).r / 16383.0;
  } else if (u_sonyLumaLutActive == 1) {
    y = texelFetch(u_sonyLumaLut, ivec2(idx & 127, idx >> 7), 0).r / 16383.0;
  }
  // 黑色/白色, between the table and the pivot line.
  y = (y - u_sonyLevels.x) * u_sonyLevels.y;
  float lumaContrast = adv ? u_sonyLumaAdvContrast : u_sonyLuma.y;
  y = clamp((y - u_sonyLuma.x) * lumaContrast + u_sonyLuma.x, 0.0, 1.0);
  // ZcTask3DLut, which the engine runs here — after YGamma, before the return
  // trip — and only when the user asks for it. It works on the engine's integer
  // planes, so they are reconstructed and converted back: 1.0 is 16383 (the
  // scale YGamma's own table is on), Y truncated as the engine truncates it,
  // and the two chroma planes rounded onto that same scale offset by 0x8000.
  // worker sony/lut3d.py apply_lut3d_float owns that pair and says where it was
  // measured (sony_repro/tools/ycc_exact.py, within 1 of 16383).
  if (u_sonyLut3dActive == 1) {
    float crPlane = floor(cr * 16383.0 + 0.5);
    float cbPlane = floor(cb * 16383.0 + 0.5);
    int yPlane = int(floor(y * 16383.0));
    // The forward 2x2: BT.601-ish colour differences u ~ R-Y and v ~ B-Y. The
    // tiny off-diagonal terms are in Edit.exe and are reproduced verbatim.
    float uw = sonyWarpCurve(trunc(clamp(cbPlane * 3.7e-05 + crPlane * 1.401988, -32768.0, 32767.0)));
    float vw = sonyWarpCurve(trunc(clamp(crPlane * 0.000135 + cbPlane * 1.771978, -32768.0, 32767.0)));
    // 5-bit grid coordinate + 11-bit fraction on each chroma axis, 5 + 9 on Y.
    int a = int(uw) + 32768;
    int b = int(vw) + 32768;
    int iu = a >> 11, f0 = a & 2047;
    int iv = b >> 11, f1 = b & 2047;
    int iy = yPlane >> 9, yf = yPlane & 511;
    int F0 = 2048 - f0, F1 = 2048 - f1, yF = 512 - yf;
    // The eight trilinear weights in the engine's own integer form, not a float
    // lerp. That is not pedantry: the weights are quantised to 512ths and w0 is
    // the *remainder* of the other seven, so all of the rounding lands on one
    // corner. A plain float trilinear drifts up to 11 of 16383 on saturated
    // pixels against the worker's bit-exact model; this form leaves only the
    // float32 curve/matrix, which is under 2. The >> 3 is after the multiply
    // and really does discard those bits — cancelling it algebraically changes
    // the answer.
    int w2 = (((f0 * yf) >> 3) * F1 + 0x40000) >> 19;
    int w6 = (((f0 * yf) >> 3) * f1 + 0x40000) >> 19;
    int w3 = (((f0 * yF) >> 3) * F1 + 0x40000) >> 19;
    int w7 = (((f0 * yF) >> 3) * f1 + 0x40000) >> 19;
    int w1 = (((F0 * yf) >> 3) * F1 + 0x40000) >> 19;
    int w5 = (((F0 * yf) >> 3) * f1 + 0x40000) >> 19;
    int w4 = (((F0 * yF) >> 3) * f1 + 0x40000) >> 19;
    int w0 = 512 - w7 - w6 - w5 - w4 - w3 - w2 - w1;
    // Corner order inside a cell is gray-coded on (f1, f0, yf):
    //   k: 0=000 1=001 2=011 3=010 4=100 5=101 6=111 7=110
    ivec3 acc = sonyLut3dPoint(iu,     iv,     iy    ) * w0
              + sonyLut3dPoint(iu,     iv,     iy + 1) * w1
              + sonyLut3dPoint(iu + 1, iv,     iy + 1) * w2
              + sonyLut3dPoint(iu + 1, iv,     iy    ) * w3
              + sonyLut3dPoint(iu,     iv + 1, iy    ) * w4
              + sonyLut3dPoint(iu,     iv + 1, iy + 1) * w5
              + sonyLut3dPoint(iu + 1, iv + 1, iy + 1) * w6
              + sonyLut3dPoint(iu + 1, iv + 1, iy    ) * w7;
    acc >>= 9;                       // the eight weights always sum to 512
    // Y has a lower clamp and no upper one (the engine's own test/cmovs); the
    // clamp below stands in for the upper end, as YCC2RGB's does in the engine.
    // The chroma goes back through the inverse of the same 2x2 — 0.564341 is
    // 1/1.771978 and 0.713273 is 1/1.401988 — skipping only the engine's
    // truncation to integer planes, which is worth under one of 16383.
    y = float(max(acc.x, 0)) / 16383.0;
    cr = (float(acc.y) * 0.713273 - float(acc.z) * 1.5e-05) / 16383.0;
    cb = (float(acc.z) * 0.564341 - float(acc.y) * 5.4e-05) / 16383.0;
  }
  vec3 o = clamp(vec3(y + 1.4020 * cr, y - 0.7141 * cr - 0.3441 * cb, y + 1.7720 * cb), 0.0, 1.0);
  // Sepia's toning goes here, on the encoded values the engine's own stage sees.
  if (u_sepiaActive == 1) {
    float t = lutCoord(clamp(dot(o, u_sepiaWeights), 0.0, 1.0));
    o = texture(u_sepia_lut, vec2(t, 0.5)).rgb;
  }
  return srgbDecode(o);
}

// The view transform: scene-referred light -> display-referred, hue-stable.
vec3 viewTransform(vec3 c) {
  c = max(c, 0.0);
  if (u_hasProfileCurve == 1) {
    // The profile tone curve IS the camera's display rendering — apply it per
    // channel (as Adobe/ACR do). This matches the camera/"official" look closely.
    //
    // Which primaries it runs on is part of the profile, not a preference: a
    // DCP's curve is defined in the working space, while Sony's MainGamma runs
    // on the body's own near-Rec.709 primaries (worker sony/profile.py). A
    // per-channel curve is basis-dependent — applying it in the wrong one skews
    // hue — so rotate into the curve's basis and back out.
    vec3 s = (u_profileCurveSrgb == 1) ? PROPHOTO_TO_SRGB * c : c;
    s = vec3(
      texture(u_profile_lut, vec2(lutCoord(clamp(s.r, 0.0, 1.0)), 0.5)).r,
      texture(u_profile_lut, vec2(lutCoord(clamp(s.g, 0.0, 1.0)), 0.5)).r,
      texture(u_profile_lut, vec2(lutCoord(clamp(s.b, 0.0, 1.0)), 0.5)).r
    );
    if (u_sonyChromaActive == 1) s = sonyChroma(s);
    return (u_profileCurveSrgb == 1) ? SRGB_TO_PROPHOTO * s : s;
  }
  // Fallback (no profile curve): identity here; the display sRGB encode supplies the
  // gamma so mid gray (0.18) lands at ~0.46 and white reaches white.
  return c;
}

// --- DCP HueSatMap application (the GPU half of dcp.py apply_hsv_table_chunk) ---
//
// HSV here is Adobe's, not the graphics-standard one: hue in turns, saturation
// delta/max, value max — and the table's three samples are a hue *shift*, a
// saturation *scale* and a value *scale*. The tie-breaking order below (red,
// then green, then blue) mirrors np.argmax, which returns the first maximum.

vec3 dcpRgbToHsv(vec3 c) {
  float mx = max(max(c.r, c.g), c.b);
  float mn = min(min(c.r, c.g), c.b);
  float d = mx - mn;
  float h = 0.0;
  if (d > 1e-8) {
    if (c.r >= c.g && c.r >= c.b) h = mod((c.g - c.b) / d, 6.0);
    else if (c.g >= c.b)          h = (c.b - c.r) / d + 2.0;
    else                          h = (c.r - c.g) / d + 4.0;
  }
  return vec3(fract(h / 6.0), mx > 1e-8 ? d / mx : 0.0, mx);
}

vec3 dcpHsvToRgb(vec3 hsv) {
  float h = fract(hsv.x) * 6.0;
  float s = clamp(hsv.y, 0.0, 1.0);
  float v = clamp(hsv.z, 0.0, 1.0);
  float f = fract(h);
  float p = v * (1.0 - s);
  float q = v * (1.0 - s * f);
  float t = v * (1.0 - s * (1.0 - f));
  int sector = int(floor(h)) % 6;
  if (sector == 0) return vec3(v, t, p);
  if (sector == 1) return vec3(q, v, p);
  if (sector == 2) return vec3(p, v, t);
  if (sector == 3) return vec3(p, q, v);
  if (sector == 4) return vec3(t, p, v);
  return vec3(v, p, q);
}

vec3 dcpSrgbEncode(vec3 c) {
  c = clamp(c, 0.0, 1.0);
  return mix(1.055 * pow(c, vec3(1.0 / 2.4)) - 0.055, c * 12.92, lessThanEqual(c, vec3(0.0031308)));
}

vec3 dcpSrgbDecode(vec3 c) {
  c = clamp(c, 0.0, 1.0);
  return mix(pow((c + 0.055) / 1.055, vec3(2.4)), c / 12.92, lessThanEqual(c, vec3(0.04045)));
}

// The two axis conventions are the table's own and differ, so they cannot share
// a mapping: hue is periodic with the grid point at i/hueCount (GL_REPEAT on
// that axis closes the loop), while saturation and value are endpoint-aligned
// at i/(count-1) and clamp.
vec3 applyHsvTable(vec3 c, sampler3D tbl, vec4 dims) {
  if (dims.x < 0.5) return c;
  vec3 nonneg = max(c, 0.0);
  // Scene-linear carries highlight headroom above 1.0, but the table is defined
  // on [0,1]: look it up clamped, then put the headroom back, so highlights are
  // not crushed to white. Below 1.0 the scale is exactly 1.
  float valueIn = max(max(nonneg.r, nonneg.g), nonneg.b);
  vec3 working = clamp(nonneg, 0.0, 1.0);
  if (dims.w > 0.5) working = dcpSrgbEncode(working);

  vec3 hsv = dcpRgbToHsv(working);
  vec3 uvw = vec3(
    (clamp(hsv.y, 0.0, 1.0) * (dims.y - 1.0) + 0.5) / dims.y,
    (fract(hsv.x) * dims.x + 0.5) / dims.x,
    dims.z > 1.5 ? (clamp(hsv.z, 0.0, 1.0) * (dims.z - 1.0) + 0.5) / dims.z : 0.5);
  vec3 d = texture(tbl, uvw).rgb;

  hsv.x = fract(hsv.x + d.r);            // already turns, scaled worker-side
  hsv.y = clamp(hsv.y * d.g, 0.0, 1.0);
  hsv.z = clamp(hsv.z * d.b, 0.0, 1.0);

  vec3 mapped = dcpHsvToRgb(hsv);
  if (dims.w > 0.5) mapped = dcpSrgbDecode(mapped);
  return mapped * max(valueIn, 1.0);
}

// Exposure shoulder / tonal-region constants + expoShoulder come from
// TONAL_GLSL above — generated from tonal-model.ts, the tested TS mirror.

// Gamut compression: bring out-of-gamut display-linear RGB back inside [0,1]^3
// by desaturating toward the equal-luminance gray. Preserves luminance and keeps
// hue far more stable than a per-channel clamp. Yw must be the luminance
// weights of the gamut that c is expressed in.
vec3 gamutMap(vec3 c, vec3 Yw) {
  float lo = min(min(c.r, c.g), c.b);
  float hi = max(max(c.r, c.g), c.b);
  if (lo >= 0.0 && hi <= 1.0) return c;
  float l = clamp(dot(c, Yw), 0.0, 1.0);
  float s = 1.0;
  if (lo < 0.0) s = min(s, (0.0 - l) / (lo - l));
  if (hi > 1.0) s = min(s, (1.0 - l) / (hi - l));
  s = clamp(s, 0.0, 1.0);
  return clamp(mix(vec3(l), c, s), 0.0, 1.0);
}

// --- Color Grading constants ---

const float GRAD_SH_EDGE0 = ${glslFloat(GRAD_SH_EDGE0)};
const float GRAD_SH_EDGE1 = ${glslFloat(GRAD_SH_EDGE1)};
const float GRAD_HL_EDGE0 = ${glslFloat(GRAD_HL_EDGE0)};
const float GRAD_HL_EDGE1 = ${glslFloat(GRAD_HL_EDGE1)};
const float GRAD_BAL_SPAN = ${glslFloat(GRAD_BAL_SPAN)};
const float GRAD_RENORM_CAP = ${glslFloat(GRAD_RENORM_CAP)};

// Evaluate a lens table at normalised radius r. Knots at i / SPAN, the last one
// short of the corner; past it the last segment continues for one more knot
// (mix() with t - i > 1 extrapolates), then holds. lens.ts lensInterp is the
// tested TS mirror of this function, and N / SPAN are injected from there so
// the two cannot drift.
float lensInterp(float table[${LENS_KNOTS}], float r) {
  float t = clamp(r * ${glslFloat(LENS_KNOT_SPAN)}, 0.0, ${glslFloat(LENS_KNOTS)});
  int i = int(min(t, ${glslFloat(LENS_KNOTS - 2)}));
  return mix(table[i], table[i + 1], t - float(i));
}

/**
 * DRO's local log-luminance mean, trilinear over the bilateral grid.
 *
 * num and den are fetched and interpolated separately and divided only at the
 * end: the engine does that, and it is not the same as interpolating the ratio.
 * Nearest fetches with the weights done by hand, since float-linear filtering
 * would interpolate the ratio for us and get the wrong answer.
 */
float droLocalMean(vec2 uv, float ylog) {
  float nx = u_droGridDims.x, ny = u_droGridDims.y, bins = u_droGridDims.z;
  float u = clamp(u_droGridU.x + u_droGridU.y * uv.x + u_droGridU.z * uv.y, 0.0, nx - 1.0);
  float v = clamp(u_droGridV.x + u_droGridV.y * uv.x + u_droGridV.z * uv.y, 0.0, ny - 1.0);
  // The luma axis is unit-width: 14 bins spanning [0, 13), so the bin index is
  // just the log luminance. (The grid's *construction* scales that axis by
  // ceiling/bins instead — the engine is inconsistent between the two and both
  // halves were checked against it.)
  float t = clamp(ylog, 0.0, u_droScale.x);
  float fu = floor(u), fv = floor(v), fb = floor(t);
  vec3 frac = vec3(u - fu, v - fv, t - fb);
  vec2 acc = vec2(0.0);
  for (int du = 0; du < 2; du++) {
    float wu = du == 0 ? 1.0 - frac.x : frac.x;
    int cu = int(min(fu + float(du), nx - 1.0));
    for (int dv = 0; dv < 2; dv++) {
      float wv = dv == 0 ? 1.0 - frac.y : frac.y;
      int cv = int(min(fv + float(dv), ny - 1.0));
      for (int db = 0; db < 2; db++) {
        float w = wu * wv * (db == 0 ? 1.0 - frac.z : frac.z);
        int cb = int(min(fb + float(db), bins - 1.0));
        acc += w * texelFetch(u_dro_grid, ivec2(cu * int(bins) + cb, cv), 0).rg;
      }
    }
  }
  // den is zero only for a cell no pixel landed in; the engine sends those to
  // the top of the range, where the curve's departure is nil.
  return acc.y <= 0.0 ? u_droScale.x : acc.x / acc.y;
}

// The selection colour for the HSL mixer and the masks: four bilinear taps
// around the pixel, averaged, then the same gain -> WB chain as the main
// sample. Which band a pixel belongs to is a property of its *neighbourhood*,
// not of the pixel: chroma noise swings a single pixel's hue by more than the
// Red-Orange centres are apart (0.41 rad, the tightest pair), so per-pixel
// weights speckle — adjacent pixels land in different bands and only some of
// them take the move. The adjustment still applies to this pixel's own colour,
// so detail and edges survive.
//
// Radius: half an output pixel, floored at 0.75 source texels. The preview
// renders at previewScale, so in a fit-to-window view one output pixel spans
// several texels; fwidth follows that and halves the worst-case residue there
// (90th-percentile grain 0.21 -> 0.10), while at 1:1 and on export it drops to
// the floor and the two agree. The floor is 0.75 rather than 0.5 because the
// source texture falls back to NEAREST when RGB32F is not filterable: at 0.5
// the taps can all round back to the centre texel and average nothing.
vec3 wbNeighbourhood(vec2 lensUV, float lensGain) {
  vec2 ts = max(0.75 / vec2(textureSize(u_input, 0)), 0.5 * fwidth(lensUV));
  vec3 nb = texture(u_input, lensUV + vec2( ts.x,  ts.y)).rgb
          + texture(u_input, lensUV + vec2(-ts.x,  ts.y)).rgb
          + texture(u_input, lensUV + vec2( ts.x, -ts.y)).rgb
          + texture(u_input, lensUV + vec2(-ts.x, -ts.y)).rgb;
  return max(u_wbMatrix * (max(nb * 0.25, 0.0) * lensGain), 0.0);
}

void main() {
  vec2 v_texCoord = v_texCoordH.xy / v_texCoordH.z;
  // Outside the source image (rotated/straightened corners in the crop editor,
  // or past the horizon of a perspective): paint the workspace background
  // instead of smearing edge texels.
  if (v_texCoordH.z <= 0.0 || v_texCoord.x < 0.0 || v_texCoord.x > 1.0 || v_texCoord.y < 0.0 || v_texCoord.y > 1.0) {
    outColor = vec4(u_bgColor, 1.0);
    return;
  }

  // --- Lens corrections: radial distortion warp + vignette gain ---
  // v_texCoord is full-source UV (post crop transform), so the warp is anchored
  // to the optical centre regardless of crop. The vignette gain is indexed by
  // the radius of the *fetched* (recorded-frame) position — vignetting is a
  // property of the recorded pixel, not of where correction displays it.
  vec2 lensUV = v_texCoord;
  float lensGain = 1.0;
  if (u_lensActive == 1) {
    vec2 d = (v_texCoord - 0.5) * u_lensNorm * u_lensScale;
    d *= lensInterp(u_lensDist, length(d));
    lensGain = lensInterp(u_lensVig, length(d));
    lensUV = 0.5 + d / u_lensNorm;
  }

  // Input is scene-linear ProPhoto (D50). Edit here in wide-gamut scene-linear.
  vec3 c = max(texture(u_input, lensUV).rgb, 0.0) * lensGain;

  // --- DCP HueSatMaps --- Adobe's order, and the position the worker applied
  // them in: straight after the colour matrix, before anything tonal. Each is
  // skipped by its own dims when the profile carries no such table.
  c = applyHsvTable(c, u_dcp_hsm, u_dcpHsmDims);
  c = applyHsvTable(c, u_dcp_look, u_dcpLookDims);
  if (u_dcpMatchActive == 1) c = applyHsvTable(c, u_dcp_match, u_dcpMatchDims);

  // --- Sony exposure compensation --- Edit's 曝光补偿 is a plain 2^EV gain on
  // the demosaicked linear RGB, before DRO and before everything tonal: at +1 EV
  // the ITP output doubles and nothing else in the chain changes
  // (sony_repro/notes/measured-chroma-gap.md 2.29). No shoulder, so the
  // shouldered exposure below is bypassed for a Sony profile and highlights
  // clip where the engine clips them.
  if (u_sonyLinearExposure == 1) c *= exp2(u_exposure);

  // --- DRO --- After Edit's exposure gain and before everything tonal,
  // matching where the engine's stage sits. One gain for all three channels, so it never shifts
  // colour; BT.601 luminance because that is what the engine forms it from.
  if (u_droActive == 1) {
    float ylog = log2(max(dot(c, vec3(0.299, 0.587, 0.114)), 1e-9) * u_droScale.y);
    // With a grid the curve is indexed by the neighbourhood's log mean, which
    // is what makes DRO local; without one the pixel stands in for it. The
    // grid is addressed by lensUV rather than v_texCoord for the same reason
    // the vignette is: DRO runs before geometric correction, so the relevant
    // position is where the pixel was recorded, not where it is displayed.
    float m = u_droGridActive == 1 ? droLocalMean(lensUV, ylog) : ylog;
    c *= texture(u_dro_lut, vec2(lutCoord(clamp(m / u_droScale.x, 0.0, 1.0)), 0.5)).r;
  }

  // --- Masks: per-pixel weights -> local parameter deltas (docs/masking.md §3) ---
  // Every local slider below is "a scalar into a per-pixel formula", so the
  // weights blend the *parameters* — p + Σ w·Δp — and each block runs once,
  // rather than mixing f(p) with f(p+Δp) per group. White balance is the
  // exception in form only: it is linear, so blending its matrix is blending
  // its result. Selection reads the WB'd neighbourhood (same as HSL) on the
  // perceptual axis toneRegions uses, so a noisy shadow does not speckle the
  // range. u_maskGroups is a uniform: the branch is coherent, and at 0 the
  // default path below is untouched — bit for bit.
  float dExpo = 0.0, dHi = 0.0, dSh = 0.0, dClar = 0.0, dHaze = 0.0, dSat = 0.0, dVib = 0.0, dHue = 0.0, wPrev = 0.0;
  if (u_maskGroups > 0) {
    vec3 cSel = wbNeighbourhood(lensUV, lensGain);
    float pLum = srgbEncode(clamp(ppLuma(cSel) * exp2(u_exposure), 0.0, 1.0));
    vec3 labSel = proPhotoToOklab(cSel);
    vec2 pImg = (u_imgFromTex * vec3(v_texCoord, 1.0)).xy;
    mat3 dWb = mat3(0.0); vec3 dTint = vec3(0.0);
    for (int g = 0; g < u_maskGroups; g++) {
      int base = g * MASK_STRIDE; vec4 hdr = m[base]; float w = 0.0;
      for (int k = 0; k < int(hdr.x); k++) {
        int cb = base + 6 + k * 3; float wc = maskComponent(cb, pImg, pLum, labSel); float op = m[cb].y;
        w = k == 0 ? wc : op == 0.0 ? 1.0 - (1.0 - w) * (1.0 - wc) : op == 1.0 ? w * (1.0 - wc) : w * wc;
      }
      if (hdr.y > 0.5) w = 1.0 - w;
      if (g == u_maskPreview) wPrev = w;
      vec4 A = m[base + 1], B = m[base + 2];
      dExpo += w * A.x; dHi += w * A.y; dSh += w * A.z; dClar += w * A.w;
      dHaze += w * B.x; dSat += w * B.y; dVib += w * B.z; dHue += w * B.w;
      dWb += w * mat3(m[base + 3].xyz, m[base + 4].xyz, m[base + 5].xyz);   // packed column-major
      dTint += w * vec3(m[base + 3].w, m[base + 4].w, m[base + 5].w);
    }
    // --- White Balance, with the blended local delta ---
    c = max((u_wbMatrix + dWb) * c, 0.0);
    // --- Local colour cast: the grading tint multiply, luma-renormalised the
    // same way (and capped for the same reason), on scene-linear values ---
    if (dTint != vec3(0.0)) {
      float lg = ppLuma(c);
      vec3 t = c * (1.0 + dTint);
      float lt = ppLuma(t);
      if (lt > 1e-6) t *= min(lg / lt, GRAD_RENORM_CAP);
      c = max(t, 0.0);
    }
  } else {
    // --- White Balance (Bradford adaptation, identity at temp=6500 / tint=0) ---
    c = max(u_wbMatrix * c, 0.0);
  }

  // === Exposure (highlight-shouldered) + tonal region gains + Clarity ===
  // One log-luminance block, applied to RGB as a single hue-preserving ratio.
  // With everything at defaults the block is an identity multiply, so skip it.
  // The branch is on uniforms — coherent across every pixel, no divergence —
  // and it avoids a needless luma round-trip on untouched frames.
  // (Contrast and Blacks are display-referred and live in the curve LUT bake.)
  // Each gate also admits a mask that touches its block (u_maskUse); the
  // deltas are zero on every frame that has none, so the maths is unchanged.
  bool clarityLocal = ((u_clarity != 0.0 || (u_maskUse & MASK_USE_CLARITY) != 0) && u_hasMask == 1);
  if (u_exposure != 0.0 || u_tonalActive == 1 || clarityLocal || (u_maskUse & MASK_USE_EXPOSURE) != 0) {
    float Y0 = max(ppLuma(c), 1e-6);
    float l = log2(Y0);
    // Exposure: mids move exactly +E; the stops added above EXPO_KNEE compress
    // through the shoulder so brights roll off instead of walling at clip.
    // Negative exposure stays a pure gain (as in Lightroom).
    // A Sony profile already took its exposure as the linear gain above.
    float expo = ((u_sonyLinearExposure == 1) ? 0.0 : u_exposure) + dExpo;
    float lOut = (expo > 0.0)
      ? l + expoShoulder(l + expo) - expoShoulder(l)
      : l + expo;
    if (u_tonalActive == 1 || clarityLocal) {
      float pixLx = lOut - LOG2_MID;                   // stops from middle gray, post-exposure
      // Blurred neighborhood log-luma (see MASK_* shaders), shifted for
      // exposure. Guarded by its consumers: an exposure-only edit must not
      // pay a per-pixel texture fetch it never reads.
      // A local exposure moved the pixel; move its neighbourhood with it, or
      // Clarity would read the offset as detail.
      float maskLx = (u_hasMask == 1)
        ? texture(u_mask_lum, lensUV).r + u_maskShift + dExpo   // mask lives in recorded-frame UV: track the lens warp
        : pixLx;
      if (u_tonalActive == 1) {
        // Whites is display-referred on both halves now (curve.ts basicCurve);
        // see the note on tonalLuma for why its old scene-referred log gain
        // could not coexist with the compressor below.
        //
        // Highlights/Shadows: one endpoint-fixed invertible compressor, its
        // amount weighted by local tone (toneRegions in TONAL_GLSL). Highlights
        // weighs on max(pixel, neighborhood) and Shadows on min, so a small
        // specular stays responsive (pixel term) while dark texture inside a
        // bright region moves with the region (mask term) — local contrast
        // preserved. Log-domain blurring makes the dark side dominate the mask
        // near edges, which keeps highlight recovery from bleeding dark halos.
        //
        // u_tonalActive is exactly (highlights != 0 || shadows != 0), so this
        // needs no further guard of its own.
        float Y = exp2(lOut);
        // exp2 is monotone, so max/min over the log values and over the linear
        // ones pick the same side; comparing here costs one exp2, not two.
        float Ym = (u_hasMask == 1) ? exp2(maskLx + LOG2_MID) : Y;
        lOut = log2(max(toneRegions(Y, u_highlights + dHi, u_shadows + dSh,
                                    max(Y, Ym), min(Y, Ym)), 1e-6));
      }
      // Clarity: local mid-tone contrast — amplify the pixel's deviation from
      // its blurred neighborhood (clarityShift in TONAL_GLSL; window + midtone
      // weight keep edges and clip points from haloing/shifting). Without the
      // mask there is no neighborhood signal, so the slider is inert — same
      // degradation story as the region weights above falling back per-pixel.
      if (clarityLocal) {
        lOut += clarityShift(pixLx, maskLx, u_clarity + dClar);
      }
    }
    c *= exp2(lOut - l);
  }

  // --- Dehaze (global contrast + saturation boost) ---
  if (u_dehaze != 0.0 || (u_maskUse & MASK_USE_DEHAZE) != 0) {
    float haze = u_dehaze + dHaze;
    c = max(c + (c - 0.18) * haze * 0.5, vec3(0.0));
    float l3 = ppLuma(c);
    vec3 ch3 = c - vec3(l3);
    c = max(l3 + ch3 * (1.0 + haze * 0.25), vec3(0.0));
  }

  // --- Vibrance + Saturation (Oklab chroma — hue-stable, no skew) ---
  if (u_vibrance != 1.0 || u_saturation != 1.0 || (u_maskUse & MASK_USE_COLOR) != 0) {
    vec3 lab = proPhotoToOklab(c);
    float C = length(lab.yz);
    float w = 1.0 - smoothstep(0.0, 0.35, C);          // boost low-chroma (vibrance) more
    // Local deltas add to the scales, floored at zero (a mask cannot invert chroma).
    float sat = max(u_saturation + dSat, 0.0), vib = max(u_vibrance + dVib, 0.0);
    // Skin protection (vibrance only, as in Lightroom — the Saturation slider
    // stays global): damp the vibrance term inside the skin-tone window
    // (SKIN_* constants and window shape from hsl-bands.ts). Skipped when
    // only Saturation is in play — the term multiplies to zero anyway.
    float skinW = (vib != 1.0)
      ? hueWindow(atan(lab.z, lab.y), SKIN_HUE, SKIN_HUE_HALF)
        * smoothstep(SKIN_C0, SKIN_C1, C) * (1.0 - smoothstep(SKIN_C2, SKIN_C3, C))
      : 0.0;
    float scale = sat * (1.0 + (vib - 1.0) * w * (1.0 - SKIN_DAMP * skinW));
    lab.yz *= scale;
    // The local Hue slider (no global counterpart): turn (a, b) as the HSL
    // mixer does, on the same ±0.5 rad scale.
    if (dHue != 0.0) {
      float ch = cos(dHue), sh = sin(dHue);
      lab.yz = vec2(lab.y * ch - lab.z * sh, lab.y * sh + lab.z * ch);
    }
    c = max(oklabToProPhoto(lab), 0.0);
  }

  // --- HSL Color Mixer (OkLCh per-band, hue-stable) ---
  // Skip the Oklab round-trip + 8-band hue loop entirely when no band is touched
  // (the default). This is the shader's most expensive block, and like the
  // vibrance/saturation guard above it must not run an identity round-trip per
  // pixel every frame. Branch is on a uniform, so it is coherent across the draw.
  if (u_hslActive == 1) {
    vec3 lab = proPhotoToOklab(c);
    // The selection colour is the neighbourhood's, not the pixel's
    // (wbNeighbourhood says why). Sampling after white balance is enough:
    // everything between it and here (exposure, tonal regions, vibrance)
    // scales luminance or chroma without rotating hue, and the gate reads the
    // ratio C/L, which those scalings leave near enough alone.
    vec3 labSel = proPhotoToOklab(wbNeighbourhood(lensUV, lensGain));
    // Chroma gate: hue is meaningless where chroma is, so near-neutral pixels
    // must fall out of every band rather than land in a random one
    // (hsl-bands.ts). Hue/Saturation and Luminance smoothstep the same ratio
    // over different windows; Luminance is gated harder because it is the axis
    // that shows the noise.
    float rel = hslChromaRatio(length(labSel.yz), labSel.x);
    float sel = smoothstep(HSL_SEL_S0, HSL_SEL_S1, rel);
    if (sel > 1e-3) {
      float hSel = atan(labSel.z, labSel.y);           // Oklab hue, radians
      // Triangular partition-of-unity band weights (HSL_GLSL, hsl-bands.ts):
      // adjacent band values interpolate exactly, with no dead zones between
      // widely spaced centres and no overshoot where bands used to overlap.
      float hAdj = 0.0, sAdj = 0.0, lAdj = 0.0;
      for (int k = 0; k < 8; k++) {
        float m = hslBandWeight(k, hSel);
        hAdj += m * u_hsl_h[k];
        sAdj += m * u_hsl_s[k];
        lAdj += m * u_hsl_l[k];
      }
      // Hue and saturation move this pixel's own (a,b) — the neighbourhood only
      // chose the band. Rotating the vector directly is exact and avoids a
      // second atan2 just to re-encode an angle nothing else reads.
      float dH = hAdj * sel * 0.5;
      float cd = cos(dH), sd = sin(dH);
      lab.yz = (1.0 + sAdj * sel) * vec2(lab.y * cd - lab.z * sd, lab.y * sd + lab.z * cd);
      // Luminance also fades into the deepest stop: its Oklab-L offset is
      // additive, so a fixed step is a far larger relative move down in the
      // shadows — re-amplifying exactly what the gate just damped.
      lAdj *= smoothstep(HSL_SEL_L_S0, HSL_SEL_L_S1, rel) * hslLumFade(lab.x);
      lab.x = max(lab.x + lAdj * 0.15, 0.0);           // per-band luminance
      c = max(oklabToProPhoto(lab), 0.0);
    }
  }

  // ===== View transform: scene-linear -> display-referred ProPhoto [0,1] =====
  c = viewTransform(c);

  // --- Tone Curve (display-referred). LUT layout (curve.ts buildToneCurveLUT):
  // .a = master stack (Basic Contrast/Blacks/Whites -> parametric -> RGB
  // curve), .r/.g/.b = the per-channel point curves.
  //
  // The master stack applies film-like (Adobe's RGBTone): the max and min
  // channels go through the curve and the middle channel is re-interpolated at
  // its original relative position between them. Saturation still rises with
  // contrast, but the RGB-HSV hue is held exactly — a per-channel application
  // skews hue (orange drifts yellow under an S-curve). The R/G/B point curves
  // then apply per channel: crosstalk is their purpose.
  c = clamp(c, 0.0, 1.0);
  if (u_curveActive == 1) { // identity bake (default sliders / compare baseline) skips all 5 fetches
    float cvMax = max(c.r, max(c.g, c.b));
    float cvMin = min(c.r, min(c.g, c.b));
    float cvMax2 = texture(u_curve_lut, vec2(lutCoord(cvMax), 0.5)).a;
    float cvMin2 = texture(u_curve_lut, vec2(lutCoord(cvMin), 0.5)).a;
    c = (cvMax - cvMin > 1e-6)
      ? cvMin2 + (cvMax2 - cvMin2) * (c - cvMin) / (cvMax - cvMin)
      : vec3(cvMax2);
    c = clamp(c, 0.0, 1.0);
    c = vec3(
      texture(u_curve_lut, vec2(lutCoord(c.r), 0.5)).r,
      texture(u_curve_lut, vec2(lutCoord(c.g), 0.5)).g,
      texture(u_curve_lut, vec2(lutCoord(c.b), 0.5)).b
    );
  }

  // --- Color Grading (display-referred split-toning) ---
  // Cascaded blending (each step is a convex mix), so overlapping region
  // weights can never sum past 1 and spike brightness at extreme balance.
  // The tint multiply is then luminance-renormalized: grading shifts color,
  // not exposure — a naive multiply darkens by the tint's luma.
  if (u_grad_blend > 0.001) {
    float lg = ppLuma(c);
    // Balance right slides both edge pairs *down*, so the Highlights region
    // grows and the Shadows region shrinks — the direction the label promises.
    float bal = -u_grad_balance * GRAD_BAL_SPAN;
    float shW = 1.0 - smoothstep(GRAD_SH_EDGE0 + bal, GRAD_SH_EDGE1 + bal, lg);
    float hlW = smoothstep(GRAD_HL_EDGE0 + bal, GRAD_HL_EDGE1 + bal, lg);
    float mdW = (1.0 - shW) * (1.0 - hlW);
    vec3 t = c;
    t = mix(t, t * u_grad_sh_tint, shW);
    t = mix(t, t * u_grad_hl_tint, hlW);
    t = mix(t, t * u_grad_md_tint, mdW);
    // Renorm gain is capped: a saturated cool tint carries little luma, and an
    // uncapped lg/lt boost just shoves the pixel out of gamut for the gamut map
    // to desaturate — the slider would read as broken (more S, less colour).
    // Past the cap, luma yields instead: deep blues darken, like film toning.
    // The orange-yellow warm axis never reaches the cap.
    float lt = ppLuma(t);
    if (lt > 1e-6) t *= min(lg / lt, GRAD_RENORM_CAP);
    c = max(mix(c, t, u_grad_blend), 0.0);
  }

  // ===== Display: ProPhoto -> target gamut -> compress -> encode =====
  vec3 disp = (u_displayGamut == 1) ? (PROPHOTO_TO_P3 * c) : (PROPHOTO_TO_SRGB * c);
  disp = gamutMap(disp, (u_displayGamut == 1) ? P3_Y : REC709_Y);
  // Mask preview: the previewed group's weight as a black-and-white matte —
  // white where the adjustment applies in full. View-only: u_maskPreview is
  // -1 for export and for the assistant's view_image.
  if (u_maskPreview >= 0) disp = vec3(wPrev);
  outColor = vec4(srgbEncode(disp), 1.0); // sRGB transfer (Display-P3 shares it)
}`;

// ===== Luma-mask pre-pass (blurred log2 luminance for Highlights/Shadows) =====
// Runs once per uploaded image into a small (≤256 px long edge) R16F texture:
// downsample to log2 luma, then a separable Gaussian. Rendered in raw source-
// texture UV space with no u_texXform — the main pass samples the mask at
// v_texCoord, which *is* source UV after its transform, so crop / straighten /
// flip stay aligned by construction.

export const MASK_VERTEX_SHADER = `#version 300 es
precision highp float;
layout(location = 0) in vec2 a_position;
out vec2 v_uv;
void main() {
  v_uv = a_position * 0.5 + 0.5;
  gl_Position = vec4(a_position, 0.0, 1.0);
}`;

export const MASK_DOWNSAMPLE_SHADER = `#version 300 es
precision highp float;
in vec2 v_uv;
out vec4 outColor;
uniform sampler2D u_input;
const vec3 PP_Y = vec3(${PROPHOTO_Y[0]}, ${PROPHOTO_Y[1]}, ${PROPHOTO_Y[2]});
void main() {
  vec3 c = max(texture(u_input, v_uv).rgb, 0.0);
  outColor = vec4(log2(max(dot(c, PP_Y), 1e-6)), 0.0, 0.0, 1.0);
}`;

// Separable Gaussian, one axis per pass (u_dir = one texel step). σ/radius are
// fixed at the mask's ≤256 px resolution, so the blur is a constant fraction
// (~3%) of the frame regardless of source size — preview and export produce
// identical masks by construction. Naive taps: 41 reads over ≤256×256 texels,
// once per image upload; not worth a bilinear-pair optimisation.
export const MASK_BLUR_SHADER = `#version 300 es
precision highp float;
in vec2 v_uv;
out vec4 outColor;
uniform sampler2D u_input;
uniform vec2 u_dir;
const float SIGMA = 8.0;
const int RADIUS = 20;          // 2.5σ
void main() {
  float sum = 0.0, wsum = 0.0;
  for (int i = -RADIUS; i <= RADIUS; i++) {
    float w = exp(-0.5 * float(i * i) / (SIGMA * SIGMA));
    sum += w * texture(u_input, v_uv + float(i) * u_dir).r;
    wsum += w;
  }
  outColor = vec4(sum / wsum, 0.0, 0.0, 1.0);
}`;

// ===== Sony in-camera Clarity (ZcTaskSIMDMarble) =====
// A post-pass on the finished, display-encoded frame, which is where the engine
// runs it. Four steps, mirroring the engine's own (sony_repro/PIPELINE.md 7.9.1
// and worker sony/clarity.py, which owns every constant below):
//
//   1. 8x box downsample to luma          -> CLARITY_DOWN_SHADER
//   2. edge-aware 5x5 mean, stride 2      -> CLARITY_EDGE_SHADER
//   3. 3x3 Gaussian, mixed with centre    -> CLARITY_BLUR_SHADER
//   4. bilinear upsample + add back detail -> SONY_POST_SHADER, which it shares
//      with the sharpening stage that runs just before it
//
// Steps 1-3 run on a grid pinned to 1/8 of the *frame*, so the blur radius is
// the same fraction of the image whatever resolution the frame is rendered at
// — the same reasoning as MASK_LONG above, and the reason a preview and its
// export agree. Below export resolution the box prefilter in step 1 degrades
// toward point sampling (there are fewer scene texels per cell than taps, so
// the renderer drops the tap count to match); the edge mean and the Gaussian
// that follow wash out the aliasing that leaves, exactly as the mask's blur
// does. The engine works on YCC's Y with Cb/Cr untouched, and Y enters RGB
// additively — so the compose step *adds* the luma delta to all three channels
// rather than scaling them, which is what keeps the hue put.

export const CLARITY_DOWN_SHADER = `#version 300 es
precision highp float;
in vec2 v_uv;
out vec4 outColor;
uniform sampler2D u_input;
// One base cell measured in scene UV — not one scene texel. The base grid is
// pinned to a fraction of the *frame* so the blur radius survives the preview
// being rendered smaller than the sensor; the taps therefore have to be keyed
// to the cell, which is a different distance from a scene texel.
uniform vec2 u_cell;
// Taps per axis across that cell. The renderer sets it from how many scene
// texels a cell actually spans: asking for more than that just reads the same
// texel repeatedly, which is most of the cost on a downscaled preview.
uniform int u_taps;
const vec3 LUMA = vec3(${glslFloat(REC709_Y[0])}, ${glslFloat(REC709_Y[1])}, ${glslFloat(REC709_Y[2])});
void main() {
  // Taps spread evenly over the cell. At export resolution there are four per
  // axis and each is a bilinear 2x2, making this an exact 8x8 box mean for a
  // quarter of the reads. Averaging RGB and then taking luma is the same
  // number as the other order, luma being linear.
  float n = float(u_taps);
  vec2 step = u_cell / n;
  vec3 sum = vec3(0.0);
  for (int j = 0; j < u_taps; j++) {
    for (int i = 0; i < u_taps; i++) {
      sum += texture(u_input, v_uv + (vec2(float(i), float(j)) + 0.5 - n * 0.5) * step).rgb;
    }
  }
  outColor = vec4(dot(sum / (n * n), LUMA), 0.0, 0.0, 1.0);
}`;

export const CLARITY_EDGE_SHADER = `#version 300 es
precision highp float;
in vec2 v_uv;
out vec4 outColor;
uniform sampler2D u_input;
uniform vec2 u_texel;
uniform float u_threshold;            // calib[0x1174], normalised
void main() {
  // 5x5 at stride 2 (reach +-4), dropping every sample further than the
  // threshold from the centre. That rejection is the only thing stopping the
  // base layer from crossing an edge and haloing it.
  float c = texture(u_input, v_uv).r;
  float sum = 0.0, n = 0.0;
  for (int j = -2; j <= 2; j++) {
    for (int i = -2; i <= 2; i++) {
      float s = texture(u_input, v_uv + vec2(float(i), float(j)) * 2.0 * u_texel).r;
      float ok = step(abs(c - s), u_threshold);
      sum += ok * s;
      n += ok;
    }
  }
  outColor = vec4(sum / max(n, 1.0), 0.0, 0.0, 1.0);
}`;

export const CLARITY_BLUR_SHADER = `#version 300 es
precision highp float;
in vec2 v_uv;
out vec4 outColor;
uniform sampler2D u_input;
uniform vec2 u_texel;
uniform float u_centerMix;            // calib[0x1176]/256, 0 on every body seen
void main() {
  // [[1,2,1],[2,4,2],[1,2,1]] / 16, then lerped back toward the centre pixel.
  // The kernel is separable, so the weight is a product of two 1D taps (2,1)
  // rather than something the reader has to expand out of an exponent.
  float sum = 0.0, c = 0.0;
  for (int j = -1; j <= 1; j++) {
    for (int i = -1; i <= 1; i++) {
      float s = texture(u_input, v_uv + vec2(float(i), float(j)) * u_texel).r;
      sum += (2.0 - abs(float(i))) * (2.0 - abs(float(j))) * s;
      if (i == 0 && j == 0) c = s;     // the centre tap, not a second fetch
    }
  }
  outColor = vec4(mix(sum / 16.0, c, u_centerMix), 0.0, 0.0, 1.0);
}`;

// ===== Sony's two post stages, composed together =====
// The engine runs `Sharpness -> Spica -> Marble`, i.e. sharpening immediately
// before Clarity, and both work on luma alone with the chroma planes untouched.
// They share this one pass because they share its expensive part: a
// full-resolution fetch of the finished frame. Each is skipped by its own gain
// being zero; with both off renderPass never builds the chain at all and draws
// straight to its target, so this pass does not run.
//
// Sharpening is the 7x7 high-pass of PIPELINE.md 7.11. The kernel below is the
// operator's own shape rather than anything per-shot, so it lives here and the
// worker ships only the amplitude — the reverse of Clarity, whose thresholds
// are camera calibration and therefore have to travel.
//
// Unlike Clarity, this kernel is keyed to *scene* texels rather than to a
// fraction of the frame, because that is what the engine's is: three sensor
// pixels. A render below full resolution therefore sharpens at its own scale
// and only the export matches the engine. The worker records that in
// SHARPEN_NOTE, on the profile's `limitations` — which nothing renders today,
// so it is written down but not yet shown.

// The three weights of the high-pass, and the binomial blur it subtracts.
// SHARPEN_CENTER - SHARPEN_BLUR_W - 4*SHARPEN_NEAR_W = 0 exactly: the taps
// cancel, which is what makes this a detail extractor rather than a brightness
// shift, and what lets the dead zone below protect flat areas.
export const SHARPEN_CENTER = 25.6;
export const SHARPEN_BLUR_W = 10.24;
export const SHARPEN_NEAR_W = 3.84;
// Pascal's 7th row, summing to 64 per axis — so the outer product sums to 4096.
export const SHARPEN_BINOMIAL = [1, 6, 15, 20, 15, 6, 1];
// The engine's dead zone of 1024 on its own 14-bit luma plane. `c` is
// opts[0x214], which every camera-written frame leaves at 0 and which the
// engine turns into 25; the threshold is 40.96*c.
export const SHARPEN_DEADZONE = (40.96 * 25) / 16383;

const BINOMIAL_GLSL = SHARPEN_BINOMIAL.map(glslFloat).join(", ");
const BINOMIAL_SUM = SHARPEN_BINOMIAL.reduce((a, b) => a + b, 0);

export const SONY_POST_SHADER = `#version 300 es
precision highp float;
in vec2 v_uv;
out vec4 outColor;
uniform sampler2D u_scene;            // the finished frame, display-encoded
uniform sampler2D u_base;             // Clarity's 1/8-scale base, LINEAR-filtered
uniform vec2 u_baseTexel;             // one base texel in UV
uniform vec2 u_sceneTexel;            // one scene texel — the sharpen kernel's step
uniform float u_gain;                 // Clarity: AMP[clarity]/1024, 0 = off
uniform float u_knee;                 // Clarity rolloff knee, 0.125
uniform float u_sharpen;              // Sharpness: the amplitude, 0 = off
const vec3 LUMA = vec3(${glslFloat(REC709_Y[0])}, ${glslFloat(REC709_Y[1])}, ${glslFloat(REC709_Y[2])});
const float BIN[7] = float[7](${BINOMIAL_GLSL});
const float DEADZONE = ${glslFloat(SHARPEN_DEADZONE)};
void main() {
  vec3 rgb = texture(u_scene, v_uv).rgb;
  float y = dot(rgb, LUMA);
  float delta = 0.0;

  if (u_sharpen > 0.0) {
    // hp = 25.6*y - 10.24*binomial(y) - 3.84*(the four neighbours). Run as one
    // 7x7 gather rather than a separable pair: separating it would need a
    // full-resolution intermediate, and at 49 taps this is cheaper in both
    // bandwidth and VRAM than the texture that would save the multiplies.
    //
    // The taps accumulate as colour and the luma dot is taken once at the end
    // — the operator is linear, so weighting luma and taking luma of the
    // weighted sum are the same number for a third of the arithmetic. The
    // centre tap is skipped in the loop and weighted straight off the rgb
    // already fetched above, rather than read a second time.
    vec3 blur = ${glslFloat(SHARPEN_BINOMIAL[3] * SHARPEN_BINOMIAL[3])} * rgb;
    vec3 near = vec3(0.0);
    for (int j = -3; j <= 3; j++) {
      for (int i = -3; i <= 3; i++) {
        if (i == 0 && j == 0) continue;
        vec3 s = texture(u_scene, v_uv + vec2(float(i), float(j)) * u_sceneTexel).rgb;
        blur += BIN[i + 3] * BIN[j + 3] * s;
        if (abs(i) + abs(j) == 1) near += s;
      }
    }
    float hp = dot(${glslFloat(SHARPEN_CENTER)} * rgb
                   - ${glslFloat(SHARPEN_BLUR_W)} * (blur / ${glslFloat(BINOMIAL_SUM * BINOMIAL_SUM)})
                   - ${glslFloat(SHARPEN_NEAR_W)} * near, LUMA);
    // A hard threshold on hp itself, never on u_sharpen*hp — that is what stops
    // a stronger setting from letting new pixels through, and it is why Sony's
    // sharpening stays off flat sky however far it is pushed. The engine also
    // floors the result onto its 14-bit plane; that step is left out, being a
    // quantisation 64x finer than the 8-bit frame this writes into.
    delta = abs(hp) < DEADZONE ? 0.0 : u_sharpen * hp;
  }

  if (u_gain > 0.0) {
    // Half a base texel to the right. The engine weights the right-hand
    // neighbour by (2d+1)/16 across a block of 8; a plain fetch at v_uv lands on
    // d/8, and the half-texel shift is exactly the missing 1/16. Without it the
    // detail layer sits off by an eighth of a base pixel, which costs ~17% of the
    // effect — a systematic error, not a rounding one (tools/clarity_check.py).
    float base = texture(u_base, v_uv + 0.5 * u_baseTexel).r;
    // Clarity runs after sharpening, so it sees the sharpened luma. Its base
    // layer does not need to: an 8x box mean has nothing left of a +-3 pixel
    // high-pass whose taps already cancel, so building it from the unsharpened
    // scene gives the same picture without a second full-resolution pass.
    float ys = y + delta;
    // min(4Y, 4(1-Y), 0.5)/0.5 — full strength through the midtones, tapering to
    // nothing in the last eighth at either end so highlights cannot halo.
    float roll = clamp(min(ys, 1.0 - ys) / u_knee, 0.0, 1.0);
    delta += roll * u_gain * (ys - base);
  }
  outColor = vec4(clamp(rgb + delta, 0.0, 1.0), 1.0);
}`;

// ===== Spica, the fine half of sharpening =====
// Runs between the two stages above — the engine's order is
// `Sharpness -> Spica -> Marble`. Unlike those two it reads its neighbours, so
// it cannot share their pass: it needs the sharpened frame to already exist,
// which is the whole reason the chain has an intermediate at all.
//
// The operator is PIPELINE.md 7.11.4.9, and the parts of it that are tables
// live in spica-tables.ts. What is left is: classify a 9-point cross into an
// 8-bit code, look the code up for a weight table and a mirroring, run a
// 25-tap diamond through it, scale the result by the minimum of three
// trapezoids, and blend that back over the original by `u_amount`.
//
// Two things the engine does that this deliberately does not. It works on a
// 14-bit integer plane and truncates twice on the way out; this works on the
// display-encoded frame in float, so the classifier's thresholds land on
// whatever steps that frame has and the truncations are dropped. And its
// diamond reaches three *sensor* pixels, so like sharpening it is only the
// engine's operator at full resolution. Both are on the worker's SPICA_NOTE.

/** Below this local range (14-bit) the engine skips the classifier: table 0. */
export const SPICA_RANGE_THRESHOLD = 8;
/**
 * `cfg[0xc4] / 2048` at base ISO — turns a trapezoid's [48, 512] into the detail
 * gain. The engine halves it by ISO 1600 and quarters it by 25600, and shifts
 * the range curve's a/b up by 128 / 384 over the same ramp; both travel on the
 * profile as `gainScale` / `rangeShift` (worker sony/spica.py), this constant
 * is what a profile without them (or a base-ISO frame) means.
 */
export const SPICA_GAIN_SCALE = 2;
/** The three trapezoids: `(a, b, c, d)` then their outside/inside values. */
export const SPICA_CURVE_DETAIL = [512, 2048, 2304, 3840, 128, 512];
export const SPICA_CURVE_RANGE = [128, 2602.666748046875, 2048, 3904, 48, 512];
export const SPICA_CURVE_MID = [0, 10752, 3072, 5760, 176, 512];
/** The weight tables' normalisation, and the gain's — two shifts of 512. */
const SPICA_WEIGHT_SUM = 512;
/** What the detail curve is indexed by: `|detail| / 128`. */
const SPICA_DETAIL_DIVISOR = 128;
/** The 14-bit plane the engine's thresholds and breakpoints are quoted on. */
const SPICA_WHITE = 16383;

// The diamond, row-major — the order the weight tables are stored in — plus a
// 7x7 reverse index so a mirrored tap can be read out of the same 25 fetches.
// Mirroring maps the diamond onto itself, so nothing is ever re-sampled: only
// which of the 25 a tap reads changes.
const SPICA_TAPS: [number, number][] = [];
for (let ty = -3; ty <= 3; ty++) {
  for (let tx = -3; tx <= 3; tx++) if (Math.abs(ty) + Math.abs(tx) <= 3) SPICA_TAPS.push([ty, tx]);
}
const SPICA_GRID = new Int32Array(49).fill(-1);
SPICA_TAPS.forEach(([ty, tx], k) => { SPICA_GRID[(ty + 3) * 7 + (tx + 3)] = k; });
const gridAt = (ty: number, tx: number) => SPICA_GRID[(ty + 3) * 7 + (tx + 3)];
// The classifier's nine points, in bit order. All are within +-2, so every one
// of them is already in the diamond and none needs a fetch of its own.
const SPICA_CROSS: [number, number][] = [
  [-2, 0], [-1, 0], [0, -2], [0, -1], [0, 0], [0, 1], [0, 2], [1, 0], [2, 0],
];

const trapGlsl = (name: string, c: number[]) =>
  `const vec4 ${name} = vec4(${c.slice(0, 4).map(glslFloat).join(", ")});\n`
  + `const vec2 ${name}_V = vec2(${glslFloat(c[4])}, ${glslFloat(c[5])});`;

export const SPICA_SHADER = `#version 300 es
precision highp float;
precision highp int;
in vec2 v_uv;
out vec4 outColor;
uniform sampler2D u_scene;      // the sharpened frame, display-encoded
uniform sampler2D u_weights;    // 25 x 100 R32F, one table per row
uniform sampler2D u_lut;        // 256 x 1 RGBA32F: (table, dx, dy)
uniform vec2 u_sceneTexel;      // one scene texel — the diamond's step
uniform float u_amount;         // how much of the filtered value survives, 0 = off
uniform float u_isoGain;        // detail scale for this shot's ISO
uniform float u_gainScale;      // cfg[0xc4] / 2048 for this shot's ISO (SPICA_GAIN_SCALE at base ISO)
uniform float u_rangeShift;     // how far the range trapezoid's a and b move up for this ISO
const vec3 LUMA = vec3(${glslFloat(REC709_Y[0])}, ${glslFloat(REC709_Y[1])}, ${glslFloat(REC709_Y[2])});
const float WHITE = ${glslFloat(SPICA_WHITE)};
const float RANGE_THRESHOLD = ${glslFloat(SPICA_RANGE_THRESHOLD)};
${trapGlsl("C_DETAIL", SPICA_CURVE_DETAIL)}
${trapGlsl("C_RANGE", SPICA_CURVE_RANGE)}
${trapGlsl("C_MID", SPICA_CURVE_MID)}
const int TAPY[25] = int[25](${SPICA_TAPS.map(t => t[0]).join(", ")});
const int TAPX[25] = int[25](${SPICA_TAPS.map(t => t[1]).join(", ")});
const int GRID[49] = int[49](${Array.from(SPICA_GRID).join(", ")});
const int CROSS[9] = int[9](${SPICA_CROSS.map(([ty, tx]) => gridAt(ty, tx)).join(", ")});
const int CENTRE = ${gridAt(0, 0)};

// Edit.exe 0x35a1d0. Flat at \`v.x\` outside [a, d], flat at \`v.y\` over [b, c],
// linear across the two flanks. Written as the engine's own chain of tests
// rather than as interval conditions on purpose: two of the three curves have
// b > c, which makes their plateau unreachable, and only this order reproduces
// that — an interval form would start answering from a segment the engine
// never reaches.
float spicaTrap(float x, vec4 abcd, vec2 v) {
  if (x < abcd.x) return v.x;
  if (x < abcd.y) { float t = (abcd.y - x) / (abcd.y - abcd.x); return mix(v.y, v.x, t); }
  if (x < abcd.z) return v.y;
  if (x < abcd.w) { float t = (abcd.w - x) / (abcd.w - abcd.z); return mix(v.x, v.y, t); }
  return v.x;
}

void main() {
  vec3 rgb = texture(u_scene, v_uv).rgb;
  if (u_amount == 0.0) { outColor = vec4(rgb, 1.0); return; }

  // The diamond, once and unmirrored, on the engine's 14-bit scale so its
  // thresholds and breakpoints can be used as written.
  float d[25];
  for (int k = 0; k < 25; k++) {
    vec2 off = vec2(float(TAPX[k]), float(TAPY[k])) * u_sceneTexel;
    d[k] = dot(texture(u_scene, v_uv + off).rgb, LUMA) * WHITE;
  }
  float y = d[CENTRE];

  float lo = d[CROSS[0]], hi = lo;
  for (int b = 1; b < 9; b++) { lo = min(lo, d[CROSS[b]]); hi = max(hi, d[CROSS[b]]); }
  float range = hi - lo;
  float mid = floor((hi + lo) * 0.5);

  // Nine bits, one per cross point: is it in the upper half of the local
  // range? Bit 8 set means the pattern is the complement of one already in the
  // table, so it folds — which is what takes 512 codes down to the LUT's 256.
  // Under the range threshold the engine does not classify at all and takes
  // code 0, whose table is the family's one isotropic shape.
  int code = 0;
  if (range >= RANGE_THRESHOLD) {
    float half_ = floor(range * 0.5);
    for (int b = 0; b < 9; b++) if (d[CROSS[b]] - lo >= half_) code |= 1 << b;
    if ((code & 256) != 0) code = 255 - (code & 255);
  }
  vec4 e = texelFetch(u_lut, ivec2(code, 0), 0);
  int table = int(e.r);
  int dx = int(e.g), dy = int(e.b);

  // The mirroring flips the sampling grid, not the table: tap (ty, tx) reads
  // the sample at (ty*dy, tx*dx), which is another entry of the same 25.
  float s = 0.0;
  for (int k = 0; k < 25; k++) {
    float w = texelFetch(u_weights, ivec2(k, table), 0).r;
    s += w * d[GRID[(TAPY[k] * dy + 3) * 7 + (TAPX[k] * dx + 3)]];
  }

  float detail = (s - ${glslFloat(SPICA_WEIGHT_SUM)} * y) * u_isoGain;
  // Three of the engine's config values ride on the ISO (worker sony/spica.py):
  // the detail scale above, the gain scale here and the range curve's a/b.
  float gain = u_gainScale * min(
    min(spicaTrap(abs(detail) / ${glslFloat(SPICA_DETAIL_DIVISOR)}, C_DETAIL, C_DETAIL_V),
        spicaTrap(range, C_RANGE + vec4(u_rangeShift, u_rangeShift, 0.0, 0.0), C_RANGE_V)),
    spicaTrap(mid, C_MID, C_MID_V));
  // Both shifts at once: the weights sum to 512 and the gain is on a 512 scale.
  float filtered = clamp(y + gain * detail / ${glslFloat(SPICA_WEIGHT_SUM * SPICA_WEIGHT_SUM)},
                         0.0, WHITE);
  // The blend the engine's second pass does. Clamping the filtered value first
  // rather than folding the two together matters: u_amount runs past 1 at the
  // top of the Sharpness ladder, so this extrapolates, and the intermediate
  // clamp is what bounds what gets extrapolated.
  float delta = (mix(y, filtered, u_amount) - y) / WHITE;
  outColor = vec4(clamp(rgb + delta, 0.0, 1.0), 1.0);
}`;

// ===== Sony in-camera chroma cleanup — the other half of ZcTaskSIMDMarble =====
// The engine's stage, decoded bit for bit in worker sony/marble.py (each step
// verified 100% against the engine's own buffers captured at export); this is
// that port in float. It runs last in the chain, on the 14-bit display RGB, and
// touches chroma only:
//
//   gamut_fwd   sRGB EOTF -> 3x3 /8192 matrix -> clamp -> x^(256/563): the
//               working space the stage converts back out of at the end.
//   YCC         Y = (2884R+3523G+625B)/2048 + 4096, C1/C2 the two colour
//               differences on a 32768 centre — the thresholds below are in
//               these units, so the shaders keep them rather than normalising.
//   marbleDown  chroma pair-mean 2:1 then a 4x4 box: everything to 1/4 res.
//   marbleMean  cnr2: a 5x5 stride-2 window (+-4 texels at 1/4 res) whose
//               neighbours are admitted only when their Y and chroma sit within
//               thresholds derived from the centre's Y level and chroma
//               magnitude; the mean of the admitted ones.
//   marbleBlur  cnr3: [1 2 1;2 4 2;1 2 1]/16, mixed with the centre by p/256.
//   marbleCompose  cnr4: bilinear upsample at the engine's centred phases (row
//               phases 1/8..7/8, column phases 1/4, 3/4), a protect term that
//               hands strongly red pixels their original C2 back, nearest 2x
//               horizontal expansion (both pixels of a pair take the same
//               cleaned chroma), blend with the original chroma by `amount`
//               (ISO- and slider-dependent, rendering/sony-marble.ts), then the
//               Q15 inverse YCC matrix and gamut_inv.
//
// Float against the engine's integers: the check in scripts/marble-check.ts
// compares the four passes against the reference on a crop of the engine's own
// tile input: 0.33/255 at the worst pixel and 0.10/255 on average, against a
// stage that moves pixels by 24/255. The two edge conventions differ (the engine
// zero-pads partial boxes at the far edge and leaves two rows undefined; here
// everything clamps to the edge texel), which only reaches the outermost
// pixels of the frame.
const MARBLE_GLSL_COMMON = `
precision highp int;
// The engine's gamut matrices over 8192 (marble.py M_FWD / M_INV), written
// column by column because that is how a GLSL constructor takes them; each
// column here is one column of the engine's row-major table.
const mat3 M_FWD = mat3(5042.0, 748.0, 115.0,
                        3234.0, 6845.0, 595.0,
                        -84.0, 599.0, 7481.0) / 8192.0;
const mat3 M_INV = mat3(14306.0, -1555.0, -96.0,
                        -6820.0, 10614.0, -739.0,
                        707.0, -867.0, 9029.0) / 8192.0;
// The tables index by v/16384 with v = rint(rgb*16383) and encode as
// floor(16384*f): the normaliser is 16384 while white is 16383.
const float NORM = 16384.0;
const float WHITE14 = 16383.0;

vec3 srgbEotf3(vec3 u) {
  return mix(u / 12.92, pow((u + 0.055) / 1.055, vec3(2.4)), step(0.04045, u));
}
vec3 srgbOetf3(vec3 u) {
  return mix(u * 12.92, 1.055 * pow(u, vec3(1.0 / 2.4)) - 0.055, step(0.0031308, u));
}
// Display RGB 0..1 -> the stage's working RGB in 14-bit units (gamut_fwd).
vec3 marbleGamutFwd(vec3 rgb) {
  vec3 lin = srgbEotf3(clamp(rgb, 0.0, 1.0) * (WHITE14 / NORM));
  vec3 w = clamp(M_FWD * lin, 0.0, WHITE14 / NORM);
  return NORM * pow(w, vec3(256.0 / 563.0));
}
// The inverse: 14-bit working RGB -> display 0..1 (gamut_inv).
vec3 marbleGamutInv(vec3 w) {
  vec3 p = pow(clamp(w, 0.0, WHITE14) / NORM, vec3(563.0 / 256.0));
  vec3 lin = clamp(M_INV * p, 0.0, WHITE14 / NORM);
  return srgbOetf3(lin) * (NORM / WHITE14);
}
// 14-bit working RGB -> engine YCC (rgb_to_ycc): Y keeps its +4096 offset;
// C1 and C2 are carried *without* the 32768 the engine adds, which is the
// form the protect term wants and what keeps a 16F fallback target precise
// where it matters. The engine clips the offset planes to 16 bits, so the
// centred ones clip to [-32768, 32767].
vec3 marbleYcc(vec3 w) {
  return vec3(dot(w, vec3(2884.0, 3523.0, 625.0)) / 2048.0 + 4096.0,
              clamp(dot(w, vec3(-1707.0, -2404.0, 4113.0)) / 2048.0, -32768.0, 32767.0),
              clamp(dot(w, vec3(6860.0, -6848.0, -9.0)) / 2048.0, -32768.0, 32767.0));
}
// Engine YCC (centred chroma) -> 14-bit working RGB (ycc_to_rgb, the Q15 matrix).
vec3 marbleYccToRgb(vec3 ycc) {
  vec3 v = vec3(ycc.x - 4096.0, ycc.yz);
  return clamp(vec3(dot(v, vec3(9542.0, -1438.0, 5414.0)),
                    dot(v, vec3(9547.0, -1459.0, -4376.0)),
                    dot(v, vec3(9538.0, 14864.0, -310.0))) / 32768.0,
               0.0, WHITE14);
}`;

/**
 * Full-res display RGB -> (Y, C1, C2) at 1/4 resolution. One texel here is the
 * 4x4 block of scene texels starting at 4x its coordinate; the engine's 2:1
 * chroma pair mean followed by its 4x4 box is, in float, just this 16-mean.
 * Blocks past the edge (a width or height not divisible by 4) repeat the edge
 * texel.
 */
export const MARBLE_DOWN_SHADER = `#version 300 es
precision highp float;
out vec4 outColor;
uniform sampler2D u_scene;
${MARBLE_GLSL_COMMON}
void main() {
  ivec2 size = textureSize(u_scene, 0);
  ivec2 cell = ivec2(gl_FragCoord.xy) * 4;
  vec3 acc = vec3(0.0);
  for (int j = 0; j < 4; j++) {
    for (int i = 0; i < 4; i++) {
      ivec2 p = min(cell + ivec2(i, j), size - 1);
      acc += marbleYcc(marbleGamutFwd(texelFetch(u_scene, p, 0).rgb));
    }
  }
  outColor = vec4(acc / 16.0, 1.0);
}`;

/**
 * cnr2 (marble.py cnr2_threshold_mean): the admitted-neighbour mean over the
 * 25 taps at offsets {-4,-2,0,2,4}^2, centre included, so the count is never
 * zero. The thresholds are the engine's, in its units: `t0` from the centre's
 * Y level, `a1`/`a2` from its chroma magnitude, and the products scaled by
 * the slider-dependent gain (p4c/p50) and clamped to 256..32768. The engine's
 * (x*p+128)>>8 steps are plain divisions here. Y passes through.
 */
export const MARBLE_MEAN_SHADER = `#version 300 es
precision highp float;
out vec4 outColor;
uniform sampler2D u_input;   // marbleDown's output
uniform vec3 u_thrY;         // p30 (the Y window), p34, p38
uniform vec3 u_thrC1;        // p3c, p40, p4c
uniform vec3 u_thrC2;        // p44, p48, p50
${MARBLE_GLSL_COMMON}
void main() {
  ivec2 size = textureSize(u_input, 0);
  ivec2 at = ivec2(gl_FragCoord.xy);
  vec3 c = texelFetch(u_input, at, 0).xyz;
  float t0 = clamp(c.x * u_thrY.y / 256.0 + u_thrY.z, 0.0, 65535.0);
  float a1 = clamp(c.y * u_thrC1.x / 256.0 + u_thrC1.y, 0.0, 65535.0);
  float a2 = clamp(c.z * u_thrC2.x / 256.0 + u_thrC2.y, 0.0, 65535.0);
  float thr1 = clamp(clamp(t0 * a1 / 256.0, 0.0, 65535.0) * u_thrC1.z / 256.0, 256.0, 32768.0);
  float thr2 = clamp(clamp(t0 * a2 / 256.0, 0.0, 65535.0) * u_thrC2.z / 256.0, 256.0, 32768.0);
  vec2 sum = vec2(0.0);
  float n = 0.0;
  for (int dy = -4; dy <= 4; dy += 2) {
    for (int dx = -4; dx <= 4; dx += 2) {
      vec3 s = texelFetch(u_input, clamp(at + ivec2(dx, dy), ivec2(0), size - 1), 0).xyz;
      vec3 d = abs(c - s);
      if (d.x <= u_thrY.x && d.y <= thr1 && d.z <= thr2) {
        sum += s.yz;
        n += 1.0;
      }
    }
  }
  outColor = vec4(c.x, sum / n, 1.0);
}`;

/**
 * cnr3 (marble.py cnr3_blur): the 3x3 binomial on each chroma plane, mixed
 * with the centre by p54/256 and p58/256 — (16*c*p + gauss*(256-p))/4096 is
 * mix(gauss/16, c, p/256). Y passes through so the compose can sample one
 * texture.
 */
export const MARBLE_BLUR_SHADER = `#version 300 es
precision highp float;
out vec4 outColor;
uniform sampler2D u_input;   // marbleMean's output
uniform vec2 u_centreMix;    // p54/256, p58/256
${MARBLE_GLSL_COMMON}
void main() {
  ivec2 size = textureSize(u_input, 0);
  ivec2 at = ivec2(gl_FragCoord.xy);
  vec2 acc = vec2(0.0);
  for (int dy = -1; dy <= 1; dy++) {
    for (int dx = -1; dx <= 1; dx++) {
      float w = float((2 - abs(dx)) * (2 - abs(dy)));
      acc += w * texelFetch(u_input, clamp(at + ivec2(dx, dy), ivec2(0), size - 1), 0).yz;
    }
  }
  vec3 c = texelFetch(u_input, at, 0).xyz;
  outColor = vec4(c.x, mix(acc / 16.0, c.yz, u_centreMix), 1.0);
}`;

/**
 * cnr4 and the return trip (marble.py cnr4_upsample, cnr4_protect, the
 * amount blend, ycc_to_rgb, gamut_inv), one pass at full resolution.
 *
 * The upsample is the hardware's bilinear read of marbleBlur's target at the
 * engine's phases. Half-res column hx (= x/2, the chroma pair) sits at 1/4-res
 * texel coordinate hx/2 - 0.25 — phases 1/4 and 3/4 — and full-res row y at
 * (y - 1.5)/4 — phases 1/8, 3/8, 5/8, 7/8 — so with texel i centred at index i
 * the UV is ((hx/2 + 0.25)/W, ((y + 0.5)/4)/H) over the 1/4-res size. Those
 * are the weights the engine's table holds (84/28/12/4 over 128) and the
 * "+2, +1" grid offset its writes show. CLAMP_TO_EDGE is the engine's
 * replicated far row and column. Both pixels of a pair read the same sample,
 * which is the nearest 2x expansion.
 *
 * `tmp`, the original half-res C2 the protect term blends back in, is the pair
 * mean of the two full-res samples and has to be rebuilt here — the scene
 * holds RGB, not YCC — which is why this pass converts its partner too.
 */
export const MARBLE_COMPOSE_SHADER = `#version 300 es
precision highp float;
out vec4 outColor;
uniform sampler2D u_scene;   // full-res display RGB, the compose output
uniform sampler2D u_blur;    // marbleBlur's output, sampled LINEAR
uniform vec4 u_protect;      // lo1*256, r1, lo2*256, r2 (sony-marble.ts)
uniform float u_strength;    // 0..255, alpha = k*strength over 32768
uniform float u_amount;      // 0 = off, 1 = fully cleaned
${MARBLE_GLSL_COMMON}
void main() {
  ivec2 size = textureSize(u_scene, 0);
  ivec2 at = ivec2(gl_FragCoord.xy);
  int hx = at.x / 2;
  ivec2 partner = ivec2(min(2 * hx + 1 - (at.x & 1), size.x - 1), at.y);
  vec3 own = marbleYcc(marbleGamutFwd(texelFetch(u_scene, at, 0).rgb));
  vec3 other = marbleYcc(marbleGamutFwd(texelFetch(u_scene, partner, 0).rgb));
  float tmp = 0.5 * (own.z + other.z);

  vec2 lo = vec2(textureSize(u_blur, 0));
  vec2 uv = vec2((float(hx) * 0.5 + 0.25) / lo.x, (float(at.y) + 0.5) * 0.25 / lo.y);
  vec2 u = texture(u_blur, uv).yz;

  // Protect: k = ((min(s1, c2) + 32768) >> 16) in 0..128, alpha = k*strength.
  const float FULL = 8388608.0;   // 2^23
  float s1 = FULL - clamp((abs(u.x) - u_protect.x) * u_protect.y, 0.0, FULL);
  float c2 = clamp((u.y - u_protect.z) * u_protect.w, 0.0, FULL);
  float k = clamp(floor(min(s1, c2) / 65536.0 + 0.5), 0.0, 128.0);
  float alpha = k * u_strength / 32768.0;
  vec2 cleaned = vec2(u.x, mix(u.y, tmp, alpha));

  vec2 cc = clamp(mix(own.yz, cleaned, u_amount), -32768.0, 32767.0);
  outColor = vec4(marbleGamutInv(marbleYccToRgb(vec3(own.x, cc))), 1.0);
}`;

// ===== Camera match =====
// The last stage of the chain, after Marble: the fitted residual between this
// pipeline's finished sRGB frame and the body's own JPEG, per body and
// Creative Look (worker sony/profile.py camera_match_table, data from
// sony_repro/tools/camera_match_fit.py). It is not an engine stage — it is
// what the engine's export still differs from the camera by, measured on a
// hundred-odd frames and small enough (a few L*, a few percent of chroma, a
// degree or two of hue) to be a fixed table. It runs on the display-encoded
// frame because that is what the fit measured: the tables are in CIELAB over
// sRGB, and the maths here is the transcription of camera-match.ts, which is
// the tested mirror.
//
// The tables travel as two textures rather than uniform arrays: the dL and
// cr surfaces are 21 x 19 each, past what a uniform array should carry. Both
// are read with texelFetch and the interpolation is written out here, never
// left to LINEAR: a float texture with a LINEAR filter is *incomplete* on a
// device without OES_texture_float_linear, and an incomplete texture samples
// as zero with nothing logged (pipeline-renderer.ts floatLinear) — the stage
// would silently flatten every pixel's chroma to nothing.

/** A row-major 3x3 as a GLSL constructor, which takes its columns. */
function glslMat3(m: readonly number[]): string {
  return `mat3(${[0, 1, 2].map(c => [0, 1, 2].map(r => glslFloat(m[3 * r + c])).join(", ")).join(", ")})`;
}

export const CAMERA_MATCH_SHADER = `#version 300 es
precision highp float;
out vec4 outColor;
uniform sampler2D u_scene;                          // the finished frame, display-encoded sRGB
// The (L*, C*) grid, NC wide (x = C* step) by NL high (y = L* step): R = dL,
// the L* offset, G = cr, the chroma ratio (camera-match.ts cameraMatchGridTexels).
uniform sampler2D u_cmGrid;
// The hue sectors, NH x 1: R = hs, the hue shift in degrees.
uniform sampler2D u_cmHue;
const int NL = ${CAMERA_MATCH_L_STEPS};
const int NC = ${CAMERA_MATCH_C_STEPS};
const int NH = ${CAMERA_MATCH_H_SECTORS};
const float L_PITCH = ${glslFloat(CAMERA_MATCH_L_PITCH)};
const float C_PITCH = ${glslFloat(CAMERA_MATCH_C_PITCH)};
const float H_ORIGIN = ${glslFloat(CAMERA_MATCH_H_ORIGIN)};
const float H_PITCH = ${glslFloat(CAMERA_MATCH_H_PITCH)};
// sRGB (D65) <-> XYZ and the Lab white, the pair every dE00 in this repo was
// measured with (docs/readme/tools/quant.py srgb_to_lab).
const mat3 SRGB_TO_XYZ = ${glslMat3(SRGB_TO_XYZ)};
const mat3 XYZ_TO_SRGB = ${glslMat3(XYZ_TO_SRGB)};
const vec3 WHITE = vec3(${LAB_WHITE.map(glslFloat).join(", ")});
const float LAB_EPS = 0.008856;
const float LAB_KAPPA = 7.787;
const float LAB_OFFSET = 16.0 / 116.0;

// Both branches of a mix() are evaluated, so every pow() gets a non-negative
// base or the unselected branch's NaN would poison the result.
vec3 srgbEotf3(vec3 u) {
  return mix(u / 12.92, pow((u + 0.055) / 1.055, vec3(2.4)), step(0.04045, u));
}
vec3 srgbOetf3(vec3 u) {
  return mix(u * 12.92, 1.055 * pow(u, vec3(1.0 / 2.4)) - 0.055, step(0.0031308, u));
}
vec3 labF3(vec3 t) {
  return mix(LAB_KAPPA * t + LAB_OFFSET, pow(max(t, 0.0), vec3(1.0 / 3.0)), step(LAB_EPS, t));
}
vec3 labFInv3(vec3 f) {
  vec3 c = f * f * f;
  return mix((f - LAB_OFFSET) / LAB_KAPPA, c, step(LAB_EPS, c));
}

void main() {
  vec3 rgb = clamp(texelFetch(u_scene, ivec2(gl_FragCoord.xy), 0).rgb, 0.0, 1.0);
  vec3 f = labF3((SRGB_TO_XYZ * srgbEotf3(rgb)) / WHITE);
  float L = 116.0 * f.y - 16.0;
  float a = 500.0 * (f.x - f.y);
  float b = 200.0 * (f.y - f.z);
  float C = length(vec2(a, b));
  // atan(0, 0) is undefined here where numpy says 0; a neutral's hue is moot
  // either way since its chroma stays zero, but a NaN would not stay anything.
  float h = C > 0.0 ? degrees(atan(b, a)) : 0.0;
  h -= 360.0 * floor(h / 360.0);

  // The (L*, C*) grid: bilinear between grid points and held at the edges —
  // the fractional index is clamped to the grid, so a C* past the last column
  // reads that column (camera-match.ts cameraMatchBilinear). Hand-written
  // rather than a LINEAR sample, see the note above the shader.
  float fl = clamp(L / L_PITCH, 0.0, float(NL - 1));
  float fc = clamp(C / C_PITCH, 0.0, float(NC - 1));
  int il = min(int(floor(fl)), NL - 2);
  int ic = min(int(floor(fc)), NC - 2);
  float tl = fl - float(il);
  float tc = fc - float(ic);
  vec2 g00 = texelFetch(u_cmGrid, ivec2(ic, il), 0).rg;
  vec2 g01 = texelFetch(u_cmGrid, ivec2(ic + 1, il), 0).rg;
  vec2 g10 = texelFetch(u_cmGrid, ivec2(ic, il + 1), 0).rg;
  vec2 g11 = texelFetch(u_cmGrid, ivec2(ic + 1, il + 1), 0).rg;
  vec2 g = mix(mix(g00, g01, tc), mix(g10, g11, tc), tl);
  float dL = g.r;
  float cr = g.g;
  // The sector table, periodic: the last sector runs into the first at 360.
  float th = (h - H_ORIGIN) / H_PITCH;
  th -= float(NH) * floor(th / float(NH));
  int j = min(int(floor(th)), NH - 1);
  float hs = mix(texelFetch(u_cmHue, ivec2(j, 0), 0).r,
                 texelFetch(u_cmHue, ivec2((j + 1) % NH, 0), 0).r, th - float(j));

  // L' = L + dL(L, C); C' = C * cr(L, C), both on the original L and C;
  // h' = h + hs(h).
  float C2 = C * cr;
  float h2 = radians(h + hs);
  vec3 lab = vec3(L + dL, C2 * cos(h2), C2 * sin(h2));
  float fy = (lab.x + 16.0) / 116.0;
  vec3 xyz = labFInv3(vec3(fy + lab.y / 500.0, fy, fy - lab.z / 200.0)) * WHITE;
  vec3 lin = clamp(XYZ_TO_SRGB * xyz, 0.0, 1.0);
  outColor = vec4(srgbOetf3(lin), 1.0);
}`;

/**
 * The offscreen programs of Sony's post chain, each with the uniforms the
 * renderer resolves for it. The list lives beside the shaders rather than in
 * pipeline-renderer so a test can hold the two in lock-step: a name that drifts
 * makes getUniformLocation return null, and gl.uniform1f(null, x) is a silent
 * no-op — the stage would simply stop running with nothing logged.
 *
 * `down`/`edge`/`blur` are Clarity's alone; `compose` applies sharpening and
 * Clarity and is built whenever either is on; `spica` runs between the two and
 * only when it is on; the four `marble*` passes are the chroma cleanup, which
 * runs after the compose when the profile carries it and NR is on.
 */
export const SONY_POST_PROGRAMS = {
  down: { fsSource: CLARITY_DOWN_SHADER, uniforms: ["u_input", "u_cell", "u_taps"] },
  edge: { fsSource: CLARITY_EDGE_SHADER, uniforms: ["u_input", "u_texel", "u_threshold"] },
  blur: { fsSource: CLARITY_BLUR_SHADER, uniforms: ["u_input", "u_texel", "u_centerMix"] },
  compose: {
    fsSource: SONY_POST_SHADER,
    uniforms: ["u_scene", "u_base", "u_baseTexel", "u_sceneTexel", "u_gain", "u_knee", "u_sharpen"],
  },
  spica: {
    fsSource: SPICA_SHADER,
    uniforms: ["u_scene", "u_weights", "u_lut", "u_sceneTexel", "u_amount", "u_isoGain", "u_gainScale", "u_rangeShift"],
  },
  marbleDown: { fsSource: MARBLE_DOWN_SHADER, uniforms: ["u_scene"] },
  marbleMean: { fsSource: MARBLE_MEAN_SHADER, uniforms: ["u_input", "u_thrY", "u_thrC1", "u_thrC2"] },
  marbleBlur: { fsSource: MARBLE_BLUR_SHADER, uniforms: ["u_input", "u_centreMix"] },
  marbleCompose: {
    fsSource: MARBLE_COMPOSE_SHADER,
    uniforms: ["u_scene", "u_blur", "u_protect", "u_strength", "u_amount"],
  },
  // The two tables are samplers (units 1 and 2, bound in runCameraMatch).
  cameraMatch: { fsSource: CAMERA_MATCH_SHADER, uniforms: ["u_scene", "u_cmGrid", "u_cmHue"] },
} as const;

export type SonyPostProgramName = keyof typeof SONY_POST_PROGRAMS;

export interface PassDef {
  name: string;
  fsSource: string;
  uniforms: string[];
}

/** Single pass — all operations fused. */
export const PASSES: PassDef[] = [
  { name: "process", fsSource: PROCESS_SHADER, uniforms: [
    "u_texXform", "u_bgColor",
    "u_wbMatrix", "u_exposure", "u_sonyLinearExposure", "u_displayGamut",
    "u_highlights", "u_shadows",
    "u_vibrance", "u_saturation", "u_clarity", "u_dehaze",
    "u_tonalActive", "u_hslActive", "u_maskShift", "u_hasMask",
    "u_maskGroups", "u_maskUse", "u_maskPreview", "u_imgFromTex", "u_imgAspect",
    "u_hsl_h[0]","u_hsl_h[1]","u_hsl_h[2]","u_hsl_h[3]","u_hsl_h[4]","u_hsl_h[5]","u_hsl_h[6]","u_hsl_h[7]",
    "u_hsl_s[0]","u_hsl_s[1]","u_hsl_s[2]","u_hsl_s[3]","u_hsl_s[4]","u_hsl_s[5]","u_hsl_s[6]","u_hsl_s[7]",
    "u_hsl_l[0]","u_hsl_l[1]","u_hsl_l[2]","u_hsl_l[3]","u_hsl_l[4]","u_hsl_l[5]","u_hsl_l[6]","u_hsl_l[7]",
    "u_grad_sh_tint","u_grad_md_tint","u_grad_hl_tint",
    "u_grad_blend","u_grad_balance",
    "u_curve_lut", "u_curveActive", "u_hasProfileCurve", "u_profileCurveSrgb",
    "u_sonyChromaActive", "u_sonyCross", "u_sonyGain", "u_sonyLuma", "u_sonySat",
    "u_sonyHue", "u_sonyLevels",
    "u_sonyLumaLutActive", "u_sonyLumaLut",
    "u_sonyLumaLutAdvActive", "u_sonyLumaLutAdv", "u_sonyLumaAdvContrast", "u_sonyLumaAdvForce",
    "u_sonyCSActive", "u_sonyCS",
    "u_sonyLut3dActive", "u_sony_lut3d",
    "u_sepiaActive", "u_sepiaWeights", "u_sepia_lut",
    "u_droActive", "u_dro_lut", "u_droScale",
    "u_droGridActive", "u_dro_grid", "u_droGridDims", "u_droGridU", "u_droGridV",
    "u_dcp_hsm", "u_dcp_look", "u_dcp_match",
    "u_dcpHsmDims", "u_dcpLookDims", "u_dcpMatchDims", "u_dcpMatchActive",
    "u_lensActive", "u_lensScale", "u_lensNorm",
    ...Array.from({ length: LENS_KNOTS }, (_, k) => `u_lensDist[${k}]`),
    ...Array.from({ length: LENS_KNOTS }, (_, k) => `u_lensVig[${k}]`),
  ]},
];
