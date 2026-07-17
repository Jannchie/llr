import { describe, expect, it } from "vitest";
import { binScaler, clipIndicators, robustMax, type HistogramBins } from "../histogram";

function makeBins(fill = 0): HistogramBins {
  return {
    r: new Uint32Array(256).fill(fill),
    g: new Uint32Array(256).fill(fill),
    b: new Uint32Array(256).fill(fill),
    l: new Uint32Array(256).fill(fill),
  };
}

describe("robustMax", () => {
  it("ignores the clipping bins so a blown background cannot flatten the plot", () => {
    const bins = makeBins();
    bins.r[0] = 1_000_000;
    bins.b[255] = 2_000_000;
    bins.g[128] = 500;
    expect(robustMax(bins)).toBe(500);
  });

  it("never returns less than 1 (empty histogram stays divisible)", () => {
    expect(robustMax(makeBins())).toBe(1);
  });

  it("takes the max across all four channels", () => {
    const bins = makeBins();
    bins.l[42] = 900;
    bins.r[42] = 100;
    expect(robustMax(bins)).toBe(900);
  });
});

describe("binScaler", () => {
  it("maps 0 to 0 and the max count to 1 for every scale", () => {
    for (const scale of ["linear", "sqrt", "log"] as const) {
      const norm = binScaler(scale, 1000);
      expect(norm(0), scale).toBe(0);
      expect(norm(1000), scale).toBeCloseTo(1, 5);
    }
  });

  it("clamps counts above the robust max (edge bars draw to the top)", () => {
    expect(binScaler("linear", 100)(1_000_000)).toBe(1);
    expect(binScaler("sqrt", 100)(1_000_000)).toBe(1);
    expect(binScaler("log", 100)(1_000_000)).toBe(1);
  });

  it("sqrt lifts small counts above linear, log lifts them above sqrt", () => {
    const c = 10, max = 1000;
    const lin = binScaler("linear", max)(c);
    const sqrt = binScaler("sqrt", max)(c);
    const log = binScaler("log", max)(c);
    expect(lin).toBeCloseTo(0.01, 5);
    expect(sqrt).toBeGreaterThan(lin);
    expect(log).toBeGreaterThan(sqrt);
  });
});

describe("clipIndicators", () => {
  // 1M samples → fractional threshold 200 dominates the 3px floor.
  function busyBins(): HistogramBins {
    const bins = makeBins();
    bins.l.fill(4000); // ~1M total
    return bins;
  }

  it("stays dark below the threshold — a handful of black pixels is normal", () => {
    const bins = busyBins();
    bins.r[0] = 50;
    bins.g[0] = 50;
    bins.b[0] = 50;
    expect(clipIndicators(bins)).toEqual({ shadow: null, highlight: null });
  });

  it("encodes which channels rail in the indicator colour", () => {
    const bins = busyBins();
    bins.r[255] = 5000;
    expect(clipIndicators(bins).highlight).toBe("rgb(255, 0, 0)");
    bins.g[255] = 5000;
    expect(clipIndicators(bins).highlight).toBe("rgb(255, 255, 0)");
    bins.b[255] = 5000;
    expect(clipIndicators(bins).highlight).toBe("rgb(255, 255, 255)");
    expect(clipIndicators(bins).shadow).toBeNull();
  });

  it("applies the absolute pixel floor when the sample count is tiny", () => {
    const bins = makeBins();
    bins.l[128] = 100; // tiny total → fraction threshold < 3px floor
    bins.r[0] = 3;
    expect(clipIndicators(bins).shadow).toBeNull(); // needs > 3
    bins.r[0] = 4;
    expect(clipIndicators(bins).shadow).toBe("rgb(255, 0, 0)");
  });
});
