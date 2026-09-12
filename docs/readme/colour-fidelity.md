# Colour fidelity against the camera JPEG

Two α7C II frames, Creative Look FL, DRO Auto. Each render is compared with the
JPEG the camera wrote into the same ARW, at 1/4 size so demosaic and sharpening
differences do not count, in CIELAB (sRGB, D65). ΔE00 is CIEDE2000. Region
columns are means over that region; for coloured regions only pixels the camera
rendered with C\* > 18 count (the object, not the gaps), and Δh° / ΔC\* / ΔL\*
are the render's mean hue, chroma and lightness shift from the camera.

The camera JPEG is the preview embedded in the ARW (`pnpm worker --
extract-preview`). Imaging Edge renders are JPEG exports from Sony's Imaging
Edge Desktop (Edit), sRGB, DRO Auto and noise reduction Auto, in both of its
colour-reproduction modes (*standard*, the panel default, and *advanced*, the
3-D LUT that makes Edit match the in-camera JPEG). LLR renders are the app's
own exports with the matching *Advanced colour reproduction* switch state;
LLR turns it on for a new Sony import, since it measures closer to the camera
on every band except deep shadows (L\* < 20, +0.5 ΔE00) and the most saturated
colours (C\* > 60, ~0.3 units more chroma lost) — small against the 0.8–0.9
ΔE00 it recovers on highlights and neutrals.
Lightroom exports are full-size JPEGs from Lightroom Classic 15.4 with the
Camera FL profile and every other setting at default. Regenerate with
`docs/readme/tools/quant.py <camera.jpg> <regions.json> label=render.jpg …`;
the region boxes are in `docs/readme/tools/regions{1,2}.json`.

Geometry is not part of the measure but it has to line up for it to mean
anything: block-matching LLR's export against the camera JPEG on a 9×13 grid
puts the two within 0.15 px radially at every radius on both frames (the
in-camera distortion correction, `sony_lens_corrections` in the worker);
Imaging Edge's own export sits 0.6–0.9 px inside the camera's, Lightroom's
within 0.4 px.

## DSC03633 — statue, hedge, brick wall

| Render | ΔE00 mean | ΔE00 median | ΔE00 p95 | red suit: ΔE00 / Δh° / ΔC* / ΔL* | hedge: ΔE00 / Δh° / ΔC* / ΔL* | wall: ΔE00 / ΔL* |
|---|---|---|---|---|---|---|
| Imaging Edge · advanced colour | 2.18 | 2.11 | 3.72 | 2.05 / -1.0° / -0.6 / +1.9 | 2.03 / +1.1° / -0.1 / +2.0 | 2.31 / +2.1 |
| Imaging Edge · standard colour | 2.38 | 2.63 | 3.73 | 2.29 / -0.7° / -0.2 / +2.3 | 2.33 / +0.6° / +0.4 / +2.4 | 2.87 / +4.0 |
| LLR · Sony, advanced colour | 1.66 | 1.52 | 3.12 | 1.24 / -0.7° / -1.4 / -0.4 | 1.27 / +1.3° / -1.1 / -0.0 | 1.83 / +1.4 |
| LLR · Sony, standard colour | 1.73 | 1.72 | 2.98 | 1.10 / -0.5° / -1.1 / +0.0 | 1.22 / +0.8° / -0.6 / +0.4 | 2.39 / +3.2 |
| Lightroom Classic · Camera FL | 2.92 | 2.76 | 5.29 | 2.47 / +0.3° / -5.9 / -0.9 | 3.62 / -4.6° / -4.5 / -2.3 | 3.22 / +4.1 |

## DSC03630 — can under leaves

| Render | ΔE00 mean | ΔE00 median | ΔE00 p95 | leaves: ΔE00 / Δh° / ΔC* / ΔL* | can: ΔE00 / ΔL* | pavement: ΔE00 / ΔL* |
|---|---|---|---|---|---|---|
| Imaging Edge · advanced colour | 1.82 | 1.85 | 3.00 | 1.32 / +0.5° / -0.3 / +1.2 | 1.93 / +1.7 | 2.14 / +2.2 |
| Imaging Edge · standard colour | 2.03 | 2.24 | 3.06 | 1.60 / +0.3° / +0.2 / +1.7 | 2.48 / +3.0 | 2.51 / +3.3 |
| LLR · Sony, advanced colour | 1.98 | 1.98 | 3.12 | 1.58 / +0.7° / -0.4 / +1.6 | 1.96 / +1.9 | 2.15 / +2.3 |
| LLR · Sony, standard colour | 2.25 | 2.40 | 3.15 | 1.91 / +0.5° / +0.0 / +2.1 | 2.53 / +3.1 | 2.56 / +3.4 |
| Lightroom Classic · Camera FL | 5.64 | 4.80 | 12.79 | 3.80 / -3.0° / -3.3 / +3.6 | 2.46 / +2.2 | 4.63 / +6.1 |

What the numbers say: Sony's own desktop render is not the camera JPEG either
— Imaging Edge sits ~+2 L\* above the camera on neutrals in advanced mode and
~+3–4 L\* in standard mode, with hue and chroma within a degree and a unit.
LLR lands in the same place: within a degree of hue and 1.5 units of chroma
on every coloured region, and the same +1–3 L\* lift on neutrals that Imaging
Edge has. Lightroom keeps hue on the red but loses 6 units of chroma, and turns
greens 3–5° toward blue while dropping 3–4 units of chroma — the "Lightroom
look" of foliage. On the second frame its larger error is tone rather than
colour: the whole frame comes out ~+6 L\* brighter than the camera, which the
DRO-aware renders do not do.

## LLR against Imaging Edge

The same measure with Imaging Edge's export as the reference instead of the
camera JPEG, both in the same colour-reproduction mode:

| Frame | Mode | ΔE00 mean | ΔE00 median | ΔE00 p95 | coloured region: ΔE00 / Δh° / ΔC* / ΔL* | neutral region: ΔE00 / ΔL* |
|---|---|---|---|---|---|---|
| DSC03633 | advanced | 1.47 | 1.24 | 3.48 | red suit 2.18 / +0.2° / -0.9 / -2.2 | wall 0.85 / -0.7 |
| DSC03633 | standard | 1.50 | 1.27 | 3.46 | red suit 2.19 / +0.2° / -0.9 / -2.2 | wall 0.93 / -0.7 |
| DSC03630 | advanced | 0.82 | 0.66 | 1.90 | leaves 0.59 / +0.1° / -0.1 / +0.4 | pavement 0.65 / +0.1 |
| DSC03630 | standard | 0.80 | 0.65 | 1.86 | leaves 0.59 / +0.1° / -0.1 / +0.4 | pavement 0.60 / +0.1 |

On DSC03630 the two renders agree to within a unit of L\* across the whole
tone range. On DSC03633 LLR's midtones (L\* 30–70) sit ~2.5 L\* below Imaging
Edge's, and with DRO switched off on both sides that gap closes to ≤0.5 L\*
everywhere: the residual is the DRO *Auto* level, which LLR takes from the gain
grid the camera wrote into the ARW (landing on the camera JPEG, +0.1 L\* on the
suit) while Imaging Edge's Auto picks a stronger lift for this frame.
