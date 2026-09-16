<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { t } from "../i18n";
import { modelKey, type ModelSpec, type ProviderCatalog } from "../composables/useAssistant";
import ComboField, { type ComboOption } from "./ComboField.vue";

// The assistant's model menu, edited in place. A native <dialog>: showModal()
// gives the backdrop, focus trap and Escape for free. Both fields are pickers
// that float their list over the dialog — what the API's registry holds is what
// is worth offering, and the text stays editable so a model released after this
// build can still be typed in.
const props = defineProps<{
  open: boolean;
  models: ModelSpec[];
  /** The API's registry; null while it is still on its way. */
  catalog: ProviderCatalog[] | null;
}>();
const emit = defineEmits<{ (e: "update:models", v: ModelSpec[]): void; (e: "close"): void }>();

const dialog = ref<HTMLDialogElement | null>(null);
watch(() => props.open, open => {
  if (open) dialog.value?.showModal(); else dialog.value?.close();
});

const draft = ref<ModelSpec>({ provider: "openai", id: "" });

// Without a catalog there is no list to judge a provider against, so the field
// keeps whatever is typed instead of discarding it on blur.
const providersKnown = computed(() => props.catalog !== null);
const providerOptions = computed<ComboOption[]>(() => (props.catalog ?? []).map(p => ({
  value: p.id, label: p.id, hint: p.hasKey ? "" : t("models.noKeyShort"),
})));

function idOptions(provider: string): ComboOption[] {
  const models = props.catalog?.find(p => p.id === provider)?.models ?? [];
  return models.map(m => ({ value: m.id, label: m.id, hint: m.name === m.id ? "" : m.name }));
}

// Unknown counts as fine: the catalog has not arrived, or the id is one the
// registry has not heard of — the warning is for a provider whose key is known
// to be missing.
function hasKey(provider: string): boolean {
  if (props.catalog === null) return true;
  return !!props.catalog.find(p => p.id === provider)?.hasKey;
}

function update(i: number, patch: Partial<ModelSpec>): void {
  emit("update:models", props.models.map((m, j) => j === i ? { ...m, ...patch } : m));
}
function remove(i: number): void {
  emit("update:models", props.models.filter((_, j) => j !== i));
}

// Switching provider usually invalidates the id — a leftover "gpt-5.6-sol" on
// Anthropic would only fail at prompt time — so the field empties for the new
// provider's list to fill.
function keepsId(provider: string, id: string): boolean {
  const served = props.catalog?.find(p => p.id === provider)?.models ?? [];
  return !served.length || served.some(m => m.id === id);
}
function setProvider(i: number, provider: string): void {
  const id = props.models[i].id;
  update(i, { provider, ...(keepsId(provider, id) ? {} : { id: "" }) });
}
function setDraftProvider(provider: string): void {
  draft.value = { provider, id: keepsId(provider, draft.value.id) ? draft.value.id : "" };
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
        <ComboField class="model-provider" :model-value="m.provider" :options="providerOptions"
          :allow-custom="!providersKnown" :placeholder="t('models.provider')" :aria-label="t('models.provider')"
          @update:model-value="v => setProvider(i, v)" />
        <ComboField class="model-id" :model-value="m.id" :options="idOptions(m.provider)" :placeholder="t('models.id')"
          :aria-label="t('models.id')" @update:model-value="v => update(i, { id: v })" />
        <span class="model-key" :title="t('models.noKey', { provider: m.provider })" v-if="!hasKey(m.provider)">⚠</span>
        <button class="ghost model-remove" type="button" :aria-label="t('common.remove')" @click="remove(i)">×</button>
      </div>
      <form class="model-row model-add" @submit.prevent="add">
        <ComboField class="model-provider" :model-value="draft.provider" :options="providerOptions"
          :allow-custom="!providersKnown" :placeholder="t('models.provider')" :aria-label="t('models.provider')"
          @update:model-value="setDraftProvider" />
        <ComboField class="model-id" :model-value="draft.id" :options="idOptions(draft.provider)"
          :placeholder="t('models.id')" :aria-label="t('models.id')" @update:model-value="v => draft.id = v" />
        <button class="ghost" type="submit" :disabled="!draft.id.trim() || !draft.provider.trim()">{{ t('common.add') }}</button>
      </form>
    </div>
  </dialog>
</template>
