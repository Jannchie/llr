<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, useId, watch } from "vue";
import { t } from "../i18n";
import { useFixedPlacement } from "../composables/useFixedPlacement";

// A text field that suggests: type freely, or pick from the list that floats
// under it. It is the model dialog's field — the API's registry supplies the
// provider's usual ids, but a model released after this build still has to be
// typeable, so unlike SelectMenu the text *is* the value rather than a label
// for one.
//
// The popup is drawn here rather than teleported to <body> like SelectMenu's:
// the dialog is a modal <dialog>, whose top layer paints over everything in the
// page's own stacking order, so a body-level popup would end up behind it. A
// fixed-position child of the dialog floats above it instead, and the dialog's
// own scroll box does not clip it.

export type ComboOption = {
  value: string;
  label: string;
  /** Dim second half of the row: a model's display name, "no key". */
  hint?: string;
};

const props = withDefaults(defineProps<{
  modelValue: string;
  options: readonly ComboOption[];
  /** Keep text that matches no option — a model id newer than the registry. */
  allowCustom?: boolean;
  placeholder?: string;
  ariaLabel?: string;
  title?: string;
  disabled?: boolean;
}>(), { allowCustom: true, placeholder: "", disabled: false });

const emit = defineEmits<{ (e: "update:modelValue", v: string): void }>();

// Ids have to be stable and unique: aria-activedescendant points at one of them
// and both fields of every row are in the document at once.
const uid = useId();
const listId = `${uid}-list`;
const optionId = (i: number) => `${uid}-opt${i}`;

const inputEl = ref<HTMLInputElement | null>(null);
const listEl = ref<HTMLDivElement | null>(null);
const open = ref(false);
const activeIndex = ref(-1);
// What the field shows: the value, or what is being typed over it.
const text = ref(props.modelValue);
// What the list is filtered by; empty until the first keystroke, so a click in
// the field offers everything.
const query = ref("");
const { placed, style, show, hide } = useFixedPlacement(inputEl, listEl);

watch(() => props.modelValue, v => { if (v !== text.value) text.value = v; });

const shown = computed(() => {
  const q = query.value.trim().toLowerCase();
  const rows = props.options.map((o, i) => ({ o, i }));
  if (!q) return rows;
  return rows.filter(({ o }) => o.label.toLowerCase().includes(q) || o.value.toLowerCase().includes(q));
});
// Nothing matched: either the typed text is what gets used (and the popup says
// so), or there is nothing to offer at all — a provider that does not exist.
const custom = computed(() => props.allowCustom && !!text.value.trim());
const visible = computed(() => shown.value.length > 0 || custom.value);
watch(visible, v => { if (!v) closeList(); });

const firstShown = () => shown.value[0]?.i ?? -1;
/** The row the field currently holds, so opening lands on it rather than the top. */
const selectedShown = () => shown.value.find(r => r.o.value === props.modelValue)?.i ?? firstShown();

function scrollActiveIntoView(): void {
  listEl.value?.querySelector<HTMLElement>(".select-option.is-active")?.scrollIntoView({ block: "nearest" });
}

/* ---------- open / close ---------- */

// The field holds the text as it is typed; the list follows it.
function onInput(value: string): void {
  text.value = value;
  void openList(value);
}

async function openList(withQuery: string): Promise<void> {
  if (props.disabled) return;
  query.value = withQuery;
  if (!visible.value) { closeList(); return; }
  // Typing narrows the list, so the cursor belongs on its first row; a click in
  // the field offers the whole list and starts where the value is.
  const cursor = withQuery.trim() ? firstShown() : selectedShown();
  if (open.value) { activeIndex.value = cursor; void nextTick(scrollActiveIntoView); return; }
  open.value = true;
  activeIndex.value = cursor;
  document.addEventListener("pointerdown", onDocPointerDown, true);
  await show();
  scrollActiveIntoView();
}

function closeList(): void {
  if (!open.value) return;
  open.value = false;
  activeIndex.value = -1;
  query.value = "";
  hide();
  document.removeEventListener("pointerdown", onDocPointerDown, true);
}

// The first click takes the whole value, the way a picker does, so typing over
// it searches instead of splicing into what is already there. A second click,
// once the field is in hand, places the caret as usual.
function onPointerDown(e: PointerEvent): void {
  const input = inputEl.value;
  if (!input || document.activeElement === input) return;
  e.preventDefault();
  input.focus();
  input.select();
}

function onDocPointerDown(e: PointerEvent): void {
  const target = e.target as Node | null;
  if (!target) return;
  if (inputEl.value?.contains(target) || listEl.value?.contains(target)) return;
  closeList(); // the blur that follows commits what was typed
}

