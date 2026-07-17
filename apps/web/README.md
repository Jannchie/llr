# @llr/web

Vue 3 + WebGL2 editor UI. This is the **authoritative renderer**: every pixel
the user sees — live preview and full-resolution export — comes from the same
fused single-pass shader over the worker's scene-linear data.

## Layout

- `src/rendering/` — the pipeline: `pipeline-renderer.ts` (WebGL orchestration,
  GPU histogram, read-back), `passes.ts` (GLSL), `tonal-model.ts` /
  `color-spaces.ts` / `curve.ts` / `hsl-bands.ts` (the math, mirrored into the
  shader and unit-tested), `crop.ts`, `histogram.ts`.
- `src/composables/` — editing mechanics: library/persistence, undo history,
  viewport pan/zoom, crop editor, tone-curve editor, histogram overlay, export.
- `src/api.ts` — the `/render-linear` binary protocol client.
- `src/persistence.ts` — IndexedDB sessions, per-image edits, thumbnails.
- `src/App.vue` — editing state + panel UI, wiring the above together.

## Commands

```bash
pnpm --filter @llr/web dev     # vite (proxies /api to :8790)
pnpm --filter @llr/web test    # vitest
pnpm --filter @llr/web check   # vue-tsc
```

Rendering-math changes should come with a spec in `src/rendering/__tests__/`;
anything pixel-visible is verified by driving the real app (see
`.claude/skills/verify`).
