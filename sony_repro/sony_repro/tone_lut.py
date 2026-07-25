"""tone_lut - Sony Imaging Edge MainGamma 的 bit-exact 复刻。

Sony Imaging Edge 的色调滑块(亮度 / 对比度 / 高光 / 阴影 / 黑 / 白 / Fade)
在引擎内部并不是一条条独立的曲线公式;它们经过多张查找表串联、再叠加相机
标定数据之后,最终塌成 **一张 1D LUT**,由反汇编中的 ``ZcTaskMainGamma``
逐像素应用。本模块精确复刻的是 **这张 LUT 的应用语义**,而不是滑块背后的
生成公式。

为什么不抄公式?
    滑块 -> LUT 的映射在引擎里是「多表串联 + 每台相机的标定」,无法干净转写
    成几行数学表达式。想得到与 Sony「完全一致」的结果,正确做法是:
    **把 Sony 在给定滑块组合下实际用的这张表提取出来**(例如喂一条已知灰阶
    进去、出图后读回输出值,再用 :meth:`ToneLUT.from_ramp` 重建 LUT),
    然后用本模块以 bit-exact 的方式回放它。

MainGamma 应用语义(反汇编确定,必须精确复刻)
    对每个通道的每个像素,输入值 ``v`` 是「索引单位」的 float,即图像值
    直接当作 LUT 下标::

        idx  = floor(v)                                  # 整数下标
        frac = v - idx                                   # 小数部分
        out  = LUT[idx]*(1-frac) + LUT[idx+1]*frac       # 相邻两项线性插值
        out  = (int16)(int)(out)                         # 先向零截断成 int,再存 int16

    - LUT 是 int16 数组;``idx`` clamp 到 ``[0, len-2]`` 以保证 ``idx+1``
      合法,``v`` 超范围一并 clamp。
    - ``exact=True`` 采用上面的向零截断(匹配 Sony);``exact=False`` 返回
      纯 float32 结果(不截断),便于分析。
    - 浮点一律用 float32,贴近原实现。
"""

from __future__ import annotations

import numpy as np

__all__ = ["ToneLUT"]


