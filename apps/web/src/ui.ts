// Small UI helpers and types shared between App.vue and its child components.

// `invalid` is frontend-only (set when the server can no longer decode the
// source, e.g. tmp/sessions was cleared); never persisted.
export type Source = { id: string; name: string; size: number; embeddedUrl: string; invalid?: boolean };

// Build a filled-track gradient for a range input. Bipolar sliders (min<0<max)
// fill from the center toward the thumb; unipolar fill from the left.
export function trackFill(value: number, min: number, max: number): string {
  const clamp = (v: number, lo: number, hi: number): number => (v < lo ? lo : v > hi ? hi : v);
  const p = clamp((value - min) / (max - min), 0, 1) * 100;
  const z = min < 0 && max > 0 ? (-min) / (max - min) * 100 : 0;
  const a = Math.min(p, z);
  const b = Math.max(p, z);
  return `linear-gradient(to right, var(--track-bg) ${a}%, var(--accent) ${a}%, var(--accent) ${b}%, var(--track-bg) ${b}%)`;
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}
