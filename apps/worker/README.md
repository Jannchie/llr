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

## Compiled kernels

The two hot stages of the Sony path are numba-compiled, each with its numpy
transcription kept as the reference and proven bit-identical (whole-frame
`array_equal`, the engine's own tiles, random inputs):

| stage | kernel | reference | switch |
|---|---|---|---|
| ITP demosaic | `sony/itp_numba.py` | `sony/itp_numpy.py` | `LLR_ITP_BACKEND=numpy` |
| RawNR sigma filter | `sony/rawnr_numba.py` | `rawnr_simd._filt_rows` | `LLR_RAWNR_BACKEND=numpy` |
| denoise plumbing | `denoise_numba.py` | the numpy chains in `denoise.py` | `LLR_DENOISE_BACKEND=numpy` |

`sony/marble.py` is the engine's last stage, the chroma cleanup of
`ZcTaskSIMDMarble`, decoded bit for bit (its test fixture is a crop of the
engine's own tile). It is numpy only: the work happens at quarter resolution,
so a 33 MP frame takes about two seconds. The browser runs the same algorithm
as four passes; `chromanr.py` is the guided-filter approximation it replaced,
kept for the notes that cite it.

On a 33 MP frame the cold decode went from 12.4 s to 2.5 s (ITP 7.7 → 0.35 s,
RawNR 2.4 → 0.3 s). Kernels are `cache=True`; the first run on a machine pays
~8 s of LLVM, which the daemon hides in a background warm-up at startup. A
build without numba falls back to the reference paths with one stderr line.
Bit-identity rules for anyone touching a kernel: every constant `np.float32`,
no `fastmath`, accumulate in the reference's loop order, reproduce the zeros the
reference leaves in unwritten margins, and keep `strip_rows` even.
