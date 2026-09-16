import { describe, expect, it } from "vitest";

import { buildRows, cellLeft, GRID_CELL, GRID_HEADER_H, photosInRect, type GridRow } from "../gridRows";
import { rowAt, topsOf } from "../composables/useVirtualRows";
import { photos } from "./photo-fixture";

const section = (label: string | null, n: number) => ({ label, photos: photos(n) });

describe("buildRows", () => {
  it("chunks each section into bands of `lanes`, last band short", () => {
    const { rows, rowOfPhoto } = buildRows([section(null, 5)], 2, false);
    expect(rows.map(r => [r.kind, r.kind === "photos" ? [r.first, r.count] : r.label]))
      .toEqual([["photos", [0, 2]], ["photos", [2, 2]], ["photos", [4, 1]]]);
    expect(rowOfPhoto).toHaveLength(5);
  });

  it("puts a header above each group when grouping is on", () => {
    const { rows } = buildRows([section("2024-05-01", 2), section(null, 1)], 3, true);
    expect(rows.map(r => r.kind)).toEqual(["header", "photos", "header", "photos"]);
    expect(rows[0]).toMatchObject({ label: "2024-05-01", count: 2, height: GRID_HEADER_H });
    expect(rows[2]).toMatchObject({ label: null, count: 1 });
    expect(rows[3]).toMatchObject({ first: 2, count: 1, height: GRID_CELL.h });
  });

  it("numbers the photos across sections, so a flat index means one photo", () => {
    const { rows, rowOfPhoto } = buildRows([section("a", 3), section("b", 2)], 2, true);
    expect(rows.filter(r => r.kind === "photos").map(r => (r as { first: number }).first)).toEqual([0, 2, 3]);
    expect([...rowOfPhoto]).toEqual([1, 1, 2, 4, 4]);
    expect(rows[rowOfPhoto[3]]).toMatchObject({ kind: "photos", first: 3 });
  });

  it("never divides by a lane count of zero (a grid measured before layout)", () => {
    expect(buildRows([section(null, 2)], 0, false).rows).toHaveLength(2);
  });
});

describe("photosInRect", () => {
  const { rows } = buildRows([section("day 1", 4), section("day 2", 2)], 2, true);
  const heights = rows.map(r => r.height);
  const tops = topsOf(heights, GRID_CELL.gap);

  const rectOf = (left: number, top: number, right: number, bottom: number) => ({ left, top, right, bottom });

  it("finds the cells a rectangle covers", () => {
    const firstRowTop = tops[1];
    expect(photosInRect(rows, tops, rectOf(0, firstRowTop, cellLeft(1) + 5, firstRowTop + 10)))
      .toEqual([0, 1]);
    expect(photosInRect(rows, tops, rectOf(cellLeft(1), firstRowTop, cellLeft(1) + 5, firstRowTop + 5)))
      .toEqual([1]);
  });

  it("leaves out the padding between cells and the headers", () => {
    const firstRowTop = tops[1];
    const gapLeft = cellLeft(0) + GRID_CELL.w + 1;
    expect(photosInRect(rows, tops, rectOf(gapLeft, firstRowTop, gapLeft + 5, firstRowTop + 5))).toEqual([]);
    const headerTop = tops[0];
    expect(photosInRect(rows, tops, rectOf(0, headerTop, 500, headerTop + 5))).toEqual([]);
  });

  it("spans rows and sections, and stops at the end of a short band", () => {
    const top = tops[1];
    expect(photosInRect(rows, tops, rectOf(0, top, 500, top + 1000))).toEqual([0, 1, 2, 3, 4, 5]);
    // The band of day 2 holds two cells: a rect over the third lane has nothing.
    const lastTop = tops[tops.length - 1];
    expect(photosInRect(rows, tops, rectOf(cellLeft(2), lastTop, cellLeft(2) + 50, lastTop + 5))).toEqual([]);
  });

  it("takes a rect that only grazes a cell", () => {
    const top = tops[1];
    expect(photosInRect(rows, tops, rectOf(cellLeft(0) - 5, top + GRID_CELL.h - 1, cellLeft(0) + 2, top + GRID_CELL.h + 4)))
      .toEqual([0]);
    expect(photosInRect(rows, tops, rectOf(0, top - 1, 100, top))).toEqual([]);
  });
});

describe("rowAt", () => {
  const heights = [GRID_HEADER_H, GRID_CELL.h, GRID_CELL.h];
  const tops = topsOf(heights, GRID_CELL.gap);

  it("finds the row under an offset", () => {
    expect(rowAt(tops, heights, 0)).toBe(0);
    expect(rowAt(tops, heights, tops[1])).toBe(1);
    expect(rowAt(tops, heights, tops[1] + heights[1] - 1)).toBe(1);
    expect(rowAt(tops, heights, tops[2] - 1)).toBe(2); // in the gap, so the row below
    expect(rowAt(tops, heights, tops[2])).toBe(2);
  });

  it("past the end is the row count, so the visible range clamps", () => {
    expect(rowAt(tops, heights, 1e6)).toBe(3);
  });

  it("puts a pointer in the gap on the row below it", () => {
    const inGap = tops[1] + heights[1] + 1;
    expect(rowAt(tops, heights, inGap)).toBe(2);
  });

  it("paces the rows by their own heights plus the gap", () => {
    expect(tops).toEqual([0, GRID_HEADER_H + GRID_CELL.gap, GRID_HEADER_H + GRID_CELL.h + 2 * GRID_CELL.gap]);
  });
});

describe("grid geometry", () => {
  it("mirrors style.css .grid-cell (176 × 140, gap 8)", () => {
    expect(GRID_CELL).toEqual({ w: 176, h: 140, gap: 8 });
  });

  it("keeps rows of a fixed height whatever their content", () => {
    const rows: GridRow[] = buildRows([section(null, 2)], 4, true).rows;
    expect(rows.every(r => r.height === (r.kind === "header" ? GRID_HEADER_H : GRID_CELL.h))).toBe(true);
  });
});
