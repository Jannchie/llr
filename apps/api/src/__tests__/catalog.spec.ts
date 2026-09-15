import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { Catalog, CatalogError, ROOT_FOLDER_ID } from "../catalog.js";

let catalog: Catalog;

beforeEach(() => { catalog = new Catalog(":memory:"); });
afterEach(() => { catalog.close(); });

function addPhoto(id: string, folderId = ROOT_FOLDER_ID, importedAt = 1000) {
  return catalog.insertPhoto({ id, folderId, name: `${id}.arw`, ext: ".arw", size: 10 }, importedAt);
}

describe("folders", () => {
  it("starts with only the root", () => {
    expect(catalog.tree()).toEqual([{ id: ROOT_FOLDER_ID, parentId: null, name: "Library", count: 0 }]);
  });

  it("creates, renames and moves", () => {
    const a = catalog.createFolder(ROOT_FOLDER_ID, "A");
    const b = catalog.createFolder(ROOT_FOLDER_ID, "B");
    catalog.renameFolder(a.id, "A2");
    catalog.moveFolder(a.id, b.id);
    expect(catalog.getFolder(a.id)).toEqual({ id: a.id, parentId: b.id, name: "A2", count: 0 });
  });

  it("refuses a same-name sibling unless existingOk, which returns the existing one", () => {
    const a = catalog.createFolder(ROOT_FOLDER_ID, "Trip");
    expect(() => catalog.createFolder(ROOT_FOLDER_ID, "Trip")).toThrow(CatalogError);
    expect(catalog.createFolder(ROOT_FOLDER_ID, "Trip", { existingOk: true }).id).toBe(a.id);
  });

  it("refuses to move a folder into itself or its subtree", () => {
    const a = catalog.createFolder(ROOT_FOLDER_ID, "A");
    const b = catalog.createFolder(a.id, "B");
    expect(() => catalog.moveFolder(a.id, a.id)).toThrow(/into itself/);
    expect(() => catalog.moveFolder(a.id, b.id)).toThrow(/into itself/);
  });

  it("protects the root", () => {
    expect(() => catalog.renameFolder(ROOT_FOLDER_ID, "x")).toThrow(CatalogError);
    expect(() => catalog.moveFolder(ROOT_FOLDER_ID, ROOT_FOLDER_ID)).toThrow(CatalogError);
    expect(() => catalog.deleteFolder(ROOT_FOLDER_ID)).toThrow(CatalogError);
  });

  it("deletes a subtree, returning every photo id in it and dropping their edits", () => {
    const a = catalog.createFolder(ROOT_FOLDER_ID, "A");
    const b = catalog.createFolder(a.id, "B");
    addPhoto("p1", a.id);
    addPhoto("p2", b.id);
    addPhoto("p3");
    catalog.putEdit("p2", { snapshot: { x: 1 }, history: [{ x: 1 }], historyIndex: 0 });

    expect(catalog.deleteFolder(a.id).sort()).toEqual(["p1", "p2"]);
    expect(catalog.tree().map(f => f.id)).toEqual([ROOT_FOLDER_ID]);
    expect(catalog.getPhoto("p3")).not.toBeNull();
    expect(catalog.getPhoto("p2")).toBeNull();
    expect(catalog.getEdit("p2")).toBeNull();
  });

  it("counts direct photos only", () => {
    const a = catalog.createFolder(ROOT_FOLDER_ID, "A");
    addPhoto("p1", a.id);
    addPhoto("p2");
    expect(catalog.tree().map(f => [f.name, f.count])).toEqual([["A", 1], ["Library", 1]]);
  });
});

describe("photos", () => {
  it("lists by capture time, unknown capture times last by import order", () => {
    addPhoto("late", ROOT_FOLDER_ID, 3);
    addPhoto("none-b", ROOT_FOLDER_ID, 2);
    addPhoto("early", ROOT_FOLDER_ID, 1);
    addPhoto("none-a", ROOT_FOLDER_ID, 1);
    catalog.setPhotoMeta("late", { capturedAt: "2024-06-01T00:00:00" }, "ready");
    catalog.setPhotoMeta("early", { capturedAt: "2024-01-01T00:00:00" }, "ready");
    expect(catalog.listPhotos(ROOT_FOLDER_ID).map(p => p.id)).toEqual(["early", "late", "none-a", "none-b"]);
  });

  it("stores metadata, leaving absent keys alone", () => {
    addPhoto("p");
    catalog.setPhotoMeta("p", { width: 6000, height: 4000, make: "SONY", iso: 100 }, "ready");
    catalog.setPhotoMeta("p", { model: "ILCE-7CM2" }, "ready");
    expect(catalog.getPhoto("p")).toMatchObject({
      width: 6000, height: 4000, make: "SONY", model: "ILCE-7CM2", iso: 100, lens: null, thumbState: "ready",
    });
  });

  it("moves and deletes in bulk, reporting what was there", () => {
    const a = catalog.createFolder(ROOT_FOLDER_ID, "A");
    addPhoto("p1");
    addPhoto("p2");
    expect(catalog.movePhotos(["p1", "p2", "ghost"], a.id)).toBe(2);
    expect(catalog.listPhotos(a.id).map(p => p.id)).toEqual(["p1", "p2"]);
    expect(catalog.deletePhotos(["p1", "ghost"])).toEqual(["p1"]);
    expect(catalog.photoIds()).toEqual(new Set(["p2"]));
  });

  it("rejects an unknown folder", () => {
    expect(() => addPhoto("p", 999)).toThrow(CatalogError);
    addPhoto("q");
    expect(() => catalog.movePhotos(["q"], 999)).toThrow(CatalogError);
  });
});

describe("edits", () => {
  it("round-trips and upserts", () => {
    addPhoto("p");
    expect(catalog.getEdit("p")).toBeNull();
    catalog.putEdit("p", { snapshot: { a: 1 }, history: [{ a: 0 }, { a: 1 }], historyIndex: 1 }, 5);
    expect(catalog.getEdit("p")).toEqual({ snapshot: { a: 1 }, history: [{ a: 0 }, { a: 1 }], historyIndex: 1, updatedAt: 5 });
    catalog.putEdit("p", { snapshot: { a: 2 }, history: [{ a: 2 }], historyIndex: 0 }, 6);
    expect(catalog.getEdit("p")).toMatchObject({ snapshot: { a: 2 }, updatedAt: 6 });
  });

  it("refuses an edit for a photo that is not in the catalog", () => {
    expect(() => catalog.putEdit("nope", { snapshot: {}, history: [], historyIndex: 0 })).toThrow(CatalogError);
  });
});

describe("meta", () => {
  it("stores flags", () => {
    expect(catalog.get("legacy_adopted")).toBeNull();
    catalog.set("legacy_adopted", "1");
    catalog.set("legacy_adopted", "2");
    expect(catalog.get("legacy_adopted")).toBe("2");
  });
});
