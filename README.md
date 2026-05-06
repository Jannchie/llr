# LLR

LLR is a pnpm monorepo skeleton for a Lightroom-like RAW editor.

The first milestone focuses on a small real loop:

- Decode RAW files through rawpy / LibRaw.
- Extract embedded camera JPEG previews from RAW files.
- Read camera metadata such as model, lens, camera white balance, and Creative Style.
- Optionally apply an external DCP camera profile for a more stable colorimetric base.
- Apply non-destructive edit recipes in a Python image pipeline.
- Keep per-image auto tone as an explicit option instead of baking it into Standard.
- Optionally apply `.cube` LUTs in display sRGB space.
- Export previews or JPEGs.
- Share profile and recipe definitions across web, API, and worker apps.

## Requirements

- Node.js 22+
- pnpm 10.28+
- rawpy / LibRaw through the Python worker
- Optional ExifTool, provided locally in `vendor/exiftool`, used under its Artistic license option for richer metadata

The worker is a Python app managed by `uv`.

## Commands

```bash
pnpm install
pnpm check
pnpm build
pnpm dev:api
pnpm dev:web
pnpm worker -- extract-preview ./samples/photo.ARW ./tmp/embedded.jpg
pnpm worker -- preview ./samples/photo.ARW ./tmp/photo.jpg
pnpm worker -- export ./samples/photo.ARW ./tmp/export.jpg --exposure 0.3 --contrast 12
pnpm worker -- export ./samples/photo.ARW ./tmp/camera-matching.jpg
pnpm worker -- export ./samples/photo.ARW ./tmp/dcp-standard.jpg --dcp /path/to/camera-profile.dcp
pnpm worker -- export ./samples/photo.ARW ./tmp/neutral.jpg --profile neutral --no-auto-tone
pnpm worker -- export ./samples/photo.ARW ./tmp/export-with-lut.jpg --lut ./luts/look.cube
```

The default export path is metadata-first where possible: rawpy uses the camera white balance and camera color data from LibRaw. Standard uses a fixed base curve and does not apply per-image auto tone unless `--auto-tone` is passed. If a local Adobe Camera Matching DCP exists under `vendor/adobe-camera-profiles/Camera/<camera model>`, Standard auto-selects it from the RAW metadata, preferring the camera Creative Style code such as `FL` or `IN`. Use `--no-dcp` to disable this. When `--dcp` is provided, the worker uses that external DCP profile instead. DCP support applies the color matrix, ProfileHueSatMap when present, ProfileLookTable when present, and the profile tone curve in a linear ProPhoto working space before converting to sRGB. ExifTool is only used for richer descriptive metadata such as model, lens, and Creative Style. GPL-only tools such as RawTherapee and exiv2 are not used by the worker.

## Workspace

```text
apps/web       Browser UI skeleton
apps/api       HTTP API skeleton
apps/worker    RAW preview/export CLI
packages/core  Shared edit recipe and parameter model
packages/profiles Built-in looks, including Standard and Sony FL-like
```
