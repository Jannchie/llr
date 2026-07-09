/**
 * Single-pass WebGL2 shader for RAW image editing.
 *
 * All per-pixel operations (WB, exposure, tonal regions, vib/sat, gamma) are
 * fused into one shader to eliminate FBO ping-pong overhead. Contrast and
 * Blacks are display-referred and baked into the tone-curve LUT (curve.ts),
 * not applied here.
 */

import { COLOR_GLSL, PROPHOTO_Y } from "./color-spaces";
import { TONAL_GLSL } from "./tonal-model";

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
uniform vec3 u_wbGain;          // relative white-balance gain (linear ProPhoto)
uniform float u_exposure;
uniform float u_highlights;
uniform float u_shadows;
uniform float u_whites;
uniform float u_vibrance;
uniform float u_saturation;
uniform float u_clarity;
uniform float u_dehaze;
uniform int u_tonalActive;      // 1 if any highlights/shadows/whites is non-zero
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
// Color Grading
uniform float u_grad_sh_h;
uniform float u_grad_sh_s;
uniform float u_grad_md_h;
uniform float u_grad_md_s;
uniform float u_grad_hl_h;
uniform float u_grad_hl_s;
uniform float u_grad_blend;
uniform float u_grad_balance;
// Tone Curve LUT (2048×1 RGB texture) — per-channel point + parametric curves,
// applied display-referred. .r/.g/.b hold the baked R/G/B channel curves.
uniform sampler2D u_curve_lut;
uniform sampler2D u_profile_lut; // DCP profile tone curve (per-channel), display rendering
uniform int u_hasProfileCurve;   // 1 if a DCP profile tone curve is available
uniform int u_viewTransform;    // 0 = Lightroom-style, 1 = AgX
uniform int u_displayGamut;     // 0 = sRGB, 1 = Display-P3
uniform vec3 u_bgColor;         // display-encoded fill for areas outside the image (crop editor)

${COLOR_GLSL}
${TONAL_GLSL}

// Sample a 2048-entry LUT: entry i holds the output for input i/2047, so map
// x onto texel centers ((x*2047 + 0.5)/2048) — sampling at x directly is off
// by up to half a texel across the range.
float lutCoord(float x) { return (x * 2047.0 + 0.5) / 2048.0; }

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

// --- Color Grading helper ---

vec3 hsvToRgb(float h, float s) {
  h = fract(h) * 6.0;
  float c = s;
  float x = c * (1.0 - abs(mod(h, 2.0) - 1.0));
  vec3 rgb;
  if (h < 1.0)      rgb = vec3(c, x, 0.0);
  else if (h < 2.0) rgb = vec3(x, c, 0.0);
  else if (h < 3.0) rgb = vec3(0.0, c, x);
  else if (h < 4.0) rgb = vec3(0.0, x, c);
  else if (h < 5.0) rgb = vec3(x, 0.0, c);
  else              rgb = vec3(c, 0.0, x);
  return rgb + (1.0 - c);
}

