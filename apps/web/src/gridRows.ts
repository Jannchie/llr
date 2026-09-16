// The library grid's rows: the sections PhotoGrid is handed, laid out into the
// rows the virtualiser scrolls. A row is either a group header or a band of
// cells, and every position is arithmetic — nothing is measured — so the grid
// can place, scroll to and hit-test a row that was never mounted.
//
// Cells are laid out here rather than by CSS so the marquee's hit test and the
// painted cells come from the same numbers.

import type { Section } from "./librarySort";

// Fixed cell geometry, mirrored by style.css (.grid-cell). The virtualiser
// positions by arithmetic, so the two must agree.
export const GRID_CELL = { w: 176, h: 140, gap: 8 };
export const GRID_HEADER_H = 30;

export type GridRow =
  | { kind: "header"; key: string; label: string | null; count: number; height: number }
  /** `first`/`count` index the flattened photo list the caller built from the same sections. */
  | { kind: "photos"; key: string; first: number; count: number; height: number };

export type GridLayout = {
  rows: GridRow[];
  /** Row index of every photo, in flattened order — what the keyboard scrolls to. */
  rowOfPhoto: Int32Array;
};

/**
 * `sections` at `lanes` cells across. Headers are only laid out when `grouped`
 * (a single unlabelled section is the whole folder, and a header naming nothing
 * would just cost a row).
 */
export function buildRows(sections: Section[], lanes: number, grouped: boolean): GridLayout {
  const across = Math.max(1, Math.floor(lanes));
  const rows: GridRow[] = [];
  const rowOf: number[] = [];
  let first = 0;
  sections.forEach((section, si) => {
    if (grouped) {
      rows.push({ kind: "header", key: `h:${si}`, label: section.label, count: section.photos.length, height: GRID_HEADER_H });
    }
    for (let i = 0; i < section.photos.length; i += across) {
      const count = Math.min(across, section.photos.length - i);
      rows.push({ kind: "photos", key: `r:${si}:${i}`, first: first + i, count, height: GRID_CELL.h });
      for (let k = 0; k < count; k++) rowOf[first + i + k] = rows.length - 1;
    }
    first += section.photos.length;
  });
  return { rows, rowOfPhoto: Int32Array.from(rowOf) };
}

export type Rect = { left: number; top: number; right: number; bottom: number };

/**
 * Every photo whose cell the rectangle touches, ascending — what a marquee drag
 * has swept. Padding between cells belongs to no cell, as in a file manager:
 * the pointer has to be over a thumbnail (or its label) to pick it up.
 *
 * `from` is the first row that can intersect the rectangle (`rowAt`, so the
 * scan does not walk the whole folder for a rectangle near its end).
 */
export function photosInRect(rows: GridRow[], tops: number[], rect: Rect, from = 0): number[] {
  const hits: number[] = [];
  const step = GRID_CELL.w + GRID_CELL.gap;
  for (let r = from; r < rows.length; r++) {
    const row = rows[r];
    const top = tops[r];
    if (top >= rect.bottom) break; // rows are in order: nothing below can match
    if (row.kind !== "photos" || top + row.height <= rect.top) continue;
    for (let lane = 0; lane < row.count; lane++) {
      const left = lane * step;
      if (left + GRID_CELL.w <= rect.left) continue;
      if (left >= rect.right) break;
      hits.push(row.first + lane);
    }
  }
  return hits;
}

/** Left edge of the cell in `lane`, in the content box's coordinates. */
export function cellLeft(lane: number): number {
  return lane * (GRID_CELL.w + GRID_CELL.gap);
}
