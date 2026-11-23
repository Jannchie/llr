// Provider keys entered in the browser. pi-ai resolves auth through a
// CredentialStore, and a stored credential owns its provider: the environment
// (ANTHROPIC_API_KEY, …) is only consulted when nothing is stored. This store
// is one JSON file beside the catalog, so a key outlives the server; until
// open() names that file it is memory only, which is all the tests need.
//
// The file holds the keys in the clear, like an .env would. It is written
// owner-only and never served: the browser can replace or remove a key, and is
// told only that one is there (see agent.ts keyHint).
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

import type { Credential, CredentialInfo, CredentialStore } from "@earendil-works/pi-ai";

type Stored = Record<string, Credential>;

export class FileCredentialStore implements CredentialStore {
  private path: string | undefined;
  private data: Promise<Stored> | undefined;
  // One file, so writes queue behind each other whatever the provider.
  private tail: Promise<unknown> = Promise.resolve();

  /** Keep the credentials in this file from now on, starting from what it holds. */
  open(path: string): void {
    this.path = path;
    this.data = undefined;
  }

  private load(): Promise<Stored> {
    this.data ??= (async () => {
      if (!this.path) return {};
      let text: string;
      try {
        text = await readFile(this.path, "utf8");
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code === "ENOENT") return {};
        throw error;
      }
      try {
        const parsed: unknown = JSON.parse(text);
        if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return parsed as Stored;
      } catch { /* reported below */ }
      // Refusing beats overwriting: the next save would otherwise drop every key in it.
      throw new Error(`${this.path} is not a credentials file; fix or delete it`);
    })();
    // A failed read is retried by the next call rather than remembered.
    this.data.catch(() => { this.data = undefined; });
    return this.data;
  }

  private async save(next: Stored): Promise<void> {
    if (this.path) {
      await mkdir(dirname(this.path), { recursive: true });
      // Whole-file replace, so a crash mid-write leaves the old keys intact.
      const scratch = `${this.path}.${process.pid}.tmp`;
      await writeFile(scratch, `${JSON.stringify(next, null, 2)}\n`, { mode: 0o600 });
      await rename(scratch, this.path);
    }
    this.data = Promise.resolve(next);
  }

  private queue<T>(task: () => Promise<T>): Promise<T> {
    const run = this.tail.then(task, task);
    this.tail = run.catch(() => {});
    return run;
  }

  async read(providerId: string): Promise<Credential | undefined> {
    return (await this.load())[providerId];
  }

  async list(): Promise<readonly CredentialInfo[]> {
    return Object.entries(await this.load()).map(([providerId, credential]) => ({ providerId, type: credential.type }));
  }

  modify(providerId: string, fn: (current: Credential | undefined) => Promise<Credential | undefined>): Promise<Credential | undefined> {
    return this.queue(async () => {
      const stored = await this.load();
      const next = await fn(stored[providerId]);
      if (next === undefined) return stored[providerId];
      await this.save({ ...stored, [providerId]: next });
      return next;
    });
  }

  delete(providerId: string): Promise<void> {
    return this.queue(async () => {
      const { [providerId]: gone, ...rest } = await this.load();
      if (gone) await this.save(rest);
    });
  }
}
