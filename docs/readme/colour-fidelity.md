# Colour fidelity against the camera JPEG

Three α7C II frames, Creative Look FL — two with DRO Auto, the sunflowers with
DRO off. Each render is compared with the
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
Camera FL profile and every other setting at default (the sunflowers frame
from Lightroom's Adobe Imagecore export path, same profile). Regenerate with
`docs/readme/tools/quant.py <camera.jpg> <regions.json> label=render.jpg …`;
the region boxes are in `docs/readme/tools/regions{1,2,3}.json`.

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
| LLR · Sony, advanced colour | 2.33 | 2.14 | 4.57 | 1.73 / -0.9° / -1.2 / +1.4 | 2.51 / +1.2° / -0.2 / +2.6 | 2.21 / +2.1 |
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

## DSC02976 — sunflowers (DRO off)

| Render | ΔE00 mean | ΔE00 median | ΔE00 p95 | petals: ΔE00 / Δh° / ΔC* / ΔL* | leaves: ΔE00 / Δh° / ΔC* / ΔL* | backdrop: ΔE00 / ΔL* |
|---|---|---|---|---|---|---|
| Imaging Edge · advanced colour | 1.66 | 1.60 | 3.09 | 2.06 / +1.8° / -0.0 / +1.7 | 0.98 / +0.9° / -0.6 / +0.2 | 1.36 / +0.3 |
| LLR · Sony, advanced colour | 1.61 | 1.56 | 3.02 | 2.02 / +1.5° / -0.2 / +1.7 | 1.11 / +0.9° / -0.7 / +0.6 | 1.29 / +0.6 |
| Lightroom · Camera FL | 6.86 | 6.64 | 14.33 | 9.29 / +12.5° / -7.7 / +6.6 | 6.41 / +9.7° / -1.1 / +5.4 | 5.73 / +5.5 |

Saturated yellow is the hard case for a fitted profile: Lightroom turns the
petals 12° and drops 8 units of chroma, and lifts the whole frame ~+5.5 L\*.
Imaging Edge and LLR both keep the hue within 2° and the chroma within a unit
of the camera, and with DRO off they agree with each other to ΔE00 0.59.

## LLR against Imaging Edge

The same measure with Imaging Edge's export as the reference instead of the
camera JPEG, both in the same colour-reproduction mode:

| Frame | Mode | ΔE00 mean | ΔE00 median | ΔE00 p95 | coloured region: ΔE00 / Δh° / ΔC* / ΔL* | neutral region: ΔE00 / ΔL* |
|---|---|---|---|---|---|---|
| DSC03633 | advanced | 1.04 | 0.88 | 2.50 | red suit 0.95 / +0.1° / -0.6 / -0.4 | wall 0.63 / -0.0 |
| DSC03633 | standard | 1.50 | 1.27 | 3.46 | red suit 2.19 / +0.2° / -0.9 / -2.2 | wall 0.93 / -0.7 |
| DSC03630 | advanced | 0.82 | 0.66 | 1.90 | leaves 0.59 / +0.1° / -0.1 / +0.4 | pavement 0.65 / +0.1 |
| DSC03630 | standard | 0.80 | 0.65 | 1.86 | leaves 0.59 / +0.1° / -0.1 / +0.4 | pavement 0.60 / +0.1 |
| DSC02976 | advanced | 0.59 | 0.51 | 1.28 | petals 0.48 / -0.2° / -0.2 / +0.0 | backdrop 0.64 / +0.4 |

All three agree with Imaging Edge to within a unit of L\* across the tone
range. DSC03633 is the one where following Edit costs something against the
camera: Edit's DRO lifts this frame's midtones ~2 L\* more than the camera did,
and LLR now lifts with it (2.33 against the camera, where an earlier build
that under-applied DRO happened to land at 1.66).

## A broad sample: 124 frames off the NAS

Three hand-picked frames flatter any renderer. To find where the pipeline
actually breaks, 124 ARW+JPEG pairs were sampled from a 4850-pair library
(one α7C II, three lenses), stratified over Creative Look (FL 40, IN 25,
VV2 15, PT 12, ST 8, SH/NT/VV/Off 6 each), ISO 100–51200, DRO Off/Auto/Lv5,
full and M-size RAW, and with/without in-camera look tweaks. Each was rendered
at LLR's defaults and measured against its camera JPEG as above (whole-frame
ΔE00 only). Three faults it exposed, all fixed in this tree:

- **Fade** was thrown away under advanced colour reproduction (the YGamma
  contrast was replaced by a constant that turned out to be the Fade table's
  entry 0): every Fade 1 frame rendered 7 L\* too dark in the shadows, IN's
  Fade 3 12 L\*, SH's Fade 6 20 L\*.
- **DRO** indexed its curve two stops too high (white sits at log2(4096) on
  the engine's axis, not at the top): DRO Auto frames came out 20–40% too dark
  through the midtones.
- **M-size RAW** (3584×2560, YCbCr) carries its white balance in SubIFD tag
  0x7039, which LibRaw does not read: indoor M frames rendered neutral where
  the camera and Edit are warm. Creative Look *Off* also fell through to a DCP
  instead of the Standard look.

| Group | n | ΔE00 mean, before → after |
|---|---|---|
| all frames | 124 | 4.29 → **2.74** (median 3.11 → 2.22) |
| all but one corrupt-ISO frame | 123 | 4.01 → 2.43 |
| frames within ΔE00 ≤ 3 | | 48% → **83%** |
| FL / IN / VV2 / PT | 40 / 25 / 15 / 12 | 3.79→3.09 / 5.73→2.35 / 3.85→3.50 / 2.03→2.17 |
| ST / SH / NT / VV / Off | 8 / 6 / 6 / 6 / 6 | 2.88→2.05 / 10.09→2.46 / 2.52→1.83 / 1.40→1.96 / 7.89→4.05 |
| DRO Off / Auto / Lv5 | 25 / 94 / 5 | 3.60→2.33 / 4.47→2.72 / 4.23→5.05 |
| full-size / M-size RAW | 89 / 35 | 3.32→2.38 / 6.74→3.64 |
| ISO ≤200 / ≤800 / ≤3200 / ≤12800 | 37 / 30 / 27 / 25 | 3.38→2.28 / 3.33→2.25 / 4.02→2.29 / 5.79→2.89 |

What is left, in order of how much of the library it touches: M-size RAWs
cannot build DRO's bilateral grid from LibRaw's planes and fall back to a
global curve (~20% too bright against Edit on strong-DRO frames); the camera's
own AWB keeps more warmth under tungsten than Edit does (R/G 1.1–1.2, not in
the file as far as Edit knows either); DRO's manual levels (Lv5) render
brighter than the camera; and the ~+2 L\* the camera sits below Edit on
well-exposed frames.