void main() {
  // Outside the source image (rotated/straightened corners in the crop editor):
  // paint the workspace background instead of smearing edge texels.
  if (v_texCoord.x < 0.0 || v_texCoord.x > 1.0 || v_texCoord.y < 0.0 || v_texCoord.y > 1.0) {
    outColor = vec4(u_bgColor, 1.0);
    return;
  }

  // Input is scene-linear ProPhoto (D50). Edit here in wide-gamut scene-linear.
  vec3 c = max(texture(u_input, v_texCoord).rgb, 0.0);

  // --- White Balance (relative gain, unit at temp=6500 / tint=0) ---
  c *= u_wbGain;

  // === Exposure (highlight-shouldered) + tonal region gains ===
  // One log-luminance block, applied to RGB as a single hue-preserving ratio.
  // With everything at defaults the block is an identity multiply, so skip it.
  // The branch is on uniforms — coherent across every pixel, no divergence —
  // and it avoids a needless luma round-trip on untouched frames.
  // (Contrast and Blacks are display-referred and live in the curve LUT bake.)
  if (u_exposure != 0.0 || u_tonalActive == 1) {
    float Y0 = max(ppLuma(c), 1e-6);
    float l = log2(Y0);
    // Exposure: mids move exactly +E; the stops added above EXPO_KNEE compress
    // through the shoulder so brights roll off instead of walling at clip.
    // Negative exposure stays a pure gain (as in Lightroom).
    float lOut = (u_exposure > 0.0)
      ? l + expoShoulder(l + u_exposure) - expoShoulder(l)
      : l + u_exposure;
    if (u_tonalActive == 1) {
      float pixLx = lOut - LOG2_MID;                   // stops from middle gray, post-exposure
      // Highlights responds to a pixel that is bright itself OR sits in a
      // bright neighborhood (max); Shadows is the mirror (min). This keeps
      // small speculars/windows responsive (pixel term) while dark texture
      // inside a bright region moves with the region (mask term) — local
      // contrast preserved, as in Lightroom. A blended average does neither:
      // it dilutes small features out of the window and drags region interiors
      // out of it. Log-domain blurring makes the dark side dominate the mask
      // near edges, which keeps highlight recovery from bleeding dark halos.
      float maskLx = (u_hasMask == 1)
        ? texture(u_mask_lum, v_texCoord).r + u_maskShift
        : pixLx;
      float wHi = smoothstep(HI_EDGE0, HI_EDGE1, max(pixLx, maskLx));
      float wSh = 1.0 - smoothstep(SH_EDGE0, SH_EDGE1, min(pixLx, maskLx));
      // Only *negative* Whites acts here, on pixel luma: pulling the white
      // point down means rescuing scene values above 1.0, which the view
      // transform clamps away — so recovery exists only scene-referred.
      // Blowing the whites is the opposite case: the profile tone curve
      // asymptotes below 1.0 and flattens the top stops, so no scene-referred
      // gain can move the clip point. Positive Whites is a display-referred
      // white-point scale baked into the curve LUT (curve.ts basicCurve).
      float wWh = smoothstep(WH_EDGE0, LX_WHITE, pixLx);
      lOut += (u_highlights >= 0.0 ? HI_GAIN_POS : HI_GAIN_NEG) * u_highlights * wHi
            + (u_shadows    >= 0.0 ? SH_GAIN_POS : SH_GAIN_NEG) * u_shadows    * wSh
            + WH_GAIN * min(u_whites, 0.0) * wWh;
    }
    c *= exp2(lOut - l);
  }

  // --- Clarity (local mid-tone contrast, scene-linear around mid gray) ---
  if (u_clarity != 0.0) {
    float lm = ppLuma(c); lm = lm / (lm + 0.18);      // display-ish proxy for masking
    float midMask = smoothstep(0.05, 0.45, lm) * (1.0 - smoothstep(0.55, 0.95, lm));
    c = max(c + (c - 0.18) * u_clarity * midMask * 0.6, vec3(0.0));
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
    float scale = u_saturation * (1.0 + (u_vibrance - 1.0) * w);
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
      // Band hue centres (Oklab): Red, Orange, Yellow, Green, Aqua, Blue, Purple, Magenta
      float centers[8] = float[8](0.5101, 0.9210, 1.9160, 2.4873, -2.8833, -1.6745, -1.1558, -0.5523);
      float hAdj = 0.0, sAdj = 0.0, lAdj = 0.0;
      for (int k = 0; k < 8; k++) {
        float d = h - centers[k];
        d = atan(sin(d), cos(d));                      // wrap to [-pi, pi]
        float m = max(0.0, 1.0 - abs(d) / 0.7);        // ~40deg half-width, linear falloff
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

  // --- Tone Curve (basic + parametric + per-channel point curves, display-referred) ---
  // Lightroom applies the tone curve per channel, so contrast also shifts
  // saturation. The LUT bakes basic (Contrast/Blacks) -> parametric ->
  // RGB master -> per-channel.
  c = clamp(c, 0.0, 1.0);
  c = vec3(
    texture(u_curve_lut, vec2(lutCoord(c.r), 0.5)).r,
    texture(u_curve_lut, vec2(lutCoord(c.g), 0.5)).g,
    texture(u_curve_lut, vec2(lutCoord(c.b), 0.5)).b
  );

  // --- Color Grading (display-referred split-toning) ---
  if (u_grad_blend > 0.001) {
    float lg = ppLuma(c);
    float bal = u_grad_balance * 0.5;
    float shW = clamp(1.0 - smoothstep(0.15 + bal, 0.45 + bal, lg), 0.0, 1.0);
    float hlW = clamp(smoothstep(0.55 + bal, 0.85 + bal, lg), 0.0, 1.0);
    float mdW = clamp(1.0 - shW - hlW, 0.0, 1.0);
    vec3 shC = hsvToRgb(u_grad_sh_h, u_grad_sh_s);
    vec3 mdC = hsvToRgb(u_grad_md_h, u_grad_md_s);
    vec3 hlC = hsvToRgb(u_grad_hl_h, u_grad_hl_s);
    vec3 tinted = c * shC * shW + c * mdC * mdW + c * hlC * hlW;
    c = mix(c, tinted, u_grad_blend);
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
    "u_wbGain", "u_exposure", "u_viewTransform", "u_displayGamut",
    "u_highlights", "u_shadows", "u_whites",
    "u_vibrance", "u_saturation", "u_clarity", "u_dehaze",
    "u_tonalActive", "u_hslActive", "u_maskShift", "u_hasMask",
    "u_hsl_h[0]","u_hsl_h[1]","u_hsl_h[2]","u_hsl_h[3]","u_hsl_h[4]","u_hsl_h[5]","u_hsl_h[6]","u_hsl_h[7]",
    "u_hsl_s[0]","u_hsl_s[1]","u_hsl_s[2]","u_hsl_s[3]","u_hsl_s[4]","u_hsl_s[5]","u_hsl_s[6]","u_hsl_s[7]",
    "u_hsl_l[0]","u_hsl_l[1]","u_hsl_l[2]","u_hsl_l[3]","u_hsl_l[4]","u_hsl_l[5]","u_hsl_l[6]","u_hsl_l[7]",
    "u_grad_sh_h","u_grad_sh_s","u_grad_md_h","u_grad_md_s",
    "u_grad_hl_h","u_grad_hl_s","u_grad_blend","u_grad_balance",
    "u_curve_lut", "u_hasProfileCurve",
  ]},
];
