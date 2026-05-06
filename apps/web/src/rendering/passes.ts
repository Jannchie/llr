/**
 * Multi-pass WebGL2 rendering pipeline for RAW image editing.
 *
 * Architecture inspired by Polarr Next SDK:
 * - Each pass is a self-contained shader with dedicated uniforms
 * - Ping-pong framebuffers eliminate read-back between passes
 * - Passes can be reordered/enabled independently
 *
 * Pass order:
 *   1. WB       – White balance (Kelvin → RGB multipliers)
 *   2. Exposure – Global exposure in stops
 *   3. Tone     – Highlights, shadows, whites, blacks
 *   4. Contrast – S-curve around 0.18 mid-gray
 *   5. VibSat   – Vibrance then saturation
 *   6. Gamma    – Linear → sRGB transfer function
 */

export const VERTEX_SHADER = `#version 300 es
precision highp float;
in vec2 a_position;
out vec2 v_texCoord;
void main() {
  v_texCoord = a_position * 0.5 + 0.5;
  gl_Position = vec4(a_position, 0.0, 1.0);
}`;

// --- Pass 1: White Balance ---
// Lightroom convention: higher temp → warmer image, lower temp → cooler.
// We invert the Kelvin logic so slider direction matches LR.
export const WB_SHADER = `#version 300 es
precision highp float;
in vec2 v_texCoord;
out vec3 outColor;
uniform sampler2D u_input;
uniform float u_temperature;  // 2000..12000, 6500=neutral
uniform float u_tint;         // -100..100

// Compute RGB multipliers. Higher temp → warmer (more red/yellow, less blue).
vec3 kelvinToRGB(float K) {
  // Invert K: low slider → cold image, high slider → warm image (LR convention)
  float eff = 13000.0 - K;
  float r = eff < 6500.0
    ? 1.0 + (6500.0 - eff) / 6500.0 * 0.82
    : 1.0 - (eff - 6500.0) / 5500.0 * 0.42;
  float b = eff < 6500.0
    ? 1.0 - (6500.0 - eff) / 6500.0 * 0.72
    : 1.0 + (eff - 6500.0) / 5500.0 * 0.92;
  return vec3(clamp(r, 0.2, 5.0), 1.0, clamp(b, 0.2, 5.0));
}

void main() {
  vec3 c = texture(u_input, v_texCoord).rgb;
  vec3 wb = kelvinToRGB(u_temperature);
  float tintFactor = u_tint / 100.0;
  // Positive tint → magenta (+red +blue, -green)
  // Negative tint → green (-red -blue, +green)
  float rAdj = 1.0 + tintFactor * 0.35;
  float bAdj = 1.0 + tintFactor * 0.35;
  float gAdj = 1.0 - abs(tintFactor) * 0.35;
  outColor = c * vec3(wb.r * clamp(rAdj, 0.2, 5.0), wb.g * clamp(gAdj, 0.2, 5.0), wb.b * clamp(bAdj, 0.2, 5.0));
}`;

// --- Pass 2: Exposure ---
export const EXPOSURE_SHADER = `#version 300 es
precision highp float;
in vec2 v_texCoord;
out vec3 outColor;
uniform sampler2D u_input;
uniform float u_exposure;  // stops, -5..5

void main() {
  vec3 c = texture(u_input, v_texCoord).rgb;
  outColor = c * pow(2.0, u_exposure);
}`;

// --- Pass 3: Tone (Highlights, Shadows, Whites, Blacks) ---
export const TONE_SHADER = `#version 300 es
precision highp float;
in vec2 v_texCoord;
out vec3 outColor;
uniform sampler2D u_input;
uniform float u_highlights;  // -1..1
uniform float u_shadows;     // -1..1
uniform float u_whites;      // -1..1
uniform float u_blacks;      // -1..1

const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);

void main() {
  vec3 c = texture(u_input, v_texCoord).rgb;
  float lum = dot(c, LUMA);

  // Shadows: boost dark areas (lum < 0.55)
  float shadowMask = clamp((0.55 - lum) / 0.55, 0.0, 1.0);
  c *= 1.0 + u_shadows * 0.45 * shadowMask;

  // Highlights: recover bright areas (lum > 0.45)
  float highlightMask = clamp((lum - 0.45) / 0.55, 0.0, 1.0);
  c *= 1.0 + u_highlights * 0.35 * highlightMask;

  // Whites: push brightest parts even brighter (lum > 0.7)
  float whiteMask = clamp((lum - 0.70) / 0.30, 0.0, 1.0);
  c += u_whites * 0.25 * whiteMask;

  // Blacks: lift/darken darkest parts (lum < 0.3)
  // Positive = lift blacks (brighter), negative = deepen (darker)
  float blackMask = clamp((0.30 - lum) / 0.30, 0.0, 1.0);
  c += u_blacks * 0.20 * blackMask;

  outColor = max(c, vec3(0.0));
}`;

