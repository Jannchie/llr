/**
 * The Creative Look rebuild, in the browser: a moved tweak or DRO strength
 * turns into a new profile here, without a round trip.
 *
 * The worker's look_render_info (sony/profile.py) is what a decode and a look
 * switch still run, and it now ships its tweak-free inputs beside the result
 * as `lookCalibration` (look_calibration_block): the factory curve's control
 * points, the ten Fade entries, the chroma gains before Saturation divides
 * them, the DRO tables at unit strength and both Fade states' camera-match
 * tables. This file is the engine's own construction over those, transcribed
 * from the worker one function at a time — tone.py for the curve and its
 * three tweaks, chroma.py for Fade, 黑色/白色, Saturation and 色相, clarity.py
 * for 清晰, dro.py for the strength — and pinned to the worker's numbers by
 * sony-look.spec.ts against a fixture tests/test_look_rebuild.py regenerates
 * from the worker itself. Where the engine rounds in float32, so does this
 * (Math.fround), because the worker matched the engine to the bit there.
 *
 * What comes out is the same ColorProfileMeta buildProfileLUT reads, so the
 * rest of the chain cannot tell a local rebuild from the worker's; export
 * still asks the worker, and the fixture is what says the two agree.
 */

import type { ColorProfileMeta, LookTweakKey, LookTweaks } from "../api";
import { srgbDecode } from "./color-spaces";
import { StaticAsset } from "./static-asset";

// ── The tone curve (worker sony/tone.py) ──

/** LUT index of Sony's white, and the curve's output full scale. */
const TONE_INDEX_WHITE = 8192;
const TONE_OUTPUT_FULL = 16384;
/** Tag 0x7805 units per LUT index, and 0x7806 units at full scale. */
const CURVE_X_SCALE = 128;
const CURVE_Y_FULL = 16 * TONE_OUTPUT_FULL;
/** Points the profile carries; the LUT bake resamples them. */
const TONE_CURVE_POINTS = 1024;
/** Edit's panel: one stop of Highlights/Shadows/Contrast is five units. */
const PANEL_PER_STOP = 5;
/** The operator family: 37 rows of 1025, row 18 the identity. */
export const TUNE_GAIN_ROWS = 37;
export const TUNE_TABLE_LEN = 1025;
const TUNE_NEUTRAL_GAIN = 18;
const TUNE_SPLIT = 471;
const TUNE_TABLE_TOP = TUNE_TABLE_LEN - 2;
const TUNE_TABLE_MAX = 65535;
const TUNE_TABLE_STEP = 64;

/** The 37 x 1025 operator curves, row-major (public/sony-tone-family.bin). */
export type ToneFamily = Uint16Array;

/** The asset's bytes as the family, or null for anything not its shape.
 * Little-endian on disk, and so is every host WebGL2 runs on. */
export function parseToneFamily(buf: ArrayBuffer): ToneFamily | null {
  return buf.byteLength === TUNE_GAIN_ROWS * TUNE_TABLE_LEN * 2 ? new Uint16Array(buf) : null;
}

/**
 * numpy.interp, as the worker calls it: `xp` ascending, samples outside held
 * at the ends, an x on a knot reading that knot rather than interpolating.
 */
function npInterp(x: number, xp: ArrayLike<number>, fp: ArrayLike<number>): number {
  const n = xp.length;
  if (x > xp[n - 1]) return fp[n - 1];
  if (x < xp[0]) return fp[0];
  let lo = 0, hi = n - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (xp[mid] <= x) lo = mid; else hi = mid;
  }
  if (lo === n - 1 || xp[lo] === x) return fp[lo];
  const slope = (fp[lo + 1] - fp[lo]) / (xp[lo + 1] - xp[lo]);
  return slope * (x - xp[lo]) + fp[lo];
}

/** tone.base_curve: the look's factory curve over [0, white], display-encoded. */
function baseCurve(curveX: readonly number[], curveY: readonly number[]): Float64Array {
  const xp = curveX.map(v => v / CURVE_X_SCALE);
  const fp = curveY.map(v => v / CURVE_Y_FULL);
  const out = new Float64Array(TONE_INDEX_WHITE + 1);
  for (let i = 0; i <= TONE_INDEX_WHITE; i++) out[i] = npInterp(i, xp, fp);
  return out;
}

