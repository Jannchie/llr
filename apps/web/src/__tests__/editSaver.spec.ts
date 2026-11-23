import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createEditSaver, SAVE_RETRY_MS } from "../editSaver";
import type { PersistedEdit } from "../edits";

type Snap = { exposure: number };
const edit = (exposure: number, history = 1): PersistedEdit<Snap> => ({
  snapshot: { exposure }, history: Array.from({ length: history }, () => ({ exposure })), historyIndex: history - 1,
});
const httpError = (status: number) => Object.assign(new Error(`HTTP ${status}`), { status });

/** A server that can be taken down and brought back. */
function harness() {
  const stored = new Map<string, PersistedEdit<Snap>>();
  const edits = new Map<string, PersistedEdit<Snap>>();
  const state = { down: null as Error | null, calls: 0, failures: 0 };
  const saver = createEditSaver<Snap>({
    edit: id => edits.get(id),
    put: async (id, e) => {
      state.calls += 1;
      if (state.down) throw state.down;
      stored.set(id, e);
    },
    onFailing: () => { state.failures += 1; },
  });
  return { saver, stored, edits, state };
}

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); });
const settle = () => vi.advanceTimersByTimeAsync(0);

describe("createEditSaver", () => {
  it("saves what is dirty and nothing else", async () => {
    const { saver, stored, edits, state } = harness();
    edits.set("a", edit(1));
    edits.set("b", edit(2));
    saver.mark("a");
    saver.flush();
    await settle();
    expect([...stored.keys()]).toEqual(["a"]);
    saver.flush();
    await settle();
    expect(state.calls).toBe(1);
    expect(saver.failing.value).toBe(false);
  });

  it("keeps retrying a failed save until it lands, and says so meanwhile", async () => {
    const { saver, stored, edits, state } = harness();
    edits.set("a", edit(1));
    state.down = new TypeError("Failed to fetch");
    saver.mark("a");
    saver.flush();
    await settle();
    expect(saver.failing.value).toBe(true);
    expect(state.failures).toBe(1);

    // Still down at the first retry: no second announcement, a longer wait.
    await vi.advanceTimersByTimeAsync(SAVE_RETRY_MS[0]);
    expect(state.calls).toBe(2);
    expect(state.failures).toBe(1);

    // The edit moved on while the server was away; what lands is the latest.
    edits.set("a", edit(5));
    state.down = null;
    await vi.advanceTimersByTimeAsync(SAVE_RETRY_MS[1]);
    expect(stored.get("a")?.snapshot).toEqual({ exposure: 5 });
    expect(saver.failing.value).toBe(false);
  });

  it("does not call a failure resolved because another photo saved", async () => {
    const stored = new Set<string>();
    const refused = new Set(["a"]);
    const saver = createEditSaver<Snap>({
      edit: () => edit(1),
      put: async (id) => { if (refused.has(id)) throw httpError(500); stored.add(id); },
    });
    saver.mark("a");
    saver.flush();
    await settle();
    expect(saver.failing.value).toBe(true);
    // "a" is waiting on its retry; "b" saves fine in the meantime.
    saver.mark("b");
    saver.flush();
    await settle();
    expect(stored.has("b")).toBe(true);
    expect(saver.failing.value).toBe(true);
    refused.clear();
    await vi.advanceTimersByTimeAsync(SAVE_RETRY_MS[0]);
    expect(stored.has("a")).toBe(true);
    expect(saver.failing.value).toBe(false);
  });

  it("retries on demand without waiting for the timer", async () => {
    const { saver, stored, edits, state } = harness();
    edits.set("a", edit(1));
    state.down = httpError(503);
    saver.mark("a");
    saver.flush();
    await settle();
    state.down = null;
    saver.retry();
    await settle();
    expect(stored.has("a")).toBe(true);
    expect(saver.failing.value).toBe(false);
    // The timer that was pending is gone with it.
    await vi.advanceTimersByTimeAsync(60_000);
    expect(state.calls).toBe(2);
  });

  it("drops a photo that is no longer in the library instead of retrying forever", async () => {
    const { saver, edits, state } = harness();
    edits.set("gone", edit(1));
    state.down = httpError(404);
    saver.mark("gone");
    saver.flush();
    await settle();
    expect(saver.failing.value).toBe(false);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(state.calls).toBe(1);
  });

  it("cuts the history down when the server says the edit is too large", async () => {
    // Each snapshot carries a few KB (brush strokes do), so forty of them are
    // well past what a keepalive-sized body holds.
    type Heavy = { strokes: string };
    const heavy: PersistedEdit<Heavy> = {
      snapshot: { strokes: "x".repeat(4_000) },
      history: Array.from({ length: 40 }, () => ({ strokes: "x".repeat(4_000) })),
      historyIndex: 39,
    };
    const bodies: PersistedEdit<Heavy>[] = [];
    const saver = createEditSaver<Heavy>({
      edit: () => heavy,
      put: async (_id, e) => {
        bodies.push(e);
        if (JSON.stringify(e).length > 100_000) throw httpError(413);   // the server's body limit
      },
    });
    saver.mark("a");
    saver.flush();
    await settle();
    expect(bodies).toHaveLength(2);
    expect(bodies[0].history).toHaveLength(40);
    // The second body is the same edit with less undo behind it.
    expect(bodies[1].history.length).toBeLessThan(40);
    expect(bodies[1].snapshot).toEqual(heavy.snapshot);
    expect(saver.failing.value).toBe(false);
  });

  it("forgets a removed photo's pending save", async () => {
    const { saver, edits, state } = harness();
    edits.set("a", edit(1));
    state.down = httpError(500);
    saver.mark("a");
    saver.flush();
    await settle();
    expect(saver.failing.value).toBe(true);
    saver.forget("a");
    expect(saver.failing.value).toBe(false);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(state.calls).toBe(1);
  });

  it("sends a keepalive-sized body when the tab closes", () => {
    const sent: { keepalive?: boolean }[] = [];
    const saver = createEditSaver<Snap>({
      edit: () => edit(1),
      put: async (_id, _e, init) => { sent.push(init ?? {}); },
    });
    saver.mark("a");
    saver.flushOnUnload();
    expect(sent).toEqual([{ keepalive: true }]);
  });
});
