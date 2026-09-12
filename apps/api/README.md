# @llr/api

Local HTTP API bridging the web app and the Python worker daemon. It owns the
worker's lifecycle (spawned on demand via `uv run`, restarted on death) and the
server-side source cache under `$LLR_CACHE_DIR/sessions/<uuid>/` (default
`~/.cache/llr`; kept until the source is deleted from the library — originals on
the user's disk are never touched).

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Liveness probe |
| POST | `/sources` | Multipart upload of a RAW/image; returns the source id |
| DELETE | `/sources/:id` | Drop the server-side cached copy |
| GET | `/sources/:id/embedded.jpg` | The RAW's embedded preview |
| POST | `/render-linear` | Decode to scene-linear pixels (binary response) |
| POST | `/export` | Embed EXIF + LLR XMP into a rendered JPEG |

`/render-linear` responds with `[u32 header length][JSON header][pixels]`,
pixels in the header's `dtype` (float16 today), streamed from disk. The frame
layout, parameter clamping, and source-id validation live in `src/protocol.ts`
(pure, unit-tested); concurrent renders are capped and shed with 429.

CORS is restricted to localhost origins; there is no authentication — this is
a local development tool, not a deployable service.

## Commands

```bash
pnpm --filter @llr/api dev    # tsx src/index.ts on :8790
pnpm --filter @llr/api test   # vitest (protocol module)
```
