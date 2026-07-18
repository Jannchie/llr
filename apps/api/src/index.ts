import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { randomUUID } from "node:crypto";
import { createReadStream, existsSync } from "node:fs";
import { access, mkdir, readdir, rm, stat, writeFile } from "node:fs/promises";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { dirname, resolve } from "node:path";
import { createInterface } from "node:readline";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";
import { fileURLToPath } from "node:url";

import {
  SOURCE_ID,
  buildLinearFrameHeader,
  clampRenderParams,
  isAllowedHost,
  isJsonContentType,
  isLocalOrigin,
  isValidSourceId,
  pickExtension,
  type RenderLinearBody,
} from "./protocol.js";

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
// linear-*.bin / export-*.jpg are per-request scratch that the request itself
// deletes. Anything still there (the API died mid-render) is orphaned inside a
// session dir whose mtime every later render refreshes, so the dir-level TTL
// will never reach it. Well above the daemon's request timeout.
const SCRATCH_TTL_MS = 60 * 60 * 1000;
const SCRATCH_FILE = /^(?:linear-[\w-]+\.bin|export-[\w-]+\.jpg)$/;

class HttpError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

const server = createServer((request, response) => {
  if (!isTrustedRequest(request)) {
    sendJson(response, { error: "Forbidden" }, 403);
    return;
  }
  setCors(request, response);
  if (request.method === "OPTIONS") {
    response.writeHead(204).end();
    return;
  }

  void route(request, response).catch((error: unknown) => {
    // A client that walks away mid-response is routine (any tab reload during a
    // render), and pipeline surfaces it as an error; logging it would drown the
    // failures that do mean something.
    if (isClientAbort(error)) {
      response.destroy();
      return;
    }
    console.error(error);
    if (!response.headersSent) {
      sendJson(response, { error: errorMessage(error) }, error instanceof HttpError ? error.status : 500);
    } else {
      response.end();
    }
  });
});

// Destroy sockets with no traffic in either direction for 5 minutes. The WSL2
// localhost relay drops its Windows half without RST-ing the Linux half, so a
// client that vanishes mid-stream leaves the response pipeline (and its
// finally-rm of linear-*.bin / export-*.jpg scratch) pinned forever. Five
// minutes clears any real transfer stall while staying far above the longest
// daemon wait a response idles through (full-res decode + denoise).
server.setTimeout(5 * 60 * 1000);

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
  const sourceId = pathname.match(SOURCE_ROUTE)?.[1];
  if (method === "DELETE" && sourceId) {
    await rm(sessionDirFor(sourceId), { recursive: true, force: true });
    sendJson(response, { ok: true });
    return;
  }

  const embeddedId = pathname.match(EMBEDDED_ROUTE)?.[1];
  if (method === "GET" && embeddedId) {
    await streamFile(response, resolve(sessionDirFor(embeddedId), "embedded.jpg"));
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

const SOURCE_ROUTE = new RegExp(`^/sources/(${SOURCE_ID})$`);
const EMBEDDED_ROUTE = new RegExp(`^/sources/(${SOURCE_ID})/embedded\\.jpg$`);

function sessionDirFor(sourceId: string): string {
  if (!isValidSourceId(sourceId)) throw new HttpError(400, "Invalid sourceId");
  return resolve(sessionsRoot, sourceId);
}

// Backpressure for the heavy decode path: each render buffers tens of MB on
// disk and occupies one of the daemon's three workers, so an unbounded pile-up
// (a stuck client in a retry loop, a tab spamming slider changes) would queue
// memory and worker time without limit. Excess requests get a fast 429; the
// UI's own sequencing (loadSeq, debounces) keeps it far from this ceiling.
const RENDER_INFLIGHT_LIMIT = 8;

async function handleRenderLinear(request: IncomingMessage, response: ServerResponse): Promise<void> {
  const body = await readJson<RenderLinearBody>(request);
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

  if (daemon.outstanding("render-linear") >= RENDER_INFLIGHT_LIMIT) {
    throw new HttpError(429, "Too many concurrent renders");
  }

  const params = clampRenderParams(body);

  // Per-request filename (concurrent renders must not overwrite each other),
  // deleted once the response is done with it: the linear data goes back in
  // this response body instead of round-tripping through a second GET.
  const outputPath = resolve(sessionDir, `linear-${randomUUID()}.bin`);
  let meta;
  try {
    meta = await daemon.send({
      command: "render-linear",
      input: sourcePath,
      output: outputPath,
      profile: params.profile,
      halfSize: params.halfSize,
      maxSize: params.maxSize,
      recipe: { autoTone: false },
      dcpCode: params.dcpCode,
      denoise: params.denoise,
    });
  } catch (error) {
    await rm(outputPath, { force: true });
    throw error;
  }

  // The pixel payload (tens of MB) is streamed from disk instead of being
  // buffered whole in memory; see buildLinearFrameHeader for the framing.
  const frameHeader = buildLinearFrameHeader(meta);
  const pixelBytes = Number(meta.bytesWritten);
  const headers: Record<string, string> = { "content-type": "application/octet-stream", "cache-control": "no-store" };
  if (Number.isFinite(pixelBytes) && pixelBytes >= 0) {
    headers["content-length"] = String(frameHeader.length + pixelBytes);
  }
  try {
    response.writeHead(200, headers);
    response.write(frameHeader);
    await pipeline(createReadStream(outputPath), response);
  } finally {
    await rm(outputPath, { force: true });
  }
}

async function handleExport(request: IncomingMessage, response: ServerResponse): Promise<void> {
  const form = await readFormData(request);
  const file = form.get("file");
  const metaRaw = form.get("meta");
  if (!(file instanceof File)) {
    sendJson(response, { error: "Missing file field" }, 400);
    return;
  }

  let meta: { sourceId?: string; settings?: unknown; stripPrivate?: boolean };
  try {
    meta = JSON.parse(typeof metaRaw === "string" ? metaRaw : "{}") as typeof meta;
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
      settings: meta.settings ?? {},
      stripPrivate: meta.stripPrivate === true
    });
    await streamFile(response, exportPath);
  } finally {
    await rm(exportPath, { force: true });
  }
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
        continue;
      }
      await sweepScratch(dir, now);
    } catch (error) {
      console.warn(`session cleanup failed for ${dir}: ${errorMessage(error)}`);
    }
  }
}

