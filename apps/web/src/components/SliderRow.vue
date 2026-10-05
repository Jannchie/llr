<script setup lang="ts">
import { computed, nextTick, ref } from "vue";
import { t } from "../i18n";
import { clamp } from "../ui";

// One labelled range + number pair. Covers every slider row in the settings
// rail (recipe, HSL, grading, denoise, parametric curve, crop angle) so the
// double-click-to-reset / track-fill / modified-state behaviour stays uniform.
// Two tiers: the label and the value on a line, the range under them across
// the full width of the rail.
const props = withDefaults(defineProps<{
  label: string;
  modelValue: number;
  min: number;
  max: number;
  step?: number;
  resetValue?: number;      // double-click target
  rowClass?: string;        // variant on top of .slider: "grading-row" | "hsl-row"
  dotColor?: string;        // colour swatch before the label, and the fill's colour (HSL rows)
  track?: string;           // custom track gradient (white balance axes)
  showModified?: boolean;   // paint is-modified when off the reset value
  inputId?: string;
}>(), {
  step: 1,
  resetValue: 0,
  rowClass: "",
  showModified: false,
});

const emit = defineEmits<{ (e: "update:modelValue", v: number): void }>();

// The visible slider is drawn by hand over a transparent native range, which
// keeps the input's keyboard, wheel-nudge, focus and a11y behaviour. Positions
// are fractions of the range handed to CSS: --range-p (the value) is a
// registered property, so a jump — double-click reset, wheel, a typed number —
// glides there, while a drag sets it with the transition off and the thumb
// stays glued to the pointer. --range-o is where the fill starts: the zero of
// a bipolar slider, else its minimum.
const frac = (v: number) => clamp((v - props.min) / (props.max - props.min), 0, 1);
const rangeVars = computed(() => ({
  "--range-p": frac(props.modelValue),
  "--range-o": props.min < 0 && props.max > 0 ? frac(0) : 0,
}));

// A small tick on the track marks the double-click target, so a nudged slider
// still shows where "untouched" was. Defaults pinned to an end of the range
// get no mark — the empty fill already says it.
const markFrac = computed(() => {
  const r = props.resetValue;
  return r > props.min && r < props.max ? frac(r) : null;
});

const dragging = ref(false);
function onPointerDown(): void {
  dragging.value = true;
  const end = () => {
    dragging.value = false;
    window.removeEventListener("pointerup", end);
    window.removeEventListener("pointercancel", end);
  };
  window.addEventListener("pointerup", end);
  window.addEventListener("pointercancel", end);
}

function onInput(e: Event): void {
  const v = (e.target as HTMLInputElement).valueAsNumber;
  if (Number.isFinite(v)) emit("update:modelValue", v);
}

// The number box is the only way an out-of-range value can enter the model: a
// range input is clamped by the browser, but type="number" happily reports 1
// for a 2000K minimum. Clamping mid-keystroke would stomp on "-" or a "1" on
// the way to "12", so the range is only enforced once the edit is committed.
async function onCommit(e: Event): Promise<void> {
  const el = e.target as HTMLInputElement;
  const raw = el.valueAsNumber;
  const v = Number.isFinite(raw) ? clamp(raw, props.min, props.max) : props.modelValue;
  if (v !== props.modelValue) emit("update:modelValue", v);
  // The parent owns the value and may normalise it further (crop angle rounds
  // to one decimal), so the box can only be resynced once the model settles.
  await nextTick();
  const settled = String(props.modelValue);
  if (el.value !== settled) el.value = settled;
}
</script>

<template>
  <div :class="['slider', rowClass, { 'is-modified': showModified && modelValue !== resetValue }]"
    :style="dotColor ? { '--fill': dotColor } : undefined">
    <div class="slider-head">
      <span v-if="dotColor" class="hsl-dot" :style="{ background: dotColor }" />
      <label :for="inputId">{{ label }}</label>
      <input class="slider-number" type="number" :min="min" :max="max" :step="step"
        :value="modelValue" :aria-label="label"
        @input="onInput" @change="onCommit" @blur="onCommit" />
    </div>
    <span class="range-wrap" :class="{ 'is-dragging': dragging }" :style="rangeVars">
      <span class="range-rail" :style="track ? { background: track } : undefined" />
      <span v-if="!track" class="range-fill" />
      <span v-if="markFrac != null" class="range-mark" :style="{ '--range-r': markFrac }" />
      <span class="range-thumb" />
      <input :id="inputId" class="range-input" type="range" :min="min" :max="max" :step="step"
        :value="modelValue" @input="onInput" @pointerdown="onPointerDown"
        @dblclick="emit('update:modelValue', resetValue)"
        :title="t('slider.hint')" />
    </span>
  </div>
</template>
