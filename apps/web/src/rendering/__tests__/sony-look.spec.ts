import { describe, expect, it } from "vitest";
import fixtureJson from "../../../../worker/tests/fixtures/look_rebuild.json?raw";
import type { ColorProfileMeta, LookTweaks } from "../../api";
import {
  TUNE_GAIN_ROWS,
  TUNE_TABLE_LEN,
  clampLookTweaks,
  clarityAmount,
  hueDegrees,
  lumaLevels,
  lumaTerms,
  parseLookCalibration,
  parseToneFamily,
  rebuildLookProfile,
  saturationFactor,
  scaleDroGain,
} from "../sony-look";

/**
 * The mirror against the worker: apps/worker/tests/test_look_rebuild.py builds
 * the fixture from look_render_info itself (the sample ARW's FL calibration,
 * a handful of tweak and DRO settings, and what each rebuilds to) and asserts
 * the worker still produces it; this file asserts rebuildLookProfile produces
 * it too. The family is read from the shipped asset, which the same worker
 * test holds to data/tone_family.npz.
 */
type Fixture = {
  base: ColorProfileMeta;
  cases: { name: string; look: Partial<LookTweaks>; dro: number | null; level: number; expected: Record<string, unknown> }[];
};
const FIXTURE = JSON.parse(fixtureJson) as Fixture;
// The asset is binary, so no `?raw` import: read it off disk. The module name
// is assembled so the browser-only tsconfig is not asked to type node:fs.
const fs = await import("node:" + "fs") as { readFileSync(path: URL): Uint8Array };
const bytes = fs.readFileSync(new URL("../../../public/sony-tone-family.bin", import.meta.url));
const FAMILY = parseToneFamily(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer);
if (!FAMILY) throw new Error("public/sony-tone-family.bin is not the family's shape");

// The worker prints doubles; the only place the mirror can differ from it is
// pow() (sRGB decode, the DRO power), and that by a last bit.
const TOL = 1e-12;
function same(got: unknown, want: unknown, path: string): void {
  if (Array.isArray(want)) {
    expect(Array.isArray(got), path).toBe(true);
    expect((got as unknown[]).length, path).toBe(want.length);
    want.forEach((w, i) => same((got as unknown[])[i], w, `${path}[${i}]`));
  } else if (typeof want === "number") {
    expect(typeof got, path).toBe("number");
    expect(Math.abs((got as number) - want), path).toBeLessThanOrEqual(TOL);
  } else if (want && typeof want === "object") {
    expect(got && typeof got === "object", path).toBe(true);
    expect(Object.keys(got as object).sort(), path).toEqual(Object.keys(want).sort());
    for (const k of Object.keys(want)) same((got as Record<string, unknown>)[k], (want as Record<string, unknown>)[k], `${path}.${k}`);
  } else {
    expect(got, path).toEqual(want);
  }
}

describe("the shipped family", () => {
  it("is the 37 x 1025 table the mirror indexes", () => {
    expect(FAMILY.length).toBe(TUNE_GAIN_ROWS * TUNE_TABLE_LEN);
    // Row 18 is the identity: entry i maps to 64 i, the table's own step.
    for (let i = 0; i < TUNE_TABLE_LEN; i += 128) expect(FAMILY[18 * TUNE_TABLE_LEN + i]).toBe(Math.min(64 * i, 65535));
    expect(parseToneFamily(new ArrayBuffer(10))).toBeNull();
  });
});

