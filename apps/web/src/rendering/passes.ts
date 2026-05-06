/**
 * Single-pass WebGL2 shader for RAW image editing.
 *
 * All per-pixel operations (WB, exposure, tone, contrast, vib/sat, gamma)
 * are fused into one shader to eliminate FBO ping-pong overhead.
 */

export const VERTEX_SHADER = `#version 300 es
precision highp float;
in vec2 a_position;
out vec2 v_texCoord;
void main() {
  v_texCoord = a_position * 0.5 + 0.5;
  gl_Position = vec4(a_position, 0.0, 1.0);
}`;

const LUMA = "const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);";

export const PROCESS_SHADER = `#version 300 es
precision highp float;
in vec2 v_texCoord;
out vec4 outColor;
uniform sampler2D u_input;
uniform float u_temperature;
uniform float u_tint;
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

${LUMA}

const float PIVOT = 0.18;
const float RADIUS = 0.5;

vec3 kelvinToRGB(float K) {
  float eff = 13000.0 - K;
  float r = eff < 6500.0
    ? 1.0 + (6500.0 - eff) / 6500.0 * 0.82
    : 1.0 - (eff - 6500.0) / 5500.0 * 0.42;
  float b = eff < 6500.0
    ? 1.0 - (6500.0 - eff) / 6500.0 * 0.72
    : 1.0 + (eff - 6500.0) / 5500.0 * 0.92;
  return vec3(clamp(r, 0.2, 5.0), 1.0, clamp(b, 0.2, 5.0));
}

float linearToSRGB(float c) {
  return c <= 0.0031308 ? c * 12.92 : 1.055 * pow(c, 1.0 / 2.4) - 0.055;
}

// --- HSL / Color Grading helpers ---

float rgbHue(vec3 c) {
  float mx = max(max(c.r, c.g), c.b);
  float mn = min(min(c.r, c.g), c.b);
  float ch = mx - mn;
  if (ch < 1e-5) return 0.0;
  float h;
  if (c.r >= mx)      h = (c.g - c.b) / ch;
  else if (c.g >= mx) h = 2.0 + (c.b - c.r) / ch;
  else                h = 4.0 + (c.r - c.g) / ch;
  return fract(h / 6.0);
}

float hueMask(float h, float center) {
  float d = abs(h - center);
  d = min(d, 1.0 - d);
  return clamp(1.0 - d / 0.07, 0.0, 1.0);
}

// Rodrigues rotation around the luminance axis (1,1,1)
vec3 hueRotate(vec3 c, float theta) {
  if (abs(theta) < 1e-5) return c;
  float co = cos(theta);
  float si = sin(theta);
  float a = (1.0 - co) / 3.0;
  float b = si / 1.7320508; // sqrt(3)
  return vec3(
    c.r * (co + a) + c.g * (a - b) + c.b * (a + b),
    c.r * (a + b) + c.g * (co + a) + c.b * (a - b),
    c.r * (a - b) + c.g * (a + b) + c.b * (co + a)
  );
}

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
  vec3 c = texture(u_input, v_texCoord).rgb;

  // --- White Balance ---
  vec3 wb = kelvinToRGB(u_temperature);
  float tintFactor = u_tint / 100.0;
  float rAdj = 1.0 + tintFactor * 0.35;
  float bAdj = 1.0 + tintFactor * 0.35;
  float gAdj = 1.0 - abs(tintFactor) * 0.35;
  c *= vec3(wb.r * clamp(rAdj, 0.2, 5.0), wb.g * clamp(gAdj, 0.2, 5.0), wb.b * clamp(bAdj, 0.2, 5.0));

  // --- Exposure ---
  c *= pow(2.0, u_exposure);

  // --- Tone (Highlights, Shadows, Whites, Blacks) ---
  float lum = dot(c, LUMA);
  float shadowMask = clamp((0.55 - lum) / 0.55, 0.0, 1.0);
  c *= 1.0 + u_shadows * 0.45 * shadowMask;
  float highlightMask = clamp((lum - 0.45) / 0.55, 0.0, 1.0);
  c *= 1.0 + u_highlights * 0.35 * highlightMask;
  float whiteMask = clamp((lum - 0.70) / 0.30, 0.0, 1.0);
  c += u_whites * 0.25 * whiteMask;
  float blackMask = clamp((0.30 - lum) / 0.30, 0.0, 1.0);
  c += u_blacks * 0.20 * blackMask;
  c = max(c, vec3(0.0));

  // --- Contrast ---
  if (u_contrast != 1.0) {
    vec3 delta = c - vec3(PIVOT);
    float strength = (u_contrast - 1.0) * 0.3;
    vec3 falloff = max(vec3(0.0), vec3(1.0) - (delta * delta) / (RADIUS * RADIUS));
    c = max(vec3(0.0), c + strength * delta * falloff);
  }

  // --- Vibrance + Saturation ---
  float grey = dot(c, LUMA);
  vec3 chroma = c - vec3(grey);
  if (u_vibrance != 1.0) {
    float maxChroma = max(max(abs(chroma.r), abs(chroma.g)), abs(chroma.b));
    float mutedMask = clamp(1.0 - maxChroma * 2.5, 0.0, 1.0);
    chroma *= 1.0 + (u_vibrance - 1.0) * mutedMask;
  }
  if (u_saturation != 1.0) {
    chroma *= u_saturation;
  }
  c = max(grey + chroma, vec3(0.0));

  // --- Clarity (mid-tone contrast) ---
  if (u_clarity != 0.0) {
    float lum2 = dot(c, LUMA);
    float midMask = clamp(1.0 - abs(lum2 - 0.5) * 2.0, 0.0, 1.0);
    c = max(c + (c - 0.5) * u_clarity * midMask * 0.5, vec3(0.0));
  }

  // --- Dehaze (global contrast + saturation boost) ---
  if (u_dehaze != 0.0) {
    c = max(c + (c - 0.5) * u_dehaze * 0.35, vec3(0.0));
    float l3 = dot(c, LUMA);
    vec3 ch3 = c - vec3(l3);
    c = max(l3 + ch3 * (1.0 + u_dehaze * 0.25), vec3(0.0));
  }

  // --- HSL Color Mixer ---
  {
    float h = rgbHue(c);
    float m0 = hueMask(h, 0.000); // Red
    float m1 = hueMask(h, 0.069); // Orange
    float m2 = hueMask(h, 0.167); // Yellow
    float m3 = hueMask(h, 0.333); // Green
    float m4 = hueMask(h, 0.500); // Aqua
    float m5 = hueMask(h, 0.667); // Blue
    float m6 = hueMask(h, 0.778); // Purple
    float m7 = hueMask(h, 0.889); // Magenta

    float hAdj = m0*u_hsl_h[0] + m1*u_hsl_h[1] + m2*u_hsl_h[2] + m3*u_hsl_h[3]
               + m4*u_hsl_h[4] + m5*u_hsl_h[5] + m6*u_hsl_h[6] + m7*u_hsl_h[7];
    float sAdj = m0*u_hsl_s[0] + m1*u_hsl_s[1] + m2*u_hsl_s[2] + m3*u_hsl_s[3]
               + m4*u_hsl_s[4] + m5*u_hsl_s[5] + m6*u_hsl_s[6] + m7*u_hsl_s[7];
    float lAdj = m0*u_hsl_l[0] + m1*u_hsl_l[1] + m2*u_hsl_l[2] + m3*u_hsl_l[3]
               + m4*u_hsl_l[4] + m5*u_hsl_l[5] + m6*u_hsl_l[6] + m7*u_hsl_l[7];

    // Hue rotation
    if (abs(hAdj) > 0.001) c = hueRotate(c, hAdj * 0.7);
    // Saturation
    if (abs(sAdj) > 0.001) {
      float gl = dot(c, LUMA);
      c = max(vec3(gl) + (c - vec3(gl)) * (1.0 + sAdj), vec3(0.0));
    }
    // Luminance
    if (abs(lAdj) > 0.001) c = max(c + lAdj * 0.35, vec3(0.0));
  }

  // --- Color Grading ---
  if (u_grad_blend > 0.001) {
    float lg = dot(c, LUMA);
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

  // --- Gamma (Linear → sRGB) ---
  outColor = vec4(linearToSRGB(c.r), linearToSRGB(c.g), linearToSRGB(c.b), 1.0);
}`;

