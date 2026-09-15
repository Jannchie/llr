<script setup lang="ts">
import { computed, ref, toRef, watch } from "vue";
import { formatBytes, type Photo } from "../ui";
import { t } from "../i18n";
import { useVirtualGrid } from "../composables/useVirtualGrid";

// The library view: the open folder as a grid of thumbnails, windowed so a
// folder of thousands mounts a screen's worth of cells. Selection is a
// reactive Set the catalog owns; the grid edits it in place (click, ctrl,
// shift, arrows) and starts drags carrying the selected ids for the folder
// tree to drop into.
const props = defineProps<{
  photos: Photo[];
  activeId: string | null;
  selectedIds: Set<string>;
  loading: boolean;
  thumbSrc: (p: Photo) => string;
  isInvalid: (id: string) => boolean;
}>();

const emit = defineEmits<{
  (e: "open", id: string): void;
  (e: "remove", ids: string[]): void;
}>();

// Fixed cell geometry (style.css .grid-cell); the virtualiser positions by
// arithmetic, so these must match the CSS.
const GRID_CELL = { w: 176, h: 140, gap: 8 };

const container = ref<HTMLElement | null>(null);
const count = computed(() => props.photos.length);
const { lanes, range, totalSize, itemStyle, scrollToIndex } = useVirtualGrid({
  container, count, cell: GRID_CELL, axis: "y", overscan: 2,
});

const visible = computed(() => {
  const { start, end } = range.value;
  const out: { photo: Photo; index: number }[] = [];
  for (let i = start; i < end; i++) out.push({ photo: props.photos[i], index: i });
  return out;
});

// The cell keyboard navigation moves from, and the fixed end of a shift-range.
const anchor = ref(-1);
watch(toRef(props, "photos"), () => { anchor.value = -1; });

function indexOf(id: string): number { return props.photos.findIndex(p => p.id === id); }

function selectOnly(i: number): void {
  props.selectedIds.clear();
  const p = props.photos[i];
  if (p) props.selectedIds.add(p.id);
  anchor.value = i;
}

function selectRange(from: number, to: number): void {
  const [a, b] = from <= to ? [from, to] : [to, from];
  for (let i = a; i <= b; i++) props.selectedIds.add(props.photos[i].id);
}

function onCellClick(e: MouseEvent, i: number): void {
  const id = props.photos[i].id;
  if (e.shiftKey && anchor.value >= 0) {
    if (!e.ctrlKey && !e.metaKey) props.selectedIds.clear();
    selectRange(anchor.value, i);
  } else if (e.ctrlKey || e.metaKey) {
    if (props.selectedIds.has(id)) props.selectedIds.delete(id); else props.selectedIds.add(id);
    anchor.value = i;
  } else {
    selectOnly(i);
  }
}

function onKeyDown(e: KeyboardEvent): void {
  const n = props.photos.length;
  if (!n) return;
  if (e.key === "Enter") {
    const id = anchor.value >= 0 ? props.photos[anchor.value].id : [...props.selectedIds][0];
    if (id) { e.preventDefault(); emit("open", id); }
    return;
  }
  if (e.key === "Delete" || e.key === "Backspace") {
    if (props.selectedIds.size) { e.preventDefault(); emit("remove", [...props.selectedIds]); }
    return;
  }
  if (e.key === "a" && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    for (const p of props.photos) props.selectedIds.add(p.id);
    return;
  }
  const step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -lanes.value, ArrowDown: lanes.value }[e.key];
  if (step === undefined) return;
  e.preventDefault();
  const from = anchor.value >= 0 ? anchor.value : (props.activeId ? Math.max(0, indexOf(props.activeId)) : 0);
  const to = Math.min(n - 1, Math.max(0, anchor.value >= 0 ? from + step : from));
  if (e.shiftKey && anchor.value >= 0) selectRange(anchor.value, to); // extend, anchor stays
  else selectOnly(to);
  scrollToIndex(to);
}

// A drag from an unselected cell drags that cell alone; from a selected one,
// the whole selection. The drag image is the cell itself, badged with the
// count when it stands for more.
function onDragStart(e: DragEvent, i: number): void {
  const id = props.photos[i].id;
  if (!props.selectedIds.has(id)) selectOnly(i);
  const ids = [...props.selectedIds];
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

function aspectStyle(p: Photo): Record<string, string> | undefined {
  return p.width && p.height ? { aspectRatio: `${p.width} / ${p.height}` } : undefined;
}
</script>

<template>
  <div class="photo-grid" ref="container" tabindex="0" @keydown="onKeyDown"
    @click.self="selectedIds.clear()">
    <div v-if="!photos.length" class="grid-empty">
      <span>{{ loading ? t('grid.loading') : t('grid.empty') }}</span>
    </div>
    <div v-else class="grid-inner" :style="{ height: totalSize + 'px' }" @click.self="selectedIds.clear()">
      <div v-for="{ photo, index } in visible" :key="photo.id"
        class="grid-cell" :style="itemStyle(index)"
        :class="{ 'is-selected': selectedIds.has(photo.id), 'is-active': photo.id === activeId, 'is-invalid': isInvalid(photo.id) }"
        :title="`${photo.name} · ${formatBytes(photo.size)}`"
        draggable="true"
        @click="onCellClick($event, index)"
        @dblclick="emit('open', photo.id)"
        @dragstart="onDragStart($event, index)">
        <div class="grid-thumb">
          <img :src="thumbSrc(photo)" :alt="photo.name" :style="aspectStyle(photo)"
            draggable="false" decoding="async" />
        </div>
        <span class="grid-name">{{ photo.name }}</span>
      </div>
    </div>
  </div>
</template>
