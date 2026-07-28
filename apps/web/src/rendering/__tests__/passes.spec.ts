import { describe, expect, it } from "vitest";
import {
  MASK_VERTEX_SHADER,
  PASSES,
  PROCESS_SHADER,
  SHARPEN_BINOMIAL,
  SHARPEN_BLUR_W,
  SHARPEN_CENTER,
  SHARPEN_DEADZONE,
  SHARPEN_NEAR_W,
  SONY_POST_PROGRAMS,
  SONY_POST_SHADER,
  VERTEX_SHADER,
} from "../passes";

// setUniforms silently skips any name missing from PASSES[0].uniforms (the
// location lookup returns null), so a shader uniform that is declared but not
// registered is never set — with no error anywhere. Keep the two lists in
// lock-step here instead.
describe("PROCESS_SHADER uniform registry", () => {
  // Samplers bound once at draw time, fetched by name outside the registry.
  const SAMPLERS = new Set(["u_input", "u_curve_lut", "u_profile_lut", "u_mask_lum"]);

  const declared = new Set<string>();
  // u_texXform lives in the vertex shader; scan both stages of the program.
  for (const m of (PROCESS_SHADER + VERTEX_SHADER).matchAll(/^uniform\s+\w+\s+(\w+)(?:\[(\d+)\])?;/gm)) {
    const [, name, count] = m;
    if (count) {
      for (let i = 0; i < Number(count); i++) declared.add(`${name}[${i}]`);
    } else {
      declared.add(name);
    }
  }
  const registered = new Set(PASSES[0].uniforms);

  it("declares at least the known uniform count (regex sanity)", () => {
    expect(declared.size).toBeGreaterThan(20);
  });

  it("every declared uniform is registered (or a known sampler)", () => {
    const missing = [...declared].filter(n => !registered.has(n) && !SAMPLERS.has(n.replace(/\[\d+\]$/, "")));
    expect(missing).toEqual([]);
  });

  it("every registered uniform is declared in the shader", () => {
    const stale = [...registered].filter(n => !declared.has(n));
    expect(stale).toEqual([]);
  });
});

// The same lock-step check for the four offscreen programs of Sony's post
// chain. They resolve their locations from SONY_POST_PROGRAMS, so a name that
// drifts from the shader yields a null location and gl.uniform1f(null, x) does
// nothing at all — the stage stops running with no error to notice.
describe("Sony post-chain uniform registry", () => {
  for (const [name, def] of Object.entries(SONY_POST_PROGRAMS)) {
    const declared = new Set<string>();
    for (const m of (def.fsSource + MASK_VERTEX_SHADER).matchAll(/^uniform\s+\w+\s+(\w+);/gm)) {
      declared.add(m[1]);
    }
    const registered = new Set<string>(def.uniforms);

    it(`${name}: every declared uniform is registered`, () => {
      expect([...declared].filter(n => !registered.has(n))).toEqual([]);
    });

    it(`${name}: every registered uniform is declared`, () => {
      expect([...registered].filter(n => !declared.has(n))).toEqual([]);
    });
  }
});

