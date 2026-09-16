import { describe, expect, it } from "vitest";

import { buildSections, groupPhotos, sortPhotos, GROUP_KEYS, SORT_KEYS } from "../librarySort";
import { photo } from "./photo-fixture";
import type { Photo } from "../ui";

const ids = (photos: Photo[]) => photos.map(p => p.id);

// A folder's listing as the API hands it over, oldest shot first.
const folder = [
  photo({ id: "b", capturedAt: "2024-05-02T09:00:00", modifiedAt: 300, importedAt: 30, size: 200, model: "ILCE-7M4" }),
  photo({ id: "a", capturedAt: "2024-05-01T23:00:00", modifiedAt: 200, importedAt: 20, size: 300, model: "ILCE-7M4" }),
  photo({ id: "c", capturedAt: "2023-12-31T10:00:00", modifiedAt: 400, importedAt: 40, size: 100, model: "X-T5" }),
  photo({ id: "d", importedAt: 10, size: 400 }),
];

describe("sortPhotos", () => {
  it("keeps the catalog's order for `default`, and reverses it", () => {
    expect(ids(sortPhotos(folder, "default", "asc"))).toEqual(["b", "a", "c", "d"]);
    expect(ids(sortPhotos(folder, "default", "desc"))).toEqual(["d", "c", "a", "b"]);
    expect(ids(folder)).toEqual(["b", "a", "c", "d"]); // the input is not touched
  });

  it("sorts by capture time with unknown clocks last, either way", () => {
    expect(ids(sortPhotos(folder, "captured", "asc"))).toEqual(["c", "a", "b", "d"]);
    expect(ids(sortPhotos(folder, "captured", "desc"))).toEqual(["b", "a", "c", "d"]);
  });

  // The catalog always fills the modified time (its import time for a file
  // that arrived without one), so unlike the other keys nothing goes last.
  it("sorts by modification time", () => {
    expect(ids(sortPhotos(folder, "modified", "asc"))).toEqual(["a", "b", "c", "d"]);
    expect(ids(sortPhotos(folder, "modified", "desc"))).toEqual(["d", "c", "b", "a"]);
  });

  it("sorts numbers and names", () => {
    expect(ids(sortPhotos(folder, "size", "asc"))).toEqual(["c", "b", "a", "d"]);
    expect(ids(sortPhotos(folder, "size", "desc"))).toEqual(["d", "a", "b", "c"]);
    expect(ids(sortPhotos([photo({ id: "x", name: "IMG10.arw" }), photo({ id: "y", name: "IMG2.arw" })], "name", "asc")))
      .toEqual(["y", "x"]);
  });

  it("sorts the camera, the lens and the file type, unlabelled last", () => {
    const mixed = [...folder, photo({ id: "e", capturedAt: "2024-05-02T09:00:00", ext: ".dng", lens: "FE 24-70", make: "SONY" })];
    expect(ids(sortPhotos(mixed, "camera", "asc"))).toEqual(["b", "a", "e", "c", "d"]);
    expect(ids(sortPhotos(mixed, "lens", "asc"))).toEqual(["e", "b", "a", "c", "d"]);
    expect(ids(sortPhotos(mixed, "type", "asc"))).toEqual(["b", "a", "c", "d", "e"]);
  });

  it("keeps ties in the catalog's order", () => {
    const tied = [photo({ id: "first", iso: 100 }), photo({ id: "second", iso: 100 })];
    expect(ids(sortPhotos(tied, "iso", "desc"))).toEqual(["first", "second"]);
  });

  it("sorts the fields that are per-shot numbers", () => {
    const shots = [
      photo({ id: "wide", focal: 24, fnumber: 1.8, exposure: 1 / 60, iso: 400 }),
      photo({ id: "long", focal: 200, fnumber: 4, exposure: 1 / 500, iso: 100 }),
    ];
    expect(ids(sortPhotos(shots, "focal", "desc"))).toEqual(["long", "wide"]);
    expect(ids(sortPhotos(shots, "aperture", "asc"))).toEqual(["wide", "long"]);
    expect(ids(sortPhotos(shots, "shutter", "asc"))).toEqual(["long", "wide"]);
    expect(ids(sortPhotos(shots, "iso", "asc"))).toEqual(["long", "wide"]);
  });

  it("sorts by the pixel count, not the megapixel label", () => {
    const shots = [photo({ id: "tall", width: 4000, height: 5000 }), photo({ id: "wide", width: 6000, height: 4000 })];
    expect(ids(sortPhotos(shots, "pixels", "asc"))).toEqual(["tall", "wide"]);
    expect(ids(sortPhotos([photo({ id: "none", width: null, height: null }), ...shots], "pixels", "asc")))
      .toEqual(["tall", "wide", "none"]);
  });
});