// --- Pass 4: Contrast ---
export const CONTRAST_SHADER = `#version 300 es
precision highp float;
in vec2 v_texCoord;
out vec3 outColor;
uniform sampler2D u_input;
uniform float u_contrast;  // 0..2, 1=neutral

const float PIVOT = 0.18;
const float RADIUS = 0.5;

void main() {
  vec3 c = texture(u_input, v_texCoord).rgb;
  if (u_contrast != 1.0) {
    vec3 delta = c - vec3(PIVOT);
    float strength = (u_contrast - 1.0) * 0.3;
    vec3 falloff = max(vec3(0.0), vec3(1.0) - (delta * delta) / (RADIUS * RADIUS));
    c = max(vec3(0.0), c + strength * delta * falloff);
  }
  outColor = c;
}`;

// --- Pass 5: Vibrance + Saturation ---
export const VIBSAT_SHADER = `#version 300 es
precision highp float;
in vec2 v_texCoord;
out vec3 outColor;
uniform sampler2D u_input;
uniform float u_vibrance;   // 0..2, 1=neutral
uniform float u_saturation;  // 0..2, 1=neutral

const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);

void main() {
  vec3 c = texture(u_input, v_texCoord).rgb;
  float grey = dot(c, LUMA);
  vec3 chroma = c - vec3(grey);

  // Vibrance: affects low-saturation colors more
  if (u_vibrance != 1.0) {
    float maxChroma = max(max(abs(chroma.r), abs(chroma.g)), abs(chroma.b));
    float mutedMask = clamp(1.0 - maxChroma * 2.5, 0.0, 1.0);
    chroma *= 1.0 + (u_vibrance - 1.0) * mutedMask;
  }

  // Saturation
  if (u_saturation != 1.0) {
    chroma *= u_saturation;
  }

  outColor = grey + chroma;
}`;

// --- Pass 6: Linear → sRGB gamma ---
export const GAMMA_SHADER = `#version 300 es
precision highp float;
in vec2 v_texCoord;
out vec4 outColor;
uniform sampler2D u_input;

float linearToSRGB(float c) {
  return c <= 0.0031308 ? c * 12.92 : 1.055 * pow(c, 1.0 / 2.4) - 0.055;
}

void main() {
  vec3 c = max(texture(u_input, v_texCoord).rgb, vec3(0.0));
  outColor = vec4(linearToSRGB(c.r), linearToSRGB(c.g), linearToSRGB(c.b), 1.0);
}`;

// --- Pass definitions ---

export interface PassDef {
  name: string;
  fsSource: string;
  uniforms: string[];
}

/** Passes in execution order. All are optional; disabled passes are skipped. */
export const PASSES: PassDef[] = [
  { name: "wb",        fsSource: WB_SHADER,        uniforms: ["u_temperature", "u_tint"] },
  { name: "exposure",  fsSource: EXPOSURE_SHADER,   uniforms: ["u_exposure"] },
  { name: "tone",      fsSource: TONE_SHADER,       uniforms: ["u_highlights", "u_shadows", "u_whites", "u_blacks"] },
  { name: "contrast",  fsSource: CONTRAST_SHADER,   uniforms: ["u_contrast"] },
  { name: "vibsat",    fsSource: VIBSAT_SHADER,     uniforms: ["u_vibrance", "u_saturation"] },
  { name: "gamma",     fsSource: GAMMA_SHADER,      uniforms: [] },
] as const;
