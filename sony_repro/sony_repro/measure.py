"""模块 1 的数据闭环:从 Imaging Edge 的两次显影里测出一个色调滑块的传递曲线。

用法:同一张 RAW 在 Imaging Edge 里显影两次导出 16-bit TIFF ——
一张所有滑块中性(`neutral`),一张只把目标滑块调到某值(`adjusted``),
其余设置(白平衡/Creative Look/DRO)保持完全一致。两张图同尺寸、逐像素对齐。

`extract_transfer_curve` 按 neutral 的像素值把两图配对,得到
"中性色调 → 调整后色调" 的 1D 传递曲线;这就是该滑块在给定值下对图像做的事。
因为色调滑块是逐像素的(见 llr/docs/sony-edit-internals.md),这条曲线完整刻画了它。

注意:这里测的是**输出空间**的传递曲线(display-referred),用浮点表示,适合回放
滑块的可见效果。若要 Sony **引擎内部** 15-bit MainGamma 那张 int16 表的 bit-exact
回放,用 `tone_lut.ToneLUT`(需从内存 dump 0x118f6 表)。
"""

from __future__ import annotations

import numpy as np

# ITU-R BT.709 luma 权重(RGB -> 亮度)
LUMA_BT709 = (0.2126, 0.7152, 0.0722)


def _to_unit(img: np.ndarray) -> np.ndarray:
    """归一化到 [0,1] 的 float32 图(整数类型按其 dtype 峰值归一)。"""
    a = np.asarray(img)
    scale = float(np.iinfo(a.dtype).max) if np.issubdtype(a.dtype, np.integer) else 1.0
    return a.astype(np.float32) / scale


def extract_transfer_curve(
    neutral: np.ndarray,
    adjusted: np.ndarray,
    n: int = 4096,
    per_channel: bool = True,
    reduce: str = "median",
    min_count: int = 1,
) -> np.ndarray:
    """测出 neutral→adjusted 的 1D 传递曲线。

    参数
    ----
    neutral, adjusted : 同尺寸图(uint8/uint16/float,HxW 或 HxWx3)。
    n        : 曲线采样点数(输入轴等分为 n 格,输出为归一化 [0,1])。
    per_channel : True 时分 R/G/B 各测一条(返回 (3,n));False 用亮度合并成一条 (n,)。
    reduce   : 每个输入格里对输出取 'median'(默认,抗噪)或 'mean'。
    min_count: 一个输入格至少要这么多样本才采信,不足的格靠相邻插值填。

    返回
    ----
    curve : 形状 (n,) 或 (3,n),float32,索引 i 对应输入归一化值 i/(n-1),
            值域 [0,1]。可直接喂给 `apply_transfer_curve`。
    """
    if neutral.shape != adjusted.shape:
        raise ValueError(f"两图尺寸必须一致: {neutral.shape} vs {adjusted.shape}")
    nu = _to_unit(neutral)
    ad = _to_unit(adjusted)

    if nu.ndim == 2:  # 灰度
        nu = nu[..., None]
        ad = ad[..., None]
    chans = nu.shape[-1]

    if not per_channel:
        w = np.array(LUMA_BT709, dtype=np.float32)[:chans]
        w = w / w.sum()
        nu = (nu * w).sum(-1, keepdims=True)
        ad = (ad * w).sum(-1, keepdims=True)
        chans = 1

    xs = np.linspace(0.0, 1.0, n, dtype=np.float64)
    out = np.empty((chans, n), dtype=np.float32)
    for c in range(chans):
        src = nu[..., c].ravel()
        dst = ad[..., c].ravel()
        idx = np.clip((src * (n - 1)).round().astype(np.int64), 0, n - 1)
        order = np.argsort(idx, kind="stable")
        idx_s, dst_s = idx[order], dst[order]
        # 每个输入格的样本区间边界
        bounds = np.searchsorted(idx_s, np.arange(n + 1))
        obs_x, obs_y = [], []
        for i in range(n):
            lo, hi = bounds[i], bounds[i + 1]
            if hi - lo >= min_count:
                seg = dst_s[lo:hi]
                obs_x.append(xs[i])
                obs_y.append(np.median(seg) if reduce == "median" else seg.mean())
        if len(obs_x) < 2:
            raise ValueError(f"通道 {c} 有效输入格不足({len(obs_x)}),无法拟合曲线")
        curve = np.interp(xs, np.asarray(obs_x), np.asarray(obs_y))
        out[c] = curve.astype(np.float32)

    return out[0] if chans == 1 else out


def apply_transfer_curve(img: np.ndarray, curve: np.ndarray, clip: bool = True) -> np.ndarray:
    """把测出的曲线应用到归一化 [0,1] 图上(线性插值查表)。

    curve 为 (n,) 时所有通道共用;为 (3,n) 时逐通道。返回 float32,值域 [0,1]。
    """
    a = np.asarray(img, dtype=np.float32)
    curve = np.asarray(curve, dtype=np.float32)
    single = a.ndim == 2
    if single:
        a = a[..., None]
    xs = np.linspace(0.0, 1.0, curve.shape[-1], dtype=np.float32)
    out = np.empty_like(a)
    for c in range(a.shape[-1]):
        lut = curve if curve.ndim == 1 else curve[min(c, curve.shape[0] - 1)]
        v = np.clip(a[..., c], 0.0, 1.0) if clip else a[..., c]
        out[..., c] = np.interp(v, xs, lut).astype(np.float32)
    if clip:
        out = np.clip(out, 0.0, 1.0)
    return out[..., 0] if single else out


def curve_error(a: np.ndarray, b: np.ndarray) -> dict:
    """两条曲线(或两图)逐元素误差统计,便于评估复刻质量。"""
    a = np.asarray(a, dtype=np.float64).ravel()
    b = np.asarray(b, dtype=np.float64).ravel()
    d = np.abs(a - b)
    return {"mean": float(d.mean()), "p95": float(np.percentile(d, 95)), "max": float(d.max())}
