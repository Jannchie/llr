r"""几版 llr 成品对 Edit 自动档:低频色偏(16px,按 Y 分桶)+ 各带 MAD + 色度残差比,一张表看改进幅度。

    cd apps/worker && uv run --with scipy python ../../sony_repro/tools/highiso_variants.py <workdir> edit=DSC03692-auto16.npy auto=llr_auto.npy fixA=llr_fixA.npy fixAB=llr_fixAB.npy [--win 1600]
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np  # noqa: E402
from scipy import ndimage  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from highiso_gap import BANDS, M601, W601, band_mads, bands, crop_center, darkest_flat  # noqa: E402

YB = ((0, .03), (.03, .06), (.06, .1), (.1, .15), (.15, .25), (.25, .4), (.4, .7), (.7, 1.01))


def box(x, n=16):
    return np.stack([ndimage.uniform_filter(x[..., i], n, mode="reflect") for i in range(3)], -1)


def main():
    work = Path(sys.argv[1])
    win = int(sys.argv[sys.argv.index("--win") + 1]) if "--win" in sys.argv else 1600
    files = dict(a.split("=") for a in sys.argv[2:] if "=" in a)
    full = {k: np.load(work / v, mmap_mode="r") for k, v in files.items()}
    H, W = full["edit"].shape[:2]
    yref = np.asarray(full["edit"], np.float32) @ W601
    df = darkest_flat(yref)
    wins = {"中央": (H // 2, W // 2, win), "暗平坦": (df[1] + 384, df[2] + 384, 768)}
    del yref
    for wname, (cy, cx, wsz) in wins.items():
        print(f"\n=================== 窗口「{wname}」{wsz}² @ ({cy},{cx}) ===================")
        yc = {k: crop_center(v, wsz, cy, cx).astype(np.float32) @ M601.T for k, v in full.items()}
        lo = {k: box(v) for k, v in yc.items()}
        yb = lo["edit"][..., 0]
        names = [k for k in files if k != "edit"]
        print("低频(16px)llr − Edit auto,×255;每格 ΔY / ΔCb / ΔCr,末列 |C| 比")
        print(f"  {'Y桶':<12}{'n':>8} " + "".join(f"| {k:^26}" for k in names))
        for a, b in YB:
            m = (yb >= a) & (yb < b)
            if m.sum() < 3000:
                continue
            row = f"  [{a:.2f},{b:.2f}) {int(m.sum()):8d} "
            for k in names:
                d = (lo[k] - lo["edit"])[m].mean(0) * 255
                cr = np.hypot(lo[k][..., 1], lo[k][..., 2])[m].mean() / max(np.hypot(lo["edit"][..., 1], lo["edit"][..., 2])[m].mean(), 1e-6)
                row += f"| {d[0]:+5.2f} {d[1]:+5.2f} {d[2]:+5.2f} {cr:5.3f} "
            print(row)
        rows = {k: band_mads(v) for k, v in yc.items()}
        print("\n各带 MAD(/255)")
        for c, lab in enumerate(("Y", "Cb", "Cr")):
            print(f"  [{lab}]{'':<17}" + "".join(f"{b:>7}" for b in BANDS))
            for k, r in rows.items():
                print(f"  {k:<22}" + "".join(f"{v:7.3f}" for v in r[c]))
            if lab != "Y":
                for k in names:
                    print(f"  {k + '/edit':<22}" + "".join(f"{v:7.2f}" for v in rows[k][c] / np.maximum(rows['edit'][c], 1e-6)))
        print("\n差图 llr − Edit 的各带 MAD(/255)")
        for c, lab in enumerate(("Y", "Cb", "Cr")):
            print(f"  [{lab}]{'':<17}" + "".join(f"{b:>7}" for b in BANDS))
            for k in names:
                r = band_mads(yc[k] - yc["edit"])
                print(f"  {k:<22}" + "".join(f"{v:7.3f}" for v in r[c]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
