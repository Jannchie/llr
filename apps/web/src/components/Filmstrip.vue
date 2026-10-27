<script setup lang="ts">
import { computed, ref, toRef, watch } from "vue";
import { formatBytes, type Photo } from "../ui";
import { t } from "../i18n";
import { firstItemAfter, useVirtualStrip } from "../composables/useVirtualStrip";
import { buildFilmLayout, FILM_CELL } from "../filmLayout";
import type { Section } from "../librarySort";
import { IconPlus, IconX } from "@jannchie/icons";
import { JIcon } from "@jannchie/icons-vue";

// The library strip along the bottom: the open folder's photos, one row,
// windowed — a folder of thousands mounts a screen's worth of cells. It shows
// the same sorted, grouped sections as the library grid, each group opening
// with a narrow divider. A separate component so slider drags and crop edits
// in App don't re-diff it.
const props = defineProps<{
  /** The folder's photos, cut into groups (one unlabelled section when off). */
  sections: Section[];
  grouped: boolean;
  activeId: string | null;
  rendering: boolean;
  thumbSrc: (p: Photo) => string;
  isInvalid: (id: string) => boolean;
}>();

const emit = defineEmits<{
  (e: "select", id: string): void;
  (e: "remove", id: string): void;
  (e: "import"): void;
  /** The section at the strip's left edge changed (-1 when not grouped). */
  (e: "section", index: number): void;
}>();

const track = ref<HTMLElement | null>(null);
const layout = computed(() => buildFilmLayout(props.sections, props.grouped));
const items = computed(() => layout.value.items);
const { range, scrollPos, viewport, scrollToIndex } = useVirtualStrip({ container: track, items, overscan: 3 });

const visible = computed(() => items.value.slice(range.value.start, range.value.end));

function itemStyle(item: { x: number; w: number }): Record<string, string> {
  return { position: "absolute", top: "0", left: `${item.x}px`, width: `${item.w}px`, height: `${FILM_CELL.h}px` };
}

// Keep the active cell in view when the selection moves (keyboard, import,
// removal) — but not on every list change, so a scroll the user made stays.
watch(toRef(props, "activeId"), (id) => {
  if (!id) return;
  const i = layout.value.itemOfPhoto.get(id);
  if (i !== undefined) scrollToIndex(i);
}, { immediate: true });

// ── Sections (the sidebar's group navigator, as in the library grid) ──

// Item index of each section's divider, in section order; empty when not grouped.
const headerItems = computed(() => {
  const out: number[] = [];
  items.value.forEach((it, i) => { if (it.kind === "header") out.push(i); });
  return out;
});

// The section picked in the navigator, while its divider is still in view: the
// strip cannot scroll a group near the end of the folder to its left edge.
const jumped = ref<number | null>(null);
// Where that jump left the strip; scrolling away from it lets go of the pick.
let jumpedPos = 0;
watch(scrollPos, (pos) => {
  if (jumped.value !== null && Math.abs(pos - jumpedPos) > 1) jumped.value = null;
});

// The section whose cells start the strip: the last divider at or left of it.
const currentSection = computed(() => {
  const hs = headerItems.value;
  if (!hs.length) return -1;
  const all = items.value;
  const j = jumped.value;
  if (j !== null && j < hs.length) {
    const x = all[hs[j]].x;
    if (x >= scrollPos.value - 1 && x < scrollPos.value + viewport.value) return j;
  }
  const first = firstItemAfter(all, scrollPos.value + 1);
  let si = 0;
  while (si + 1 < hs.length && hs[si + 1] <= first) si++;
  return si;
});
watch(currentSection, si => emit("section", si), { immediate: true });
watch(() => props.sections, () => { jumped.value = null; });

/** Scroll so section `si` opens at the strip's left edge. */
function scrollToSection(si: number): void {
  const item = items.value[headerItems.value[si]];
  const el = track.value;
  if (!el || !item) return;
  el.scrollLeft = item.x;
  // Read back: the browser clamps a group near the end short of item.x.
  jumpedPos = el.scrollLeft;
  jumped.value = si;
}

defineExpose({ scrollToSection });

// A plain mouse wheel only has a vertical axis; on a one-row strip that is the
// scroll. Trackpads that already send deltaX are left to native scrolling.
function onWheel(e: WheelEvent): void {
  if (e.deltaX !== 0) return;
  e.preventDefault();
  (e.currentTarget as HTMLElement).scrollLeft += e.deltaY;
}

function onDragStart(e: DragEvent, id: string): void {
  e.dataTransfer?.setData("application/x-llr-photos", JSON.stringify([id]));
  if (e.dataTransfer) e.dataTransfer.effectAllowed = "move";
}
</script>

<template>
  <footer class="filmstrip">
    <button class="filmstrip-import" type="button" @click="emit('import')">
      <JIcon :icon="IconPlus" />
      {{ t('film.import') }}
    </button>
    <div class="filmstrip-track" ref="track" @wheel="onWheel">
      <div class="filmstrip-inner" :style="{ width: layout.totalSize + 'px', height: FILM_CELL.h + 'px' }">
        <template v-for="item in visible" :key="item.key">
          <div v-if="item.kind === 'header'" class="film-group" :style="itemStyle(item)"
            :title="`${item.label ?? t('library.unknown')} · ${item.count}`">
            <span class="film-group-label">{{ item.label ?? t('library.unknown') }}</span>
          </div>
          <div v-else
            class="film-cell" :style="itemStyle(item)"
            :class="{ 'is-active': item.photo.id === activeId, 'is-invalid': isInvalid(item.photo.id) }"
            draggable="true" @dragstart="onDragStart($event, item.photo.id)">
            <button type="button" class="film-cell-main"
              :title="`${item.photo.name} · ${formatBytes(item.photo.size)}`"
              @click="emit('select', item.photo.id)">
              <div class="film-thumb">
                <!-- draggable=false: a native image drag off a cell reads to the
                     shell as an incoming file and pops the import overlay. -->
                <img :src="thumbSrc(item.photo)" :alt="item.photo.name" draggable="false" decoding="async" />
                <span v-if="item.photo.id === activeId && rendering" class="thumb-loading" aria-hidden="true">
                  <span class="spinner" />
                </span>
                <span v-if="isInvalid(item.photo.id)" class="film-badge" :title="t('film.staleHint')">{{ t('film.stale') }}</span>
              </div>
              <span class="film-name">{{ item.photo.name }}</span>
            </button>
            <button type="button" class="film-remove" :title="t('film.remove')"
              :aria-label="t('aria.removeFromLibrary')" @click="emit('remove', item.photo.id)">
              <JIcon :icon="IconX" weight="bold" />
            </button>
          </div>
        </template>
      </div>
    </div>
  </footer>
</template>
