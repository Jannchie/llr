import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { randomUUID } from "node:crypto";
import { createReadStream, existsSync } from "node:fs";
import { access, mkdir, readdir, rm, stat, writeFile } from "node:fs/promises";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { homedir } from "node:os";
import { dirname, resolve } from "node:path";
import { createInterface } from "node:readline";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";
import { fileURLToPath } from "node:url";

import {
  agentProviders,
  handleAgentAbort,
  handleAgentSteer,
  handleAgentPrompt,
  handleAgentReset,
  handleAgentToolResult,
  isSessionBusy,
  type PromptBody,
  type ToolResultBody,
} from "./agent.js";
import { Catalog, CatalogError, ROOT_FOLDER_ID, type PhotoMeta, type PhotoRow } from "./catalog.js";
import {
  FOLDER_ID,
  SOURCE_ID,
  buildLinearFrameHeader,
  clampDroLevel,
  clampDroStrength,
  clampLookStyle,
  clampLookTweaks,
  clampRenderParams,
  isAllowedHost,
  isJsonContentType,
  isLocalOrigin,
  isValidFolderId,
  isValidSourceId,
  normalizeFolderName,
  pickExtension,
  type RenderLinearBody,
} from "./protocol.js";

const port = Number(process.env.PORT ?? 8790);
const host = process.env.HOST ?? "127.0.0.1";
const repoRoot = resolveRepoRoot();
// Everything the server keeps for the user — uploaded sources, their embedded
// previews, the worker's decode cache — lives outside the checkout, so a
// `git clean`, a moved repo or a rebuild cannot orphan a library the browser
// still points at. A photo's files stay until it leaves the catalog; only
// per-request scratch (and directories the catalog no longer names) is swept.
const cacheRoot = process.env.LLR_CACHE_DIR ?? resolve(process.env.XDG_CACHE_HOME ?? resolve(homedir(), ".cache"), "llr");
const sessionsRoot = resolve(cacheRoot, "sessions");
// What is in the library: folders, photos and their edits (catalog.ts). The
// files a photo owns live in sessions/<id>/ beside the worker's decode cache.
const catalog = new Catalog(resolve(cacheRoot, "catalog.db"));
for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.once(signal, () => {
    catalog.close(); // checkpoints the WAL so the next open starts clean
    process.exit(0);
  });
}
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
// linear-*.bin / export-*.jpg are per-request scratch that the request itself
// deletes. Anything still there (the API died mid-render) is orphaned, so it is
// swept on age. Well above the daemon's request timeout.
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
    // Same reasoning for the 4xx we raise ourselves: a stale tab asking for a
    // source the TTL sweep already removed is the client's problem, not a
    // failure worth a stack trace. 5xx and anything unrecognised still log.
    const status = error instanceof HttpError || error instanceof CatalogError ? error.status : 500;
    if (status >= 500) console.error(error);
    if (!response.headersSent) {
      sendJson(response, { error: errorMessage(error) }, status);
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
  console.log(`Cache root: ${cacheRoot}`);
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

  if (pathname === "/catalog/tree" || pathname === "/catalog/adopt" || pathname.startsWith("/folders") || pathname.startsWith("/photos")) {
    await routeCatalog(pathname, method, request, response);
    return;
  }

  if (method === "POST" && pathname === "/render-linear") {
    await handleRenderLinear(request, response);
    return;
  }

  if (method === "POST" && pathname === "/look-profile") {
    await handleLookProfile(request, response);
    return;
  }

  if (method === "POST" && pathname === "/export") {
    await handleExport(request, response);
    return;
  }

  if (pathname.startsWith("/agent/")) {
    await routeAgent(pathname.slice("/agent/".length), method, request, response);
    return;
  }

  sendJson(response, { error: "Not found", path: pathname }, 404);
}

