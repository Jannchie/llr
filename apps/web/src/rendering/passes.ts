/**
 * Single-pass WebGL2 shader for RAW image editing.
 *
 * All per-pixel operations (WB, exposure, tonal regions, vib/sat, gamma) are
 * fused into one shader to eliminate FBO ping-pong overhead. Contrast and
 * Blacks are display-referred and baked into the tone-curve LUT (curve.ts),
 * not applied here.
 */

import { COLOR_GLSL, PROPHOTO_Y, REC709_Y, glslFloat } from "./color-spaces";
import { LENS_KNOTS } from "./lens";
import { LUT_GLSL } from "./curve";
import { HSL_GLSL } from "./hsl-bands";
import { TONAL_GLSL } from "./tonal-model";

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
out vec2 v_texCoord;
// Affine map output-quad -> source texcoords (crop / straighten / flip / rotate).
// Identity for an un-cropped frame; built on the CPU in crop.ts.
uniform mat3 u_texXform;
void main() {
  vec2 uv = a_position * 0.5 + 0.5;
  vec2 p = vec2(uv.x, 1.0 - uv.y);          // output-frame coord, y-down
  vec3 t = u_texXform * vec3(p, 1.0);
  v_texCoord = t.xy;
  gl_Position = vec4(a_position, 0.0, 1.0);
}`;

export const PROCESS_SHADER = `#version 300 es
precision highp float;
// sampler3D has no default precision in GLSL ES 3.0 (unlike sampler2D), so
// omitting this is a compile error, not a silent downgrade.
precision highp sampler3D;
in vec2 v_texCoord;
out vec4 outColor;
uniform sampler2D u_input;
uniform mat3 u_wbMatrix;        // relative WB: Bradford adaptation in linear ProPhoto
uniform float u_exposure;
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
// YGamma, which runs between the two chroma halves: the shot's Fade setting,
// as a pivot and a contrast. Fade 0 is pivot 0, so it degenerates to a gain.
uniform vec2 u_sonyLuma;        // (pivot, contrast)
// The Saturation slider. u_sonyGain arrives already divided by it; this
// multiplies the chroma back after the clamp, exactly as the engine's separate
// ZcTaskSIMDHueSaturation stage does. The two nearly cancel — the clamp in
// between is the whole visible effect.
uniform float u_sonySat;
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

${COLOR_GLSL}
${TONAL_GLSL}
${HSL_GLSL}
${LUT_GLSL}

// ===== View transforms: scene-linear ProPhoto -> display-linear ProPhoto [0,1] =====

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
  // YGamma, which the engine runs here, between the two halves: it pulls Y
  // toward a pivot and clips, and leaves both chroma planes bit-identical
  // (worker sony/chroma.py). This is where the in-camera Fade setting lives.
  y = clamp((y - u_sonyLuma.x) * u_sonyLuma.y + u_sonyLuma.x, 0.0, 1.0);
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

