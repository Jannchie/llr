/**
 * Network layer for the LLR API: the /render-linear binary protocol and the
 * base URL. Kept out of components so the wire format has one owner.
 */

import type { LinearPixels, ProfileClarity, ProfileSharpen, ProfileSpica } from "./rendering/pipeline-renderer";
import type { ProfileMarble } from "./rendering/sony-marble";

export const API = (import.meta.env.VITE_API_URL as string | undefined) ?? "/api";

// The Creative Look tweaks, on Imaging Edge Edit's own panel scale — the
// numbers Edit shows in its 创意外观 panel, -100..100 (褪色 and 清晰 0..100).
// The camera's own -9..+9 stops arrive already converted (worker
// sony/profile.py stops_to_panel), so the as-shot values read the same here as
// in Edit. Sony only. What each one can be set to is not written here: the
// ranges are the engine's, so they come down with the profile as lookRanges.
export type LookTweaks = {
  contrast: number; highlights: number; shadows: number; white: number; black: number;
  fade: number; hue: number; saturation: number; clarity: number;
};
export type LookTweakKey = keyof LookTweaks;

// What render-linear takes for denoising, and what an export freezes. Declared
// here rather than inline at each site because otherwise the two are equal only
// by hand: an ExportPlan's denoise field is built solely by calling App.vue's
// denoisePayload, so excess-property checking never runs on it, and a field
// missing from the plan is dropped at the type level with no error — the worker
// then falls back to its own defaults and the export denoises unlike the
// preview it was taken from, silently. That already happened: the plan went
// without `edge`/`chroma` for a while and nothing caught it.
// ⚠️ `amount` is 0..1 here. The UI state and the persisted Snapshot are 0..100
// (see denoisePayload); mixing them up is a silent 100x.
export type DenoisePayload = {
  enabled: boolean; auto: boolean; amount: number;
  edge: number; chroma: number;
};

