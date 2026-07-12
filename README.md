# LLR

LLR is a pnpm monorepo for a Lightroom-like RAW editor.

The editing loop:

- Decode RAW files through rawpy / LibRaw in the Python worker.
- Auto-apply a local Adobe Camera Matching DCP profile (color matrix,
  ProfileHueSatMap, ProfileLookTable, profile tone curve) in a linear ProPhoto
  working space when one exists under `vendor/adobe-camera-profiles/Camera/`.
- Optional RAW-domain denoise on the Bayer mosaic before demosaic.
- Ship scene-linear pixels to the browser, where the authoritative WebGL
  pipeline renders every edit (tone, HSL, grading, curves, crop) in real time.
- Export by re-rendering at full resolution in WebGL, then embedding the edit
  recipe as XMP (`llr:*` fields plus a lossless JSON blob) via the worker.

## Requirements

- Node.js 22+
- pnpm 10.28+
- rawpy / LibRaw through the Python worker (managed by `uv`)
- Optional ExifTool, provided locally in `vendor/exiftool`, used under its
  Artistic license option for richer metadata

## Commands

```bash
pnpm install
pnpm check
pnpm build
pnpm dev        # api + web together
pnpm dev:api
pnpm dev:web
pnpm worker -- extract-preview ./samples/photo.ARW ./tmp/embedded.jpg
pnpm worker -- preview ./samples/photo.ARW ./tmp/photo.jpg
```

Camera color: rawpy uses the camera white balance and camera color data from
LibRaw. The Standard profile auto-selects a local DCP from the RAW metadata,
preferring the camera Creative Style code such as `FL` or `IN`. ExifTool is
only used for richer descriptive metadata such as model, lens, and Creative
Style. GPL-only tools such as RawTherapee and exiv2 are not used by the worker.

## Workspace

```text
apps/web       Browser UI + WebGL rendering pipeline (authoritative render)
apps/api       HTTP API; bridges the web app and the worker daemon
apps/worker    Python RAW decode/preview daemon + XMP export
```
