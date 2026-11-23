import { existsSync, mkdirSync, mkdtempSync, readdirSync, rmSync, utimesSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { markRemoved, SCRATCH_TTL_MS, sweepSessions, TRASH_TTL_MS } from "../sweep.js";

const A = "0058441c-3576-45b4-9351-7c384dbcf111";
const B = "027b1ce0-452b-4018-8b67-d5fd43121247";
const HOUR = 60 * 60 * 1000;
const NOW = Date.UTC(2026, 10, 24, 12);

let root: string;
let sessions: string;
let trash: string;
beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), "llr-sweep-"));
  sessions = join(root, "sessions");
  trash = join(root, "trash");
  mkdirSync(sessions);
});
afterEach(() => { rmSync(root, { recursive: true, force: true }); });

/** A photo's directory as an import leaves it, last touched `ageMs` ago. */
function photoDir(name: string, ageMs: number, files: string[] = ["source.arw", "embedded.jpg", "thumb.jpg"]): string {
  const dir = join(sessions, name);
  mkdirSync(dir);
  for (const f of files) writeFileSync(join(dir, f), f);
  const at = (NOW - ageMs) / 1000;
  for (const f of files) utimesSync(join(dir, f), at, at);
  utimesSync(dir, at, at);
  return dir;
}
const sweep = (known: Set<string> | null, now = NOW) => sweepSessions({ sessionsRoot: sessions, trashRoot: trash, known, now });

describe("sweepSessions", () => {
  it("leaves a photo the catalog names alone, and clears only its stale scratch", async () => {
    const dir = photoDir(A, 3 * HOUR, ["source.arw", "cache-abc.f16", "linear-old.bin", "export-old.jpg"]);
    writeFileSync(join(dir, "linear-new.bin"), "in flight");
    utimesSync(join(dir, "linear-new.bin"), NOW / 1000, NOW / 1000);
    const result = await sweep(new Set([A]));
    expect(result).toEqual({ deleted: [], trashed: [], expired: [] });
    expect(readdirSync(dir).sort()).toEqual(["cache-abc.f16", "linear-new.bin", "source.arw"]);
  });

  it("finishes a removal that was meant: a marked orphan is deleted", async () => {
    const dir = photoDir(A, 3 * HOUR);
    await markRemoved(dir);
    utimesSync(dir, (NOW - 3 * HOUR) / 1000, (NOW - 3 * HOUR) / 1000);
    const result = await sweep(new Set());
    expect(result.deleted).toEqual([A]);
    expect(existsSync(dir)).toBe(false);
    expect(existsSync(trash)).toBe(false);
  });

  it("moves an orphan nobody asked to delete into the trash, without its decode cache", async () => {
    // A catalog restored from yesterday's backup does not list today's import.
    photoDir(A, 3 * HOUR, ["source.arw", "embedded.jpg", "thumb.jpg", "edited.jpg", "cache-abc.f16", "cache-abc.json", "aimask-1.npz"]);
    const result = await sweep(new Set([B]));
    expect(result.trashed).toEqual([A]);
    expect(existsSync(join(sessions, A))).toBe(false);
    expect(readdirSync(join(trash, A)).sort()).toEqual([".trashed", "edited.jpg", "embedded.jpg", "source.arw", "thumb.jpg"]);
  });

  it("waits an hour before touching an orphan: it may be an import in flight", async () => {
    const dir = photoDir(A, SCRATCH_TTL_MS / 2);
    expect(await sweep(new Set())).toEqual({ deleted: [], trashed: [], expired: [] });
    expect(existsSync(dir)).toBe(true);
  });

  it("does not touch a directory this server would not have named", async () => {
    const dir = photoDir("holiday-2019", 30 * 24 * HOUR, ["IMG_0001.CR2"]);
    expect(await sweep(new Set())).toEqual({ deleted: [], trashed: [], expired: [] });
    expect(readdirSync(dir)).toEqual(["IMG_0001.CR2"]);
  });

  it("sweeps only scratch while the catalog cannot yet say what it owns", async () => {
    const dir = photoDir(A, 3 * HOUR, ["source.arw", "linear-old.bin"]);
    expect(await sweep(null)).toEqual({ deleted: [], trashed: [], expired: [] });
    expect(readdirSync(dir)).toEqual(["source.arw"]);
  });

  it("keeps the trash for a week, counted from when the photo went in", async () => {
    photoDir(A, 400 * 24 * HOUR);   // a year-old import: its own dates say nothing
    await sweep(new Set());
    expect((await sweep(new Set(), NOW + TRASH_TTL_MS - HOUR)).expired).toEqual([]);
    expect(existsSync(join(trash, A))).toBe(true);
    expect((await sweep(new Set(), NOW + TRASH_TTL_MS + HOUR)).expired).toEqual([A]);
    expect(existsSync(join(trash, A))).toBe(false);
  });

  it("does nothing, quietly, when there is no sessions directory yet", async () => {
    rmSync(sessions, { recursive: true });
    const warnings: string[] = [];
    const result = await sweepSessions({ sessionsRoot: sessions, trashRoot: trash, known: new Set(), now: NOW, warn: m => warnings.push(m) });
    expect(result).toEqual({ deleted: [], trashed: [], expired: [] });
    expect(warnings).toEqual([]);
  });
});
