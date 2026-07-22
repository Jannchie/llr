<script setup lang="ts">
import { formatBytes, type Source } from "../ui";
import { t } from "../i18n";

// The library strip along the bottom. A separate component so slider drags and
// crop edits in App don't re-diff one cell per imported image.
defineProps<{
  sources: Source[];
  activeId: string | null;
  rendering: boolean;
  thumbSrc: (s: Source) => string;
}>();

defineEmits<{
  (e: "select", id: string): void;
  (e: "remove", id: string): void;
  (e: "import"): void;
}>();
</script>

<template>
  <footer class="filmstrip">
    <button class="filmstrip-import" type="button" @click="$emit('import')">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">
        <path d="M12 5v14M5 12h14" />
      </svg>
      {{ t('film.import') }}
    </button>
    <div class="filmstrip-track">
      <div v-for="source in sources" :key="source.id"
        class="film-cell" :class="{ 'is-active': source.id === activeId, 'is-invalid': source.invalid }">
        <button type="button" class="film-cell-main"
          :title="`${source.name} · ${formatBytes(source.size)}`"
          @click="$emit('select', source.id)">
          <div class="film-thumb">
            <!-- draggable=false: a native image drag off a cell reads to the
                 shell as an incoming file and pops the import overlay. -->
            <img v-if="thumbSrc(source)" :src="thumbSrc(source)" :alt="source.name" draggable="false" />
            <div v-else class="thumb-skeleton" aria-hidden="true" />
            <span v-if="source.id === activeId && rendering" class="thumb-loading" aria-hidden="true">
              <span class="spinner" />
            </span>
            <span v-if="source.invalid" class="film-badge" :title="t('film.staleHint')">{{ t('film.stale') }}</span>
          </div>
          <span class="film-name">{{ source.name }}</span>
        </button>
        <button type="button" class="film-remove" :title="t('film.remove')"
          :aria-label="t('aria.removeFromLibrary')" @click="$emit('remove', source.id)">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true">
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </button>
      </div>
    </div>
  </footer>
</template>
