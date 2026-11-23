import { mkdtempSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { Catalog, ROOT_FOLDER_ID } from "../catalog.js";
import { dailyBackupName, writeDailyBackup } from "../catalog-backup.js";

let dir: string;
beforeEach(() => { dir = mkdtempSync(join(tmpdir(), "llr-backup-")); });
afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

const day = (d: number) => new Date(2026, 10, d, 9, 30); // local time, like the name

describe("writeDailyBackup", () => {
  it("copies what is committed, including what is still only in the WAL", () => {
    const catalog = new Catalog(join(dir, "catalog.db"));
    catalog.insertPhoto({ id: "p", folderId: ROOT_FOLDER_ID, name: "p.arw", ext: ".arw", size: 10 }, 1000);
    catalog.putEdit("p", { snapshot: { exposure: 1 }, history: [], historyIndex: 0 }, 2000);

    const written = writeDailyBackup(catalog, join(dir, "backups"), day(23));
    expect(written).toBe(join(dir, "backups", "catalog-2026-11-23.db"));

    // The live database has not been closed or checkpointed; the copy stands alone.
    const copy = new Catalog(written!);
    expect(copy.getPhoto("p")).toMatchObject({ name: "p.arw" });
    expect(copy.getEdit("p")?.snapshot).toEqual({ exposure: 1 });
    copy.close();
    catalog.close();
  });

  it("writes one copy a day", () => {
    const catalog = new Catalog(":memory:");
    const backups = join(dir, "backups");
    expect(writeDailyBackup(catalog, backups, day(23))).not.toBeNull();
    expect(writeDailyBackup(catalog, backups, day(23))).toBeNull();
    expect(writeDailyBackup(catalog, backups, day(24))).not.toBeNull();
    expect(readdirSync(backups).sort()).toEqual(["catalog-2026-11-23.db", "catalog-2026-11-24.db"]);
    catalog.close();
  });

  it("keeps the newest few and leaves every other file alone", () => {
    const catalog = new Catalog(":memory:");
    const backups = join(dir, "backups");
    for (let d = 1; d <= 5; d++) writeDailyBackup(catalog, backups, day(d), 3);
    writeFileSync(join(backups, "catalog-before-schema-4-123.db"), "kept");
    writeFileSync(join(backups, "notes.txt"), "kept");
    writeDailyBackup(catalog, backups, day(6), 3);
    expect(readdirSync(backups).sort()).toEqual([
      "catalog-2026-11-04.db", "catalog-2026-11-05.db", "catalog-2026-11-06.db",
      "catalog-before-schema-4-123.db", "notes.txt",
    ]);
    catalog.close();
  });

  it("leaves no day's backup behind when the copy fails", () => {
    const backups = join(dir, "backups");
    const failing = { backupTo: (path: string) => { writeFileSync(path, "half"); throw new Error("disk full"); } };
    expect(() => writeDailyBackup(failing, backups, day(23))).toThrow("disk full");
    expect(readdirSync(backups)).toEqual([]);
    // ...so the next attempt that day still runs.
    const catalog = new Catalog(":memory:");
    expect(writeDailyBackup(catalog, backups, day(23))).not.toBeNull();
    catalog.close();
  });

  it("names the copy by the local date", () => {
    expect(dailyBackupName(new Date(2026, 0, 5, 23, 59))).toBe("catalog-2026-01-05.db");
  });
});

describe("Catalog schema guards", () => {
  it("copies the library before migrating it", () => {
    const path = join(dir, "catalog.db");
    const fresh = new Catalog(path);
    fresh.insertPhoto({ id: "p", folderId: ROOT_FOLDER_ID, name: "p.arw", ext: ".arw", size: 10 }, 1000);
    fresh.close();
    // Rewind to the schema before preview_at, as an older build left it.
    const raw = new DatabaseSync(path);
    raw.exec("ALTER TABLE photos DROP COLUMN preview_at");
    raw.exec("PRAGMA user_version = 2");
    raw.close();

    const backups = join(dir, "backups");
    const migrated = new Catalog(path, backups);
    migrated.close();
    const [copy] = readdirSync(backups);
    expect(copy).toMatch(/^catalog-before-schema-\d+-\d+\.db$/);
    const before = new DatabaseSync(join(backups, copy));
    expect((before.prepare("PRAGMA user_version").get() as { user_version: number }).user_version).toBe(2);
    expect(before.prepare("SELECT name FROM photos").all()).toEqual([{ name: "p.arw" }]);
    before.close();
  });

  it("does not copy a library that needs no migration, or a new one", () => {
    const backups = join(dir, "backups");
    new Catalog(join(dir, "catalog.db"), backups).close();   // created
    new Catalog(join(dir, "catalog.db"), backups).close();   // current
    expect(() => readdirSync(backups)).toThrow();            // never made
  });

  it("refuses a library a newer build has migrated", () => {
    const path = join(dir, "catalog.db");
    new Catalog(path).close();
    const raw = new DatabaseSync(path);
    raw.exec("PRAGMA user_version = 999");
    raw.close();
    expect(() => new Catalog(path)).toThrow(/newer LLR/);
  });
});