describe("rebuildLookProfile", () => {
  it("parses the worker's block", () => {
    expect(parseLookCalibration(FIXTURE.base.lookCalibration)).not.toBeNull();
    expect(parseLookCalibration(null)).toBeNull();
    expect(parseLookCalibration({ curveX: [1, 2] })).toBeNull();
  });

  for (const c of FIXTURE.cases) {
    it(`rebuilds what the worker built: ${c.name}`, () => {
      const got = rebuildLookProfile(FIXTURE.base, { ...FIXTURE.base.lookAsShot!, ...c.look },
        { strength: c.dro, level: c.level }, FAMILY);
      expect(got).not.toBeNull();
      for (const key of Object.keys(c.expected)) {
        same((got as unknown as Record<string, unknown>)[key], c.expected[key], `${c.name}.${key}`);
      }
    });
  }

  it("leaves what no tweak reaches as the base's", () => {
    const base = { ...FIXTURE.base, profileLumaLut: [1, 2, 3], profileSharpness: { amount: 0.5 } } as ColorProfileMeta;
    const got = rebuildLookProfile(base, FIXTURE.base.lookAsShot!, { strength: null, level: -1 }, FAMILY)!;
    expect(got.profileLumaLut).toBe(base.profileLumaLut);
    expect(got.profileSharpness).toBe(base.profileSharpness);
    expect(got.lookCalibration).toBe(base.lookCalibration);
  });

  it("hands a manual DRO level it does not hold back to the worker", () => {
    const asShot = FIXTURE.base.lookAsShot!;
    // The base was built at Auto, so a preset is not here to give.
    expect(rebuildLookProfile(FIXTURE.base, asShot, { strength: 1, level: 30 }, FAMILY)).toBeNull();
    // ...unless it was built at that very level: then the table is the base's,
    // at unit strength whatever the control says.
    const atLevel = { ...FIXTURE.base, droLevel: 30, droStrength: 1, profileDroGain: [1, 1.5, 2] };
    const got = rebuildLookProfile(atLevel, asShot, { strength: 1.7, level: 30 }, FAMILY)!;
    expect(got.profileDroGain).toEqual([1, 1.5, 2]);
    expect(got.droLevel).toBe(30);
    // Off is off at any level, and needs nobody.
    expect(rebuildLookProfile(FIXTURE.base, asShot, { strength: 0, level: 30 }, FAMILY)!.profileDroGain).toBeNull();
  });

  it("refuses anything but a Sony profile with the block", () => {
    expect(rebuildLookProfile({ kind: "dcp" }, {}, { strength: null, level: -1 }, FAMILY)).toBeNull();
    expect(rebuildLookProfile({ kind: "sony" }, {}, { strength: null, level: -1 }, FAMILY)).toBeNull();
  });
});

describe("the scalar stages", () => {
  it("clamp the way the worker's _clamp_tweak does: truncate, then the range", () => {
    const t = clampLookTweaks({ contrast: 250, shadows: 3.7, fade: -5, clarity: 100.9, hue: -0.4 } as Partial<LookTweaks>);
    expect(t).toEqual({ contrast: 100, highlights: 0, shadows: 3, white: 0, black: 0, fade: 0, hue: 0, saturation: 0, clarity: 100 });
    expect(clampLookTweaks({ fade: 50 }, { fade: [0, 20] }).fade).toBe(20);
  });

  it("luma_terms: the pivot takes the entry above, the contrast interpolates and extrapolates", () => {
    const pivot = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19], contrast = [100, 90, 80, 70, 60, 50, 40, 30, 20, 10];
    expect(lumaTerms(pivot, contrast, 0)).toEqual([10 / 16383, 100 / 16384]);
    expect(lumaTerms(pivot, contrast, 5)).toEqual([11 / 16383, 95 / 16384]);
    expect(lumaTerms(pivot, contrast, 100)).toEqual([19 / 16383, 0]);
  });

  it("luma_levels: black truncates toward zero, positive black lifts, positive white stretches", () => {
    expect(lumaLevels(0, 0)).toEqual([0, 1]);
    expect(lumaLevels(7, 0)).toEqual([-(32767 / 512) / 16383, 512 / 513]);
    expect(lumaLevels(-7, 0)).toEqual([(32767 / 512) / 16383, 512 / 511]);
    expect(lumaLevels(0, 12)).toEqual([0, 512 / 500]);
  });

  it("saturation, hue and clarity on Edit's scale", () => {
    expect(saturationFactor(55)).toBe(1.55);
    expect(saturationFactor(-100)).toBe(0);
    expect(hueDegrees(10)).toBeCloseTo(7, 12);
    expect(hueDegrees(-10)).toBeCloseTo(-3.5, 12);
    expect(clarityAmount(0)).toBe(0);
    expect(clarityAmount(10)).toBe(32 / 1024);
    expect(clarityAmount(45)).toBe((260 + (336 - 260) * 0.5) / 1024);
    expect(clarityAmount(100)).toBe(646 / 1024);
    expect(clarityAmount(-3)).toBe(0);
  });

  it("scale_dro_gain is a power, with unit and zero strength special-cased", () => {
    expect(scaleDroGain([1, 2, 4], 1)).toEqual([1, 2, 4]);
    expect(scaleDroGain([1, 2, 4], 0)).toEqual([1, 1, 1]);
    expect(scaleDroGain([1, 2, 4], 0.5)).toEqual([1, Math.SQRT2, 2]);
    expect(scaleDroGain([2], 5)).toEqual([4]); // clamped to 2
  });
});
