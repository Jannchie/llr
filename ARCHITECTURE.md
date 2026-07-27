# Architecture

LLR is three processes with one rule: **the browser's WebGL pipeline is the
single source of truth for pixels.** The Python worker decodes RAW into
scene-linear data; everything the user sees — preview and export alike — is
rendered from that data by the same shader, so preview and export cannot
disagree.

```text
┌─────────┐  scene-linear f16   ┌─────────┐  JSON over stdio   ┌────────────┐
│ apps/web│ ◄────────────────── │ apps/api│ ◄────────────────► │ apps/worker│
│ Vue +   │  POST /render-linear│ Node.js │   daemon, 3-thread │ Python     │
│ WebGL2  │ ──────────────────► │ HTTP    │   pool             │ rawpy/LibRaw│
└─────────┘  POST /export (JPEG)└─────────┘                    └────────────┘
```

## Decode: worker (`apps/worker`)

`llr-worker daemon` reads JSON requests line-by-line from stdin and answers on
stdout; heavy work runs on a 3-thread pool with per-source locks (the caches
assume no same-source concurrency). The decode path:

1. rawpy/LibRaw decode (optionally half-size / long-edge capped).
2. Optional RAW-domain denoise on the Bayer mosaic *before* demosaic
   (`denoise.py`; 2×2 Bayer CFAs only — X-Trans is detected and skipped).
3. Auto-selected Adobe DCP camera profile (`dcp.py`: color matrix,
   HueSatMap, LookTable) into **linear ProPhoto (D50)**.
4. Result written to disk as **float16** and framed by the API into the
   response (see `apps/api/src/protocol.ts` for the wire format).

Layered LRU caches make slider-driven re-requests cheap: camera-RGB per
(source, size, denoise) → re-applying a DCP skips the RAW decode; final linear
per full parameter set → repeat requests skip everything.

## Render: web (`apps/web`)

`rendering/pipeline-renderer.ts` owns a single fused WebGL2 shader pass, split
by the view transform into a scene-referred and a display-referred half.
Scene-referred: white balance (Bradford CAT), exposure,
tonal model (PV2012-style) and clarity — the latter two over a blurred
log-luminance mask — then dehaze, vibrance/saturation, HSL. The view transform
(the camera profile's own tone curve) then takes the pixel display-referred, and the tone
curves (as baked LUT textures) and color grading run *after* it, on [0,1], as
does the final gamut map and sRGB/P3 encode. Uniform-only edits redraw in real
time; Contrast/Blacks are display-referred and re-bake the curve LUT; crop is a
per-frame affine on the sampling UVs (`u_texXform`), no re-decode; denoise/DCP
changes re-request linear data.

`App.vue` holds the editing state; the mechanics live in composables
(`useLibrary`, `useHistory`, `useViewport`, `useCropEditor`, `useToneCurve`,
`useHistogram`, `useExport`) and `api.ts` (the binary protocol client).
Sessions, per-image edits, and thumbnails persist in IndexedDB
(`persistence.ts`).

## Export

Export re-renders at **full resolution in WebGL** from a frozen edit snapshot
(`useExport.ts`), then POSTs the JPEG to the worker, which copies the RAW's
full provenance EXIF (an opt-in "private-safe" mode strips GPS, serials,
owner name, and maker notes instead) and embeds the edit recipe as XMP:
structured `llr:*` fields plus a lossless JSON blob, spilling into
Extended-XMP chunks when a heavy edit overflows the 64 KB APP1 limit.

## Testing

Pure logic is tested where it lives: vitest for the web rendering maths and
persistence (`apps/web/src/**/__tests__`), vitest for the API's protocol
module, pytest for the worker's DCP/XMP/crop/Bayer code (`apps/worker/tests`).
Pixel-level verification is manual/driven (see `.claude/skills/verify`) since
the render output is a GPU artifact.
