r"""highiso_gap 的补充:低频色偏(按 16px 盒均值分桶,避开噪声选择效应)与孤立彩点统计(MAD 看不见)。

    cd apps/worker && uv run --with scipy python ../../sony_repro/tools/highiso_bias.py <workdir> [--win 1600]

依赖 highiso_gap 已经缓存好的 <workdir>/DSC03692-*.npy 与 llr_*.npy。
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np  # noqa: E402
from scipy import ndimage  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from highiso_gap import M601, W601, bands, crop_center, darkest_flat  # noqa: E402

YB = ((0, .03), (.03, .06), (.06, .1), (.1, .15), (.15, .25), (.25, .4), (.4, .6), (.6, .8), (.8, 1.01))


def box(x, n=16):
    return np.stack([ndimage.uniform_filter(x[..., i], n, mode="reflect") for i in range(x.shape[-1])], -1)


def main() -> int:
    work = Path(sys.argv[1])
    win = int(sys.argv[sys.argv.index("--win") + 1]) if "--win" in sys.argv else 1600
    names = {"E_off": "DSC03692-off16.npy", "E_auto": "DSC03692-auto16.npy", "E_m75": "DSC03692-m75.npy",
             "E_m100": "DSC03692-m100.npy", "L_off": "llr_off.npy", "L_auto": "llr_auto.npy", "L_nomarble": "llr_nomarble.npy"}
    full = {k: np.load(work / v, mmap_mode="r") for k, v in names.items()}
    H, W = full["E_auto"].shape[:2]
    yref = np.asarray(full["E_auto"], np.float32) @ W601
    df = darkest_flat(yref)
    wins = {"中央": (H // 2, W // 2, win), "暗平坦": (df[1] + 384, df[2] + 384, 768)}
    del yref
    for wname, (cy, cx, wsz) in wins.items():
        print(f"\n=================== 窗口「{wname}」{wsz}² @ ({cy},{cx}) ===================")
        img = {k: crop_center(v, wsz, cy, cx).astype(np.float32) for k, v in full.items()}
        yc = {k: v @ M601.T for k, v in img.items()}
        lo = {k: box(v) for k, v in yc.items()}
        yb = lo["E_auto"][..., 0]
        print("\nB'. 16px 低频差,按 Edit auto 的 16px 低频 Y 分桶(×255):")
        print(f"  {'Y桶':<12}{'n':>8} | llr auto−Edit auto: {'ΔY':>6}{'ΔCb':>6}{'ΔCr':>6} | llr off−Edit off: {'ΔY':>6}{'ΔCb':>6}{'ΔCr':>6} | Edit auto−off: {'ΔY':>6}{'ΔCb':>6}{'ΔCr':>6} | llr auto−off: {'ΔY':>6}{'ΔCb':>6}{'ΔCr':>6} | |C| llr/Edit")
        for a, b in YB:
            m = (yb >= a) & (yb < b)
            if m.sum() < 3000:
                continue
            d1 = (lo["L_auto"] - lo["E_auto"])[m].mean(0) * 255
            d2 = (lo["L_off"] - lo["E_off"])[m].mean(0) * 255
            d3 = (lo["E_auto"] - lo["E_off"])[m].mean(0) * 255
            d4 = (lo["L_auto"] - lo["L_off"])[m].mean(0) * 255
            cr = np.hypot(lo["L_auto"][..., 1], lo["L_auto"][..., 2])[m].mean() / max(np.hypot(lo["E_auto"][..., 1], lo["E_auto"][..., 2])[m].mean(), 1e-6)
            f = lambda d: "".join(f"{v:+6.2f}" for v in d)  # noqa: E731
            print(f"  [{a:.2f},{b:.2f}) {int(m.sum()):8d} | {'':19}{f(d1)} | {'':17}{f(d2)} | {'':14}{f(d3)} | {'':13}{f(d4)} | {cr:.3f}")

        print("\nE. 孤立点/离群统计:≤2px 两带之和的 |v| 超过阈值的像素比例(%),Y / Cb / Cr")
        print(f"  {'图':<12}" + "".join(f"{'Y>' + str(t):>8}" for t in (4, 8, 16)) + "".join(f"{'Cb>' + str(t):>8}" for t in (2, 4, 8)) + "".join(f"{'Cr>' + str(t):>8}" for t in (2, 4, 8)))
        for k in ("E_off", "E_auto", "E_m75", "E_m100", "L_off", "L_nomarble", "L_auto"):
            row = f"  {k:<12}"
            for c, ths in ((0, (4, 8, 16)), (1, (2, 4, 8)), (2, (2, 4, 8))):
                bb = bands(yc[k][..., c])
                hp = np.abs(bb[0] + bb[1]) * 255
                row += "".join(f"{np.mean(hp > t) * 100:8.3f}" for t in ths)
            print(row)
        # 「llr auto 有而 Edit auto 没有」的离群点:同一位置比较
        for c, lab, t in ((0, "Y", 8), (1, "Cb", 3), (2, "Cr", 3)):
            be = bands(yc["E_auto"][..., c]); bl = bands(yc["L_auto"][..., c])
            he = np.abs(be[0] + be[1]) * 255; hl = np.abs(bl[0] + bl[1]) * 255
            print(f"  [{lab}] |hp|>{t}: Edit {np.mean(he > t) * 100:.3f}%  llr {np.mean(hl > t) * 100:.3f}%  两者都超 {np.mean((he > t) & (hl > t)) * 100:.3f}%  "
                  f"llr 超/Edit 不超 {np.mean((hl > t) & ~(he > t)) * 100:.3f}%   p99.9: Edit {np.percentile(he, 99.9):.2f} llr {np.percentile(hl, 99.9):.2f}")
        if "--png" in sys.argv:
            from PIL import Image
            n, z = 240, 4
            tiles = [crop_center(img[k], n) for k in ("E_off", "E_auto", "E_m100", "L_off", "L_auto")]
            row = np.concatenate([np.kron(t, np.ones((z, z, 1), np.float32)) for t in tiles], 1)
            # 暗部拉伸 ×3 看清楚
            Image.fromarray(np.clip(row * 255 * 3, 0, 255).astype(np.uint8)).save(work / f"zoom_{wname}.png")
            print(f"  PNG(×3 增益)-> {work / f'zoom_{wname}.png'}  顺序 Edit off / Edit auto / Edit m100 / llr off / llr auto")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
