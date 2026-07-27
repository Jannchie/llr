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

// The in-camera Creative Look tweaks, on Sony's own scales. Sent only for the
// fields the client is actually overriding: the worker fills the rest in from
// what the body recorded, so a moved slider does not have to echo the others.
// Ranges are the camera's and are enforced worker-side (sony/profile.py).
export const LOOK_TWEAK_KEYS = ["highlights", "shadows", "contrast", "fade", "saturation"] as const;
export type LookTweaks = Partial<Record<(typeof LOOK_TWEAK_KEYS)[number], number>>;

export interface RenderLinearBody {
  sourceId?: string;
  profileId?: string;
  halfSize?: boolean;
  maxSize?: number;
  dcpCode?: string;
  cameraMatch?: boolean;
  denoise?: { enabled?: boolean; model?: string; amount?: number };
  look?: LookTweaks;
  purpose?: string;
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
  cameraMatch: boolean;
  denoise: { enabled: boolean; model: string | undefined; amount: number };
  look: LookTweaks;
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
  return {
    profile: typeof body.profileId === "string" && body.profileId ? body.profileId : "standard",
    halfSize: typeof body.halfSize === "boolean" ? body.halfSize : true,
    maxSize: Number.isFinite(maxSizeRaw) ? Math.min(16384, Math.max(0, Math.trunc(maxSizeRaw))) : 1600,
    dcpCode: typeof body.dcpCode === "string" ? body.dcpCode : undefined,
    // Defaults on when omitted — a client that never sets it still gets the
    // fitted match, matching the worker's own default.
    cameraMatch: body.cameraMatch !== false,
    denoise: {
      enabled: body.denoise?.enabled === true,
      model: typeof body.denoise?.model === "string" ? body.denoise.model : undefined,
      amount: Number.isFinite(denoiseAmount) ? Math.min(1, Math.max(0, denoiseAmount)) : 1,
    },
    look: clampLookTweaks(body.look),
    // Defaults to the cacheable path: a client that never states its intent is
    // treated as a preview, so at worst it costs memory rather than making
    // every subsequent edit re-decode.
    purpose: body.purpose === "export" ? "export" : "preview",
  };
}

// [u32 header length][JSON header, space-padded so the pixels stay 4-byte
// aligned for a zero-copy typed-array view][linear RGB in header.dtype].
export function buildLinearFrameHeader(meta: Record<string, unknown>): Buffer {
  let header = Buffer.from(JSON.stringify({
    width: meta.width,
    height: meta.height,
    fullWidth: meta.fullWidth ?? null,
    fullHeight: meta.fullHeight ?? null,
    colorProfile: meta.colorProfile ?? null,
    dtype: typeof meta.dtype === "string" ? meta.dtype : "float32",
  }), "utf8");
  if (header.length % 4) header = Buffer.concat([header, Buffer.alloc(4 - (header.length % 4), 0x20)]);
  const prefix = Buffer.alloc(4);
  prefix.writeUInt32BE(header.length, 0);
  return Buffer.concat([prefix, header]);
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
