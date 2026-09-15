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

Sony RAWs can take a second colour path at step 3 (`sony/`), reproducing
Imaging Edge from calibration the body wrote into the file: its hue-segmented
matrix, the chosen Creative Look's tone curve and chroma terms, and DRO. That
path is reverse-engineered rather than documented — `sony_repro/PIPELINE.md` is
the record, and the code carries the measured accuracy in its docstrings.

Its parts split by what they cost. The matrix is the only half that touches
pixels, and all ten Creative Looks share one, so switching looks — or moving any
of the nine tweaks, or DRO — needs no decode: the profile is rebuilt and the
shader re-applies it to pixels the browser already holds. Switching looks is a
`look-profile` request (another look is another calibration to read); the
tweaks and the DRO strength are rebuilt in the browser itself
(`rendering/sony-look.ts`, a transcription of the worker's construction over
the `lookCalibration` block every profile carries, pinned to the worker's
numbers by `tests/test_look_rebuild.py`'s fixture), so a drag redraws one frame
behind the finger. DRO rides that same route because it is one scalar gain per
pixel, and a scalar commutes with the matrix. What travels with the profile is
a gain table plus the engine's 8×6×14 bilateral grid, which the shader
interpolates to recover the local log mean the gain is indexed by.

Layered LRU caches make slider-driven re-requests cheap: camera-RGB per
(source, size, denoise) → re-applying a DCP skips the RAW decode; final linear
per full parameter set → repeat requests skip everything. Under the latter sits
a disk tier (`cache-<key>.f16` + `.json` beside the source, hardlinked into the
response, LRU-bounded across sessions by `LLR_DISK_CACHE_MB`), so a reopened
session skips the decode after a worker restart too. Sources and that cache
live under `$LLR_CACHE_DIR` (default `~/.cache/llr`), outside the checkout, and
stay until the photo is removed from the library.

## Catalog: api (`apps/api/src/catalog.ts`)

What is in the library lives in one SQLite file, `$LLR_CACHE_DIR/catalog.db`
(Node's built-in `node:sqlite`, WAL): the virtual folder tree, every photo
with the EXIF a library view sorts and labels by, and each photo's edit —
snapshot plus capped undo history — as JSON. The browser holds none of it
beyond what is on screen, so two tabs cannot disagree and a cleared browser
profile loses nothing. A photo's files stay in `sessions/<id>/`: the copied
source, the camera preview (`embedded.jpg`), a 384px `thumb.jpg` the worker
cuts from the same decode at import, and the decode cache above. Import is
`POST /photos`: copy the bytes, insert the row, ask the worker for previews
and metadata (one exiftool call, memoised for the decode that follows), and
answer with the finished record — the browser never polls for a thumbnail.
Removal deletes rows first and files best-effort; a directory the catalog no
longer names is swept on the hour (Windows will not delete a RAW the worker
still has open). Previews are served `immutable` under their UUID, so a grid
of thousands costs one request per thumbnail, ever.

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
changes re-request linear data. Noise reduction is two-tier, as in Lightroom:
the RAW-domain stage above is the decode's, and a display-side luminance stage
(`passes.ts` `NOISE_LUMA_SHADER`, a bilateral on the finished frame, first in
the post chain so sharpening never sees the grain) drags in real time on top
of it. Masks (`rendering/masks.ts`, `docs/masking.md`)
are analytic components — luminance/colour range, linear/radial gradient —
packed into a uniform block; the shader evaluates each group's weight per pixel
and blends the *parameters* (exposure, WB, tonal, clarity, dehaze,
saturation/vibrance, hue) before those blocks run once.

`App.vue` holds the editing state; the mechanics live in composables
(`useCatalog`, `useHistory`, `useViewport`, `useCropEditor`, `useToneCurve`,
`useHistogram`, `useExport`) and `api.ts` (the binary protocol client).
`useCatalog` is the browser's half of the catalog: the folder tree, the open
folder's photos as plain (non-reactive) records, the active photo — which need
not be in that folder — and the edits touched this session, saved with a
debounce (`PUT /photos/:id/edit`; a keepalive request on unload). The library
grid and the filmstrip are windowed (`useVirtualGrid`: fixed cells, so an
index maps to a position by arithmetic and a folder of thousands mounts a
screenful). Folders, photos and OS drops all move by HTML5 drag-and-drop onto
the tree. What is UI state — open folder, expanded folders, active photo, view
settings — stays in `localStorage`. `persistence.ts` is the pre-catalog
IndexedDB store, kept to migrate it: on first boot the browser hands its
photos and edits to `POST /catalog/adopt` and clears it.

## Export

Export re-renders at **full resolution in WebGL** from a frozen edit snapshot
(`useExport.ts`), then POSTs the JPEG to the worker, which copies the RAW's
full provenance EXIF (an opt-in "private-safe" mode strips GPS, serials,
owner name, and maker notes instead) and embeds the edit recipe as XMP:
structured `llr:*` fields plus a lossless JSON blob, spilling into
Extended-XMP chunks when a heavy edit overflows the 64 KB APP1 limit.

## Assistant

The Assistant tab is a chat that edits the open photo. The agent loop
(`@mariozechner/pi-agent-core`) runs in the API (`apps/api/src/agent.ts`),
which holds the LLM key and streams events to the browser over SSE; every tool
the model calls — look at the render, read the edit, apply an edit — executes in
the browser against the same reactive state the sliders drive, so its changes
redraw, enter history and persist like hand edits. The tool schemas travel with
each prompt, so the API knows nothing about photography. `LLR_AGENT_MODEL`
(`provider/model`) picks the model; the key comes from the provider's usual env
var.

## Testing

Pure logic is tested where it lives: vitest for the web rendering maths,
folder-tree helpers and the legacy store (`apps/web/src/**/__tests__`), vitest
for the API's protocol module and the catalog (against an in-memory SQLite),
pytest for the worker's DCP/XMP/crop/Bayer/metadata code (`apps/worker/tests`).
Pixel-level verification is manual/driven (see `.claude/skills/verify`) since
the render output is a GPU artifact.
