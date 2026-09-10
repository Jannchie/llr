import { MARBLE_SLIDER_AUTO } from "./sony-marble";

/**
 * Edit's manual Noise Reduction panel, as far as the browser-side half of it
 * goes. The panel is three sliders: 量 (amount, 0..100), 色彩降噪 (colour NR,
 * 0..10 on the control, 0..100 internally — the value box shows the ×10
 * number, and that is the scale `denoise.chroma` uses) and 边缘降噪 (edge NR,
 * 0..100). Measured at export on DSC03036 (ILCE-7CM2, ISO 2000;
 * sony_repro/notes/measured-chroma-gap.md 2.24.2):
 *
 *  - The panel's defaults (50 / 5 / 50) *are* Auto: RawNR's output is
 *    bit-identical, and Marble's chroma cleanup runs at the same thresholds.
 *  - Colour NR reaches only Marble's chroma cleanup — RawNR's output is the
 *    same at 0, 5 and 10. It is not an amount but a position on Edit's own
 *    0..10 control, and the engine turns that position into the stage's
 *    thresholds and blend (sony-marble.ts, worker sony/marble.py
 *    slider_params / blend_amount). This file's only job is the scale change.
 *  - Edge NR moves neither stage; whatever it does happens somewhere this
 *    renderer does not yet model, so it is ignored here.
 */

/** The internal colour-NR value the manual panel starts at — Auto's. */
export const SONY_CHROMA_NR_AUTO = 50;

/**
 * The panel's colour-NR value as Edit's own 0..10 slider position.
 *
 * The control has eleven stops and the panel stores them ×10, so the mapping
 * is exactly that divided back out; Auto is the default stop, which is the
 * midpoint 5 (MARBLE_SLIDER_AUTO). Out-of-range and non-finite values fall
 * back to the default rather than producing a stop the engine has no
 * calibration for.
 */
export function sonyChromaNrSlider(chroma: number, auto: boolean): number {
  if (auto || !Number.isFinite(chroma)) return MARBLE_SLIDER_AUTO;
  const c = Math.max(0, Math.min(100, chroma));
  return Math.round(c / 10);
}
