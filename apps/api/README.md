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
| GET | `/catalog/tree` | The folder tree with photo counts |
| POST | `/catalog/adopt` | One-time handover of the pre-catalog browser library |
| POST | `/folders` | Create a folder |
| PATCH / DELETE | `/folders/:id` | Rename or move a folder; delete it with its photos |
| GET | `/folders/:id/photos` | The photos of a folder |
| POST | `/photos` | Multipart import of a RAW/image; answers with the finished record |
| PATCH | `/photos/move` | Move photos to a folder |
| POST | `/photos/delete` | Remove photos (rows first, files best-effort) |
| GET / PATCH / DELETE | `/photos/:id` | One photo's record; rename; remove |
| GET / PUT | `/photos/:id/edit` | The stored edit: snapshot plus undo history |
| GET | `/photos/:id/(thumb\|embedded\|edited).jpg` | Previews, served immutable |
| PUT | `/photos/:id/preview` | Store the rendering of the photo's edit |
| POST | `/render-linear` | Decode to scene-linear pixels (binary response) |
| POST | `/look-profile` | Re-derive a Sony colour profile without a decode |
| POST | `/relight-geometry`, `/ai-masks` | Per-decode analyses (binary response) |
| GET / POST | `/(erase\|relight\|ai)-status`, `-download` | A worker model's availability; start its download |
| POST | `/export` | Embed EXIF + LLR XMP into a rendered JPEG |
| GET | `/agent/models` | Providers, their models, and where each stands on a key |
| PUT / DELETE | `/agent/keys/:provider` | Save or forget a provider's API key (never read back) |
| POST | `/agent/(prompt\|tool-result\|steer\|abort\|reset)` | The assistant's loop; `prompt` answers as SSE |

`/render-linear` responds with `[u32 header length][JSON header][pixels]`,
pixels in the header's `dtype` (float16 today), streamed from disk. The frame
layout, parameter clamping, and source-id validation live in `src/protocol.ts`
(pure, unit-tested); concurrent renders are capped and shed with 429.

There is no authentication — this is a local tool, not a deployable service.
What stands in for it is the request gate (`isTrustedRequest`): the server
binds 127.0.0.1, a request with an `Origin` must come from this machine, and
the `Host` header must name it (which is what stops DNS rebinding). Started
with `--lan` (`pnpm dev:lan`), pages opened by a private-network IP are
trusted too, so the app can be used from another device — along with anything
else on that network.

The catalog (`catalog.db` in the cache directory) is copied to `backups/`
once a day and before a schema migration (`src/catalog-backup.ts`).

## Commands

```bash
pnpm --filter @llr/api dev    # tsx src/index.ts on :8790
pnpm --filter @llr/api test   # vitest (protocol module)
```