class ToneLUT:
    """持有一张 int16 一维 LUT,并以 Sony MainGamma 语义应用它。"""

    def __init__(self, entries: np.ndarray):
        entries = np.asarray(entries, dtype=np.int16)
        if entries.ndim != 1:
            raise ValueError("entries 必须是一维数组")
        if entries.shape[0] < 2:
            raise ValueError("LUT 至少需要 2 个条目(插值需要 idx+1)")
        self.entries = entries
        self._lut_f32 = entries.astype(np.float32)  # 插值用,避免每次 apply 重复转型

    def __len__(self) -> int:
        return int(self.entries.shape[0])

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return f"ToneLUT(n={len(self)}, dtype=int16)"

    # ------------------------------------------------------------------
    # 应用
    # ------------------------------------------------------------------
    def apply(self, values: np.ndarray, exact: bool = True) -> np.ndarray:
        """对任意形状的 float 输入按 MainGamma 语义查表 + 线性插值。

        Parameters
        ----------
        values:
            输入值,直接当作 LUT 下标(索引单位)。任意形状。
        exact:
            ``True`` 用向零截断得到 int16(匹配 Sony);``False`` 返回
            未截断的 float32 结果。

        Returns
        -------
        np.ndarray
            与 ``values`` 同形状。``exact=True`` 时 dtype 为 int16,
            ``exact=False`` 时为 float32。
        """
        lut = self._lut_f32
        n = lut.shape[0]

        v = np.asarray(values, dtype=np.float32)
        # v 超范围 clamp 到合法下标域 [0, n-1]
        v = np.clip(v, np.float32(0.0), np.float32(n - 1))

        idx = np.floor(v).astype(np.int32)
        # idx clamp 到 [0, n-2] 保证 idx+1 合法
        idx = np.clip(idx, 0, n - 2)

        frac = v - idx.astype(np.float32)

        lo = lut[idx]
        hi = lut[idx + 1]
        out = lo * (np.float32(1.0) - frac) + hi * frac

        if not exact:
            return out

        # C 语义:(int) 向零截断再存 int16。out 被相邻两 int16 项夹住,必在 int16 域内,
        # float32 -> int16 的 astype 本身即向零截断,与先 trunc 再存位一致。
        return out.astype(np.int16)

    def apply_image(self, img: np.ndarray, exact: bool = True, luts=None) -> np.ndarray:
        """对 HxWx3 图像逐通道应用 LUT。

        默认三通道共用本 LUT(``self``)。若传入 ``luts``(长度为通道数的
        ``ToneLUT`` 列表),则每个通道用各自的 LUT。
        """
        img = np.asarray(img)
        if img.ndim != 3:
            raise ValueError("img 必须是 HxWxC")
        c = img.shape[2]

        if luts is None:
            channel_luts = [self] * c
        else:
            if len(luts) != c:
                raise ValueError(f"luts 长度 {len(luts)} 与通道数 {c} 不匹配")
            channel_luts = luts

        out_dtype = np.int16 if exact else np.float32
        out = np.empty(img.shape, dtype=out_dtype)
        for ch in range(c):
            out[..., ch] = channel_luts[ch].apply(img[..., ch], exact=exact)
        return out

    # ------------------------------------------------------------------
    # 构造
    # ------------------------------------------------------------------
    @staticmethod
    def identity(n: int, out_max: int = None) -> "ToneLUT":
        """恒等 LUT。

        ``out_max is None`` 时 ``entries[i] = i``;否则线性映射
        ``entries[i] = round(i/(n-1) * out_max)``。
        """
        if n < 2:
            raise ValueError("n 至少为 2")
        idx = np.arange(n, dtype=np.float64)
        if out_max is None:
            vals = idx
        else:
            vals = np.round(idx / (n - 1) * out_max)
        return ToneLUT(vals.astype(np.int16))

    @classmethod
    def from_ramp(cls, input_idx: np.ndarray, output_vals: np.ndarray, n: int) -> "ToneLUT":
        """从测量到的(输入下标 -> 输出值)散点重建 0..n-1 的稠密 LUT。

        用 ``np.interp`` 在整数下标 ``0..n-1`` 上重采样。``input_idx`` 需
        升序排列(``np.interp`` 要求 x 递增)。若 ``input_idx`` 恰好就是
        稠密的 ``0..n-1``,结果直接等于 ``output_vals``。
        """
        input_idx = np.asarray(input_idx, dtype=np.float64)
        output_vals = np.asarray(output_vals, dtype=np.float64)
        if input_idx.shape != output_vals.shape:
            raise ValueError("input_idx 与 output_vals 形状必须一致")
        if np.any(np.diff(input_idx) < 0):
            raise ValueError("input_idx 必须升序排列")

        targets = np.arange(n, dtype=np.float64)
        resampled = np.interp(targets, input_idx, output_vals)
        return cls(np.round(resampled).astype(np.int16))

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------
    def save(self, path) -> None:
        """保存为 npz。"""
        np.savez(path, entries=self.entries)

    @classmethod
    def load(cls, path) -> "ToneLUT":
        """从 npz 载入。"""
        with np.load(path) as data:
            return cls(data["entries"])

    def to_cube(self, path) -> None:
        """导出为 1D ``.cube`` LUT 文本。

        标准 ``LUT_1D_SIZE`` 头 + 每行一个归一化值。int16 entries 按
        LUT 自身的 [min, max] 归一化到 [0,1](退化为常量时全写 0)。
        """
        n = len(self)
        vals = self.entries.astype(np.float64)
        vmin = vals.min()
        vmax = vals.max()
        if vmax > vmin:
            norm = (vals - vmin) / (vmax - vmin)
        else:
            norm = np.zeros_like(vals)
        norm = np.clip(norm, 0.0, 1.0)

        lines = ["# Generated by tone_lut (Sony MainGamma repro)", f"LUT_1D_SIZE {n}", ""]
        for x in norm:
            lines.append(f"{x:.6f} {x:.6f} {x:.6f}")
        text = "\n".join(lines) + "\n"
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
