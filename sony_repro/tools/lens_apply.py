"""把 llr 的镜头校正补到离线渲染上 —— 不补,逐像素对比全是错位的。

`align_check.py` 量到 llr 的成品与 Edit 的成品在四角错开 ±24 像素以上,而且中心
几乎不动、四角一律指向中心 —— 标准的**径向畸变**。原因是 llr 的镜头校正只在 web
的 shader 里(`rendering/lens.ts` + `passes.ts`),而离线链路 `aniso_stage.render`
走的是 worker 端,压根没有这一步。notes 2.12 早就写过这条,这次又踩了。

于是所有基于成品的读数(|Δ| 0.0486、高光 −0.113、条件散布带宽 0.2~0.3)都是在错开
二十多像素的两张图上量的,不能用。

这里按 `lens.ts` 逐条复刻:

  * 表在**归一化到半对角线**的半径上,16 个结点,knot[i] = (i+0.5)/16;
  * `distortion[i]` 是采样因子:校正后半径 r 的像素,去原图 r·f(r) 处取;
  * `lensFillScale` 预缩放 s,使边界不会采到画幅之外 —— 桶形时短边中点才是约束,
    只锚角点会差 1%;
  * `vignetting[i]` 是**线性光**下的增益,按记录半径给。

⚠️ 暗角增益作用在线性光上,而 render() 交出来的已经是编码过的 sRGB。这里只做几何,
不动亮度 —— 混着做会把两种误差搅在一起。
"""
import numpy as np

LENS_KNOTS = 16
LENS_KNOT_SPAN = 15.2   # 结点 i 在半对角线的 i/15.2 处(lens.ts LENS_KNOT_SPAN,实测值)
LENS_EXTRAP_KNOTS = 1
FILL_SAMPLES = 16
FILL_ITERS = 16


def knot_r(i):
    return i / LENS_KNOT_SPAN


def lens_interp(table, r):
    """`lens.ts` 的 lensInterp:最后一个结点(r=0.987)之外沿最后一段线性外推一个结点距,再钳住。"""
    t = np.clip(np.asarray(r) * LENS_KNOT_SPAN, 0.0, LENS_KNOTS - 1.0 + LENS_EXTRAP_KNOTS)
    i = np.minimum(np.floor(t).astype(np.int32), LENS_KNOTS - 2)
    tab = np.asarray(table, np.float64)
    return tab[i] + (tab[i + 1] - tab[i]) * (t - i)


def _spline_at(knots, values, r):
    if r <= knots[0]:
        return values[0]
    for i in range(1, len(knots)):
        if r <= knots[i]:
            t = (r - knots[i - 1]) / (knots[i] - knots[i - 1])
            return values[i - 1] + (values[i] - values[i - 1]) * t
    return values[-1]


def parse_lens_corr(meta):
    """worker 给的任意结点表,重采样到固定的 16 结点网格上。"""
    if not meta:
        return None
    k = meta.get("knots")
    d = meta.get("distortion")
    v = meta.get("vignetting")
    if not (k and d and v) or not (len(k) == len(d) == len(v)) or len(k) < 2:
        return None
    return {
        "distortion": [_spline_at(k, d, knot_r(i)) for i in range(LENS_KNOTS)],
        "vignetting": [_spline_at(k, v, knot_r(i)) for i in range(LENS_KNOTS)],
    }


def fill_scale(distortion, short_edge):
    """`lensFillScale`:短边中点到角点都试一遍,取最紧的那个。"""
    out = np.inf
    for k in range(FILL_SAMPLES + 1):
        anchor = short_edge + (1.0 - short_edge) * k / FILL_SAMPLES
        s = 1.0
        for _ in range(FILL_ITERS):
            s = 1.0 / float(lens_interp(distortion, s * anchor))
        out = min(out, s)
    return out


def apply_distortion(img, distortion):
    """按 distortion 表重采样。img 是 (h, w, c),返回同尺寸。

    输出像素在校正后的坐标 p(归一化到半对角线),去原图 s·p·f(s·|p|) 处取值。
    """
    h, w = img.shape[:2]
    half = np.hypot(w, h) / 2.0
    short_edge = min(w, h) / np.hypot(w, h)
    s = fill_scale(distortion, short_edge)

    yy = (np.arange(h, dtype=np.float64) - (h - 1) / 2.0)[:, None]
    xx = (np.arange(w, dtype=np.float64) - (w - 1) / 2.0)[None, :]
    r = np.hypot(yy, xx) / half
    f = lens_interp(distortion, s * r)
    k = s * f
    sy = yy * k + (h - 1) / 2.0
    sx = xx * k + (w - 1) / 2.0

    # 双线性采样。越界的钳到边界 —— fill scale 本就是为了让它不该发生。
    y0 = np.clip(np.floor(sy), 0, h - 1).astype(np.int32)
    x0 = np.clip(np.floor(sx), 0, w - 1).astype(np.int32)
    y1 = np.clip(y0 + 1, 0, h - 1)
    x1 = np.clip(x0 + 1, 0, w - 1)
    wy = np.clip(sy - y0, 0.0, 1.0)[..., None]
    wx = np.clip(sx - x0, 0.0, 1.0)[..., None]
    a = img[y0, x0] * (1 - wx) + img[y0, x1] * wx
    b = img[y1, x0] * (1 - wx) + img[y1, x1] * wx
    return (a * (1 - wy) + b * wy).astype(img.dtype), s
