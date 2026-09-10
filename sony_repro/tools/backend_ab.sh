#!/usr/bin/env bash
# numpy 后端 vs numba 后端,三帧整链 A/B。用法: bash backend_ab.sh [ARW ...]
set -u
cd "$HOME/llr/apps/worker" || exit 1
T="$HOME/llr/sony_repro/tools/backend_ab.py"
frames=("$@")
if [ ${#frames[@]} -eq 0 ]; then
  frames=(/mnt/e/temp_photo/DSC03036.ARW /mnt/e/temp_photo/cs_fl_test.ARW /mnt/e/temp_photo/cs_DSC02961.ARW)
fi
for f in "${frames[@]}"; do
  echo "== $f"
  LLR_ITP_BACKEND=numpy LLR_RAWNR_BACKEND=numpy LLR_DENOISE_BACKEND=numpy uv run python "$T" "$f" /tmp/lin_numpy.bin 2>&1 | grep -v "^demosaic\|^denoise "
  uv run python "$T" "$f" /tmp/lin_numba.bin 2>&1 | grep -v "^demosaic\|^denoise "
  uv run python "$T" --compare /tmp/lin_numpy.bin /tmp/lin_numba.bin
done