// The editing assistant (see agent.ts). Bodies are JSON; /prompt answers with
// an SSE stream that lasts for the whole agent run.
async function routeAgent(action: string, method: string, request: IncomingMessage, response: ServerResponse): Promise<void> {
  if (method === "GET" && action === "providers") {
    sendJson(response, { providers: agentProviders() });
    return;
  }
  if (method !== "POST") throw new HttpError(404, "Not found");
  const body = await readJson<PromptBody & ToolResultBody>(request);
  if (typeof body.session !== "string" || !/^[\w-]{1,64}$/.test(body.session)) throw new HttpError(400, "Bad session id");
  switch (action) {
    case "prompt":
      if (typeof body.text !== "string" || typeof body.systemPrompt !== "string" || !Array.isArray(body.tools)
        || typeof body.model?.provider !== "string" || typeof body.model?.id !== "string") {
        throw new HttpError(400, "Expected text, systemPrompt, tools and model");
      }
      if (body.thinking !== undefined && typeof body.thinking !== "string") throw new HttpError(400, "Bad thinking level");
      if (isSessionBusy(body.session)) throw new HttpError(409, "Assistant is busy");
      try {
        await handleAgentPrompt(body, response);
      } catch (error) {
        // Before the stream opens the only thing that can fail is the model spec.
        if (response.headersSent) throw error;
        throw new HttpError(400, errorMessage(error));
      }
      return;
    case "tool-result":
      if (typeof body.toolCallId !== "string") throw new HttpError(400, "Expected toolCallId");
      if (!handleAgentToolResult(body)) throw new HttpError(404, "No pending tool call");
      sendJson(response, { ok: true });
      return;
    case "steer":
      if (typeof body.text !== "string" || !body.text.trim()) throw new HttpError(400, "Expected text");
      if (!isSessionBusy(body.session)) throw new HttpError(409, "Assistant is idle");
      handleAgentSteer(body.session, body.text);
      sendJson(response, { ok: true });
      return;
    case "abort":
      handleAgentAbort(body.session);
      sendJson(response, { ok: true });
      return;
    case "reset":
      handleAgentReset(body.session);
      sendJson(response, { ok: true });
      return;
    default:
      throw new HttpError(404, "Not found");
  }
}

// ── Catalog: folders, photos, edits ──
//
// Every route here is a thin validation layer over catalog.ts; the only I/O
// beyond the database is copying an upload in, asking the worker for its
// previews, and removing a photo's directory once its row is gone.

const FOLDER_ROUTE = new RegExp(`^/folders/(${FOLDER_ID})$`);
const FOLDER_PHOTOS_ROUTE = new RegExp(`^/folders/(${FOLDER_ID})/photos$`);
const PHOTO_ROUTE = new RegExp(`^/photos/(${SOURCE_ID})$`);
const PHOTO_EDIT_ROUTE = new RegExp(`^/photos/(${SOURCE_ID})/edit$`);
const PHOTO_IMAGE_ROUTE = new RegExp(`^/photos/(${SOURCE_ID})/(thumb|embedded)\\.jpg$`);

// The most ids one request may name. A whole folder's worth is the realistic
// maximum; anything larger is a runaway client.
const IDS_LIMIT = 5000;