// The sharpening kernel is hard-coded in the shader rather than shipped by the
// worker — it is the operator's shape, not per-shot calibration. That makes the
// GLSL the only copy, so the invariants have to be checked against the emitted
// source rather than against a mirror of the numbers in some other language.
describe("SONY_POST_SHADER sharpen kernel", () => {
  it("has taps that cancel, so flat areas give exactly nothing", () => {
    // 25.6 - 10.24 - 4*3.84 = 0. Without this the stage would shift the whole
    // image's brightness instead of extracting detail, and the dead zone —
    // which compares against the high-pass itself — would stop protecting
    // flat sky. Zero only to within binary floating point: none of the three
    // is representable, so the residue is ~4e-15.
    expect(SHARPEN_CENTER - SHARPEN_BLUR_W - 4 * SHARPEN_NEAR_W).toBeCloseTo(0, 12);
  });

  it("normalises the binomial by the square of its own sum", () => {
    // The 7x7 blur is the outer product of SHARPEN_BINOMIAL with itself, so the
    // divisor in the shader has to be sum^2. A mismatch here scales the whole
    // detail term without changing its shape — invisible by eye, and exactly
    // the class of error tools/sharp_shader_check.py exists to catch.
    const sum = SHARPEN_BINOMIAL.reduce((a, b) => a + b, 0);
    expect(sum).toBe(64);
    expect(SHARPEN_BINOMIAL).toEqual([...SHARPEN_BINOMIAL].reverse());
    expect(SONY_POST_SHADER).toContain(`blur / ${sum * sum}.0`);
  });

  it("puts the dead zone on the engine's own 1024, and tests hp not amp*hp", () => {
    // Comparing amp*hp instead would let a stronger setting admit new pixels,
    // which is the opposite of what Sony's stage does.
    expect(SHARPEN_DEADZONE * 16383).toBeCloseTo(1024, 6);
    expect(SONY_POST_SHADER).toContain("abs(hp) < DEADZONE");
  });

  it("weights the centre tap without fetching it a second time", () => {
    // `rgb` is already in hand from the fetch at the top of main(); the loop
    // skips (0,0) and the centre's own binomial weight is added from it.
    const body = SONY_POST_SHADER.slice(SONY_POST_SHADER.indexOf("void main()"));
    expect(body).toContain("if (i == 0 && j == 0) continue;");
    expect(body).toContain(`${SHARPEN_BINOMIAL[3] * SHARPEN_BINOMIAL[3]}.0 * rgb`);
  });

  it("runs sharpening before Clarity, as the engine does", () => {
    // Sharpness -> Spica -> Marble. Clarity has to see the sharpened luma, so
    // its branch must come second and read the accumulated delta.
    const body = SONY_POST_SHADER.slice(SONY_POST_SHADER.indexOf("void main()"));
    expect(body.indexOf("u_sharpen > 0.0")).toBeLessThan(body.indexOf("u_gain > 0.0"));
    expect(body).toContain("float ys = y + delta;");
  });
});

// Sony runs DRO right after demosaic — before the colour matrix, and so before
// anything the user does. Applying it after the matrix is only equivalent
// because it is a scalar gain; applying it after white balance or exposure is
// not equivalent at all, because then it reads a luminance the camera never saw
// and picks the wrong point on the curve.
describe("DRO placement", () => {
  const body = PROCESS_SHADER.slice(PROCESS_SHADER.lastIndexOf("void main()"));
  const at = (needle: string) => {
    const i = body.indexOf(needle);
    expect(i, `not found: ${needle}`).toBeGreaterThan(-1);
    return i;
  };

  it("gains the pixel before white balance and exposure", () => {
    const fetch = at("texture(u_input, lensUV)");
    const dro = at("u_droActive == 1");
    expect(dro).toBeGreaterThan(fetch);
    expect(dro).toBeLessThan(at("u_wbMatrix"));
    expect(dro).toBeLessThan(at("u_exposure != 0.0"));
  });

  it("scales all three channels by one number", () => {
    // A per-channel lookup here would tint the shadows: the engine's stage owes
    // its colour neutrality entirely to being a single gain.
    expect(body).toContain("c *= texture(u_dro_lut");
  });

  it("indexes the curve by the grid's local mean when there is a grid", () => {
    // The whole difference between DRO and a global tone curve is that the
    // curve is indexed by the neighbourhood, not by the pixel. Falling back to
    // the pixel's own ylog is right only when no grid arrived.
    expect(body).toContain("u_droGridActive == 1 ? droLocalMean(lensUV, ylog) : ylog");
  });

  it("divides num by den only after interpolating both", () => {
    // Mlog is not the interpolation of num/den, so storing the ratio per node —
    // or leaning on hardware trilinear filtering to interpolate it — would be
    // subtly and permanently wrong. The eight taps accumulate a vec2 and divide
    // once, at the end.
    const fn = PROCESS_SHADER.slice(
      PROCESS_SHADER.indexOf("float droLocalMean"),
      PROCESS_SHADER.lastIndexOf("void main()"),
    );
    expect(fn).toContain("acc += w * texelFetch(u_dro_grid");
    expect(fn).toContain("acc.x / acc.y");
    expect(fn.indexOf("acc += w *")).toBeLessThan(fn.indexOf("acc.x / acc.y"));
    // texelFetch, not texture(): linear filtering would interpolate the ratio.
    expect(fn).not.toContain("texture(u_dro_grid");
  });
});
