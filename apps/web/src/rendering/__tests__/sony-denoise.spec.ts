import { describe, expect, it } from "vitest";
import { SONY_CHROMA_NR_AUTO, sonyChromaNrSlider } from "../sony-denoise";
import { MARBLE_SLIDER_AUTO } from "../sony-marble";

/**
 * The manual colour-NR value against Edit's own control. The panel stores the
 * eleven stops of 色彩降噪 at ten times their value (measured-chroma-gap.md
 * 2.24.2), and Auto is the default stop — the midpoint the engine calls 5.
 */
describe("sonyChromaNrSlider", () => {
  it("is Auto's stop on Auto, whatever the stored value says", () => {
    expect(sonyChromaNrSlider(0, true)).toBe(MARBLE_SLIDER_AUTO);
    expect(sonyChromaNrSlider(100, true)).toBe(MARBLE_SLIDER_AUTO);
  });

  it("meets Auto at the panel's default", () => {
    expect(sonyChromaNrSlider(SONY_CHROMA_NR_AUTO, false)).toBe(MARBLE_SLIDER_AUTO);
  });

  it("is the panel's value over ten, to the nearest stop", () => {
    expect(sonyChromaNrSlider(0, false)).toBe(0);
    expect(sonyChromaNrSlider(30, false)).toBe(3);
    expect(sonyChromaNrSlider(100, false)).toBe(10);
    expect(sonyChromaNrSlider(74, false)).toBe(7);
    expect(sonyChromaNrSlider(75, false)).toBe(8);
  });

  it("clamps out of range and falls back to Auto on a non-number", () => {
    expect(sonyChromaNrSlider(-10, false)).toBe(0);
    expect(sonyChromaNrSlider(1000, false)).toBe(10);
    expect(sonyChromaNrSlider(Number.NaN, false)).toBe(MARBLE_SLIDER_AUTO);
  });
});
