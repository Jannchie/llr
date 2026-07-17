/**
 * Persistence layer: v1→v2 migration, history trimming, orphan cleanup.
 *
 * The module caches its IndexedDB connection at module scope, so every test
 * gets a fresh module instance (vi.resetModules) over a fresh fake-indexeddb
 * factory.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { IDBFactory, IDBKeyRange } from "fake-indexeddb";

type Persistence = typeof import("../persistence");

const SESSION_KEY = "llr.session.v2";
const STATE_KEY_V1 = "llr.state.v1";

function makeLocalStorage(): Storage {
  const map = new Map<string, string>();
  return {
    getItem: (k: string) => map.get(k) ?? null,
    setItem: (k: string, v: string) => void map.set(k, v),
    removeItem: (k: string) => void map.delete(k),
    clear: () => map.clear(),
    key: (i: number) => [...map.keys()][i] ?? null,
    get length() { return map.size; },
  } as Storage;
}

async function loadModule(): Promise<Persistence> {
  vi.resetModules();
  return await import("../persistence");
}

// Raw read straight from the fake DB, bypassing the module under test.
function rawGet<T>(key: string): Promise<T | undefined> {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open("llr", 1);
    open.onsuccess = () => {
      const req = open.result.transaction("kv", "readonly").objectStore("kv").get(key);
      req.onsuccess = () => { open.result.close(); resolve(req.result as T | undefined); };
      req.onerror = () => reject(req.error);
    };
    open.onerror = () => reject(open.error);
  });
}

function edit(n: number) {
  return { snapshot: { v: n }, history: [{ v: n }], historyIndex: 0 };
}

const source = { id: "a", name: "a.arw", size: 1, embeddedUrl: "/sources/a/embedded.jpg" };

beforeEach(() => {
  globalThis.indexedDB = new IDBFactory();
  globalThis.IDBKeyRange = IDBKeyRange;
  globalThis.localStorage = makeLocalStorage();
});

describe("session round-trip", () => {
  it("stores and reloads the v2 session with its per-image edits", async () => {
    const p = await loadModule();
    await p.saveSession({ version: 2, activeId: "a", viewSettings: { z: 1 }, sources: [source] });
    await p.saveEdit("a", edit(7));

    const loaded = await p.loadSession();
    expect(loaded).not.toBeNull();
    expect(loaded!.session.activeId).toBe("a");
    expect(loaded!.edits.a.snapshot).toEqual({ v: 7 });
  });

  it("returns null when nothing was ever persisted", async () => {
    const p = await loadModule();
    expect(await p.loadSession()).toBeNull();
  });
});

describe("v1 → v2 migration", () => {
  const v1State = {
    version: 1,
    activeId: "a",
    viewSettings: { z: 2 },
    sources: [source],
    edits: { a: edit(3) },
  };

  it("migrates a v1 localStorage record into per-key v2 records and drops it", async () => {
    localStorage.setItem(STATE_KEY_V1, JSON.stringify(v1State));
    const p = await loadModule();

    const loaded = await p.loadSession();
    expect(loaded!.session).toEqual({ version: 2, activeId: "a", viewSettings: { z: 2 }, sources: [source] });
    expect(loaded!.edits.a.snapshot).toEqual({ v: 3 });

    // v2 records exist on disk, the v1 record is gone.
    expect(await rawGet(SESSION_KEY)).toBeDefined();
    expect(await rawGet("llr.edit.a")).toBeDefined();
    expect(localStorage.getItem(STATE_KEY_V1)).toBeNull();

    // A reload takes the plain v2 path.
    const again = await (await loadModule()).loadSession();
    expect(again!.edits.a.snapshot).toEqual({ v: 3 });
  });

  it("rejects malformed v1 payloads instead of migrating garbage", async () => {
    localStorage.setItem(STATE_KEY_V1, JSON.stringify({ version: 1, sources: "nope" }));
    const p = await loadModule();
    expect(await p.loadSession()).toBeNull();
  });
});

describe("history trimming", () => {
  it("caps persisted undo history at 50 entries and shifts the index", async () => {
    const p = await loadModule();
    const history = Array.from({ length: 60 }, (_, i) => ({ v: i }));
    await p.saveEdit("a", { snapshot: { v: 59 }, history, historyIndex: 55 });

    const stored = await rawGet<{ history: { v: number }[]; historyIndex: number }>("llr.edit.a");
    expect(stored!.history).toHaveLength(50);
    expect(stored!.history[0]).toEqual({ v: 10 }); // oldest 10 dropped
    expect(stored!.historyIndex).toBe(45); // 55 - 10
  });

  it("clamps the index at 0 when it pointed into the dropped range", async () => {
    const p = await loadModule();
    const history = Array.from({ length: 60 }, (_, i) => ({ v: i }));
    await p.saveEdit("a", { snapshot: { v: 0 }, history, historyIndex: 5 });
    const stored = await rawGet<{ historyIndex: number }>("llr.edit.a");
    expect(stored!.historyIndex).toBe(0);
  });

  it("leaves short histories untouched", async () => {
    const p = await loadModule();
    await p.saveEdit("a", edit(1));
    const stored = await rawGet<{ history: unknown[] }>("llr.edit.a");
    expect(stored!.history).toHaveLength(1);
  });
});

describe("orphan edit cleanup", () => {
  it("drops edits whose source is no longer in the session index", async () => {
    const p = await loadModule();
    await p.saveSession({ version: 2, activeId: "a", viewSettings: {}, sources: [source] });
    await p.saveEdit("a", edit(1));
    await p.saveEdit("ghost", edit(2)); // e.g. remove persisted the index, tab died before deleteEdit

    const loaded = await p.loadSession();
    expect(Object.keys(loaded!.edits)).toEqual(["a"]);
    await vi.waitFor(async () => {
      expect(await rawGet("llr.edit.ghost")).toBeUndefined();
    });
  });
});
