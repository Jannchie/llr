export type EditRecipe = {
  profileId: string;
  autoTone: boolean;
  exposure: number;
  contrast: number;
  highlights: number;
  shadows: number;
  whites: number;
  blacks: number;
  temperature: number;
  tint: number;
  vibrance: number;
  saturation: number;
  clarity: number;
  dehaze: number;
  sharpen: number;
  noiseReduction: number;
  denoise: DenoiseRecipe;
  hsl: HslRecipe;
  toneCurve: TonePoint[];
};

// AI RAW-domain denoise. Applied in the Python worker on the Bayer mosaic before
// demosaic (not in the WebGL pipeline), so it parameterises render-linear rather
// than the edit shader. `amount` is 0..100 (UI scale); the web normalises to
// 0..1 when calling the API. `model` selects the backend ("wavelet" classical
// default; neural backends added later).
export type DenoiseRecipe = {
  enabled: boolean;
  model: string;
  amount: number;
};

export type HslRecipe = {
  red: HslChannel;
  orange: HslChannel;
  yellow: HslChannel;
  green: HslChannel;
  aqua: HslChannel;
  blue: HslChannel;
  purple: HslChannel;
  magenta: HslChannel;
};

export type HslChannel = {
  hue: number;
  saturation: number;
  luminance: number;
};

export type TonePoint = {
  x: number;
  y: number;
};

export type ImageProfile = {
  id: string;
  name: string;
  description: string;
  baseRecipe: EditRecipe;
};

export const neutralHsl: HslRecipe = {
  red: { hue: 0, saturation: 0, luminance: 0 },
  orange: { hue: 0, saturation: 0, luminance: 0 },
  yellow: { hue: 0, saturation: 0, luminance: 0 },
  green: { hue: 0, saturation: 0, luminance: 0 },
  aqua: { hue: 0, saturation: 0, luminance: 0 },
  blue: { hue: 0, saturation: 0, luminance: 0 },
  purple: { hue: 0, saturation: 0, luminance: 0 },
  magenta: { hue: 0, saturation: 0, luminance: 0 }
};

export const neutralRecipe: EditRecipe = {
  profileId: "neutral",
  autoTone: false,
  exposure: 0,
  contrast: 0,
  highlights: 0,
  shadows: 0,
  whites: 0,
  blacks: 0,
  temperature: 0,
  tint: 0,
  vibrance: 0,
  saturation: 0,
  clarity: 0,
  dehaze: 0,
  sharpen: 0,
  noiseReduction: 0,
  denoise: { enabled: false, model: "wavelet", amount: 100 },
  hsl: neutralHsl,
  toneCurve: [
    { x: 0, y: 0 },
    { x: 255, y: 255 }
  ]
};

export function mergeRecipe(base: EditRecipe, override: Partial<EditRecipe>): EditRecipe {
  return {
    ...base,
    ...override,
    hsl: {
      ...base.hsl,
      ...override.hsl
    },
    denoise: {
      ...base.denoise,
      ...override.denoise
    },
    toneCurve: override.toneCurve ?? base.toneCurve
  };
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}