// Keep the text where it is while a pick is in flight: the popup must not steal
// focus, or the field would blur and commit over the pick. The scrollbar is the
// exception, as in SelectMenu — dragging it needs the default action.
function onListPointerDown(e: PointerEvent): void {
  const list = listEl.value;
  if (list && e.clientX - list.getBoundingClientRect().left >= list.clientWidth) return;
  e.preventDefault();
}

/* ---------- what the field holds ---------- */

function emitValue(v: string): void {
  if (v !== props.modelValue) emit("update:modelValue", v);
}

/** Settle the text into a value: an option's own string, or the text itself. */
function commit(): void {
  const typed = text.value.trim();
  const match = props.options.find(o => o.value.toLowerCase() === typed.toLowerCase());
  // A provider that is not in the registry is not a provider; the field goes
  // back to what it held rather than inventing one.
  const value = match ? match.value : props.allowCustom ? typed : props.modelValue;
  text.value = value;
  emitValue(value);
}

function pick(i: number): void {
  const o = props.options[i];
  if (!o) return;
  text.value = o.value;
  closeList();
  emitValue(o.value);
}

/* ---------- keyboard ---------- */

function move(step: number): void {
  const rows = shown.value;
  if (!rows.length) return;
  const at = rows.findIndex(r => r.i === activeIndex.value);
  const next = at < 0 ? (step > 0 ? 0 : rows.length - 1) : Math.min(rows.length - 1, Math.max(0, at + step));
  activeIndex.value = rows[next].i;
  void nextTick(scrollActiveIntoView);
}

// Home/End stay with the caret — this is a text field first. Escape closes the
// list and not the dialog; the next Escape is the dialog's own.
function onKeydown(e: KeyboardEvent): void {
  if (props.disabled) return;
  const consume = () => { e.preventDefault(); e.stopPropagation(); };

  if (open.value) {
    switch (e.key) {
      case "Escape": consume(); closeList(); text.value = props.modelValue; return;
      // A highlighted row takes the key; with nothing highlighted the text is
      // what the field means, so the list closes and the key carries on to the
      // form around it ("type the id, press Enter" adds it in one go).
      case "Enter":
        if (activeIndex.value >= 0) { consume(); pick(activeIndex.value); return; }
        closeList(); commit(); return;
      case "Tab": closeList(); commit(); return; // focus moves on, as from any field
      case "ArrowDown": consume(); move(1); return;
      case "ArrowUp": consume(); move(-1); return;
      case "PageDown": consume(); move(10); return;
      case "PageUp": consume(); move(-10); return;
    }
    return;
  }
  // Closed: Enter is the surrounding form's (the add row submits on it), so it
  // commits and then lets the event through.
  if (e.key === "Enter") { commit(); return; }
  if (e.key === "ArrowDown" || e.key === "ArrowUp") { consume(); void openList(""); }
}

watch(() => props.disabled, d => { if (d) closeList(); });
onBeforeUnmount(() => closeList());
</script>

<template>
  <div class="combo-field" :class="{ 'is-open': open }">
    <input ref="inputEl" class="combo-input" type="text" role="combobox" autocomplete="off" spellcheck="false"
      :value="text" :placeholder="placeholder" :disabled="disabled" :title="title" :aria-label="ariaLabel"
      aria-haspopup="listbox" :aria-expanded="open" :aria-controls="open ? listId : undefined"
      :aria-activedescendant="open && activeIndex >= 0 ? optionId(activeIndex) : undefined"
      @input="onInput(($event.target as HTMLInputElement).value)"
      @pointerdown="onPointerDown" @focus="openList('')" @blur="closeList(); commit()" @keydown="onKeydown" />
    <svg class="combo-caret" viewBox="0 0 10 6" aria-hidden="true">
      <path d="M1 1l4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"
        stroke-linejoin="round" />
    </svg>
    <div v-if="open" :id="listId" ref="listEl" class="select-pop" :class="{ 'is-placed': placed }" :style="style"
      role="listbox" :aria-label="ariaLabel" @pointerdown="onListPointerDown">
      <div v-for="row in shown" :id="optionId(row.i)" :key="row.o.value" class="select-option"
        :class="{ 'is-active': row.i === activeIndex, 'is-selected': row.o.value === modelValue }" role="option"
        :aria-selected="row.o.value === modelValue" @pointerenter="activeIndex = row.i" @click="pick(row.i)">
        <svg class="select-check" viewBox="0 0 14 14" aria-hidden="true">
          <path d="M2 7.5l3.2 3.2L12 3.8" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"
            stroke-linejoin="round" />
        </svg>
        <span class="select-option-label">{{ row.o.label }}</span>
        <span class="select-option-hint" v-if="row.o.hint">{{ row.o.hint }}</span>
      </div>
      <!-- Nothing in the registry matches but the text is kept: say which value
           the field will hand on, so a typed id never looks like a typo. -->
      <p class="select-custom" v-if="!shown.length">{{ t('models.custom', { id: text.trim() }) }}</p>
    </div>
  </div>
</template>
