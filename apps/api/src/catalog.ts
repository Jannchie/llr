/**
 * The photo catalog: every folder, photo, and per-photo edit the user has, in
 * one SQLite file under the cache root. It is the single source of truth the
 * browser reads from — the browser keeps only what is on screen, so a library
 * of thousands costs it nothing until a folder is opened, and two tabs (or a
 * cleared browser profile) cannot disagree about what is in the library.
 *
 * The only module that touches `node:sqlite`. Everything here is synchronous:
 * the database is local, each call is one indexed statement, and the HTTP
 * layer is the only caller. Files themselves (the copied source, its
 * previews, the worker's decode cache) stay in `sessions/<photo id>/`; this
 * module never touches the filesystem beyond opening its own database.
 */

import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { DatabaseSync, type SQLInputValue, type SQLOutputValue } from "node:sqlite";

// Every library has one root folder, the drop target when nothing else is
// chosen; it cannot be renamed, moved or deleted.
export const ROOT_FOLDER_ID = 1;

export type ThumbState = "pending" | "ready" | "failed";

export type PhotoRow = {
  id: string;
  folderId: number;
  name: string;
  ext: string;
  size: number;
  importedAt: number;
  width: number | null;
  height: number | null;
  orientation: number | null;
  capturedAt: string | null;
  make: string | null;
  model: string | null;
  lens: string | null;
  iso: number | null;
  exposure: number | null;
  fnumber: number | null;
  focal: number | null;
  thumbState: ThumbState;
};

export type PhotoMeta = Partial<Pick<PhotoRow,
  "width" | "height" | "orientation" | "capturedAt" | "make" | "model" | "lens" | "iso" | "exposure" | "fnumber" | "focal">>;

export type FolderRow = {
  id: number;
  parentId: number | null;
  name: string;
  /** Photos directly in this folder (subtree totals are the client's sum). */
  count: number;
};

export type EditRecord = {
  snapshot: unknown;
  history: unknown[];
  historyIndex: number;
  updatedAt: number;
};

export class CatalogError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
  }
}

const SCHEMA_VERSION = 1;

const SCHEMA = `
CREATE TABLE folders (
  id INTEGER PRIMARY KEY,
  parent_id INTEGER REFERENCES folders(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  UNIQUE(parent_id, name)
);
CREATE TABLE photos (
  id TEXT PRIMARY KEY,
  folder_id INTEGER NOT NULL REFERENCES folders(id),
  name TEXT NOT NULL,
  ext TEXT NOT NULL,
  size INTEGER NOT NULL,
  imported_at INTEGER NOT NULL,
  width INTEGER,
  height INTEGER,
  orientation INTEGER,
  captured_at TEXT,
  make TEXT,
  model TEXT,
  lens TEXT,
  iso REAL,
  exposure REAL,
  fnumber REAL,
  focal REAL,
  thumb_state TEXT NOT NULL DEFAULT 'pending'
);
CREATE INDEX photos_folder ON photos(folder_id);
CREATE TABLE edits (
  photo_id TEXT PRIMARY KEY REFERENCES photos(id) ON DELETE CASCADE,
  snapshot TEXT NOT NULL,
  history TEXT NOT NULL,
  history_index INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE TABLE meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
`;

const PHOTO_COLUMNS = `id, folder_id, name, ext, size, imported_at, width, height, orientation, captured_at,
  make, model, lens, iso, exposure, fnumber, focal, thumb_state`;

// Chronological where the shot has a capture time, import order for the rest
// (a screenshot, a file with no EXIF) — those sort last rather than by an
// accidental name order.
const PHOTO_ORDER = `ORDER BY captured_at IS NULL, captured_at, imported_at, name COLLATE NOCASE`;

type Row = Record<string, SQLOutputValue>;

function num(v: SQLOutputValue): number | null {
  return typeof v === "number" ? v : typeof v === "bigint" ? Number(v) : null;
}

function str(v: SQLOutputValue): string | null {
  return typeof v === "string" ? v : null;
}

