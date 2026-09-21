<script setup lang="ts" generic="T extends string | number">
import { computed, nextTick, onBeforeUnmount, ref, useId, watch } from "vue";
import { useFixedPlacement } from "../composables/useFixedPlacement";

// A listbox picker that replaces the native <select>. The native control draws
// its popup at the OS level: it ignores the app's dark palette, cannot show a
// selected-check or a two-part label, and on Windows renders white-on-white in
// a dark shell. This is the ARIA combobox pattern instead — focus never leaves
// the trigger button, and the popup is described by aria-activedescendant, so
// keyboard, screen-reader and pointer behaviour all match a real <select>:
// type-ahead, Home/End/PageUp/PageDown, no wrap-around, Tab closes and moves on.

type Option = { value: T; label: string; disabled?: boolean };

const props = withDefaults(defineProps<{
  modelValue: T;
  options: readonly Option[];
  id?: string;
  disabled?: boolean;
  title?: string;
  ariaLabel?: string;
  /** Replaces the value text in the trigger while retaining the full menu. */
  icon?: "sort" | "group";
  /** Shown when the model holds a value no option carries (e.g. mid-decode). */
  placeholder?: string;
}>(), { disabled: false, placeholder: "" });

const emit = defineEmits<{ (e: "update:modelValue", v: T): void }>();

// Ids have to be stable and unique: aria-activedescendant points at one of them
// and several pickers live in the rail at once.
const uid = useId();
const listId = `${uid}-list`;
const optionId = (i: number) => `${uid}-opt${i}`;

const open = ref(false);
const activeIndex = ref(-1);
const triggerEl = ref<HTMLButtonElement | null>(null);
const listEl = ref<HTMLDivElement | null>(null);
// Fixed coordinates, recomputed on scroll and resize while open; the popup is
// transparent until `placed`, so it never flashes at the wrong spot.
const { placed, style: popStyle, show: showPopup, hide: hidePopup } = useFixedPlacement(triggerEl, listEl);

const selectedIndex = computed(() => props.options.findIndex(o => o.value === props.modelValue));
const selectedLabel = computed(() => props.options[selectedIndex.value]?.label ?? props.placeholder);

function scrollActiveIntoView(): void {
  const el = listEl.value?.querySelector<HTMLElement>(".select-option.is-active");
  el?.scrollIntoView({ block: "nearest" });
}

/* ---------- open / close ---------- */

async function openMenu(seek: "selected" | "first" | "last" = "selected"): Promise<void> {
  if (props.disabled || !props.options.length) return;
  open.value = true;
  activeIndex.value = seek === "first" ? firstEnabled(1)
    : seek === "last" ? firstEnabled(-1)
      : selectedIndex.value >= 0 ? selectedIndex.value : firstEnabled(1);
  document.addEventListener("pointerdown", onDocPointerDown, true);
  await showPopup();
  scrollActiveIntoView();
}

function close(refocus: boolean): void {
  if (!open.value) return;
  open.value = false;
  activeIndex.value = -1;
  hidePopup();
  document.removeEventListener("pointerdown", onDocPointerDown, true);
  if (refocus) triggerEl.value?.focus();
}

function onDocPointerDown(e: PointerEvent): void {
  const target = e.target as Node | null;
  if (!target) return;
  if (triggerEl.value?.contains(target) || listEl.value?.contains(target)) return;
  close(false); // clicking elsewhere means focus belongs there, not back here
}

function onTriggerClick(): void {
  if (open.value) close(true);
  else void openMenu("selected");
}

// The trigger owns the keyboard for as long as the popup is up, so a click in
// the list must not move focus. The scrollbar is the exception: dragging it
// needs the default action, and it never lands on an option.
function onListPointerDown(e: PointerEvent): void {
  const list = listEl.value;
  if (list && e.clientX - list.getBoundingClientRect().left >= list.clientWidth) return;
  e.preventDefault();
}

/* ---------- selection ---------- */

function choose(i: number): void {
  const o = props.options[i];
  if (!o || o.disabled) return;
  if (o.value !== props.modelValue) emit("update:modelValue", o.value);
  close(true);
}

function firstEnabled(dir: 1 | -1): number {
  const n = props.options.length;
  for (let i = dir > 0 ? 0 : n - 1; i >= 0 && i < n; i += dir) {
    if (!props.options[i].disabled) return i;
  }
  return -1;
}

// Native selects do not wrap, so neither does this: at either end the step
// stops rather than jumping across the whole list.
function move(step: number): void {
  const n = props.options.length;
  const dir: 1 | -1 = step > 0 ? 1 : -1;
  let i = activeIndex.value;
  for (let left = Math.abs(step); left > 0; left--) {
    let next = i + dir;
    while (next >= 0 && next < n && props.options[next].disabled) next += dir;
    if (next < 0 || next >= n) break;
    i = next;
  }
  if (i !== activeIndex.value) {
    activeIndex.value = i;
    void nextTick(scrollActiveIntoView);
  }
}

/* ---------- type-ahead ---------- */

let buf = "";
let bufTimer = 0;