/**
 * tone._segment: one stretch of the operator table at one gain, a linear
 * blend of the two family rows either side and the same line continued past
 * both ends. Rounding is the engine's: +0.5 and truncate, in float32.
 */
function segment(family: ToneFamily, gain: number, lo: number, hi: number, out: Float32Array): void {
  const last = TUNE_GAIN_ROWS - 1;
  let k: number, blend: number;
  if (gain < 0) { k = 0; blend = gain; }
  else if (gain >= last) { k = last - 1; blend = gain - (last - 1); }
  else { k = Math.trunc(gain); blend = gain - k; }
  const b = Math.fround(blend);
  const r0 = k * TUNE_TABLE_LEN, r1 = (k + 1) * TUNE_TABLE_LEN;
  for (let i = lo; i < hi; i++) {
    const f0 = family[r0 + i], f1 = family[r1 + i];
    const mixed = f0 + (f1 - f0) * b;
    const v = Math.trunc(Math.fround(Math.fround(mixed) + 0.5));
    out[i] = Math.min(Math.max(v, 0), TUNE_TABLE_MAX);
  }
}

/** tone._operator_table: the whole table for one set of tweaks, in stops. */
function operatorTable(family: ToneFamily, highlights: number, shadows: number, contrast: number): Float32Array {
  const table = new Float32Array(TUNE_TABLE_LEN);
  segment(family, TUNE_NEUTRAL_GAIN + contrast - shadows, 0, TUNE_SPLIT, table);
  segment(family, TUNE_NEUTRAL_GAIN + contrast + highlights, TUNE_SPLIT, TUNE_TABLE_LEN, table);
  return table;
}

/** tone.apply_tuning: the curve through the operator table, the engine's integer steps. */
function applyTuning(curve: Float64Array, table: Float32Array): Float64Array {
  const out = new Float64Array(curve.length);
  for (let i = 0; i < curve.length; i++) {
    const scaled = Math.trunc(Math.min(Math.max(curve[i], 0), 1) * CURVE_Y_FULL) >> 2;
    const index = scaled >> 6;
    const lower = table[Math.min(index, TUNE_TABLE_TOP)];
    const upper = table[Math.min(index + 1, TUNE_TABLE_TOP)];
    const blend = Math.fround((scaled - (index << 6)) / TUNE_TABLE_STEP);
    const mixed = Math.fround(Math.fround(lower * Math.fround(1 - blend)) + Math.fround(upper * blend));
    out[i] = (Math.trunc(mixed) >> 2) / TONE_OUTPUT_FULL;
  }
  return out;
}

/**
 * profile.tone_curve_points: the look's curve for three tweaks on the panel
 * scale, as the (x, y) points the profile carries — y display-linear, the
 * curve's own sRGB encode undone.
 */
function toneCurvePoints(base: Float64Array, family: ToneFamily,
                         highlights: number, shadows: number, contrast: number): [number, number][] {
  const table = operatorTable(family, highlights / PANEL_PER_STOP, shadows / PANEL_PER_STOP, contrast / PANEL_PER_STOP);
  const lut = applyTuning(base, table);
  // np.linspace(0, 1, n) as the worker builds it: i * step, the last exactly 1.
  const xp = Float64Array.from(lut, (_, i) => i === lut.length - 1 ? 1 : i * (1 / (lut.length - 1)));
  const step = 1 / (TONE_CURVE_POINTS - 1);
  return Array.from({ length: TONE_CURVE_POINTS }, (_, i) => {
    const x = i === TONE_CURVE_POINTS - 1 ? 1 : i * step;
    return [x, srgbDecode(Math.min(Math.max(npInterp(x, xp, lut), 0), 1))];
  });
}

// ── YGamma and RGB2YCC (worker sony/chroma.py) ──

const LUMA_FULL_SCALE = 16383;
const LUMA_CONTRAST_UNIT = 16384;
const FADE_PANEL_PER_STOP = 10;

