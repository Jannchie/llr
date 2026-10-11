<script lang="ts">
import {
  IconAlertTriangle, IconChartColumn, IconChat, IconCornerDownRight, IconEye, IconInfo, IconLayoutColumns, IconLightbulb,
  IconList, IconLoader, IconSliders, IconStop, IconWrench,
} from "@jannchie/icons";

// The transcript's row icons by role, so callers name what a row is rather
// than which glyph draws it.
const ICONS = {
  bulb: IconLightbulb,
  loader: IconLoader,
  alert: IconAlertTriangle,
  stop: IconStop,
  info: IconInfo,
  eye: IconEye,
  list: IconList,
  adjust: IconSliders,
  columns: IconLayoutColumns,
  chart: IconChartColumn,
  tool: IconWrench,
  steer: IconCornerDownRight,
  chat: IconChat,
} as const;
export type ChatIconName = keyof typeof ICONS;
</script>

<script setup lang="ts">
import { JIcon } from "@jannchie/icons-vue";

defineProps<{ name: ChatIconName }>();
</script>

<template>
  <JIcon :icon="ICONS[name]" />
</template>
