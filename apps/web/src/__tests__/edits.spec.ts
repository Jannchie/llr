import { describe, expect, it } from "vitest";

import { fitForKeepalive, trimEdit, type PersistedEdit } from "../edits";

function edit(n: number, index = n - 1, pad = 0): PersistedEdit<{ i: number; pad: string }> {
  const history = Array.from({ length: n }, (_, i) => ({ i, pad: "x".repeat(pad) }));
  return { snapshot: history[index], history, historyIndex: index };
}

describe("trimEdit", () => {
  it("returns the record untouched when it fits", () => {
    const e = edit(10);
    expect(trimEdit(e, 10)).toBe(e);
  });

  it("keeps the newest entries and re-bases the index", () => {
    const out = trimEdit(edit(100), 50);
    expect(out.history).toHaveLength(50);
    expect(out.history[0].i).toBe(50);
    expect(out.historyIndex).toBe(49);
  });

  it("never drops the entry the snapshot sits on", () => {
    // Undone back to entry 3 of 100: the window must start at 3, not 50.
    const out = trimEdit(edit(100, 3), 50);
    expect(out.history[0].i).toBe(3);
    expect(out.historyIndex).toBe(0);
    expect(out.history[out.historyIndex]).toEqual(out.snapshot);
  });
});

describe("fitForKeepalive", () => {
  it("drops history until the JSON fits the budget", () => {
    const e = edit(40, 39, 1000); // ~40 KB of history
    const out = fitForKeepalive(e, 10_000);
    expect(JSON.stringify(out).length).toBeLessThanOrEqual(10_000);
    expect(out.history[out.historyIndex]).toEqual(out.snapshot);
    expect(out.history.length).toBeGreaterThan(0);
  });

  it("bottoms out at the snapshot's own entry", () => {
    const e = edit(4, 3, 5000);
    const out = fitForKeepalive(e, 100);
    expect(out.history).toHaveLength(1);
    expect(out.historyIndex).toBe(0);
    expect(out.history[0]).toEqual(out.snapshot);
  });
});
