import { describe, expect, it } from "vitest";

import { buildFilmLayout, FILM_CELL, FILM_HEADER_W } from "../filmLayout";
import { firstItemAfter } from "../composables/useVirtualStrip";
import { photos } from "./photo-fixture";

const step = FILM_CELL.w + FILM_CELL.gap;

describe("buildFilmLayout", () => {
  it("lays one ungrouped section out as a plain row of cells", () => {
    const ps = photos(3);
    const { items, itemOfPhoto, totalSize } = buildFilmLayout([{ label: null, photos: ps }], false);
    expect(items.map(i => [i.kind, i.x])).toEqual([["photo", 0], ["photo", step], ["photo", 2 * step]]);
    expect(itemOfPhoto.get(ps[2].id)).toBe(2);
    expect(totalSize).toBe(3 * step - FILM_CELL.gap);
  });

  it("opens each group with a divider when grouping is on", () => {
    const ps = photos(3);
    const { items, itemOfPhoto } = buildFilmLayout(
      [{ label: "2024-05-01", photos: ps.slice(0, 2) }, { label: null, photos: ps.slice(2) }], true);
    expect(items.map(i => i.kind)).toEqual(["header", "photo", "photo", "header", "photo"]);
    expect(items[0]).toMatchObject({ label: "2024-05-01", count: 2, x: 0, w: FILM_HEADER_W });
    expect(items[1].x).toBe(FILM_HEADER_W + FILM_CELL.gap);
    expect(items[3]).toMatchObject({ label: null, count: 1 });
    expect(itemOfPhoto.get(ps[2].id)).toBe(4);
  });
});

describe("firstItemAfter", () => {
  it("finds the first item whose right edge passes the position", () => {
    const { items } = buildFilmLayout([{ label: "a", photos: photos(2) }], true);
    expect(firstItemAfter(items, 0)).toBe(0);
    expect(firstItemAfter(items, FILM_HEADER_W)).toBe(1);
    expect(firstItemAfter(items, 1e6)).toBe(items.length);
  });
});
