import { describe, expect, it } from "vitest";
import {
  SPICA_CODE_COUNT,
  SPICA_LUT,
  SPICA_TABLE_COUNT,
  SPICA_TAP_COUNT,
  SPICA_WEIGHTS,
  SPICA_WEIGHT_SUM,
} from "../spica-tables";
import { SPICA_CURVE_DETAIL, SPICA_CURVE_MID, SPICA_CURVE_RANGE, SPICA_SHADER } from "../passes";

// The tables are generated from Edit.exe's own memory
// (sony_repro/tools/spica_export_tables.py) and are the only copy — nothing
// downstream re-derives them, so a truncated or mis-transcribed regeneration
// would render wrong with nothing to compare against. These are the invariants
// the reverse-engineering established, checked against the shipped file.
describe("Spica weight tables", () => {
  it("has every table the classifier can reach", () => {
    expect(SPICA_WEIGHTS.length).toBe(SPICA_TABLE_COUNT * SPICA_TAP_COUNT);
    expect(SPICA_LUT.length).toBe(SPICA_CODE_COUNT * 3);
  });

  it("normalises every table to 512, so a flat patch comes through unchanged", () => {
    // This is what makes `S - 512 * centre` a pure detail term. A table that
    // summed to anything else would shift brightness wherever it was selected,
    // and the classifier picks tables per pixel — so it would shift brightness
    // in patches, which is far harder to spot than a global shift.
    for (let table = 0; table < SPICA_TABLE_COUNT; table++) {
      let sum = 0;
      for (let k = 0; k < SPICA_TAP_COUNT; k++) sum += SPICA_WEIGHTS[table * SPICA_TAP_COUNT + k];
      expect(sum, `table ${table}`).toBe(SPICA_WEIGHT_SUM);
    }
  });

  it("is a family of band-pass kernels, not a family of blurs", () => {
    // Every table has negative weights somewhere — six of the twenty-five at
    // the least. A regeneration that lost the sign anywhere would turn that
    // table into a weighted mean, and the stage would soften the patterns it
    // selected instead of resolving them, which is invisible in an average and
    // obvious only on the pixels that pattern happens to cover.
    for (let table = 0; table < SPICA_TABLE_COUNT; table++) {
      const w = SPICA_WEIGHTS.slice(table * SPICA_TAP_COUNT, (table + 1) * SPICA_TAP_COUNT);
      expect(w.some(v => v < 0), `table ${table}`).toBe(true);
      expect(w.some(v => v > 0), `table ${table}`).toBe(true);
    }
  });

  it("does not sharpen on every pattern, which is the point of classifying", () => {
    // Tap 12 is the diamond's centre. A centre above 512 with the taps summing
    // to 512 makes a table a sharpener; 27 of the 100 sit below it and one
    // goes negative, so those patterns get directional reconstruction instead.
    // Pinned as a spread rather than as exact counts: what a regeneration must
    // not do is collapse the family into one behaviour, which is exactly what
    // an off-by-one row stride or a truncated dump would produce.
    const CENTRE = 12;
    const centres = Array.from({ length: SPICA_TABLE_COUNT },
      (_, t) => SPICA_WEIGHTS[t * SPICA_TAP_COUNT + CENTRE]);
    expect(centres.filter(c => c > SPICA_WEIGHT_SUM).length).toBeGreaterThan(SPICA_TABLE_COUNT / 2);
    expect(centres.filter(c => c < SPICA_WEIGHT_SUM).length).toBeGreaterThan(0);
  });

  it("keeps every LUT entry in range, with both mirrors always +-1", () => {
    // The shader indexes the weight texture by row with this table number and
    // reads the mirrored tap through GRID[]; an out-of-range table samples
    // another table's weights, and a mirror other than +-1 walks off the 7x7
    // grid into GRID's -1 sentinel. Neither raises anything at draw time.
    for (let code = 0; code < SPICA_CODE_COUNT; code++) {
      const [table, dx, dy] = [SPICA_LUT[code * 3], SPICA_LUT[code * 3 + 1], SPICA_LUT[code * 3 + 2]];
      expect(table, `code ${code}`).toBeGreaterThanOrEqual(0);
      expect(table, `code ${code}`).toBeLessThan(SPICA_TABLE_COUNT);
      expect(Math.abs(dx), `code ${code}`).toBe(1);
      expect(Math.abs(dy), `code ${code}`).toBe(1);
    }
  });

  it("sends a flat neighbourhood to the isotropic table", () => {
    // Code 0 is what the shader takes when the local range is under the
    // threshold, without classifying at all. Its table has to be symmetric
    // under both mirrors or a flat patch would pick up a direction.
    expect(SPICA_LUT[0]).toBe(0);
    const w = Array.from(SPICA_WEIGHTS.slice(0, SPICA_TAP_COUNT));
    expect(w).toEqual([...w].reverse());
  });
});