export type ColorProfileMeta = {
  // How the worker produced the linear data: a DCP profile, Sony's own rendering
  // reproduced from calibration inside the RAW, LibRaw's matrix fallback, or
  // "rendered-image" for a decoded JPEG/PNG/TIFF, which carries no mosaic,
  // camera profile or lens correction data.
  kind?: "dcp" | "sony" | "libraw-matrix" | "rendered-image";
  // Sony path only: the Creative Look whose tone curve was applied, the ones
  // this particular RAW carries a calibration for, and the one the body itself
  // chose. Every ARW ships all of them, so the picker is built from the file
  // rather than from a constant — a body with looks this client has never heard
  // of (FL2, FL3) populates it with its own.
  creativeLook?: string;
  availableLooks?: string[] | null;
  lookAsShotStyle?: string | null;
  // True when the look is not in this RAW at all — the body predates it, and its
  // curve and chroma came from a donor. Labelled in the picker rather than passed
  // off as the body's own; see the worker's BORROWED_NOTE for what differs.
  lookBorrowed?: boolean | null;
  // How the DCP was picked, and which styles this camera body actually ships.
  // The style picker is built from these, so it can only offer profiles that
  // resolve — there is no "auto" entry, the matched code is simply selected.
  selection?: { matchedCode?: string | null; availableCodes?: string[] } | null;
  profileToneCurve?: [number, number][] | null;
  // Sony's RGB2YCC, applied right after that curve: four cross terms and four
  // gains, each pair indexed by the sign of a chroma difference. Sony only.
  profileChromaCross?: number[] | null;
  profileChromaGain?: number[] | null;
  // YGamma, run between the two chroma halves: the shot's Fade setting, as a
  // pivot on the luma scale and a contrast about it. Sony only.
  profileLumaPivot?: number | null;
  profileLumaContrast?: number | null;
  // The table YGamma indexes Y through before that line: 16384 entries, in and
  // out on the engine's 0..16383 scale. Standard and Neutral carry a highlight
  // knee (slope 0.90625 above Y=8192); the other eight are near identity. ~90 kB
  // of JSON, and per-look, so it arrives with every profile rebuild. Null for an
  // older response, which renders as the pivot/contrast line alone. Sony only.
  profileLumaLut?: number[] | null;
  // The pair Imaging Edge's 色彩复制 = 高级 puts in place of those two. That
  // setting is not only the 3-D LUT it was first taken for: it swaps YGamma's
  // table as well, and its contrast, which is 17280/16384 for every look and not
  // the look's own entry. Both ride along so the browser's switch stays a redraw
  // rather than a round trip. Null for an older response. Sony only.
  profileLumaLutAdvanced?: number[] | null;
  profileLumaContrastAdvanced?: number | null;
  // Edit's 黑色/白色 sliders as YGamma applies them: y = (y - black) * scale
  // between the table and the pivot line (worker sony/chroma.py luma_levels).
  // (0, 1) on every camera-shot frame, and for an older response. Sony only.
  profileLumaBlack?: number | null;
  profileLumaScale?: number | null;
  // ChromaSuppres, which the engine runs just *before* YGamma and indexes by
  // the luma from before it. The mid-tones lose a flat 1/256 of their chroma
  // and everything above hiY fades out linearly. All four are in the engine's
  // own units — hiY/loY on its 0..16383 luma scale, the slopes over 4096.
  // Null when the shot's four SR2 tags could not be read. Sony only.
  profileChromaSuppres?: { hiY: number; loY: number; slopeHi: number; slopeLo: number } | null;
  // The Saturation slider: profileChromaGain is already divided by it, and the
  // shader multiplies the chroma back after the clamp. Sony only.
  profileChromaSaturation?: number | null;
  // Edit's 色相 slider as a rotation of (Cb, Cr) in degrees, applied right
  // after that multiply (worker sony/chroma.py rotate_chroma). 0 is off.
  profileChromaHue?: number | null;
  // Sepia's toning stage: a weighted sum of the encoded RGB through one curve
  // per channel. Null for every look but Sepia.
  profileSepia?: { weights: number[]; lut: number[][] } | null;
  // Sony's DRO. The gain table is null whenever nothing is being applied, which
  // includes the strength sitting at zero — droAvailable is what says the shot
  // carries a curve at all, and so whether the control is worth offering.
  // droAsShot is what the camera itself did, and the reset target.
  profileDroGain?: number[] | null;
  // The engine's bilateral grid: num and den flattened row-major over
  // (ny, nx, bins), plus `uv`, the affine map from normalised image coordinates
  // to continuous cell coordinates. Present means DRO can be applied as the
  // local operator it actually is; absent means the gain table gets indexed by
  // each pixel's own luminance instead, which is a visibly weaker match.
  profileDroGrid?: {
    nx: number; ny: number; bins: number;
    num: number[]; den: number[]; uv: number[];
  } | null;
  droStrength?: number | null;
  droAsShot?: number | null;
  droAvailable?: boolean | null;
  // -1 is Auto, meaning the curve the body wrote for this frame. 0..99 selects
  // one of Edit.exe's ten built-in curves instead, and droLevels is the ladder
  // of level values the UI offers — sent by the worker so the two cannot drift.
  droLevel?: number | null;
  droLevels?: number[] | null;
  droLogCeiling?: number | null;
  droLumaWhite?: number | null;
  droGridLumaWhite?: number | null;
  // Sony's in-camera Clarity, as the shader's blur chain needs it. The setting
  // itself is in lookTweaks; `gain` of zero means it is off. Every constant is
  // the body's own calibration rather than anything in the file.
  profileClarity?: ProfileClarity | null;
  // Sony's in-camera sharpening, the stage just before Clarity. Not one of the
  // lookTweaks — it is a camera setting of its own, and no slider here changes
  // it, so it rides through a profile rebuild untouched. An amount of zero is
  // not a camera position: the body has no "off" for sharpening, and the engine
  // only zeroes the stage when the SharpnessRange tag cannot be read.
  profileSharpness?: ProfileSharpen | null;
  // Spica, the fine half of the same control, which runs between sharpening
  // and Clarity. Read from the same two MakerNotes ladders weighted the other
  // way, plus the shot's ISO, so it moves with SharpnessRange rather than with
  // any slider here — and rides through a profile rebuild for the same reason.
  profileSpica?: ProfileSpica | null;
  // Marble's chroma cleanup, the stage that runs last on the finished frame
  // (worker sony/marble.py::marble_block). `iso` is the amount's only per-shot
  // input, `amountAuto` is what that amount comes to at the panel's default,
  // and `calib` is the body's threshold table. The browser recomputes the
  // amount rather than using amountAuto, because only it knows where the
  // colour-NR slider sits. Null for a render with no Marble to reproduce.
  profileMarble?: ProfileMarble | null;
  // Camera match, the stage after Marble (worker sony/profile.py
  // camera_match_table): three small CIELAB tables fitted per body and
  // Creative Look against the camera's own JPEG. Null when no table was
  // fitted for this body, which hides the switch — a table from another body
  // is never substituted. Shape in rendering/camera-match.ts parseCameraMatch.
  profileCameraMatch?: unknown;
  // What the tone curve and chroma terms above were built with, and what the
  // body itself recorded. The Creative Look panel starts at lookAsShot, which
  // is also what a double-click resets a slider to.
  lookTweaks?: LookTweaks | null;
  lookAsShot?: LookTweaks | null;
  // [min, max] per tweak, the range the engine renders over — Fade and Clarity
  // have no negative side, the rest are centred on zero. Sent by the worker so
  // the sliders cannot offer a stop it would clamp away (worker TWEAK_RANGES).
  lookRanges?: Partial<Record<LookTweakKey, [number, number]>> | null;
  // The DCP's HueSatMaps, each with its samples inline (worker dcp.py
  // table_payload). The worker no longer applies them — they are 3D LUTs, which
  // is one GPU fetch and thirty-odd whole-array numpy passes — so these are what
  // the shader runs. See pipeline-renderer parseDcpTables for the shape.
  profileHueSatMap?: unknown;
  profileLookTable?: unknown;
  // The table fitted against this body's own JPEG rendering. Always sent when
  // one exists, since the toggle that governs it is a shader uniform now;
  // cameraMatchAvailable says whether the control is worth offering at all.
  cameraMatch?: unknown;
  cameraMatchAvailable?: boolean;
  // Per-shot lens correction splines from the RAW's maker notes (vendor-neutral
  // factor tables; see rendering/lens.ts parseLensCorr for the shape).
  lensCorr?: unknown;
};

