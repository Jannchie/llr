/**
 * The per-shot numbers Marble's chroma cleanup runs on, ported from the
 * worker's bit-exact reference (sony/marble.py: `slider_params`,
 * `blend_amount`, `marble_block`). The shaders themselves are the marble*
 * passes in passes.ts; this file turns what the worker sends plus the panel's
 * colour-NR slider into their uniforms.
 *
 * Everything here keeps the engine's integer semantics — the truncations are
 * toward zero because the engine's are — so the thresholds the shader gets are
 * the ones the engine's own buffers were verified against, not a float
 * approximation of them.
 */

/** Wire form of the threshold calibration (marble.py CALIB_7CM2, per body). */
export interface MarbleCalib {
  p30: number; p34: number; p38: number; p3c: number; p40: number; p44: number; p48: number;
  base_4c: number; base_50: number; base_54: number; base_58: number;
  lo1: number; hi1: number; lo2: number; hi2: number; strength: number;
}

/** `profileMarble` as the worker sends it (marble.py marble_block). */
export interface ProfileMarble {
  iso: number;
  // The blend amount at the default slider, informational: the browser
  // recomputes it (marbleBlendAmount) because only it knows the slider.
  amountAuto: number;
  calib: MarbleCalib;
}

/** Edit's 色彩降噪 slider position that is Auto — the panel's default. */
export const MARBLE_SLIDER_AUTO = 5;

/** The thresholds after the slider (marble.py slider_params). */
export interface MarbleParams {
  p30: number; p34: number; p38: number; p3c: number; p40: number; p44: number; p48: number;
  p4c: number; p50: number; p54: number; p58: number;
  lo1: number; hi1: number; lo2: number; hi2: number; strength: number;
  esi: number;
}

/**
 * Edit's manual colour-NR slider (0..10, 5 = Auto) applied to the calibration.
 * opts[0x224] = 10*(slider-5); esi = trunc(v/20)+5, ebx = -trunc(v/20). Above 5
 * the threshold gain grows 8/5 per step; below it the gain shrinks linearly
 * and the blur mixes less of the centre. Every division truncates toward zero
 * the way the engine's imul/sar idiom does.
 */
export function marbleSliderParams(calib: MarbleCalib, slider: number = MARBLE_SLIDER_AUTO): MarbleParams {
  const v = 10 * (Math.trunc(slider) - 5);
  const q = Math.trunc(v / 20);
  const esi = q + 5;
  const ebx = -q;
  const gain = (base: number) =>
    esi > 5 ? Math.trunc(((esi - 5) * base * 8) / 5) + base : Math.trunc((base * esi) / 5);
  return {
    p30: calib.p30, p34: calib.p34, p38: calib.p38,
    p3c: calib.p3c, p40: calib.p40, p44: calib.p44, p48: calib.p48,
    p4c: gain(calib.base_4c),
    p50: gain(calib.base_50),
    p54: Math.trunc((ebx * calib.base_54) / 5) + calib.base_54,
    p58: Math.trunc((ebx * calib.base_58) / 5) + calib.base_58,
    lo1: calib.lo1, hi1: calib.hi1, lo2: calib.lo2, hi2: calib.hi2,
    strength: calib.strength,
    esi,
  };
}

/**
 * How much of the cleaned chroma replaces the original (marble.py
 * blend_amount, engine 0x140395dd0): 0.5 at ISO <= 100 rising linearly to 1.0
 * at ISO 1600, then ramped towards 1 by the slider above its midpoint. float32
 * throughout like the engine, so the numbers agree to the last bit.
 */
export function marbleBlendAmount(iso: number, slider: number = MARBLE_SLIDER_AUTO): number {
  const f = Math.fround;
  const isoInt = Math.round(iso);
  let x4: number;
  if (isoInt >= 1600) {
    x4 = 1;
  } else {
    const t = f(f(isoInt - 100) / f(1500));
    x4 = f(f(f(1 - t) * f(0.5)) + f(t * 1));
  }
  const esi = Math.trunc((10 * (Math.trunc(slider) - 5)) / 20) + 5;
  if (esi <= 5) return x4;
  const t2 = f(f(esi - 5) / f(5));
  return f(f(f(1 - t2) * x4) + t2);
}

/** round(32768 / n) as the engine's reciprocal table has it (marble.py _RECIP256). */
function recip256(n: number): number {
  if (n === 0) return 0;
  if (n === 1) return 32768;
  return Math.floor(32768 / n + 0.5);
}

/**
 * What the four marble* passes read, resolved from the profile and the
 * slider. Plain numbers so a profile rebuild is one uniform upload and a
 * redraw. `amount` of zero means the stage has nothing to do.
 */
export interface MarbleUniforms {
  // cnr2 thresholds: (p30, p34, p38), (p3c, p40, p4c), (p44, p48, p50).
  thrY: [number, number, number];
  thrC1: [number, number, number];
  thrC2: [number, number, number];
  // cnr3: how much of the centre survives the 3x3 blur, per plane, 0..1.
  centreMix: [number, number];
  // cnr4 protect: (lo1*256, r1, lo2*256, r2) with r = round(32768/(hi-lo)),
  // negative when hi < lo, exactly as the engine's reciprocal lookup has it.
  protect: [number, number, number, number];
  strength: number;
  amount: number;
}

export function marbleUniforms(profile: ProfileMarble, slider: number = MARBLE_SLIDER_AUTO): MarbleUniforms {
  const p = marbleSliderParams(profile.calib, slider);
  const r1 = p.hi1 >= p.lo1 ? recip256(p.hi1 - p.lo1) : -recip256(p.lo1 - p.hi1);
  const r2 = p.hi2 >= p.lo2 ? recip256(p.hi2 - p.lo2) : -recip256(p.lo2 - p.hi2);
  return {
    thrY: [p.p30, p.p34, p.p38],
    thrC1: [p.p3c, p.p40, p.p4c],
    thrC2: [p.p44, p.p48, p.p50],
    centreMix: [p.p54 / 256, p.p58 / 256],
    protect: [p.lo1 * 256, r1, p.lo2 * 256, r2],
    strength: p.strength,
    amount: marbleBlendAmount(profile.iso, slider),
  };
}