async function routeCatalog(pathname: string, method: string, request: IncomingMessage, response: ServerResponse): Promise<void> {
  if (method === "GET" && pathname === "/catalog/tree") {
    sendJson(response, { folders: catalog.tree() });
    return;
  }
  if (method === "POST" && pathname === "/catalog/adopt") {
    await handleAdopt(request, response);
    return;
  }

  if (method === "POST" && pathname === "/folders") {
    const body = await readJson<{ parentId?: unknown; name?: unknown; existingOk?: unknown }>(request);
    const name = normalizeFolderName(body.name);
    if (!name) throw new HttpError(400, "Invalid folder name");
    const parentId = folderIdFromBody(body.parentId);
    sendJson(response, { folder: catalog.createFolder(parentId, name, { existingOk: body.existingOk === true }) }, 201);
    return;
  }

  const folderId = pathname.match(FOLDER_ROUTE)?.[1];
  if (folderId && method === "PATCH") {
    const id = Number(folderId);
    const body = await readJson<{ name?: unknown; parentId?: unknown }>(request);
    if (body.name !== undefined) {
      const name = normalizeFolderName(body.name);
      if (!name) throw new HttpError(400, "Invalid folder name");
      catalog.renameFolder(id, name);
    }
    if (body.parentId !== undefined) catalog.moveFolder(id, folderIdFromBody(body.parentId));
    sendJson(response, { folder: catalog.getFolder(id) });
    return;
  }
  if (folderId && method === "DELETE") {
    const removed = catalog.deleteFolder(Number(folderId));
    await removePhotoDirs(removed);
    sendJson(response, { ok: true, removed });
    return;
  }

  const listId = pathname.match(FOLDER_PHOTOS_ROUTE)?.[1];
  if (listId && method === "GET") {
    if (!catalog.getFolder(Number(listId))) throw new HttpError(404, "Unknown folder");
    sendJson(response, { photos: catalog.listPhotos(Number(listId)).map(publicPhoto) });
    return;
  }

  if (method === "POST" && pathname === "/photos") {
    await handlePhotoUpload(request, response);
    return;
  }
  if (method === "PATCH" && pathname === "/photos/move") {
    const body = await readJson<{ ids?: unknown; folderId?: unknown }>(request);
    const moved = catalog.movePhotos(photoIdsFromBody(body.ids), folderIdFromBody(body.folderId));
    sendJson(response, { moved });
    return;
  }
  if (method === "POST" && pathname === "/photos/delete") {
    const body = await readJson<{ ids?: unknown }>(request);
    const removed = catalog.deletePhotos(photoIdsFromBody(body.ids));
    await removePhotoDirs(removed);
    sendJson(response, { ok: true, removed });
    return;
  }

  const imageMatch = pathname.match(PHOTO_IMAGE_ROUTE);
  if (imageMatch && method === "GET") {
    if (!catalog.getPhoto(imageMatch[1])) throw new HttpError(404, "Unknown photo");
    await streamImmutable(response, resolve(sessionDirFor(imageMatch[1]), `${imageMatch[2]}.jpg`));
    return;
  }

  const editId = pathname.match(PHOTO_EDIT_ROUTE)?.[1];
  if (editId && method === "GET") {
    // `edit: null` (not a 404) for a photo still at its defaults: that is the
    // common case for a fresh import, not an error worth a console line.
    if (!catalog.getPhoto(editId)) throw new HttpError(404, "Unknown photo");
    sendJson(response, { edit: catalog.getEdit(editId) });
    return;
  }
  if (editId && method === "PUT") {
    const body = await readJson<{ snapshot?: unknown; history?: unknown; historyIndex?: unknown }>(request);
    catalog.putEdit(editId, validateEdit(body));
    sendJson(response, { ok: true });
    return;
  }

  const photoId = pathname.match(PHOTO_ROUTE)?.[1];
  if (photoId && method === "GET") {
    const photo = catalog.getPhoto(photoId);
    if (!photo) throw new HttpError(404, "Unknown photo");
    sendJson(response, { photo: publicPhoto(photo) });
    return;
  }
  if (photoId && method === "DELETE") {
    const removed = catalog.deletePhotos([photoId]);
    await removePhotoDirs(removed);
    sendJson(response, { ok: true, removed });
    return;
  }

  sendJson(response, { error: "Not found", path: pathname }, 404);
}

function folderIdFromBody(value: unknown): number {
  if (value === undefined || value === null) return ROOT_FOLDER_ID;
  if (typeof value === "number" && Number.isInteger(value) && value > 0) return value;
  if (typeof value === "string" && isValidFolderId(value)) return Number(value);
  throw new HttpError(400, "Invalid folderId");
}

function photoIdsFromBody(value: unknown): string[] {
  if (!Array.isArray(value) || value.length > IDS_LIMIT) throw new HttpError(400, "Invalid ids");
  for (const id of value) {
    if (typeof id !== "string" || !isValidSourceId(id)) throw new HttpError(400, "Invalid ids");
  }
  return value as string[];
}

function validateEdit(body: { snapshot?: unknown; history?: unknown; historyIndex?: unknown }): { snapshot: unknown; history: unknown[]; historyIndex: number } {
  const { snapshot, history, historyIndex } = body;
  if (!snapshot || typeof snapshot !== "object" || !Array.isArray(history)) throw new HttpError(400, "Invalid edit");
  if (typeof historyIndex !== "number" || !Number.isInteger(historyIndex) || historyIndex < 0 || historyIndex >= Math.max(1, history.length)) {
    throw new HttpError(400, "Invalid historyIndex");
  }
  return { snapshot, history, historyIndex };
}

// The record as the browser sees it: the row plus where its images are.
function publicPhoto(p: PhotoRow) {
  return { ...p, embeddedUrl: `/photos/${p.id}/embedded.jpg`, thumbUrl: `/photos/${p.id}/thumb.jpg` };
}

// Rows are already gone by the time this runs; a directory that will not go
// (Windows, while the worker still holds the RAW open) is left for the hourly
// sweep, which removes any directory the catalog no longer names.
async function removePhotoDirs(ids: string[]): Promise<void> {
  for (const id of ids) {
    try {
      await rm(sessionDirFor(id), { recursive: true, force: true });
    } catch (error) {
      console.warn(`could not remove ${id}: ${errorMessage(error)}`);
    }
  }
}

