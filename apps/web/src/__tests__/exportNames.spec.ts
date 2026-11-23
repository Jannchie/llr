import { describe, expect, it } from "vitest";
import { freeName } from "../composables/useExport";

describe("export file names", () => {
  it("keeps a name nothing else has", async () => {
    expect(await freeName("DSC0001.jpg", () => false)).toBe("DSC0001.jpg");
  });

  it("numbers a taken name before its extension, skipping numbers that are taken too", async () => {
    const taken = new Set(["DSC0001.jpg", "DSC0001-2.jpg"]);
    expect(await freeName("DSC0001.jpg", n => taken.has(n))).toBe("DSC0001-3.jpg");
  });

  it("asks an asynchronous check (a folder on disk) the same way", async () => {
    const taken = new Set(["a.b.jpg"]);
    expect(await freeName("a.b.jpg", async n => taken.has(n))).toBe("a.b-2.jpg");
  });

  it("numbers a name without an extension at its end", async () => {
    expect(await freeName("export", n => n === "export")).toBe("export-2");
  });
});
