"""横纹竖纹是**哪一级**造成的:逐级关掉 llr 的算子,看轴向能量什么时候塌下去。

起因是用户在 200% 下看到的东西:"llr 的噪点甚至有种纹理感,像是爬满了横纹和
竖纹"。`aniso.py` 已经在用户自己的两张成品图上确认了这件事 —— llr 水平 1.19、
垂直 1.20 一起抬起来,直出是 0.81/0.77。**水平垂直同时抬起**是可分离变换
(先行后列)的签名:它的 LH/HL 子带正好就是横纹和竖纹。

llr 的马赛克域降噪用的就是可分离小波,所以它是头号嫌疑。但"嫌疑"要能证伪:
本脚本把同一张 ARW 渲多遍,每遍只改一件事,量同一批 tile 的轴向/对角比。
若关掉小波后轴向比塌到 1.0 附近,来源就锁定了;若不塌,小波是清白的,得往
去马赛克和锐化上找 —— 那两级同样是可分离的。

⚠️ 必须在**线性域之后、同一批 tile 位置**上量,几个变体的画面内容才一样。
tile 位置一律取自第一个变体,不允许各挑各的 —— 各挑各的会挑到不同的平坦区,
那时候比的是画面不是算子。

用法::

    python aniso_stage.py DSC03036 [DSC02995 ...]
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")
sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")

from aniso import TILE, flat_tiles, spectrum  # noqa: E402
from aniso_freq import RINGS, ring_ratio  # noqa: E402
import e2e_pipeline as E  # noqa: E402
from llr_worker.cli import prepare_linear  # noqa: E402
from llr_worker.sony.chromanr import apply_chroma_nr  # noqa: E402

#: 语料放在三处 —— 16 帧的批量语料在 10960725,用户自己挑出来的那张在 temp_photo,
#: 仓库自带的样片(fl_test 等)在 samples。少了最后一个,凡是用 find_arw 的工具碰到
#: fl_test 都会拿到 None,再被 rawpy 报成 I/O error,看着像文件坏了。
SRC_DIRS = [Path("/mnt/e/10960725"), Path("/mnt/e/temp_photo"),
            Path("/home/jannchie/llr/samples")]
W601 = np.array([0.299, 0.587, 0.114], np.float32)
NTILE = 16


def find_arw(stem):
    for d in SRC_DIRS:
        p = d / f"{stem}.ARW"
        if p.exists():
            return p
    return None


def render(path, denoise_model="wavelet", chroma=True, sharp=0.0):
    """和 luma_gap.render_llr 同一条链路,只把降噪开关抽出来。"""
    r = prepare_linear(path, {"profileId": "sony"}, E.ROOT, None, False,
                       half_size=False, denoise_model=denoise_model)
    cp = r.color_profile
    c = r.linear.astype(np.float32)
    pts = cp.get("profileToneCurve")
    if not pts:
        raise SystemExit("这张片没有 profileToneCurve")
    p = np.asarray(pts, np.float64)
    grid = np.linspace(0, 1, 2048)
    lut = np.interp(grid, p[:, 0], p[:, 1])
    srgb_basis = cp["kind"] == "sony"
    s = np.clip(c @ E.PROPHOTO_TO_SRGB.T if srgb_basis else c, 0, 1)
    s = np.interp(s, grid, lut).astype(np.float32)
    cross, gain = cp.get("profileChromaCross"), cp.get("profileChromaGain")
    if cross and gain:
        s = E.sony_chroma(s, np.asarray(cross), np.asarray(gain),
                          cp.get("profileLumaPivot", 0.0),
                          cp.get("profileLumaContrast", 1.0),
                          cp.get("profileChromaSaturation", 1.0)).astype(np.float32)
    cc = s @ E.SRGB_TO_PROPHOTO.T if srgb_basis else s
    out = E.srgb_encode(np.clip(cc, 0, 1) @ E.PROPHOTO_TO_SRGB.T).astype(np.float32)
    # 顺序和引擎一致:Sharpness -> (Spica) -> Marble 的色度清理。
    #
    # ⚠️ 括号里的 Spica **没有实现**,而 web 端有(passes.ts:899 起)。所以这条离线
    # 链路比用户实际看到的画面少一个阶段,拿它和 Edit 的导出比绝对差距会**低估
    # llr**:Spica 读邻域、按细节强度分类,平坦区几乎不动、结构区作用最大,而实测
    # 剩余差距正好集中在结构区(notes 2.19.x,0.00972 → 0.02416)。
    # 用这个函数比差距之前先想清楚:少的这一阶段会不会正好落在你要测的那个量上。
    if sharp > 0.0:
        out = sharpen(out, sharp)
    return apply_chroma_nr(out, subsample=8) if chroma else out


#: web 端 `SONY_POST_SHADER` 的三个权重与死区,原样搬过来(passes.ts:815..823)。
SHARPEN_CENTER = 25.6
SHARPEN_BLUR_W = 10.24
SHARPEN_NEAR_W = 3.84
SHARPEN_BINOMIAL = np.array([1, 6, 15, 20, 15, 6, 1], np.float32)
SHARPEN_DEADZONE = (40.96 * 25) / 16383.0
REC709_Y = np.array([0.2126, 0.7152, 0.0722], np.float32)


def sharpen(rgb, amount):
    """`SONY_POST_SHADER` 的锐化那一半,逐点复现。

    ⚠️ 这里刻意**保留死区**。死区是个硬阈值,是整条链路上少数几个非线性之一,
    而线性算子的频率响应可以纸上算、非线性的不能 —— 把它去掉就等于把要测的
    东西测没了。核本身的线性响应在对角奈奎斯特处比轴向大 15.36/25.6,也就是
    说**线性部分应当压低**轴向/对角比;若实测反而抬高,那就是死区干的。

    数值上跟 shader 的差别只有:shader 在 8 位帧上算,这里在 float 上算。
    """
    b = SHARPEN_BINOMIAL / SHARPEN_BINOMIAL.sum()
    # 可分离的二项模糊,用 roll 边界(平坦 tile 都在画面内部,边界怎么处理不影响)。
    blur = np.zeros_like(rgb)
    for k, w in enumerate(b):
        blur += w * np.roll(rgb, k - 3, axis=0)
    tmp, blur = blur, np.zeros_like(rgb)
    for k, w in enumerate(b):
        blur += w * np.roll(tmp, k - 3, axis=1)
    near = np.zeros_like(rgb)
    for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        near += np.roll(np.roll(rgb, dy, axis=0), dx, axis=1)
    hp = ((SHARPEN_CENTER * rgb - SHARPEN_BLUR_W * blur - SHARPEN_NEAR_W * near)
          @ REC709_Y)
    delta = np.where(np.abs(hp) < SHARPEN_DEADZONE, 0.0, amount * hp)
    return np.clip(rgb + delta[..., None], 0.0, 1.0)


VARIANTS = [
    ("wavelet(llr 自己的)", dict(denoise_model="wavelet", chroma=True)),
    ("sony(Edit 的 RawNRSIMD)", dict(denoise_model="sony", chroma=True)),
    ("  sony + 锐化 1.0", dict(denoise_model="sony", chroma=True, sharp=1.0)),
    ("不降噪", dict(denoise_model=None, chroma=True)),
]


def measure(rgb, tiles):
    """**逐频带**报轴向/对角比。

    ⚠️ 不要退回 `aniso.directional` 的单一读数。它在 r∈[0.15,0.85] 上取中位数,
    而格纹坐落在 r>0.75(周期 2.0-2.7px)那一带 —— 那一带在总数里被稀释到几乎
    看不见,头一版就是这么误判的:单一读数说 wavelet 让画面**更**各向同性
    (0.98 对 1.10),按频带拆开才看得到它在最细一带上干了什么。
    """
    y = (rgb * 255.0) @ W601
    specs = [spectrum(y[cy:cy + TILE, cx:cx + TILE]) for cy, cx in tiles]
    return [np.median([ring_ratio(s, lo, hi) for s in specs], axis=0)
            for lo, hi in RINGS]


def main():
    stems = sys.argv[1:] or ["DSC03036"]
    hdr = "  ".join(f"{f'{2/hi:.1f}-{2/lo:.1f}px':>11}" for lo, hi in RINGS)
    for stem in stems:
        path = find_arw(stem)
        if path is None:
            print(f"跳过 {stem}:在 {[str(d) for d in SRC_DIRS]} 都没找到")
            continue
        print(f"\n=== {stem}")
        print(f"  {'变体':<28} {hdr}")
        tiles = None
        for name, kw in VARIANTS:
            rgb = render(path, **kw)
            if tiles is None:
                tiles = flat_tiles((rgb * 255.0) @ W601, n=NTILE)
            row = measure(rgb, tiles)
            cells = "  ".join(f"{r[0]:11.2f}" for r in row)
            print(f"  {name:<28} {cells}")
            fine = row[-1]
            print(f"  {'':<28} {'':>11}  {'':>11}  {'':>11}  {'':>11}  "
                  f"竖{fine[1]:.2f}/横{fine[2]:.2f}")
    print(f"\n  同一批 {NTILE} 块最平坦的 {TILE}x{TILE},位置取自第一个变体。")
    print("  1.00 = 各向同性。最细一带(2.0-2.7px)是要看的那一列 —— 半分辨率相位"
          "平面里的 1px\n  就是全分辨率的 2px,可分离小波的 LH/HL 子带正落在这里。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
