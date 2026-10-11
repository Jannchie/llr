import { describe, expect, it } from "vitest";
import { applyRecipe, DEFAULT_SECTIONS, extractRecipe, parseRecipe, serializeRecipe } from "../recipes";
import { defaultAdjust, defaultComponent, type MaskGroup } from "../masks";

const group = (id: string, type: "luminance" | "radial" | "brush" | "ai"): MaskGroup => ({
  id, enabled: true, invert: false, components: [defaultComponent(type)], adjust: { ...defaultAdjust(), exposure: 1 },
});

const source = {
  recipe: { exposure: 1.5, contrast: 20, temperature: 4500, sharpAmount: 60, grainAmount: 30, blurAmount: 50, blurFocusX: 0.3, blurFocusY: 0.6 },
  hslHue: [1, 2, 3, 4, 5, 6, 7, 8], curve: { points: [] }, look: { contrast: 5 }, crop: { cx: 0.4 },
  relight: { lights: [{ id: "a" }] }, erase: [{ id: "e" }],
  masks: [group("lum", "luminance"), group("rad", "radial"), group("br", "brush"), group("ai", "ai")],
};
const target = {
  recipe: { exposure: 0, contrast: 0, temperature: 6500, sharpAmount: 0, grainAmount: 0, blurAmount: 0, blurFocusX: 0.7, blurFocusY: 0.2 },
  hslHue: [0, 0, 0, 0, 0, 0, 0, 0], crop: { cx: 0.5 }, relight: { lights: [] },
  masks: [group("mine", "radial")],
};

describe("recipes", () => {
  it("copies the chosen sections and nothing positional", () => {
    const r = extractRecipe(source, DEFAULT_SECTIONS);
    const out = applyRecipe(target, r);
    expect(out.recipe).toMatchObject({ exposure: 1.5, contrast: 20, temperature: 4500, sharpAmount: 60, grainAmount: 30, blurAmount: 50 });
    // The focus point is the target's own.
    expect(out.recipe.blurFocusX).toBe(0.7);
    expect(out.hslHue).toEqual(source.hslHue);
    // Crop and relight are unticked by default; erase never travels.
    expect(out.crop).toEqual({ cx: 0.5 });
    expect(out.relight).toEqual({ lights: [] });
    expect((out as Record<string, unknown>).erase).toBeUndefined();
    // Portable masks (tone, AI) come over; gradients and brushes stay home, the target's own survive.
    const types = (out.masks ?? []).map(g => g.components[0]!.type).sort();
    expect(types).toEqual(["ai", "luminance", "radial"]);
    expect(out.masks!.find(g => g.components[0]!.type === "radial")!.id).toBe("mine");
  });

  it("leaves unticked sections untouched", () => {
    const out = applyRecipe(target, extractRecipe(source, ["color"]));
    expect(out.recipe).toMatchObject({ temperature: 4500, exposure: 0, sharpAmount: 0 });
    expect(out.hslHue).toEqual(target.hslHue);
    expect(out.masks).toEqual(target.masks);
  });

  it("carries crop and relight when asked", () => {
    const out = applyRecipe(target, extractRecipe(source, ["crop", "relight"]));
    expect(out.crop).toEqual({ cx: 0.4 });
    expect(out.relight).toEqual({ lights: [{ id: "a" }] });
  });

  it("round-trips the clipboard text and reads a pre-section recipe", () => {
    const r = extractRecipe(source);
    expect(parseRecipe(serializeRecipe(r))).toEqual(r);
    const legacy = JSON.stringify({ "llr-recipe": 1, recipe: { exposure: 2, contrast: 10 }, hslHue: [9, 9, 9, 9, 9, 9, 9, 9], masks: [group("rad", "radial")] });
    const old = parseRecipe(legacy)!;
    const out = applyRecipe(target, old);
    expect(out.recipe.exposure).toBe(2);
    expect(out.hslHue[0]).toBe(9);
    expect(out.masks!.map(g => g.id)).toEqual(["mine"]);
    expect(parseRecipe("not a recipe")).toBeNull();
  });
});
