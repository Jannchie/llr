r"""`0x14038b010` 的 numpy 位精确复刻(Sony Imaging Edge ``Edit.exe``)。

一步做两件事:

1. **色度混合** —— 把 ChromaNR 处理后的色度和原始色度按 ``amount`` 线性混合::

       C = clamp(orig * (1 - amount) + new * amount, 0, 65535)

2. **YCC -> 14bit RGB** —— 用 0x14038aa40 那个正变换的逆矩阵(Q15 定点)::

       Yf = Y - 4096 ; C1f = C1 - 32768 ; C2f = C2 - 32768
       R = clamp(trunc((-1438*C1f + 9542*Yf + 5414*C2f) / 32768), 0, 16383)
       G = clamp(trunc((-1459*C1f + 9547*Yf - 4376*C2f) / 32768), 0, 16383)
       B = clamp(trunc(( 14864*C1f + 9538*Yf -  310*C2f) / 32768), 0, 16383)

函数里有两条路径,**都要复刻**才能全图位精确:

* **向量路径**(0x14038b2b0,每次 8 像素,float32):对 ``x in [0, w & ~7)``、
  所有行执行。混合结果保持浮点(不取整),矩阵乘加也在 float32 里做,
  ``vdivps /32768`` 后 ``vroundps ...,0xb`` 向零取整,再 max(0)/min(16383)。
  9542*61439 ≈ 5.9e8 已经超过 2^24,所以 float32 的舍入是**可观测的**,必须逐步模拟。

* **标量尾巴**(0x14038b4e0,整数 Q15):本该只跑 ``x in [w&~7, w)``,但
  循环变量 ``esi`` 在**行循环之外**才被清零(0x14038b1c7),而且色度指针
  ``[rbp+8]/[rbp+0x28]/[rbp+0x10]/[rbp+0x30]`` 在标量循环里从不前进。结果是:

    - 第 0 行:标量循环从 x=0 一路跑到 x=w-1,把向量路径刚写的整行**全部覆盖**,
      而且四个色度值全部取自「第 0 行起点 + (w&~7) 个元素」这一个固定位置
      (stride = 2*w 时就是第 1 行第 0 列),只有 Y 随 x 变化;
    - 第 1..h-1 行:``esi`` 已经等于 w,标量循环直接跳过 —— 这些行只有向量路径的结果。

  这是二进制里的既有缺陷。第 0 行落在无效边框里,对成像没有影响,但要做到
  全图位精确就必须照抄。标量路径用 ``cvttss2si`` + ``movzx ax`` 把混合后的色度
  截断成 16 位整数,再用 ``sar 15``(向下取整)而不是向零取整 —— 由于之后
  会 clamp 到 0,两者结果一致。
"""

from __future__ import annotations

import numpy as np

__all__ = ['blend_b010', 'YCC_TO_RGB_Q15']

F32 = np.float32

# (C1 系数, Y 系数, C2 系数, 常数项) —— 常数项 = -(kY*4096 + (kC1+kC2)*32768)
YCC_TO_RGB_Q15 = {
    'R': (-1438, 9542, 5414, -169369600),   # 0x59e / 0x2546 / 0x1526 / -0xa186000
    'G': (-1459, 9547, -4376, 152096768),   # 0x5b3 / 0x254b / 0x1118 / +0x910d000
    'B': (14864, 9538, -310, -515973120),   # 0x3a10 / 0x2542 / 0x136 / -0x1ec12000
}


def _f32(x):
    return np.asarray(x, dtype=np.float32)


def blend_b010(y, c1_new, c1_orig, c2_new, c2_orig, amount: float):
    """返回 (r, g, b),都是 (h, w) uint16,取值 0..16383。

    y / c1_* / c2_* 都是 (h, w) uint16 全分辨率平面。
    ``amount`` = 0 表示完全用原始色度,1 表示完全用 ChromaNR 之后的色度。
    """
    y = np.asarray(y, dtype=np.uint16)
    c1_new = np.asarray(c1_new, dtype=np.uint16)
    c1_orig = np.asarray(c1_orig, dtype=np.uint16)
    c2_new = np.asarray(c2_new, dtype=np.uint16)
    c2_orig = np.asarray(c2_orig, dtype=np.uint16)
    h, w = y.shape
    wv = w & ~7                      # [rbp] = w & 0xfffffff8

    a = F32(amount)
    ia = F32(F32(1.0) - a)           # xmm11 = 1.0f - amount

    r = np.zeros((h, w), dtype=np.uint16)
    g = np.zeros((h, w), dtype=np.uint16)
    b = np.zeros((h, w), dtype=np.uint16)

    # ---------- 向量路径:所有行 x in [0, wv) ----------
    if wv > 0 and h > 0:
        yf = _f32(y[:, :wv]) - F32(4096.0)

        def _blend(new, old):
            # vmulps orig*(1-a) ; vmulps new*a ; vaddps ; vmaxps 0 ; vminps 65535 ; vsubps 32768
            t = (_f32(old[:, :wv]) * ia) + (_f32(new[:, :wv]) * a)
            t = np.maximum(F32(0.0), t)
            t = np.minimum(F32(65535.0), t)
            return t - F32(32768.0)

        c1f = _blend(c1_new, c1_orig)
        c2f = _blend(c2_new, c2_orig)

        for plane, out in (('R', r), ('G', g), ('B', b)):
            k1, k0, k2, _ = YCC_TO_RGB_Q15[plane]
            s = (c1f * F32(k1)) + (yf * F32(k0))     # vmulps, vmulps, vaddps
            s = s + (c2f * F32(k2))                  # vmulps, vaddps
            q = s / F32(32768.0)                     # vdivps(精确,2 的幂)
            q = np.trunc(q)                          # vroundps imm=0xb,向零取整
            q = np.maximum(F32(0.0), q)              # vmaxps
            q = np.minimum(F32(16383.0), q)          # vminps
            out[:, :wv] = q.astype(np.uint16)        # vcvtps2dq + vpackusdw

    # ---------- 标量尾巴:只在第 0 行跑,而且跑满整行 ----------
    if h > 0 and w > 0:
        # 色度指针停在「第 0 行起点 + wv 个元素」,整个标量循环里不再前进。
        # 平面是紧凑的(stride = 2*w),所以这就是 flat 索引 wv。
        def _fixed(arr):
            flat = arr.reshape(-1)
            return int(flat[wv]) if wv < flat.size else 0

        c1b = np.float32(_fixed(c1_orig)) * ia + np.float32(_fixed(c1_new)) * a
        c2b = np.float32(_fixed(c2_new)) * a + np.float32(_fixed(c2_orig)) * ia
        c1b = min(F32(65535.0), max(F32(0.0), c1b))
        c2b = min(F32(65535.0), max(F32(0.0), c2b))
        # cvttss2si + movzx ax:向零取整成 int32,再取低 16 位
        c1v = int(np.trunc(np.float64(c1b))) & 0xFFFF
        c2v = int(np.trunc(np.float64(c2b))) & 0xFFFF

        yr = y[0].astype(np.int64)
        for plane, out in (('R', r), ('G', g), ('B', b)):
            k1, k0, k2, off = YCC_TO_RGB_Q15[plane]
            v = (k0 * yr + k2 * c2v + k1 * c1v + off) >> 15   # sar 15 = 向下取整
            out[0] = np.clip(v, 0, 16383).astype(np.uint16)

    return r, g, b