function typeahead(ch: string): void {
  const n = props.options.length;
  if (!n) return;
  clearTimeout(bufTimer);
  bufTimer = window.setTimeout(() => { buf = ""; }, 500);
  buf += ch.toLowerCase();
  // One key pressed repeatedly cycles the options starting with it; anything
  // else accumulates into a prefix. Same rule as a native select.
  const repeat = buf.length > 1 && [...buf].every(c => c === buf[0]);
  const query = repeat ? buf[0] : buf;
  const from = open.value ? activeIndex.value : selectedIndex.value;
  // A fresh key (or the same one again) looks past the current row so repeats
  // cycle; a growing prefix re-tests the current row first.
  for (let step = repeat || buf.length === 1 ? 1 : 0; step <= n; step++) {
    const i = (from + step + n) % n;
    const o = props.options[i];
    if (o.disabled || !o.label.toLowerCase().startsWith(query)) continue;
    if (open.value) {
      activeIndex.value = i;
      void nextTick(scrollActiveIntoView);
    } else {
      choose(i); // matches the native closed-select behaviour: commit outright
    }
    return;
  }
}

/* ---------- keyboard ---------- */

// Every key this control acts on is stopped as well as prevented: the window
// keydown handler runs app shortcuts (R, X, O, backslash) off plain letters,
// which would otherwise fire mid-typeahead.
function onKeydown(e: KeyboardEvent): void {
  if (props.disabled) return;
  const consume = () => { e.preventDefault(); e.stopPropagation(); };

  if (open.value) {
    switch (e.key) {
      case "Escape": consume(); close(true); return;
      case "Enter": case " ": consume(); choose(activeIndex.value); return;
      case "Tab": close(false); return; // let focus move on, like a native select
      case "ArrowDown": consume(); move(1); return;
      case "ArrowUp": consume(); move(-1); return;
      case "PageDown": consume(); move(10); return;
      case "PageUp": consume(); move(-10); return;
      case "Home": consume(); activeIndex.value = firstEnabled(1); void nextTick(scrollActiveIntoView); return;
      case "End": consume(); activeIndex.value = firstEnabled(-1); void nextTick(scrollActiveIntoView); return;
    }
  } else {
    switch (e.key) {
      case "ArrowDown": case "ArrowUp": case "Enter": case " ":
        consume(); void openMenu("selected"); return;
      case "Home": consume(); void openMenu("first"); return;
      case "End": consume(); void openMenu("last"); return;
    }
  }

  if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
    consume();
    typeahead(e.key);
  }
}

// A row can disappear under an open popup (the engine switch hides the DCP
// picker), and a disabled control must not stay open.
watch(() => props.disabled, (d) => { if (d) close(false); });
onBeforeUnmount(() => close(false));
</script>

<template>
  <button ref="triggerEl" :id="id" type="button" class="control-select"
    :class="{ 'is-open': open, 'is-icon-trigger': icon }"
    :disabled="disabled" :title="title" :aria-label="ariaLabel" role="combobox" aria-haspopup="listbox"
    :aria-expanded="open" :aria-controls="open ? listId : undefined"
    :aria-activedescendant="open && activeIndex >= 0 ? optionId(activeIndex) : undefined" @click="onTriggerClick"
    @keydown="onKeydown">
    <template v-if="icon === 'sort'">
      <svg class="control-select-icon" viewBox="0 0 24 24" aria-hidden="true">
        <path d="M5 7h14M5 12h10M5 17h6" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" />
      </svg>
    </template>
    <template v-else-if="icon === 'group'">
      <svg class="control-select-icon" viewBox="0 0 24 24" aria-hidden="true">
        <rect x="4" y="5" width="6" height="6" rx="1" fill="none" stroke="currentColor" stroke-width="1.8" />
        <rect x="14" y="5" width="6" height="6" rx="1" fill="none" stroke="currentColor" stroke-width="1.8" />
        <rect x="4" y="14" width="6" height="6" rx="1" fill="none" stroke="currentColor" stroke-width="1.8" />
        <rect x="14" y="14" width="6" height="6" rx="1" fill="none" stroke="currentColor" stroke-width="1.8" />
      </svg>
    </template>
    <span v-else class="control-select-value">{{ selectedLabel }}</span>
    <svg class="control-select-caret" viewBox="0 0 10 6" aria-hidden="true">
      <path d="M1 1l4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"
        stroke-linejoin="round" />
    </svg>
  </button>
  <!-- Body-level so the rail's overflow and the cards' stacking contexts can't
       clip it; fixed coordinates are recomputed on scroll. -->
  <Teleport to="body">
    <div v-if="open" :id="listId" ref="listEl" class="select-pop" :class="{ 'is-placed': placed }" :style="popStyle"
      role="listbox" :aria-label="ariaLabel" @pointerdown="onListPointerDown">
      <div v-for="(o, i) in options" :id="optionId(i)" :key="String(o.value)" class="select-option"
        :class="{ 'is-active': i === activeIndex, 'is-selected': o.value === modelValue, 'is-disabled': o.disabled }"
        role="option" :aria-selected="o.value === modelValue" :aria-disabled="o.disabled || undefined"
        @pointerenter="o.disabled || (activeIndex = i)" @click="choose(i)">
        <svg class="select-check" viewBox="0 0 14 14" aria-hidden="true">
          <path d="M2 7.5l3.2 3.2L12 3.8" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"
            stroke-linejoin="round" />
        </svg>
        <span class="select-option-label">{{ o.label }}</span>
      </div>
    </div>
  </Teleport>
</template>
