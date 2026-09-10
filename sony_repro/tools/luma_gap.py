"""llr 的亮度细节比 Edit 多出来的那部分,是**噪声**还是**结构**?

起因(measured-chroma-gap.md §2.11):按绝对幅度重读 16 帧时,分母那一列露出一条
独立的线 —— 平坦区里 llr 的亮度细节在若干帧上是 Edit 的 1.6~1.9 倍。而
`log(llr/Edit)` 对 `log(Edit 亮度)` 的相关是 **−0.798**,对 ISO 只有 −0.222:
**Edit 把亮度清得越干净的片子,llr 落后越多**,这不是 ISO 现象。

两种解释,后续动作完全不同:
  * llr 残留了 Edit 已经去掉的**噪声** -> 亮度降噪不足
  * llr 把同样的**结构**放得更大   -> 锐化/Clarity 过强

分法跟 §2.7 判「去掉 vs 缩小」用的是同一招:把 llr 的细带对 Edit 的细带做最小二乘,
拆成「跟 Edit 同相的部分」和「Edit 里没有的部分」:

    d_llr = a · d_edit + r

`a` 是放大倍数(锐化说了算),`σ_r` 是 llr 独有的量(噪声说了算)。
两者都按尺度分开看,因为噪声集中在最细一带而锐化不是。

⚠️ **亚像素错位会伪装成噪声**:错位让 d_llr 与 d_edit 去相关,σ_r 跟着涨,
看上去就像"llr 多了噪声"。align() 只做整像素位移和 rot90,错位一定还在。
所以每帧都同时在**结构区**(方差最高的 25% tile)量一遍相关系数当对照 ——
那里信噪比高,相关本该接近 1;若结构区的相关也低,那读数就是对齐问题,
不是噪声,平坦区的结论也就不能信。这一列是**证伪用的**,不是装饰。
"""
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")
sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")
import e2e_pipeline as E  # noqa: E402
from engine_final_check import align  # noqa: E402
from llr_worker.cli import prepare_linear  # noqa: E402
from llr_worker.sony.chromanr import _box, apply_chroma_nr  # noqa: E402

SRC = Path("/mnt/e/10960725")
TMP = Path("/home/jannchie/llr/tmp")
FULL = 16383
TILE = 32
W601 = np.array([0.299, 0.587, 0.114], np.float32)
NBANDS = 4


def mad(x):
    return float(np.median(np.abs(x - np.median(x))) * 1.4826)


def bands(y, n=NBANDS):
    """拉普拉斯式的分带:半径 1/2/4 的 box 逐级相减,最细的一带在前。"""
    out, prev = [], y
    for s in range(n):
        cur = _box(y, 2 ** s)
        out.append(prev - cur)
        prev = cur
    return out


