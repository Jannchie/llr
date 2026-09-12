# Colour fidelity against the camera JPEG

Two α7C II frames, Creative Look FL, DRO Auto. Each render is compared with the
JPEG the camera wrote into the same ARW, at 1/4 size so demosaic and sharpening
differences do not count, in CIELAB (sRGB, D65). ΔE00 is CIEDE2000. Region
columns are means over that region; for coloured regions only pixels the camera
rendered with C\* > 18 count (the object, not the gaps), and Δh° / ΔC\* / ΔL\*
are the render's mean hue, chroma and lightness shift from the camera.

The camera JPEG is the preview embedded in the ARW (`pnpm worker --
extract-preview`); LLR renders are the app's own exports; Lightroom exports are
full-size JPEGs from Lightroom Classic 15.4 with the Camera FL profile and every
other setting at default. Regenerate with `docs/readme/tools/quant.py <camera.jpg> <regions.json> label=render.jpg …`;
the region boxes are in `docs/readme/tools/regions{1,2}.json`.

## DSC03633 — statue, hedge, brick wall

| Render | ΔE00 mean | ΔE00 median | ΔE00 p95 | red suit: ΔE00 / Δh° / ΔC* / ΔL* | hedge: ΔE00 / Δh° / ΔC* / ΔL* | wall: ΔE00 / ΔL* |
|---|---|---|---|---|---|---|
| LLR · Sony | 1.80 | 1.74 | 3.18 | 1.21 / -0.5° / -1.1 / +0.1 | 1.27 / +0.8° / -0.6 / +0.4 | 2.42 / +3.2 |
| Lightroom Classic · Camera FL | 2.92 | 2.76 | 5.29 | 2.47 / +0.3° / -5.9 / -0.9 | 3.62 / -4.6° / -4.5 / -2.3 | 3.22 / +4.1 |
| LLR · Adobe DCP | 3.24 | 2.74 | 6.48 | 4.20 / +0.4° / -8.5 / +0.3 | 5.22 / -6.6° / -6.6 / -3.3 | 2.71 / +2.6 |

## DSC03630 — can under leaves

| Render | ΔE00 mean | ΔE00 median | ΔE00 p95 | leaves: ΔE00 / Δh° / ΔC* / ΔL* | can: ΔE00 / ΔL* | pavement: ΔE00 / ΔL* |
|---|---|---|---|---|---|---|
| LLR · Sony | 2.37 | 2.41 | 3.87 | 2.00 / +0.5° / +0.0 / +2.1 | 2.60 / +3.2 | 2.57 / +3.4 |
| Lightroom Classic · Camera FL | 5.64 | 4.80 | 12.79 | 3.80 / -3.0° / -3.3 / +3.6 | 2.46 / +2.2 | 4.63 / +6.1 |
| LLR · Adobe DCP | 3.02 | 2.55 | 5.83 | 3.84 / -4.7° / -7.0 / +0.4 | 3.07 / +2.3 | 2.10 / +2.6 |

What the numbers say: the Sony path lands within a degree of hue and a unit of
chroma on every coloured region, and its residual is a uniform ~+3 L\* on
neutrals. A Camera Matching DCP keeps hue on the red but loses 6–8 units of
chroma, and turns greens 3–7° toward blue while dropping 3–7 units of chroma —
the "Lightroom look" of foliage.

Lightroom Classic (process version 15.4, profile Camera FL, everything else at
default) drifts the same way LLR's own DCP render does. On the first frame it
goes a little less far (the two agree to a mean ΔE00 of 3.0); on the second its
larger error is tone rather than colour — the whole frame comes out ~+6 L\*
brighter than the camera, which the DRO-aware Sony path does not do.
