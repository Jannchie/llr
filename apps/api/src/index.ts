import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { randomUUID } from "node:crypto";
import { createReadStream, existsSync } from "node:fs";
import { access, mkdir, readdir, readFile, rm, stat, writeFile } from "node:fs/promises";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { dirname, extname, resolve } from "node:path";
import { createInterface } from "node:readline";
import { Readable } from "node:stream";
import { fileURLToPath } from "node:url";

const port = Number(process.env.PORT ?? 8790);
const host = process.env.HOST ?? "127.0.0.1";
const repoRoot = resolveRepoRoot();
const sessionsRoot = resolve(repoRoot, "tmp/sessions");
// Upload formats the worker can decode: RAW via LibRaw, plus plain images
// (jpg/png/tiff) via Pillow. Broader than the worker's RAW_EXTENSIONS, which
// answers "is this a RAW file", not "can we ingest it".
const SUPPORTED_EXTENSIONS = new Set([
  ".arw",
  ".srf",
  ".sr2",
  ".dng",
  ".cr2",
  ".cr3",
  ".nef",
  ".raf",
  ".rw2",
  ".orf",
  ".jpg",
  ".jpeg",
  ".png",
  ".tif",
  ".tiff"
]);

const JSON_BODY_LIMIT = 10 * 1024 * 1024;
const FORM_BODY_LIMIT = 512 * 1024 * 1024;
const SESSION_TTL_MS = 7 * 24 * 60 * 60 * 1000;

class HttpError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

const server = createServer((request, response) => {
  setCors(request, response);
  if (request.method === "OPTIONS") {
    response.writeHead(204).end();
    return;
  }

  void route(request, response).catch((error: unknown) => {
    console.error(error);
    if (!response.headersSent) {
      sendJson(response, { error: errorMessage(error) }, error instanceof HttpError ? error.status : 500);
    } else {
      response.end();
    }
  });
});

server.listen(port, host, () => {
  console.log(`LLR API listening on http://${host}:${port}`);
  console.log(`Workspace root: ${repoRoot}`);
});

void cleanupSessions();
setInterval(() => void cleanupSessions(), 60 * 60 * 1000).unref();

async function route(request: IncomingMessage, response: ServerResponse): Promise<void> {
  const url = new URL(request.url ?? "/", `http://${request.headers.host ?? "localhost"}`);
  const pathname = url.pathname;
  const method = request.method ?? "GET";

  if (method === "GET" && pathname === "/health") {
    sendJson(response, { ok: true });
    return;
  }

  if (method === "POST" && pathname === "/sources") {
    await handleSourceUpload(request, response);
    return;
  }

  // Remove the server-side cached copy of an import. Only ever touches
  // tmp/sessions — the user's original file never enters this system.
  const sourceMatch = pathname.match(/^\/sources\/([\w-]+)$/);
  if (method === "DELETE" && sourceMatch) {
    await rm(resolve(sessionsRoot, sourceMatch[1]), { recursive: true, force: true });
    sendJson(response, { ok: true });
    return;
  }

  const embeddedMatch = pathname.match(/^\/sources\/([\w-]+)\/embedded\.jpg$/);
  if (method === "GET" && embeddedMatch) {
    streamFile(response, resolve(sessionsRoot, embeddedMatch[1], "embedded.jpg"));
    return;
  }

  if (method === "POST" && pathname === "/render-linear") {
    await handleRenderLinear(request, response);
    return;
  }

  if (method === "POST" && pathname === "/export") {
    await handleExport(request, response);
    return;
  }

  sendJson(response, { error: "Not found", path: pathname }, 404);
}

async function handleSourceUpload(request: IncomingMessage, response: ServerResponse): Promise<void> {
  const form = await readFormData(request);
  const file = form.get("file");
  if (!(file instanceof File)) {
    sendJson(response, { error: "Missing file field" }, 400);
    return;
  }

  const ext = pickExtension(file.name);
  if (!SUPPORTED_EXTENSIONS.has(ext)) {
    sendJson(response, { error: `Unsupported extension: ${ext || "(none)"}` }, 415);
    return;
  }

  const id = randomUUID();
  const sessionDir = resolve(sessionsRoot, id);
  await mkdir(sessionDir, { recursive: true });

  const sourcePath = resolve(sessionDir, `source${ext}`);
  await writeFile(sourcePath, Buffer.from(await file.arrayBuffer()));

  const embeddedPath = resolve(sessionDir, "embedded.jpg");
  await daemon.send({ command: "extract-preview", input: sourcePath, output: embeddedPath });

  sendJson(response, {
    id,
    name: file.name,
    size: file.size,
    embeddedUrl: `/sources/${id}/embedded.jpg`,
    renderUrl: null
  }, 201);
}

