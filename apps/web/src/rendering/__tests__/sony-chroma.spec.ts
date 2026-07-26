import { describe, expect, it } from "vitest";
import { srgbDecode, srgbEncode } from "../color-spaces";
import { PROCESS_SHADER } from "../passes";

/**
 * Sony's RGB2YCC, as the shader runs it (passes.ts `sonyChroma`).
 *
 * What this file can and cannot prove: there is no GL context in the test
 * environment, so the GLSL itself never executes here. The mirror below is a
 * hand port, and the expectations come from the worker's `sony/chroma.py`,
 * which *was* checked pixel by pixel against Imaging Edge's own buffers (Y
 * bit-identical, chroma within 1 of 16383). So this pins the maths and the
 * numbers; the source-text assertions at the end guard the port against drift.
 * A typo that only breaks compilation would still get through — for that, run
 * `scripts/shader-check.ts` and open the page it writes, which builds every
 * program for real. Worth doing after touching any GLSL here.
 */

// The engine's own interpolated values for DSC03015 (VV2), which are also what
// the RAW's 0x7842 holds. cross: 9-bit signed at bit 2 over 256; gain: 8-bit
// unsigned at bit 3 over 128.
const CROSS = [-0.261719, -0.222656, -0.230469, -0.167969];
const GAIN = [1.078125, 0.632812, 0.929688, 1.09375];

// YGamma's two terms, which carry the shot's Fade setting: at Fade 0 the pivot
// is zero and the stage is a plain gain. Both come out of the RAW (worker
// sony/sr2.py, tags 0x780b and 0x780e); these are DSC03015's Fade 0 entries.
const LUMA_PIVOT = 0;
const LUMA_CONTRAST = 1.0546875;
// The same file's Fade 5 entries, where the pivot does show up.
const FADE5_PIVOT = 10624 / 16383;
const FADE5_CONTRAST = 12928 / 16384;

const clamp = (x: number, lo: number, hi: number) => Math.min(Math.max(x, lo), hi);

/** Line-for-line port of the GLSL. Display-linear in, display-linear out. */
function sonyChroma(
  s: number[], cross: number[], gain: number[],
  pivot = LUMA_PIVOT, contrast = LUMA_CONTRAST, sat = 1,
): number[] {
  const e = s.map(v => srgbEncode(clamp(v, 0, 1)));
  const y = (e[0] * 2432 + e[1] * 4864 + e[2] * 896) / 8192;
  const u = e[0] - e[1];
  const v = e[2] - e[1];
  const v2 = (u >= 0 ? cross[1] : cross[3]) * u + v;
  const u2 = (v >= 0 ? cross[0] : cross[2]) * v + u;
  const cr = clamp((u2 >= 0 ? gain[1] : gain[3]) * u2, -0.5, 0.5) * sat;
  const cb = clamp((v2 >= 0 ? gain[0] : gain[2]) * v2, -0.5, 0.5) * sat;
  const yg = clamp((y - pivot) * contrast + pivot, 0, 1); // YGamma — Y only
  const o = [yg + 1.402 * cr, yg - 0.7141 * cr - 0.3441 * cb, yg + 1.772 * cb];
  return o.map(x => srgbDecode(clamp(x, 0, 1)));
}

