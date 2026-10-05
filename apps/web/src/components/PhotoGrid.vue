<script setup lang="ts">
import { computed, onBeforeUnmount, ref, useTemplateRef, watch } from "vue";
import ContextMenu, { type MenuItem } from "./ContextMenu.vue";
import InlineEdit from "./InlineEdit.vue";
import { t } from "../i18n";
import { rowAt, useVirtualRows } from "../composables/useVirtualRows";
import { buildRows, cellLeft, GRID_CELL, photosInRect, type GridRow, type Rect } from "../gridRows";
import type { Section } from "../librarySort";
import { formatBytes, type Photo } from "../ui";

// The library view: the open folder as a grid of thumbnails, windowed so a
// folder of thousands mounts a screen's worth of cells. The catalog sorts and
// groups the photos into sections; this component lays those sections out —
// group headers over bands of cells — and drives everything the pointer does
// to them: the selection (click, ctrl, shift, marquee, arrows), the drags that
// carry the selected ids to the folder tree, the rename in place, and the
// right-click menu.
//
// Selection is a reactive Set the catalog owns; the grid edits it in place. The
// layout is arithmetic (gridRows.ts), so a cell that was never mounted can
// still be hit-tested, scrolled to, and selected by a marquee.

const props = defineProps<{
  /** The folder's photos, cut into groups (one unlabelled section when off). */
  sections: Section[];
  grouped: boolean;
  activeId: string | null;
  selectedIds: Set<string>;
  loading: boolean;
  thumbSrc: (p: Photo) => string;
  isInvalid: (id: string) => boolean;
  /** Where "move to…" can send the selection: every folder but the open one. */
  moveTargets: { id: number; label: string }[];
}>();

const emit = defineEmits<{
  (e: "open", id: string): void;
  (e: "remove", ids: string[]): void;
  (e: "rename", id: string, name: string): void;
  (e: "move", ids: string[], folderId: number): void;
  (e: "newFolder"): void;
  (e: "importFiles"): void;
}>();

const container = ref<HTMLElement | null>(null);
const inner = ref<HTMLElement | null>(null);

// The flattened listing: section by section, the order the rows count through.
const ordered = computed(() => props.sections.flatMap(s => s.photos));

// The virtualiser measures the lane count (how many cells fit across) and the
// layout turns it into rows, so the two depend on each other once — the
// composable owns `lanes`, the layout reads it.
const lanes = ref(1);
const layout = computed(() => buildRows(props.sections, lanes.value, props.grouped));
const heights = computed(() => layout.value.rows.map(r => r.height));
const { visible, tops, totalSize, viewport, scrollToRow } = useVirtualRows({
  container, heights, lanes, cellWidth: GRID_CELL.w, gap: GRID_CELL.gap,
});

const visibleRows = computed(() => {
  const all = layout.value.rows;
  const out: { row: GridRow; top: number }[] = [];
  for (let i = visible.value.start; i < visible.value.end && i < all.length; i++) {
    out.push({ row: all[i], top: tops.value[i] ?? 0 });
  }
  return out;
});

function rowPhotos(row: GridRow): Photo[] {
  return row.kind === "photos" ? ordered.value.slice(row.first, row.first + row.count) : [];
}

function indexOfId(id: string): number {
  return ordered.value.findIndex(p => p.id === id);
}

// ── Selection ──

// `anchor` is the fixed end of a shift-range (the cell a run of extensions
// grows from); `cursor` is the moving end, and where the keyboard sits.
const anchor = ref(-1);
const cursor = ref(-1);
watch(ordered, () => { anchor.value = -1; cursor.value = -1; });

function selectOnly(i: number): void {
  props.selectedIds.clear();
  const p = ordered.value[i];
  if (p) props.selectedIds.add(p.id);
  anchor.value = i;
  cursor.value = i;
}

function selectRange(from: number, to: number): void {
  const [a, b] = from <= to ? [from, to] : [to, from];
  for (let i = a; i <= b; i++) {
    const p = ordered.value[i];
    if (p) props.selectedIds.add(p.id);
  }
}

// Shift extends from the anchor to `to` — the whole run each time, so a second
// shift-arrow grows the range rather than replacing it with the same cell.
function extendTo(to: number, additive: boolean): void {
  if (!additive) props.selectedIds.clear();
  selectRange(anchor.value >= 0 ? anchor.value : to, to);
  cursor.value = to;
}

