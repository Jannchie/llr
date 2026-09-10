#!/bin/bash
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$HOME/.nvm/versions/node/v22.22.1/bin:$HOME/.local/bin:$PATH"
cd "$ROOT/apps/worker"
uv run ruff check src tests --output-format=concise