// Evaluate a lens table at normalised radius r. Knots at (i+0.5)/N; outside the
// knot range clamp to the nearest knot. lens.ts lensInterp is the tested TS
// mirror of this function, and N is injected from there so the two cannot drift.
float lensInterp(float table[${LENS_KNOTS}], float r) {
  float t = clamp(r * ${glslFloat(LENS_KNOTS)} - 0.5, 0.0, ${glslFloat(LENS_KNOTS - 1)});
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

void main() {
  // Outside the source image (rotated/straightened corners in the crop editor):
  // paint the workspace background instead of smearing edge texels.
  if (v_texCoord.x < 0.0 || v_texCoord.x > 1.0 || v_texCoord.y < 0.0 || v_texCoord.y > 1.0) {
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

  // --- DRO --- Before white balance and before exposure, matching where the
  // engine's stage sits. One gain for all three channels, so it never shifts
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

  // --- White Balance (Bradford adaptation, identity at temp=6500 / tint=0) ---
  c = max(u_wbMatrix * c, 0.0);

  // === Exposure (highlight-shouldered) + tonal region gains + Clarity ===
  // One log-luminance block, applied to RGB as a single hue-preserving ratio.
  // With everything at defaults the block is an identity multiply, so skip it.
  // The branch is on uniforms — coherent across every pixel, no divergence —
  // and it avoids a needless luma round-trip on untouched frames.
  // (Contrast and Blacks are display-referred and live in the curve LUT bake.)
  bool clarityLocal = (u_clarity != 0.0 && u_hasMask == 1);
  if (u_exposure != 0.0 || u_tonalActive == 1 || clarityLocal) {
    float Y0 = max(ppLuma(c), 1e-6);
    float l = log2(Y0);
    // Exposure: mids move exactly +E; the stops added above EXPO_KNEE compress
    // through the shoulder so brights roll off instead of walling at clip.
    // Negative exposure stays a pure gain (as in Lightroom).
    float lOut = (u_exposure > 0.0)
      ? l + expoShoulder(l + u_exposure) - expoShoulder(l)
      : l + u_exposure;
    if (u_tonalActive == 1 || clarityLocal) {
      float pixLx = lOut - LOG2_MID;                   // stops from middle gray, post-exposure
      // Blurred neighborhood log-luma (see MASK_* shaders), shifted for
      // exposure. Guarded by its consumers: an exposure-only edit must not
      // pay a per-pixel texture fetch it never reads.
      float maskLx = (u_hasMask == 1)
        ? texture(u_mask_lum, lensUV).r + u_maskShift   // mask lives in recorded-frame UV: track the lens warp
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
        lOut = log2(max(toneRegions(Y, u_highlights, u_shadows,
                                    max(Y, Ym), min(Y, Ym)), 1e-6));
      }
      // Clarity: local mid-tone contrast — amplify the pixel's deviation from
      // its blurred neighborhood (clarityShift in TONAL_GLSL; window + midtone
      // weight keep edges and clip points from haloing/shifting). Without the
      // mask there is no neighborhood signal, so the slider is inert — same
      // degradation story as the region weights above falling back per-pixel.
      if (clarityLocal) {
        lOut += clarityShift(pixLx, maskLx, u_clarity);
      }
    }
    c *= exp2(lOut - l);
  }

  // --- Dehaze (global contrast + saturation boost) ---
  if (u_dehaze != 0.0) {
    c = max(c + (c - 0.18) * u_dehaze * 0.5, vec3(0.0));
    float l3 = ppLuma(c);
    vec3 ch3 = c - vec3(l3);
    c = max(l3 + ch3 * (1.0 + u_dehaze * 0.25), vec3(0.0));
  }

  // --- Vibrance + Saturation (Oklab chroma — hue-stable, no skew) ---
  if (u_vibrance != 1.0 || u_saturation != 1.0) {
    vec3 lab = proPhotoToOklab(c);
    float C = length(lab.yz);
    float w = 1.0 - smoothstep(0.0, 0.35, C);          // boost low-chroma (vibrance) more
    // Skin protection (vibrance only, as in Lightroom — the Saturation slider
    // stays global): damp the vibrance term inside the skin-tone window
    // (SKIN_* constants and window shape from hsl-bands.ts). Skipped when
    // only Saturation is in play — the term multiplies to zero anyway.
    float skinW = (u_vibrance != 1.0)
      ? hueWindow(atan(lab.z, lab.y), SKIN_HUE, SKIN_HUE_HALF)
        * smoothstep(SKIN_C0, SKIN_C1, C) * (1.0 - smoothstep(SKIN_C2, SKIN_C3, C))
      : 0.0;
    float scale = u_saturation * (1.0 + (u_vibrance - 1.0) * w * (1.0 - SKIN_DAMP * skinW));
    lab.yz *= scale;
    c = max(oklabToProPhoto(lab), 0.0);
  }

  // --- HSL Color Mixer (OkLCh per-band, hue-stable) ---
  // Skip the Oklab round-trip + 8-band hue loop entirely when no band is touched
  // (the default). This is the shader's most expensive block, and like the
  // vibrance/saturation guard above it must not run an identity round-trip per
  // pixel every frame. Branch is on a uniform, so it is coherent across the draw.
  if (u_hslActive == 1) {
    vec3 lab = proPhotoToOklab(c);
    // Which band a pixel belongs to is a property of its *neighbourhood*, not
    // of the pixel: chroma noise swings a single pixel's hue by more than the
    // Red-Orange centres are apart (0.41 rad, the tightest pair), so per-pixel
    // band weights speckle — adjacent pixels land in different bands and only
    // some of them take the Luminance move. Take the selection colour from
    // four bilinear taps around the pixel instead. The adjustment still applies
    // to this pixel's own colour, so detail and edges survive.
    //
    // Sampling after white balance is enough: everything between it and here
    // (exposure, tonal regions, vibrance) scales luminance or chroma without
    // rotating hue, and the gate reads the ratio C/L, which those scalings
    // leave near enough alone.
    // Radius: half an output pixel, floored at 0.75 source texels. The preview
    // renders at previewScale, so in a fit-to-window view one output pixel
    // spans several texels; fwidth follows that and halves the worst-case
    // residue there (90th-percentile grain 0.21 -> 0.10), while at 1:1 and on
    // export it drops to the floor and the two agree. The floor is 0.75 rather
    // than 0.5 because the source texture falls back to NEAREST when RGB32F is
    // not filterable: at 0.5 the taps can all round back to the centre texel
    // and average nothing.
    vec2 ts = max(0.75 / vec2(textureSize(u_input, 0)), 0.5 * fwidth(lensUV));
    vec3 nb = texture(u_input, lensUV + vec2( ts.x,  ts.y)).rgb
            + texture(u_input, lensUV + vec2(-ts.x,  ts.y)).rgb
            + texture(u_input, lensUV + vec2( ts.x, -ts.y)).rgb
            + texture(u_input, lensUV + vec2(-ts.x, -ts.y)).rgb;
    // Same fetch -> gain -> WB chain as the main sample above, on the average.
    vec3 labSel = proPhotoToOklab(max(u_wbMatrix * (max(nb * 0.25, 0.0) * lensGain), 0.0));
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
/** `cfg[0xc4] / 2048` — turns a trapezoid's [48, 512] into the detail gain. */
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
const vec3 LUMA = vec3(${glslFloat(REC709_Y[0])}, ${glslFloat(REC709_Y[1])}, ${glslFloat(REC709_Y[2])});
const float WHITE = ${glslFloat(SPICA_WHITE)};
const float RANGE_THRESHOLD = ${glslFloat(SPICA_RANGE_THRESHOLD)};
const float GAIN_SCALE = ${glslFloat(SPICA_GAIN_SCALE)};
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
  float gain = GAIN_SCALE * min(
    min(spicaTrap(abs(detail) / ${glslFloat(SPICA_DETAIL_DIVISOR)}, C_DETAIL, C_DETAIL_V),
        spicaTrap(range, C_RANGE, C_RANGE_V)),
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

/**
 * The five offscreen programs of Sony's post chain, each with the uniforms the
 * renderer resolves for it. The list lives beside the shaders rather than in
 * pipeline-renderer so a test can hold the two in lock-step: a name that drifts
 * makes getUniformLocation return null, and gl.uniform1f(null, x) is a silent
 * no-op — the stage would simply stop running with nothing logged.
 *
 * `down`/`edge`/`blur` are Clarity's alone; `compose` applies sharpening and
 * Clarity and is built whenever either is on; `spica` runs between the two and
 * only when it is on.
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
    uniforms: ["u_scene", "u_weights", "u_lut", "u_sceneTexel", "u_amount", "u_isoGain"],
  },
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
    "u_wbMatrix", "u_exposure", "u_displayGamut",
    "u_highlights", "u_shadows",
    "u_vibrance", "u_saturation", "u_clarity", "u_dehaze",
    "u_tonalActive", "u_hslActive", "u_maskShift", "u_hasMask",
    "u_hsl_h[0]","u_hsl_h[1]","u_hsl_h[2]","u_hsl_h[3]","u_hsl_h[4]","u_hsl_h[5]","u_hsl_h[6]","u_hsl_h[7]",
    "u_hsl_s[0]","u_hsl_s[1]","u_hsl_s[2]","u_hsl_s[3]","u_hsl_s[4]","u_hsl_s[5]","u_hsl_s[6]","u_hsl_s[7]",
    "u_hsl_l[0]","u_hsl_l[1]","u_hsl_l[2]","u_hsl_l[3]","u_hsl_l[4]","u_hsl_l[5]","u_hsl_l[6]","u_hsl_l[7]",
    "u_grad_sh_tint","u_grad_md_tint","u_grad_hl_tint",
    "u_grad_blend","u_grad_balance",
    "u_curve_lut", "u_curveActive", "u_hasProfileCurve", "u_profileCurveSrgb",
    "u_sonyChromaActive", "u_sonyCross", "u_sonyGain", "u_sonyLuma", "u_sonySat",
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