// Session ids are server-minted UUIDs; reject anything else before it reaches
// resolve() — a traversal like "../x" or an absolute path would escape
// sessionsRoot (and /export writes there). Same shape the GET/DELETE routes match.
function sessionDirFor(sourceId: string): string {
  if (!/^[\w-]+$/.test(sourceId)) throw new HttpError(400, "Invalid sourceId");
  return resolve(sessionsRoot, sourceId);
}

async function handleRenderLinear(request: IncomingMessage, response: ServerResponse): Promise<void> {
  const body = await readJson<{
    sourceId: string;
    profileId?: string;
    halfSize?: boolean;
    maxSize?: number;
    dcpCode?: string;
    denoise?: { enabled?: boolean; model?: string; amount?: number };
  }>(request);
  if (!body.sourceId) {
    sendJson(response, { error: "Missing sourceId" }, 400);
    return;
  }
  const sessionDir = sessionDirFor(body.sourceId);
  const sourcePath = await findSource(sessionDir);
  if (!sourcePath) {
    sendJson(response, { error: "Unknown sourceId" }, 404);
    return;
  }

  // Clamp/coerce what gets forwarded to the Python daemon: a malformed field
  // would otherwise surface as a worker ValueError → opaque 500.
  const maxSizeRaw = Number(body.maxSize ?? 1600);
  const maxSize = Number.isFinite(maxSizeRaw) ? Math.min(16384, Math.max(0, Math.trunc(maxSizeRaw))) : 1600;
  const denoiseAmount = Number(body.denoise?.amount ?? 1);
  const denoise = {
    enabled: body.denoise?.enabled === true,
    model: typeof body.denoise?.model === "string" ? body.denoise.model : undefined,
    amount: Number.isFinite(denoiseAmount) ? Math.min(1, Math.max(0, denoiseAmount)) : 1,
  };

  // Per-request filename (concurrent renders must not overwrite each other),
  // deleted right after the read: the linear data goes back in this response
  // body instead of round-tripping through a second GET.
  const outputPath = resolve(sessionDir, `linear-${randomUUID()}.bin`);
  let meta;
  let data: Buffer;
  try {
    meta = await daemon.send({
      command: "render-linear",
      input: sourcePath,
      output: outputPath,
      profile: body.profileId ?? "standard",
      halfSize: typeof body.halfSize === "boolean" ? body.halfSize : true,
      maxSize,
      recipe: { autoTone: false },
      dcpCode: typeof body.dcpCode === "string" ? body.dcpCode : undefined,
      denoise,
    });
    data = await readFile(outputPath);
  } finally {
    await rm(outputPath, { force: true });
  }

  // [u32 header length][JSON header, space-padded so the pixels stay 4-byte
  // aligned for a zero-copy Float32Array view][float32 linear RGB].
  let header = Buffer.from(JSON.stringify({
    width: meta.width,
    height: meta.height,
    fullWidth: meta.fullWidth ?? null,
    fullHeight: meta.fullHeight ?? null,
    colorProfile: meta.colorProfile ?? null,
  }), "utf8");
  if (header.length % 4) header = Buffer.concat([header, Buffer.alloc(4 - (header.length % 4), 0x20)]);
  const prefix = Buffer.alloc(4);
  prefix.writeUInt32BE(header.length, 0);
  response.writeHead(200, { "content-type": "application/octet-stream", "cache-control": "no-store" });
  response.end(Buffer.concat([prefix, header, data]));
}