// Copy the upload into its own session directory, ask the worker for the
// camera preview + thumbnail + metadata, and only then answer — the record
// the browser gets back is complete, so it never has to poll for a thumbnail.
async function handlePhotoUpload(request: IncomingMessage, response: ServerResponse): Promise<void> {
  const form = await readFormData(request);
  const file = form.get("file");
  if (!(file instanceof File)) {
    sendJson(response, { error: "Missing file field" }, 400);
    return;
  }
  const folderId = folderIdFromBody(form.get("folderId") ?? undefined);
  if (!catalog.getFolder(folderId)) throw new HttpError(404, "Unknown folder");
  const lastModified = Number(form.get("lastModified"));

  const ext = pickExtension(file.name);
  if (!SUPPORTED_EXTENSIONS.has(ext)) {
    sendJson(response, { error: `Unsupported extension: ${ext || "(none)"}` }, 415);
    return;
  }

  const id = randomUUID();
  const sessionDir = resolve(sessionsRoot, id);
  await mkdir(sessionDir, { recursive: true });
  try {
    const sourcePath = resolve(sessionDir, `source${ext}`);
    await writeFile(sourcePath, Buffer.from(await file.arrayBuffer()));
    catalog.insertPhoto({ id, folderId, name: file.name, ext, size: file.size });
    const photo = await extractPreviews(id, sourcePath, sessionDir, lastModified);
    sendJson(response, { photo: publicPhoto(photo) }, 201);
  } catch (error) {
    // Nothing half-imported stays: no row without previews, no directory
    // without a row.
    catalog.deletePhotos([id]);
    await rm(sessionDir, { recursive: true, force: true }).catch(() => {});
    throw error;
  }
}

async function extractPreviews(id: string, sourcePath: string, sessionDir: string, lastModified: number): Promise<PhotoRow> {
  const result = await daemon.send({
    command: "extract-preview",
    input: sourcePath,
    output: resolve(sessionDir, "embedded.jpg"),
    thumbOutput: resolve(sessionDir, "thumb.jpg"),
    meta: true,
  });
  const meta = (result.meta ?? {}) as PhotoMeta;
  // A file with no EXIF clock (a screenshot, a scan) still has a mtime, and
  // that is a better "when" than nothing.
  if (!meta.capturedAt && Number.isFinite(lastModified) && lastModified > 0) {
    meta.capturedAt = new Date(lastModified).toISOString().slice(0, 19);
  }
  const photo = catalog.setPhotoMeta(id, meta, "ready");
  if (!photo) throw new HttpError(500, "Photo vanished during import");
  return photo;
}

// One-time adoption of the pre-catalog library: the browser's IndexedDB knew
// the ids, names and edits, and the files are still in sessions/<id>/. Ids
// whose directory is gone are reported back and dropped. Idempotent, so a tab
// that crashed halfway can simply run it again.
async function handleAdopt(request: IncomingMessage, response: ServerResponse): Promise<void> {
  const body = await readJson<{ photos?: unknown }>(request);
  if (!Array.isArray(body.photos) || body.photos.length > IDS_LIMIT) throw new HttpError(400, "Invalid photos");
  const adopted: string[] = [];
  const missing: string[] = [];
  for (const entry of body.photos as { id?: unknown; name?: unknown; size?: unknown; edit?: unknown }[]) {
    const id = typeof entry.id === "string" && isValidSourceId(entry.id) ? entry.id : null;
    if (!id) continue;
    const sessionDir = resolve(sessionsRoot, id);
    const sourcePath = await findSource(sessionDir);
    if (!sourcePath) {
      missing.push(id);
      continue;
    }
    try {
      if (!catalog.getPhoto(id)) {
        const name = typeof entry.name === "string" && entry.name ? entry.name : `source${pickExtension(sourcePath)}`;
        const size = typeof entry.size === "number" ? entry.size : (await stat(sourcePath)).size;
        catalog.insertPhoto({ id, folderId: ROOT_FOLDER_ID, name, ext: pickExtension(sourcePath), size });
        await extractPreviews(id, sourcePath, sessionDir, 0);
      }
      if (entry.edit && typeof entry.edit === "object") {
        try {
          catalog.putEdit(id, validateEdit(entry.edit as Record<string, unknown>));
        } catch {
          // A malformed stored edit is not worth failing the whole adoption.
        }
      }
      adopted.push(id);
    } catch (error) {
      console.warn(`could not adopt ${id}: ${errorMessage(error)}`);
      catalog.deletePhotos([id]);
      missing.push(id);
    }
  }
  catalog.set("legacy_adopted", "1");
  sendJson(response, { adopted, missing });
}

function sessionDirFor(sourceId: string): string {
  if (!isValidSourceId(sourceId)) throw new HttpError(400, "Invalid sourceId");
  return resolve(sessionsRoot, sourceId);
}

