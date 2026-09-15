<script setup lang="ts">
import { computed, ref, toRef, watch } from "vue";
import { formatBytes, type Photo } from "../ui";
import { t } from "../i18n";
import { useVirtualGrid } from "../composables/useVirtualGrid";

// The library strip along the bottom: the open folder's photos, one row,
// windowed — a folder of thousands mounts a screen's worth of cells. A
// separate component so slider drags and crop edits in App don't re-diff it.
const props = defineProps<{
  photos: Photo[];
  activeId: string | null;
  rendering: boolean;
  thumbSrc: (p: Photo) => string;
  isInvalid: (id: string) => boolean;
}>();

const emit = defineEmits<{
  (e: "select", id: string): void;
  (e: "remove", id: string): void;
  (e: "import"): void;
}>();

// Cell geometry is fixed (style.css .film-cell): the virtualiser positions by
// arithmetic, so these must match the CSS.
const FILM_CELL = { w: 108, h: 96, gap: 4 };

const track = ref<HTMLElement | null>(null);
const count = computed(() => props.photos.length);
const { range, totalSize, itemStyle, scrollToIndex } = useVirtualGrid({
  container: track, count, cell: FILM_CELL, axis: "x", overscan: 3,
});

const visible = computed(() => {
  const { start, end } = range.value;
  const out: { photo: Photo; index: number }[] = [];
  for (let i = start; i < end; i++) out.push({ photo: props.photos[i], index: i });
  return out;
});

// Keep the active cell in view when the selection moves (keyboard, import,
// removal) — but not on every list change, so a scroll the user made stays.
watch(toRef(props, "activeId"), (id) => {
  if (!id) return;
  const i = props.photos.findIndex(p => p.id === id);
  if (i >= 0) scrollToIndex(i);
}, { immediate: true });

function onDragStart(e: DragEvent, id: string): void {
  e.dataTransfer?.setData("application/x-llr-photos", JSON.stringify([id]));
  if (e.dataTransfer) e.dataTransfer.effectAllowed = "move";
}
</script>

<template>
  <footer class="filmstrip">
    <button class="filmstrip-import" type="button" @click="emit('import')">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">
        <path d="M12 5v14M5 12h14" />
      </svg>
      {{ t('film.import') }}
    </button>
    <div class="filmstrip-track" ref="track">
      <div class="filmstrip-inner" :style="{ width: totalSize + 'px', height: FILM_CELL.h + 'px' }">
        <div v-for="{ photo, index } in visible" :key="photo.id"
          class="film-cell" :style="itemStyle(index)"
          :class="{ 'is-active': photo.id === activeId, 'is-invalid': isInvalid(photo.id) }"
          draggable="true" @dragstart="onDragStart($event, photo.id)">
          <button type="button" class="film-cell-main"
            :title="`${photo.name} · ${formatBytes(photo.size)}`"
            @click="emit('select', photo.id)">
            <div class="film-thumb">
              <!-- draggable=false: a native image drag off a cell reads to the
                   shell as an incoming file and pops the import overlay. -->
              <img :src="thumbSrc(photo)" :alt="photo.name" draggable="false" decoding="async" />
              <span v-if="photo.id === activeId && rendering" class="thumb-loading" aria-hidden="true">
                <span class="spinner" />
              </span>
              <span v-if="isInvalid(photo.id)" class="film-badge" :title="t('film.staleHint')">{{ t('film.stale') }}</span>
            </div>
            <span class="film-name">{{ photo.name }}</span>
          </button>
          <button type="button" class="film-remove" :title="t('film.remove')"
            :aria-label="t('aria.removeFromLibrary')" @click="emit('remove', photo.id)">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>
      </div>
    </div>
  </footer>
</template>
