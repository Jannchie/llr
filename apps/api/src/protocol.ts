/**
 * Pure request/response logic for the LLR API, kept free of I/O so it can be
 * unit-tested: source-id validation, render-parameter clamping, the binary
 * framing of /render-linear responses, and CORS origin checks.
 */

import { isIP } from "node:net";

// The one definition of a valid source id (server-minted UUIDs), shared by
// the route patterns and sessionDirFor so they cannot drift apart.
export const SOURCE_ID = String.raw`[\w-]+`;
const SOURCE_ID_RE = new RegExp(`^${SOURCE_ID}$`);

// Reject anything else before it reaches resolve() — a traversal like "../x"
// or an absolute path would escape sessionsRoot (and /export writes there).
export function isValidSourceId(sourceId: string): boolean {
  return SOURCE_ID_RE.test(sourceId);
}

// Folder ids are SQLite rowids; the route patterns accept only digits so a
// malformed id never reaches the catalog.
export const FOLDER_ID = String.raw`\d{1,12}`;
const FOLDER_ID_RE = new RegExp(`^${FOLDER_ID}$`);

export function isValidFolderId(folderId: string): boolean {
  return FOLDER_ID_RE.test(folderId);
}

export const FOLDER_NAME_MAX = 128;

// A name here is display-only (never a path), so the only rules are the ones
// that keep it readable: non-empty once trimmed, bounded, and no control
// characters. Returns the trimmed name or null — what the caller does with it
// (a folder's is the whole name, a photo's has rules of its own) is its own.
function displayLabel(name: unknown, max: number): string | null {
  if (typeof name !== "string") return null;
  const trimmed = name.trim();
  if (!trimmed || trimmed.length > max) return null;
  if (/[\u0000-\u001f\u007f]/.test(trimmed)) return null;
  return trimmed;
}

export function normalizeFolderName(name: unknown): string | null {
  return displayLabel(name, FOLDER_NAME_MAX);
}

// The Creative Look tweaks, on Imaging Edge Edit's own panel scale (every
// slider -100..100, 褪色 and 清晰 0..100). Sent only for the fields the client
// is actually overriding: the worker fills the rest in from what the body
// recorded, so a moved slider does not have to echo the others. Ranges are the
// panel's and are enforced worker-side (sony/profile.py TWEAK_RANGES).
export const LOOK_TWEAK_KEYS = [
  "contrast", "highlights", "shadows", "white", "black", "fade", "hue", "saturation", "clarity",
] as const;
export type LookTweaks = Partial<Record<(typeof LOOK_TWEAK_KEYS)[number], number>>;

export interface RenderLinearBody {
  sourceId?: string;
  profileId?: string;
  halfSize?: boolean;
  maxSize?: number;
  dcpCode?: string;
  denoise?: {
    enabled?: boolean;
    // Let the worker set `amount` from the shot's ISO, the way Edit ramps its
    // RAW stage. See RenderParams.denoise.auto.
    auto?: boolean;
    amount?: number;
    // Edge and colour ride Edit.exe's own 0..100 scale with 50 neutral, so the
    // same number means the same thing in both applications.
    edge?: number;
    chroma?: number;
  };
  look?: LookTweaks;
  style?: string;
  dro?: number;
  droLevel?: number;
  purpose?: string;
  erase?: unknown;
}

export interface RenderParams {
  profile: string;
  halfSize: boolean;
  maxSize: number;
  // What the decode is for. Both the preview and an export ask for full sensor
  // resolution, so the request shape alone no longer says which is which — and
  // the worker needs to know: a preview decode is cached (a DCP or denoise
  // change must not re-decode the RAW), a one-off export must not be.
  purpose: "preview" | "export";
  dcpCode: string | undefined;
  // Which filter runs is the worker's own business (denoise.DEFAULT_MODEL) and
  // deliberately absent from the wire: llr has one denoiser, aimed at being
  // Edit's, so there is nothing here for a client to pick.
  denoise: {
    enabled: boolean;
    // Whether the worker derives `amount` from ISO rather than taking the one
    // below. Passed through rather than resolved here because ISO lives in the
    // file, which only the worker opens — and because resolving it before the
    // worker's cache key would mean the key no longer names the strength the
    // cached pixels were actually denoised at.
    auto: boolean;
    amount: number;
    edge: number;
    chroma: number;
  };
  look: LookTweaks;
  style: string | undefined;
  dro: number | undefined;
  droLevel: number | undefined;
  // Generative erase strokes, in the decode's own source-normalised frame (see
  // clampErase). Empty means none — the worker serves the cached decode as is.
  erase: EraseStroke[];
}