describe("groupPhotos", () => {
  it("makes one section for the whole folder when grouping is off", () => {
    expect(groupPhotos(folder, "none")).toEqual([{ label: null, photos: folder }]);
  });

  const shot = [
    photo({ id: "a", capturedAt: "2024-05-01T23:00:00", model: "ILCE-7M4", lens: "FE 24-70" }),
    photo({ id: "b", capturedAt: "2024-05-02T09:00:00", model: "ILCE-7M4", lens: "FE 24-70" }),
    photo({ id: "c", capturedAt: "2024-05-02T21:00:00", model: "X-T5", lens: "XF 35" }),
    photo({ id: "d" }),
  ];

  it("groups by day, month and year of the capture time", () => {
    expect(groupPhotos(shot, "day").map(s => [s.label, s.photos.length]))
      .toEqual([["2024-05-01", 1], ["2024-05-02", 2], [null, 1]]);
    expect(groupPhotos(shot, "month").map(s => [s.label, s.photos.length])).toEqual([["2024-05", 3], [null, 1]]);
    expect(groupPhotos(shot, "year").map(s => [s.label, s.photos.length])).toEqual([["2024", 3], [null, 1]]);
  });

  it("groups by camera, lens and file type", () => {
    expect(groupPhotos(shot, "camera").map(s => [s.label, s.photos.length])).toEqual([["ILCE-7M4", 2], ["X-T5", 1], [null, 1]]);
    expect(groupPhotos(shot, "lens").map(s => [s.label, s.photos.length])).toEqual([["FE 24-70", 2], ["XF 35", 1], [null, 1]]);
    expect(groupPhotos(shot, "type").map(s => [s.label, s.photos.length])).toEqual([["ARW", 4]]);
  });

  it("falls back to the make when the body wrote no model", () => {
    const noModel = [photo({ id: "a", make: "SONY" }), photo({ id: "b", model: "ILCE-7M4", make: "SONY" })];
    expect(groupPhotos(noModel, "camera").map(s => [s.label, s.photos.length])).toEqual([["SONY", 1], ["ILCE-7M4", 1]]);
  });

  it("orders the sections by where their first photo landed", () => {
    const newestFirst = sortPhotos(shot, "captured", "desc");
    expect(groupPhotos(newestFirst, "day").map(s => s.label)).toEqual(["2024-05-02", "2024-05-01", null]);
  });
});

describe("buildSections", () => {
  it("sorts first, then groups — so the sections follow the sort", () => {
    const byDay = buildSections(folder, "captured", "desc", "day");
    expect(byDay.grouped).toBe(true);
    expect(byDay.sections.map(s => [s.label, ids(s.photos)])).toEqual([
      ["2024-05-02", ["b"]], ["2024-05-01", ["a"]], ["2023-12-31", ["c"]], [null, ["d"]],
    ]);
    const byCamera = buildSections(folder, "name", "asc", "camera");
    expect(byCamera.sections.map(s => s.label)).toEqual(["ILCE-7M4", "X-T5", null]);
    expect(byCamera.sections.flatMap(s => ids(s.photos))).toEqual(ids(sortPhotos(folder, "name", "asc")));
  });

  it("hands back the flat listing unchanged when neither is asked for", () => {
    const flat = buildSections(folder, "default", "asc", "none");
    expect(flat.grouped).toBe(false);
    expect(flat.sections.flatMap(s => s.photos)).toEqual(folder);
  });
});

describe("key lists", () => {
  it("offers each key once", () => {
    expect(new Set(SORT_KEYS).size).toBe(SORT_KEYS.length);
    expect(new Set(GROUP_KEYS).size).toBe(GROUP_KEYS.length);
  });

  it("draws headings for every group key but `none`", () => {
    for (const key of GROUP_KEYS) {
      expect(buildSections(folder, "default", "asc", key).grouped, key).toBe(key !== "none");
    }
  });
});