async function sweepScratch(dir: string, now: number): Promise<void> {
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    if (!entry.isFile() || !SCRATCH_FILE.test(entry.name)) {
      continue;
    }
    const path = resolve(dir, entry.name);
    const stats = await stat(path);
    if (now - stats.mtimeMs > SCRATCH_TTL_MS) {
      await rm(path, { force: true });
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

type DaemonHandler = { resolve: (value: DaemonResponse) => void; reject: (error: Error) => void };

type InflightRequest = {
  command: string;
  // Files the Python side writes. Needed once a caller has given up: nothing
  // else knows the path of a file that does not exist yet.
  outputs: string[];
  // null once the caller has timed out and stopped waiting for the reply.
  handler: DaemonHandler | null;
};

class WorkerDaemon {
  private child: ChildProcessWithoutNullStreams | null = null;
  // Every request written to the daemon that has not replied — including ones
  // whose caller has given up, because the Python side cannot be cancelled and
  // is still holding a worker.
  private readonly inflight = new Map<string, InflightRequest>();
  private readonly cwd: string;
  private booting: Promise<void> | null = null;

  constructor(cwd: string) {
    this.cwd = cwd;
  }

  outstanding(command: string): number {
    let count = 0;
    for (const request of this.inflight.values()) {
      if (request.command === command) count += 1;
    }
    return count;
  }

  async send(payload: Record<string, unknown>, timeoutMs = 120_000): Promise<DaemonResponse> {
    const id = randomUUID();
    const request: InflightRequest = {
      command: typeof payload.command === "string" ? payload.command : "",
      outputs: [payload.output, payload.target].filter((value): value is string => typeof value === "string"),
      handler: null
    };
    // Registered before the boot await so outstanding() already counts it: a
    // caller checks the limit and calls send() with nothing awaited between.
    this.inflight.set(id, request);
    try {
      await this.ensureRunning();
    } catch (error) {
      this.inflight.delete(id);
      throw error;
    }
    return new Promise<DaemonResponse>((resolvePromise, rejectPromise) => {
      const timer = setTimeout(() => {
        // A timeout cannot cancel the Python side (a running thread cannot be
        // killed), so the request stays in flight: it still occupies a worker,
        // and it will still write its output — which handleLine then deletes,
        // since the caller has already rm'd the path it knew about.
        request.handler = null;
        rejectPromise(new Error(`worker daemon request timed out after ${timeoutMs}ms`));
      }, timeoutMs);
      request.handler = {
        resolve: (value) => {
          clearTimeout(timer);
          resolvePromise(value);
        },
        reject: (error) => {
          clearTimeout(timer);
          rejectPromise(error);
        }
      };
      const child = this.child;
      if (!child) {
        clearTimeout(timer);
        this.inflight.delete(id);
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

    let rejectReady!: (error: Error) => void;
    const ready = new Promise<void>((resolveReady, reject) => {
      rejectReady = reject;
      const onReady = (chunk: string): void => {
        if (chunk.includes("daemon ready")) {
          child.stderr.off("data", onReady);
          resolveReady();
        }
      };
      child.stderr.on("data", onReady);
    });

    const reader = createInterface({ input: child.stdout });
    reader.on("line", (line: string) => this.handleLine(line));

    // The single death path for this child. A failed spawn fires `error` with
    // no matching `exit` (an exit-only cleanup would cache a dead child
    // forever), and writing to a dead stdin surfaces as an async `error`
    // event that would crash the whole process if unhandled. Rejecting
    // `ready` here also fails a boot in progress (a no-op once resolved).
    let cleaned = false;
    const cleanup = (cause: Error): void => {
      if (cleaned) return; // exit + error can both fire for the same child;
      cleaned = true;      // a late second event must not reset a newer boot
      rejectReady(cause);
      for (const [, request] of this.inflight) {
        // A dead child writes nothing more, so an abandoned request's output is
        // whatever it managed to leave behind.
        if (request.handler) request.handler.reject(cause);
        else discardOutputs(request);
      }
      this.inflight.clear();
      if (this.child === child) this.child = null;
      this.booting = null;
    };
    child.on("exit", (code) => cleanup(new Error(`worker daemon exited with code ${code ?? "null"}`)));
    child.on("error", (error) => cleanup(new Error(`worker daemon failed: ${error.message}`)));
    child.stdin.on("error", (error) => cleanup(new Error(`worker daemon stdin error: ${error.message}`)));

    await ready;
    // Publish only a daemon that reached ready; ensureRunning treats a
    // non-null child as usable.
    this.child = child;
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
    const request = this.inflight.get(id);
    if (!request) {
      return;
    }
    this.inflight.delete(id);
    if (!request.handler) {
      discardOutputs(request);
      return;
    }
    if (payload.ok === false) {
      request.handler.reject(new Error(typeof payload.error === "string" ? payload.error : "worker error"));
    } else {
      request.handler.resolve(payload);
    }
  }
}

// The file a timed-out request finally produced. Its caller is long gone and
// rm'd the path before the daemon re-created it, so this is the last chance:
// nothing else in the system holds the name.
function discardOutputs(request: InflightRequest): void {
  for (const output of request.outputs) {
    void rm(output, { force: true }).catch((error: unknown) => {
      console.warn(`failed to discard ${output}: ${errorMessage(error)}`);
    });
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
  if (!isJsonContentType(request.headers["content-type"])) {
    throw new HttpError(415, "Expected content-type: application/json");
  }
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

// pipeline, not pipe: pipe() only *unpipes* its source when the destination
// closes, so an aborted client would leave the read stream — and its fd, which
// pins the file's disk space for this process's lifetime even once unlinked —
// open forever.
async function streamFile(response: ServerResponse, path: string): Promise<void> {
  const stream = createReadStream(path);
  try {
    // Wait for the open so a missing file is still a JSON error rather than a
    // truncated 200.
    await new Promise<void>((resolveOpen, rejectOpen) => {
      stream.once("open", () => resolveOpen());
      stream.once("error", rejectOpen);
    });
  } catch (error) {
    stream.destroy();
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      sendJson(response, { error: "Not found" }, 404);
    } else {
      sendJson(response, { error: errorMessage(error) }, 500);
    }
    return;
  }
  response.writeHead(200, {
    "content-type": "image/jpeg",
    "cache-control": "no-store"
  });
  await pipeline(stream, response);
}

function sendJson(response: ServerResponse, body: unknown, status = 200): void {
  response.writeHead(status, { "content-type": "application/json; charset=utf-8" });
  response.end(JSON.stringify(body, null, 2));
}

// Runs before anything reads the body: a hostile page's request must not land
// its side effects (a 512MB upload in tmp/sessions, attacker bytes handed to
// LibRaw's parser) just because the reply it gets back is opaque to it.
function isTrustedRequest(request: IncomingMessage): boolean {
  const origin = request.headers.origin;
  if (typeof origin === "string" && !isLocalOrigin(origin)) {
    return false;
  }
  return isAllowedHost(request.headers.host, host);
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

// The destination end of a pipeline() failing once the client is gone.
function isClientAbort(error: unknown): boolean {
  const code = (error as NodeJS.ErrnoException | undefined)?.code;
  return code === "ERR_STREAM_PREMATURE_CLOSE" || code === "ECONNRESET" || code === "EPIPE";
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