function selection(): string[] {
  return [...props.selectedIds];
}

function invertSelection(): void {
  for (const p of ordered.value) {
    if (props.selectedIds.has(p.id)) props.selectedIds.delete(p.id);
    else props.selectedIds.add(p.id);
  }
}

function onCellClick(e: MouseEvent, i: number): void {
  const id = ordered.value[i].id;
  if (e.shiftKey && anchor.value >= 0) {
    extendTo(i, e.ctrlKey || e.metaKey);
  } else if (e.ctrlKey || e.metaKey) {
    if (props.selectedIds.has(id)) props.selectedIds.delete(id); else props.selectedIds.add(id);
    anchor.value = i;
    cursor.value = i;
  } else {
    selectOnly(i);
  }
}

// ── Marquee ──
//
// A drag from the empty space between cells sweeps a rectangle over them. The
// rectangle lives in the content box's coordinates, so it stays over the photos
// it swept while the container scrolls underneath; holding ctrl, shift or cmd
// adds to what was already selected instead of starting over.
//
// The box is drawn by writing the element's style, not through a reactive
// value: this runs on every pointermove and on every frame of the auto-scroll,
// and a re-render of the grid per frame is not worth the rectangle.

const marquee = ref(false);
const marqueeEl = useTemplateRef<HTMLElement>("marqueeEl");
const EDGE_SCROLL = 40;  // how close to the edge starts scrolling, px
const EDGE_SPEED = 18;   // and how far it scrolls each frame

// Everything the drag needs that does not change while it lasts — read once, at
// pointerdown, so no handler on this path forces a layout.
type Marquee = {
  origin: { x: number; y: number };  // where the press was, content coordinates
  client: { x: number; y: number };  // the pointer now, client coordinates
  base: Set<string> | null;          // what a modifier key keeps selected
  contentLeft: number; contentTop: number; scrollTop: number;
  containerTop: number; containerBottom: number;
  edge: number;                      // px per frame, signed, 0 when not scrolling
};

let drag: Marquee | null = null;
let pointerId = -1;
let frame = 0;
// What the last sweep selected, so a frame only touches the difference: a
// marquee dragged over a large folder would otherwise re-walk the whole
// selection on every pointer move.
let sweptIds = new Set<string>();

/**
 * The pointer's position in the content box's coordinates. The origin and the
 * scroll offset were read once, at pointerdown, and the content box's top
 * travels up as the container scrolls — which is exactly the offset the rows are
 * placed in.
 */
function contentPoint(m: Marquee): { x: number; y: number } {
  const el = container.value;
  return {
    x: m.client.x - m.contentLeft,
    y: m.client.y - m.contentTop + (el ? el.scrollTop - m.scrollTop : 0),
  };
}

function marqueeRect(m: Marquee): Rect {
  const point = contentPoint(m);
  return {
    left: Math.min(m.origin.x, point.x), top: Math.min(m.origin.y, point.y),
    right: Math.max(m.origin.x, point.x), bottom: Math.max(m.origin.y, point.y),
  };
}

function drawMarquee(rect: Rect): void {
  const el = marqueeEl.value;
  if (!el) return;
  el.style.left = `${rect.left}px`;
  el.style.top = `${rect.top}px`;
  el.style.width = `${rect.right - rect.left}px`;
  el.style.height = `${rect.bottom - rect.top}px`;
}

/** Draw the rectangle and make the selection it sweeps. */
function sweep(m: Marquee): void {
  const rows = layout.value.rows;
  const ts = tops.value;
  const rect = marqueeRect(m);
  drawMarquee(rect);
  // Rows are in order, so the scan can start at the first one that can reach
  // the rectangle instead of at the top of the folder.
  const hits = photosInRect(rows, ts, rect, rowAt(ts, heights.value, rect.top));
  const next = new Set<string>();
  for (const i of hits) {
    const p = ordered.value[i];
    if (p) next.add(p.id);
  }
  // Only what entered or left the rectangle since the last frame; the ids the
  // press kept (a modifier was held) stay where they are.
  for (const id of sweptIds) if (!next.has(id) && !m.base?.has(id)) props.selectedIds.delete(id);
  for (const id of next) props.selectedIds.add(id);
  sweptIds = next;
}