/** chroma.luma_terms: YGamma's (pivot, contrast) for a 褪色 value on Edit's scale. */
export function lumaTerms(pivotTable: readonly number[], contrastTable: readonly number[], fade: number): [number, number] {
  const x = Math.max(0, fade) / FADE_PANEL_PER_STOP;
  const last = pivotTable.length - 1;
  const pivot = pivotTable[Math.min(last, Math.ceil(x))] / LUMA_FULL_SCALE;
  const k = Math.min(Math.trunc(x), last - 1);
  const c = contrastTable[k] + (contrastTable[k + 1] - contrastTable[k]) * (x - k);
  return [pivot, c / LUMA_CONTRAST_UNIT];
}

const LEVEL_PANEL_LIMIT = 100;
const LEVEL_UNIT = 0x200;
const LEVEL_TO_LUMA = 32767 / 512;

/** chroma.luma_levels: 黑色/白色 -> YGamma's (black, scale), normalised. */
export function lumaLevels(black: number, white: number): [number, number] {
  const bl = Math.trunc(Math.max(-LEVEL_PANEL_LIMIT, Math.min(LEVEL_PANEL_LIMIT, black)));
  const wl = Math.trunc(Math.max(-LEVEL_PANEL_LIMIT, Math.min(LEVEL_PANEL_LIMIT, white)));
  // `|| 0` folds JS's -0 into the worker's plain integer zero.
  const blackInt = -Math.trunc(bl / 4) || 0;
  const black16 = blackInt * LEVEL_TO_LUMA;
  const scale = LEVEL_UNIT / ((LEVEL_UNIT - wl) - blackInt);
  return [black16 / LUMA_FULL_SCALE, scale];
}

const SATURATION_PANEL_LIMIT = 100;

/** chroma.saturation_factor: the factor both halves of the stage use; 0 at -100. */
export function saturationFactor(value: number): number {
  const v = Math.max(-SATURATION_PANEL_LIMIT, Math.min(SATURATION_PANEL_LIMIT, value));
  return 1 + v / 100;
}

const HUE_DEGREES_PER_UNIT_POS = 0.7;
const HUE_DEGREES_PER_UNIT_NEG = 0.35;
const HUE_PANEL_LIMIT = 100;

/** chroma.hue_degrees: 色相 on Edit's scale -> the rotation in degrees. */
export function hueDegrees(value: number): number {
  const v = Math.max(-HUE_PANEL_LIMIT, Math.min(HUE_PANEL_LIMIT, value));
  return v * (v >= 0 ? HUE_DEGREES_PER_UNIT_POS : HUE_DEGREES_PER_UNIT_NEG);
}

// ── Clarity (worker sony/clarity.py) ──

const CLARITY_AMP = [0, 32, 108, 184, 260, 336, 412, 488, 564, 646];
const CLARITY_AMP_SCALE = 1024;
const CLARITY_MIN = 0;
const CLARITY_MAX = 100;
const CLARITY_PANEL_PER_STOP = 10;

/** clarity.clarity_amount: the detail gain for a 清晰 value, 0 = off. */
export function clarityAmount(clarity: number): number {
  const x = Math.max(CLARITY_MIN, Math.min(CLARITY_MAX, Math.trunc(clarity))) / CLARITY_PANEL_PER_STOP;
  const last = CLARITY_AMP.length - 1;
  const k = Math.min(Math.trunc(x), last - 1);
  const amp = CLARITY_AMP[k] + (CLARITY_AMP[k + 1] - CLARITY_AMP[k]) * Math.min(x - k, 1);
  return amp / CLARITY_AMP_SCALE;
}

// ── DRO (worker sony/dro.py) ──

const DRO_MAX_STRENGTH = 2;

/**
 * dro.scale_dro_gain: a unit-strength table at another strength. The
 * strength scales the departure in the log domain, so this is a power (the
 * worker's unit and zero short-cuts are what pow gives exactly anyway).
 */
