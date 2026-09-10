r"""`0x140395f20` 的 numpy 位精确复刻(Sony Imaging Edge ``Edit.exe``)。

作用:把 ChromaNR 之后的 4:2:2 小图集合 ``[Y(H,W), C1(H,W/2), C2(H,W/2)]``
放回一张全尺寸(out_w x out_h)的三平面集合里 —— 色度做 2:1 水平**复制**上采样,
亮度直接搬运,写入位置从 ``(x0, y0)`` 开始。

反汇编要点(见 marble_disasm/fn_140395f20.txt + 0x140396167.. 补齐部分)::

    halfw = (x1 - x0 + 1) / 2      // 向零取整,x1/y1 取自 functor 的 rect
    for (y = y0; y < y1; y++) {
        sy = y - y0;                                  // 源行(源图原点对齐 rect 左上角)
        for (i = 0; i < halfw; i++) {
            dstY [y][x0+2*i  ] = srcY [sy][2*i  ];
            dstY [y][x0+2*i+1] = srcY [sy][2*i+1];
            dstC1[y][x0+2*i  ] = dstC1[y][x0+2*i+1] = srcC1[sy][i];
            dstC2[y][x0+2*i  ] = dstC2[y][x0+2*i+1] = srcC2[sy][i];
        }
    }

没有插值、没有滤波、没有四舍五入 —— 纯粹的 nearest / pixel-doubling。
目标集合是 ``0x1401527f0`` 新分配的,函数**只写 rect 内部**,rect 外那圈
(本例是 8 px 边框)保持分配时的内容 —— 实测是未初始化的堆垃圾,不可复现。
这里用 0 填充,并且允许调用方通过 ``border_*`` 传入自己的底图。

函数返回后,调用点把 task 的 rect 从 (x0,y0,x1,y1) 各向内收 16
(``functor->vtable[0x58](16,16,16,16)``),本例 (8,8,1072,624) -> (24,24,1056,608)。
"""

from __future__ import annotations

import numpy as np

__all__ = ['fin_5f20']


def fin_5f20(y, c1_half, c2_half, x0, y0, W, H, out_w, out_h,
             border_y=None, border_c1=None, border_c2=None):
    """把 4:2:2 的 (y, c1_half, c2_half) 展开进 out_h x out_w 的三个平面。

    参数
    ----
    y         : (H, W)   uint16,亮度(带 +4096 偏置)
    c1_half   : (H, W//2 或更宽) uint16,半分辨率色度 1
    c2_half   : 同上
    x0, y0    : 写入目标平面的左上角 = functor rect 的 (x0, y0)
    W, H      : rect 的宽高,即 x1 = x0 + W, y1 = y0 + H
                (二进制里 halfw = (x1-x0+1)/2 向零取整 = (W+1)//2)
    out_w/h   : 目标平面尺寸
    border_*  : 可选,rect 外那圈的底图(默认 0;二进制里是未初始化内存)

    返回 (y_full, c1_full, c2_full),都是 (out_h, out_w) uint16。
    """
    y = np.asarray(y, dtype=np.uint16)
    c1_half = np.asarray(c1_half, dtype=np.uint16)
    c2_half = np.asarray(c2_half, dtype=np.uint16)

    halfw = int(W + 1) // 2          # (x1 - x0 + 1) / 2,向零取整(W >= 0)
    rows = int(H)                    # y1 - y0

    def _plane(border):
        if border is None:
            return np.zeros((out_h, out_w), dtype=np.uint16)
        out = np.array(border, dtype=np.uint16, copy=True)
        assert out.shape == (out_h, out_w)
        return out

    y_full = _plane(border_y)
    c1_full = _plane(border_c1)
    c2_full = _plane(border_c2)

    if rows <= 0 or halfw <= 0:
        return y_full, c1_full, c2_full

    # 亮度:直接搬 2*halfw 列(注意源的列索引是 2*i / 2*i+1,不带 x0 偏移)
    y_full[y0:y0 + rows, x0:x0 + 2 * halfw] = y[:rows, :2 * halfw]

    # 色度:每个样本写两列 —— 复制上采样(左对齐相位,无插值)
    c1_full[y0:y0 + rows, x0:x0 + 2 * halfw] = np.repeat(c1_half[:rows, :halfw], 2, axis=1)
    c2_full[y0:y0 + rows, x0:x0 + 2 * halfw] = np.repeat(c2_half[:rows, :halfw], 2, axis=1)

    return y_full, c1_full, c2_full
