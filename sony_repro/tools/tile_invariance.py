"""降噪算子与**尺寸/分块无关**吗 —— 补上逻辑链缺的那一环。

引擎的全分辨率 RawNR 只在导出时跑,而 Edit 会缓存渲染结果,这一步抓不到
(notes 2.19.2)。所以"llr 全分辨率管线"没法直接对着引擎验。能立的论证是三段:

  1. 算子在引擎的输入上**逐位正确** —— 已验(红蓝 100.0000%,绿色 99.994%+);
  2. 算子**与尺寸无关**(纯逐像素/局部,无全局状态)—— **本工具验这一条**;
  3. `Lossless Compressed RAW 2` 是无损压缩,解码结果唯一 —— 由格式保证。

三段都立,全分辨率的输出才跟着立。第 2 条以前一直是"想当然":实现里只要藏着任何
全局量(比如按整幅算的统计、按尺寸变的常数),链子就断在这儿。

做法:同一份平面,整幅跑一次;再切成若干块、每块四周留够 margin 各跑一次,把有效
区拼回去,逐位比。相同 => 算子只看局部,尺寸怎么变都一样。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

from llr_worker.sony.rawnr_simd import (
    PHASE_MARGIN,
    denoise_phase_green,
    denoise_phase_rb,
)

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "rawnr_full_fl_test.npz"
    z = np.load(os.path.join(HERE, name))
    mos = z["mosaic"].astype(np.float32)
    # 用真实马赛克拆出的相位,而不是造随机数 —— 要的是真实的边缘与高光分布。
    g0 = mos[0::2, 1::2]
    g1 = mos[1::2, 0::2]
    rb = mos[0::2, 0::2]
    tbl = z["rb0_tbl0"]
    m = PHASE_MARGIN
    print(f"{name}  相位 {rb.shape}  margin={m}\n")

    cases = [
        ("红蓝", lambda p, o: denoise_phase_rb(p, tbl), rb, None),
        ("绿色 phase0", lambda p, o: denoise_phase_green(p, o, tbl, phase=0), g0, g1),
        ("绿色 phase1", lambda p, o: denoise_phase_green(p, o, tbl, phase=1), g1, g0),
    ]
    ok_all = True
    for label, fn, plane, other in cases:
        whole = fn(plane, other)
        h, w = plane.shape
        # 切四块,每块向外多取 margin,算完再把有效区拼回去。
        out = np.empty_like(whole)
        cuts_y = [(0, h // 2), (h // 2, h)]
        cuts_x = [(0, w // 2), (w // 2, w)]
        for y0, y1 in cuts_y:
            for x0, x1 in cuts_x:
                ey0, ey1 = max(0, y0 - m), min(h, y1 + m)
                ex0, ex1 = max(0, x0 - m), min(w, x1 + m)
                sub = plane[ey0:ey1, ex0:ex1]
                sub_o = None if other is None else other[ey0:ey1, ex0:ex1]
                r = fn(sub, sub_o)
                # r 的 (0,0) 对应 sub 的 (m,m),也就是原图的 (ey0+m, ex0+m)。
                ty0, tx0 = y0 - m, x0 - m           # 目标区在 whole 里的起点
                sy0 = (y0 - m) - (ey0 + m - m) - m  # 在 r 里的起点
                sy0 = y0 - ey0 - m
                sx0 = x0 - ex0 - m
                ny, nx = (y1 - y0), (x1 - x0)
                if ty0 < 0:
                    ny += ty0
                    sy0 -= ty0
                    ty0 = 0
                if tx0 < 0:
                    nx += tx0
                    sx0 -= tx0
                    tx0 = 0
                ny = min(ny, whole.shape[0] - ty0, r.shape[0] - sy0)
                nx = min(nx, whole.shape[1] - tx0, r.shape[1] - sx0)
                if ny <= 0 or nx <= 0:
                    continue
                out[ty0:ty0 + ny, tx0:tx0 + nx] = r[sy0:sy0 + ny, sx0:sx0 + nx]
        # 只比四块都覆盖到的内部,边界那圈本来就没定义。
        inner = (slice(m, whole.shape[0] - m), slice(m, whole.shape[1] - m))
        same = float(np.mean(out[inner] == whole[inner]))
        mx = float(np.abs(out[inner] - whole[inner]).max())
        ok_all &= same == 1.0
        print(f"  {label:<12} 整幅 vs 分块:逐位相同 {100 * same:8.4f}%   "
              f"最大差 {mx:.6g}")
    print("\n  全部 100% => 算子只看局部,与尺寸/分块无关;"
          "\n  于是「引擎输入上逐位正确」+「无损压缩解码唯一」就能推到全分辨率。")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
