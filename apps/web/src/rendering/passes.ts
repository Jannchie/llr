/**
 * Single-pass WebGL2 shader for RAW image editing.
 *
 * All per-pixel operations (WB, exposure, tone, contrast, vib/sat, gamma)
 * are fused into one shader to eliminate FBO ping-pong overhead.
 */

import { COLOR_GLSL } from "./color-spaces";

export const VERTEX_SHADER = `#version 300 es
precision highp float;
in vec2 a_position;
out vec2 v_texCoord;
void main() {
  v_texCoord = a_position * 0.5 + 0.5;
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
uniform float u_blacks;
uniform float u_contrast;
uniform float u_vibrance;
uniform float u_saturation;
uniform float u_clarity;
uniform float u_dehaze;
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

${COLOR_GLSL}

// ===== View transforms: scene-linear ProPhoto -> display-linear ProPhoto [0,1] =====

// (a) Lightroom-style: hue-stable luminance shoulder + highlight desaturation.
vec3 viewTransformLR(vec3 c) {
  c = max(c, 0.0);
  if (u_hasProfileCurve == 1) {
    // The DCP profile tone curve IS the camera's display rendering — apply it per
    // channel (as Adobe/ACR do). This matches the camera/"official" look closely.
    return vec3(
      texture(u_profile_lut, vec2(clamp(c.r, 0.0, 1.0), 0.5)).r,
      texture(u_profile_lut, vec2(clamp(c.g, 0.0, 1.0), 0.5)).r,
      texture(u_profile_lut, vec2(clamp(c.b, 0.0, 1.0), 0.5)).r
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

// Gamut compression: bring out-of-gamut display-linear RGB back inside [0,1]^3
// by desaturating toward the equal-luminance gray. Preserves luminance and keeps
// hue far more stable than a per-channel clamp.
vec3 gamutMap(vec3 c) {
  float lo = min(min(c.r, c.g), c.b);
  float hi = max(max(c.r, c.g), c.b);
  if (lo >= 0.0 && hi <= 1.0) return c;
  float l = clamp(dot(c, vec3(0.2126, 0.7152, 0.0722)), 0.0, 1.0);
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
  // Input is scene-linear ProPhoto (D50). Edit here in wide-gamut scene-linear.
  vec3 c = max(texture(u_input, v_texCoord).rgb, 0.0);

  // --- White Balance (relative gain, unit at temp=6500 / tint=0) ---
  c *= u_wbGain;

  // --- Exposure ---
  c *= exp2(u_exposure);

  // === Tonal region adjustments on scene luminance (log-luminance masks) ===
  float Y0 = ppLuma(c);
  float Y = max(Y0, 1e-6);
  float lx = log2(Y / 0.18);                          // stops from middle gray
  // Lightroom model: Highlights/Shadows are *bumps* that taper at the extremes
  // (recover the bright/dark region without moving the clip points), while
  // Whites/Blacks are *broad ramps* pivoted at the opposite endpoint (scale a
  // wide range and set where white/black clip).
  float wHi = clamp(smoothstep(0.0, 2.0, lx) - smoothstep(3.0, 5.5, lx), 0.0, 1.0); // bright bump, white-point protected
  float wSh = 1.0 - smoothstep(-3.5, 0.0, lx);        // shadows
  float wWh = smoothstep(-2.0, 3.5, lx);              // whites: pivots at black -> broad, reaches mids, max at white
  float wBl = 1.0 - smoothstep(-5.0, -1.5, lx);       // blacks (extreme lows)
  float gain = 0.0;
  gain += (u_highlights >= 0.0 ? 0.70 : 0.90) * u_highlights * wHi;
  gain += (u_shadows    >= 0.0 ? 0.80 : 0.55) * u_shadows    * wSh;
  gain += (u_whites     >= 0.0 ? 0.70 : 0.80) * u_whites     * wWh;
  gain += (u_blacks     <= 0.0 ? 0.60 : 0.50) * u_blacks     * wBl;
  Y *= exp2(gain);

  // -- Contrast: power curve pivoting at middle gray (scene-linear) --
  float contrastPow = 1.0 + (u_contrast - 1.0) * 0.6; // u_contrast = 1 + slider/100
  Y = 0.18 * pow(max(Y / 0.18, 1e-6), contrastPow);

  // Apply the luminance change to RGB, hue-preserving in ProPhoto.
  c *= Y / max(Y0, 1e-6);

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
  {
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

  // --- Tone Curve (parametric + per-channel point curves, display-referred) ---
  // Lightroom applies the tone curve per channel, so contrast also shifts
  // saturation. The LUT bakes parametric -> RGB master -> per-channel.
  c = clamp(c, 0.0, 1.0);
  c = vec3(
    texture(u_curve_lut, vec2(c.r, 0.5)).r,
    texture(u_curve_lut, vec2(c.g, 0.5)).g,
    texture(u_curve_lut, vec2(c.b, 0.5)).b
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
  disp = gamutMap(disp);
  outColor = vec4(srgbEncode(disp), 1.0); // sRGB transfer (Display-P3 shares it)
}`;

export interface PassDef {
  name: string;
  fsSource: string;
  uniforms: string[];
}

/** Single pass — all operations fused. */
export const PASSES: PassDef[] = [
  { name: "process", fsSource: PROCESS_SHADER, uniforms: [
    "u_wbGain", "u_exposure", "u_viewTransform", "u_displayGamut",
    "u_highlights", "u_shadows", "u_whites", "u_blacks",
    "u_contrast", "u_vibrance", "u_saturation", "u_clarity", "u_dehaze",
    "u_hsl_h[0]","u_hsl_h[1]","u_hsl_h[2]","u_hsl_h[3]","u_hsl_h[4]","u_hsl_h[5]","u_hsl_h[6]","u_hsl_h[7]",
    "u_hsl_s[0]","u_hsl_s[1]","u_hsl_s[2]","u_hsl_s[3]","u_hsl_s[4]","u_hsl_s[5]","u_hsl_s[6]","u_hsl_s[7]",
    "u_hsl_l[0]","u_hsl_l[1]","u_hsl_l[2]","u_hsl_l[3]","u_hsl_l[4]","u_hsl_l[5]","u_hsl_l[6]","u_hsl_l[7]",
    "u_grad_sh_h","u_grad_sh_s","u_grad_md_h","u_grad_md_s",
    "u_grad_hl_h","u_grad_hl_s","u_grad_blend","u_grad_balance",
    "u_curve_lut", "u_hasProfileCurve",
  ]},
];
