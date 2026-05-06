import { ImageProfile, mergeRecipe, neutralRecipe } from "@llr/core";

export const profiles: ImageProfile[] = [
  {
    id: "standard",
    name: "Standard",
    description: "A stable RAW starting point with fixed base rendering and no per-image auto tone.",
    baseRecipe: mergeRecipe(neutralRecipe, {
      profileId: "standard",
      autoTone: false,
      sharpen: 18,
      toneCurve: [
        { x: 0, y: 0 },
        { x: 32, y: 22 },
        { x: 128, y: 130 },
        { x: 224, y: 236 },
        { x: 255, y: 255 }
      ]
    })
  },
  {
    id: "neutral",
    name: "Neutral",
    description: "A low-opinion starting point for technical RAW development.",
    baseRecipe: neutralRecipe
  },
  {
    id: "sony-fl-like",
    name: "Sony FL-like",
    description: "A gentle film-like baseline inspired by Sony FL, tuned as an independent approximation.",
    baseRecipe: mergeRecipe(neutralRecipe, {
      profileId: "sony-fl-like",
      contrast: -6,
      highlights: -18,
      shadows: 10,
      whites: -4,
      blacks: 7,
      temperature: -2,
      vibrance: 8,
      saturation: -5,
      clarity: 5,
      hsl: {
        red: { hue: 0, saturation: -4, luminance: 2 },
        orange: { hue: -2, saturation: -6, luminance: 4 },
        yellow: { hue: -5, saturation: -8, luminance: 2 },
        green: { hue: -8, saturation: -10, luminance: 4 },
        aqua: { hue: -4, saturation: 4, luminance: 2 },
        blue: { hue: -3, saturation: 6, luminance: -2 },
        purple: { hue: 0, saturation: -6, luminance: 0 },
        magenta: { hue: 0, saturation: -6, luminance: 0 }
      },
      toneCurve: [
        { x: 0, y: 7 },
        { x: 45, y: 42 },
        { x: 128, y: 132 },
        { x: 210, y: 216 },
        { x: 255, y: 249 }
      ]
    })
  }
];

export function getProfile(id: string): ImageProfile {
  const profile = profiles.find((item) => item.id === id);

  if (!profile) {
    throw new Error(`Unknown profile: ${id}`);
  }

  return profile;
}
