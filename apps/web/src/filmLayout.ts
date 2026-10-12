// The filmstrip's layout: the same sections the library grid shows, laid out
// along one row. When grouping is on, each group opens with a narrow labelled
// divider, so the strip reads day by day (or camera by camera) like the grid.
// Every x is arithmetic, so the strip can window and scroll to a cell that was
// never mounted.

import type { Section } from "./librarySort";
import type { Photo } from "./ui";

// Fixed geometry, mirrored by style.css (.film-cell, .film-group). The
// virtualiser positions by arithmetic, so the two must agree.
export const FILM_CELL = { w: 108, h: 96, gap: 4 };
export const FILM_HEADER_W = 22;

export type FilmItem =
  | { kind: "header"; key: string; x: number; w: number; label: string | null; count: number }
  | { kind: "photo"; key: string; x: number; w: number; photo: Photo };

export type FilmLayout = {
  items: FilmItem[];
  /** Item index of every photo, in flattened order — what the strip scrolls to. */
  itemOfPhoto: Map<string, number>;
  totalSize: number;
};

/**
 * `sections` along one row. Dividers are only laid out when `grouped` (a single
 * unlabelled section is the whole folder, and a divider naming nothing would
 * just cost width).
 */
export function buildFilmLayout(sections: Section[], grouped: boolean): FilmLayout {
  const items: FilmItem[] = [];
  const itemOfPhoto = new Map<string, number>();
  let x = 0;
  sections.forEach((section, si) => {
    if (grouped && section.photos.length) {
      items.push({ kind: "header", key: `h:${si}`, x, w: FILM_HEADER_W, label: section.label, count: section.photos.length });
      x += FILM_HEADER_W + FILM_CELL.gap;
    }
    for (const photo of section.photos) {
      itemOfPhoto.set(photo.id, items.length);
      items.push({ kind: "photo", key: photo.id, x, w: FILM_CELL.w, photo });
      x += FILM_CELL.w + FILM_CELL.gap;
    }
  });
  return { items, itemOfPhoto, totalSize: Math.max(0, x - FILM_CELL.gap) };
}
