r"""在导出(降噪自动)的 Marble tile 上扫 chromanr 的参数,找最接近引擎出口的一档。

指标(色差平面 R-G / B-G,rect 内缩 16):
  fine  = 3x3 高通后 |ours - engine| 的 MAD(细节带残差)
  all   = |ours - engine| 的 MAD(整体残差)
  edge  = 亮度梯度前 10% 像素上的整体残差(保边好不好)
参照:什么都不做(in vs out)与引擎自身的噪声底。

    bash run_py.sh chromanr_sweep.py
"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np  # noqa: E402

from llr_worker.sony.chromanr import apply_chroma_nr  # noqa: E402

TOOLS = "/home/jannchie/llr/sony_repro/tools/"
W601 = np.array([0.299, 0.587, 0.114], np.float32)


def hp(x):
    k = np.zeros_like(x)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            k += np.roll(np.roll(x, dy, 0), dx, 1)
    return x - k / 9


def mad(v):
    return float(np.median(np.abs(v - np.median(v))) * 1.4826)


def score(ours, eng, s, edge):
    r = []
    for c in (0, 2):
        do = ours[..., c] - ours[..., 1]
        de = eng[..., c] - eng[..., 1]
        fine = mad((hp(do) - hp(de))[s])
        allr = mad((do - de)[s])
        edg = mad((do - de)[s][edge])
        r.append((fine, allr, edg))
    return r


def main():
    z = np.load(TOOLS + "tiles_SIMDMarble_export.npz")
    tiles = []
    for t in ("t0", "t1", "t2"):
        a = z[f"{t}_in"].astype(np.float32)
        b = z[f"{t}_out"].astype(np.float32)
        m = z[f"{t}_meta"]
        x0, y0, x1, y1 = m[0x30 // 4:0x40 // 4]
        s = (slice(y0 + 16, y1 - 16), slice(x0 + 16, x1 - 16))
        y = a @ W601
        gy = np.abs(np.gradient(y, axis=0)) + np.abs(np.gradient(y, axis=1))
        edge = gy[s] > np.percentile(gy[s], 90)
        tiles.append((a, b, s, edge))
    cands = [("none", dict(levels=0))]
    for lv in (3, 4, 5, 6):
        for eps in (1e-4, 1e-3, 1e-2, 1e-1):
            for sub in (8,):
                cands.append((f"guide L{lv} eps{eps:g} sub{sub} a1.0", dict(levels=lv, guide=True, eps=eps, subsample=sub, amount=1.0)))
        cands.append((f"guide L{lv} eps1e-4 sub8 a0.9", dict(levels=lv, guide=True, eps=1e-4, subsample=8, amount=0.9)))
    for lv in (2, 3, 4, 5):
        cands.append((f"pyramid L{lv} a1.0", dict(levels=lv, guide=False, amount=1.0)))
    print(f"{'候选':<32} {'R-G fine':>9} {'all':>7} {'edge':>7}   {'B-G fine':>9} {'all':>7} {'edge':>7}")
    for name, kw in cands:
        acc = np.zeros(6)
        for a, b, s, edge in tiles:
            ours = a if kw.get("levels", 1) == 0 else apply_chroma_nr(a / 16383.0, **kw) * 16383.0
            r = score(ours, b, s, edge)
            acc += np.array([r[0][0], r[0][1], r[0][2], r[1][0], r[1][1], r[1][2]])
        acc /= len(tiles)
        print(f"{name:<32} {acc[0]:9.1f} {acc[1]:7.1f} {acc[2]:7.1f}   {acc[3]:9.1f} {acc[4]:7.1f} {acc[5]:7.1f}")
    # 引擎自身:out 的细节带 MAD(噪声底)
    a, b, s, edge = tiles[0]
    print("引擎 out 细节带 MAD  R-G", round(mad(hp(b[..., 0] - b[..., 1])[s]), 1), " B-G", round(mad(hp(b[..., 2] - b[..., 1])[s]), 1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
