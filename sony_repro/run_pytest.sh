#!/bin/bash
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$HOME/.local/bin:$PATH"
cd "$ROOT/apps/worker"
uv run pytest "$@"
