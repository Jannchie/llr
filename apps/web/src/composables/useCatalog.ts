import { computed, onBeforeUnmount, onMounted, reactive, ref, shallowRef, type Ref } from "vue";
import * as api from "../catalog-api";
import { buildTree, pathOf, type FolderRow } from "../catalogTree";
import { fitForKeepalive, trimEdit, type PersistedEdit } from "../edits";
import { t } from "../i18n";
import { clearState, clearThumbs, loadSession } from "../persistence";
import { IMPORT_EXTENSIONS, type Photo, type Source } from "../ui";

// The library as the browser holds it: the folder tree, the photos of the
// one folder that is open, and the photo being edited — which need not be in
// that folder. The catalog itself lives in the API's SQLite (catalog.ts);
// this composable keeps only what is on screen plus the edits touched this
// session, so a library of thousands costs the tab a folder listing.
//
// Photo records are plain objects held in shallowRefs and replaced wholesale:
// a folder of 3000 photos made deeply reactive would be 3000 proxies for
// nothing, since a record never changes while it is displayed.
//
// The composable owns library CRUD, import, and persistence; how an edit is
// captured from / pushed into the live state, and how pixels reach the
// renderer, are injected by the caller (the same contract useLibrary had).

export const ROOT_FOLDER_ID = 1;
export type LibraryView = "library" | "develop";

const UI_STATE_KEY = "llr.ui.v1";
const ADOPTED_KEY = "llr.catalog.adopted";
// Uploads in flight at once. The worker has three threads and one of them may
// be decoding the photo being edited; more uploads would only queue there.
const IMPORT_CONCURRENCY = 3;
const PERSIST_DEBOUNCE = 600;

type UiState<V> = {
  activeId: string | null;
  folderId: number;
  expanded: number[];
  view: LibraryView;
  viewSettings: V | null;
};

function isAbsoluteUrl(u: string): boolean {
  return u.startsWith("blob:") || u.startsWith("data:") || u.startsWith("http");
}

// The server's listing order (catalog.ts PHOTO_ORDER), so a photo imported
// into the open folder lands where a reload would put it.
function comparePhotos(a: Photo, b: Photo): number {
  if (a.capturedAt && b.capturedAt && a.capturedAt !== b.capturedAt) return a.capturedAt < b.capturedAt ? -1 : 1;
  if (!!a.capturedAt !== !!b.capturedAt) return a.capturedAt ? -1 : 1;
  if (a.importedAt !== b.importedAt) return a.importedAt - b.importedAt;
  return a.name.localeCompare(b.name, undefined, { sensitivity: "base" });
}

