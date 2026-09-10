r"""Marble 的色差清理强度是否随降噪模式变:导出时降噪自动 / 关 各抓的 tile,与 llr chromanr 比。

    bash run_py.sh marble_mode_cmp.py
"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np  # noqa: E402

from llr_worker.sony.chromanr import apply_chroma_nr  # noqa: E402

TOOLS = "/home/jannchie/llr/sony_repro/tools/"
W601 = np.array([0.299, 0.587, 0.114])


def hp(x):
    k = np.zeros_like(x)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            k += np.roll(np.roll(x, dy, 0), dx, 1)
    return x - k / 9


def mad(v):
    return float(np.median(np.abs(v - np.median(v))) * 1.4826)


def main():
    for fn, lab in (("tiles_SIMDMarble_export.npz", "降噪自动"), ("tiles_SIMDMarble_export_nroff.npz", "降噪关")):
        z = np.load(TOOLS + fn)
        for t in ("t0", "t1", "t2"):
            a = z[f"{t}_in"].astype(np.float32)
            b = z[f"{t}_out"].astype(np.float32)
            m = z[f"{t}_meta"]
            x0, y0, x1, y1 = m[0x30 // 4:0x40 // 4]
            s = (slice(y0 + 16, y1 - 16), slice(x0 + 16, x1 - 16))
            ours = apply_chroma_nr(np.clip(a / 16383.0, 0, 1), subsample=8) * 16383.0
            parts = []
            for c, nm in ((0, "R-G"), (2, "B-G")):
                din = hp(a[..., c] - a[..., 1])[s]
                dout = hp(b[..., c] - b[..., 1])[s]
                do = hp(ours[..., c] - ours[..., 1])[s]
                parts.append(f"{nm} in {mad(din):.0f} -> 引擎 {mad(dout):.1f} | llr {mad(do):.1f}")
            ya, yb = a @ W601, b @ W601
            print(f"{lab} {t}: " + " ; ".join(parts) + f" ; 亮度改动 median {np.median(np.abs(yb - ya)[s]):.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
