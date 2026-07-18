/**
 * Color Grading region weights: a TS mirror of the shader block in passes.ts
 * (PROCESS_SHADER, "Color Grading"), sharing its GRAD_* constants. Pins the
 * Balance slider's direction and the invariant that costs the wheels nothing
 * to state and everything to violate — no wheel may be translated off the
 * [0,1] luma axis, because a wheel with an empty region is a silent no-op.
 */
import { describe, expect, it } from "vitest";
import {
  GRAD_BAL_SPAN, GRAD_HL_EDGE0, GRAD_HL_EDGE1, GRAD_SH_EDGE0, GRAD_SH_EDGE1, PROCESS_SHADER,
} from "../passes";

function smoothstep(e0: number, e1: number, x: number): number {
  const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0)));
  return t * t * (3 - 2 * t);
}

/** Shadow / midtone / highlight wheel weights at display luma `lg`. */
function gradWeights(lg: number, balance: number): { sh: number; md: number; hl: number } {
  const bal = -balance * GRAD_BAL_SPAN;
  const sh = 1 - smoothstep(GRAD_SH_EDGE0 + bal, GRAD_SH_EDGE1 + bal, lg);
  const hl = smoothstep(GRAD_HL_EDGE0 + bal, GRAD_HL_EDGE1 + bal, lg);
  return { sh, md: (1 - sh) * (1 - hl), hl };
}

// lg is the ProPhoto luma of a colour the curve block clamped to [0,1], so it
// can only ever land here.
const AXIS = Array.from({ length: 201 }, (_, i) => i / 200);
const BALANCES = [-1, -0.5, 0, 0.5, 1];

describe("color grading balance", () => {
  it("moves the Highlights wheel's effect in the direction of the label", () => {
    // A bright-but-not-white tone: Balance right must pull it into Highlights.
    const at = (b: number) => gradWeights(0.6, b);
    expect(at(1).hl).toBeGreaterThan(at(0).hl);
    expect(at(0).hl).toBeGreaterThan(at(-1).hl);
    // ...and the Shadows wheel the other way, on a dark-but-not-black tone.
    const dark = (b: number) => gradWeights(0.35, b);
    expect(dark(-1).sh).toBeGreaterThan(dark(0).sh);
    expect(dark(0).sh).toBeGreaterThan(dark(1).sh);
  });

  it("keeps every wheel's region on the axis at the extremes", () => {
    for (const b of BALANCES) {
      for (const k of ["sh", "md", "hl"] as const) {
        const peak = Math.max(...AXIS.map((lg) => gradWeights(lg, b)[k]));
        expect(peak).toBeCloseTo(1, 6);
      }
    }
  });

  it("keeps all three weights in [0,1] across the axis", () => {
    for (const b of BALANCES) {
      for (const lg of AXIS) {
        const w = gradWeights(lg, b);
        for (const v of [w.sh, w.md, w.hl]) {
          expect(v).toBeGreaterThanOrEqual(0);
          expect(v).toBeLessThanOrEqual(1);
        }
      }
    }
  });

  it("shader and mirror share one definition of the block", () => {
    expect(PROCESS_SHADER).toContain("float bal = -u_grad_balance * GRAD_BAL_SPAN;");
    expect(PROCESS_SHADER).toContain(`const float GRAD_BAL_SPAN = ${GRAD_BAL_SPAN};`);
    // The luminance renorm must stay capped: uncapped, saturated cool tints
    // blow past the gamut map and the wheels read as warm-only (see grading.ts).
    expect(PROCESS_SHADER).toContain("min(lg / lt, GRAD_RENORM_CAP)");
  });
});
