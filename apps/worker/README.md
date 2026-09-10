# llr-worker

Python worker for RAW decode, DCP color, RAW-domain denoise, and XMP export.
Managed by `uv`; the API spawns it as a long-lived daemon.

## Commands

```bash
uv run --project apps/worker llr-worker daemon                    # stdio JSON daemon (what the API runs)
uv run --project apps/worker llr-worker extract-preview IN OUT    # embedded preview → JPEG
uv run --project apps/worker llr-worker preview IN OUT            # quick decode → JPEG
uv run --project apps/worker pytest -q apps/worker/tests          # tests
```

The daemon protocol is one JSON object per line on stdin (`command`,
`id`, command-specific fields) answered by one JSON line on stdout.
Commands: `ping`, `extract-preview`, `render-linear`, `export`.

## Modules

- `cli.py` — daemon loop, decode/cache orchestration, XMP building and JPEG
  APP1 embedding, camera-crop math. Exports copy the RAW's full EXIF; the
  `stripPrivate` export flag excludes GPS/serials/owner/maker notes instead.
- `dcp.py` — Adobe DCP (classic-TIFF) parsing and application: color matrix,
  ProfileHueSatMap, ProfileLookTable, profile tone curve, in linear ProPhoto.
- `denoise.py` — Bayer-mosaic denoise before demosaic (2×2 CFAs only, X-Trans is
  skipped). One denoiser, chosen here rather than by the request: `DEFAULT_MODEL`
  is a transcription of Sony's own filter, with the wavelet as `FALLBACK_MODEL`
  for frames that carry no Sony noise tags. A neural backend can register itself
  via `register_denoiser()`; none ships yet (must be non-GPL).

Output pixels are written as float16 (the precision the browser's RGB16F
textures use); in-memory caches stay float32.
