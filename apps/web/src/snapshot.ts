// One photo's edit: the shape that is stored in the catalog, embedded in an
// export's XMP, handed to the assistant and copied between photos — and every
// rule about reading one that was stored by an older build (normalizeSnapshot).

import type { LookTweaks } from "./api";
import { DEFAULT_ASPECT } from "./composables/useCropEditor";
import { cloneCrop, defaultCrop, type CropState } from "./rendering/crop";
import { defaultToneCurve, normalizeToneCurve, type ToneCurve } from "./rendering/curve";
import { cloneErase, type EraseStroke } from "./rendering/erase";
import { cloneMasks, defaultAdjust, type MaskGroup } from "./rendering/masks";
import { cloneRelight, defaultRelight, type RelightState } from "./rendering/relight";

// ── The Basic recipe ──

export type RecipeKey = "exposure"|"contrast"|"highlights"|"shadows"|"whites"|"blacks"|"vibrance"|"saturation"|"temperature"|"tint"|"clarity"|"dehaze"|"lensDistortion"|"lensVignetting"|"nrLuminance"|"nrDetail"
  |"sharpAmount"|"sharpRadius"|"sharpDetail"|"sharpMasking"|"defringePurple"|"defringeGreen"
  |"blurAmount"|"blurRange"|"blurFocusX"|"blurFocusY"|"fogAmount"|"fogStart"
  |"vigAmount"|"vigMidpoint"|"vigFeather"|"vigRoundness"|"grainAmount"|"grainSize"|"grainRoughness";
export type Recipe = Record<RecipeKey, number>;

// Distortion correction defaults to fully applied (mirrorless glass is designed
// around it — uncorrected geometry reads as broken). Vignetting stays off by
// default: natural falloff is often part of the look, so brightening the
// corners is an opt-in, not a baseline.
export const defaultRecipe = (): Recipe => ({
  exposure: 0, contrast: 0, highlights: 0, shadows: 0,
  whites: 0, blacks: 0, vibrance: 0, saturation: 0,
  temperature: 6500, tint: 0, clarity: 0, dehaze: 0,
  lensDistortion: 100, lensVignetting: 0,
  nrLuminance: 0, nrDetail: 50,
  sharpAmount: 0, sharpRadius: 1, sharpDetail: 25, sharpMasking: 0,
  defringePurple: 0, defringeGreen: 0,
  // The lens blur's focus point is source-norm (y down), -1 = the frame's centre;
  // set by clicking the image, not by a slider.
  blurAmount: 0, blurRange: 20, blurFocusX: -1, blurFocusY: -1,
  fogAmount: 0, fogStart: 30,
  vigAmount: 0, vigMidpoint: 50, vigFeather: 50, vigRoundness: 0,
  grainAmount: 0, grainSize: 25, grainRoughness: 50,
});

// ── Panels ──

/** The slider groups of the rail; a panel's on/off switch is keyed by these. */
export type GroupTitle = "tone" | "presence" | "color" | "lens" | "noise" | "sharpen" | "defringe" | "lensBlur" | "fog" | "vignette" | "grain";
export type BypassKey = GroupTitle | "hsl" | "grading" | "curve" | "transform" | "look" | "masks";

// ── Colour engine ──

export type ProfileId = "standard" | "sony";

// ── Denoise, grading ──

// 50 for every slider: the camera's own position on the Sony filter, and
// Edit's default. (Amount used to start at 100, which on a Sony frame renders
// exactly as 50 — above it the engine changes the filter itself, which the
// transcription cannot follow, so the strength holds at Auto's.)
export const defaultDenoise = () => ({
  enabled: true, auto: true, amount: 50, edge: 50, chroma: 50,
});
export type DenoiseState = ReturnType<typeof defaultDenoise>;

export const defaultGrading = () => ({
  shH: 0, shS: 0,
  mdH: 0, mdS: 0,
  hlH: 0, hlS: 0,
  blend: 50,
  balance: 0,
});
type Grading = ReturnType<typeof defaultGrading>;

// ── Creative Look and DRO ──

// Sessions saved before the panel scale stored the camera's stops. The same
// conversion the worker does (sony/profile.py stops_to_panel), so a restored
// session lands where it rendered; the three sliders the camera lacks were
// never stored and start at zero.
export const LOOK_SCALE = "panel";
const SATURATION_STOPS = [0, 10, 20, 25, 30, 35, 40, 45, 50, 55];
export function lookStopsToPanel(old: Partial<Record<string, number>>): LookTweaks {
  const n = (k: string) => Math.trunc(old[k] ?? 0);
  const sat = Math.min(Math.abs(n("saturation")), SATURATION_STOPS.length - 1);
  return {
    contrast: n("contrast") * 5, highlights: n("highlights") * 5, shadows: n("shadows") * 5,
    white: 0, black: 0, fade: n("fade") * 10, hue: 0,
    saturation: SATURATION_STOPS[sat]! * (n("saturation") < 0 ? -1 : 1),
    clarity: n("clarity") * 10,
  };
}

/** DRO's level in the engine's own encoding: -1 is Auto (the shot's own curve), 0..99 a built-in preset. */
export const DRO_AUTO = -1;

// ── The snapshot ──

/**
 * As stored. A field marked optional did not exist when some snapshots were
 * written; normalizeSnapshot says what its absence means.
 */
