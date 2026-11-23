import { describe, expect, it } from "vitest";
import { activityOf, decodingFor, IDLE, type DecodeState } from "../activity";

const quiet = { importing: false, exporting: false };

describe("activityOf", () => {
  it("reports the frame's own state when nothing is in flight", () => {
    expect(activityOf(IDLE, quiet)).toEqual({ state: "idle" });
    expect(activityOf({ phase: "ready", ms: 812 }, quiet)).toEqual({ state: "ready", ms: 812 });
    expect(activityOf({ phase: "failed" }, quiet)).toEqual({ state: "failed" });
  });

  it("puts a decode ahead of an export and an export ahead of an import", () => {
    const decoding: DecodeState = { phase: "decoding", cause: "open" };
    expect(activityOf(decoding, { importing: true, exporting: true })).toEqual({ state: "decoding" });
    expect(activityOf({ phase: "ready", ms: 5 }, { importing: true, exporting: true })).toEqual({ state: "exporting" });
    expect(activityOf({ phase: "ready", ms: 5 }, { importing: true, exporting: false })).toEqual({ state: "importing" });
  });

  it("lets a finished import or export fall back to the decode it did not own", () => {
    // The old shared status let an import finishing over a running decode
    // flip it to idle; here the decode stays what loadSource last said.
    const decoding: DecodeState = { phase: "decoding", cause: "erase" };
    expect(activityOf(decoding, quiet)).toEqual({ state: "decoding" });
    expect(activityOf({ phase: "failed" }, { importing: true, exporting: false })).toEqual({ state: "importing" });
  });
});

describe("decodingFor", () => {
  it("is busy only for the cause that started the decode in flight", () => {
    const erase: DecodeState = { phase: "decoding", cause: "erase" };
    expect(decodingFor(erase, "erase")).toBe(true);
    expect(decodingFor(erase, "denoise")).toBe(false);
    expect(decodingFor({ phase: "ready", ms: 1 }, "erase")).toBe(false);
  });
});