// One brushed region to refill: a polyline in source-normalised coordinates
// (y down, the decode's orientation; may stray a little past the frame where
// the brush did) with a radius in units of the frame's long edge, plus any
// further polylines (`more`) painting the same region — one fill brushed in
// several goes. The worker fills each stroke in order (worker erase.py); its
// limits match these. `grow` and `feather` (fractions of the stroke's largest
// radius) widen the filled region and fade it out; a stroke without them keeps
// the fixed edge it was made with.
export type EraseLine = { points: [number, number][]; radius: number };
export type EraseStroke = EraseLine & { mode?: "heal"; more?: EraseLine[]; grow?: number; feather?: number };
export const ERASE_MAX_STROKES = 64;
export const ERASE_MAX_POINTS = 4096;
export const ERASE_MAX_LINES = 32;
export const ERASE_MAX_SOFT = 1;

function clampEraseLine(item: unknown): EraseLine | null {
  if (!item || typeof item !== "object") return null;
  const { points, radius } = item as { points?: unknown; radius?: unknown };
  if (typeof radius !== "number" || !(radius > 0 && radius <= 0.5) || !Array.isArray(points)) return null;
  const pts: [number, number][] = [];
  for (const p of points.slice(0, ERASE_MAX_POINTS)) {
    if (Array.isArray(p) && p.length === 2 && p.every((v) => typeof v === "number" && Number.isFinite(v) && v > -1 && v < 2)) {
      pts.push([p[0] as number, p[1] as number]);
    }
  }
  return pts.length ? { points: pts, radius } : null;
}

function clampSoft(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? Math.min(ERASE_MAX_SOFT, Math.max(0, value)) : undefined;
}

// Keep only well-formed strokes. A malformed one is dropped rather than
// repaired: a guessed stroke would fill pixels the user never brushed.
export function clampErase(erase: unknown): EraseStroke[] {
  if (!Array.isArray(erase)) return [];
  const out: EraseStroke[] = [];
  for (const item of erase.slice(0, ERASE_MAX_STROKES)) {
    const first = clampEraseLine(item);
    if (!first) continue;
    const { mode, more, grow, feather } = item as { mode?: unknown; more?: unknown; grow?: unknown; feather?: unknown };
    const lines = Array.isArray(more)
      ? more.slice(0, ERASE_MAX_LINES - 1).map(clampEraseLine).filter((l): l is EraseLine => l !== null)
      : [];
    const soft = { grow: clampSoft(grow), feather: clampSoft(feather) };
    out.push({
      ...first, ...(mode === "heal" ? { mode: "heal" as const } : {}), ...(lines.length ? { more: lines } : {}),
      ...(soft.grow !== undefined ? { grow: soft.grow } : {}), ...(soft.feather !== undefined ? { feather: soft.feather } : {}),
    });
  }
  return out;
}

// DRO strength, where 1 is what the camera itself applied. Absent means "as
// shot", which is not the same as 0 — the worker reads the shot's own answer
// out of the RAW. The ceiling matches sony/dro.py's DRO_MAX_STRENGTH.
export const DRO_MAX_STRENGTH = 2;
export function clampDroStrength(dro: unknown): number | undefined {
  if (dro === undefined || dro === null) return undefined;
  const value = Number(dro);
  if (!Number.isFinite(value)) return undefined;
  return Math.min(DRO_MAX_STRENGTH, Math.max(0, value));
}

// Which of Edit.exe's built-in DRO curves to use, on the engine's own 0..99
// level scale, where -1 means Auto — the curve the body wrote into this
// particular RAW. Absent means the client said nothing, which leaves whatever
// the profile already had, so -1 is a real choice and has to survive the clamp
// rather than being folded into "unset". Mirrors sony/dro_presets.py.
export const DRO_LEVEL_AUTO = -1;
export const DRO_LEVEL_MAX = 99;
export function clampDroLevel(level: unknown): number | undefined {
  if (level === undefined || level === null) return undefined;
  const value = Number(level);
  if (!Number.isFinite(value)) return undefined;
  return Math.min(DRO_LEVEL_MAX, Math.max(DRO_LEVEL_AUTO, Math.round(value)));
}

