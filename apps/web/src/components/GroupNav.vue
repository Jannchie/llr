<script setup lang="ts">
import { nextTick, ref, watch } from "vue";
import { t } from "../i18n";
import type { Section } from "../librarySort";

// The library's groups, docked under the folder tree: one row per section the
// grid is showing (a day, a month, a camera…) with its photo count. A click
// scrolls the grid to that group; the group at the top of the grid is lit, so
// the list doubles as a "where am I" while scrolling.
const props = defineProps<{
  title: string;
  sections: Section[];
  current: number;
}>();

const emit = defineEmits<{
  (e: "jump", index: number): void;
}>();

const list = ref<HTMLElement | null>(null);

// Follow the grid: keep the lit row inside the list's own scroll box.
watch(() => props.current, async (i) => {
  await nextTick();
  const row = list.value?.children[i] as HTMLElement | undefined;
  row?.scrollIntoView({ block: "nearest" });
});
</script>

<template>
  <nav class="group-nav" :aria-label="title">
    <header class="group-nav-head">{{ title }}</header>
    <ol class="group-nav-list" ref="list">
      <li v-for="(section, i) in sections" :key="section.label ?? ''">
        <button type="button" class="group-nav-row" :class="{ 'is-current': i === current }"
          :aria-current="i === current ? 'true' : undefined" @click="emit('jump', i)">
          <span class="group-nav-label">{{ section.label ?? t('library.unknown') }}</span>
          <span class="group-nav-count">{{ section.photos.length }}</span>
        </button>
      </li>
    </ol>
  </nav>
</template>
