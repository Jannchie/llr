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

// ChromaSuppres' four terms, in the engine's own units. This body's anchors
// give hiY = 15360 (the worker's approximation of the engine's LUT chain — see
// sony/chromasuppres.py), loY = 0 and both slopes 512. Note the mid-tone factor
// is 255/256: the stage is not an identity anywhere.
const SUPPRESS = { hiY: 15360, loY: 0, slopeHi: 512, slopeLo: 512 };

// YGamma's table, the third thing that stage reads. Which one a look gets comes
// from two per-look SR2 tags (worker sony/sr2.py, 0x780c and 0x780d), and only
// two have been dumped out of the engine (calib+0x318fc): Standard and Neutral
// get a highlight knee above Y=8192, the other eight a near identity. The
// entries below are those tables' own, at the indices these tests reach, copied
// from worker sony/data/ygamma_luts.npz — the npz has no business being loaded
// from a browser test. Everything else is left identity, which is what both
// families genuinely are from Y=0 up to the knee.
const LUMA_LUT_SIZE = 16384;
const KNEE_LUT_SAMPLES: Record<number, number> = { 12288: 11904, 14744: 14120, 16383: 15614 };
const FLAT_LUT_SAMPLES: Record<number, number> = { 12288: 12288, 14744: 14744, 16383: 16382 };
// The same two families as Imaging Edge's 色彩复制 = 高级 dumped them, and the
// contrast that goes with them — 17280/16384 for both, where 标准 gives Standard
// 1.0546875 and FL exactly 1.0. This is the half of that setting that is not the
// 3-D LUT: it is a different table, not the same one plus a gain, and it departs
// from identity from Y=1 upward (8192 maps to 8176 in both) rather than only
// past a knee. Same source as above, worker sony/data/ygamma_luts.npz.
const ADV_LUMA_CONTRAST = 17280 / 16384;
const ADV_KNEE_LUT_SAMPLES: Record<number, number> = { 12288: 11728, 14744: 13680, 16383: 14975 };
const ADV_FLAT_LUT_SAMPLES: Record<number, number> = { 12288: 12096, 14744: 14256, 16383: 15679 };

function lutFrom(samples: Record<number, number>): number[] {
  const t = Array.from({ length: LUMA_LUT_SIZE }, (_, i) => i);
  for (const [i, v] of Object.entries(samples)) t[Number(i)] = v;
  return t;
}

// Sony's 3-D LUT — ZcTask3DLut, Edit's "advanced colour reproduction". The
// shipped table is 33^3 grid points (public/sony-lut3d.bin, 210 KB) and none of
// it has any business being loaded from a browser test, so only the 24 points
// the three cases below actually reach are here, keyed "iu,iv,iy" and copied
// out of the worker's own data/lut3d_points.npz. A fetch that misses one is a
// test failure rather than a silently interpolated zero.
const LUT3D_POINTS: Record<string, [number, number, number]> = {
  "11,20,21": [10707, -5709, 4032],
  "11,20,22": [11220, -5709, 4032],
  "11,21,21": [10643, -5620, 5509],
  "11,21,22": [11092, -5530, 5509],
  "12,20,21": [10728, -4009, 4040],
  "12,20,22": [11242, -4009, 4040],
  "12,21,21": [10643, -4002, 5622],
  "12,21,22": [11028, -3912, 5509],
  "16,16,24": [12270, 44, 55],
  "16,16,25": [12784, 44, 55],
  "16,16,26": [13298, 44, 55],
  "16,17,24": [12270, 44, 397],
  "16,17,25": [12784, 44, 397],
  "16,17,26": [13298, 44, 397],
  "17,16,24": [12270, 494, 55],
  "17,16,25": [12784, 494, 55],
  "17,16,26": [13298, 494, 55],
  "17,17,24": [12270, 494, 397],
  "17,17,25": [12784, 494, 397],
  "17,17,26": [13298, 494, 397],
  "19,11,24": [12246, 2650, -5625],
  "19,11,25": [12759, 2650, -5625],
  "19,12,24": [12246, 2650, -4035],
  "19,12,25": [12759, 2650, -4035],
  "20,11,24": [12182, 3999, -5625],
  "20,11,25": [12631, 3909, -5512],
  "20,12,24": [12206, 4007, -4042],
  "20,12,25": [12592, 3917, -3928],
};

const clamp = (x: number, lo: number, hi: number) => Math.min(Math.max(x, lo), hi);

