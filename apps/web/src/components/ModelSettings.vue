<script setup lang="ts">
import { ref, watch } from "vue";
import { t } from "../i18n";
import { modelKey, type ModelSpec } from "../composables/useAssistant";

// The assistant's model menu, edited in place. A native <dialog>: showModal()
// gives the backdrop, focus trap and Escape for free.
const props = defineProps<{
  open: boolean;
  models: ModelSpec[];
  /** Providers the API holds a key for; null while still loading. */
  providers: string[] | null;
}>();
const emit = defineEmits<{ (e: "update:models", v: ModelSpec[]): void; (e: "close"): void }>();

const dialog = ref<HTMLDialogElement | null>(null);
watch(() => props.open, open => {
  if (open) dialog.value?.showModal(); else dialog.value?.close();
});

const draft = ref<ModelSpec>({ provider: "openai", id: "" });

function hasKey(provider: string): boolean {
  return props.providers === null || props.providers.includes(provider);
}

function update(i: number, patch: Partial<ModelSpec>): void {
  emit("update:models", props.models.map((m, j) => j === i ? { ...m, ...patch } : m));
}
function remove(i: number): void {
  emit("update:models", props.models.filter((_, j) => j !== i));
}
function add(): void {
  const id = draft.value.id.trim();
  const provider = draft.value.provider.trim();
  if (!id || !provider || props.models.some(m => m.provider === provider && m.id === id)) return;
  emit("update:models", [...props.models, { provider, id }]);
  draft.value = { provider, id: "" };
}
</script>

<template>
  <dialog ref="dialog" class="sheet" @close="emit('close')">
    <header class="sheet-head">
      <span>{{ t('models.title') }}</span>
      <button class="ghost" type="button" @click="emit('close')">{{ t('common.done') }}</button>
    </header>
    <p class="control-note">{{ t('models.hint') }}</p>
    <div class="model-rows">
      <div v-for="(m, i) in models" :key="modelKey(m) + i" class="model-row" :class="{ 'no-key': !hasKey(m.provider) }">
        <input class="model-field" :value="m.provider" :placeholder="t('models.provider')" spellcheck="false"
          @change="update(i, { provider: ($event.target as HTMLInputElement).value.trim() })" />
        <input class="model-field model-id" :value="m.id" :placeholder="t('models.id')" spellcheck="false"
          @change="update(i, { id: ($event.target as HTMLInputElement).value.trim() })" />
        <span class="model-key" :title="t('models.noKey', { provider: m.provider })" v-if="!hasKey(m.provider)">⚠</span>
        <button class="ghost model-remove" type="button" :aria-label="t('common.remove')" @click="remove(i)">×</button>
      </div>
      <form class="model-row model-add" @submit.prevent="add">
        <input class="model-field" v-model="draft.provider" :placeholder="t('models.provider')" spellcheck="false" list="model-providers" />
        <datalist id="model-providers">
          <option v-for="p in providers ?? []" :key="p" :value="p" />
        </datalist>
        <input class="model-field model-id" v-model="draft.id" :placeholder="t('models.id')" spellcheck="false" />
        <button class="ghost" type="submit" :disabled="!draft.id.trim() || !draft.provider.trim()">{{ t('common.add') }}</button>
      </form>
    </div>
  </dialog>
</template>