async function handleExport(request: IncomingMessage, response: ServerResponse): Promise<void> {
  const form = await readFormData(request);
  const file = form.get("file");
  const metaRaw = form.get("meta");
  if (!(file instanceof File)) {
    sendJson(response, { error: "Missing file field" }, 400);
    return;
  }

  let meta: { sourceId?: string; settings?: unknown };
  try {
    meta = JSON.parse(typeof metaRaw === "string" ? metaRaw : "{}") as { sourceId?: string; settings?: unknown };
  } catch {
    sendJson(response, { error: "Invalid meta field" }, 400);
    return;
  }
  if (!meta.sourceId) {
    sendJson(response, { error: "Missing sourceId" }, 400);
    return;
  }

  const sessionDir = sessionDirFor(meta.sourceId);
  const sourcePath = await findSource(sessionDir);
  if (!sourcePath) {
    sendJson(response, { error: "Unknown sourceId" }, 404);
    return;
  }

  // Per-request filename: concurrent exports of the same source must not
  // overwrite each other's file between the write, the daemon's in-place XMP
  // embed, and the response stream.
  const exportPath = resolve(sessionDir, `export-${randomUUID()}.jpg`);
  try {
    await writeFile(exportPath, Buffer.from(await file.arrayBuffer()));
    await daemon.send({
      command: "export",
      input: sourcePath,
      target: exportPath,
      settings: meta.settings ?? {}
    });
  } catch (error) {
    await rm(exportPath, { force: true });
    throw error;
  }
  streamFile(response, exportPath, () => void rm(exportPath, { force: true }));
}

async function cleanupSessions(): Promise<void> {
  let entries;
  try {
    entries = await readdir(sessionsRoot, { withFileTypes: true });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
      console.warn(`session cleanup failed: ${errorMessage(error)}`);
    }
    return;
  }
  const now = Date.now();
  for (const entry of entries) {
    if (!entry.isDirectory()) {
      continue;
    }
    const dir = resolve(sessionsRoot, entry.name);
    try {
      const stats = await stat(dir);
      if (now - stats.mtimeMs > SESSION_TTL_MS) {
        await rm(dir, { recursive: true, force: true });
      }
    } catch (error) {
      console.warn(`session cleanup failed for ${dir}: ${errorMessage(error)}`);
    }
  }
}

async function findSource(sessionDir: string): Promise<string | null> {
  for (const ext of SUPPORTED_EXTENSIONS) {
    const candidate = resolve(sessionDir, `source${ext}`);
    try {
      await access(candidate);
      return candidate;
    } catch {
      continue;
    }
  }
  return null;
}

type DaemonResponse = Record<string, unknown> & { ok?: boolean; error?: string };

class WorkerDaemon {
  private child: ChildProcessWithoutNullStreams | null = null;
  private readonly pending = new Map<string, { resolve: (value: DaemonResponse) => void; reject: (error: Error) => void }>();
  private readonly cwd: string;
  private booting: Promise<void> | null = null;

  constructor(cwd: string) {
    this.cwd = cwd;
  }

  async send(payload: Record<string, unknown>, timeoutMs = 120_000): Promise<DaemonResponse> {
    await this.ensureRunning();
    const id = randomUUID();
    return new Promise<DaemonResponse>((resolvePromise, rejectPromise) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        rejectPromise(new Error(`worker daemon request timed out after ${timeoutMs}ms`));
      }, timeoutMs);
      this.pending.set(id, {
        resolve: (value) => {
          clearTimeout(timer);
          resolvePromise(value);
        },
        reject: (error) => {
          clearTimeout(timer);
          rejectPromise(error);
        }
      });
      const child = this.child;
      if (!child) {
        clearTimeout(timer);
        this.pending.delete(id);
        rejectPromise(new Error("worker daemon is not running"));
        return;
      }
      child.stdin.write(`${JSON.stringify({ ...payload, id })}\n`);
    });
  }

  private async ensureRunning(): Promise<void> {
    if (this.child) {
      return;
    }
    if (!this.booting) {
      this.booting = this.boot();
    }
    await this.booting;
  }

  private async boot(): Promise<void> {
    const child = spawn("uv", ["run", "--project", "apps/worker", "llr-worker", "daemon"], {
      cwd: this.cwd,
      env: { ...process.env, INIT_CWD: this.cwd, PYTHONUNBUFFERED: "1" }
    });

    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (chunk: string) => {
      process.stderr.write(`[worker] ${chunk}`);
    });

    const ready = new Promise<void>((resolveReady, rejectReady) => {
      const onReady = (chunk: string): void => {
        if (chunk.includes("daemon ready")) {
          child.stderr.off("data", onReady);
          resolveReady();
        }
      };
      child.stderr.on("data", onReady);
      child.once("error", rejectReady);
      child.once("exit", (code) => {
        rejectReady(new Error(`worker daemon exited before ready (code ${code ?? "null"})`));
      });
    });

    const reader = createInterface({ input: child.stdout });
    reader.on("line", (line: string) => this.handleLine(line));

    child.on("exit", (code) => {
      const error = new Error(`worker daemon exited with code ${code ?? "null"}`);
      for (const [, handler] of this.pending) {
        handler.reject(error);
      }
      this.pending.clear();
      this.child = null;
      this.booting = null;
    });

    this.child = child;
    await ready;
    this.booting = null;
  }

  private handleLine(line: string): void {
    const trimmed = line.trim();
    if (!trimmed) {
      return;
    }
    let payload: DaemonResponse;
    try {
      payload = JSON.parse(trimmed) as DaemonResponse;
    } catch {
      console.warn(`[worker] non-JSON line: ${trimmed}`);
      return;
    }
    const id = typeof payload.id === "string" ? payload.id : null;
    if (!id) {
      return;
    }
    const handler = this.pending.get(id);
    if (!handler) {
      return;
    }
    this.pending.delete(id);
    if (payload.ok === false) {
      handler.reject(new Error(typeof payload.error === "string" ? payload.error : "worker error"));
    } else {
      handler.resolve(payload);
    }
  }
}

