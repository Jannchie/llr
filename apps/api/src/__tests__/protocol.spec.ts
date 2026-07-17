import { describe, expect, it } from "vitest";

import {
  buildLinearFrameHeader,
  clampRenderParams,
  isAllowedHost,
  isJsonContentType,
  isLocalOrigin,
  isValidSourceId,
  pickExtension,
} from "../protocol.js";

describe("isValidSourceId", () => {
  it("accepts server-minted UUIDs", () => {
    expect(isValidSourceId("d4c0ffee-1234-4abc-9def-000000000000")).toBe(true);
    expect(isValidSourceId("plain_id-1")).toBe(true);
  });

  it("rejects traversal and absolute paths before they reach resolve()", () => {
    for (const id of ["../x", "..", "a/b", "/etc/passwd", "a\\b", "id.", "", "a b", "%2e%2e"]) {
      expect(isValidSourceId(id), id).toBe(false);
    }
  });
});

describe("clampRenderParams", () => {
  it("fills defaults for an empty body", () => {
    expect(clampRenderParams({})).toEqual({
      profile: "standard",
      halfSize: true,
      maxSize: 1600,
      dcpCode: undefined,
      denoise: { enabled: false, model: undefined, amount: 1 },
    });
  });

  it("clamps out-of-range and malformed numbers instead of forwarding them", () => {
    expect(clampRenderParams({ maxSize: 99999 }).maxSize).toBe(16384);
    expect(clampRenderParams({ maxSize: -5 }).maxSize).toBe(0);
    expect(clampRenderParams({ maxSize: 1024.9 }).maxSize).toBe(1024);
    expect(clampRenderParams({ maxSize: Number.NaN }).maxSize).toBe(1600);
    expect(clampRenderParams({ denoise: { amount: 7 } }).denoise.amount).toBe(1);
    expect(clampRenderParams({ denoise: { amount: -1 } }).denoise.amount).toBe(0);
  });

  it("only accepts typed fields", () => {
    const params = clampRenderParams({
      dcpCode: 5 as unknown as string,
      halfSize: "yes" as unknown as boolean,
      denoise: { enabled: 1 as unknown as boolean, model: 3 as unknown as string },
    });
    expect(params.dcpCode).toBeUndefined();
    expect(params.halfSize).toBe(true);
    expect(params.denoise.enabled).toBe(false);
    expect(params.denoise.model).toBeUndefined();
  });
});

describe("buildLinearFrameHeader", () => {
  const meta = { width: 100, height: 50, fullWidth: 200, fullHeight: 100, colorProfile: { kind: "dcp" }, dtype: "float16" };

  it("frames a 4-byte-aligned JSON header behind a u32 length prefix", () => {
    const frame = buildLinearFrameHeader(meta);
    const headerLen = frame.readUInt32BE(0);
    expect(headerLen).toBe(frame.length - 4);
    expect(headerLen % 4).toBe(0);
    expect((4 + headerLen) % 4).toBe(0); // pixels start 4-byte aligned

    const parsed = JSON.parse(frame.subarray(4).toString("utf8")) as Record<string, unknown>;
    expect(parsed).toEqual({
      width: 100,
      height: 50,
      fullWidth: 200,
      fullHeight: 100,
      colorProfile: { kind: "dcp" },
      dtype: "float16",
    });
  });

  it("defaults dtype to float32 and missing dims to null", () => {
    const parsed = JSON.parse(buildLinearFrameHeader({ width: 1, height: 1 }).subarray(4).toString("utf8")) as Record<string, unknown>;
    expect(parsed.dtype).toBe("float32");
    expect(parsed.fullWidth).toBeNull();
    expect(parsed.colorProfile).toBeNull();
  });

  it("stays aligned across header lengths", () => {
    // Sweep name lengths so every padding remainder (0..3) is exercised.
    for (let i = 0; i < 8; i += 1) {
      const frame = buildLinearFrameHeader({ width: 1, height: 1, colorProfile: { name: "x".repeat(i) } });
      expect(frame.readUInt32BE(0) % 4).toBe(0);
      expect(() => JSON.parse(frame.subarray(4).toString("utf8"))).not.toThrow();
    }
  });
});

describe("isLocalOrigin", () => {
  it("allows localhost and 127.0.0.1 on any port", () => {
    expect(isLocalOrigin("http://localhost:5173")).toBe(true);
    expect(isLocalOrigin("http://127.0.0.1:8790")).toBe(true);
  });

  it("rejects other hosts, lookalikes, and garbage", () => {
    for (const origin of ["http://example.com", "http://localhost.evil.com", "http://[::1]:5173", "not a url", ""]) {
      expect(isLocalOrigin(origin), origin).toBe(false);
    }
  });
});

describe("isLocalOrigin", () => {
  it("keeps the cross-origin dev setup working", () => {
    // The web dev server is on 5180 and the API on 8790, so normal development
    // is cross-origin and must not be gated out.
    expect(isLocalOrigin("http://localhost:5180")).toBe(true);
  });

  it("rejects the origins a hostile page would send", () => {
    expect(isLocalOrigin("http://evil.example")).toBe(false);
    expect(isLocalOrigin("null")).toBe(false); // sandboxed iframe / file://
  });
});

describe("isAllowedHost", () => {
  it("accepts local hosts and the configured bind host", () => {
    expect(isAllowedHost("127.0.0.1:8790", "127.0.0.1")).toBe(true);
    expect(isAllowedHost("localhost:8790", "127.0.0.1")).toBe(true);
    expect(isAllowedHost("dev.box:8790", "dev.box")).toBe(true);
    expect(isAllowedHost(undefined, "127.0.0.1")).toBe(true); // HTTP/1.0 clients
  });

  it("rejects a rebound hostname pointed at the bind address", () => {
    expect(isAllowedHost("evil.example:8790", "127.0.0.1")).toBe(false);
    expect(isAllowedHost("localhost.evil.example", "127.0.0.1")).toBe(false);
    expect(isAllowedHost("", "127.0.0.1")).toBe(false);
  });
});

describe("isJsonContentType", () => {
  it("accepts what the real client sends", () => {
    expect(isJsonContentType("application/json")).toBe(true);
    expect(isJsonContentType("application/json; charset=utf-8")).toBe(true);
    expect(isJsonContentType("Application/JSON")).toBe(true);
  });

  it("rejects the CORS-simple types that dodge a preflight", () => {
    for (const value of ["text/plain", "multipart/form-data", "application/x-www-form-urlencoded", undefined, ""]) {
      expect(isJsonContentType(value), String(value)).toBe(false);
    }
  });
});

describe("pickExtension", () => {
  it("lowercases the final extension", () => {
    expect(pickExtension("IMG_0001.ARW")).toBe(".arw");
    expect(pickExtension("photo.final.DNG")).toBe(".dng");
  });

  it("returns empty for missing extensions and dotfiles", () => {
    expect(pickExtension("noext")).toBe("");
    expect(pickExtension(".bashrc")).toBe("");
  });
});