function toPhoto(r: Row): PhotoRow {
  return {
    id: str(r.id) ?? "",
    folderId: num(r.folder_id) ?? ROOT_FOLDER_ID,
    name: str(r.name) ?? "",
    ext: str(r.ext) ?? "",
    size: num(r.size) ?? 0,
    importedAt: num(r.imported_at) ?? 0,
    width: num(r.width),
    height: num(r.height),
    orientation: num(r.orientation),
    capturedAt: str(r.captured_at),
    make: str(r.make),
    model: str(r.model),
    lens: str(r.lens),
    iso: num(r.iso),
    exposure: num(r.exposure),
    fnumber: num(r.fnumber),
    focal: num(r.focal),
    thumbState: (str(r.thumb_state) as ThumbState | null) ?? "pending",
  };
}

function toFolder(r: Row): FolderRow {
  return { id: num(r.id) ?? 0, parentId: num(r.parent_id), name: str(r.name) ?? "", count: num(r.count) ?? 0 };
}

const META_KEYS: (keyof PhotoMeta)[] = [
  "width", "height", "orientation", "capturedAt", "make", "model", "lens", "iso", "exposure", "fnumber", "focal",
];
const META_COLUMNS: Record<keyof PhotoMeta, string> = {
  width: "width", height: "height", orientation: "orientation", capturedAt: "captured_at",
  make: "make", model: "model", lens: "lens", iso: "iso", exposure: "exposure", fnumber: "fnumber", focal: "focal",
};

export class Catalog {
  private readonly db: DatabaseSync;

  /** `path` may be `:memory:` (tests). The parent directory is created. */
  constructor(path: string) {
    if (path !== ":memory:") mkdirSync(dirname(path), { recursive: true });
    this.db = new DatabaseSync(path);
    // WAL: readers never block the writer, and a crash mid-write leaves the
    // last committed state rather than a torn file. NORMAL is safe under WAL
    // (durability against power loss is not worth an fsync per slider stop).
    this.db.exec("PRAGMA journal_mode = WAL");
    this.db.exec("PRAGMA synchronous = NORMAL");
    this.db.exec("PRAGMA foreign_keys = ON");
    this.migrate();
  }

  private migrate(): void {
    const row = this.db.prepare("PRAGMA user_version").get() as Row;
    const version = num(row.user_version) ?? 0;
    if (version >= SCHEMA_VERSION) return;
    this.db.exec("BEGIN");
    try {
      if (version < 1) {
        this.db.exec(SCHEMA);
        this.db.prepare("INSERT INTO folders (id, parent_id, name) VALUES (?, NULL, ?)").run(ROOT_FOLDER_ID, "Library");
      }
      this.db.exec(`PRAGMA user_version = ${SCHEMA_VERSION}`);
      this.db.exec("COMMIT");
    } catch (error) {
      this.db.exec("ROLLBACK");
      throw error;
    }
  }

  close(): void {
    this.db.close();
  }

  private transaction<T>(fn: () => T): T {
    this.db.exec("BEGIN IMMEDIATE");
    try {
      const out = fn();
      this.db.exec("COMMIT");
      return out;
    } catch (error) {
      this.db.exec("ROLLBACK");
      throw error;
    }
  }

  // ── Folders ──

  /** Every folder, flat, with its direct photo count; the client builds the tree. */
  tree(): FolderRow[] {
    const rows = this.db.prepare(`
      SELECT f.id, f.parent_id, f.name, COUNT(p.id) AS count
      FROM folders f LEFT JOIN photos p ON p.folder_id = f.id
      GROUP BY f.id ORDER BY f.name COLLATE NOCASE
    `).all() as Row[];
    return rows.map(toFolder);
  }

  getFolder(id: number): FolderRow | null {
    const row = this.db.prepare(`
      SELECT f.id, f.parent_id, f.name, COUNT(p.id) AS count
      FROM folders f LEFT JOIN photos p ON p.folder_id = f.id WHERE f.id = ? GROUP BY f.id
    `).get(id) as Row | undefined;
    return row ? toFolder(row) : null;
  }