const daemon = new WorkerDaemon(repoRoot);

async function readFormData(request: IncomingMessage): Promise<FormData> {
  const headers = new Headers();
  for (const [key, value] of Object.entries(request.headers)) {
    if (typeof value === "string") {
      headers.set(key, value);
    } else if (Array.isArray(value)) {
      headers.set(key, value.join(", "));
    }
  }

  let exceeded = false;
  const limited = Readable.from((async function* () {
    let total = 0;
    for await (const chunk of request) {
      total += (chunk as Buffer).length;
      if (total > FORM_BODY_LIMIT) {
        exceeded = true;
        throw new HttpError(413, "Request body too large");
      }
      yield chunk as Buffer;
    }
  })());

  const body = Readable.toWeb(limited) as unknown as BodyInit;
  const fakeUrl = `http://localhost${request.url ?? "/"}`;
  const req = new Request(fakeUrl, { method: "POST", headers, body, duplex: "half" } as RequestInit & { duplex: "half" });
  try {
    return await req.formData();
  } catch (error) {
    if (exceeded) {
      throw new HttpError(413, "Request body too large");
    }
    throw error;
  }
}

async function readJson<T>(request: IncomingMessage): Promise<T> {
  const chunks: Buffer[] = [];
  let total = 0;
  for await (const chunk of request) {
    total += (chunk as Buffer).length;
    if (total > JSON_BODY_LIMIT) {
      throw new HttpError(413, "Request body too large");
    }
    chunks.push(chunk as Buffer);
  }
  const text = Buffer.concat(chunks).toString("utf8");
  if (!text) {
    return {} as T;
  }
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new HttpError(400, "Invalid JSON body");
  }
}

function streamFile(response: ServerResponse, path: string, onClose?: () => void): void {
  const stream = createReadStream(path);
  if (onClose) stream.once("close", onClose);
  stream.once("open", () => {
    response.writeHead(200, {
      "content-type": "image/jpeg",
      "cache-control": "no-store"
    });
    stream.pipe(response);
  });
  stream.on("error", (error: NodeJS.ErrnoException) => {
    if (response.headersSent) {
      response.destroy();
      return;
    }
    if (error.code === "ENOENT") {
      sendJson(response, { error: "Not found" }, 404);
    } else {
      sendJson(response, { error: errorMessage(error) }, 500);
    }
  });
}

function pickExtension(filename: string): string {
  const ext = extname(filename).toLowerCase();
  return ext;
}

function sendJson(response: ServerResponse, body: unknown, status = 200): void {
  response.writeHead(status, { "content-type": "application/json; charset=utf-8" });
  response.end(JSON.stringify(body, null, 2));
}

function setCors(request: IncomingMessage, response: ServerResponse): void {
  const origin = request.headers.origin;
  if (typeof origin !== "string" || !isLocalOrigin(origin)) {
    return;
  }
  response.setHeader("Access-Control-Allow-Origin", origin);
  response.setHeader("Access-Control-Allow-Headers", "content-type");
  response.setHeader("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS");
}

function isLocalOrigin(origin: string): boolean {
  try {
    const { hostname } = new URL(origin);
    return hostname === "localhost" || hostname === "127.0.0.1";
  } catch {
    return false;
  }
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}

function resolveRepoRoot(): string {
  const here = dirname(fileURLToPath(import.meta.url));
  let current = here;
  for (let i = 0; i < 8; i += 1) {
    if (existsSync(resolve(current, "pnpm-workspace.yaml"))) {
      return current;
    }
    const parent = dirname(current);
    if (parent === current) {
      break;
    }
    current = parent;
  }
  return process.cwd();
}
