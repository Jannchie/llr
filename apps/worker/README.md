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
  for frames that carry no Sony noise tags.

Output pixels are written as float16 (the precision the browser's RGB16F
textures use); in-memory caches stay float32.

## Compiled kernels

The two hot stages of the Sony path are numba-compiled: the ITP demosaic
(`sony/itp_numba.py`, driven by `sony/itp.py`), the RawNR sigma filter and
its analyses (`sony/rawnr_numba.py`, driven by `sony/rawnr_simd.py`), and the
normalisation either side of a denoiser (`denoise_numba.py`). Each was ported
from a whole-array numpy transcription that had been scored against the
engine's own dumps and proven bit-identical to it (whole-frame `array_equal`,
the engine's own tiles, random inputs) before that transcription was retired;
the tests now pin the kernels to the engine fixtures directly.

`sony/marble.py` is the engine's last stage, the chroma cleanup of
`ZcTaskSIMDMarble`, decoded bit for bit (its test fixture is a crop of the
engine's own tile). It is numpy only: the work happens at quarter resolution,
so a 33 MP frame takes about two seconds. The browser runs the same algorithm
as four passes; `chromanr.py` is the guided-filter approximation it replaced,
kept for the notes that cite it.

On a 33 MP frame the cold decode went from 12.4 s to 2.5 s (ITP 7.7 → 0.35 s,
RawNR 2.4 → 0.3 s). Kernels are `cache=True`; the first run on a machine pays
~8 s of LLVM, which the daemon hides in a background warm-up at startup.
Bit-identity rules for anyone touching a kernel: every constant `np.float32`,
no `fastmath`, keep the accumulation order the comments call out, reproduce the
zeros the engine leaves in unwritten margins, and keep `strip_rows` even.
