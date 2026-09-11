import type { MaskGroup } from "./masks";

/**
 * Recipes: the part of an edit that transfers between photos. Everything
 * tied to *where* things are in the frame stays behind — the crop, and any
 * mask group that carries a gradient — as does everything tied to the camera
 * or the file (colour engine, DCP, Creative Look, DRO, denoise), which the
 * next photo has its own answer for.
 */
export const RECIPE_KEYS = ["recipe", "hslHue", "hslSat", "hslLum", "grading", "curve", "masks"] as const;
export type RecipeKey = typeof RECIPE_KEYS[number];

/** Mask groups that select by position rather than by pixel: not portable. */
export function isPositional(g: MaskGroup): boolean {
  return g.components.some(c => c.type === "linear" || c.type === "radial");
}

export type RecipeData = Record<Exclude<RecipeKey, "masks">, unknown> & { masks: MaskGroup[] };
type Editable = Partial<Record<RecipeKey, unknown>> & { masks?: MaskGroup[] };

export function extractRecipe(s: Editable): RecipeData {
  const out = {} as RecipeData;
  for (const k of RECIPE_KEYS) if (k !== "masks") out[k] = s[k];
  out.masks = (s.masks ?? []).filter(g => !isPositional(g));
  return out;
}

/** The snapshot with the recipe laid over it; the photo's positional masks survive. */
export function applyRecipe<S extends Editable>(s: S, r: RecipeData): S {
  return { ...s, ...r, masks: [...(s.masks ?? []).filter(isPositional), ...r.masks] };
}

// Clipboard payload: tagged so a paste of unrelated text is refused.
const TAG = "llr-recipe";
export function serializeRecipe(r: RecipeData): string {
  return JSON.stringify({ [TAG]: 1, ...r });
}
export function parseRecipe(text: string): RecipeData | null {
  try {
    const o = JSON.parse(text);
    if (!o || o[TAG] !== 1 || typeof o.recipe !== "object") return null;
    return { ...o, masks: Array.isArray(o.masks) ? o.masks : [] };
  } catch {
    return null;
  }
}

export type SavedRecipe = { name: string; data: RecipeData };
const STORE = "llr.recipes";
export function loadSavedRecipes(): SavedRecipe[] {
  try {
    const raw = JSON.parse(localStorage.getItem(STORE) ?? "[]");
    return Array.isArray(raw) ? raw.filter(r => typeof r?.name === "string" && r?.data?.recipe) : [];
  } catch {
    return [];
  }
}
export function storeSavedRecipes(list: SavedRecipe[]): void {
  localStorage.setItem(STORE, JSON.stringify(list));
}
