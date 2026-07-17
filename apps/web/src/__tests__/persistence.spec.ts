/**
 * Persistence layer: v1→v2 migration, history trimming, index merging,
 * orphan cleanup.
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

// Raw reads/writes straight from the fake DB, bypassing the module under test
// (rawPut stands in for another tab writing the shared store).
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

function rawPut(key: string, value: unknown): Promise<void> {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open("llr", 1);
    open.onupgradeneeded = () => { open.result.createObjectStore("kv"); };
    open.onsuccess = () => {
      const tx = open.result.transaction("kv", "readwrite");
      tx.objectStore("kv").put(value, key);
      tx.oncomplete = () => { open.result.close(); resolve(); };
      tx.onerror = () => reject(tx.error);
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
    await p.saveEdit("a", { snapshot: { v: 55 }, history, historyIndex: 55 });

    const stored = await rawGet<{ snapshot: { v: number }; history: { v: number }[]; historyIndex: number }>("llr.edit.a");
    expect(stored!.history).toHaveLength(50);
    expect(stored!.history[0]).toEqual({ v: 10 }); // oldest 10 dropped
    expect(stored!.historyIndex).toBe(45); // 55 - 10
    expect(stored!.history[stored!.historyIndex]).toEqual(stored!.snapshot);
  });

  it("keeps the snapshot's own entry inside the window when the index is old", async () => {
    const p = await loadModule();
    const history = Array.from({ length: 60 }, (_, i) => ({ v: i }));
    // Held Ctrl+Z back to entry 5: the last-50 window would have dropped it.
    await p.saveEdit("a", { snapshot: { v: 5 }, history, historyIndex: 5 });

    const stored = await rawGet<{ snapshot: { v: number }; history: { v: number }[]; historyIndex: number }>("llr.edit.a");
    expect(stored!.history).toHaveLength(50);
    expect(stored!.history[stored!.historyIndex]).toEqual(stored!.snapshot);
    expect(stored!.history[0]).toEqual({ v: 5 }); // oldest 5 dropped, no more
  });

  it("leaves short histories untouched", async () => {
    const p = await loadModule();
    await p.saveEdit("a", edit(1));
    const stored = await rawGet<{ history: unknown[] }>("llr.edit.a");
    expect(stored!.history).toHaveLength(1);
  });
});

describe("session index merging", () => {
  const other = { id: "b", name: "b.arw", size: 2, embeddedUrl: "/sources/b/embedded.jpg" };

  it("keeps sources another tab imported since this tab read the index", async () => {
    const p = await loadModule();
    await p.saveSession({ version: 2, activeId: "a", viewSettings: {}, sources: [source] });
    await rawPut(SESSION_KEY, { version: 2, activeId: "b", viewSettings: {}, sources: [source, other] });

    // This tab never saw "b", but writing its own library must not evict it.
    await p.saveSession({ version: 2, activeId: "a", viewSettings: { z: 3 }, sources: [source] });

    const stored = await rawGet<{ activeId: string; viewSettings: { z: number }; sources: { id: string }[] }>(SESSION_KEY);
    expect(stored!.sources.map(s => s.id)).toEqual(["a", "b"]);
    expect(stored!.activeId).toBe("a"); // the writing tab still owns activeId + view settings
    expect(stored!.viewSettings).toEqual({ z: 3 });
  });

  it("does not resurrect sources this tab removed", async () => {
    const p = await loadModule();
    await rawPut(SESSION_KEY, { version: 2, activeId: "a", viewSettings: {}, sources: [source, other] });

    await p.saveSession({ version: 2, activeId: "a", viewSettings: {}, sources: [source] }, ["b"]);

    const stored = await rawGet<{ sources: { id: string }[] }>(SESSION_KEY);
    expect(stored!.sources.map(s => s.id)).toEqual(["a"]);
  });
});

describe("orphan edit cleanup", () => {
  it("keeps an edit missing from the index — another tab may own it", async () => {
    const p = await loadModule();
    await p.saveSession({ version: 2, activeId: "a", viewSettings: {}, sources: [source] });
    await p.saveEdit("a", edit(1));
    await p.saveEdit("ghost", edit(2));

    const loaded = await p.loadSession();
    expect(Object.keys(loaded!.edits)).toEqual(["a"]);
    await vi.waitFor(async () => {
      expect(await rawGet("llr.orphans.v1")).toBeDefined();
    });
    expect(await rawGet("llr.edit.ghost")).toBeDefined();
  });

  it("drops an edit that has stayed unreferenced past the TTL", async () => {
    const p = await loadModule();
    await p.saveSession({ version: 2, activeId: "a", viewSettings: {}, sources: [source] });
    await p.saveEdit("ghost", edit(2));
    await rawPut("llr.orphans.v1", { ghost: Date.now() - 8 * 24 * 60 * 60 * 1000 });

    await p.loadSession();
    await vi.waitFor(async () => {
      expect(await rawGet("llr.edit.ghost")).toBeUndefined();
    });
  });

  it("forgets an orphan that reappears in the index", async () => {
    const p = await loadModule();
    await p.saveSession({ version: 2, activeId: "a", viewSettings: {}, sources: [source] });
    await p.saveEdit("a", edit(1));
    await rawPut("llr.orphans.v1", { a: Date.now() - 8 * 24 * 60 * 60 * 1000 });

    await p.loadSession();
    await vi.waitFor(async () => {
      expect(await rawGet("llr.orphans.v1")).toEqual({});
    });
    expect(await rawGet("llr.edit.a")).toBeDefined();
  });
});
