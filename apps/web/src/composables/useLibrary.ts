import { computed, reactive, ref, type Ref } from "vue";
import {
  loadSession, saveSession, saveEdit, deleteEdit,
  loadThumbs, saveThumb, deleteThumb, generateThumb,
  type PersistedEdit,
} from "../persistence";
import { type Source } from "../ui";

function isAbsoluteUrl(u: string): boolean {
  return u.startsWith("blob:") || u.startsWith("data:") || u.startsWith("http");
}

// The image library: imported sources, the per-image edit map, thumbnail
// cache, and session persistence orchestration. Each source owns an
// independent edit (snapshot + undo history); the live reactive edit state
// always mirrors the active image, so switching stashes the outgoing edit
// here and loads the incoming one back.
//
// The composable owns library CRUD + persistence; how an edit is captured
// from / pushed into the live state, and how pixels reach the renderer, are
// injected by the caller.
export function useLibrary<S, V>(opts: {
  api: string;
  status: Ref<"idle" | "uploading" | "rendering" | "error">;
  errorMessage: Ref<string | null>;
  cropMode: Ref<boolean>;
  /** Snapshot the live edit (snapshot + history) for stashing/persisting. */
  captureEdit: () => PersistedEdit<S>;
  /** Fresh edit for a new import. */
  defaultEdit: () => PersistedEdit<S>;
  /** Push an edit (null = defaults) into the live reactive state. */
  loadEdit: (e: PersistedEdit<S> | null) => void;
  /** Decode + render `id`'s pixels (the caller's loadSource). */
  loadPixels: (id: string, o: { resetView?: boolean }) => Promise<boolean>;
  /** Commit any debounced history entry before an image switch. */
  flushPendingHistory: () => void;
  /** Runs before pointing the app at another image (cancel pending reloads). */
  beforeActivate: () => void;
  /** Renderer/UI cleanup when the last image is removed. */
  onEmptied: () => void;
  /** True while a snapshot restore is in flight (suppresses persist). */
  isRestoring: () => boolean;
  /** Extra per-session state stored in the index record (view settings). */
  sessionExtras: { get: () => V; apply: (v: V) => void };
}) {
  const { api, status, errorMessage, cropMode } = opts;

  const sources = ref<Source[]>([]);
  const activeId = ref<string | null>(null);
  const activeSource = computed(() => sources.value.find(s => s.id === activeId.value) ?? null);

  type ImageEdit = PersistedEdit<S>;
  const edits = new Map<string, ImageEdit>();
  // Edits touched since the last persist — only these are written to IndexedDB
  // (each image is its own record; cloning the whole library per save is what
  // made the old single-record layout expensive).
  const dirtyEditIds = new Set<string>();

  const thumbs = reactive<Record<string, string>>({});

  let persistTimer = 0;
  const PERSIST_DEBOUNCE = 600;

  // Copy the current live edit (snapshot + history) into the map under `id`.
  function syncLiveToMap(id: string | null): void {
    if (!id) return;
    edits.set(id, opts.captureEdit());
    dirtyEditIds.add(id);
  }

  // Load an image's edit into the live reactive state (defaults if none stored).
  // Does not decode/draw — the caller pairs this with loadPixels().
  function loadEditFromMap(id: string): void {
    opts.loadEdit(edits.get(id) ?? null);
  }

  function persistNow(): void {
    syncLiveToMap(activeId.value);
    for (const id of dirtyEditIds) {
      const e = edits.get(id);
      if (e) void saveEdit(id, e);
    }
    dirtyEditIds.clear();
    void saveSession({
      version: 2,
      activeId: activeId.value,
      viewSettings: opts.sessionExtras.get(),
      sources: sources.value.map(s => ({ id: s.id, name: s.name, size: s.size, embeddedUrl: s.embeddedUrl })),
    });
  }

  function schedulePersist(): void {
    if (opts.isRestoring()) return;
    if (persistTimer) clearTimeout(persistTimer);
    persistTimer = window.setTimeout(() => { persistTimer = 0; persistNow(); }, PERSIST_DEBOUNCE);
  }

  // Resolve a possibly-relative API url to something <img>/fetch can use.
  function resolveUrl(u: string): string { return isAbsoluteUrl(u) ? u : `${api}${u}`; }

  // Thumbnail shown in the filmstrip / preview fallback: prefer the locally
  // cached copy (survives server eviction), else the live server preview.
  function thumbSrc(s: Source): string {
    return thumbs[s.id] ?? (s.embeddedUrl ? resolveUrl(s.embeddedUrl) : "");
  }

  async function cacheThumb(s: Source): Promise<void> {
    if (thumbs[s.id] || !s.embeddedUrl) return;
    const blob = await generateThumb(resolveUrl(s.embeddedUrl));
    if (blob) { thumbs[s.id] = URL.createObjectURL(blob); void saveThumb(s.id, blob); }
  }

  function markInvalid(id: string): void {
    const s = sources.value.find(x => x.id === id);
    if (s) s.invalid = true;
    status.value = "error";
    errorMessage.value = "Source file no longer available (the server cache may have been cleared) — re-import this photo.";
  }

  // Point the live edit + pixels at `id` (does NOT save the outgoing edit).
  function activateSource(id: string): Promise<boolean> {
    opts.beforeActivate();
    activeId.value = id;
    loadEditFromMap(id);
    return opts.loadPixels(id, { resetView: true });
  }

  // Switch the active image: stash the current edit, load the target's edit + pixels.
  async function selectSource(id: string): Promise<void> {
    if (id === activeId.value) return;
    opts.flushPendingHistory();
    cropMode.value = false; // leave the crop editor when switching images
    syncLiveToMap(activeId.value);
    await activateSource(id);
    schedulePersist();
  }

  // Remove an image from the library: drop its edit, thumbnail and server-side
  // cached copy. The original file on the user's disk is never touched — imports
  // only ever copy bytes into the server cache.
  async function removeSource(id: string): Promise<void> {
    const idx = sources.value.findIndex(s => s.id === id);
    if (idx < 0) return;
    sources.value = sources.value.filter(s => s.id !== id);
    edits.delete(id);
    dirtyEditIds.delete(id);
    void deleteEdit(id);
    if (thumbs[id]) {
      if (thumbs[id].startsWith("blob:")) URL.revokeObjectURL(thumbs[id]);
      delete thumbs[id];
    }
    void deleteThumb(id);
    void fetch(`${api}/sources/${id}`, { method: "DELETE" }).catch(() => {});
    let loading: Promise<boolean> | null = null;
    if (id === activeId.value) {
      cropMode.value = false;
      const next = sources.value[Math.min(idx, sources.value.length - 1)];
      if (next) {
        loading = activateSource(next.id); // synchronously points activeId + live edit at next
      } else {
        activeId.value = null;
        opts.onEmptied(); // frees the removed image's GPU texture, resets status
        opts.loadEdit(null); // resets the live edit to defaults
      }
    }
    // Persist the removal now — not after the neighbour's multi-second decode.
    persistNow();
    await loading;
  }

  // Rehydrate imported images + their edits from IndexedDB. Returns false if
  // there's nothing to restore (so the caller falls back to a sample).
  async function restoreSession(): Promise<boolean> {
    const persisted = await loadSession<S, V>();
    if (!persisted || !persisted.session.sources.length) return false;

    sources.value = persisted.session.sources.map(s => ({ ...s }));
    if (persisted.session.viewSettings) opts.sessionExtras.apply(persisted.session.viewSettings);
    edits.clear();
    for (const [id, e] of Object.entries(persisted.edits)) edits.set(id, e as ImageEdit);

    const targetId = persisted.session.activeId && sources.value.some(s => s.id === persisted.session.activeId)
      ? persisted.session.activeId
      : sources.value[0].id;
    await activateSource(targetId);

    // Backfill any thumbnails missing from the cache (e.g. first run after upgrade).
    for (const s of sources.value) if (!thumbs[s.id]) void cacheThumb(s);
    return true;
  }

  async function loadThumbCache(): Promise<void> {
    Object.assign(thumbs, await loadThumbs());
  }

  // Revoke every thumbnail object URL (component teardown). removeSource
  // already revokes per-image; this catches the rest so a long-lived page
  // that mounts the app repeatedly doesn't accumulate blob references.
  function releaseThumbs(): void {
    for (const [id, url] of Object.entries(thumbs)) {
      if (url.startsWith("blob:")) URL.revokeObjectURL(url);
      delete thumbs[id];
    }
  }

  async function uploadFiles(files: File[]): Promise<void> {
    if (!files.length) return;
    status.value = "uploading";
    errorMessage.value = null;

    // Preserve the edit of the image we're leaving before importing new ones.
    opts.flushPendingHistory();
    cropMode.value = false; // leave the crop editor when importing
    syncLiveToMap(activeId.value);

    let lastId: string | null = null;
    for (const file of files) {
      const formData = new FormData();
      formData.append("file", file);
      try {
        const res = await fetch(`${api}/sources`, { method: "POST", body: formData });
        if (!res.ok) throw new Error(await res.text());
        const source = await res.json() as Source;
        sources.value = [...sources.value, source];
        // Each new import starts from a fresh, independent edit.
        edits.set(source.id, opts.defaultEdit());
        dirtyEditIds.add(source.id);
        void cacheThumb(source);
        lastId = source.id;
      } catch (err) {
        status.value = "error";
        errorMessage.value = err instanceof Error ? err.message : String(err);
        return;
      }
    }

    // Make the last imported image active and render it (loadPixels sets the
    // final status to idle/error and records the decode timing).
    if (lastId) {
      await activateSource(lastId);
    } else {
      status.value = "idle";
    }
    persistNow();
  }

  return {
    sources, activeId, activeSource, thumbs,
    syncLiveToMap, loadEditFromMap, persistNow, schedulePersist,
    resolveUrl, thumbSrc, cacheThumb, markInvalid,
    activateSource, selectSource, removeSource,
    restoreSession, loadThumbCache, releaseThumbs, uploadFiles,
  };
}