export type Snapshot = {
  recipe: Recipe;
  hslHue: number[]; hslSat: number[]; hslLum: number[];
  grading: Grading;
  curve: ToneCurve;
  crop: CropState;
  aspect?: string;  // crop aspect-lock key; optional: absent in older persisted sessions
  dcp: string;
  profile?: ProfileId;  // colour engine; optional: absent in pre-Sony persisted sessions
  denoise?: DenoiseState;  // optional: absent in pre-denoise persisted sessions
  // Creative Look tweaks; null (or absent, in older sessions) means as shot.
  look?: LookTweaks | null;
  // Which scale `look` is on. Absent means the camera's stops, which is what
  // every session before Edit's panel scale stored (see lookStopsToPanel).
  lookScale?: typeof LOOK_SCALE;
  // Which Creative Look to render, when it is not the body's own. Same "null
  // means as shot" convention, and likewise absent in older sessions.
  lookStyle?: string | null;
  // DRO strength, on the same convention: null is what the camera applied.
  dro?: number | null;
  // Which DRO curve: -1 for the shot's own (Auto), 0..99 for a built-in preset.
  droLevel?: number | null;
  // Sony's advanced colour reproduction (ZcTask3DLut). Absent in older
  // sessions, which rendered with it off, and off is the default again.
  sonyAdvancedColour?: boolean;
  // Camera match (the fitted correction toward the body's JPEG). Absent in
  // older sessions, which rendered without it; a new snapshot starts with it on.
  sonyCameraMatch?: boolean;
  // Local adjustments; absent in sessions that predate them, which is empty.
  masks?: MaskGroup[];
  // Generative erase strokes (rendering/erase.ts); absent before erase existed.
  erase?: EraseStroke[];
  // Added lights (rendering/relight.ts); absent before relight existed.
  relight?: RelightState;
  // Panels switched off (see BypassKey); absent in older sessions, i.e. none.
  bypass?: BypassKey[];
};

/** A snapshot with every field present and on today's scales. */
export type NormalizedSnapshot = Required<Omit<Snapshot, "look" | "lookStyle" | "dro" | "droLevel">> & {
  look: LookTweaks | null; lookStyle: string | null; dro: number | null; droLevel: number;
};

// Sony's own engine for Sony's own files: ARW (and the older SRF/SR2) carry the
// calibration it renders from, and the worker falls back to the DCP when a shot
// has none. Anything else has only the DCP, so Adobe is the honest default.
function defaultProfile(name?: string): ProfileId {
  return /\.(arw|srf|sr2)$/i.test(name ?? "") ? "sony" : "standard";
}

export function defaultSnapshot(name?: string): NormalizedSnapshot {
  return {
    recipe: defaultRecipe(),
    hslHue: [0, 0, 0, 0, 0, 0, 0, 0],
    hslSat: [0, 0, 0, 0, 0, 0, 0, 0],
    hslLum: [0, 0, 0, 0, 0, 0, 0, 0],
    grading: defaultGrading(),
    curve: defaultToneCurve(),
    crop: defaultCrop(),
    aspect: DEFAULT_ASPECT,
    dcp: "",
    profile: defaultProfile(name),
    denoise: defaultDenoise(),
    look: null,
    lookScale: LOOK_SCALE,
    lookStyle: null,
    dro: null,
    droLevel: DRO_AUTO,
    sonyAdvancedColour: false,
    sonyCameraMatch: true,
    masks: [],
    erase: [],
    relight: defaultRelight(),
    bypass: [],
  };
}

/**
 * `stored` with everything an older build left out filled in. What an absent
 * field means is whatever that build rendered, which is not always today's
 * default: a snapshot from before camera match existed was rendered without
 * it, though a new photo starts with it on.
 *
 * Returns fresh objects throughout — the caller may put them straight into
 * reactive state without aliasing the stored copy.
 */
export function normalizeSnapshot(stored: Snapshot): NormalizedSnapshot {
  const hsl = (v: number[] | undefined) => Array.from({ length: 8 }, (_, i) => v?.[i] ?? 0);
  return {
    // Layered over the defaults: a snapshot stored before a recipe key existed
    // must get that key's default, not whatever the previous photo left there.
    recipe: { ...defaultRecipe(), ...stored.recipe },
    hslHue: hsl(stored.hslHue), hslSat: hsl(stored.hslSat), hslLum: hsl(stored.hslLum),
    grading: { ...defaultGrading(), ...stored.grading },
    curve: normalizeToneCurve(stored.curve ?? defaultToneCurve()),
    crop: stored.crop ? cloneCrop(stored.crop) : defaultCrop(),
    // No aspect lock rather than today's default: a crop box drawn before the
    // lock existed need not match it, and the next drag would reshape it.
    aspect: stored.aspect ?? "free",
    dcp: stored.dcp ?? "",
    profile: stored.profile ?? "standard",
    denoise: { ...defaultDenoise(), ...stored.denoise },
    // Before Edit's panel scale, the look was stored in the camera's stops.
    look: stored.look ? (stored.lookScale === LOOK_SCALE ? { ...stored.look } : lookStopsToPanel(stored.look)) : null,
    lookScale: LOOK_SCALE,
    lookStyle: stored.lookStyle ?? null,
    dro: stored.dro ?? null,
    // Before manual levels existed, Auto is what was rendered.
    droLevel: stored.droLevel ?? DRO_AUTO,
    // Both absent in snapshots that predate them, which rendered without.
    sonyAdvancedColour: stored.sonyAdvancedColour ?? false,
    sonyCameraMatch: stored.sonyCameraMatch ?? false,
    masks: cloneMasks(stored.masks ?? []).map(g => ({ ...g, adjust: { ...defaultAdjust(), ...g.adjust } })),
    erase: cloneErase(stored.erase ?? []),
    relight: cloneRelight(stored.relight),
    bypass: [...(stored.bypass ?? [])],
  };
}
