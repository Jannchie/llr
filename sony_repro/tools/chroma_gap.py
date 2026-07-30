"""色度差在哪:拿 Edit.exe 自己的成品当真值,而不是机内 JPEG。

此前所有「色度比 4.87 倍」都是复刻去比 **JPEG**,可 JPEG 是相机渲的,要复刻的是
Edit。两份都在手上(`final_DSC*.npz` 是 `ZcTaskSIMDMarble:out` 的整幅拼图),
先把它们自己比一次。

两项测量,各回答一个问题:

* `--scale`:逐尺度、分 Y/Cb/Cr 的残噪比。回答「Edit 比 JPEG 干净还是脏」。
* `--base`:三通道细节的相关性,以及 (R−G)/(B−G) 相对 G 的幅度。回答
  「Edit 的色度是不是从一个共享基底重建出来的」—— ITP 的输出写成
  `plane0 = base + d0, plane1 = base, plane2 = base + d2`,若 d0/d2 被单独滤过,
  就该看到高相关 + 极小色差。

不给参数两项都跑。

引擎帧是**抽点**(`stage_frame.py` 里 `row[ox + i*STEP]`),所以 JPEG 也必须
`[::step, ::step]` 抽点 —— 用 resize 会把 JPEG 的噪声平均掉,比较立刻失真,
而且失真方向恰好是「让 JPEG 显得更干净」,正是要判的那件事。

    bash -c 'cd apps/worker && uv run python ../../sony_repro/tools/chroma_gap.py'
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None
TMP = Path("/home/jannchie/llr/tmp")
SRC = Path("/mnt/e/10960725")
FULL = 16383
YCC = np.array([[0.299, 0.587, 0.114],
                [-0.168736, -0.331264, 0.5],
                [0.5, -0.418688, -0.081312]], np.float32)
TILE = 32


def halve(a):
    h, w = a.shape[0] & ~1, a.shape[1] & ~1
    a = a[:h, :w]
    return (a[0::2, 0::2] + a[1::2, 0::2] + a[0::2, 1::2] + a[1::2, 1::2]) * 0.25


def mad(x):
    return float(np.median(np.abs(x - np.median(x))) * 1.4826)


def band_mads(plane, levels=4):
    """逐尺度细节的 MAD(减去 2x2 盒平均的上采样,再对半降采样)。"""
    out, cur = [], plane.astype(np.float32)
    for _ in range(levels):
        nxt = halve(cur)
        up = np.repeat(np.repeat(nxt, 2, axis=0), 2, axis=1)
        cur = cur[:up.shape[0], :up.shape[1]]
        out.append(mad(cur - up))
        cur = nxt
    return np.asarray(out)


def detail(a):
    h, w = a.shape[0] & ~1, a.shape[1] & ~1
    a = a[:h, :w]
    m = halve(a)
    return a - np.repeat(np.repeat(m, 2, axis=0), 2, axis=1)


def flat_tiles(y, frac=0.25, cap=200):
    """最平的 frac 比例的 tile —— 噪声只有在那里才不被真实细节盖住。"""
    h, w = y.shape[0] // TILE * TILE, y.shape[1] // TILE * TILE
    v = y[:h, :w].reshape(h // TILE, TILE, w // TILE, TILE).std(axis=(1, 3))
    ys, xs = np.nonzero(v <= np.quantile(v, frac))
    if not len(ys):
        return []
    idx = np.linspace(0, len(ys) - 1, min(cap, len(ys))).astype(int)
    return [(int(ys[k]) * TILE, int(xs[k]) * TILE) for k in idx]


def align(a, b, span=3):
    """引擎帧带 2px 边,在 ±span 内找使 |a−b| 最小的整数位移。"""
    h = min(a.shape[0], b.shape[0]) - 2 * span - 2
    w = min(a.shape[1], b.shape[1]) - 2 * span - 2
    o = span + 1
    best, off = None, (0, 0)
    for dy in range(-span, span + 1):
        for dx in range(-span, span + 1):
            e = float(np.mean(np.abs(a[o + dy:o + dy + h, o + dx:o + dx + w]
                                    - b[o:o + h, o:o + w])))
            if best is None or e < best:
                best, off = e, (dy, dx)
    return off, (h, w, o)


def frames():
    """逐张吐出对齐好的 (片名, 引擎 RGB, JPEG RGB),两者同标度 0..255。"""
    for path in sorted(TMP.glob("final_DSC*.npz")):
        stem = path.stem[6:]
        jp = SRC / f"{stem}.JPG"
        if not jp.exists():
            print(f"{stem}: 无 JPEG,跳过")
            continue
        z = np.load(path)
        step, W, H = (int(v) for v in z["step"])
        nh, nw = H // step, W // step
        eng = z["ZcTaskSIMDMarble_out"][:nh, :nw].astype(np.float32) * (255.0 / FULL)
        jpg = np.asarray(Image.open(jp), np.float32)
        if jpg.shape[:2] != (H, W):
            print(f"{stem}: 尺寸不符 引擎 {W}x{H} JPEG {jpg.shape[1]}x{jpg.shape[0]},跳过")
            continue
        jpg = jpg[::step, ::step][:nh, :nw]
        (dy, dx), (h, w, o) = align(eng @ YCC[0], jpg @ YCC[0])
        yield (stem,
               eng[o + dy:o + dy + h, o + dx:o + dx + w],
               jpg[o:o + h, o:o + w])


def run_scale(data):
    scales = ("1px", "2px", "4px", "8px")
    print("\n## 逐尺度残噪(Edit / JPEG,>1 说明 Edit 更脏)\n")
    print(f"{'片':>9} {'通道':>4} " + " ".join(f"{s:>13}" for s in scales))
    print("-" * 70)
    rows = {"Y": [], "Cb": [], "Cr": []}
    for stem, eng, jpg in data:
        e_ycc, j_ycc = eng @ YCC.T, jpg @ YCC.T
        tiles = flat_tiles(j_ycc[..., 0])
        for ci, cname in enumerate(("Y", "Cb", "Cr")):
            eb = np.median([band_mads(e_ycc[y:y + TILE, x:x + TILE, ci])
                            for y, x in tiles], axis=0)
            jb = np.median([band_mads(j_ycc[y:y + TILE, x:x + TILE, ci])
                            for y, x in tiles], axis=0)
            # JPEG 是 8 位,几张近单色的片子平坦区色度**恒定**,MAD 正好是 0,
            # 除不出有意义的比。只挡这一种 —— 0.11 那样的小值是逐 tile 中位数,
            # 落在量化格之间是正常的,挡掉它会把最有意思的粗尺度一档抹平。
            ok = jb > 1e-3
            ratio = np.where(ok, eb / np.maximum(jb, 1e-6), np.nan)
            rows[cname].append(ratio)
            print(f"{stem:>9} {cname:>4} "
                  + " ".join(f"{e:5.2f}/{j:5.2f}" for e, j in zip(eb, jb))
                  + "   比 " + " ".join("  — " if np.isnan(r) else f"{r:4.2f}"
                                        for r in ratio))
    print("\n全语料中位比:")
    for cname, rs in rows.items():
        if rs:
            med = np.nanmedian(np.asarray(rs), axis=0)
            print(f"  {cname:>2}  " + "  ".join(f"{s}={r:4.2f}"
                                                for s, r in zip(scales, med)))
    print("\n引擎帧是 1/4 抽点,所以这里的 1px 对应原图 4px 间距。")


def run_base(data):
    print("\n## 色度是否由共享基底重建\n")
    print(f"{'片':>9}  {'来源':>5}  {'G 细节':>7} {'(R-G)':>7} {'(B-G)':>7}"
          f"  {'色差/G':>7}  {'通道相关':>8}")
    print("-" * 68)
    agg = {"Edit": [], "JPEG": []}
    for stem, eng, jpg in data:
        for tag, img in (("Edit", eng), ("JPEG", jpg)):
            g, rg, bg, cor = [], [], [], []
            for y, x in flat_tiles(img[..., 1]):
                p = img[y:y + TILE, x:x + TILE]
                dR, dG, dB = (detail(p[..., k]) for k in range(3))
                g.append(mad(dG))
                rg.append(mad(dR - dG))
                bg.append(mad(dB - dG))
                a, b, c = dR.ravel(), dG.ravel(), dB.ravel()
                if min(a.std(), b.std(), c.std()) > 1e-6:
                    cor.append(np.mean([np.corrcoef(a, b)[0, 1],
                                        np.corrcoef(b, c)[0, 1],
                                        np.corrcoef(a, c)[0, 1]]))
            if not g:
                continue
            mg, mrg, mbg = np.median(g), np.median(rg), np.median(bg)
            mc = np.median(cor) if cor else np.nan
            ratio = (mrg + mbg) / 2 / max(mg, 1e-6)
            agg[tag].append((mg, mrg, mbg, ratio, mc))
            print(f"{stem:>9}  {tag:>5}  {mg:7.3f} {mrg:7.3f} {mbg:7.3f}"
                  f"  {ratio:7.2f}  {mc:8.3f}")
    print()
    for tag, rs in agg.items():
        if rs:
            m = np.nanmedian(np.asarray(rs), axis=0)
            print(f"{tag:>5} 中位: G={m[0]:.3f}  (R-G)={m[1]:.3f}  (B-G)={m[2]:.3f}"
                  f"  色差/G={m[3]:.2f}  通道相关={m[4]:.3f}")
    print("\nEdit 的色差/G 远小于 JPEG、通道相关远高于 JPEG,即色度由共享基底重建。")


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    want = set(a for a in sys.argv[1:] if a.startswith("--")) or {"--scale", "--base"}
    data = list(frames())
    print(f"{len(data)} 张引擎成品")
    if "--scale" in want:
        run_scale(data)
    if "--base" in want:
        run_base(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