// Which Creative Look to render, when the client wants one other than the body's
// own. Only the shape is checked here: which looks a RAW actually carries is a
// property of that file, so the worker decides whether this one resolves and
// falls back to the shot's own look when it does not.
export function clampLookStyle(style: unknown): string | undefined {
  if (typeof style !== "string") return undefined;
  const trimmed = style.trim();
  return trimmed && trimmed.length <= 16 ? trimmed : undefined;
}

// Keep only the known keys carrying a real number. An absent field means "leave
// the shot's own setting alone", so anything unusable — null, NaN, a string —
// has to drop out rather than reach the worker as a zero, which would read as a
// deliberate override. The camera's own range is enforced worker-side.
export function clampLookTweaks(look: unknown): LookTweaks {
  if (!look || typeof look !== "object") return {};
  const out: LookTweaks = {};
  for (const key of LOOK_TWEAK_KEYS) {
    const v = (look as Record<string, unknown>)[key];
    if (typeof v === "number" && Number.isFinite(v)) out[key] = Math.trunc(v);
  }
  return out;
}

// Clamp/coerce what gets forwarded to the Python daemon: a malformed field
// would otherwise surface as a worker ValueError → opaque 500.
export function clampRenderParams(body: RenderLinearBody): RenderParams {
  const maxSizeRaw = Number(body.maxSize ?? 1600);
  const denoiseAmount = Number(body.denoise?.amount ?? 1);
  // 50 is neutral for both, so an absent field must land there rather than at 0.
  const clamp100 = (v: unknown) => {
    const n = Number(v ?? 50);
    return Number.isFinite(n) ? Math.min(100, Math.max(0, n)) : 50;
  };
  return {
    profile: typeof body.profileId === "string" && body.profileId ? body.profileId : "standard",
    halfSize: typeof body.halfSize === "boolean" ? body.halfSize : true,
    maxSize: Number.isFinite(maxSizeRaw) ? Math.min(16384, Math.max(0, Math.trunc(maxSizeRaw))) : 1600,
    dcpCode: typeof body.dcpCode === "string" ? body.dcpCode : undefined,
    // No cameraMatch here on purpose: the fitted table is a HueSatMap, and every
    // HueSatMap now ships to the shader rather than being baked into the decode,
    // so applying it is the frontend's call and never reaches the worker.
    denoise: {
      enabled: body.denoise?.enabled === true,
      // Auto makes the worker derive `amount` from the shot's ISO instead, the
      // way Edit ramps its RAW stage (four tenths to ISO 400, full from 1600).
      // The client still sends an `amount`, and it is still what gets used when
      // the file has no readable ISO — so this flag adds a source for the
      // number rather than replacing it.
      auto: body.denoise?.auto === true,
      amount: Number.isFinite(denoiseAmount) ? Math.min(1, Math.max(0, denoiseAmount)) : 1,
      edge: clamp100(body.denoise?.edge),
      chroma: clamp100(body.denoise?.chroma),
    },
    look: clampLookTweaks(body.look),
    style: clampLookStyle(body.style),
    dro: clampDroStrength(body.dro),
    droLevel: clampDroLevel(body.droLevel),
    erase: clampErase(body.erase),
    // Defaults to the cacheable path: a client that never states its intent is
    // treated as a preview, so at worst it costs memory rather than making
    // every subsequent edit re-decode.
    purpose: body.purpose === "export" ? "export" : "preview",
  };
}

// [u32 header length][JSON header, space-padded so the pixels stay 4-byte
// aligned for a zero-copy typed-array view][linear RGB in header.dtype].
export function buildLinearFrameHeader(meta: Record<string, unknown>): Buffer {
  return frameHeader({
    width: meta.width,
    height: meta.height,
    fullWidth: meta.fullWidth ?? null,
    fullHeight: meta.fullHeight ?? null,
    colorProfile: meta.colorProfile ?? null,
    dtype: typeof meta.dtype === "string" ? meta.dtype : "float32",
    // Which denoiser this frame got, and whether Color NR reaches it. Per-frame
    // rather than a constant: Sony RAWs run the transcription of Sony's own
    // filter, which has no such control, while anything without its noise tags
    // falls back to the wavelet, where the control works. The UI hides the
    // slider when this is false instead of leaving an inert one on screen.
    // Defaults to true so a worker that does not send it keeps showing the
    // control — the old behaviour — rather than hiding a working one.
    denoiseUsesChroma: meta.denoiseUsesChroma !== false,
    // How the erase strokes went: null when the request had none, otherwise
    // the count and, when the fills could not run (no model, no runtime), why —
    // the frame then arrives as decoded rather than not at all.
    erase: meta.erase && typeof meta.erase === "object" ? meta.erase : null,
  });
}

