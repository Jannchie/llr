#!/bin/bash
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# 类型检查 + 测试。node 走 nvm 的版本,uv 走 ~/.local/bin。
export PATH="$HOME/.nvm/versions/node/v22.22.1/bin:$HOME/.local/bin:$PATH"
echo "===== web check (vue-tsc) ====="
cd "$ROOT/apps/web" && npm run -s check 2>&1 | tail -25
echo "===== web test ====="
cd "$ROOT/apps/web" && npm run -s test 2>&1 | tail -20
echo "===== api check ====="
cd "$ROOT/apps/api" && npm run -s check 2>&1 | tail -25
echo "===== api test ====="
cd "$ROOT/apps/api" && npm run -s test 2>&1 | tail -20
echo "===== worker tests ====="
cd "$ROOT/apps/worker" && uv run pytest -q 2>&1 | tail -15
