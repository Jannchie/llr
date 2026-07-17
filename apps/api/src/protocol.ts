/**
 * Pure request/response logic for the LLR API, kept free of I/O so it can be
 * unit-tested: source-id validation, render-parameter clamping, the binary
 * framing of /render-linear responses, and CORS origin checks.
 */

// The one definition of a valid source id (server-minted UUIDs), shared by
// the route patterns and sessionDirFor so they cannot drift apart.
export const SOURCE_ID = String.raw`[\w-]+`;
const SOURCE_ID_RE = new RegExp(`^${SOURCE_ID}$`);

// Reject anything else before it reaches resolve() — a traversal like "../x"
// or an absolute path would escape sessionsRoot (and /export writes there).
export function isValidSourceId(sourceId: string): boolean {
  return SOURCE_ID_RE.test(sourceId);
}

export interface RenderLinearBody {
  sourceId?: string;
  profileId?: string;
  halfSize?: boolean;
  maxSize?: number;
  dcpCode?: string;
  denoise?: { enabled?: boolean; model?: string; amount?: number };
}

export interface RenderParams {
  profile: string;
  halfSize: boolean;
  maxSize: number;
  dcpCode: string | undefined;
  denoise: { enabled: boolean; model: string | undefined; amount: number };
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
    denoise: {
      enabled: body.denoise?.enabled === true,
      model: typeof body.denoise?.model === "string" ? body.denoise.model : undefined,
      amount: Number.isFinite(denoiseAmount) ? Math.min(1, Math.max(0, denoiseAmount)) : 1,
    },
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

// CORS is limited to local origins: this is a localhost development tool and
// the API carries the user's original files.
export function isLocalOrigin(origin: string): boolean {
  try {
    const { hostname } = new URL(origin);
    return hostname === "localhost" || hostname === "127.0.0.1";
  } catch {
    return false;
  }
}

export function pickExtension(filename: string): string {
  const dot = filename.lastIndexOf(".");
  if (dot < 0) return "";
  const base = filename.slice(dot).toLowerCase();
  // extname-compatible: a leading dot (".bashrc") is a name, not an extension.
  return dot === 0 ? "" : base;
}