  /**
   * Create `name` under `parentId`. A sibling of the same name is a 409 —
   * unless `existingOk`, which returns it instead: dropping the same OS
   * folder twice merges into the folder it made the first time.
   */
  createFolder(parentId: number, name: string, opts: { existingOk?: boolean } = {}): FolderRow {
    return this.transaction(() => {
      if (!this.getFolder(parentId)) throw new CatalogError(404, "Unknown parent folder");
      const existing = this.db.prepare("SELECT id FROM folders WHERE parent_id = ? AND name = ?").get(parentId, name) as Row | undefined;
      if (existing) {
        if (opts.existingOk) return this.getFolder(num(existing.id) ?? 0)!;
        throw new CatalogError(409, "A folder of that name already exists here");
      }
      const result = this.db.prepare("INSERT INTO folders (parent_id, name) VALUES (?, ?)").run(parentId, name);
      return { id: Number(result.lastInsertRowid), parentId, name, count: 0 };
    });
  }

  renameFolder(id: number, name: string): void {
    if (id === ROOT_FOLDER_ID) throw new CatalogError(400, "The root folder cannot be renamed");
    this.transaction(() => {
      const folder = this.getFolder(id);
      if (!folder) throw new CatalogError(404, "Unknown folder");
      const clash = this.db.prepare("SELECT id FROM folders WHERE parent_id = ? AND name = ? AND id != ?").get(folder.parentId, name, id);
      if (clash) throw new CatalogError(409, "A folder of that name already exists here");
      this.db.prepare("UPDATE folders SET name = ? WHERE id = ?").run(name, id);
    });
  }

  /** Re-parent `id`. Refuses the root, itself, and anything in its own subtree. */
  moveFolder(id: number, parentId: number): void {
    if (id === ROOT_FOLDER_ID) throw new CatalogError(400, "The root folder cannot be moved");
    this.transaction(() => {
      const folder = this.getFolder(id);
      if (!folder) throw new CatalogError(404, "Unknown folder");
      if (!this.getFolder(parentId)) throw new CatalogError(404, "Unknown parent folder");
      if (this.subtreeIds(id).includes(parentId)) throw new CatalogError(400, "A folder cannot be moved into itself");
      const clash = this.db.prepare("SELECT id FROM folders WHERE parent_id = ? AND name = ? AND id != ?").get(parentId, folder.name, id);
      if (clash) throw new CatalogError(409, "A folder of that name already exists there");
      this.db.prepare("UPDATE folders SET parent_id = ? WHERE id = ?").run(parentId, id);
    });
  }

  /**
   * Delete a folder and everything under it. Returns the ids of the photos
   * that went with it so the caller can remove their session directories —
   * rows first, files after: a file that refuses to go (Windows, a handle
   * still open in the worker) must not keep a deleted photo in the library.
   */
  deleteFolder(id: number): string[] {
    if (id === ROOT_FOLDER_ID) throw new CatalogError(400, "The root folder cannot be deleted");
    return this.transaction(() => {
      if (!this.getFolder(id)) throw new CatalogError(404, "Unknown folder");
      const ids = this.subtreeIds(id);
      const marks = ids.map(() => "?").join(",");
      const photos = this.db.prepare(`SELECT id FROM photos WHERE folder_id IN (${marks})`).all(...ids) as Row[];
      const photoIds = photos.map(r => str(r.id) ?? "");
      this.db.prepare(`DELETE FROM photos WHERE folder_id IN (${marks})`).run(...ids);
      this.db.prepare("DELETE FROM folders WHERE id = ?").run(id); // cascades to descendants
      return photoIds;
    });
  }

  /** `id` and every folder below it. */
  private subtreeIds(id: number): number[] {
    const rows = this.db.prepare(`
      WITH RECURSIVE sub(id) AS (
        SELECT ? UNION ALL SELECT f.id FROM folders f JOIN sub ON f.parent_id = sub.id
      ) SELECT id FROM sub
    `).all(id) as Row[];
    return rows.map(r => num(r.id) ?? 0);
  }

  // ── Photos ──

  listPhotos(folderId: number): PhotoRow[] {
    const rows = this.db.prepare(`SELECT ${PHOTO_COLUMNS} FROM photos WHERE folder_id = ? ${PHOTO_ORDER}`).all(folderId) as Row[];
    return rows.map(toPhoto);
  }

  getPhoto(id: string): PhotoRow | null {
    const row = this.db.prepare(`SELECT ${PHOTO_COLUMNS} FROM photos WHERE id = ?`).get(id) as Row | undefined;
    return row ? toPhoto(row) : null;
  }

