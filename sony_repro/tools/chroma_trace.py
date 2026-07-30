"""把色差噪声在 llr 自己这几步里追一遍:马赛克 → demosaic → 白平衡。

背景:llr 降噪后的马赛克「色差/同色」只有 0.10~0.30,比 Edit 进 ITP 时(~1.35)
干净得多,可 llr 成品的色差是 Edit 的 6.6 倍 —— 噪声是马赛克**之后**来的。

⚠️ **跨网格的比较不成立。** 马赛克的相位网格是半分辨率,它的「1px 细节」对应全网格
的 2px。拿马赛克那一行去比 demosaic 之后那一行,分母(同色细节)会因为网格变化而
塌掉,比值凭空涨十几倍 —— 头一版就是这么读的,得出「demosaic 造了 44 倍色差噪声」,
**是错的**。真正干净的对照是**同网格、只差一件事**的那两行:
`AHD 无白平衡` 对 `AHD + 白平衡`。

结论(见 notes/measured-chroma-gap.md §2.6):放大色差噪声的是**白平衡**
(R−G ×1.1~4.3),不是 demosaic 算法 —— AHD / VNG / DHT 三者的比值差异在 20% 以内。
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import rawpy

sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")
from llr_worker.denoise import denoise_raw_inplace, get_denoiser  # noqa: E402
from llr_worker.fit_profile import postprocess_camera_native  # noqa: E402
from llr_worker.sony.rawnr import detail_restore, noise_model  # noqa: E402

SRC = Path("/mnt/e/10960725")
TILE = 32


def mad(x):
    return float(np.median(np.abs(x - np.median(x))) * 1.4826)


def detail(a):
    h, w = a.shape[0] & ~1, a.shape[1] & ~1
    a = a[:h, :w]
    m = (a[0::2, 0::2] + a[1::2, 0::2] + a[0::2, 1::2] + a[1::2, 1::2]) * 0.25
    return a - np.repeat(np.repeat(m, 2, axis=0), 2, axis=1)


def flat_tiles(y, frac=0.25, cap=250):
    h, w = y.shape[0] // TILE * TILE, y.shape[1] // TILE * TILE
    v = y[:h, :w].reshape(h // TILE, TILE, w // TILE, TILE).std(axis=(1, 3))
    ys, xs = np.nonzero(v <= np.quantile(v, frac))
    if not len(ys):
        return []
    idx = np.linspace(0, len(ys) - 1, min(cap, len(ys))).astype(int)
    return [(int(ys[k]) * TILE, int(xs[k]) * TILE) for k in idx]


def triple_ratio(green, a, b, scale=1.0):
    """三张同网格的图:同色基准 green,两条色差 a-green / b-green。"""
    same, da, db = [], [], []
    for y, x in flat_tiles(green):
        s = (slice(y, y + TILE), slice(x, x + TILE))
        same.append(mad(detail(green[s])))
        da.append(mad(detail(a[s] - green[s])))
        db.append(mad(detail(b[s] - green[s])))
    ms, ma, mb = (np.median(v) * scale for v in (same, da, db))
    return ms, ma, mb, (ma + mb) / 2 / max(ms, 1e-9)


def main():
    stems = sys.argv[1:] or ["DSC02995", "DSC02961", "DSC03025"]
    print(f"{'片':>10} {'位置':>14} {'同色/G':>9} {'R-G':>9} {'B-G':>9} {'色差/同色':>9}")
    print("-" * 66)
    for stem in stems:
        path = SRC / f"{stem}.ARW"
        curve, restore = noise_model(path), detail_restore(path)
        with rawpy.imread(str(path)) as raw:
            r = restore.for_edge_slider(50.0) if restore else None
            denoise_raw_inplace(
                raw, get_denoiser("wavelet"), noise=curve,
                detail=None if r is None or curve is None
                else (r.fraction, r.limit_in_thresholds(curve)),
                chroma_scale=1.0)
            m = raw.raw_image_visible.astype(np.float64)
            p = [m[i::2, j::2] for i in (0, 1) for j in (0, 1)]
            n0 = min(x.shape[0] for x in p)
            n1 = min(x.shape[1] for x in p)
            p00, p01, p10, p11 = (x[:n0, :n1] for x in p)
            ms, ma, mb, rat = triple_ratio((p01 + p10) * 0.5, p00, p11)
            print(f"{stem:>10} {'马赛克(降噪后)':>14} {ms:9.3f} {ma:9.3f} {mb:9.3f}"
                  f" {rat:9.3f}")

            rgb = postprocess_camera_native(raw, half_size=False)
            # 把白平衡摘掉,单看 demosaic 自己 —— 两者在 postprocess 里是绑在一起的
            plain = {}
            for name, alg in (("AHD", rawpy.DemosaicAlgorithm.AHD),
                              ("VNG", rawpy.DemosaicAlgorithm.VNG),
                              ("DHT", rawpy.DemosaicAlgorithm.DHT)):
                try:
                    plain[name] = raw.postprocess(
                        user_wb=[1.0, 1.0, 1.0, 1.0], no_auto_bright=True,
                        output_color=rawpy.ColorSpace.raw, gamma=(1, 1),
                        output_bps=16, demosaic_algorithm=alg,
                    ).astype(np.float64) / 4.0   # 16 位回到 14 位标度
                except Exception as e:  # noqa: BLE001
                    print(f"    {name} 不可用: {e}")
        rgb = rgb.astype(np.float64)
        # 相机 RGB 是 0..1 线性;乘回 16383 才好与马赛克的量级比
        ms, ma, mb, rat = triple_ratio(rgb[..., 1], rgb[..., 0], rgb[..., 2],
                                       scale=16383.0)
        print(f"{stem:>10} {'AHD + 白平衡':>14} {ms:9.3f} {ma:9.3f} {mb:9.3f}"
              f" {rat:9.3f}")
        for name, a in plain.items():
            ms, ma, mb, rat = triple_ratio(a[..., 1], a[..., 0], a[..., 2])
            print(f"{stem:>10} {name + ' 无白平衡':>14} {ms:9.3f} {ma:9.3f} {mb:9.3f}"
                  f" {rat:9.3f}")
    print("\n两行之间只有 demosaic 与白平衡。比值若从 ~0.2 跳到 ~1,"
          "\n就是 demosaic 在造色差噪声,而 Edit 的 ITP 不这么干。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
