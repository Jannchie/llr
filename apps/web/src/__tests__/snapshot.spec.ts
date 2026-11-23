import { describe, expect, it } from "vitest";
import { defaultCrop } from "../rendering/crop";
import { defaultToneCurve } from "../rendering/curve";
import { defaultAdjust, type MaskGroup } from "../rendering/masks";
import { defaultRelight } from "../rendering/relight";
import {
  DRO_AUTO, LOOK_SCALE, defaultDenoise, defaultGrading, defaultRecipe, defaultSnapshot, lookStopsToPanel,
  normalizeSnapshot, type Snapshot,
} from "../snapshot";

/** What the first builds stored: the Basic sliders of the time, HSL, grading, curve, crop and a DCP. */
function earliest(): Snapshot {
  return {
    recipe: { exposure: 0.5, contrast: 10, highlights: 0, shadows: 0, whites: 0, blacks: 0, vibrance: 0, saturation: 0, temperature: 5200, tint: 4, clarity: 0, dehaze: 0 } as Snapshot["recipe"],
    hslHue: [0, 0, 0, 0, 0, 0, 0, 0], hslSat: [0, 0, 0, 0, 0, 0, 0, 0], hslLum: [0, 0, 0, 0, 0, 0, 0, 0],
    grading: defaultGrading(),
    curve: defaultToneCurve(),
    crop: defaultCrop(),
    dcp: "Camera Standard",
  };
}

describe("normalizeSnapshot", () => {
  it("leaves a current snapshot as it is", () => {
    const now = defaultSnapshot("DSC0001.ARW");
    expect(normalizeSnapshot(now)).toEqual(now);
  });

  it("is stable across the JSON round trip a stored edit makes", () => {
    const once = normalizeSnapshot(earliest());
    expect(normalizeSnapshot(JSON.parse(JSON.stringify(once)) as Snapshot)).toEqual(once);
  });

  it("gives a snapshot from before a field existed what that build rendered", () => {
    const s = normalizeSnapshot(earliest());
    // Recipe keys added since get their defaults, and what was stored stands.
    expect(s.recipe).toEqual({ ...defaultRecipe(), exposure: 0.5, contrast: 10, temperature: 5200, tint: 4 });
    expect(s.recipe.lensDistortion).toBe(100);
    // No aspect lock: the stored crop box predates the lock and need not fit it.
    expect(s.aspect).toBe("free");
    // Before the Sony engine there was only the DCP.
    expect(s.profile).toBe("standard");
    expect(s.denoise).toEqual(defaultDenoise());
    expect(s.look).toBeNull();
    expect(s.lookStyle).toBeNull();
    expect(s.dro).toBeNull();
    expect(s.droLevel).toBe(DRO_AUTO);
    // Rendered without either, though a new photo starts with camera match on.
    expect(s.sonyAdvancedColour).toBe(false);
    expect(s.sonyCameraMatch).toBe(false);
    expect(defaultSnapshot().sonyCameraMatch).toBe(true);
    expect(s.masks).toEqual([]);
    expect(s.erase).toEqual([]);
    expect(s.relight).toEqual(defaultRelight());
    expect(s.bypass).toEqual([]);
  });

  it("fills a denoise block that predates one of its fields from the defaults", () => {
    const stored = { ...earliest(), denoise: { enabled: false, amount: 80 } } as unknown as Snapshot;
    expect(normalizeSnapshot(stored).denoise).toEqual({ ...defaultDenoise(), enabled: false, amount: 80 });
  });

  it("converts a look stored in the camera's stops to the panel scale", () => {
    const stops = { contrast: 2, highlights: -1, shadows: 3, fade: 1, saturation: -3, clarity: 2 };
    const s = normalizeSnapshot({ ...earliest(), look: stops as unknown as Snapshot["look"] });
    expect(s.look).toEqual({ contrast: 10, highlights: -5, shadows: 15, white: 0, black: 0, fade: 10, hue: 0, saturation: -25, clarity: 20 });
    expect(s.look).toEqual(lookStopsToPanel(stops));
    expect(s.lookScale).toBe(LOOK_SCALE);
  });

  it("keeps a look that is already on the panel scale, as a copy", () => {
    const look = { contrast: 12, highlights: 0, shadows: 0, white: 5, black: 0, fade: 0, hue: 0, saturation: 30, clarity: 0 };
    const stored: Snapshot = { ...earliest(), look, lookScale: LOOK_SCALE };
    const s = normalizeSnapshot(stored);
    expect(s.look).toEqual(look);
    expect(s.look).not.toBe(look);
  });

  it("completes a mask's adjustments from before one of them existed", () => {
    const group = { id: "m1", enabled: true, invert: false, components: [], adjust: { exposure: 0.4 } } as unknown as MaskGroup;
    const s = normalizeSnapshot({ ...earliest(), masks: [group] });
    expect(s.masks[0].adjust).toEqual({ ...defaultAdjust(), exposure: 0.4 });
  });

  it("returns nothing that aliases the stored snapshot", () => {
    const stored = { ...defaultSnapshot(), erase: [{ id: "e1", points: [[0.1, 0.2]] as [number, number][], radius: 0.02 }] };
    const s = normalizeSnapshot(stored);
    s.recipe.exposure = 3;
    s.hslHue[0] = 9;
    s.crop.w = 0.5;
    s.erase[0].points.push([0.3, 0.4]);
    s.bypass.push("curve");
    expect(stored.recipe.exposure).toBe(0);
    expect(stored.hslHue[0]).toBe(0);
    expect(stored.crop.w).toBe(1);
    expect(stored.erase[0].points).toHaveLength(1);
    expect(stored.bypass).toEqual([]);
  });

  it("pads HSL bands a corrupt record lost", () => {
    const s = normalizeSnapshot({ ...earliest(), hslHue: [5, 6] });
    expect(s.hslHue).toEqual([5, 6, 0, 0, 0, 0, 0, 0]);
  });
});

describe("defaultSnapshot", () => {
  it("picks Sony's engine for Sony's files and the DCP for anything else", () => {
    expect(defaultSnapshot("DSC01157.ARW").profile).toBe("sony");
    expect(defaultSnapshot("old.sr2").profile).toBe("sony");
    expect(defaultSnapshot("IMG_0001.CR3").profile).toBe("standard");
    expect(defaultSnapshot().profile).toBe("standard");
  });
});