function onMarqueeMove(e: PointerEvent): void {
  const m = drag;
  if (!m) return;
  m.client = { x: e.clientX, y: e.clientY };
  m.edge = e.clientY < m.containerTop + EDGE_SCROLL ? -EDGE_SPEED
    : e.clientY > m.containerBottom - EDGE_SCROLL ? EDGE_SPEED
      : 0;
  sweep(m);
  if (m.edge && !frame) frame = requestAnimationFrame(autoScroll);
}

// Scroll by a frame's worth and re-sweep: the pointer has not moved, but the
// content under it has, which is the whole point of the auto-scroll.
function autoScroll(): void {
  frame = 0;
  const m = drag;
  const el = container.value;
  if (!m || !el || !m.edge) return;
  el.scrollTop += m.edge;
  sweep(m);
  if (m.edge) frame = requestAnimationFrame(autoScroll);
}

function onPointerDown(e: PointerEvent): void {
  const target = e.target as HTMLElement;
  const el = container.value;
  if (e.button !== 0 || drag || !el) return;
  // The scrollbar is part of the container: dragging it is not a marquee.
  if (e.clientX - el.getBoundingClientRect().left >= el.clientWidth) return;
  if (target.closest(".grid-cell") || target.closest(".grid-header")) return;
  // Keeps the drag from starting a text selection over the thumbnails; focus
  // is taken by hand instead, since preventDefault would otherwise drop it.
  e.preventDefault();
  el.focus();

  const box = el.getBoundingClientRect();
  const content = inner.value?.getBoundingClientRect();
  if (!content) return;
  drag = {
    origin: { x: e.clientX - content.left, y: e.clientY - content.top },
    client: { x: e.clientX, y: e.clientY },
    base: e.ctrlKey || e.metaKey || e.shiftKey ? new Set(props.selectedIds) : null,
    contentLeft: content.left, contentTop: content.top, scrollTop: el.scrollTop,
    containerTop: box.top, containerBottom: box.bottom,
    edge: 0,
  };
  pointerId = e.pointerId;
  sweptIds = new Set();
  el.setPointerCapture(e.pointerId);
  marquee.value = true;
  window.addEventListener("pointermove", onMarqueeMove);
  window.addEventListener("pointerup", onPointerUp);
  window.addEventListener("pointercancel", onPointerUp);
}

function onPointerUp(): void {
  const m = drag;
  window.removeEventListener("pointermove", onMarqueeMove);
  window.removeEventListener("pointerup", onPointerUp);
  window.removeEventListener("pointercancel", onPointerUp);
  stopAutoScroll();
  if (pointerId >= 0) container.value?.releasePointerCapture(pointerId);
  pointerId = -1;
  drag = null;
  sweptIds = new Set();
  marquee.value = false;
  if (!m) return;
  // A press with no drag is a click on the background: it clears the selection
  // (a modifier press leaves it alone).
  const rect = marqueeRect(m);
  const swept = rect.right - rect.left < 2 && rect.bottom - rect.top < 2;
  if (swept && !m.base) props.selectedIds.clear();
  // The keyboard continues from what the marquee swept, not from wherever the
  // cursor happened to be before it.
  if (swept) return;
  const hits = photosInRect(layout.value.rows, tops.value, rect, rowAt(tops.value, heights.value, rect.top));
  if (!hits.length) return;
  anchor.value = hits[0];
  cursor.value = hits[hits.length - 1];
}

function stopAutoScroll(): void {
  if (frame) { cancelAnimationFrame(frame); frame = 0; }
}

onBeforeUnmount(() => {
  window.removeEventListener("pointermove", onMarqueeMove);
  window.removeEventListener("pointerup", onPointerUp);
  window.removeEventListener("pointercancel", onPointerUp);
  stopAutoScroll();
});

// ── Keyboard ──

// Where the keyboard is: the cursor, else the photo the stage is on, else the
// first cell.
function keyboardOrigin(): number {
  if (cursor.value >= 0) return cursor.value;
  return Math.max(0, props.activeId ? indexOfId(props.activeId) : 0);
}

// Move the keyboard to `i`, extending the run from the anchor when the shift is
// held (with ctrl/cmd there, it adds to what was already selected rather than
// replacing it).
function moveTo(i: number, modifiers: { shift: boolean; additive: boolean }): void {
  const to = Math.min(ordered.value.length - 1, Math.max(0, i));
  if (modifiers.shift) extendTo(to, modifiers.additive);
  else selectOnly(to);
  scrollToRow(layout.value.rowOfPhoto[to]);
}

