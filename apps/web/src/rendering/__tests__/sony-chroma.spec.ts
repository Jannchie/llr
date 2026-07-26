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

const clamp = (x: number, lo: number, hi: number) => Math.min(Math.max(x, lo), hi);

/** Line-for-line port of the GLSL. Display-linear in, display-linear out. */
function sonyChroma(s: number[], cross: number[], gain: number[]): number[] {
  const e = s.map(v => srgbEncode(clamp(v, 0, 1)));
  const y = (e[0] * 2432 + e[1] * 4864 + e[2] * 896) / 8192;
  const u = e[0] - e[1];
  const v = e[2] - e[1];
  const v2 = (u >= 0 ? cross[1] : cross[3]) * u + v;
  const u2 = (v >= 0 ? cross[0] : cross[2]) * v + u;
  const cr = clamp((u2 >= 0 ? gain[1] : gain[3]) * u2, -0.5, 0.5);
  const cb = clamp((v2 >= 0 ? gain[0] : gain[2]) * v2, -0.5, 0.5);
  const o = [y + 1.402 * cr, y - 0.7141 * cr - 0.3441 * cb, y + 1.772 * cb];
  return o.map(x => srgbDecode(clamp(x, 0, 1)));
}

describe("Sony RGB2YCC", () => {
  it("leaves neutrals exactly alone", () => {
    // Both chroma differences are zero on grey, so every branch collapses.
    for (const g of [0, 0.05, 0.25, 0.5, 0.75, 1]) {
      expect(sonyChroma([g, g, g], CROSS, GAIN)).toEqual([
        expect.closeTo(g, 6), expect.closeTo(g, 6), expect.closeTo(g, 6),
      ]);
    }
  });

  it("matches the worker, which matches the engine", () => {
    const cases: [number[], number[]][] = [
      [[0.5, 0.5, 0.5], [0.500000, 0.500000, 0.500000]],
      [[0.6, 0.2, 0.05], [0.744396, 0.199059, 0.003650]],
      [[0.05, 0.2, 0.6], [0.000000, 0.280816, 1.000000]],
      [[0.9, 0.8, 0.4], [0.969610, 0.843826, 0.195387]],
      [[0.02, 0.05, 0.03], [0.007997, 0.068831, 0.018231]],
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
    // Each cross term reads the *other* difference's sign, and both read the
    // unmodified u and v — swapping either is a silent hue error.
    expect(body).toContain("(u >= 0.0 ? u_sonyCross.y : u_sonyCross.w) * u + v");
    expect(body).toContain("(v >= 0.0 ? u_sonyCross.x : u_sonyCross.z) * v + u");
    expect(body).toContain("(u2 >= 0.0 ? u_sonyGain.y : u_sonyGain.w) * u2, -0.5, 0.5");
    expect(body).toContain("(v2 >= 0.0 ? u_sonyGain.x : u_sonyGain.z) * v2, -0.5, 0.5");
  });
});
