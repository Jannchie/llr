# LLR

LLR is a browser RAW editor for Sony shooters. It renders an ARW the way the
camera did — Creative Look, DRO and all — and lets an LLM agent do the edit from
one sentence.

Two things it does that Lightroom does not:

## The camera's colour, not Adobe's

![Camera JPEG, LLR's Sony engine, and an Adobe DCP render of the same ARW](docs/readme/sony-engine.jpg)

<table><tr>
<td><img src="docs/readme/can-llr.gif" alt="Camera JPEG toggling against LLR's Sony render" width="440"></td>
<td><img src="docs/readme/can-adobe.gif" alt="Camera JPEG toggling against the Adobe DCP render" width="440"></td>
</tr></table>

*Left: camera JPEG ↔ LLR. Right: camera JPEG ↔ Adobe DCP. Foliage is where a
fitted profile drifts most — watch the greens go cool and dark on the right.*

Every ARW carries the calibration Imaging Edge renders from: the body's
hue-segmented colour matrix, the tone curve and chroma terms of each Creative
Look, and the DRO gain grid. LLR reads them back and runs that pipeline, so the
first frame you see is the camera JPEG — at RAW depth, with every slider still
live. On the first frame the render sits within a mean ΔE\*ab of 2.4 of the
camera's own JPEG; Lightroom's Camera Matching profiles approximate the same
look from a fitted DCP, and the Film look's reds and the wall's tone are where
that drifts.

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
