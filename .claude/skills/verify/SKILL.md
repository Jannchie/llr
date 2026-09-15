---
name: verify
description: Build, launch, and drive the LLR web app to verify changes end-to-end (dev servers, sample RAW import, Playwright driving).
---

# Verifying LLR changes at runtime

## Launch

```bash
pnpm dev   # starts @llr/api (port 8790) + @llr/web (vite, port 5173 or next free — read the log!)
```

- The API spawns the Python worker daemon itself (`uv run --project apps/worker llr-worker daemon`); no separate worker start needed.
- Vite may not get 5173 — always read the "Local: http://localhost:XXXX/" line from the dev log.

## Drive (browser)

- Python Playwright is installed globally; the repo has no JS playwright. The cached
  Playwright chromium may be version-mismatched — launch with the system Chrome:
  `p.chromium.launch(channel="chrome", headless=True)`.
- Import an image through the real UI: `page.set_input_files("input[type=file][accept]", "/home/jannchie/llr/samples/DSC01157.ARW")`
  (the second, `webkitdirectory` input imports a whole directory with its subfolders).
- Wait for decode: `.status-value` text contains `Decoded` (first decode takes ~5-30s; use a 120s timeout).
- The library lives server-side (`$LLR_CACHE_DIR/catalog.db`) and the open folder /
  active photo / view in localStorage — a reload restores the last session (useful for
  persistence tests, but means a run against the same cache root starts with leftover
  images). Point `LLR_CACHE_DIR` at a scratch directory for a clean library.
- Library view: press `g` (grid, `.grid-cell` per visible photo — windowed, so the count is
  a screenful, not the folder), `e` back to develop. Folder tree rows are `.tree-row`;
  `page.drag_and_drop(".grid-cell", ".tree-row:has-text('Name')")` moves photos.

## Useful hooks

- Crop editor: press `r` (Lightroom-style). Overlay is an SVG: `.crop-frame` rect
  (read `width`/`height` attrs for the box ratio in output-frame px), `.crop-handle`
  rects in order tl,t,tr,l,r,bl,b,br, `.crop-grid line` for guide lines.
- Keyboard shortcuts are blocked while focus is in a text/number input — click the
  body/stage first before pressing single-key shortcuts (r/x/o/Enter).
- Undo/redo: `Control+z` / `Control+Shift+z`.

- Export: `page.expect_download()` around `page.click(".export-btn")` captures the JPEG
  (full-res WebGL render + upload; allow a 180s timeout). Inspect metadata with the
  system `exiftool` (vendor/exiftool has no binary in this checkout).

## Gotchas

- Stopping `pnpm dev` can leave the API (`tsx src/index.ts`) and worker daemon alive,
  holding port 8790 → next start dies with EADDRINUSE. `pkill -f "tsx src/index.ts";
  pkill -f "llr-worker daemon"` before relaunching.
- Run `pnpm dev` from the repo root — the shell cwd persists between Bash calls, and
  from `apps/web` it silently starts only Vite (API missing → decode hangs on proxy
  ECONNREFUSED).
- On an empty library the app starts at the
  dropzone with nothing loaded — there is no sample auto-import any more, so a
  script has to import `samples/DSC01157.ARW` itself via `input[type=file]`
  before there is anything to drive.
- `Decoded` in `.status-value` can be STALE from the previous image right after
  triggering a new import. Gate on the expected `.film-cell` count together with
  the `Decoded` text, not on the text alone.

- WebGL renders fine in headless Chrome; screenshots show the real render.
- Don't run `pnpm check`/`vitest` as verification — drive the app.