export function scaleDroGain(table: readonly number[], strength: number): number[] {
  const s = Math.max(0, Math.min(DRO_MAX_STRENGTH, strength));
  return table.map(g => Math.pow(g, s));
}

// ── The rebuild (worker sony/profile.py look_render_info) ──

/** `lookCalibration` as the worker sends it (profile.py look_calibration_block). */
export interface LookCalibration {
  curveX: number[];
  curveY: number[];
  lumaPivot: number[];
  lumaContrast: number[];
  chromaGain: number[];
  droGainAsShot: number[] | null;
  droGainNoCurve: number[] | null;
  cameraMatch: unknown;
  cameraMatchFade: unknown;
}

const numbers = (v: unknown, min: number): number[] | null =>
  Array.isArray(v) && v.length >= min && v.every(Number.isFinite) ? [...(v as number[])] : null;

/** Validate the worker's block; null for an older response or anything malformed. */
export function parseLookCalibration(meta: unknown): LookCalibration | null {
  if (meta && typeof meta === "object") {
    const hit = PARSED.get(meta as object);
    if (hit !== undefined) return hit.cal;
  }
  const cal = parseLookCalibrationUncached(meta);
  if (meta && typeof meta === "object" && cal) PARSED.set(meta as object, { cal, base: baseCurve(cal.curveX, cal.curveY) });
  return cal;
}

// A block's parse and its factory curve, keyed on the block itself: a drag
// rebuilds the profile at every input event and neither depends on the tweak.
const PARSED = new WeakMap<object, { cal: LookCalibration; base: Float64Array }>();

function parseLookCalibrationUncached(meta: unknown): LookCalibration | null {
  const m = meta as Record<string, unknown> | null | undefined;
  if (!m || typeof m !== "object") return null;
  const curveX = numbers(m.curveX, 2), curveY = numbers(m.curveY, 2);
  const lumaPivot = numbers(m.lumaPivot, 2), lumaContrast = numbers(m.lumaContrast, 2);
  const chromaGain = numbers(m.chromaGain, 4);
  if (!curveX || !curveY || curveX.length !== curveY.length) return null;
  if (!lumaPivot || !lumaContrast || lumaPivot.length !== lumaContrast.length) return null;
  if (!chromaGain || chromaGain.length !== 4) return null;
  const droGainAsShot = m.droGainAsShot == null ? null : numbers(m.droGainAsShot, 2);
  const droGainNoCurve = m.droGainNoCurve == null ? null : numbers(m.droGainNoCurve, 2);
  if (m.droGainAsShot != null && !droGainAsShot) return null;
  if (m.droGainNoCurve != null && !droGainNoCurve) return null;
  return {
    curveX, curveY, lumaPivot, lumaContrast, chromaGain, droGainAsShot, droGainNoCurve,
    cameraMatch: m.cameraMatch ?? null, cameraMatchFade: m.cameraMatchFade ?? null,
  };
}

/** The panel's ranges, the worker's TWEAK_RANGES, for a profile that sent none. */
const DEFAULT_RANGES: Record<LookTweakKey, [number, number]> = {
  contrast: [-100, 100], highlights: [-100, 100], shadows: [-100, 100],
  white: [-100, 100], black: [-100, 100], fade: [0, 100],
  hue: [-100, 100], saturation: [-100, 100], clarity: [0, 100],
};
const TWEAK_KEYS = Object.keys(DEFAULT_RANGES) as LookTweakKey[];

/** profile._clamp_tweak on every field: truncated to an integer, then into range. */
export function clampLookTweaks(
  tweaks: Partial<LookTweaks>, ranges?: Partial<Record<LookTweakKey, [number, number]>> | null,
): LookTweaks {
  const out = {} as LookTweaks;
  for (const key of TWEAK_KEYS) {
    const [lo, hi] = ranges?.[key] ?? DEFAULT_RANGES[key];
    const v = tweaks[key];
    out[key] = typeof v === "number" && Number.isFinite(v) ? Math.max(lo, Math.min(hi, Math.trunc(v))) || 0 : 0;
  }
  return out;
}

/** The DRO control as the request carries it: strength null = as shot. */
export interface DroRequest {
  strength: number | null;
  level: number;
}