function onKeyDown(e: KeyboardEvent): void {
  const n = ordered.value.length;
  if (!n) return;
  if (e.key === "Enter") {
    const id = cursor.value >= 0 ? ordered.value[cursor.value]?.id : selection()[0];
    if (id) { e.preventDefault(); emit("open", id); }
    return;
  }
  if (e.key === "Delete" || e.key === "Backspace") {
    if (props.selectedIds.size) { e.preventDefault(); emit("remove", selection()); }
    return;
  }
  if (e.key === "F2") {
    const only = selection();
    if (only.length === 1) { e.preventDefault(); renamingId.value = only[0]; }
    return;
  }
  if (e.key === "Escape") {
    if (props.selectedIds.size) { e.preventDefault(); props.selectedIds.clear(); }
    return;
  }
  if (e.key === "a" && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    for (const p of ordered.value) props.selectedIds.add(p.id);
    return;
  }
  if (e.key === "i" && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    invertSelection();
    return;
  }
  const perRow = Math.max(1, lanes.value);
  const perScreen = Math.max(1, Math.floor(viewport.value / (GRID_CELL.h + GRID_CELL.gap))) * perRow;
  const step = {
    ArrowLeft: -1, ArrowRight: 1, ArrowUp: -perRow, ArrowDown: perRow,
    PageUp: -perScreen, PageDown: perScreen,
  }[e.key];
  const modifiers = { shift: e.shiftKey, additive: e.ctrlKey || e.metaKey };
  if (step !== undefined) {
    e.preventDefault();
    // The first press picks up the cell the stage is on; after that the cursor
    // walks.
    const from = keyboardOrigin();
    moveTo(cursor.value >= 0 ? from + step : from, modifiers);
    return;
  }
  if (e.key === "Home" || e.key === "End") {
    e.preventDefault();
    const from = cursor.value >= 0 ? cursor.value : 0;
    const rowStart = from - (from % perRow);
    if (e.key === "Home") moveTo(rowStart, modifiers);
    else moveTo(cursor.value >= 0 ? rowStart + perRow - 1 : n - 1, modifiers);
  }
}

// ── Drag out (to a folder in the tree) ──

// A drag from an unselected cell drags that cell alone; from a selected one,
// the whole selection. The drag image is the cell itself, badged with the
// count when it stands for more.
function onDragStart(e: DragEvent, i: number): void {
  const id = ordered.value[i].id;
  if (!props.selectedIds.has(id)) selectOnly(i);
  const ids = selection();
  if (!e.dataTransfer) return;
  e.dataTransfer.setData("application/x-llr-photos", JSON.stringify(ids));
  e.dataTransfer.effectAllowed = "move";
  if (ids.length > 1) {
    const badge = document.createElement("div");
    badge.className = "grid-drag-badge";
    badge.textContent = t("grid.dragCount", { n: ids.length });
    document.body.appendChild(badge);
    e.dataTransfer.setDragImage(badge, 12, 12);
    requestAnimationFrame(() => badge.remove());
  }
}

// ── Rename in place ──

const renamingId = ref<string | null>(null);

function commitRename(photo: Photo, name: string): void {
  renamingId.value = null;
  if (name !== photo.name) emit("rename", photo.id, name);
}

// ── Context menu ──

const menu = ref<{ x: number; y: number; items: MenuItem[] } | null>(null);

const selectionItems = (): MenuItem[] => [
  { id: "selectAll", label: t("grid.selectAll") },
  { id: "invert", label: t("grid.invertSelection") },
  { id: "clear", label: t("grid.clearSelection"), disabled: !props.selectedIds.size },
];

// The menu carries the id of the photo it was opened on for "open", so the
// action cannot be confused by the selection changing under it.
function cellMenu(photo: Photo): MenuItem[] {
  const ids = selection();
  const none = !ids.length;
  return [
    { id: `open:${photo.id}`, label: t("grid.open") },
    { id: "rename", label: t("grid.rename"), disabled: ids.length !== 1 },
    { id: "-" },
    {
      id: "move", label: t("grid.moveTo"), disabled: none || !props.moveTargets.length,
      children: props.moveTargets.map(f => ({ id: `move:${f.id}`, label: f.label })),
    },
    { id: "-" },
    ...selectionItems(),
    { id: "-" },
    { id: "remove", label: t("grid.remove"), danger: true, disabled: none },
  ];
}

