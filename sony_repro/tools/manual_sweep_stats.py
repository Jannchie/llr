r"""手动档滑块扫描的统计:每个 suffix 一行,量 Marble 对色差细节带/整体的压缩比,和 RawNR 出口的变化。

    bash -lc "cd ~/llr/apps/worker && uv run python ../../sony_repro/tools/manual_sweep_stats.py m50-5 m50-0 m50-10 ..."

Marble 指标(色差平面 R−G / B−G,rect 内缩 16,3x3 高通):
    fine_ratio = MAD(hp(out)) / MAD(hp(in))      细节带剩多少
    all_ratio  = MAD(out − in 的整体) / MAD(in 整体)  (参考)
    dY         = 亮度中位变化
RawNR 指标:出口相对入口的细节带 MAD 比(降噪强度),以及和第一个 suffix 出口的逐位一致率。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def hp(x):
    k = np.zeros_like(x)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            k += np.roll(np.roll(x, dy, 0), dx, 1)
    return x - k / 9


def mad(v):
    return float(np.median(np.abs(v - np.median(v))) * 1.4826)


def rect(meta, shape):
    x0, y0, x1, y1 = meta[0x30 // 4:0x40 // 4]
    return (slice(y0 + 16, y1 - 16), slice(x0 + 16, x1 - 16))


def marble_stats(path):
    z = np.load(path)
    rows = []
    for t in ("t0", "t1", "t2"):
        if f"{t}_out" not in z:
            continue
        a = z[f"{t}_in"].astype(np.float32)
        b = z[f"{t}_out"].astype(np.float32)
        s = rect(z[f"{t}_meta"], a.shape)
        r = []
        for c in (0, 2):
            di, do = (a[..., c] - a[..., 1])[s], (b[..., c] - b[..., 1])[s]
            r.append(mad(hp(do)) / max(mad(hp(di)), 1e-6))
            r.append(mad(do - np.median(do)) / max(mad(di - np.median(di)), 1e-6))
        y = a @ np.array([0.299, 0.587, 0.114], np.float32)
        y2 = b @ np.array([0.299, 0.587, 0.114], np.float32)
        r.append(float(np.median((y2 - y)[s])))
        rows.append(r)
    return np.mean(rows, axis=0), z


def by_pos(z):
    """RawNR 的 tile 顺序随线程调度变,按 tile 在整幅里的位置(meta 0x48..0x4c)配对。"""
    out = {}
    for t in ("t0", "t1", "t2"):
        if f"{t}_out" in z:
            m = z[f"{t}_meta"]
            out[(int(m[0x48 // 4]), int(m[0x4c // 4]), z[f"{t}_in"].shape)] = t
    return out


def rawnr_stats(path, ref):
    z = np.load(path)
    rows = []
    refpos = by_pos(ref) if ref is not None else {}
    for key, t in by_pos(z).items():
        a = z[f"{t}_in"][..., 0].astype(np.float32)
        b = z[f"{t}_out"][..., 0].astype(np.float32)
        s = rect(z[f"{t}_meta"], a.shape)
        rt = refpos.get(key)
        same_in = np.array_equal(z[f"{t}_in"], ref[f"{rt}_in"]) if rt else None
        eq = (z[f"{t}_out"] == ref[f"{rt}_out"]).mean() if rt else np.nan
        rows.append([mad(hp(b)[s]) / max(mad(hp(a)[s]), 1e-6), float(eq), float(bool(same_in)) if same_in is not None else np.nan])
    return np.nanmean(rows, axis=0) if rows else np.full(3, np.nan)


def main():
    sufs = sys.argv[1:]
    print(f"{'suffix':<12} {'Marble R-G fine/all':>20} {'B-G fine/all':>16} {'dY':>6}   {'RawNR fine ratio':>16} {'==first out':>11} {'in==first':>9}")
    ref = None
    for suf in sufs:
        # 'auto' / 'nroff' 是上一轮自动档/关档的捕获(tiles_SIMDMarble_export[_nroff].npz),当参照。
        alias = {"auto": "tiles_SIMDMarble_export.npz", "nroff": "tiles_SIMDMarble_export_nroff.npz"}
        mp = os.path.join(HERE, alias.get(suf, f"tiles_SIMDMarble_export_{suf}.npz"))
        rp = os.path.join(HERE, f"tiles_RawNRSIMD_export_{suf}.npz")
        m = marble_stats(mp)[0] if os.path.exists(mp) else np.full(5, np.nan)
        if os.path.exists(rp):
            if ref is None:
                ref = np.load(rp)
            r = rawnr_stats(rp, ref)
        else:
            r = np.full(3, np.nan)
        print(f"{suf:<12} {m[0]:9.3f} / {m[1]:6.3f}  {m[2]:7.3f} / {m[3]:6.3f} {m[4]:6.1f}   {r[0]:16.3f} {r[1] * 100:10.3f}% {r[2]:9.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
