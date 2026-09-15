import { describe, expect, it } from "vitest";

import { buildTree, descendantIds, pathOf, visibleRows, type FolderRow } from "../catalogTree";

const rows: FolderRow[] = [
  { id: 1, parentId: null, name: "Library", count: 2 },
  { id: 2, parentId: 1, name: "trips", count: 0 },
  { id: 3, parentId: 2, name: "Kyoto", count: 5 },
  { id: 4, parentId: 2, name: "berlin", count: 3 },
  { id: 5, parentId: 1, name: "Family", count: 1 },
];

describe("buildTree", () => {
  it("nests, sorts children case-insensitively and sums subtree totals", () => {
    const [root] = buildTree(rows);
    expect(root.id).toBe(1);
    expect(root.total).toBe(11);
    expect(root.children.map(c => c.name)).toEqual(["Family", "trips"]);
    const trips = root.children[1];
    expect(trips.children.map(c => [c.name, c.depth])).toEqual([["berlin", 2], ["Kyoto", 2]]);
    expect(trips.total).toBe(8);
  });

  it("treats a row whose parent is missing as a root", () => {
    const roots = buildTree([{ id: 9, parentId: 42, name: "orphan", count: 0 }]);
    expect(roots.map(r => r.id)).toEqual([9]);
  });
});

describe("visibleRows", () => {
  it("flattens only expanded folders", () => {
    const tree = buildTree(rows);
    expect(visibleRows(tree, new Set()).map(n => n.id)).toEqual([1]);
    expect(visibleRows(tree, new Set([1])).map(n => n.name)).toEqual(["Library", "Family", "trips"]);
    expect(visibleRows(tree, new Set([1, 2])).map(n => n.name)).toEqual(["Library", "Family", "trips", "berlin", "Kyoto"]);
  });
});

describe("descendantIds / pathOf", () => {
  it("covers the subtree including the folder itself", () => {
    expect([...descendantIds(rows, 2)].sort()).toEqual([2, 3, 4]);
    expect([...descendantIds(rows, 3)]).toEqual([3]);
  });

  it("walks the ancestry root-first", () => {
    expect(pathOf(rows, 3).map(r => r.name)).toEqual(["Library", "trips", "Kyoto"]);
    expect(pathOf(rows, 99)).toEqual([]);
  });
});
