<script setup lang="ts">
import { computed, nextTick, ref } from "vue";
import { descendantIds, visibleRows, type FolderNode, type FolderRow } from "../catalogTree";
import { t } from "../i18n";

// The folder sidebar: the catalog's virtual folders as an expandable tree.
// Every row is a drop target — for photos dragged from the grid or filmstrip
// (a move), for another folder (a re-parent), and for files or directories
// from the OS (an import into that folder).
const props = defineProps<{
  folders: FolderRow[];
  tree: FolderNode[];
  expanded: Set<number>;
  selectedId: number;
  busy: boolean;
}>();

const emit = defineEmits<{
  (e: "select", id: number): void;
  (e: "toggle", id: number): void;
  (e: "create", parentId: number, name: string): void;
  (e: "rename", id: number, name: string): void;
  (e: "delete", id: number): void;
  (e: "movePhotos", ids: string[], folderId: number): void;
  (e: "moveFolder", id: number, parentId: number): void;
  (e: "dropFiles", transfer: DataTransfer, folderId: number): void;
  (e: "importFiles", folderId: number): void;
  (e: "importFolder", folderId: number): void;
}>();

const PHOTOS_MIME = "application/x-llr-photos";
const FOLDER_MIME = "application/x-llr-folder";
const ROOT_ID = 1;

const rows = computed(() => visibleRows(props.tree, props.expanded));

// ── Inline editing: a rename in place, or a new folder typed into a row that
// appears under its parent. One editor at a time.
const editing = ref<{ kind: "rename"; id: number } | { kind: "create"; parentId: number; depth: number } | null>(null);
const editText = ref("");
const editInput = ref<HTMLInputElement | null>(null);

function startCreate(parentId: number): void {
  const parent = props.folders.find(f => f.id === parentId);
  const depth = parent ? rows.value.find(r => r.id === parentId)?.depth ?? 0 : 0;
  if (!props.expanded.has(parentId)) emit("toggle", parentId);
  editing.value = { kind: "create", parentId, depth: depth + 1 };
  editText.value = t("tree.newFolderName");
  focusEditor();
}

function startRename(node: FolderNode): void {
  if (node.id === ROOT_ID) return;
  editing.value = { kind: "rename", id: node.id };
  editText.value = node.name;
  focusEditor();
}

// A function ref: a plain `ref="..."` inside v-for collects an array, and
// there is only ever one editor open.
function setEditInput(el: unknown): void { editInput.value = el instanceof HTMLInputElement ? el : null; }

function focusEditor(): void {
  void nextTick(() => { editInput.value?.focus(); editInput.value?.select(); });
}

function commitEdit(): void {
  const e = editing.value;
  editing.value = null;
  const name = editText.value.trim();
  if (!e || !name) return;
  if (e.kind === "create") emit("create", e.parentId, name);
  else if (name !== props.folders.find(f => f.id === e.id)?.name) emit("rename", e.id, name);
}

function cancelEdit(): void { editing.value = null; }

function confirmDelete(node: FolderNode): void {
  if (node.id === ROOT_ID) return;
  if (window.confirm(t("tree.deleteConfirm", { name: node.name, n: node.total }))) emit("delete", node.id);
}

function label(node: FolderNode): string {
  return node.id === ROOT_ID ? t("tree.root") : node.name;
}

// The row that follows `parentId`'s subtree is where a new-folder editor goes.
function createRowAfter(): number {
  const e = editing.value;
  if (!e || e.kind !== "create") return -1;
  const subtree = descendantIds(props.folders, e.parentId);
  let last = -1;
  rows.value.forEach((r, i) => { if (subtree.has(r.id)) last = i; });
  return last;
}
const createAfterIndex = computed(createRowAfter);

// ── Drag and drop ──

const dropTarget = ref<number | null>(null);
// dragenter/leave fire for every child crossed; count them so the highlight
// only clears when the pointer really leaves the row.
let enterDepth = 0;
let enterRow: number | null = null;

// A folder may not be dropped into itself or its own subtree.
const draggingFolder = ref<number | null>(null);
const blockedTargets = computed(() => draggingFolder.value == null ? new Set<number>() : descendantIds(props.folders, draggingFolder.value));

function accepts(e: DragEvent, id: number): boolean {
  const types = e.dataTransfer?.types ?? [];
  if (types.includes(FOLDER_MIME)) return !blockedTargets.value.has(id);
  return types.includes(PHOTOS_MIME) || types.includes("Files");
}

function onDragEnter(e: DragEvent, id: number): void {
  if (!accepts(e, id)) return;
  if (enterRow !== id) { enterRow = id; enterDepth = 0; }
  enterDepth++;
  dropTarget.value = id;
}

function onDragOver(e: DragEvent, id: number): void {
  if (!accepts(e, id)) return;
  e.preventDefault();
  e.stopPropagation(); // the shell's import overlay must not take this drop
  if (e.dataTransfer) e.dataTransfer.dropEffect = e.dataTransfer.types.includes("Files") ? "copy" : "move";
}

function onDragLeave(e: DragEvent, id: number): void {
  if (enterRow !== id) return;
  enterDepth--;
  if (enterDepth <= 0) { enterDepth = 0; enterRow = null; dropTarget.value = null; }
  void e;
}

function onDrop(e: DragEvent, id: number): void {
  enterDepth = 0; enterRow = null; dropTarget.value = null;
  const dt = e.dataTransfer;
  if (!dt || !accepts(e, id)) return;
  e.preventDefault();
  e.stopPropagation();
  const photos = dt.getData(PHOTOS_MIME);
  if (photos) {
    try {
      const ids = JSON.parse(photos) as unknown;
      if (Array.isArray(ids) && ids.every(x => typeof x === "string")) emit("movePhotos", ids as string[], id);
    } catch { /* not ours */ }
    return;
  }
  const folder = dt.getData(FOLDER_MIME);
  if (folder) {
    const fid = Number(folder);
    if (Number.isInteger(fid) && fid !== id && !blockedTargets.value.has(id)) emit("moveFolder", fid, id);
    return;
  }
  if (dt.types.includes("Files")) emit("dropFiles", dt, id);
}