/** The 1-D chroma warp, by the closed form the shader uses (worker lut3d.py). */
function sonyWarpCurve(x: number): number {
  return Math.sign(x) * Math.min(Math.trunc(32768 * Math.abs(x / 32768) ** (2 / 3)), 32767);
}

/**
 * The 3-D LUT step, as the GLSL runs it. Takes the normalised y/cr/cb the rest
 * of this stage carries, reconstructs the engine's integer planes, interpolates,
 * and converts back.
 *
 * The trilinear weights are the engine's integers, not a float lerp: they are
 * quantised to 512ths and w0 is the *remainder* of the other seven, so all of
 * the rounding lands on one corner. A plain float trilinear drifts up to 11 of
 * 16383 against the worker's bit-exact model on saturated pixels — worth having
 * in both the shader and this mirror.
 */
function sonyLut3d(y: number, cr: number, cb: number): [number, number, number] {
  const crPlane = Math.floor(cr * 16383 + 0.5);
  const cbPlane = Math.floor(cb * 16383 + 0.5);
  const yPlane = Math.floor(y * 16383);
  const uw = sonyWarpCurve(Math.trunc(clamp(cbPlane * 3.7e-05 + crPlane * 1.401988, -32768, 32767)));
  const vw = sonyWarpCurve(Math.trunc(clamp(crPlane * 0.000135 + cbPlane * 1.771978, -32768, 32767)));
  const a = uw + 32768, b = vw + 32768;
  const iu = a >> 11, f0 = a & 2047;
  const iv = b >> 11, f1 = b & 2047;
  const iy = yPlane >> 9, yf = yPlane & 511;
  const F0 = 2048 - f0, F1 = 2048 - f1, yF = 512 - yf;
  const w2 = (((f0 * yf) >> 3) * F1 + 0x40000) >> 19;
  const w6 = (((f0 * yf) >> 3) * f1 + 0x40000) >> 19;
  const w3 = (((f0 * yF) >> 3) * F1 + 0x40000) >> 19;
  const w7 = (((f0 * yF) >> 3) * f1 + 0x40000) >> 19;
  const w1 = (((F0 * yf) >> 3) * F1 + 0x40000) >> 19;
  const w5 = (((F0 * yf) >> 3) * f1 + 0x40000) >> 19;
  const w4 = (((F0 * yF) >> 3) * f1 + 0x40000) >> 19;
  const w0 = 512 - w7 - w6 - w5 - w4 - w3 - w2 - w1;
  // Corner order inside a cell is gray-coded on (f1, f0, yf).
  const corners: [number, number, number, number][] = [
    [iu, iv, iy, w0], [iu, iv, iy + 1, w1], [iu + 1, iv, iy + 1, w2], [iu + 1, iv, iy, w3],
    [iu, iv + 1, iy, w4], [iu, iv + 1, iy + 1, w5], [iu + 1, iv + 1, iy + 1, w6], [iu + 1, iv + 1, iy, w7],
  ];
  const acc = [0, 0, 0];
  for (const [pu, pv, py, w] of corners) {
    const p = LUT3D_POINTS[`${pu},${pv},${py}`];
    if (!p) throw new Error(`3-D LUT point ${pu},${pv},${py} is not in the test table`);
    for (let k = 0; k < 3; k++) acc[k] += p[k] * w;
  }
  for (let k = 0; k < 3; k++) acc[k] >>= 9;
  // Y has a lower clamp and no upper one; the chroma goes back through the
  // inverse of the same 2x2 that produced the axes.
  return [
    Math.max(acc[0], 0) / 16383,
    (acc[1] * 0.713273 - acc[2] * 1.5e-05) / 16383,
    (acc[2] * 0.564341 - acc[1] * 5.4e-05) / 16383,
  ];
}