export type LinearMeta = {
  width: number; height: number; fullWidth: number | null; fullHeight: number | null;
  colorProfile: ColorProfileMeta | null; dtype?: string;
  // Whether Color NR reaches this frame's denoiser. Per-frame, not a setting: a
  // Sony RAW runs a transcription of Sony's own filter, which has no such
  // control, while a frame without its noise tags falls back to the wavelet,
  // where the control works. Always present: the API fills it in (true for a
  // worker that does not report it), so the "older worker" rule lives there.
  denoiseUsesChroma: boolean;
};

// Decode linear data via /render-linear. The response carries the pixels
// directly: [u32 header length][JSON header, padded so the pixels stay 4-byte
// aligned][linear RGB in header.dtype]. float16 payloads (half the bytes of
// float32) stay as Uint16Array and upload straight into an RGB16F texture.
// Returns null on 404 (source evicted server-side).
export async function fetchLinear(body: Record<string, unknown>): Promise<{ meta: LinearMeta; pixels: LinearPixels } | null> {
  const res = await fetch(`${API}/render-linear`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(await res.text());
  const buf = await res.arrayBuffer();
  const headerLen = new DataView(buf).getUint32(0);
  const meta = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, headerLen))) as LinearMeta;
  const pixels = meta.dtype === "float16"
    ? new Uint16Array(buf, 4 + headerLen)
    : new Float32Array(buf, 4 + headerLen);
  return { meta, pixels };
}

// Re-derive the colour profile for a different Creative Look setting — one of
// the six tweaks, the choice of look itself, or the DRO strength. None of them
// reaches a pixel: the looks share the body's one hue-segmented matrix, DRO is
// a per-pixel scalar gain, which commutes with that matrix and so can be
// applied by the shader afterwards, and Clarity is a post-pass the shader owns
// outright. All of it costs this instead of pulling the whole frame back
// through /render-linear. Null means the shot has no Sony rendering to
// re-derive, and the caller keeps what it has.
//
// `dro` undefined means "as shot", which is not the same as 0 — see the API's
// clampDroStrength. `droLevel` undefined means "leave the level alone", which is
// not the same as -1 (Auto); both distinctions exist for the same reason.
export async function fetchLookProfile(
  sourceId: string, look: Partial<LookTweaks>, style?: string, dro?: number,
  droLevel?: number,
): Promise<ColorProfileMeta | null> {
  const res = await fetch(`${API}/look-profile`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ sourceId, look, style, dro, droLevel }),
  });
  if (!res.ok) throw new Error(await res.text());
  return ((await res.json()) as { colorProfile: ColorProfileMeta | null }).colorProfile;
}
