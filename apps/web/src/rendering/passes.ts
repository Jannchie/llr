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
    "u_contrast", "u_vibrance", "u_saturation",
  ]},
];