// Every handler that works on a photo opens the same doors: the id must be
// there, the catalog must know it, and its file must still exist (a cleared
// cache root leaves rows pointing at nothing — the browser marks those stale).
async function resolveSource(sourceId: string | undefined): Promise<{ sessionDir: string; sourcePath: string }> {
  if (!sourceId) throw new HttpError(400, "Missing sourceId");
  const sessionDir = sessionDirFor(sourceId);
  const photo = catalog.getPhoto(sourceId);
  if (!photo) throw new HttpError(404, "Unknown sourceId");
  const sourcePath = resolve(sessionDir, `source${photo.ext}`);
  try {
    await access(sourcePath);
  } catch {
    throw new HttpError(404, "Source file is gone");
  }
  return { sessionDir, sourcePath };
}

// Backpressure for the heavy decode path: each render buffers tens of MB on
// disk and occupies one of the daemon's three workers, so an unbounded pile-up
// (a stuck client in a retry loop, a tab spamming slider changes) would queue
// memory and worker time without limit. Excess requests get a fast 429; the
// UI's own sequencing (loadSeq, debounces) keeps it far from this ceiling.
const RENDER_INFLIGHT_LIMIT = 8;

async function handleRenderLinear(request: IncomingMessage, response: ServerResponse): Promise<void> {
  const body = await readJson<RenderLinearBody>(request);
  const { sessionDir, sourcePath } = await resolveSource(body.sourceId);

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
      look: params.look,
      style: params.style,
      dro: params.dro,
      droLevel: params.droLevel,
      purpose: params.purpose,
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

// The Creative Look sliders, and the choice of look itself. Neither reaches a
// pixel — the looks share the body's one hue-segmented matrix, and all that
// differs is the tone curve and the chroma terms the browser applies — so this
// returns the profile alone. The client re-bakes its LUT and redraws the frame
// it already has, instead of pulling tens of megabytes back through
// /render-linear for a slider drag or a look change.
async function handleLookProfile(request: IncomingMessage, response: ServerResponse): Promise<void> {
  const body = await readJson<RenderLinearBody>(request);
  const { sourcePath } = await resolveSource(body.sourceId);
  const meta = await daemon.send({
    command: "look-profile",
    input: sourcePath,
    look: clampLookTweaks(body.look),
    style: clampLookStyle(body.style),
    dro: clampDroStrength(body.dro),
    droLevel: clampDroLevel(body.droLevel),
  });
  sendJson(response, { colorProfile: meta.colorProfile ?? null });
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
  const { sessionDir, sourcePath } = await resolveSource(meta.sourceId);

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
  // A directory the catalog does not name is a photo that was removed while
  // something held its file open, or an import that died halfway. Until the
  // pre-catalog library has been adopted, though, every directory is one the
  // browser may still be about to claim — so the sweep waits for that.
  const known = catalog.get("legacy_adopted") === "1" ? catalog.photoIds() : null;
  for (const entry of entries) {
    if (!entry.isDirectory()) {
      continue;
    }
    const dir = resolve(sessionsRoot, entry.name);
    try {
      if (known && !known.has(entry.name)) {
        if (now - (await stat(dir)).mtimeMs > SCRATCH_TTL_MS) await rm(dir, { recursive: true, force: true });
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
      outputs: [payload.output, payload.thumbOutput, payload.target].filter((value): value is string => typeof value === "string"),
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

// A photo's previews never change once written and their URL carries a UUID,
// so the browser may keep them for good: a grid of thousands of thumbnails
// then costs one request each, ever, and a scroll back up costs none.
async function streamImmutable(response: ServerResponse, path: string): Promise<void> {
  let size: number;
  try {
    size = (await stat(path)).size;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      sendJson(response, { error: "Not found" }, 404);
    } else {
      sendJson(response, { error: errorMessage(error) }, 500);
    }
    return;
  }
  response.writeHead(200, {
    "content-type": "image/jpeg",
    "content-length": size,
    "cache-control": "public, max-age=31536000, immutable"
  });
  await pipeline(createReadStream(path), response);
}

function sendJson(response: ServerResponse, body: unknown, status = 200): void {
  response.writeHead(status, { "content-type": "application/json; charset=utf-8" });
  response.end(JSON.stringify(body, null, 2));
}

// Runs before anything reads the body: a hostile page's request must not land
// its side effects (a 512MB upload in the cache root, attacker bytes handed to
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
  response.setHeader("Access-Control-Allow-Methods", "GET,POST,PUT,PATCH,DELETE,OPTIONS");
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
