import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { randomUUID } from "node:crypto";
import { createReadStream, existsSync } from "node:fs";
import { access, mkdir, writeFile } from "node:fs/promises";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { dirname, extname, resolve } from "node:path";
import { createInterface } from "node:readline";
import { Readable } from "node:stream";
import { fileURLToPath } from "node:url";
import { profiles } from "@llr/profiles";

type RecipeBody = {
  sourceId: string;
  recipe: Record<string, unknown>;
  profileId?: string;
  autoTone?: boolean;
};

const port = Number(process.env.PORT ?? 8790);
const repoRoot = resolveRepoRoot();
const sessionsRoot = resolve(repoRoot, "tmp/sessions");
const RAW_EXTENSIONS = new Set([
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

const NUMERIC_RECIPE_KEYS = [
  "exposure",
  "contrast",
  "highlights",
  "shadows",
  "whites",
  "blacks",
  "vibrance",
  "saturation",
  "clarity",
  "dehaze",
  "sharpen"
] as const;

const server = createServer((request, response) => {
  setCors(response);
  if (request.method === "OPTIONS") {
    response.writeHead(204).end();
    return;
  }

  void route(request, response).catch((error: unknown) => {
    console.error(error);
    if (!response.headersSent) {
      sendJson(response, { error: errorMessage(error) }, 500);
    } else {
      response.end();
    }
  });
});

server.listen(port, () => {
  console.log(`LLR API listening on http://localhost:${port}`);
  console.log(`Workspace root: ${repoRoot}`);
});

async function route(request: IncomingMessage, response: ServerResponse): Promise<void> {
  const url = new URL(request.url ?? "/", `http://${request.headers.host ?? "localhost"}`);
  const pathname = url.pathname;
  const method = request.method ?? "GET";

  if (method === "GET" && pathname === "/health") {
    sendJson(response, { ok: true });
    return;
  }

  if (method === "GET" && pathname === "/profiles") {
    sendJson(response, { profiles });
    return;
  }

  if (method === "POST" && pathname === "/sources") {
    await handleSourceUpload(request, response);
    return;
  }

  const embeddedMatch = pathname.match(/^\/sources\/([\w-]+)\/embedded\.jpg$/);
  if (method === "GET" && embeddedMatch) {
    streamFile(response, resolve(sessionsRoot, embeddedMatch[1], "embedded.jpg"));
    return;
  }

  const renderMatch = pathname.match(/^\/sources\/([\w-]+)\/render\.jpg$/);
  if (method === "GET" && renderMatch) {
    streamFile(response, resolve(sessionsRoot, renderMatch[1], "render.jpg"));
    return;
  }

  if (method === "POST" && pathname === "/render") {
    await handleRender(request, response);
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

  const linearMatch = pathname.match(/^\/sources\/([\w-]+)\/linear\.bin$/);
  if (method === "GET" && linearMatch) {
    streamBinary(response, resolve(sessionsRoot, linearMatch[1], "linear.bin"));
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
  if (!RAW_EXTENSIONS.has(ext)) {
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

async function handleRender(request: IncomingMessage, response: ServerResponse): Promise<void> {
  const body = await readJson<RecipeBody>(request);
  if (!body.sourceId) {
    sendJson(response, { error: "Missing sourceId" }, 400);
    return;
  }

  const sessionDir = resolve(sessionsRoot, body.sourceId);
  const sourcePath = await findSource(sessionDir);
  if (!sourcePath) {
    sendJson(response, { error: "Unknown sourceId" }, 404);
    return;
  }

  const outputPath = resolve(sessionDir, "render.jpg");
  const profileId = body.profileId === "standard" || body.profileId === "neutral" ? body.profileId : "standard";

  const recipe: Record<string, number> = {};
  for (const key of NUMERIC_RECIPE_KEYS) {
    const value = body.recipe?.[key];
    if (typeof value === "number" && Number.isFinite(value)) {
      recipe[key] = value;
    }
  }

  const meta = await daemon.send({
    command: "render",
    input: sourcePath,
    output: outputPath,
    profile: profileId,
    autoTone: body.autoTone ?? null,
    halfSize: true,
    maxSize: 1024,
    recipe
  });

  sendJson(response, {
    sourceId: body.sourceId,
    renderUrl: `/sources/${body.sourceId}/render.jpg?t=${Date.now()}`,
    metadata: meta?.metadata ?? null,
    recipe: null,
    pipeline: meta?.pipeline ?? null,
    colorProfile: meta?.colorProfile ?? null,
    autoTone: meta?.autoTone ?? null,
    cached: meta?.cached ?? null
  });
}

async function handleRenderLinear(request: IncomingMessage, response: ServerResponse): Promise<void> {
  const body = await readJson<{ sourceId: string; profileId?: string; halfSize?: boolean; maxSize?: number; dcpCode?: string }>(request);
  if (!body.sourceId) {
    sendJson(response, { error: "Missing sourceId" }, 400);
    return;
  }
  const sessionDir = resolve(sessionsRoot, body.sourceId);
  const sourcePath = await findSource(sessionDir);
  if (!sourcePath) {
    sendJson(response, { error: "Unknown sourceId" }, 404);
    return;
  }

  const outputPath = resolve(sessionDir, "linear.bin");
  const meta = await daemon.send({
    command: "render-linear",
    input: sourcePath,
    output: outputPath,
    profile: body.profileId ?? "standard",
    halfSize: body.halfSize ?? true,
    maxSize: body.maxSize ?? 1600,
    recipe: { autoTone: false },
    dcpCode: body.dcpCode,
  });

  sendJson(response, {
    width: meta.width,
    height: meta.height,
    linearUrl: `/api/sources/${body.sourceId}/linear.bin`,
    colorProfile: meta.colorProfile ?? null,
  });
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

  const sessionDir = resolve(sessionsRoot, meta.sourceId);
  const sourcePath = await findSource(sessionDir);
  if (!sourcePath) {
    sendJson(response, { error: "Unknown sourceId" }, 404);
    return;
  }

  const exportPath = resolve(sessionDir, "export.jpg");
  await writeFile(exportPath, Buffer.from(await file.arrayBuffer()));

  await daemon.send({
    command: "export",
    input: sourcePath,
    target: exportPath,
    settings: meta.settings ?? {}
  });

  streamFile(response, exportPath);
}

async function findSource(sessionDir: string): Promise<string | null> {
  for (const ext of RAW_EXTENSIONS) {
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

  async send(payload: Record<string, unknown>): Promise<DaemonResponse> {
    await this.ensureRunning();
    const id = randomUUID();
    return new Promise<DaemonResponse>((resolvePromise, rejectPromise) => {
      this.pending.set(id, { resolve: resolvePromise, reject: rejectPromise });
      const child = this.child;
      if (!child) {
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
        if (code !== 0) {
          rejectReady(new Error(`worker daemon exited before ready (code ${code ?? "null"})`));
        }
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

  const body = Readable.toWeb(request) as unknown as BodyInit;
  const fakeUrl = `http://localhost${request.url ?? "/"}`;
  const req = new Request(fakeUrl, { method: "POST", headers, body, duplex: "half" } as RequestInit & { duplex: "half" });
  return req.formData();
}

async function readJson<T>(request: IncomingMessage): Promise<T> {
  const chunks: Buffer[] = [];
  for await (const chunk of request) {
    chunks.push(chunk as Buffer);
  }
  const text = Buffer.concat(chunks).toString("utf8");
  if (!text) {
    return {} as T;
  }
  return JSON.parse(text) as T;
}

function streamFile(response: ServerResponse, path: string): void {
  const stream = createReadStream(path);
  stream.on("error", (error: NodeJS.ErrnoException) => {
    if (error.code === "ENOENT") {
      sendJson(response, { error: "Not found" }, 404);
    } else {
      sendJson(response, { error: errorMessage(error) }, 500);
    }
  });
  response.writeHead(200, {
    "content-type": "image/jpeg",
    "cache-control": "no-store"
  });
  stream.pipe(response);
}

function streamBinary(response: ServerResponse, path: string): void {
  const stream = createReadStream(path);
  stream.on("error", (error: NodeJS.ErrnoException) => {
    response.writeHead(404).end();
  });
  response.writeHead(200, {
    "content-type": "application/octet-stream",
    "cache-control": "no-store",
  });
  stream.pipe(response);
}

function pickExtension(filename: string): string {
  const ext = extname(filename).toLowerCase();
  return ext;
}

function sendJson(response: ServerResponse, body: unknown, status = 200): void {
  response.writeHead(status, { "content-type": "application/json; charset=utf-8" });
  response.end(JSON.stringify(body, null, 2));
}

function setCors(response: ServerResponse): void {
  response.setHeader("Access-Control-Allow-Origin", "*");
  response.setHeader("Access-Control-Allow-Headers", "content-type");
  response.setHeader("Access-Control-Allow-Methods", "GET,POST,OPTIONS");
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
