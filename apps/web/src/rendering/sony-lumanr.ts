/**
 * BSNR_Y's parameters for an M/S-size (YCbCr) Sony frame, from the sliders.
 *
 * A port of worker sony/lumanr.py ycc_luma_nr_params, pinned to it by
 * __tests__/sony-lumanr.spec.ts (fixture lumanr_ycc.json, generated from the
 * worker), which is pinned in turn to the engine's own arguments captured on
 * two frames (apps/worker/tests/test_lumanr.py). The shader that consumes
 * these is passes.ts BSNR_SHADER; the stage itself is described there.
 *
 * Integer arithmetic with truncation toward zero throughout, because that is
 * how the engine builds its tables: a port that rounded would land a step off
 * at every slider position that is not the default.
 */

/** `profileLumaNr` as the worker sends it (lumanr.py ycc_luma_nr_block). */
export interface ProfileLumaNr {
  lo: number; hi: number;
  baseCoeff: number; slopeCoeff: number; strength: number;
  gain: number; limit: number;
  amountBias: number; gainScale: number;
}

/** What the kernel runs with at one slider position. */
export interface LumaNrParams {
  // The threshold curve: base + ((clamp(level, lo, hi) - lo) * slope >> 12).
  lo: number; hi: number; base: number; slope: number;
  // The centre-vs-mean reference weight, 0..1024.
  weight: number;
  // The detail restore pair: gain in 8.8 fixed point, limit on the excursion.
  gain: number; limit: number;
}

const DETAIL_GAIN_UNIT = 256;

/**
 * The user's t = (amount - 50) * 2 remapped so that neutral lands on the
 * frame's bias (40 on the frames measured): the span below is compressed
 * onto [-100, bias], the span above stretched onto [bias, 100].
 */
export function yccEffectiveAmount(amount: number, bias: number): number {
  const t = (amount - 50) * 2;
  return t >= 0 ? bias + (100 - bias) * t / 100 : bias + (100 + bias) * t / 100;
}

export function lumaNrParams(block: ProfileLumaNr, amount: number, edge: number): LumaNrParams {
  const tEff = yccEffectiveAmount(amount, block.amountBias);
  // NoiseModel.for_amount: the strength tag scaled by (1 + t/100), truncated,
  // then folded into the coefficients with the engine's shifts.
  const factor = Math.max(0, 1 + tEff / 100);
  const fold = Math.trunc(block.strength * factor) * 3;
  const base = (fold * block.baseCoeff) >> 8;
  const slope = (fold * block.slopeCoeff) >> 8;
  // weight_for_amount at the effective t.
  const weight = Math.trunc((tEff + 100) / 200 * 1024);
  // The detail pair: the tag scaled for this path, then the Edge slider's
  // mapping (DetailRestore.for_edge_slider) — toward "restore all" with a
  // wider clamp below neutral, toward zero above it.
  const gain0 = Math.trunc(block.gain * block.gainScale);
  const te = (edge - 50) * 2;
  let gain: number;
  let limit: number;
  if (te >= 0) {
    gain = Math.trunc(gain0 * (1 - te / 100));
    limit = block.limit;
  } else {
    const room = -te / 100;
    gain = Math.trunc(gain0 + (DETAIL_GAIN_UNIT - gain0) * room);
    limit = Math.trunc(block.limit + 7 * block.limit * room);
  }
  return { lo: block.lo, hi: block.hi, base, slope, weight, gain, limit };
}
