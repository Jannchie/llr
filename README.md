# LLR

LLR is a browser RAW editor for Sony shooters. It renders an ARW the way the
camera did — Creative Look, DRO and all — and lets an LLM agent do the edit from
one sentence.

Two things it does that Lightroom does not:

## The camera's colour, not Adobe's

![Camera JPEG, Sony's Imaging Edge, LLR's Sony engine, and Lightroom Classic on the same ARW](docs/readme/sony-engine.jpg)

<table><tr>
<td><img src="docs/readme/can-imaging-edge.gif" alt="Camera JPEG toggling against Sony's Imaging Edge" width="300"></td>
<td><img src="docs/readme/can-llr.gif" alt="Camera JPEG toggling against LLR's Sony render" width="300"></td>
<td><img src="docs/readme/can-lightroom.gif" alt="Camera JPEG toggling against Lightroom Classic" width="300"></td>
</tr></table>

*Camera JPEG ↔ Imaging Edge (Sony's own RAW converter), ↔ LLR, ↔ Lightroom
Classic (Camera FL profile, defaults). Foliage is where a fitted profile
drifts most — watch the greens lose their colour and the whole frame lift on
the right.*

Measured against the camera JPEG (CIEDE2000; hue and chroma shifts are means
over the coloured pixels of that region; [method and full tables](docs/readme/colour-fidelity.md)):

| Render | ΔE00 mean, frame 1 / 2 | Red suit ΔC\* | Hedge Δh° / ΔC\* | Leaves Δh° / ΔC\* |
|---|---|---|---|---|
| Imaging Edge · advanced colour | 2.18 / 1.82 | −0.6 | +1.1° / −0.1 | +0.5° / −0.3 |
| LLR · Sony engine | 1.66 / 1.98 | −1.4 | +1.3° / −1.1 | +0.7° / −0.4 |
| Lightroom Classic · Camera FL | 2.92 / 5.64 | −5.9 | −4.6° / −4.5 | −3.0° / −3.3 |

LLR is as close to the camera JPEG as Sony's own software is, and against
Imaging Edge itself it measures ΔE00 0.8–1.5 mean — the two renders agree to
within a unit of L\* wherever the DRO level is the same.

Every ARW carries the calibration Imaging Edge renders from: the body's
hue-segmented colour matrix, the tone curve and chroma terms of each Creative
Look, and the DRO gain grid. LLR reads them back and runs that pipeline, so the
first frame you see is the camera JPEG — at RAW depth, with every slider still
live. Lightroom's Camera Matching profiles approximate the same look from a
fitted DCP, and saturated reds and greens are where that drifts.

What that buys you, after the shot:

- All of the body's Creative Looks (ST, PT, VV, FL, IN, …) switchable per
  image, with the look's own contrast / highlights / shadows / fade / hue /
  saturation / clarity tweaks on the camera's scale.
- DRO as the camera applied it, or any of Imaging Edge's built-in levels.
- Imaging Edge's *advanced colour reproduction* (its 3-D LUT), the step that
  makes Edit's output match the in-camera JPEG.
- Sony's own RAW-domain noise reduction, transcribed rather than approximated.

Anything that is not a Sony RAW renders through an Adobe DCP instead, and the
engine is a per-image switch.

## Edit by describing it

![The Assistant tab: a prompt, the tool calls it made, and the result](docs/readme/assistant.jpg)

The Assistant is an agent with tools over the same state the sliders drive. It
looks at the render, edits (Basic sliders, HSL, colour grading, curves, crop,
masks, Creative Look, DRO, denoise), compares before/after, measures clipping,
and iterates until the intent reads without over-correction. Every change is a
history step: undo it, or take over by hand mid-way. The prompt above cost
$0.40 on `gpt-5.6-sol`. Bring your own key; any provider `pi-ai` knows works.

## Run it

Node 22+, pnpm 10, [uv](https://docs.astral.sh/uv/) for the Python worker,
and a WebGL2 browser.

```bash
pnpm install
pnpm dev        # api on :8790, web on the port Vite prints
```

Drop a RAW on the page. Then, optionally:

- Adobe DCP profiles for non-Sony RAWs: copy `Camera/` from a DNG Converter or
  Lightroom install to `vendor/adobe-camera-profiles/Camera/`.
- The Assistant: export `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` (or another
  provider's usual variable) before `pnpm dev`, then pick the model in the
  panel.
- Everything the server keeps — imports, decode cache — lives under
  `~/.cache/llr` (`LLR_CACHE_DIR`), never in the checkout, and imports stay
  until you remove them from the library.

## More

- [ARCHITECTURE.md](ARCHITECTURE.md) — the three processes and why the
  browser's WebGL pass is the single source of truth for pixels.
- [docs/sony-edit-internals.md](docs/sony-edit-internals.md) and
  [sony_repro/PIPELINE.md](sony_repro/PIPELINE.md) — how the Imaging Edge
  pipeline was recovered, and how faithful each stage is.
- [docs/masking.md](docs/masking.md), [docs/assistant-ux.md](docs/assistant-ux.md)
- `pnpm check` runs typecheck, lint, and the web / api / worker test suites.