  insertPhoto(p: Pick<PhotoRow, "id" | "folderId" | "name" | "ext" | "size">, importedAt = Date.now()): PhotoRow {
    if (!this.getFolder(p.folderId)) throw new CatalogError(404, "Unknown folder");
    this.db.prepare("INSERT INTO photos (id, folder_id, name, ext, size, imported_at) VALUES (?, ?, ?, ?, ?, ?)")
      .run(p.id, p.folderId, p.name, p.ext, p.size, importedAt);
    return this.getPhoto(p.id)!;
  }

  /** Fill in what the preview extraction learned; absent keys are left alone. */
  setPhotoMeta(id: string, meta: PhotoMeta, thumbState: ThumbState): PhotoRow | null {
    const sets: string[] = ["thumb_state = ?"];
    const values: SQLInputValue[] = [thumbState];
    for (const key of META_KEYS) {
      if (meta[key] === undefined) continue;
      sets.push(`${META_COLUMNS[key]} = ?`);
      values.push(meta[key] as SQLInputValue);
    }
    values.push(id);
    this.db.prepare(`UPDATE photos SET ${sets.join(", ")} WHERE id = ?`).run(...values);
    return this.getPhoto(id);
  }

  /** Returns how many rows moved. */
  movePhotos(ids: string[], folderId: number): number {
    if (!ids.length) return 0;
    return this.transaction(() => {
      if (!this.getFolder(folderId)) throw new CatalogError(404, "Unknown folder");
      const result = this.db.prepare(`UPDATE photos SET folder_id = ? WHERE id IN (${ids.map(() => "?").join(",")})`).run(folderId, ...ids);
      return Number(result.changes);
    });
  }

  /** Returns the ids that were actually in the catalog (and are now gone). */
  deletePhotos(ids: string[]): string[] {
    if (!ids.length) return [];
    return this.transaction(() => {
      const marks = ids.map(() => "?").join(",");
      const present = (this.db.prepare(`SELECT id FROM photos WHERE id IN (${marks})`).all(...ids) as Row[]).map(r => str(r.id) ?? "");
      this.db.prepare(`DELETE FROM photos WHERE id IN (${marks})`).run(...ids);
      return present;
    });
  }

  /** Every photo id in the catalog — what the session-dir sweep keeps. */
  photoIds(): Set<string> {
    const rows = this.db.prepare("SELECT id FROM photos").all() as Row[];
    return new Set(rows.map(r => str(r.id) ?? ""));
  }

  // ── Edits ──

  getEdit(photoId: string): EditRecord | null {
    const row = this.db.prepare("SELECT snapshot, history, history_index, updated_at FROM edits WHERE photo_id = ?").get(photoId) as Row | undefined;
    if (!row) return null;
    try {
      return {
        snapshot: JSON.parse(str(row.snapshot) ?? "null"),
        history: JSON.parse(str(row.history) ?? "[]") as unknown[],
        historyIndex: num(row.history_index) ?? 0,
        updatedAt: num(row.updated_at) ?? 0,
      };
    } catch {
      return null;
    }
  }

  putEdit(photoId: string, edit: Omit<EditRecord, "updatedAt">, updatedAt = Date.now()): void {
    if (!this.getPhoto(photoId)) throw new CatalogError(404, "Unknown photo");
    this.db.prepare(`
      INSERT INTO edits (photo_id, snapshot, history, history_index, updated_at) VALUES (?, ?, ?, ?, ?)
      ON CONFLICT(photo_id) DO UPDATE SET snapshot = excluded.snapshot, history = excluded.history,
        history_index = excluded.history_index, updated_at = excluded.updated_at
    `).run(photoId, JSON.stringify(edit.snapshot), JSON.stringify(edit.history), edit.historyIndex, updatedAt);
  }

  // ── Meta ──

  get(key: string): string | null {
    const row = this.db.prepare("SELECT value FROM meta WHERE key = ?").get(key) as Row | undefined;
    return row ? str(row.value) : null;
  }

  set(key: string, value: string): void {
    this.db.prepare("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value").run(key, value);
  }
}
