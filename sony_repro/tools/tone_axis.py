"""检验:把色调曲线从「逐通道套 RGB」改成「只作用在亮度」,色差噪声掉多少?

这是 §2.4 那条结论的直接检验,不是实现方案。最干净的形式是把亮度增量加回三通道:

    s' = s + (tone(Y) - Y)          # R−G 与 B−G 逐位不变

这就是「亮度被放大、色差不动」的极端版。若色差噪声掉到 Edit 的量级,
说明差距确实出在色调作用的位置。副作用(暗部会显得欠饱和)是已知的,
Edit 靠 ChromaSuppres 和色度增益去补 —— 这里不管那个,只量噪声。
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")
sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")
import e2e_pipeline as E  # noqa: E402
from engine_final_check import align, to_grid  # noqa: E402
from llr_worker.cli import prepare_linear  # noqa: E402

SRC = Path("/mnt/e/10960725")
TMP = Path("/home/jannchie/llr/tmp")
FULL = 16383
TILE = 32
W601 = np.array([0.299, 0.587, 0.114], np.float32)


def mad(x):
    return float(np.median(np.abs(x - np.median(x))) * 1.4826)


def detail(a):
    h, w = a.shape[0] & ~1, a.shape[1] & ~1
    a = a[:h, :w]
    m = (a[0::2, 0::2] + a[1::2, 0::2] + a[0::2, 1::2] + a[1::2, 1::2]) * 0.25
    return a - np.repeat(np.repeat(m, 2, axis=0), 2, axis=1)


def flat_tiles(y, frac=0.25, cap=200):
    h, w = y.shape[0] // TILE * TILE, y.shape[1] // TILE * TILE
    v = y[:h, :w].reshape(h // TILE, TILE, w // TILE, TILE).std(axis=(1, 3))
    ys, xs = np.nonzero(v <= np.quantile(v, frac))
    if not len(ys):
        return []
    idx = np.linspace(0, len(ys) - 1, min(cap, len(ys))).astype(int)
    return [(int(ys[k]) * TILE, int(xs[k]) * TILE) for k in idx]


def stats(name, rgb255):
    ly, la, lb = [], [], []
    luma = rgb255 @ W601
    for y, x in flat_tiles(luma):
        s = (slice(y, y + TILE), slice(x, x + TILE))
        ly.append(mad(detail(luma[s])))
        la.append(mad(detail(rgb255[s][..., 0] - rgb255[s][..., 1])))
        lb.append(mad(detail(rgb255[s][..., 2] - rgb255[s][..., 1])))
    my, ma, mb = np.median(ly), np.median(la), np.median(lb)
    print(f"  {name:<22} 亮度 {my:8.3f}   (R-G) {ma:7.3f}   (B-G) {mb:7.3f}"
          f"   色差/亮度 {(ma + mb) / 2 / max(my, 1e-9):6.3f}")
    return my, ma, mb


def render_two(path):
    """照 e2e_pipeline.render 的顺序走,只在色调那一步分叉。"""
    r = prepare_linear(path, {"profileId": "sony"}, E.ROOT, None, False,
                       half_size=False, denoise_model="wavelet")
    cp = r.color_profile
    c = r.linear.astype(np.float32)
    pts = cp.get("profileToneCurve")
    if not pts:
        raise SystemExit("这张片没有 profileToneCurve")
    p = np.asarray(pts, np.float64)
    grid = np.linspace(0, 1, 2048)
    lut = np.interp(grid, p[:, 0], p[:, 1])
    srgb_basis = cp["kind"] == "sony"
    s0 = (c @ E.PROPHOTO_TO_SRGB.T if srgb_basis else c)
    s0 = np.clip(s0, 0, 1)

    out = {}
    # (a) 现状:逐通道
    out["逐通道 (现状)"] = np.interp(s0, grid, lut).astype(np.float32)
    # (b) 只作用在亮度,色差加性保留
    y = (s0 @ W601).astype(np.float32)
    y2 = np.interp(np.clip(y, 0, 1), grid, lut).astype(np.float32)
    out["只作用在亮度"] = s0 + (y2 - y)[..., None]

    # 第三个变体:亮度色调 + **跳过** sony_chroma。两条色差改善得不对称
    # (B 到位、R 还差 3.8 倍),而 sony_chroma 是这之后唯一还动色度的一步 ——
    # 跳掉它就能判断那 3.8 倍是不是它造成的。
    SKIP = "只作用在亮度 + 跳过 sony_chroma"
    out[SKIP] = out["只作用在亮度"]

    cross, gain = cp.get("profileChromaCross"), cp.get("profileChromaGain")
    res = {}
    for k, s in out.items():
        if cross and gain and k != SKIP:
            s = E.sony_chroma(s, np.asarray(cross), np.asarray(gain),
                              cp.get("profileLumaPivot", 0.0),
                              cp.get("profileLumaContrast", 1.0),
                              cp.get("profileChromaSaturation", 1.0)).astype(np.float32)
        cc = s @ E.SRGB_TO_PROPHOTO.T if srgb_basis else s
        res[k] = E.srgb_encode(np.clip(cc, 0, 1) @ E.PROPHOTO_TO_SRGB.T).astype(np.float32)
    return res


def main():
    """默认跑三张。**别拿一张片下结论** —— 头一版只测了 DSC02995,得出「色调那一改
    收益 2.9 倍」,换两张之后中位只有 1.5 倍,其中一张几乎没有收益。
    `dump_finals.py` 的开头早就写着「两三张的样本量不足以为一个全局改动背书」。"""
    stems = sys.argv[1:] or ["DSC02995", "DSC02961", "DSC03025"]
    ratios = {}
    for stem in stems:
        z = np.load(TMP / f"final_{stem}.npz")
        step, W, H = (int(v) for v in z["step"])
        eng = z["ZcTaskSIMDMarble_out"][:H // step, :W // step]
        print(f"\n=== {stem}  引擎成品 {eng.shape}")
        rows = {"Edit": stats("Edit", eng.astype(np.float32) * (255.0 / FULL))}
        for name, img in render_two(SRC / f"{stem}.ARW").items():
            a = align(img, eng, eng.shape[:2])[0]
            rows[name] = stats(name, to_grid(a, eng.shape[:2]) * 255.0)
        for name, (my, ma, mb) in rows.items():
            ratios.setdefault(name, []).append((ma + mb) / 2 / max(my, 1e-9))

    print(f"\n{len(stems)} 张的「色差/亮度」中位:")
    edit = float(np.median(ratios["Edit"]))
    for name, rs in ratios.items():
        med = float(np.median(rs))
        tail = "" if name == "Edit" else f"   对 Edit {med / max(edit, 1e-9):5.2f}×"
        print(f"  {name:<32} {med:6.3f}   逐张 "
              + " ".join(f"{r:.3f}" for r in rs) + tail)
    print("\n注:「只作用在亮度」会让暗部欠饱和,「跳过 sony_chroma」会让颜色不对 ——"
          "\n   两个都是**定位用的变体**,不是可交付的实现。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
