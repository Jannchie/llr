"""Dynamic Range Optimizer (DRO) — structural reproduction of Sony's DRO.

免责声明 / DISCLAIMER
====================
这是对 Sony 相机内 **Dynamic Range Optimizer (DRO)** 的**结构性近似重建**,
**并非 bit-exact 复刻**。

⚠️ **归属已更正:DRO 是 ``ZcTaskVatr``,不是本文档原先写的 ``ZcTaskAreaComp``。**

决定性证据是执行普查(``tools/stage_census.py``)对照两张图:65 张素材里只有
2 张 ``DynamicRangeOptimizer=Auto``,**恰好只有这两张跑 ``ZcTaskVatr``(36 次),
其余 63 张一次不跑**,而两份普查再无任何其它差异。``ZcTaskAreaComp`` 本身从未
执行(跑的是 ``ZcTaskAreaCompSIMD``),且它实测只在 Cb-Cr 平面上做楔形**加性**
修正,整幅色度只改 ×0.9999 —— 那是记忆色修正,不是动态范围优化。
(PIPELINE.md 里"Vatr 不在链上"那句同样是错的:那是只在 DRO=Off 的图上测的。)

实测 ``ZcTaskVatr``(``tools/vatr_probe.py`` / ``vatr_scale.py``):
排在 DemosaicRough 之后、GeometricTransformCorrection 之前,吃**线性 RGB**;
三通道增益几乎相同(1.030 / 1.029 / 1.028),是保色的;暗部抬得多
(in=128 → ×1.048,in=1024 → ×1.020)。

**主体是逐像素的一条曲线,不是空间算法。** 按输入值分箱的中位增益解释掉
76% 的增益方差;残差与邻域均值的相关随核尺度单调上升(132px 处 −0.0001,
2052px 处 −0.34),只解释残差方差的 12%,即总方差约 3%。
下面这份结构性重建把空间部分当成了核心,这一点与实测不符。

本模块保留的是一个**结构性近似**,处理结构为:

    局部亮度估计 (local luminance estimate)
      → 自适应增益 (adaptive per-pixel gain)
      → 保色应用 (color-preserving application)

各系数、曲线形状、模糊核尺度均为结构上合理的近似,而非从二进制恢复的数值。
⚠️ 请勿声称本实现为 bit-exact。要做对,应当照 ``ZcTaskVatr``(RVA 0x366920)
重建,并且还要解决「DRO=Auto 时档位由引擎按画面自行决定」这一步 ——
相机只写下 ``Auto``,没有写下它选了哪一档。

实现说明
========
- 纯 ``numpy`` 实现,不依赖 ``scipy``。
- 高斯低通用「多次 box blur 逼近高斯」实现(可分离,O(N) per pass)。
- 输入 / 输出:HxWx3 float,线性 RGB,值域 [0, white](默认 white=1.0)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

# ITU-R BT.709 luma 权重(线性 RGB → 亮度)
DEFAULT_LUMA = (0.2126, 0.7152, 0.0722)

# Lv1..Lv5 强度映射表。每档给出 (low, high, shadow_detail)。
# low  = 暗部提亮强度;high = 亮部保护强度;shadow_detail = 暗部提亮锐度。
# 数值随档位单调递增,保证 Lv1..Lv5 暗区提亮量单调不减。
_LEVEL_TABLE = {
    "lv1": (0.15, 0.10, 0.30),
    "lv2": (0.30, 0.18, 0.45),
    "lv3": (0.45, 0.26, 0.60),
    "lv4": (0.62, 0.34, 0.75),
    "lv5": (0.80, 0.42, 0.90),
}


@dataclass
class DROParams:
    """DRO 参数。

    Parameters
    ----------
    mode:
        ``"off"``  恒等(输出 == 输入);
        ``"auto"`` 根据图像暗部占比自动推强度;
        ``"lv1"``..``"lv5"`` 固定档位(对应 Sony DRO Lv1..Lv5)。
    high:
        亮部保护强度 [0,1]。越大,局部偏亮区域被抑制/保护得越多。
    low:
        暗部提亮强度 [0,1]。越大,局部偏暗区域被提亮得越多。
    shadow_detail:
        暗部提亮的锐度/强度 [0,1]。越大,提亮曲线在暗部越陡,
        暗部细节被拉得越开。
    luma:
        亮度权重 (wr, wg, wb)。
    white:
        值域上界(白点)。输入按 [0, white] 处理,输出 clip 到该范围。

    Notes
    -----
    当 ``mode`` 为 ``"lv1"``..``"lv5"`` 或 ``"auto"`` 时,
    ``high`` / ``low`` / ``shadow_detail`` 会被对应策略覆盖。
    仅当 ``mode`` 显式设为 ``"manual"`` 时才使用手动传入的三个值。
    """

    mode: str = "auto"
    high: float = 0.25
    low: float = 0.40
    shadow_detail: float = 0.5
    luma: Sequence[float] = DEFAULT_LUMA
    white: float = 1.0

    def resolve(self, img: np.ndarray, lum: np.ndarray | None = None) -> "DROParams":
        """根据 mode 解析出实际生效的 (low, high, shadow_detail)。

        auto 模式需要图像亮度;调用方若已算好可经 ``lum`` 传入以免重复计算。
        """
        mode = self.mode.lower()
        if mode == "off":
            low, high, sd = 0.0, 0.0, 0.0
        elif mode in _LEVEL_TABLE:
            low, high, sd = _LEVEL_TABLE[mode]
        elif mode == "auto":
            low, high, sd = _auto_strength(img, self.luma, self.white, lum)
        elif mode == "manual":
            low, high, sd = self.low, self.high, self.shadow_detail
        else:
            raise ValueError(f"unknown DRO mode: {self.mode!r}")
        return DROParams(
            mode=mode,
            high=float(np.clip(high, 0.0, 1.0)),
            low=float(np.clip(low, 0.0, 1.0)),
            shadow_detail=float(np.clip(sd, 0.0, 1.0)),
            luma=self.luma,
            white=self.white,
        )


def _auto_strength(img: np.ndarray, luma, white: float, lum: np.ndarray | None = None):
    """auto 模式:从图像暗部占比推强度。

    暗部像素越多,提亮(low)与锐度(shadow_detail)越强。
    ``lum`` 为调用方预先算好的亮度图,省去重复计算。
    """
    L = (_luminance(img, luma) if lum is None else lum) / max(white, 1e-12)
    dark_frac = float(np.mean(L < 0.25))  # 暗部占比
    low = 0.20 + 0.60 * dark_frac
    high = 0.15 + 0.25 * dark_frac
    sd = 0.35 + 0.45 * dark_frac
    return low, high, sd


def _luminance(img: np.ndarray, luma) -> np.ndarray:
    wr, wg, wb = luma
    return wr * img[..., 0] + wg * img[..., 1] + wb * img[..., 2]


def _box_blur_1d(a: np.ndarray, radius: int, axis: int) -> np.ndarray:
    """沿 axis 的一维 box blur,边界用 reflect(通过累积和实现 O(N))。"""
    if radius <= 0:
        return a
    a = np.moveaxis(a, axis, -1)
    n = a.shape[-1]
    r = min(radius, n - 1) if n > 1 else 0
    if r <= 0:
        return np.moveaxis(a, -1, axis)
    # reflect pad
    pad = np.pad(a, [(0, 0)] * (a.ndim - 1) + [(r, r)], mode="reflect")
    # 前缀和,窗口宽 2r+1
    csum = np.cumsum(pad, axis=-1)
    csum = np.concatenate(
        [np.zeros(csum.shape[:-1] + (1,), dtype=csum.dtype), csum], axis=-1
    )
    win = 2 * r + 1
    out = (csum[..., win:] - csum[..., :-win]) / win
    return np.moveaxis(out, -1, axis)


def _gaussian_blur(a: np.ndarray, sigma: float, passes: int = 3) -> np.ndarray:
    """用多次 box blur 逼近高斯(可分离,纯 numpy)。

    box 半径按 Wells(1986)近似公式由 sigma 与 passes 推得。
    """
    if sigma <= 0:
        return a.copy()
    # 理想 box 宽度
    ideal_w = np.sqrt(12.0 * sigma * sigma / passes + 1.0)
    wl = int(np.floor(ideal_w))
    if wl % 2 == 0:
        wl -= 1
    wl = max(wl, 1)
    radius = (wl - 1) // 2
    out = a.astype(np.float64, copy=False)  # box blur 不原地修改,首趟即产生新数组
    for _ in range(passes):
        out = _box_blur_1d(out, radius, axis=0)
        out = _box_blur_1d(out, radius, axis=1)
    return out


def _smoothstep(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def _gain_curve(
    Llocal: np.ndarray,
    low: float,
    high: float,
    shadow_detail: float,
) -> np.ndarray:
    """由归一化局部亮度 Llocal∈[0,1] 构造逐像素增益 g。

    - 局部偏暗 (Llocal 小):g > 1,提亮(shadow lift),受 low / shadow_detail 控制。
    - 局部偏亮 (Llocal 大):g < 1,抑制/保护(highlight protection),受 high 控制。
    - 中间调:g ≈ 1。
    曲线连续平滑(smoothstep 权重),无突变。
    """
    # 暗部权重:Llocal 越小越接近 1,shadow_detail 控制过渡的锐度。
    # 用 gamma 弯曲输入,让锐度更高时暗部权重更集中在深阴影。
    sd_gamma = 1.0 + 2.0 * shadow_detail  # 1..3
    dark_edge = 0.5  # 暗/中过渡中心
    wd_raw = _smoothstep((dark_edge - Llocal) / dark_edge)  # Llocal<0.5 时>0
    w_dark = np.power(wd_raw, 1.0 / sd_gamma)

    # 亮部权重:Llocal 越大越接近 1。
    bright_edge = 0.5
    w_bright = _smoothstep((Llocal - bright_edge) / (1.0 - bright_edge))

    # 最大提亮倍率:low 越大提得越多;shadow_detail 额外增强。
    max_lift = 1.0 + low * (1.2 + 0.8 * shadow_detail)  # 1 .. ~3
    # 最大压暗倍率:high 越大压得越多。
    max_drop = 1.0 - 0.5 * high  # 1 .. 0.5

    g = 1.0 + w_dark * (max_lift - 1.0) + w_bright * (max_drop - 1.0)
    return g


def apply_dro(img: np.ndarray, params: DROParams) -> np.ndarray:
    """对线性 RGB 图像施加 DRO(结构性近似)。

    Parameters
    ----------
    img:
        HxWx3 float,线性 RGB,值域 [0, params.white]。
    params:
        :class:`DROParams`。

    Returns
    -------
    HxWx3 float,处理后图像,clip 到 [0, params.white]。
    """
    img = np.asarray(img, dtype=np.float64)
    if img.ndim != 3 or img.shape[2] != 3:
        raise ValueError("img must be HxWx3")

    # 亮度只算一次:auto 模式的强度推断与后续局部估计共用
    L = _luminance(img, params.luma)
    p = params.resolve(img, lum=L)
    white = max(p.white, 1e-12)

    # off(以及被解析为零强度):恒等。np.clip 已返回新数组,无需 copy。
    if p.low == 0.0 and p.high == 0.0:
        return np.clip(img, 0.0, white)

    # 局部亮度估计:大半径低通,半径随图像尺寸缩放。
    h, w = L.shape
    sigma = max(h, w) * 0.06  # 大尺度 area 平均
    sigma = max(sigma, 2.0)
    Llocal = _gaussian_blur(L, sigma)

    # 归一化到 [0,1] 供 gain 曲线使用
    Ln = np.clip(Llocal / white, 0.0, 1.0)
    g = _gain_curve(Ln, p.low, p.high, p.shadow_detail)

    # 保色应用:按亮度增益缩放三通道,保持 R:G:B 比例(色相/色度不变)。
    out = img * g[..., None]
    return np.clip(out, 0.0, white)


__all__ = ["DROParams", "apply_dro"]