/**
 * look_render_info over `base`'s calibration for new tweaks and DRO, or null
 * when this cannot be done here and the worker has to be asked: a profile
 * without the block (a DCP render, an older worker), or a manual DRO level
 * whose table the base does not hold — those are Edit.exe's presets, which
 * only the worker has. Everything the tweaks cannot reach is `base`'s.
 */
export function rebuildLookProfile(
  base: ColorProfileMeta, tweaks: Partial<LookTweaks>, dro: DroRequest, family: ToneFamily,
): ColorProfileMeta | null {
  if (base.kind !== "sony") return null;
  const cal = parseLookCalibration(base.lookCalibration);
  if (!cal) return null;
  const factory = PARSED.get(base.lookCalibration as object)!.base;
  const t = clampLookTweaks(tweaks, base.lookRanges);

  // DRO first: it is the one part that can still need the worker.
  const asShotDro = base.droAsShot ?? 0;
  const strength = dro.strength == null ? asShotDro : Math.max(0, Math.min(DRO_MAX_STRENGTH, dro.strength));
  const level = Math.round(dro.level);
  let droGain: number[] | null;
  if (strength <= 0) {
    droGain = null;
  } else if (level >= 0) {
    // A preset, at unit strength whatever the control says (the worker's
    // rule). The base carries it only if it was built at this very level.
    if (base.droLevel !== level || !base.profileDroGain?.length) return null;
    droGain = base.profileDroGain;
  } else if (cal.droGainAsShot) {
    droGain = scaleDroGain(cal.droGainAsShot, strength);
  } else if (asShotDro > 0) {
    if (!cal.droGainNoCurve) return null;
    droGain = scaleDroGain(cal.droGainNoCurve, strength);
  } else {
    droGain = null;
  }

  const sat = saturationFactor(t.saturation);
  const [lumaPivot, lumaContrast] = lumaTerms(cal.lumaPivot, cal.lumaContrast, t.fade);
  const [lumaBlack, lumaScale] = lumaLevels(t.black, t.white);
  return {
    ...base,
    profileToneCurve: toneCurvePoints(factory, family, t.highlights, t.shadows, t.contrast),
    // 饱和度 -100 is a factor of 0: the gains go out undivided, the shader's
    // multiply-back zeroes the chroma regardless.
    profileChromaGain: sat > 0 ? cal.chromaGain.map(g => g / sat) : [...cal.chromaGain],
    profileChromaSaturation: sat,
    profileChromaHue: hueDegrees(t.hue),
    profileLumaPivot: lumaPivot,
    profileLumaContrast: lumaContrast,
    // 高级 reads the same Fade table as 标准 (chroma.luma_terms), so the
    // advanced contrast is this one too.
    profileLumaContrastAdvanced: lumaContrast,
    profileLumaBlack: lumaBlack,
    profileLumaScale: lumaScale,
    profileDroGain: droGain,
    droStrength: strength,
    droLevel: level,
    profileClarity: base.profileClarity ? { ...base.profileClarity, gain: clarityAmount(t.clarity) } : null,
    // Any non-zero Fade is "on" for the match, on either scale.
    profileCameraMatch: t.fade ? cal.cameraMatchFade : cal.cameraMatch,
    lookTweaks: t,
  };
}

// ── The family asset ──

/**
 * public/sony-tone-family.bin (sony_repro/tools/make_tone_family_asset.py
 * writes it from the worker's data/tone_family.npz). Static like the 3-D LUT:
 * the operator family is the engine's, not a shot's. While it has not landed
 * — or once a fetch has failed — every rebuild goes to the worker instead.
 */
const TONE_FAMILY = new StaticAsset<ToneFamily>("sony-tone-family.bin", parseToneFamily, "Creative Look rebuild in the browser");
export const loadToneFamily = (): Promise<ToneFamily | null> => TONE_FAMILY.load();
/** The family if the fetch has landed; the rebuild is synchronous, so it cannot wait. */
export const toneFamilyLoaded = (): ToneFamily | null => TONE_FAMILY.loaded;
