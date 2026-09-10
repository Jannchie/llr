r"""numpy 后端 vs numba 后端:同一张 RAW 走完整的 render-linear,把 f16 输出落盘并计时。

    LLR_ITP_BACKEND=numpy LLR_RAWNR_BACKEND=numpy LLR_DENOISE_BACKEND=numpy \
        uv run python ../../sony_repro/tools/backend_ab.py <ARW> /tmp/lin_numpy.bin
    uv run python ../../sony_repro/tools/backend_ab.py <ARW> /tmp/lin_numba.bin
然后 `backend_ab.py --compare /tmp/lin_numpy.bin /tmp/lin_numba.bin`。
后端开关在 import 时读一次,所以两次必须是两个进程。
"""
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np  # noqa: E402


def main():
    if sys.argv[1] == "--compare":
        a = np.fromfile(sys.argv[2], dtype=np.float16)
        b = np.fromfile(sys.argv[3], dtype=np.float16)
        assert a.size == b.size, (a.size, b.size)
        diff = a != b
        n = int(diff.sum())
        print(f"元素 {a.size}  不同 {n} ({n / a.size * 100:.6f}%)  逐位相同 {np.array_equal(a.view(np.uint16), b.view(np.uint16))}")
        if n:
            d = np.abs(a[diff].astype(np.float32) - b[diff].astype(np.float32))
            print(f"  最大差 {d.max():.6g}  均差 {d.mean():.6g}")
        return 0
    from llr_worker import cli
    from llr_worker.sony import itp, rawnr_simd
    from llr_worker import denoise
    arw, out = sys.argv[1], sys.argv[2]
    print("backends:", itp.BACKEND, rawnr_simd.BACKEND, denoise.BACKEND)
    req = dict(command="render-linear", input=arw, output=out, profile="standard", halfSize=False, maxSize=0,
               recipe={"autoTone": False}, denoise={"enabled": True, "auto": True, "amount": 1.0, "edge": 50, "chroma": 50})
    t0 = time.perf_counter()
    meta = cli.daemon_linear(req, Path.home() / "llr")
    print(f"{meta['width']}x{meta['height']}  {time.perf_counter() - t0:.2f} s  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
