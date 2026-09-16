<script setup lang="ts">
import { nextTick, ref, useTemplateRef } from "vue";

// A name being typed over the top of something — a folder row, a photo cell.
// Both callers had their own copy of the same field: focus it once it is up,
// select what the user is about to replace, Enter commits, Escape and blur
// back out, and an empty or unchanged name is not a change at all.
//
// The field owns the keyboard while it is open: it stops the event, so the
// grid's Enter/Delete/F2 shortcuts and the tree row's Delete cannot fire out
// from under someone who is typing a name.
const props = withDefaults(defineProps<{
  value: string;
  /** What to select on open: the whole name, or just the part before the `.`. */
  select?: "all" | "stem";
}>(), { select: "all" });

const emit = defineEmits<{
  (e: "commit", name: string): void;
  (e: "cancel"): void;
}>();

const input = useTemplateRef<HTMLInputElement>("input");
const text = ref(props.value);

void nextTick(() => {
  const el = input.value;
  if (!el) return;
  el.focus();
  // The extension is the file's own and is put back if it is left out, so the
  // stem is what is worth editing; a folder name has no extension to spare.
  const dot = props.select === "stem" ? el.value.lastIndexOf(".") : -1;
  el.setSelectionRange(0, dot > 0 ? dot : el.value.length);
});

// Settled once: Enter commits and the field is torn down with it, which fires
// the blur that would otherwise commit a second time.
let settled = false;

// An empty name is not a name: both callers drop the edit with nothing said.
// Whether an unchanged one is a change is the caller's rule (a rename of a
// photo to what it is already called is not; a new folder called "New folder"
// is), so the name goes through either way.
function commit(): void {
  if (settled) return;
  settled = true;
  const name = text.value.trim();
  if (name) emit("commit", name);
  else emit("cancel");
}

function cancel(): void {
  if (settled) return;
  settled = true;
  emit("cancel");
}

function onKeyDown(e: KeyboardEvent): void {
  e.stopPropagation();
  if (e.key === "Enter") { e.preventDefault(); commit(); }
  else if (e.key === "Escape") { e.preventDefault(); cancel(); }
}
</script>

<template>
  <input ref="input" class="inline-edit" v-model="text" spellcheck="false"
    :aria-label="value" @keydown="onKeyDown" @blur="commit" />
</template>