// The gain curves are duplicated on purpose: the shader needs them inlined as
// GLSL constants and the worker documents them beside the setting ladders
// (sony/spica.py). Duplication is fine; drift is not, and only the shader's
// copy affects a render — so check the emitted source, not the array.
describe("Spica gain curves", () => {
  const emitted = (name: string) => {
    const m = SPICA_SHADER.match(new RegExp(`const vec4 ${name} = vec4\\(([^)]*)\\);`));
    const v = SPICA_SHADER.match(new RegExp(`const vec2 ${name}_V = vec2\\(([^)]*)\\);`));
    if (!m || !v) throw new Error(`${name} not emitted`);
    return [...m[1].split(","), ...v[1].split(",")].map(s => parseFloat(s));
  };

  for (const [name, curve] of [
    ["C_DETAIL", SPICA_CURVE_DETAIL], ["C_RANGE", SPICA_CURVE_RANGE], ["C_MID", SPICA_CURVE_MID],
  ] as const) {
    it(`${name} reaches the shader intact`, () => {
      emitted(name).forEach((got, i) => expect(got).toBeCloseTo(curve[i], 3));
    });
  }

  it("keeps the flanks non-degenerate, so neither divides by zero", () => {
    // spicaTrap divides by (b - a) and (d - c). Both are safe as shipped, and
    // both would produce NaN across a whole band of pixels if a regenerated
    // curve ever collapsed one of them.
    for (const c of [SPICA_CURVE_DETAIL, SPICA_CURVE_RANGE, SPICA_CURVE_MID]) {
      expect(c[1] - c[0]).toBeGreaterThan(0);
      expect(c[3] - c[2]).toBeGreaterThan(0);
    }
  });

  it("leaves the plateau unreachable on range and midpoint", () => {
    // b > c on those two, which is why spicaTrap has to be written as the
    // engine's chain of tests rather than as interval conditions: an interval
    // form would answer from a segment the engine never reaches. Detail is the
    // one curve with a real plateau, and that asymmetry is deliberate.
    expect(SPICA_CURVE_RANGE[1]).toBeGreaterThan(SPICA_CURVE_RANGE[2]);
    expect(SPICA_CURVE_MID[1]).toBeGreaterThan(SPICA_CURVE_MID[2]);
    expect(SPICA_CURVE_DETAIL[1]).toBeLessThan(SPICA_CURVE_DETAIL[2]);
  });
});

// The mirroring is a permutation of the 25 taps rather than a re-sample, which
// is what lets the shader fetch the diamond once. That only holds because the
// diamond is symmetric under both flips — check it here rather than trusting
// the GRID[] the shader builds from the same list.
describe("Spica tap mirroring", () => {
  const taps: [number, number][] = [];
  for (let ty = -3; ty <= 3; ty++) {
    for (let tx = -3; tx <= 3; tx++) if (Math.abs(ty) + Math.abs(tx) <= 3) taps.push([ty, tx]);
  }

  it("has the 25 taps the tables are sized for", () => {
    expect(taps.length).toBe(SPICA_TAP_COUNT);
  });

  it("maps onto itself under every mirroring", () => {
    const key = (ty: number, tx: number) => `${ty},${tx}`;
    const inDiamond = new Set(taps.map(([ty, tx]) => key(ty, tx)));
    for (const dy of [1, -1]) {
      for (const dx of [1, -1]) {
        for (const [ty, tx] of taps) {
          expect(inDiamond.has(key(ty * dy, tx * dx)), `(${ty},${tx}) x (${dx},${dy})`).toBe(true);
        }
      }
    }
  });

  it("keeps the classifier's nine points inside the diamond", () => {
    // The cross reaches +-2 and the diamond +-3, so the classifier needs no
    // fetches of its own. If it ever did, the shader's CROSS[] would index
    // past the 25 it gathered.
    const cross: [number, number][] = [
      [-2, 0], [-1, 0], [0, -2], [0, -1], [0, 0], [0, 1], [0, 2], [1, 0], [2, 0],
    ];
    for (const [ty, tx] of cross) {
      expect(taps.some(([y, x]) => y === ty && x === tx), `(${ty},${tx})`).toBe(true);
    }
  });
});