export function useCatalog<S, V>(opts: {
  api: string;
  status: Ref<"idle" | "uploading" | "rendering" | "error">;
  errorMessage: Ref<string | null>;
  cropMode: Ref<boolean>;
  /** Snapshot the live edit (snapshot + history) for stashing/persisting. */
  captureEdit: () => PersistedEdit<S>;
  /** Push an edit (null = defaults) into the live reactive state. */
  loadEdit: (e: PersistedEdit<S> | null) => void;
  /** Decode + render `id`'s pixels (the caller's loadSource). */
  loadPixels: (id: string, o: { resetView?: boolean }) => Promise<boolean>;
  /** Commit any debounced history entry before an image switch. */
  flushPendingHistory: () => void;
  /** Runs before pointing the app at another image (cancel pending reloads). */
  beforeActivate: () => void;
  /** Renderer/UI cleanup when the active image goes away. */
  onEmptied: () => void;
  /** True while a snapshot restore is in flight (suppresses persist). */
  isRestoring: () => boolean;
  /** Extra per-browser state stored with the UI state (view settings). */
  sessionExtras: { get: () => V; apply: (v: V) => void };
}) {
  const { status, errorMessage, cropMode } = opts;

  // ── Folders ──
  const folders = shallowRef<FolderRow[]>([]);
  const tree = computed(() => buildTree(folders.value));
  const selectedFolderId = ref(ROOT_FOLDER_ID);
  const expanded = reactive(new Set<number>([ROOT_FOLDER_ID]));
  const view = ref<LibraryView>("develop");
  const folderLoading = ref(false);

  // ── Photos ──
  const folderPhotos = shallowRef<Photo[]>([]);
  // Every record seen this session, by id — the active photo is looked up
  // here, so it stays addressable after the user opens another folder.
  const photosById = new Map<string, Photo>();
  const activeId = ref<string | null>(null);
  const activePhoto = shallowRef<Photo | null>(null);
  const invalidIds = reactive(new Set<string>());
  const activeSource = computed<Source | null>(() =>
    activePhoto.value ? { ...activePhoto.value, invalid: invalidIds.has(activePhoto.value.id) } : null);
  const selectedIds = reactive(new Set<string>());
  const importProgress = ref<{ done: number; total: number; failed: number } | null>(null);

  // ── Edits ──
  // null = the server has nothing stored (the photo is at its defaults), so a
  // second activation does not ask again.
  const edits = new Map<string, PersistedEdit<S> | null>();
  const dirtyEditIds = new Set<string>();
  let persistTimer = 0;
  // The photo whose edit is still on its way from the server. Until it lands,
  // the live state belongs to the previous photo — capturing it under the new
  // id would overwrite that photo's edit with someone else's.
  let pendingActivation: string | null = null;

  function photoById(id: string): Photo | undefined { return photosById.get(id); }
  function resolveUrl(u: string): string { return isAbsoluteUrl(u) ? u : `${opts.api}${u}`; }
  function thumbSrc(p: Photo): string { return resolveUrl(p.thumbUrl); }

  function markInvalid(id: string): void {
    invalidIds.add(id);
    status.value = "error";
    errorMessage.value = t("error.sourceGone");
  }
  function clearInvalid(id: string): void { invalidIds.delete(id); }

  function fail(error: unknown): void {
    status.value = "error";
    errorMessage.value = error instanceof Error ? error.message : String(error);
  }

  // ── Persistence ──

  // Copy the current live edit (snapshot + history) into the map under `id`.
  // Commits the debounced history entry first: captureEdit() pairs the live
  // snapshot with the committed history, so capturing mid-debounce would store
  // a snapshot that no history entry holds — undo would then jump two steps.
  function syncLiveToMap(id: string | null): void {
    if (!id || id === pendingActivation) return;
    opts.flushPendingHistory();
    edits.set(id, opts.captureEdit());
    dirtyEditIds.add(id);
  }

  function saveUiState(): void {
    const state: UiState<V> = {
      activeId: activeId.value,
      folderId: selectedFolderId.value,
      expanded: [...expanded],
      view: view.value,
      viewSettings: opts.sessionExtras.get(),
    };
    try { localStorage.setItem(UI_STATE_KEY, JSON.stringify(state)); } catch { /* quota / private mode */ }
  }

  function loadUiState(): Partial<UiState<V>> {
    try {
      const raw = localStorage.getItem(UI_STATE_KEY);
      return raw ? (JSON.parse(raw) as Partial<UiState<V>>) : {};
    } catch {
      return {};
    }
  }

  function persistNow(): void {
    if (persistTimer) { clearTimeout(persistTimer); persistTimer = 0; }
    syncLiveToMap(activeId.value);
    for (const id of dirtyEditIds) {
      const e = edits.get(id);
      if (e) api.putEdit(id, trimEdit(e)).catch(() => { dirtyEditIds.add(id); });
    }
    dirtyEditIds.clear();
    saveUiState();
  }

  // The tab is closing: only a keepalive request survives, and only a small one.
  function persistOnUnload(): void {
    if (persistTimer) { clearTimeout(persistTimer); persistTimer = 0; }
    syncLiveToMap(activeId.value);
    for (const id of dirtyEditIds) {
      const e = edits.get(id);
      if (e) void api.putEdit(id, fitForKeepalive(e), { keepalive: true }).catch(() => {});
    }
    dirtyEditIds.clear();
    saveUiState();
  }

  function schedulePersist(): void {
    if (opts.isRestoring()) return;
    if (persistTimer) clearTimeout(persistTimer);
    persistTimer = window.setTimeout(() => { persistTimer = 0; persistNow(); }, PERSIST_DEBOUNCE);
  }

  // ── Activation ──

  // Point the live edit + pixels at `id` (does NOT save the outgoing edit).
  // The edit has to land before the decode is asked for: loadSource reads the
  // profile / denoise / look it sends from the live edit.
  async function activateSource(id: string): Promise<boolean> {
    opts.beforeActivate();
    activeId.value = id;
    pendingActivation = id;
    const stale = () => activeId.value !== id;
    try {
      const photo = photosById.get(id) ?? await api.getPhoto(id);
      if (stale()) return false;
      if (!photo) {
        activeId.value = null;
        pendingActivation = null;
        return false;
      }
      photosById.set(id, photo);
      activePhoto.value = photo;
      let e = edits.get(id);
      if (e === undefined) {
        e = await api.getEdit<S>(id);
        if (stale()) return false;
        edits.set(id, e);
      }
      opts.loadEdit(e);
      pendingActivation = null;
    } catch (error) {
      if (stale()) return false;
      pendingActivation = null;
      fail(error);
      return false;
    }
    return opts.loadPixels(id, { resetView: true });
  }

  function clearActive(): void {
    activeId.value = null;
    pendingActivation = null;
    activePhoto.value = null;
    opts.onEmptied(); // frees the removed image's GPU texture, resets status
    opts.loadEdit(null); // resets the live edit to defaults
  }

  // Switch the active image: stash the current edit, load the target's edit + pixels.
  async function selectSource(id: string): Promise<void> {
    if (id === activeId.value) return;
    cropMode.value = false; // leave the crop editor when switching images
    syncLiveToMap(activeId.value);
    await activateSource(id);
    schedulePersist();
  }

  /** Open `id` in the develop view (the grid's double-click). */
  async function openPhoto(id: string): Promise<void> {
    view.value = "develop";
    await selectSource(id);
    saveUiState();
  }

  function setView(v: LibraryView): void {
    if (view.value === v) return;
    if (v === "library") cropMode.value = false;
    view.value = v;
    saveUiState();
  }

  // ── Folder navigation ──

  async function refreshTree(): Promise<void> {
    folders.value = await api.catalogTree();
  }

  function bumpCount(folderId: number, delta: number): void {
    folders.value = folders.value.map(f => f.id === folderId ? { ...f, count: Math.max(0, f.count + delta) } : f);
  }

  async function selectFolder(id: number, opts: { keepSelection?: boolean } = {}): Promise<void> {
    selectedFolderId.value = id;
    if (!opts.keepSelection) selectedIds.clear();
    folderLoading.value = true;
    try {
      const photos = await api.listPhotos(id);
      if (selectedFolderId.value !== id) return; // the user moved on
      for (const p of photos) photosById.set(p.id, p);
      folderPhotos.value = photos;
    } catch (error) {
      if (selectedFolderId.value === id) fail(error);
    } finally {
      if (selectedFolderId.value === id) folderLoading.value = false;
    }
    saveUiState();
  }

  function toggleExpanded(id: number): void {
    if (expanded.has(id)) expanded.delete(id); else expanded.add(id);
    saveUiState();
  }

  async function createFolder(parentId: number, name: string): Promise<FolderRow | null> {
    try {
      const folder = await api.createFolder(parentId, name);
      expanded.add(parentId);
      await refreshTree();
      return folder;
    } catch (error) {
      fail(error);
      return null;
    }
  }

  async function renameFolder(id: number, name: string): Promise<void> {
    try {
      await api.renameFolder(id, name);
      await refreshTree();
    } catch (error) {
      fail(error);
    }
  }

  async function moveFolder(id: number, parentId: number): Promise<void> {
    try {
      await api.moveFolder(id, parentId);
      expanded.add(parentId);
      await refreshTree();
    } catch (error) {
      fail(error);
    }
  }

  // Deletes the folder and every photo under it. The caller confirms first.
  async function deleteFolder(id: number): Promise<void> {
    const parent = pathOf(folders.value, id).at(-2)?.id ?? ROOT_FOLDER_ID;
    let removed: string[];
    try {
      removed = await api.deleteFolder(id);
    } catch (error) {
      fail(error);
      return;
    }
    forgetPhotos(removed);
    if (activeId.value && removed.includes(activeId.value)) clearActive();
    await refreshTree();
    if (!folders.value.some(f => f.id === selectedFolderId.value)) await selectFolder(parent);
    persistNow();
  }

  function forgetPhotos(ids: string[]): void {
    const gone = new Set(ids);
    for (const id of gone) {
      photosById.delete(id);
      edits.delete(id);
      dirtyEditIds.delete(id);
      selectedIds.delete(id);
      invalidIds.delete(id);
    }
    if (folderPhotos.value.some(p => gone.has(p.id))) folderPhotos.value = folderPhotos.value.filter(p => !gone.has(p.id));
  }

  // ── Photo operations ──

  // Remove photos from the library: their rows, edits, thumbnails and the
  // server-side cached copies. The originals on the user's disk are never
  // touched — imports only ever copy bytes into the server cache.
  async function removePhotos(ids: string[]): Promise<void> {
    if (!ids.length) return;
    const gone = new Set(ids);
    const list = folderPhotos.value;
    // The neighbour that takes over when the active photo goes: the first
    // survivor after the last removed one, else the last survivor of all.
    let next: Photo | null = null;
    if (activeId.value && gone.has(activeId.value)) {
      let lastIdx = -1;
      list.forEach((p, i) => { if (gone.has(p.id)) lastIdx = i; });
      next = list.slice(lastIdx + 1).find(p => !gone.has(p.id)) ?? [...list].reverse().find(p => !gone.has(p.id)) ?? null;
    }
    const wasActive = !!activeId.value && gone.has(activeId.value);
    forgetPhotos(ids);
    for (const p of list) if (gone.has(p.id)) bumpCount(p.folderId, -1);
    let loading: Promise<boolean> | null = null;
    if (wasActive) {
      cropMode.value = false;
      if (next) loading = activateSource(next.id); // synchronously points activeId + live edit at next
      else clearActive();
    }
    // Persist the removal now — not after the neighbour's multi-second decode.
    persistNow();
    api.deletePhotos(ids).catch(fail);
    await loading;
  }

  async function movePhotos(ids: string[], folderId: number): Promise<void> {
    const moving = ids.filter(id => photosById.get(id)?.folderId !== folderId);
    if (!moving.length) return;
    for (const id of moving) {
      const p = photosById.get(id);
      if (!p) continue;
      bumpCount(p.folderId, -1);
      bumpCount(folderId, 1);
      photosById.set(id, { ...p, folderId });
    }
    const gone = new Set(moving);
    if (folderId !== selectedFolderId.value) {
      folderPhotos.value = folderPhotos.value.filter(p => !gone.has(p.id));
      for (const id of moving) selectedIds.delete(id);
    }
    try {
      await api.movePhotos(moving, folderId);
    } catch (error) {
      fail(error);
      await Promise.all([refreshTree(), selectFolder(selectedFolderId.value)]);
    }
  }

  // ── Import ──

  type ImportEntry = { file: File; folderId: number };

  function insertIntoFolder(photo: Photo): void {
    photosById.set(photo.id, photo);
    bumpCount(photo.folderId, 1);
    if (photo.folderId !== selectedFolderId.value) return;
    const list = folderPhotos.value.slice();
    let i = list.length;
    while (i > 0 && comparePhotos(photo, list[i - 1]) < 0) i--;
    list.splice(i, 0, photo);
    folderPhotos.value = list;
  }

  // Upload `entries` a few at a time. The first success is opened when nothing
  // is — the user edits it while the rest stream in — and a single dropped
  // file is opened regardless: that is what dropping one file means.
  async function importFiles(entries: ImportEntry[]): Promise<void> {
    if (!entries.length) return;
    errorMessage.value = null;
    cropMode.value = false; // leave the crop editor when importing
    const progress = importProgress.value ?? { done: 0, total: 0, failed: 0 };
    progress.total += entries.length;
    importProgress.value = { ...progress };
    if (!activeSource.value) status.value = "uploading";

    const single = entries.length === 1;
    let opened = false;
    let openTarget: string | null = null;
    const queue = entries.slice();
    const worker = async () => {
      for (let entry = queue.shift(); entry; entry = queue.shift()) {
        try {
          const photo = await api.uploadPhoto(entry.file, entry.folderId);
          insertIntoFolder(photo);
          progress.done++;
          if (!opened && (single || !activeId.value)) {
            opened = true;
            openTarget = photo.id;
            syncLiveToMap(activeId.value);
            void activateSource(photo.id);
          }
        } catch {
          progress.done++;
          progress.failed++;
        }
        importProgress.value = { ...progress };
      }
    };
    await Promise.all(Array.from({ length: Math.min(IMPORT_CONCURRENCY, entries.length) }, worker));

    if (importProgress.value && importProgress.value.done >= importProgress.value.total) {
      const failed = importProgress.value.failed;
      importProgress.value = null;
      if (failed) {
        status.value = "error";
        errorMessage.value = t("status.importFailed", { n: failed });
      } else if (status.value === "uploading") {
        status.value = "idle";
      }
    }
    if (openTarget) view.value = "develop";
    void refreshTree();
    persistNow();
  }

  function importable(name: string): boolean {
    const dot = name.lastIndexOf(".");
    return dot > 0 && IMPORT_EXTENSIONS.has(name.slice(dot + 1).toLowerCase());
  }

  // A drop from the OS. Directories arrive only through the entries API, and
  // only while the drop event is on the stack — so entries are taken here,
  // synchronously, and walked afterwards. Each directory becomes a folder
  // under `folderId` (merged with an existing one of the same name).
  async function importDropped(transfer: DataTransfer, folderId: number): Promise<void> {
    const entries: FileSystemEntry[] = [];
    for (const item of Array.from(transfer.items)) {
      const entry = item.webkitGetAsEntry?.();
      if (entry) entries.push(entry);
    }
    if (!entries.some(e => e.isDirectory)) {
      await importFiles(Array.from(transfer.files).filter(f => importable(f.name)).map(file => ({ file, folderId })));
      return;
    }
    const out: ImportEntry[] = [];
    for (const entry of entries) await walkEntry(entry, folderId, out);
    await importFiles(out);
  }

  async function walkEntry(entry: FileSystemEntry, folderId: number, out: ImportEntry[]): Promise<void> {
    if (entry.isFile) {
      if (!importable(entry.name)) return;
      const file = await new Promise<File | null>(res => (entry as FileSystemFileEntry).file(res, () => res(null)));
      if (file) out.push({ file, folderId });
      return;
    }
    if (!entry.isDirectory) return;
    let folder: FolderRow;
    try {
      folder = await api.createFolder(folderId, entry.name, true);
    } catch (error) {
      fail(error);
      return;
    }
    expanded.add(folderId);
    const reader = (entry as FileSystemDirectoryEntry).createReader();
    // readEntries hands back at most ~100 per call; drain it.
    for (;;) {
      const batch = await new Promise<FileSystemEntry[]>(res => reader.readEntries(res, () => res([])));
      if (!batch.length) break;
      for (const child of batch) await walkEntry(child, folder.id, out);
    }
  }

  // A picked directory (<input webkitdirectory>): files carry their path
  // relative to the picked root, which becomes the folder structure.
  async function importPickedDirectory(files: File[], folderId: number): Promise<void> {
    const byPath = new Map<string, number>();
    const out: ImportEntry[] = [];
    for (const file of files) {
      if (!importable(file.name)) continue;
      const parts = (file.webkitRelativePath || file.name).split("/");
      parts.pop();
      let parent = folderId;
      let path = "";
      for (const part of parts) {
        path += `/${part}`;
        let id = byPath.get(path);
        if (id === undefined) {
          try {
            id = (await api.createFolder(parent, part, true)).id;
          } catch (error) {
            fail(error);
            return;
          }
          byPath.set(path, id);
          expanded.add(parent);
        }
        parent = id;
      }
      out.push({ file, folderId: parent });
    }
    await refreshTree();
    await importFiles(out);
  }

  // ── Boot ──

  // The pre-catalog library lived in this browser's IndexedDB (ids, names,
  // edits) with the files already in the API's cache. Hand it over once; the
  // API answers with what it could still find. Runs even when there is
  // nothing to hand over — the API's directory sweep waits for the handover.
  async function adoptLegacy(): Promise<{ activeId: string | null; viewSettings: V | null }> {
    let carried: { activeId: string | null; viewSettings: V | null } = { activeId: null, viewSettings: null };
    try {
      if (localStorage.getItem(ADOPTED_KEY) === "1") return carried;
    } catch { /* fall through: adopt again, it is idempotent */ }
    const legacy = await loadSession<S, V>();
    const photos = legacy?.session.sources.map(s => ({ id: s.id, name: s.name, size: s.size, edit: legacy.edits[s.id] })) ?? [];
    if (photos.length) status.value = "uploading";
    try {
      const result = await api.adoptLegacy(photos);
      if (legacy) {
        carried = {
          activeId: legacy.session.activeId && result.adopted.includes(legacy.session.activeId) ? legacy.session.activeId : null,
          viewSettings: legacy.session.viewSettings ?? null,
        };
        await clearState();
        await clearThumbs();
      }
      try { localStorage.setItem(ADOPTED_KEY, "1"); } catch { /* ignore */ }
    } catch (error) {
      // The API is not there yet; the next boot tries again.
      fail(error);
    }
    if (status.value === "uploading") status.value = "idle";
    return carried;
  }

  async function boot(): Promise<void> {
    const carried = await adoptLegacy();
    const saved = loadUiState();
    if (saved.view === "library" || saved.view === "develop") view.value = saved.view;
    if (Array.isArray(saved.expanded)) for (const id of saved.expanded) if (typeof id === "number") expanded.add(id);
    const viewSettings = saved.viewSettings ?? carried.viewSettings;
    if (viewSettings) opts.sessionExtras.apply(viewSettings);
    try {
      await refreshTree();
    } catch (error) {
      fail(error);
      return;
    }
    const folderId = typeof saved.folderId === "number" && folders.value.some(f => f.id === saved.folderId) ? saved.folderId : ROOT_FOLDER_ID;
    const target = saved.activeId ?? carried.activeId;
    await Promise.all([
      selectFolder(folderId),
      target ? activateSource(target) : Promise.resolve(false),
    ]);
    // Nothing open but a library to browse: start in the grid rather than at
    // an empty stage.
    if (!activeId.value && folders.value.some(f => f.count > 0)) view.value = "library";
    saveUiState();
  }

  // Another tab may have imported or reorganised meanwhile; the catalog is
  // the truth, so coming back to this tab re-reads what it shows.
  function onVisible(): void {
    if (document.visibilityState !== "visible" || importProgress.value) return;
    void refreshTree().then(() => selectFolder(selectedFolderId.value, { keepSelection: true })).catch(() => {});
  }
  onMounted(() => document.addEventListener("visibilitychange", onVisible));
  onBeforeUnmount(() => document.removeEventListener("visibilitychange", onVisible));

  return {
    folders, tree, selectedFolderId, expanded, view, folderLoading,
    folderPhotos, activeId, activeSource, selectedIds, importProgress,
    photoById, resolveUrl, thumbSrc, markInvalid, clearInvalid,
    persistNow, persistOnUnload, schedulePersist,
    activateSource, selectSource, openPhoto, setView,
    selectFolder, toggleExpanded, createFolder, renameFolder, moveFolder, deleteFolder,
    removePhotos, movePhotos, importFiles, importDropped, importPickedDirectory,
    boot,
  };
}
