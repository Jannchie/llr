/**
 * Single-pass WebGL2 shader for RAW image editing.
 *
 * All per-pixel operations (WB, exposure, tonal regions, vib/sat, gamma) are
 * fused into one shader to eliminate FBO ping-pong overhead. Contrast and
 * Blacks are display-referred and baked into the tone-curve LUT (curve.ts),
 * not applied here.
 */

import { COLOR_GLSL, PROPHOTO_Y, glslFloat } from "./color-spaces";
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
uniform sampler2D u_profile_lut; // DCP profile tone curve (per-channel), display rendering
uniform int u_hasProfileCurve;   // 1 if a DCP profile tone curve is available
uniform int u_viewTransform;    // 0 = Lightroom-style, 1 = AgX
uniform int u_displayGamut;     // 0 = sRGB, 1 = Display-P3
uniform vec3 u_bgColor;         // display-encoded fill for areas outside the image (crop editor)
// Lens corrections (per-shot radial tables from the RAW's metadata; lens.ts).
// Both tables sit on knots (i+0.5)/15 in radius normalised to the source
// half-diagonal. u_lensDist is the sampling factor toward the recorded frame
// (corrected r fetches r*f), u_lensVig the linear-light gain at the recorded
// radius. u_lensScale is the pincushion fill scale (lens.ts lensFillScale),
// u_lensNorm = 2*(w,h)/diagonal so the frame corner lands at radius 1.
uniform int u_lensActive;
uniform float u_lensDist[16];
uniform float u_lensVig[16];
uniform float u_lensScale;
uniform vec2 u_lensNorm;

${COLOR_GLSL}
${TONAL_GLSL}
${HSL_GLSL}
${LUT_GLSL}

// ===== View transforms: scene-linear ProPhoto -> display-linear ProPhoto [0,1] =====

// (a) Lightroom-style: hue-stable luminance shoulder + highlight desaturation.
vec3 viewTransformLR(vec3 c) {
  c = max(c, 0.0);
  if (u_hasProfileCurve == 1) {
    // The DCP profile tone curve IS the camera's display rendering — apply it per
    // channel (as Adobe/ACR do). This matches the camera/"official" look closely.
    return vec3(
      texture(u_profile_lut, vec2(lutCoord(clamp(c.r, 0.0, 1.0)), 0.5)).r,
      texture(u_profile_lut, vec2(lutCoord(clamp(c.g, 0.0, 1.0)), 0.5)).r,
      texture(u_profile_lut, vec2(lutCoord(clamp(c.b, 0.0, 1.0)), 0.5)).r
    );
  }
  // Fallback (no profile curve): identity here; the display sRGB encode supplies the
  // gamma so mid gray (0.18) lands at ~0.46 and white reaches white.
  return c;
}

// (b) AgX (Troy Sobotka / Blender 4.0): per-channel sigmoid in an inset basis.
const float AGX_MIN_EV = -12.47393;
const float AGX_MAX_EV = 4.026069;
vec3 agxContrast(vec3 x) {
  vec3 x2 = x * x;
  vec3 x4 = x2 * x2;
  return 15.5 * x4 * x2 - 40.14 * x4 * x + 31.96 * x4
       - 6.868 * x2 * x + 0.4298 * x2 + 0.1191 * x - 0.00232;
}
vec3 viewTransformAgX(vec3 c) {
  vec3 v = AGX_INSET_FROM_PROPHOTO * max(c, 0.0);
  v = clamp((log2(max(v, 1e-10)) - AGX_MIN_EV) / (AGX_MAX_EV - AGX_MIN_EV), 0.0, 1.0);
  v = agxContrast(v);
  vec3 rec709 = pow(max(AGX_OUTSET * v, 0.0), vec3(2.2));  // -> display-linear Rec.709
  return SRGB_TO_PROPHOTO * rec709;                        // -> display-linear ProPhoto
}

vec3 viewTransform(vec3 c) {
  return (u_viewTransform == 1) ? viewTransformAgX(c) : viewTransformLR(c);
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

// Evaluate a 16-knot lens table at normalised radius r. Knots at (i+0.5)/15;
// outside the knot range clamp to the nearest knot (lens.ts lensInterp is the
// tested TS mirror of this function).
float lensInterp(float table[16], float r) {
  float t = clamp(r * 15.0 - 0.5, 0.0, 15.0);
  int i = int(min(t, 14.0));
  return mix(table[i], table[i + 1], t - float(i));
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
    float C = length(lab.yz);
    if (C > 1e-4) {
      float h = atan(lab.z, lab.y);                    // Oklab hue, radians
      // Triangular partition-of-unity band weights (HSL_GLSL, hsl-bands.ts):
      // adjacent band values interpolate exactly, with no dead zones between
      // widely spaced centres and no overshoot where bands used to overlap.
      float hAdj = 0.0, sAdj = 0.0, lAdj = 0.0;
      for (int k = 0; k < 8; k++) {
        float m = hslBandWeight(k, h);
        hAdj += m * u_hsl_h[k];
        sAdj += m * u_hsl_s[k];
        lAdj += m * u_hsl_l[k];
      }
      float newH = h + hAdj * 0.5;                     // hue rotation (radians)
      float newC = C * (1.0 + sAdj);                   // per-band saturation
      lab.x = max(lab.x + lAdj * 0.15, 0.0);           // per-band luminance
      lab.y = newC * cos(newH);
      lab.z = newC * sin(newH);
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

export interface PassDef {
  name: string;
  fsSource: string;
  uniforms: string[];
}

/** Single pass — all operations fused. */
export const PASSES: PassDef[] = [
  { name: "process", fsSource: PROCESS_SHADER, uniforms: [
    "u_texXform", "u_bgColor",
    "u_wbMatrix", "u_exposure", "u_viewTransform", "u_displayGamut",
    "u_highlights", "u_shadows",
    "u_vibrance", "u_saturation", "u_clarity", "u_dehaze",
    "u_tonalActive", "u_hslActive", "u_maskShift", "u_hasMask",
    "u_hsl_h[0]","u_hsl_h[1]","u_hsl_h[2]","u_hsl_h[3]","u_hsl_h[4]","u_hsl_h[5]","u_hsl_h[6]","u_hsl_h[7]",
    "u_hsl_s[0]","u_hsl_s[1]","u_hsl_s[2]","u_hsl_s[3]","u_hsl_s[4]","u_hsl_s[5]","u_hsl_s[6]","u_hsl_s[7]",
    "u_hsl_l[0]","u_hsl_l[1]","u_hsl_l[2]","u_hsl_l[3]","u_hsl_l[4]","u_hsl_l[5]","u_hsl_l[6]","u_hsl_l[7]",
    "u_grad_sh_tint","u_grad_md_tint","u_grad_hl_tint",
    "u_grad_blend","u_grad_balance",
    "u_curve_lut", "u_curveActive", "u_hasProfileCurve",
    "u_lensActive", "u_lensScale", "u_lensNorm",
    ...Array.from({ length: 16 }, (_, k) => `u_lensDist[${k}]`),
    ...Array.from({ length: 16 }, (_, k) => `u_lensVig[${k}]`),
  ]},
];