describe("Sony RGB2YCC", () => {
  it("keeps neutrals neutral, but brightens them by YGamma's gain", () => {
    // Both chroma differences are zero on grey, so every chroma branch
    // collapses — the channels stay equal. Y does not stay put: YGamma lifts it
    // by 1.0546875 in the encoded domain, which is the engine's own behaviour.
    for (const g of [0, 0.05, 0.25, 0.5, 0.75, 1]) {
      const [r, gg, b] = sonyChroma([g, g, g], CROSS, GAIN);
      expect(gg).toBeCloseTo(r, 6);
      expect(b).toBeCloseTo(r, 6);
      expect(srgbEncode(r)).toBeCloseTo(Math.min(srgbEncode(g) * LUMA_CONTRAST, 1), 5);
    }
  });

  it("fades toward the pivot, which is the whole of the Fade slider", () => {
    // On grey the luma weights sum to one and both chroma branches collapse, so
    // the encoded output *is* YGamma's own output — the stage can be read off
    // directly. What makes this a fade rather than a brightness change is that
    // it moves everything toward the pivot: lifting below it, cutting above it.
    for (const g of [0.02, 0.1, 0.3, 0.6, 0.9]) {
      const [r, gg, b] = sonyChroma([g, g, g], CROSS, GAIN, FADE5_PIVOT, FADE5_CONTRAST);
      expect(gg).toBeCloseTo(r, 6);
      expect(b).toBeCloseTo(r, 6);
      const before = srgbEncode(g), after = srgbEncode(r);
      expect(Math.abs(after - FADE5_PIVOT)).toBeLessThan(Math.abs(before - FADE5_PIVOT));
      if (before < FADE5_PIVOT) expect(after).toBeGreaterThan(before);
      else expect(after).toBeLessThan(before);
    }
  });

  it("matches the worker, which matches the engine", () => {
    const cases: [number[], number[]][] = [
      [[0.5, 0.5, 0.5], [0.563248, 0.563248, 0.563248]],
      [[0.6, 0.2, 0.05], [0.803479, 0.226881, 0.006806]],
      [[0.05, 0.2, 0.6], [0.000000, 0.308154, 1.000000]],
      [[0.9, 0.8, 0.4], [1.000000, 0.948125, 0.241093]],
      [[0.02, 0.05, 0.03], [0.009673, 0.074523, 0.020898]],
    ];
    for (const [input, want] of cases) {
      const got = sonyChroma(input, CROSS, GAIN);
      for (let i = 0; i < 3; i++) expect(got[i]).toBeCloseTo(want[i], 5);
    }
  });

  it("desaturates completely when the gains are zero — that is Black & White", () => {
    const out = sonyChroma([0.6, 0.2, 0.05], [0, 0, 0, 0], [0, 0, 0, 0]);
    expect(out[0]).toBeCloseTo(out[1], 6);
    expect(out[1]).toBeCloseTo(out[2], 6);
  });

  it("adds saturation rather than removing it", () => {
    // The whole point of the stage: the pair is not an identity, and the gains
    // above 1.0 are what the in-camera JPEG shows and a plain BT.601 round trip
    // cannot produce.
    const input = [0.6, 0.3, 0.15];
    const out = sonyChroma(input, CROSS, GAIN);
    const spread = (c: number[]) => Math.max(...c) - Math.min(...c);
    expect(spread(out)).toBeGreaterThan(spread(input));
  });

  it("keeps the shader's own constants in step with this port", () => {
    const src = PROCESS_SHADER.slice(PROCESS_SHADER.indexOf("vec3 sonyChroma"));
    const body = src.slice(0, src.indexOf("\n}"));
    expect(body).toContain("vec3(2432.0, 4864.0, 896.0) / 8192.0");
    expect(body).toContain("y + 1.4020 * cr, y - 0.7141 * cr - 0.3441 * cb, y + 1.7720 * cb");
    // YGamma must land after the chroma is computed and clamped: applying it to
    // Y first would scale Cb and Cr with it, which the engine does not do.
    expect(body).toContain("y = clamp((y - u_sonyLuma.x) * u_sonyLuma.y + u_sonyLuma.x, 0.0, 1.0);");
    expect(body.indexOf("y = clamp((y -")).toBeGreaterThan(body.indexOf("float cb = clamp"));
    // Each cross term reads the *other* difference's sign, and both read the
    // unmodified u and v — swapping either is a silent hue error.
    expect(body).toContain("(u >= 0.0 ? u_sonyCross.y : u_sonyCross.w) * u + v");
    expect(body).toContain("(v >= 0.0 ? u_sonyCross.x : u_sonyCross.z) * v + u");
    expect(body).toContain("(u2 >= 0.0 ? u_sonyGain.y : u_sonyGain.w) * u2, -0.5, 0.5) * u_sonySat");
    expect(body).toContain("(v2 >= 0.0 ? u_sonyGain.x : u_sonyGain.z) * v2, -0.5, 0.5) * u_sonySat");
  });

  it("puts Saturation on both sides of the clamp, where the engine puts it", () => {
    // The gains arrive already divided by the factor and the chroma is
    // multiplied back after the clamp, so away from the clamp the setting does
    // nothing at all — that near-cancellation is the engine's actual behaviour,
    // not an approximation of it.
    const f = 1.55;
    const divided = GAIN.map(g => g / f);
    for (const input of [[0.5, 0.5, 0.5], [0.45, 0.4, 0.35], [0.3, 0.35, 0.4]]) {
      const plain = sonyChroma(input, CROSS, GAIN);
      const sat = sonyChroma(input, CROSS, divided, LUMA_PIVOT, LUMA_CONTRAST, f);
      for (let i = 0; i < 3; i++) expect(sat[i]).toBeCloseTo(plain[i], 5);
    }
    // Where it does clamp, the two no longer cancel — that is the whole slider.
    const wide = [0.95, 0.2, 0.05];
    const under = sonyChroma(wide, CROSS, GAIN.map(g => g * 2.3), LUMA_PIVOT, LUMA_CONTRAST, 1 / 2.3);
    expect(Math.abs(under[0] - sonyChroma(wide, CROSS, GAIN)[0])).toBeGreaterThan(1e-3);
  });
});