def tile_masks(y, frac=0.25, valid=None):
    """(平坦 mask, 结构 mask),都按 tile 的标准差分位数切,再摊回像素。

    `valid` 是逐像素的可用掩码 —— 引擎的 dump 偶尔有没拼上的块,那里是 0,
    会在边界上造出一圈假的强边,既能把 tile 误判成"结构",又会污染统计。
    整块含无效像素的 tile 直接弃掉,不做部分采纳。
    """
    h, w = y.shape[0] // TILE * TILE, y.shape[1] // TILE * TILE
    v = y[:h, :w].reshape(h // TILE, TILE, w // TILE, TILE).std(axis=(1, 3))
    ok = np.ones(v.shape, bool)
    if valid is not None:
        ok = valid[:h, :w].reshape(h // TILE, TILE, w // TILE, TILE).all(axis=(1, 3))
    vv = v[ok]
    if vv.size == 0:
        vv = v.ravel()
    lo, hi = np.quantile(vv, frac), np.quantile(vv, 1 - frac)
    out = []
    for m in ((v <= lo) & ok, (v >= hi) & ok):
        full = np.zeros(y.shape, bool)
        full[:h, :w] = np.repeat(np.repeat(m, TILE, 0), TILE, 1)
        out.append(full)
    return out


def decompose(d_edit, d_llr, mask):
    """d_llr = a·d_edit + r,返回 (σ_e, σ_l, a, σ_r, corr)。"""
    e, q = d_edit[mask].astype(np.float64), d_llr[mask].astype(np.float64)
    e, q = e - e.mean(), q - q.mean()
    ee = float(e @ e)
    a = float(e @ q) / ee if ee > 0 else 0.0
    r = q - a * e
    c = float(np.corrcoef(e, q)[0, 1]) if ee > 0 and float(q @ q) > 0 else 0.0
    return mad(e), mad(q), a, mad(r), c


def subsample(full, dy, dx, shape):
    """按 (dy,dx) 相位在全分辨率上抽样到 shape,step 由尺寸比推出。"""
    sy = full.shape[0] // shape[0]
    sx = full.shape[1] // shape[1]
    out = full[dy::sy, dx::sx]
    return out[:shape[0], :shape[1]]


def align_full(full, eng_y, shape, span=12):
    """**在抽样之前**找整像素偏移。

    引擎的 dump 是 step=4 抽样的,而它的画面还带 2px 边 —— 在抽样网格上就是
    半个单位。先抽样再比较的话,这半个单位没有任何整数位移能修,最细的一带
    直接被打散成不相关的噪声(实测结构区 corr 0.19 / −0.00)。

    在全分辨率上 2px 是整数,搜得到。用最细一带做判据,因为它对偏移最敏感:
    低频对半个像素几乎无动于衷,拿它对齐会停在错的地方。
    """
    e = bands(eng_y, 1)[0]
    e = e - e.mean()
    en = float(np.linalg.norm(e))
    best = None
    for dy in range(span):
        for dx in range(span):
            g = subsample(full, dy, dx, shape)
            if g.shape != shape:
                continue
            d = bands(g, 1)[0]
            d = d - d.mean()
            n = float(np.linalg.norm(d)) * en
            c = float((d * e).sum() / n) if n > 0 else 0.0
            if best is None or c > best[0]:
                best = (c, dy, dx)
    return best


def phase_shift(a, b, cy, cx, patch=512):
    """相位相关求 a 相对 b 的位移,峰值用抛物线插到亚像素。

    返回 (dy, dx, 峰值信噪比)。约定:`a(p) ≈ b(p − d)`,所以把 a 搬到 b 的几何
    上要取 `a[y+dy, x+dx]`。
    """
    r = patch // 2
    A = a[cy - r:cy + r, cx - r:cx + r].astype(np.float64)
    B = b[cy - r:cy + r, cx - r:cx + r].astype(np.float64)
    if A.shape != (patch, patch) or B.shape != (patch, patch):
        return 0.0, 0.0, 0.0
    win = np.outer(np.hanning(patch), np.hanning(patch))
    A = (A - A.mean()) * win
    B = (B - B.mean()) * win
    R = np.fft.rfft2(A) * np.conj(np.fft.rfft2(B))
    R /= np.maximum(np.abs(R), 1e-12)
    c = np.fft.irfft2(R, s=(patch, patch))
    k = np.unravel_index(np.argmax(c), c.shape)
    q = float(c.max() / max(c.std(), 1e-12))

    def sub(axis_vals):
        """三点抛物线顶点,越界就退回整数。"""
        y0, y1, y2 = axis_vals
        d = y0 - 2 * y1 + y2
        return 0.0 if abs(d) < 1e-12 else float(np.clip(0.5 * (y0 - y2) / d, -1, 1))

    ky, kx = int(k[0]), int(k[1])
    fy = sub([c[(ky - 1) % patch, kx], c[ky, kx], c[(ky + 1) % patch, kx]])
    fx = sub([c[ky, (kx - 1) % patch], c[ky, kx], c[ky, (kx + 1) % patch]])
    dy = (ky if ky < patch // 2 else ky - patch) + fy
    dx = (kx if kx < patch // 2 else kx - patch) + fx
    return dy, dx, q


def shift_field(a, b, ny=9, nx=13, patch=512, margin=40):
    """在 ny×nx 网格上测位移场。返回 (ys, xs, dyg, dxg, 信噪比中位)。

    不拟合径向模型 —— 场本来就光滑(实测切向分量 RMS 只有 0.62px),直接插值
    能顺带吸收畸变之外的任何几何差异,也不用赌 Sony 那套定点数的解释是否正确。
    """
    h, w = b.shape
    r = patch // 2
    ys = np.linspace(r + margin, h - r - margin, ny).astype(int)
    xs = np.linspace(r + margin, w - r - margin, nx).astype(int)
    dyg = np.zeros((ny, nx))
    dxg = np.zeros((ny, nx))
    qs = np.zeros((ny, nx))
    for i, cy in enumerate(ys):
        for j, cx in enumerate(xs):
            dyg[i, j], dxg[i, j], qs[i, j] = phase_shift(a, b, int(cy), int(cx), patch)
    # 低信噪比的点(天空、纯色块)会给出乱七八糟的位移,用邻域中位数顶掉。
    bad = qs < 20
    if bad.any() and (~bad).any():
        dyg[bad] = np.median(dyg[~bad])
        dxg[bad] = np.median(dxg[~bad])
    return ys, xs, dyg, dxg, float(np.median(qs))


def warp_by_field(a, ys, xs, dyg, dxg):
    """把位移场双线性插值到全图,再双线性重采样 a,得到与 b 同几何的图。"""
    h, w = a.shape
    gy = np.interp(np.arange(h), ys, np.arange(len(ys)))
    gx = np.interp(np.arange(w), xs, np.arange(len(xs)))
    y0 = np.clip(gy.astype(int), 0, len(ys) - 1)
    x0 = np.clip(gx.astype(int), 0, len(xs) - 1)
    y1 = np.clip(y0 + 1, 0, len(ys) - 1)
    x1 = np.clip(x0 + 1, 0, len(xs) - 1)
    ty = (gy - y0)[:, None]
    tx = (gx - x0)[None, :]

    def spread(g):
        top = g[np.ix_(y0, x0)] * (1 - tx) + g[np.ix_(y0, x1)] * tx
        bot = g[np.ix_(y1, x0)] * (1 - tx) + g[np.ix_(y1, x1)] * tx
        return top * (1 - ty) + bot * ty

    yy = np.arange(h)[:, None] + spread(dyg)
    xx = np.arange(w)[None, :] + spread(dxg)
    iy = np.clip(np.floor(yy), 0, h - 2).astype(np.int32)
    ix = np.clip(np.floor(xx), 0, w - 2).astype(np.int32)
    fy = (yy - iy).astype(np.float32)
    fx = (xx - ix).astype(np.float32)
    top = a[iy, ix] * (1 - fx) + a[iy, ix + 1] * fx
    bot = a[iy + 1, ix] * (1 - fx) + a[iy + 1, ix + 1] * fx
    return (top * (1 - fy) + bot * fy).astype(np.float32)


def align_shift(a, b, span=8, patch=1024):
    """同尺寸的两幅图,在中心 patch 上按最细带找整数位移。返回 (corr, dy, dx)。

    只在中心一小块上搜:全分辨率下 17x17 次全图 box filter 要跑几分钟,而位移
    是全局的,一块就够定。判据仍用最细带 —— 低频对一两个像素的错位没有反应。
    """
    h, w = b.shape
    cy, cx = h // 2, w // 2
    r = patch // 2
    e = bands(b[cy - r:cy + r, cx - r:cx + r], 1)[0]
    e = e - e.mean()
    en = float(np.linalg.norm(e))
    best = None
    for dy in range(-span, span + 1):
        for dx in range(-span, span + 1):
            g = a[cy - r + dy:cy + r + dy, cx - r + dx:cx + r + dx]
            if g.shape != e.shape:
                continue
            d = bands(g, 1)[0]
            d = d - d.mean()
            n = float(np.linalg.norm(d)) * en
            c = float((d * e).sum() / n) if n > 0 else 0.0
            if best is None or c > best[0]:
                best = (c, dy, dx)
    return best


def render_llr(path, denoise_model):
    """上线配置:逐通道色调 + sony_chroma + 色度降噪(默认 amount)。

    `denoise_model=None` 就是**完全不在马赛克域降噪**,用来判断成品里那份
    「Edit 没有的独立成分」是不是 llr 自己的降噪器留下的 —— §2.6 量到 llr 的
    马赛克比 Edit 干净一个数量级(0.296 对 1.38),而 §2.13 又量到成品在 8px 上
    多出与 Edit 无关的成分,8px 不是传感器噪声的尺度,是降噪斑块的尺度。
    """
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
    return apply_chroma_nr(out, subsample=8)


def iso_of(stem):
    try:
        r = subprocess.run(["exiftool", "-ISO", "-T", str(SRC / f"{stem}.ARW")],
                           capture_output=True, text=True, timeout=30)
        return int(r.stdout.strip())
    except Exception:
        return 0


def load_pair(stem, denoise_model):
    """(Edit 亮度, llr 亮度, 说明) —— 有全分辨率 dump 就用它,否则退回 step=4。

    step=4 的那一支保留只为了跟旧结果对照:它的最细带过半是混叠(§2.12),
    别拿它下"噪声还是结构"的结论。
    """
    full = TMP / f"full_{stem}.npz"
    ours = render_llr(SRC / f"{stem}.ARW", denoise_model)
    if full.exists():
        z = np.load(full)
        step, W, H = (int(v) for v in z["step"])
        eng = z["ZcTaskSIMDMarble_out"][:H, :W]
        ed = eng.astype(np.float32) * (255.0 / FULL)
        y_e = ed @ W601
        rot = align(ours, eng, eng.shape[:2])[0] * 255.0
        y_l_full = rot @ W601
        # ⚠️ 必须先把几何对上。Edit 应用了 ARW 里的镜头畸变校正,llr 的离线复现
        # 路径没有(web 端有 lens.ts,这条链路绕过了它) —— 实测位移场纯径向、
        # 中心 ~0、四角 50+px,切向 RMS 只有 0.62px。不补这一步,细带的相关会被
        # 打到 0,看着像"llr 全是噪声"。见 §2.12。
        ys, xs, dyg, dxg, q = shift_field(y_l_full, y_e)
        # **两边各走一半**,在中间几何上会合。双线性重采样自带低通:只 warp llr
        # 的话,它的细带会被插值削掉一截,而那正是要测的量 —— 头一版就是这么做的,
        # σ_llr 从 0.721 掉到 0.458,看着像"几何一修差距就没了",其实一半是插值
        # 的功劳。各走一半,两侧受同一个算子,损失才对消。
        raw_e = y_e
        # dump 偶有没拼上的块(那里全 0),跟着 Edit 一起 warp,再当掩码用。
        ok = warp_by_field((eng.sum(axis=2) > 0).astype(np.float32),
                           ys, xs, -dyg * 0.5, -dxg * 0.5) > 0.999
        y_e = warp_by_field(y_e, ys, xs, -dyg * 0.5, -dxg * 0.5)
        y_l_full = warp_by_field(y_l_full, ys, xs, dyg * 0.5, dxg * 0.5)
        h, w = y_e.shape
        m = 64  # warp 后边界不可靠,裁掉
        cost = mad(bands(y_e, 1)[0]) / max(mad(bands(raw_e, 1)[0]), 1e-9)
        sl = (slice(m, h - m), slice(m, w - m))
        y_e, y_l, ok = y_e[sl], y_l_full[sl], ok[sl]
        rms = float(np.sqrt((dyg ** 2 + dxg ** 2).mean()))
        return y_e, y_l, ok, (f"全分辨率 step=1  几何各校正一半"
                              f"(RMS {rms:.1f}px, 峰 {np.hypot(dyg, dxg).max():.1f}px,"
                              f" 信噪比中位 {q:.0f}; 重采样让 Edit 的 band0 变成"
                              f" {cost:.2f}×,两侧同代价; 可用 {ok.mean():.1%})")

    z = np.load(TMP / f"final_{stem}.npz")
    step, W, H = (int(v) for v in z["step"])
    eng = z["ZcTaskSIMDMarble_out"][:H // step, :W // step]
    ed = eng.astype(np.float32) * (255.0 / FULL)
    y_e = ed @ W601
    rot = align(ours, eng, eng.shape[:2])[0]
    c, dy, dx = align_full(rot @ W601 * 255.0, y_e, y_e.shape)
    y_l = subsample(rot * 255.0, dy, dx, y_e.shape) @ W601
    return (y_e, y_l, eng.sum(axis=2) > 0,
            f"⚠️ step=4 裸抽样(最细带是混叠)  相位 ({dy},{dx})  corr {c:.3f}")


def main():
    args = sys.argv[1:]
    denoise_model = None if "--no-denoise" in args else "wavelet"
    stems = [a for a in args if a != "--no-denoise"] or ["DSC02995"]
    print(f"llr 马赛克域降噪: {denoise_model or '关'}")
    for stem in stems:
        y_e, y_l, ok, how = load_pair(stem, denoise_model)
        flat, edge = tile_masks(y_e, valid=ok)
        be, bl = bands(y_e), bands(y_l)

        print(f"\n=== {stem}  ISO {iso_of(stem)}   平坦 {flat.sum() / flat.size:.0%}"
              f"   {how}")
        # 先确认画面本身对上了。低通对错位和混叠都不敏感,它要是也低,那就不是
        # "细节多了"的问题,是两张图根本没对齐,后面每一行都不用看。
        lp_e, lp_l = _box(y_e, 8), _box(y_l, 8)
        c_lp = float(np.corrcoef(lp_e.ravel(), lp_l.ravel())[0, 1])
        print(f"  低通(半径8)相关 {c_lp:.4f}   均值 Edit {y_e.mean():.2f} / "
              f"llr {y_l.mean():.2f}"
              + ("   ✅ 画面对上了" if c_lp > 0.98 else "   ❌ 画面没对上"))
        print(f"  {'带':<6} {'σ_Edit':>8} {'σ_llr':>8} {'比':>6} │"
              f" {'放大 a':>7} {'σ_r 独有':>9} {'独有/超出':>9} {'corr':>6} │"
              f" {'结构区 corr':>11}")
        for i, (de, dl) in enumerate(zip(be, bl, strict=True)):
            se, sl, k, sr, c = decompose(de, dl, flat)
            ce = decompose(de, dl, edge)[4]
            # llr 比 Edit 多出来的量,有多少是「Edit 里没有的」而不是「放大的」。
            excess = max(sl ** 2 - se ** 2, 0.0)
            share = sr ** 2 / excess if excess > 1e-12 else float("nan")
            print(f"  band{i} {2 ** i:>2}px {se:8.3f} {sl:8.3f} {sl / max(se, 1e-9):6.2f} │"
                  f" {k:7.2f} {sr:9.3f} {share:9.2f} {c:6.2f} │ {ce:11.2f}")
            # 机器可读,供 luma_gap_agg.py 汇总 —— 分批跑时逐行追加进文件,
            # 中途被杀也只丢当前一帧。
            print(f"SUMMARY {stem} {iso_of(stem)} {i} {se:.4f} {sl:.4f} {k:.4f}"
                  f" {sr:.4f} {c:.4f} {ce:.4f}")
    print("\n  放大 a>1 = llr 把同样的结构放得更大(锐化);σ_r 大 = llr 有 Edit 里"
          "没有的东西(噪声)。\n  「独有/超出」接近 1 说明超出量几乎全是独有成分。"
          "\n  ⚠️ 结构区 corr 若不接近 1,说明两张图没对齐,平坦区的分解不可信。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
