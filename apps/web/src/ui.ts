// Small UI helpers and types shared between App.vue and its child components.

// `invalid` is frontend-only (set when the server can no longer decode the
// source, e.g. tmp/sessions was cleared); never persisted.
export type Source = { id: string; name: string; size: number; embeddedUrl: string; invalid?: boolean };

export function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

// Build a filled-track gradient for a range input. Bipolar sliders (min<0<max)
// fill from the center toward the thumb; unipolar fill from the left.
export function trackFill(value: number, min: number, max: number): string {
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

// Importable formats, in the order the dropzone names them. One list drives
// both the file picker's accept string and the hint under the dropzone, which
// had drifted apart — .srf/.sr2/.cr2 were accepted but went unmentioned.
// The aliases grouped under each label are accepted but not worth listing.
const IMPORT_FORMATS: { label: string; ext: string[] }[] = [
  { label: "ARW", ext: ["arw", "srf", "sr2"] },
  { label: "DNG", ext: ["dng"] },
  { label: "CR3", ext: ["cr2", "cr3"] },
  { label: "NEF", ext: ["nef"] },
  { label: "RAF", ext: ["raf"] },
  { label: "RW2", ext: ["rw2"] },
  { label: "ORF", ext: ["orf"] },
  { label: "JPEG", ext: ["jpg", "jpeg"] },
  { label: "PNG", ext: ["png"] },
  { label: "TIFF", ext: ["tif", "tiff"] },
];

/** `accept` for the hidden file input. */
export const IMPORT_ACCEPT = IMPORT_FORMATS.flatMap(f => f.ext.map(e => `.${e}`)).join(",");

/** The format line under the dropzone. */
export const IMPORT_FORMAT_HINT = IMPORT_FORMATS.map(f => f.label).join(" · ");
