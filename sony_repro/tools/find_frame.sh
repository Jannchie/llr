#!/usr/bin/env bash
# 找一张 RAW:先看 app 的 session 目录,再在 /mnt/e 下搜(限深度)。用法: bash find_frame.sh DSC02919
set -u
stem="$1"
echo "== sessions"
for d in "$HOME"/llr/tmp/sessions/*/; do
  f=$(ls "$d"source.* 2>/dev/null | head -1)
  [ -n "$f" ] || continue
  echo "$d: $(exiftool -q -T -Model -ISO -CreativeStyle -DynamicRangeOptimizer -DateTimeOriginal "$f" 2>/dev/null)"
done
echo "== /mnt/e (maxdepth 3)"
find /mnt/e -maxdepth 3 -iname "${stem}*" 2>/dev/null | head