/** [u32 BE length][JSON, space-padded to 4 bytes so the payload after it stays aligned]. */
export function frameHeader(header: unknown): Buffer {
  let json = Buffer.from(JSON.stringify(header), "utf8");
  if (json.length % 4) json = Buffer.concat([json, Buffer.alloc(4 - (json.length % 4), 0x20)]);
  const prefix = Buffer.alloc(4);
  prefix.writeUInt32BE(json.length, 0);
  return Buffer.concat([prefix, json]);
}

// Private address space, i.e. what a LAN client of the dev server can be:
// loopback, RFC1918, link-local, and CGNAT (100.64/10, which Tailscale hands
// out). Octet comparison, so 172.19.x is in but 172.32.x is out.
function isPrivateIPv4(ip: string): boolean {
  const [a, b] = ip.split(".").map(Number);
  return (
    a === 127 ||
    a === 10 ||
    (a === 172 && b >= 16 && b <= 31) ||
    (a === 192 && b === 168) ||
    (a === 169 && b === 254) ||
    (a === 100 && b >= 64 && b <= 127)
  );
}

// Vite binds 0.0.0.0 precisely so the app can be opened from another machine
// (the Windows side of WSL, a phone on the LAN) by IP, and that page's origin
// is the IP it was opened at. An IP-literal origin in private address space
// can only be a page served from this network, so it gets the same trust as
// localhost. DNS names stay rejected: a hostile page on the internet carries
// its own domain, and a rebound domain is still a domain.
function isLocalHostname(hostname: string): boolean {
  if (hostname === "localhost") return true;
  if (isIP(hostname) === 4) return isPrivateIPv4(hostname);
  // WHATWG URL keeps the brackets on an IPv6 hostname.
  return hostname === "[::1]";
}

// Requests are limited to local origins: this is a localhost development tool
// and the API carries the user's original files. Withholding the CORS headers
// is not enough — a page the user happens to visit can still fire a
// simple-request POST and take the side effects — so this gates the request.
export function isLocalOrigin(origin: string): boolean {
  try {
    return isLocalHostname(new URL(origin).hostname);
  } catch {
    return false;
  }
}

// The one thing the origin gate cannot see is DNS rebinding: a page on a
// hostname that has been re-pointed at 127.0.0.1 is *same-origin* with the API,
// so its requests carry no Origin at all. The Host header still names the
// attacker's domain, which is what makes it separable from a real client.
export function isAllowedHost(hostHeader: string | undefined, bindHost: string): boolean {
  // Only HTTP/1.0 clients omit Host; no browser can be made to.
  if (hostHeader === undefined) return true;
  try {
    const { hostname } = new URL(`http://${hostHeader}`);
    return isLocalHostname(hostname) || hostname === bindHost;
  } catch {
    return false;
  }
}

// Not just validation: application/json is not a CORS-simple content type, so
// requiring it forces a cross-origin POST through a preflight the origin gate
// answers before any body is read.
export function isJsonContentType(value: string | undefined): boolean {
  return value?.split(";", 1)[0]?.trim().toLowerCase() === "application/json";
}

export function pickExtension(filename: string): string {
  const dot = filename.lastIndexOf(".");
  if (dot < 0) return "";
  const base = filename.slice(dot).toLowerCase();
  // extname-compatible: a leading dot (".bashrc") is a name, not an extension.
  return dot === 0 ? "" : base;
}

export const PHOTO_NAME_MAX = 200;

/**
 * A photo's display name. Imports are copies, so the name is a label rather
 * than a path — but it is the label the user types into the grid, so the rules
 * are the ones that keep it readable as a file name: non-empty once trimmed,
 * bounded, no control characters, no path separators. The file behind it does
 * not change type, so a bare stem gets the photo's own extension back
 * ("sunset" → "sunset.arw") and a *different* extension is refused rather than
 * silently kept. Returns the name to store, or null.
 */
export function normalizePhotoName(name: unknown, ext: string): string | null {
  const trimmed = displayLabel(name, PHOTO_NAME_MAX);
  if (!trimmed) return null;
  if (/[\\/]/.test(trimmed)) return null;
  if (ext && trimmed.toLowerCase().endsWith(ext.toLowerCase())) {
    // A name that is nothing but the extension ("‎.arw") has no stem left.
    return trimmed.length > ext.length ? trimmed : null;
  }
  if (pickExtension(trimmed)) return null;
  return `${trimmed}${ext}`;
}