export interface PassDef {
  name: string;
  fsSource: string;
  uniforms: string[];
}

/** Single pass — all operations fused. */
export const PASSES: PassDef[] = [
  { name: "process", fsSource: PROCESS_SHADER, uniforms: [
    "u_temperature", "u_tint", "u_exposure",
    "u_highlights", "u_shadows", "u_whites", "u_blacks",
    "u_contrast", "u_vibrance", "u_saturation", "u_clarity", "u_dehaze",
    "u_hsl_h[0]","u_hsl_h[1]","u_hsl_h[2]","u_hsl_h[3]","u_hsl_h[4]","u_hsl_h[5]","u_hsl_h[6]","u_hsl_h[7]",
    "u_hsl_s[0]","u_hsl_s[1]","u_hsl_s[2]","u_hsl_s[3]","u_hsl_s[4]","u_hsl_s[5]","u_hsl_s[6]","u_hsl_s[7]",
    "u_hsl_l[0]","u_hsl_l[1]","u_hsl_l[2]","u_hsl_l[3]","u_hsl_l[4]","u_hsl_l[5]","u_hsl_l[6]","u_hsl_l[7]",
    "u_grad_sh_h","u_grad_sh_s","u_grad_md_h","u_grad_md_s",
    "u_grad_hl_h","u_grad_hl_s","u_grad_blend","u_grad_balance",
  ]},
];
