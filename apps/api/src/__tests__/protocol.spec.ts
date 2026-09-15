import { describe, expect, it } from "vitest";

import {
  DRO_MAX_STRENGTH,
  buildLinearFrameHeader,
  clampDroStrength,
  clampRenderParams,
  isAllowedHost,
  isJsonContentType,
  isLocalOrigin,
  isValidFolderId,
  isValidSourceId,
  normalizeFolderName,
  pickExtension,
} from "../protocol.js";

describe("isValidFolderId", () => {
  it("accepts rowids only", () => {
    expect(isValidFolderId("1")).toBe(true);
    expect(isValidFolderId("123456789012")).toBe(true);
    for (const id of ["", "-1", "1.5", "a", "1234567890123", " 1"]) {
      expect(isValidFolderId(id), id).toBe(false);
    }
  });
});

describe("normalizeFolderName", () => {
  it("trims and bounds", () => {
    expect(normalizeFolderName("  Trip 2024 ")).toBe("Trip 2024");
    expect(normalizeFolderName("x".repeat(128))).toHaveLength(128);
    expect(normalizeFolderName("x".repeat(129))).toBeNull();
  });

  it("rejects empty, non-string and control characters", () => {
    for (const name of ["", "   ", 12, null, undefined, "a\nb", "a\u0000b"]) {
      expect(normalizeFolderName(name), String(name)).toBeNull();
    }
  });
});

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
    // toEqual, not toMatchObject: this pins the *whole* shape, so a field added
    // to RenderParams without a default fails here rather than reaching the
    // worker as undefined.
    expect(clampRenderParams({})).toEqual({
      profile: "standard",
      halfSize: true,
      maxSize: 1600,
      dcpCode: undefined,
      denoise: { enabled: false, auto: false, amount: 1, edge: 50, chroma: 50 },
      look: {},
      style: undefined,
      dro: undefined,
      droLevel: undefined,
      purpose: "preview",
    });
  });

  it("lands edge and colour on neutral when absent, and clamps them to 0..100", () => {
    // 50 is neutral for both, so an absent field must not fall to 0 — that would
    // silently mean "restore no detail" and "no colour noise reduction".
    expect(clampRenderParams({ denoise: {} }).denoise.edge).toBe(50);
    expect(clampRenderParams({ denoise: { edge: -20, chroma: 300 } }).denoise)
      .toMatchObject({ edge: 0, chroma: 100 });
    expect(clampRenderParams({ denoise: { edge: Number.NaN } }).denoise.edge).toBe(50);
  });

  it("treats anything but an explicit export as a cacheable preview decode", () => {
    expect(clampRenderParams({ purpose: "export" }).purpose).toBe("export");
    expect(clampRenderParams({ purpose: "preview" }).purpose).toBe("preview");
    expect(clampRenderParams({ purpose: "Export" }).purpose).toBe("preview");
    expect(clampRenderParams({ purpose: 1 as unknown as string }).purpose).toBe("preview");
  });

  it("keeps only the Creative Look keys, as whole numbers", () => {
    expect(clampRenderParams({ look: { highlights: -6, fade: 2.7 } }).look)
      .toEqual({ highlights: -6, fade: 2 });
    // An absent field means "leave the camera's own value alone", so a null or
    // a NaN has to drop out rather than reach the worker as a zero.
    expect(clampRenderParams({
      look: { shadows: null, contrast: Number.NaN, nonsense: 3 } as never,
    }).look).toEqual({});
    expect(clampRenderParams({ look: "all of them" as never }).look).toEqual({});
  });

  it("does not forward cameraMatch — the shader owns that table now", () => {
    expect(clampRenderParams({ cameraMatch: false } as never)).not.toHaveProperty("cameraMatch");
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
      denoise: { enabled: 1 as unknown as boolean },
    });
    expect(params.dcpCode).toBeUndefined();
    expect(params.halfSize).toBe(true);
    expect(params.denoise.enabled).toBe(false);
  });
});

describe("clampDroStrength", () => {
  it("distinguishes as-shot from off", () => {
    // Absent means "whatever the camera did", which the worker reads out of the
    // RAW. Zero means someone turned it off. Collapsing the two would silently
    // drop DRO from every render that did not mention it.
    expect(clampDroStrength(undefined)).toBeUndefined();
    expect(clampDroStrength(null)).toBeUndefined();
    expect(clampDroStrength(0)).toBe(0);
  });

  it("clamps to the range the worker will accept", () => {
    expect(clampDroStrength(1)).toBe(1);
    expect(clampDroStrength(2.5)).toBe(DRO_MAX_STRENGTH);
    expect(clampDroStrength(-3)).toBe(0);
    expect(clampDroStrength(Number.NaN)).toBeUndefined();
    expect(clampDroStrength("1.5")).toBe(1.5);
    expect(clampDroStrength("nope")).toBeUndefined();
  });

  it("rides through clampRenderParams", () => {
    expect(clampRenderParams({ dro: 9 }).dro).toBe(DRO_MAX_STRENGTH);
    expect(clampRenderParams({}).dro).toBeUndefined();
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
      denoiseUsesChroma: true,
    });
  });

  it("defaults dtype to float32 and missing dims to null", () => {
    const parsed = JSON.parse(buildLinearFrameHeader({ width: 1, height: 1 }).subarray(4).toString("utf8")) as Record<string, unknown>;
    expect(parsed.dtype).toBe("float32");
    expect(parsed.fullWidth).toBeNull();
    expect(parsed.colorProfile).toBeNull();
  });

  it("forwards denoiseUsesChroma, defaulting to true when the worker omits it", () => {
    // The UI hides Color NR on false, so the default has to be the safe
    // direction: an older worker that does not report it keeps showing a
    // control that works, rather than hiding one that does.
    const on = JSON.parse(buildLinearFrameHeader({ ...meta, denoiseUsesChroma: false }).subarray(4).toString("utf8")) as Record<string, unknown>;
    expect(on.denoiseUsesChroma).toBe(false);
    const missing = JSON.parse(buildLinearFrameHeader({ width: 1, height: 1 }).subarray(4).toString("utf8")) as Record<string, unknown>;
    expect(missing.denoiseUsesChroma).toBe(true);
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
    expect(isLocalOrigin("http://[::1]:5173")).toBe(true);
  });

  it("allows private-network IP literals (vite --host 0.0.0.0 opened by LAN IP)", () => {
    for (const origin of [
      "http://172.19.63.184:5180", // WSL seen from the Windows browser
      "http://192.168.1.10:5180",
      "http://10.255.255.254:5180",
      "http://100.100.7.7:5180", // Tailscale
    ]) {
      expect(isLocalOrigin(origin), origin).toBe(true);
    }
  });

  it("rejects public IPs, DNS lookalikes, and garbage", () => {
    for (const origin of [
      "http://example.com",
      "http://localhost.evil.com",
      "http://10.evil.com", // DNS name, not an IP literal
      "http://8.8.8.8:5180",
      "http://172.32.0.1:5180", // one past RFC1918's 172.16/12
      "http://100.128.0.1:5180", // one past CGNAT's 100.64/10
      "not a url",
      "",
    ]) {
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