function emptyMenu(): MenuItem[] {
  return [
    ...selectionItems(),
    { id: "-" },
    { id: "newFolder", label: t("tree.newFolder") },
    { id: "importFiles", label: t("tree.importFiles") },
  ];
}

function onContextMenu(e: MouseEvent): void {
  const cell = (e.target as HTMLElement).closest<HTMLElement>(".grid-cell");
  const photo = cell?.dataset.index === undefined ? null : ordered.value[Number(cell.dataset.index)];
  if (!photo || !cell) {
    menu.value = { x: e.clientX, y: e.clientY, items: emptyMenu() };
    return;
  }
  // Right-clicking outside the selection acts on what was clicked, as in a
  // file manager; inside it, the menu keeps the whole selection.
  if (!props.selectedIds.has(photo.id)) selectOnly(Number(cell.dataset.index));
  menu.value = { x: e.clientX, y: e.clientY, items: cellMenu(photo) };
}

function onMenuSelect(id: string): void {
  if (id.startsWith("open:")) return emit("open", id.slice(5));
  if (id.startsWith("move:")) return emit("move", selection(), Number(id.slice(5)));
  switch (id) {
    case "rename": {
      const only = selection();
      if (only.length === 1) renamingId.value = only[0];
      return;
    }
    case "selectAll":
      for (const p of ordered.value) props.selectedIds.add(p.id);
      return;
    case "invert": return invertSelection();
    case "clear": return props.selectedIds.clear();
    case "remove": return emit("remove", selection());
    case "newFolder": return emit("newFolder");
    case "importFiles": return emit("importFiles");
    default:
  }
}

function onMenuClose(): void {
  menu.value = null;
  container.value?.focus(); // the menu had the keyboard; the grid wants it back
}

// Reserves the thumbnail's box before it loads. Only the camera's thumbnail
// has the source's proportions; an edited preview may be cropped to any.
function aspectStyle(p: Photo): Record<string, string> | undefined {
  return !p.previewUrl && p.width && p.height ? { aspectRatio: `${p.width} / ${p.height}` } : undefined;
}
</script>

<template>
  <div class="photo-grid" ref="container" tabindex="0" @keydown="onKeyDown" @pointerdown="onPointerDown"
    @contextmenu.prevent="onContextMenu">
    <div v-if="!sections.length || !ordered.length" class="grid-empty">
      <span>{{ loading ? t('grid.loading') : t('grid.empty') }}</span>
    </div>
    <div v-else class="grid-inner" ref="inner" :style="{ height: totalSize + 'px' }">
      <template v-for="{ row, top } in visibleRows" :key="row.key">
        <div v-if="row.kind === 'header'" class="grid-header" :style="{ top: top + 'px' }">
          <span>{{ row.label ?? t('library.unknown') }}</span>
          <span class="grid-header-count">{{ row.count }}</span>
        </div>
        <div v-else class="grid-row" :style="{ top: top + 'px', height: row.height + 'px' }">
          <div v-for="(photo, lane) in rowPhotos(row)" :key="photo.id"
            class="grid-cell" :data-index="row.first + lane"
            :style="{ left: cellLeft(lane) + 'px', width: GRID_CELL.w + 'px', height: row.height + 'px' }"
            :class="{ 'is-selected': selectedIds.has(photo.id), 'is-active': photo.id === activeId, 'is-invalid': isInvalid(photo.id) }"
            :title="`${photo.name} · ${formatBytes(photo.size)}`"
            draggable="true"
            @click="onCellClick($event, row.first + lane)"
            @dblclick="emit('open', photo.id)"
            @dragstart="onDragStart($event, row.first + lane)">
            <div class="grid-thumb">
              <img :src="thumbSrc(photo)" :alt="photo.name" :style="aspectStyle(photo)"
                draggable="false" decoding="async" />
            </div>
            <InlineEdit v-if="renamingId === photo.id" class="grid-rename" :value="photo.name" select="stem"
              @click.stop @dblclick.stop @pointerdown.stop
              @commit="(name) => commitRename(photo, name)" @cancel="renamingId = null" />
            <span v-else class="grid-name">{{ photo.name }}</span>
          </div>
        </div>
      </template>
      <div v-show="marquee" ref="marqueeEl" class="grid-marquee" />
    </div>
    <ContextMenu :menu="menu" @select="onMenuSelect" @close="onMenuClose" />
  </div>
</template>
