import { describe, expect, it } from "vitest";
import { binScaler, clipIndicators, measureHint, measureHistogram, measurePixels, robustMax, type HistogramBins, type HistogramMeasure } from "../histogram";

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

describe("measureHistogram", () => {
  it("reads percentiles off the cumulative count and the mean off the weights", () => {
    const bins = makeBins();
    bins.l[10] = 50; bins.l[100] = 900; bins.l[240] = 50;
    const m = measureHistogram(bins);
    expect(m.luminance.p1).toBe(10);
    expect(m.luminance.p5).toBe(10);
    expect(m.luminance.p50).toBe(100);
    expect(m.luminance.p95).toBe(100);
    expect(m.luminance.p99).toBe(240);
    expect(m.luminance.mean).toBe(Math.round((500 + 90_000 + 12_000) / 1000));
  });

  it("names the railed channels with clipIndicators' threshold", () => {
    const bins = makeBins();
    bins.l[128] = 10_000; bins.l[255] = 200;
    bins.r[255] = 200; bins.g[255] = 150; bins.b[255] = 1;
    const m = measureHistogram(bins);
    expect(m.clipped.highlights_pct).toBeCloseTo(2, 5);
    expect(m.clipped.highlight_channels).toBe("R+G");
    expect(m.clipped.shadow_channels).toBeNull();
    expect(m.clipped.shadows_pct).toBe(0);
  });

  it("survives an empty histogram", () => {
    const m = measureHistogram(makeBins());
    expect(m.luminance).toEqual({ p1: 0, p5: 0, p50: 0, p95: 0, p99: 0, mean: 0 });
    expect(m.clipped.highlight_channels).toBeNull();
  });
});

describe("measurePixels", () => {
  it("splits rows into thirds and measures chroma as max-min", () => {
    // 1 px wide, 6 rows: two black, two grey, two pure red.
    const rows = [[0, 0, 0], [0, 0, 0], [128, 128, 128], [128, 128, 128], [255, 0, 0], [255, 0, 0]];
    const rgba = new Uint8ClampedArray(rows.flatMap(([r, g, b]) => [r, g, b, 255]));
    const m = measurePixels(rgba, 1, 6);
    expect(m.regions).toEqual({ top: 0, middle: 128, bottom: Math.round(0.2126 * 255) });
    expect(m.chroma_mean).toBeCloseTo(2 / 6, 2);
  });
});

describe("measureHint", () => {
  const base = (): HistogramMeasure => ({
    luminance: { p1: 5, p5: 12, p50: 110, p95: 220, p99: 245, mean: 112 },
    clipped: { shadows_pct: 0, highlights_pct: 0, shadow_channels: null, highlight_channels: null },
  });
  it("stays silent when nothing trips", () => {
    expect(measureHint(base())).toBeUndefined();
  });
  it("speaks up for clipping and a dark median", () => {
    const m = base();
    m.clipped.highlights_pct = 1.8; m.clipped.highlight_channels = "R+G";
    m.luminance.p50 = 30;
    expect(measureHint(m)).toBe("highlights clip in R+G: lower highlights/whites or exposure; median is dark; the picture may read as underexposed");
  });
});
