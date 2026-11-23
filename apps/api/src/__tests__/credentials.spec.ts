import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { agentCatalog, clearProviderKey, setProviderKey } from "../agent.js";
import { FileCredentialStore } from "../credentials.js";

describe("credential file", () => {
  let dir: string;
  let file: string;
  const open = () => {
    const store = new FileCredentialStore();
    store.open(file);
    return store;
  };
  beforeEach(async () => {
    dir = await mkdtemp(join(tmpdir(), "llr-credentials-"));
    file = join(dir, "nested", "credentials.json");
  });
  afterEach(() => rm(dir, { recursive: true, force: true }));

  it("keeps a key across a restart and forgets it on delete", async () => {
    const store = open();
    expect(await store.read("anthropic")).toBeUndefined();
    await store.modify("anthropic", async () => ({ type: "api_key", key: "sk-one" }));
    await store.modify("openai", async () => ({ type: "api_key", key: "sk-two" }));

    const reopened = open();
    expect(await reopened.read("anthropic")).toEqual({ type: "api_key", key: "sk-one" });
    expect(await reopened.list()).toEqual([{ providerId: "anthropic", type: "api_key" }, { providerId: "openai", type: "api_key" }]);

    await reopened.delete("anthropic");
    expect(await open().list()).toEqual([{ providerId: "openai", type: "api_key" }]);
  });

  it("hands modify the current credential and leaves it be on undefined", async () => {
    const store = open();
    await store.modify("anthropic", async () => ({ type: "api_key", key: "sk-one" }));
    const seen: unknown[] = [];
    const after = await store.modify("anthropic", async current => { seen.push(current); return undefined; });
    expect(seen).toEqual([{ type: "api_key", key: "sk-one" }]);
    expect(after).toEqual({ type: "api_key", key: "sk-one" });
  });

  it("runs concurrent writes one after another, losing none", async () => {
    const store = open();
    await Promise.all(["a", "b", "c", "d"].map(id => store.modify(id, async () => ({ type: "api_key", key: id }))));
    expect(Object.keys(JSON.parse(await readFile(file, "utf8")))).toEqual(["a", "b", "c", "d"]);
  });

  it("refuses a file it cannot read as credentials instead of overwriting it", async () => {
    await open().modify("anthropic", async () => ({ type: "api_key", key: "sk-one" }));
    await writeFile(file, "{ not json");
    const store = open();
    await expect(store.read("anthropic")).rejects.toThrow(/not a credentials file/);
    await expect(store.modify("openai", async () => ({ type: "api_key", key: "sk-two" }))).rejects.toThrow(/not a credentials file/);
    expect(await readFile(file, "utf8")).toBe("{ not json");
  });
});

// The agent's own store is never opened here, so these stay in memory.
describe("provider keys from the browser", () => {
  const entry = async (id: string) => (await agentCatalog()).find(p => p.id === id)!;

  it("saves a key, reports only its tail, and forgets it again", async () => {
    await setProviderKey("deepseek", "sk-test-0123456789abcd");
    const saved = await entry("deepseek");
    expect(saved).toMatchObject({ hasKey: true, keyHint: "abcd", takesKey: true });
    expect(saved.keySource).toBeUndefined();
    expect(JSON.stringify(await agentCatalog())).not.toContain("0123456789");

    await setProviderKey("deepseek", "sk-test-replaced-wxyz");
    expect((await entry("deepseek")).keyHint).toBe("wxyz");

    await clearProviderKey("deepseek");
    expect((await entry("deepseek")).keyHint).toBeUndefined();
  });

  it("refuses a provider it does not know or that only signs in", async () => {
    await expect(setProviderKey("nope", "sk-x")).rejects.toThrow(/Unknown provider/);
    await expect(clearProviderKey("nope")).rejects.toThrow(/Unknown provider/);
    await expect(setProviderKey("openai-codex", "sk-x")).rejects.toThrow(/does not take an API key/);
    expect((await entry("openai-codex")).takesKey).toBe(false);
  });
});
