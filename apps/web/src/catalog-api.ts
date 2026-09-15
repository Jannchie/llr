// The catalog half of the API (apps/api/src/index.ts routeCatalog): folders,
// photos, and per-photo edits. Thin and typed; the state that these feed
// lives in composables/useCatalog.ts.

import { API } from "./api";
import type { FolderRow } from "./catalogTree";
import type { PersistedEdit } from "./edits";
import type { Photo } from "./ui";

export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API}${path}`, init);
  if (!res.ok) throw new ApiError(res.status, await errorText(res));
  return res.json() as Promise<T>;
}

async function errorText(res: Response): Promise<string> {
  const text = await res.text();
  try {
    return (JSON.parse(text) as { error?: string }).error ?? text;
  } catch {
    return text || res.statusText;
  }
}

function json(method: string, body: unknown, extra: RequestInit = {}): RequestInit {
  return { method, headers: { "content-type": "application/json" }, body: JSON.stringify(body), ...extra };
}

// ── Folders ──

export async function catalogTree(): Promise<FolderRow[]> {
  return (await request<{ folders: FolderRow[] }>("/catalog/tree")).folders;
}

export async function createFolder(parentId: number, name: string, existingOk = false): Promise<FolderRow> {
  return (await request<{ folder: FolderRow }>("/folders", json("POST", { parentId, name, existingOk }))).folder;
}

export async function renameFolder(id: number, name: string): Promise<FolderRow> {
  return (await request<{ folder: FolderRow }>(`/folders/${id}`, json("PATCH", { name }))).folder;
}

export async function moveFolder(id: number, parentId: number): Promise<FolderRow> {
  return (await request<{ folder: FolderRow }>(`/folders/${id}`, json("PATCH", { parentId }))).folder;
}

/** Deletes the folder, its subfolders and every photo in them; returns the photo ids removed. */
export async function deleteFolder(id: number): Promise<string[]> {
  return (await request<{ removed: string[] }>(`/folders/${id}`, { method: "DELETE" })).removed;
}

// ── Photos ──

export async function listPhotos(folderId: number): Promise<Photo[]> {
  return (await request<{ photos: Photo[] }>(`/folders/${folderId}/photos`)).photos;
}

export async function getPhoto(id: string): Promise<Photo | null> {
  try {
    return (await request<{ photo: Photo }>(`/photos/${id}`)).photo;
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export async function uploadPhoto(file: File, folderId: number): Promise<Photo> {
  const form = new FormData();
  form.append("file", file);
  form.append("folderId", String(folderId));
  form.append("lastModified", String(file.lastModified));
  return (await request<{ photo: Photo }>("/photos", { method: "POST", body: form })).photo;
}

export async function movePhotos(ids: string[], folderId: number): Promise<number> {
  return (await request<{ moved: number }>("/photos/move", json("PATCH", { ids, folderId }))).moved;
}

export async function deletePhotos(ids: string[]): Promise<string[]> {
  return (await request<{ removed: string[] }>("/photos/delete", json("POST", { ids }))).removed;
}

// ── Edits ──

/** Null when nothing has been stored for the photo yet (it is at its defaults). */
export async function getEdit<S>(id: string): Promise<PersistedEdit<S> | null> {
  return (await request<{ edit: PersistedEdit<S> | null }>(`/photos/${id}/edit`)).edit;
}

// `keepalive` lets the request outlive the page (the unload flush); the
// browser caps such bodies at ~64 KB, which edits.ts fitForKeepalive respects.
export async function putEdit<S>(id: string, edit: PersistedEdit<S>, opts: { keepalive?: boolean } = {}): Promise<void> {
  await request(`/photos/${id}/edit`, json("PUT", edit, { keepalive: opts.keepalive === true }));
}

// ── Migration ──

export type LegacyPhoto = { id: string; name: string; size: number; edit?: PersistedEdit<unknown> };

export async function adoptLegacy(photos: LegacyPhoto[]): Promise<{ adopted: string[]; missing: string[] }> {
  return request("/catalog/adopt", json("POST", { photos }));
}