function onFolderDragStart(e: DragEvent, node: FolderNode): void {
  if (node.id === ROOT_ID || !e.dataTransfer) { e.preventDefault(); return; }
  draggingFolder.value = node.id;
  e.dataTransfer.setData(FOLDER_MIME, String(node.id));
  e.dataTransfer.effectAllowed = "move";
}

function onFolderDragEnd(): void {
  draggingFolder.value = null;
  dropTarget.value = null;
}

function onRowKeyDown(e: KeyboardEvent, node: FolderNode): void {
  if (e.key === "F2") { e.preventDefault(); startRename(node); }
  else if (e.key === "Delete") { e.preventDefault(); confirmDelete(node); }
  else if (e.key === "ArrowRight" && node.children.length && !props.expanded.has(node.id)) { e.preventDefault(); emit("toggle", node.id); }
  else if (e.key === "ArrowLeft" && props.expanded.has(node.id)) { e.preventDefault(); emit("toggle", node.id); }
}
</script>

<template>
  <aside class="folder-tree" :aria-label="t('tree.title')">
    <header class="tree-header">
      <span class="tree-title">{{ t('tree.title') }}</span>
      <div class="tree-actions">
        <button type="button" class="tree-btn" :title="t('tree.newFolder')" :aria-label="t('tree.newFolder')"
          :disabled="busy" @click="startCreate(selectedId)">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
            <path d="M12 11v6M9 14h6" />
          </svg>
        </button>
        <button type="button" class="tree-btn" :title="t('tree.importFiles')" :aria-label="t('tree.importFiles')"
          :disabled="busy" @click="emit('importFiles', selectedId)">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M12 16V4M7 9l5-5 5 5" />
            <path d="M4 20h16" />
          </svg>
        </button>
        <button type="button" class="tree-btn" :title="t('tree.importFolder')" :aria-label="t('tree.importFolder')"
          :disabled="busy" @click="emit('importFolder', selectedId)">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
            <path d="M12 17v-6M9 14l3-3 3 3" />
          </svg>
        </button>
      </div>
    </header>

    <div class="tree-list" role="tree">
      <template v-for="(node, i) in rows" :key="node.id">
        <div class="tree-row" role="treeitem" tabindex="0"
          :aria-expanded="node.children.length ? expanded.has(node.id) : undefined"
          :aria-selected="node.id === selectedId"
          :class="{ 'is-selected': node.id === selectedId, 'is-drop-target': dropTarget === node.id, 'is-blocked': blockedTargets.has(node.id) && draggingFolder != null }"
          :style="{ '--depth': node.depth }"
          :draggable="node.id !== 1"
          @click="emit('select', node.id)"
          @dblclick="startRename(node)"
          @keydown="onRowKeyDown($event, node)"
          @dragstart="onFolderDragStart($event, node)"
          @dragend="onFolderDragEnd"
          @dragenter="onDragEnter($event, node.id)"
          @dragover="onDragOver($event, node.id)"
          @dragleave="onDragLeave($event, node.id)"
          @drop="onDrop($event, node.id)">
          <button type="button" class="tree-twisty" tabindex="-1" :class="{ 'is-open': expanded.has(node.id), 'is-leaf': !node.children.length }"
            :aria-hidden="!node.children.length" @click.stop="node.children.length && emit('toggle', node.id)">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M9 6l6 6-6 6" />
            </svg>
          </button>
          <svg class="tree-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" aria-hidden="true">
            <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
          </svg>
          <input v-if="editing?.kind === 'rename' && editing.id === node.id" :ref="setEditInput" class="tree-edit"
            v-model="editText" @keydown.enter.prevent="commitEdit" @keydown.esc.prevent="cancelEdit" @blur="commitEdit" @click.stop />
          <span v-else class="tree-name">{{ label(node) }}</span>
          <!-- Collapsed: everything below; expanded: the folder's own photos,
               the children carry theirs (Lightroom's convention). -->
          <span class="tree-count" v-if="expanded.has(node.id) ? node.count : node.total">{{ expanded.has(node.id) ? node.count : node.total }}</span>
          <span class="tree-row-actions" v-if="node.id !== 1">
            <button type="button" class="tree-btn tree-btn-sm" tabindex="-1" :title="t('tree.rename')" :aria-label="t('tree.rename')"
              @click.stop="startRename(node)">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M4 20h4l10-10-4-4L4 16z" />
              </svg>
            </button>
            <button type="button" class="tree-btn tree-btn-sm" tabindex="-1" :title="t('tree.delete')" :aria-label="t('tree.delete')"
              @click.stop="confirmDelete(node)">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">
                <path d="M6 6l12 12M18 6L6 18" />
              </svg>
            </button>
          </span>
        </div>
        <div v-if="editing?.kind === 'create' && i === createAfterIndex" class="tree-row is-editing" :style="{ '--depth': editing.depth }">
          <span class="tree-twisty is-leaf" aria-hidden="true" />
          <svg class="tree-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" aria-hidden="true">
            <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
          </svg>
          <input :ref="setEditInput" class="tree-edit" v-model="editText"
            @keydown.enter.prevent="commitEdit" @keydown.esc.prevent="cancelEdit" @blur="commitEdit" />
        </div>
      </template>
    </div>
  </aside>
</template>
