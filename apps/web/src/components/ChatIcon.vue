<script lang="ts">
// The transcript's row icons, inlined as Tabler-style outline paths so the
// chat needs no icon font. One path string per name; subpaths are joined.
const PATHS = {
  bulb: "M3 12h1m8-9v1m8 8h1M5.6 5.6l.7.7m12.1-.7l-.7.7M9 16a5 5 0 1 1 6 0a3.5 3.5 0 0 0-1 3a2 2 0 0 1-4 0a3.5 3.5 0 0 0-1-3M9.7 17h4.6",
  loader: "M12 3a9 9 0 1 0 9 9",
} as const;
export type ChatIconName = keyof typeof PATHS;
</script>

<script setup lang="ts">
defineProps<{ name: ChatIconName }>();
</script>

<template>
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path :d="PATHS[name]" />
  </svg>
</template>
