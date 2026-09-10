r"""导出时抓到的全分辨率 ITP tile,用 llr_worker.sony.itp 从 WB 标签重算逐位比。

    bash run_py.sh itp_export_verify.py <npz> <ARW>
"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np  # noqa: E402
import rawpy  # noqa: E402

from llr_worker.sony import itp  # noqa: E402


def main() -> int:
    z = np.load(sys.argv[1])
    with rawpy.imread(sys.argv[2]) as raw:
        cwb = [float(v) for v in raw.camera_whitebalance]
        black = float(np.mean(raw.black_level_per_channel))
    wb = (cwb[0], cwb[1], cwb[3] if cwb[3] > 0 else cwb[1], cwb[2])
    gains = itp.wb_gains(wb)
    print("WB", wb, "gains x2048", (gains * 2048).astype(int), "black", black)
    for k in range(2):
        if f"in_{k}" not in z:
            continue
        mos = z[f"in_{k}"]
        rect = tuple(int(v) for v in z[f"meta{k}_rect"])
        pos = z[f"meta{k}_pos"]
        got = itp.itp_tile(mos, gains, black, rect)
        x0, y0, x1, y1 = rect
        t = 12
        print(f"tile{k} {mos.shape[1]}x{mos.shape[0]} rect {rect} pos {pos}")
        for c in range(3):
            want = z[f"out{c}_{k}"]
            d = np.abs(got[c].astype(int) - want.astype(int))[y0 + t:y1 - t, x0 + t:x1 - t]
            print(f"   out{c}: 逐位 {np.mean(d == 0) * 100:.4f}%  max|d| {d.max()}  n>0 {(d > 0).sum()}/{d.size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
