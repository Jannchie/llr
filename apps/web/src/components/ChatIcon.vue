<script lang="ts">
// The transcript's row icons, inlined as Tabler-style outline paths so the
// chat needs no icon font. One path string per name; subpaths are joined.
const PATHS = {
  bulb: "M3 12h1m8-9v1m8 8h1M5.6 5.6l.7.7m12.1-.7l-.7.7M9 16a5 5 0 1 1 6 0a3.5 3.5 0 0 0-1 3a2 2 0 0 1-4 0a3.5 3.5 0 0 0-1-3M9.7 17h4.6",
  loader: "M12 3a9 9 0 1 0 9 9",
  alert: "M12 9v4m0 4h.01M10.4 3.6L2.3 17.1a1.9 1.9 0 0 0 1.6 2.9h16.2a1.9 1.9 0 0 0 1.6-2.9L13.6 3.6a1.9 1.9 0 0 0-3.2 0z",
  stop: "M5 7a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2z",
  info: "M3 12a9 9 0 1 0 18 0a9 9 0 0 0-18 0M12 9h.01M11 12h1v4h1",
  eye: "M10 12a2 2 0 1 0 4 0a2 2 0 0 0-4 0M21 12c-2.4 4-5.4 6-9 6s-6.6-2-9-6c2.4-4 5.4-6 9-6s6.6 2 9 6",
  list: "M13 5h8M13 9h5M13 15h8M13 19h5M3 5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1zM3 15a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z",
  adjust: "M12 6a2 2 0 1 0 4 0a2 2 0 1 0-4 0M4 6h8M16 6h4M6 12a2 2 0 1 0 4 0a2 2 0 1 0-4 0M4 12h2M10 12h10M15 18a2 2 0 1 0 4 0a2 2 0 1 0-4 0M4 18h11M19 18h1",
  columns: "M3 5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM12 3v18",
  chart: "M3 13a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1zM15 9a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1h-4a1 1 0 0 1-1-1zM9 5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4a1 1 0 0 1-1-1zM4 20h14",
  tool: "M7 10h3V7L6.5 3.5a6 6 0 0 1 8 8l6 6a2 2 0 0 1-3 3l-6-6a6 6 0 0 1-8-8z",
  steer: "M6 5v6a3 3 0 0 0 3 3h10M15 10l4 4l-4 4",
  chat: "M3 20l1.3-3.9C2 12.7 2.9 8.2 6.4 5.7s8.6-2.3 11.8.5s3.7 7.3 1 10.5s-7.6 4.2-11.6 2.3L3 20",
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
