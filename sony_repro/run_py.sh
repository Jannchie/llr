#!/bin/bash
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# 在 worker 的 uv 环境里跑 sony_repro 的任意脚本。第一个参数是脚本名(不含目录)。
export PATH="$HOME/.local/bin:$PATH"
script="$1"; shift
cd "$ROOT/apps/worker"
uv run python "$ROOT/sony_repro/tools/$script" "$@"
