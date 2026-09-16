import { describe, expect, it } from "vitest";
import { lumaNrParams, yccEffectiveAmount } from "../sony-lumanr";
import type { LumaNrParams, ProfileLumaNr } from "../sony-lumanr";
import fixture from "./lumanr_ycc.json";

/**
 * Pinned to the worker's ycc_luma_nr_params (apps/worker/src/llr_worker/sony/
 * lumanr.py), which apps/worker/tests/test_lumanr.py pins to the arguments
 * Edit.exe was seen to hand its SIMD BSNR_Y core: two S-size frames (ISO 200
 * and 6400), Amount 0..100, Edge 0..100. 80 slider positions in the fixture.
 */
type Case = { block: ProfileLumaNr; amount: number; edge: number; params: LumaNrParams };
const cases = (fixture as { cases: Case[] }).cases;

describe("lumaNrParams", () => {
  it("reproduces the worker at every slider position in the fixture", () => {
    expect(cases.length).toBeGreaterThan(50);
    for (const c of cases) {
      expect(lumaNrParams(c.block, c.amount, c.edge), `amount ${c.amount} edge ${c.edge}`).toEqual(c.params);
    }
  });

  it("is the engine's own numbers at the default on the ISO 6400 frame", () => {
    const block = cases.find((c) => c.block.baseCoeff === 64)!.block;
    expect(lumaNrParams(block, 50, 50)).toEqual({ lo: 0, hi: 2560, base: 33, slope: 198, weight: 716, gain: 115, limit: 1023 });
  });
});

describe("yccEffectiveAmount", () => {
  it("keeps both ends and puts neutral on the bias", () => {
    expect(yccEffectiveAmount(0, 40)).toBe(-100);
    expect(yccEffectiveAmount(25, 40)).toBeCloseTo(-30);
    expect(yccEffectiveAmount(50, 40)).toBe(40);
    expect(yccEffectiveAmount(70, 40)).toBeCloseTo(64);
    expect(yccEffectiveAmount(100, 40)).toBe(100);
  });
});