/** Line-for-line port of the GLSL. Display-linear in, display-linear out. */
function sonyChroma(
  s: number[], cross: number[], gain: number[],
  pivot = LUMA_PIVOT, contrast = LUMA_CONTRAST, sat = 1,
  suppress: typeof SUPPRESS | null = null,
  lut: number[] | null = null,
  lut3d = false,
  // What 高级 swaps YGamma over to. `lut3d` selects them, because in Imaging
  // Edge it is one setting — null means the caller has only the standard pair,
  // which is what an older profile response leaves the shader with.
  lutAdvanced: number[] | null = null,
  contrastAdvanced = ADV_LUMA_CONTRAST,
): number[] {
  const e = s.map(v => srgbEncode(clamp(v, 0, 1)));
  const y = (e[0] * 2432 + e[1] * 4864 + e[2] * 896) / 8192;
  const u = e[0] - e[1];
  const v = e[2] - e[1];
  const v2 = (u >= 0 ? cross[1] : cross[3]) * u + v;
  const u2 = (v >= 0 ? cross[0] : cross[2]) * v + u;
  let cr = clamp((u2 >= 0 ? gain[1] : gain[3]) * u2, -0.5, 0.5) * sat;
  let cb = clamp((v2 >= 0 ? gain[0] : gain[2]) * v2, -0.5, 0.5) * sat;
  // ChromaSuppres, on the luma from *before* YGamma — that is the plane the
  // engine's own stage reads, and running it after would fade the wrong pixels.
  if (suppress) {
    const y16 = y * 16383;
    let f = 255;
    if (y16 > suppress.hiY) f = 255 - Math.floor((y16 - suppress.hiY) * suppress.slopeHi / 4096);
    else if (y16 < suppress.loY) f = 255 - Math.floor((suppress.loY - y16) * suppress.slopeLo / 4096);
    f = clamp(f, 0, 255) / 256;
    cr *= f;
    cb *= f;
  }
  // YGamma — Y only, and the table before the pivot/contrast line. The index is
  // truncated, not interpolated: the engine's Y is an integer here, and rounding
  // the knee smooth is exactly what the shader's texelFetch is there to avoid.
  // 高级 takes the other table and the other contrast, gated on that table
  // having arrived — exactly the shader's `adv`.
  const adv = lut3d && lutAdvanced != null;
  const table = adv ? lutAdvanced : lut;
  let yl = y;
  if (table) yl = table[clamp(Math.floor(y * 16383), 0, table.length - 1)] / 16383;
  let yg = clamp((yl - pivot) * (adv ? contrastAdvanced : contrast) + pivot, 0, 1);
  // The 3-D LUT, where the engine has it: after YGamma, before the return trip.
  if (lut3d) [yg, cr, cb] = sonyLut3d(yg, cr, cb);
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
    expect(body).toContain("y = clamp((y - u_sonyLuma.x) * lumaContrast + u_sonyLuma.x, 0.0, 1.0);");
    // Only the contrast is chosen between the two settings; the pivot is the
    // shot's either way, which is what the worker measured (sony/chroma.py).
    expect(body).toContain("float lumaContrast = adv ? u_sonyLumaAdvContrast : u_sonyLuma.y;");
    expect(body.indexOf("y = clamp((y -")).toBeGreaterThan(body.indexOf("float cb = clamp"));
    // Each cross term reads the *other* difference's sign, and both read the
    // unmodified u and v — swapping either is a silent hue error.
    expect(body).toContain("(u >= 0.0 ? u_sonyCross.y : u_sonyCross.w) * u + v");
    expect(body).toContain("(v >= 0.0 ? u_sonyCross.x : u_sonyCross.z) * v + u");
    expect(body).toContain("(u2 >= 0.0 ? u_sonyGain.y : u_sonyGain.w) * u2, -0.5, 0.5) * u_sonySat");
    expect(body).toContain("(v2 >= 0.0 ? u_sonyGain.x : u_sonyGain.z) * v2, -0.5, 0.5) * u_sonySat");
  });

  it("suppresses chroma above hiY, and by 1/256 everywhere else", () => {
    // YCC2RGB is plain BT.601, so the two chroma planes can be read back out of
    // the encoded output — which is what lets this compare the stage's effect
    // rather than the RGB it lands on. Both inputs are chosen to stay inside
    // [0, 1] after the round trip, so no clamp can stand in for the effect;
    // that is also why these run at contrast 1 rather than at DSC03015's 1.0547,
    // which alone pushes a highlight past white.
    const chromaOf = (out: number[]): [number, number] => {
      const o = out.map(srgbEncode);
      const y = 0.299 * o[0] + 0.587 * o[1] + 0.114 * o[2];
      return [(o[0] - y) / 1.402, (o[2] - y) / 1.772];
    };
    const ratio = (input: number[], f: number) => {
      const plain = chromaOf(sonyChroma(input, CROSS, GAIN, 0, 1));
      const cut = chromaOf(sonyChroma(input, CROSS, GAIN, 0, 1, 1, SUPPRESS));
      expect(cut[0] / plain[0]).toBeCloseTo(f, 3);
      expect(cut[1] / plain[1]).toBeCloseTo(f, 3);
    };

    // A mid-tone reaches neither knee, and still loses a flat 1/256. That is
    // the part of this stage that looks like an identity until it is measured.
    ratio([0.25, 0.2, 0.15], 255 / 256);
    // A near-white pixel: its luma lands at 15681 of 16383, 321 above hiY, so
    // the engine's `(d * slope) >> 12` takes 40 off the 255 and leaves 215.
    ratio([0.93, 0.93, 0.72], 215 / 256);
  });

  it("keeps ChromaSuppres between the chroma and YGamma, where the engine has it", () => {
    const src = PROCESS_SHADER.slice(PROCESS_SHADER.indexOf("vec3 sonyChroma"));
    const body = src.slice(0, src.indexOf("\n}"));
    expect(body).toContain("float y16 = y * 16383.0;");
    expect(body).toContain("f = 255.0 - floor((y16 - u_sonyCS.x) * u_sonyCS.z / 4096.0);");
    expect(body).toContain("f = 255.0 - floor((u_sonyCS.y - y16) * u_sonyCS.w / 4096.0);");
    expect(body).toContain("f = clamp(f, 0.0, 255.0) / 256.0;");
    // The order is the whole of it: the stage runs on the chroma once it exists
    // and on the luma from *before* YGamma. Moving it after YGamma would index
    // the fade by a luma the engine's own stage never sees.
    expect(body.indexOf("u_sonyCSActive")).toBeGreaterThan(body.indexOf("float cb = clamp"));
    expect(body.indexOf("u_sonyCSActive")).toBeLessThan(body.indexOf("y = clamp((y -"));
  });

  it("pulls the highlights down through Standard's table, and only there", () => {
    // On grey the luma weights sum to one, so the encoded output *is* YGamma's
    // own output and the table can be read straight off it. y = 0.9 lands on
    // index 14744, where Standard's table holds 14120 — a 4.2% cut — while the
    // eight-look table is still identity there. Without the table the stage
    // applies the flat 1.0547 everywhere, which is what left llr's highlights
    // 2-3% bright against Imaging Edge's own export of DSC03036.
    const grey = srgbDecode(0.9);
    const encoded = (rgb: number[]) => srgbEncode(rgb[0]);

    const plain = encoded(sonyChroma([grey, grey, grey], CROSS, GAIN));
    expect(plain).toBeCloseTo(0.9 * LUMA_CONTRAST, 4);

    const knee = encoded(sonyChroma([grey, grey, grey], CROSS, GAIN,
      LUMA_PIVOT, LUMA_CONTRAST, 1, null, lutFrom(KNEE_LUT_SAMPLES)));
    expect(knee).toBeCloseTo((14120 / 16383) * LUMA_CONTRAST, 6);
    expect(plain - knee).toBeCloseTo(0.0403, 3);

    // The other eight looks: identity at this index, so the table costs nothing
    // beyond the engine's own truncation of the index (under 1 in 16383).
    const flat = encoded(sonyChroma([grey, grey, grey], CROSS, GAIN,
      LUMA_PIVOT, LUMA_CONTRAST, 1, null, lutFrom(FLAT_LUT_SAMPLES)));
    expect(Math.abs(flat - plain)).toBeLessThan(2 / 16383);
  });

  it("keeps YGamma's table where the engine has it, and unfiltered", () => {
    const src = PROCESS_SHADER.slice(PROCESS_SHADER.indexOf("vec3 sonyChroma"));
    const body = src.slice(0, src.indexOf("\n}"));
    // texelFetch and not texture(): the engine's index is a truncated integer,
    // and linear filtering across the knee would round off the whole point of
    // the table. The 128-wide fold has to match uploadSonyLumaLUT's layout.
    expect(body).toContain("int idx = int(clamp(floor(y * 16383.0), 0.0, 16383.0));");
    expect(body).toContain("texelFetch(u_sonyLumaLut, ivec2(idx & 127, idx >> 7), 0).r / 16383.0");
    expect(body).not.toContain("texture(u_sonyLumaLut");
    // Before the pivot/contrast line (the engine's order) but after
    // ChromaSuppres, which reads the luma from before all of YGamma.
    expect(body.indexOf("u_sonyLumaLutActive")).toBeLessThan(body.indexOf("y = clamp((y -"));
    expect(body.indexOf("u_sonyLumaLutActive")).toBeGreaterThan(body.indexOf("u_sonyCSActive"));
  });

  it("matches the worker with advanced colour reproduction on", () => {
    // ZcTask3DLut through the whole stage, against the worker's `apply_chroma(
    // lut3d=True)` — the model that reproduces Imaging Edge's own buffers bit
    // for bit on nineteen captured tiles. The expectations are the worker's
    // output pasted as literals (it takes display-*encoded* values, so these
    // were computed on srgbEncode of the input and decoded back).
    //
    // 2 of 16383 is the budget, and what spends it is this port evaluating the
    // warp curve and the forward 2x2 in float32 where the worker uses double;
    // the trilinear itself is the engine's integers on both sides. Measured
    // worst case over the three: 1.5.
    const cases: [number[], number[]][] = [
      // A bright saturated orange — the pixel this stage exists for. It comes
      // down in both luma and chroma (0.861778/0.472369/0.220022 without it).
      [[0.7, 0.42, 0.28], [0.852807, 0.466670, 0.220511]],
      // Mid grey, which the stage does *not* leave alone: it picks up a
      // constant +31 of 16383 on both chroma planes. That bias is the engine's,
      // measured (sony_repro/notes/static-3dlut.md 7), and a port "fixed" to
      // keep neutrals neutral would no longer be the engine.
      [[0.5, 0.5, 0.5], [0.565861, 0.558293, 0.567004]],
      // A saturated cyan-blue, on the other side of both chroma axes — the
      // signs of u and v both flip, which is where a transposed axis shows up.
      [[0.25, 0.42, 0.62], [0.144336, 0.537329, 0.926624]],
    ];
    for (const [input, want] of cases) {
      const got = sonyChroma(input, CROSS, GAIN, LUMA_PIVOT, LUMA_CONTRAST, 1, null, null, true);
      for (let i = 0; i < 3; i++) expect(Math.abs(got[i] - want[i])).toBeLessThanOrEqual(2 / 16383);
      // And it is not a no-op on any of them, which a table fetched from the
      // wrong axis order could easily still satisfy above.
      const plain = sonyChroma(input, CROSS, GAIN);
      expect(Math.max(...got.map((v, i) => Math.abs(v - plain[i])))).toBeGreaterThan(2 / 16383);
    }
  });

  it("swaps YGamma's table and contrast with the same switch", () => {
    // The half of "advanced colour reproduction" that is not the 3-D LUT. Grey
    // reads it cleanly: both chroma differences are zero, so the whole stage is
    // YGamma and then the 3-D LUT on a neutral. The encoded input is 0.75008,
    // which truncates onto table index 12288 — where the eight-look table is
    // identity and its advanced twin holds 12096 — and the contrast goes from
    // this shot's 1.0546875 to 高级's 17280/16384.
    //
    // All three expectations are the worker's own output (sony/chroma.py
    // apply_chroma, which reproduces Imaging Edge's buffers bit for bit),
    // pasted as literals.
    const grey = srgbDecode(0.75008);
    const rgb = [grey, grey, grey];
    const flat = lutFrom(FLAT_LUT_SAMPLES);
    const advFlat = lutFrom(ADV_FLAT_LUT_SAMPLES);
    const close = (got: number[], want: number[]) => {
      for (let i = 0; i < 3; i++) expect(Math.abs(got[i] - want[i])).toBeLessThanOrEqual(2 / 16383);
    };

    // Advanced: both halves.
    close(sonyChroma(rgb, CROSS, GAIN, LUMA_PIVOT, LUMA_CONTRAST, 1, null, flat, true, advFlat),
      [0.570954, 0.563347, 0.572104]);
    // The 3-D LUT alone, which is what this build did before the table and the
    // contrast were found. It is 0.02 brighter — 2% of full scale, and in the
    // direction llr was measured to be off against Edit's own 高级 export.
    close(sonyChroma(rgb, CROSS, GAIN, LUMA_PIVOT, LUMA_CONTRAST, 1, null, flat, true),
      [0.591595, 0.583829, 0.592769]);
    // And the standard path, unmoved by any of this.
    close(sonyChroma(rgb, CROSS, GAIN, LUMA_PIVOT, LUMA_CONTRAST, 1, null, flat),
      [0.588792, 0.588792, 0.588792]);

    // Standard and Neutral get their own advanced table, not a shared one.
    const knee = lutFrom(ADV_KNEE_LUT_SAMPLES);
    expect(knee[12288]).not.toBe(advFlat[12288]);
    expect(sonyChroma(rgb, CROSS, GAIN, LUMA_PIVOT, LUMA_CONTRAST, 1, null,
      lutFrom(KNEE_LUT_SAMPLES), true, knee)[0]).not.toBeCloseTo(0.570954, 4);
  });

  it("has the shader pick the advanced table on the 3-D LUT's flag, or on camera match's", () => {
    const src = PROCESS_SHADER.slice(PROCESS_SHADER.indexOf("vec3 sonyChroma"));
    const body = src.slice(0, src.indexOf("\n}"));
    // The 3-D LUT flag drives both halves — that is what makes 高级 Imaging
    // Edge's single 色彩复制 setting. Camera match is the one other caller: it
    // takes the advanced luma pair (the camera's highlight roll-off) without
    // the LUT, via u_sonyLumaAdvForce. The arrival guard stays: an older
    // profile response carries no advanced table, and 高级's contrast against
    // 标准's table would be neither setting.
    expect(body).toContain("bool adv = u_sonyLumaLutAdvActive == 1 && (u_sonyLut3dActive == 1 || u_sonyLumaAdvForce == 1);");
    expect(body).toContain("y = texelFetch(u_sonyLumaLutAdv, ivec2(idx & 127, idx >> 7), 0).r / 16383.0;");
    expect(body).not.toContain("texture(u_sonyLumaLutAdv");
    // The advanced fetch wins over the standard one rather than running after
    // it: two lookups in a row would index the second table by the first
    // table's output.
    expect(body.indexOf("u_sonyLumaLutAdv,")).toBeLessThan(body.indexOf("u_sonyLumaLut,"));
    expect(body).toContain("} else if (u_sonyLumaLutActive == 1) {");
    // Still before the pivot/contrast line and after ChromaSuppres.
    expect(body.indexOf("bool adv =")).toBeGreaterThan(body.indexOf("u_sonyCSActive"));
    expect(body.indexOf("bool adv =")).toBeLessThan(body.indexOf("y = clamp((y -"));
  });

  it("keeps the 3-D LUT's shifts, gray code and inverse matrix in the shader", () => {
    const src = PROCESS_SHADER.slice(PROCESS_SHADER.indexOf("vec3 sonyChroma"));
    const body = src.slice(0, src.indexOf("\n}"));
    // The two splits: 5+11 bits on each chroma axis, 5+9 on Y. Getting either
    // shift wrong lands in a different cell entirely.
    expect(body).toContain("int iu = a >> 11, f0 = a & 2047;");
    expect(body).toContain("int iy = yPlane >> 9, yf = yPlane & 511;");
    // w0 is the remainder of the other seven — that is what pins the weight sum
    // at 512 and parks the rounding on one corner. Computing it like the rest
    // is a ±1 error the engine does not make.
    expect(body).toContain("int w0 = 512 - w7 - w6 - w5 - w4 - w3 - w2 - w1;");
    expect(body).toContain("acc >>= 9;");
    // The corner order is gray-coded, not binary: slot 2 is (f1,f0,yf)=011.
    expect(body).toContain("sonyLut3dPoint(iu + 1, iv,     iy + 1) * w2");
    expect(body).toContain("sonyLut3dPoint(iu,     iv + 1, iy    ) * w4");
    // The exit is the inverse of the entry matrix, and Y clamps only downward.
    expect(body).toContain("y = float(max(acc.x, 0)) / 16383.0;");
    expect(body).toContain("cr = (float(acc.y) * 0.713273 - float(acc.z) * 1.5e-05) / 16383.0;");
    expect(body).toContain("cb = (float(acc.z) * 0.564341 - float(acc.y) * 5.4e-05) / 16383.0;");
    // After YGamma and before the BT.601 return trip, which is the engine's own
    // order — running it on the pre-YGamma luma would index the wrong Y plane.
    // The flag itself is read earlier, because it also selects YGamma's table;
    // it is the `if` block that has to sit here, so match on that and not on
    // the first mention of the uniform.
    const block = "if (u_sonyLut3dActive == 1) {";
    expect(body.indexOf(block)).toBeGreaterThan(body.indexOf("y = clamp((y -"));
    expect(body.indexOf(block)).toBeLessThan(body.indexOf("vec3 o = clamp"));
    // texelFetch and never texture(): the grid is an integer texture and the
    // shader does its own interpolation with the engine's weights.
    expect(PROCESS_SHADER).toContain("texelFetch(u_sony_lut3d, ivec3(iy, iv, iu), 0).rgb");
    expect(PROCESS_SHADER).not.toContain("texture(u_sony_lut3d");
    // 32-bit ints are load-bearing here: the weight products reach 2^28, and a
    // fragment shader's int is mediump — 16 bits — unless this says otherwise.
    expect(PROCESS_SHADER).toContain("precision highp int;");
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
