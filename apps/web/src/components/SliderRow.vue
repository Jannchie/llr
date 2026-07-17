<script setup lang="ts">
import { computed, nextTick } from "vue";
import { trackFill, clamp } from "../ui";

// One labelled range + number pair. Covers every slider row in the settings
// rail (recipe, HSL, grading, denoise, parametric curve, crop angle) so the
// double-click-to-reset / track-fill / modified-state behaviour stays uniform.
const props = withDefaults(defineProps<{
  label: string;
  modelValue: number;
  min: number;
  max: number;
  step?: number;
  resetValue?: number;      // double-click target
  rowClass?: string;        // "slider" | "grading-row" | "hsl-row"
  numberClass?: string;     // "slider-number" | "hsl-number"
  dotColor?: string;        // colour swatch before the label (HSL rows)
  track?: string;           // custom track gradient (white balance axes)
  showModified?: boolean;   // paint is-modified when off the reset value
  inputId?: string;
}>(), {
  step: 1,
  resetValue: 0,
  rowClass: "slider",
  numberClass: "slider-number",
  showModified: false,
});

const emit = defineEmits<{ (e: "update:modelValue", v: number): void }>();

const trackStyle = computed(() => props.track ?? trackFill(props.modelValue, props.min, props.max));

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
  <div :class="[rowClass, { 'is-modified': showModified && modelValue !== resetValue }]">
    <template v-if="dotColor">
      <span class="hsl-dot" :style="{ background: dotColor }" />
      <span class="hsl-label">{{ label }}</span>
    </template>
    <label v-else :for="inputId">{{ label }}</label>
    <input :id="inputId" type="range" :min="min" :max="max" :step="step"
      :value="modelValue" :style="{ '--track': trackStyle }"
      @input="onInput" @dblclick="emit('update:modelValue', resetValue)"
      title="Double-click to reset" />
    <input :class="numberClass" type="number" :min="min" :max="max" :step="step"
      :value="modelValue" :aria-label="label"
      @input="onInput" @change="onCommit" @blur="onCommit" />
  </div>
</template>
