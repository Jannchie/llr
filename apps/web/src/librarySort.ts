// Sorting and grouping for the library grid: the two view decisions that turn
// the open folder's listing into the blocks PhotoGrid renders. Pure and
// separate from the component because it is the part worth testing — the grid
// only has to lay the sections out.
//
// A section is a group's photos plus the label the grid prints above them.
// `label` is null for the group that has nothing to name it (a photo with no
// EXIF clock, no camera body, no lens): the caller renders that bucket with a
// localized "unknown" rather than inventing a value here, where there is no
// locale to depend on.

import type { Photo } from "./ui";

export type SortKey =
  | "default" | "name" | "captured" | "modified" | "imported" | "size"
  | "pixels" | "iso" | "focal" | "aperture" | "shutter" | "camera" | "lens" | "type";
export type SortDir = "asc" | "desc";
export type GroupKey = "none" | "day" | "month" | "year" | "camera" | "lens" | "type";

export type Section = { label: string | null; photos: Photo[] };
/** The grid's input: the photos as sections, and whether to draw their headings. */
export type Library = { sections: Section[]; grouped: boolean };

/** The sort keys, in the order the picker lists them. */
export const SORT_KEYS: SortKey[] = [
  "default", "captured", "modified", "imported", "name", "size", "pixels",
  "camera", "lens", "focal", "aperture", "shutter", "iso", "type",
];

export const GROUP_KEYS: GroupKey[] = ["none", "day", "month", "year", "camera", "lens", "type"];

// One collator for the whole module: localeCompare re-resolves the locale and
// allocates an options object on every call, and a folder of thousands spends
// that six figures of times inside one sort.
const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });

function text(a: string, b: string): number {
  return collator.compare(a, b);
}

// The three fields a photo can be labelled by, spelled once so sorting and
// grouping cannot disagree about what a camera or a file type is.
function cameraOf(p: Photo): string | null {
  return p.model ?? p.make;
}

function typeOf(p: Photo): string | null {
  return p.ext ? p.ext.replace(/^\./, "").toUpperCase() : null;
}

// Nothing sorts before anything: a photo missing the field goes last in either
// direction, which is what keeps a folder of untagged scans from heading the
// list when the user asks for the newest shots first.
function compareValues(a: string | number | null, b: string | number | null): number {
  if (a === null && b === null) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return text(String(a), String(b));
}

function valueOf(p: Photo, key: SortKey): string | number | null {
  switch (key) {
    case "name": return p.name;
    case "captured": return p.capturedAt;
    case "modified": return p.modifiedAt;
    case "imported": return p.importedAt;
    case "size": return p.size;
    case "pixels": return p.width && p.height ? p.width * p.height : null;
    case "iso": return p.iso;
    case "focal": return p.focal;
    case "aperture": return p.fnumber;
    case "shutter": return p.exposure;
    case "camera": return cameraOf(p);
    case "lens": return p.lens;
    case "type": return typeOf(p);
    default: return null;
  }
}

function compareBy(key: SortKey, dir: SortDir, a: Photo, b: Photo): number {
  const va = valueOf(a, key);
  const vb = valueOf(b, key);
  // The direction flips the comparison, never the missing-value rule: an
  // unlabelled photo stays at the end when the list is reversed.
  if (va === null || vb === null) return compareValues(va, vb);
  return (dir === "asc" ? 1 : -1) * compareValues(va, vb);
}

/**
 * The folder's photos in the requested order. `default` keeps the catalog's own
 * order (capture time, then import order) and `desc` reverses it; every other
 * key compares the field with missing values last, so flipping the direction
 * never pulls the unlabelled photos to the top. Ties keep the catalog's order —
 * Array.sort is stable, and the input arrives in it.
 */
export function sortPhotos(photos: Photo[], key: SortKey, dir: SortDir): Photo[] {
  if (key === "default") return dir === "asc" ? photos.slice() : photos.slice().reverse();
  return photos.slice().sort((a, b) => compareBy(key, dir, a, b));
}

/** The string a photo groups under, or null for the bucket that has none. */
function groupValueOf(p: Photo, key: GroupKey): string | null {
  switch (key) {
    case "day": return p.capturedAt ? p.capturedAt.slice(0, 10) : null;
    case "month": return p.capturedAt ? p.capturedAt.slice(0, 7) : null;
    case "year": return p.capturedAt ? p.capturedAt.slice(0, 4) : null;
    case "camera": return cameraOf(p);
    case "lens": return p.lens;
    case "type": return typeOf(p);
    default: return null;
  }
}

/**
 * Cut `photos` (already sorted) into sections by `key`, in the order the groups
 * first appear — so sorting by capture time groups the days chronologically,
 * and sorting by name groups the camera bodies alphabetically. Photos with no
 * value for the key collect in one unlabelled section, which therefore lands
 * wherever the sort put them (last, for every key that sorts nulls last).
 *
 * The label is the group value itself — dates as their ISO prefix, so they stay
 * comparable and need no locale to print.
 */
export function groupPhotos(photos: Photo[], key: GroupKey): Section[] {
  if (key === "none") return [{ label: null, photos }];
  const sections: Section[] = [];
  const byLabel = new Map<string, Section>();
  for (const photo of photos) {
    const label = groupValueOf(photo, key);
    let section = byLabel.get(label ?? "");
    if (!section) {
      section = { label, photos: [] };
      byLabel.set(label ?? "", section);
      sections.push(section);
    }
    section.photos.push(photo);
  }
  return sections;
}

/** The folder's photos as the grid needs them: sorted, then grouped. */
export function buildSections(photos: Photo[], sort: SortKey, dir: SortDir, group: GroupKey): Library {
  return { sections: groupPhotos(sortPhotos(photos, sort, dir), group), grouped: group !== "none" };
}
