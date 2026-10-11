import type { MaskGroup } from "./masks";

/**
 * Recipes: the part of an edit that transfers between photos — Lightroom's
 * Copy / Paste Settings. An edit is cut into sections (the panels), and a
 * recipe carries only the sections chosen when it was copied; pasting lays
 * those over the target's edit and leaves every other section as it was.
 *
 * Some things never travel, whatever is ticked, because they are tied to
 * *where* things are in this particular frame: mask groups that select by
 * position (gradients, painted strokes), the lens blur's focus point, the
 * erase strokes. The relight rig and the crop are positional too, but a
 * series shot from one spot can share them, so they are sections that start
 * unticked.
 */

export const RECIPE_SECTIONS = [
  "tone", "presence", "color", "hsl", "grading", "curve",
  "detail", "lens", "effects", "masks", "look", "relight", "crop",
] as const;
export type RecipeSection = typeof RECIPE_SECTIONS[number];
/** Ticked by default: everything that is not about the frame's geometry. */
export const DEFAULT_SECTIONS: RecipeSection[] = RECIPE_SECTIONS.filter(s => s !== "relight" && s !== "crop");

// Which section each recipe slider belongs to. A key not listed here (the lens
// blur's focus point) is positional and never copied.
const SLIDER_SECTION: Record<string, RecipeSection> = {
  exposure: "tone", contrast: "tone", highlights: "tone", shadows: "tone", whites: "tone", blacks: "tone",
  clarity: "presence", dehaze: "presence",
  temperature: "color", tint: "color", vibrance: "color", saturation: "color",
  nrLuminance: "detail", nrDetail: "detail",
  sharpAmount: "detail", sharpRadius: "detail", sharpDetail: "detail", sharpMasking: "detail",
  defringePurple: "detail", defringeGreen: "detail",
  lensDistortion: "lens", lensVignetting: "lens",
  blurAmount: "effects", blurRange: "effects", fogAmount: "effects", fogStart: "effects",
  vigAmount: "effects", vigMidpoint: "effects", vigFeather: "effects", vigRoundness: "effects",
  grainAmount: "effects", grainSize: "effects", grainRoughness: "effects",
};

// The snapshot fields (beyond the sliders) each section carries.
const SECTION_FIELDS: Partial<Record<RecipeSection, readonly string[]>> = {
  hsl: ["hslHue", "hslSat", "hslLum"],
  grading: ["grading"],
  curve: ["curve"],
  detail: ["denoise"],
  look: ["look", "lookScale", "lookStyle", "dro", "droLevel", "sonyAdvancedColour", "sonyCameraMatch"],
  relight: ["relight"],
  crop: ["crop", "aspect"],
};

/** Mask groups that select by position rather than by pixel: not portable. */
export function isPositional(g: MaskGroup): boolean {
  return g.components.some(c => c.type === "linear" || c.type === "radial" || c.type === "brush");
}

export type RecipeData = {
  v: 2;
  sections: RecipeSection[];
  recipe: Record<string, number>;      // the copied sliders only
  fields: Record<string, unknown>;     // the copied snapshot fields
  masks: MaskGroup[];                  // portable groups, when "masks" is copied
};
type Editable = { recipe?: Record<string, number>; masks?: MaskGroup[] } & Record<string, unknown>;

const clone = <T>(v: T): T => (v === undefined ? v : JSON.parse(JSON.stringify(v)));

export function extractRecipe(s: Editable, sections: readonly RecipeSection[] = DEFAULT_SECTIONS): RecipeData {
  const on = new Set(sections);
  const recipe: Record<string, number> = {};
  for (const [k, v] of Object.entries(s.recipe ?? {})) {
    const sec = SLIDER_SECTION[k];
    if (sec && on.has(sec)) recipe[k] = v;
  }
  const fields: Record<string, unknown> = {};
  for (const sec of on) for (const f of SECTION_FIELDS[sec] ?? []) if (s[f] !== undefined) fields[f] = clone(s[f]);
  const masks = on.has("masks") ? clone((s.masks ?? []).filter(g => !isPositional(g))) : [];
  return { v: 2, sections: RECIPE_SECTIONS.filter(x => on.has(x)), recipe, fields, masks };
}

/** The snapshot with the recipe laid over it; untouched sections and positional masks survive. */
export function applyRecipe<S extends Editable>(s: S, r: RecipeData): S {
  const on = new Set(r.sections);
  const out = { ...s, recipe: { ...(s.recipe ?? {}), ...r.recipe } } as S;
  for (const [k, v] of Object.entries(clone(r.fields))) (out as Record<string, unknown>)[k] = v;
  if (on.has("masks")) {
    const fresh = clone(r.masks).map(g => ({ ...g, id: Math.random().toString(36).slice(2, 8) }));
    (out as Editable).masks = [...(s.masks ?? []).filter(isPositional), ...fresh];
  }
  return out;
}

// Before sections existed a recipe was the whole slider set plus HSL, grading,
// curve and the portable masks; read it as exactly that.
function upgrade(o: Record<string, unknown>): RecipeData | null {
  if (o.v === 2 && Array.isArray(o.sections) && o.recipe && typeof o.recipe === "object") {
    return {
      v: 2, sections: (o.sections as string[]).filter((x): x is RecipeSection => (RECIPE_SECTIONS as readonly string[]).includes(x)),
      recipe: o.recipe as Record<string, number>, fields: (o.fields as Record<string, unknown>) ?? {},
      masks: Array.isArray(o.masks) ? o.masks as MaskGroup[] : [],
    };
  }
  if (!o.recipe || typeof o.recipe !== "object") return null;
  const legacy = { ...o, recipe: o.recipe as Record<string, number>, masks: Array.isArray(o.masks) ? o.masks as MaskGroup[] : [] };
  return extractRecipe(legacy, ["tone", "presence", "color", "detail", "lens", "hsl", "grading", "curve", "masks"]);
}

// Clipboard payload: tagged so a paste of unrelated text is refused.
const TAG = "llr-recipe";
export function serializeRecipe(r: RecipeData): string {
  return JSON.stringify({ [TAG]: 1, ...r });
}
export function parseRecipe(text: string): RecipeData | null {
  try {
    const o = JSON.parse(text);
    if (!o || o[TAG] !== 1) return null;
    return upgrade(o);
  } catch {
    return null;
  }
}

export type SavedRecipe = { name: string; data: RecipeData };
const STORE = "llr.recipes";
export function loadSavedRecipes(): SavedRecipe[] {
  try {
    const raw = JSON.parse(localStorage.getItem(STORE) ?? "[]");
    if (!Array.isArray(raw)) return [];
    return raw.flatMap(r => {
      const data = typeof r?.name === "string" && r?.data ? upgrade(r.data) : null;
      return data ? [{ name: r.name as string, data }] : [];
    });
  } catch {
    return [];
  }
}
export function storeSavedRecipes(list: SavedRecipe[]): void {
  localStorage.setItem(STORE, JSON.stringify(list));
}

const SECTIONS_STORE = "llr.copySections";
/** The sections the last copy used — the dialog's starting ticks and the quick copy's choice. */
export function loadCopySections(): RecipeSection[] {
  try {
    const raw = JSON.parse(localStorage.getItem(SECTIONS_STORE) ?? "null");
    if (Array.isArray(raw)) return RECIPE_SECTIONS.filter(s => raw.includes(s));
  } catch { /* fall through */ }
  return [...DEFAULT_SECTIONS];
}
export function storeCopySections(s: readonly RecipeSection[]): void {
  try { localStorage.setItem(SECTIONS_STORE, JSON.stringify(s)); } catch { /* per-viewer convenience only */ }
}
