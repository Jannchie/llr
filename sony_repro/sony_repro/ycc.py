r"""YCC 段:引擎真正调整饱和度的地方。

管线在 MainGamma 之后转进 YCbCr,而这一对变换**并不互逆** —— 差额就是各创意外观的
饱和度与色相风格:

    RGB2YCC   色差取 R-G / B-G,先按符号做一次交叉耦合,再按符号各乘一个增益
    YCC2RGB   标准 BT.601 逆变换,定点系数 1.4020 / 0.7141 / 0.3441 / 1.7720

八个参数逐外观不同,躺在 DataIFD 里:`0x7842` 是基准,`0x7843`..`0x7846` 是四组光源
增量,按光源权重(1024 = 1.0)插值。**BW 的八个值全是 0** —— 增益为零即 Cb=Cr=0,
黑白外观的去色就发生在这里,不需要任何额外机制。

尚未复刻:SIMDITP 与 AreaComp。目前实测这一段本身会把色度做过头约两成,说明它们
之中还有一步压制。详见 PIPELINE.md §9。
"""
import numpy as np

CHROMA_BASE_TAG = 0x7842
CHROMA_ILLUM_TAGS = (0x7843, 0x7844, 0x7845, 0x7846)

# lv+0x218f6 起的三个 int16,紧跟在 tone LUT 之后。8192 = 1.0,接近 BT.601 但不等同
LUMA_WEIGHTS = (2432, 4864, 896)
LUMA_SHIFT = 8192

CHROMA_LIMIT = 8192   # 引擎在某个标志为 0 时把色差钳在这里
FULL = 16383          # YCC2RGB 的输出量程,14 位

__all__ = ["CHROMA_BASE_TAG", "CHROMA_ILLUM_TAGS", "LUMA_WEIGHTS", "FULL",
           "chroma_params", "unpack_chroma", "rgb_to_ycc", "ycc_to_rgb", "apply_ycc"]


def chroma_params(ifd, weights=(1024, 0, 0, 0)):
    """按光源权重插值出这一外观的八个色度参数。

    权重由引擎依白平衡给出,实测常见情形是 (1024, 0, 0, 0),此时结果就是基准本身。
    """
    base = np.asarray(ifd[CHROMA_BASE_TAG]).astype(np.int64)
    acc = np.zeros(8, dtype=np.int64)
    for tag, w in zip(CHROMA_ILLUM_TAGS, weights, strict=False):
        if w:
            acc += np.asarray(ifd[tag]).astype(np.int64) * w
    return base + (acc >> 10)


def unpack_chroma(p):
    """八个 short -> (交叉耦合 4 个, 增益 4 个),都已化成实数。

    交叉取 bit2 起的 9 位有符号再 /256;增益取 bit3 起的 8 位再 /64,应用时还要 *0.5,
    合起来就是 /128。
    """
    p = np.asarray(p).astype(np.int64)
    return (p[:4] >> 2) / 256.0, ((p[4:] >> 3) & 0xFF) / 128.0


def rgb_to_ycc(rgb, p, luma=LUMA_WEIGHTS, clamp=True):
    """引擎的正向变换。rgb 为 14 位域的浮点,返回 (Y, Cb, Cr)。"""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    y = (r * luma[0] + g * luma[1] + b * luma[2]) // LUMA_SHIFT

    cross, gain = unpack_chroma(p)
    u = r - g   # 红色差
    v = b - g   # 蓝色差
    # 两个交叉项都看对方的符号,而且用的都是尚未修改的 u / v
    v2 = np.where(u >= 0, cross[1], cross[3]) * u + v
    u2 = np.where(v >= 0, cross[0], cross[2]) * v + u

    cr = np.where(u2 >= 0, gain[1], gain[3]) * u2
    cb = np.where(v2 >= 0, gain[0], gain[2]) * v2
    if clamp:
        cb = np.clip(cb, -CHROMA_LIMIT, CHROMA_LIMIT - 1)
        cr = np.clip(cr, -CHROMA_LIMIT, CHROMA_LIMIT - 1)
    return y, cb, cr


def ycc_to_rgb(y, cb, cr):
    """标准 BT.601,定点系数与引擎一致(整数除以 10000)。"""
    r = (y * 10000 + cr * 14020) // 10000
    g = (y * 10000 - cr * 7141 - cb * 3441) // 10000
    b = (y * 10000 + cb * 17720) // 10000
    return np.clip(np.stack([r, g, b], -1), 0, FULL)


def apply_ycc(rgb, p, **kw):
    """走一遍 RGB2YCC -> YCC2RGB。输入输出都是 14 位域。"""
    return ycc_to_rgb(*rgb_to_ycc(rgb, p, **kw))
