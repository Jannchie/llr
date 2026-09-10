"""在整幅 raw 里定位引擎那个 tile。

`mosaic_noise_scale.py` 已经判定它是**原始像素**(噪声 σ 之比 0.972,降采样平均
会降到 1/sqrt(n))。那它就是原图的某一块,只是不在左上角 —— `mosaic_tile_match.py`
只搜了 32 像素以内,自然找不到。

这里用互相关在全图找它。找到且逐位相同,就彻底坐实:**降噪的输入两边同源**,
之前"那些捕获都是预览路径"的撤回本身要再撤回。

做法:拿 mosaic 中心的一小块当模板,用 FFT 互相关在对应相位平面上找峰;找到粗位置
再在附近逐像素核对。相位平面上的偏移乘 2 就是原图坐标。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import rawpy

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")

from aniso_stage import find_arw  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
T = 192          # 模板边长


def main() -> int:
    stem = sys.argv[1] if len(sys.argv) > 1 else "DSC03036"
    cap = sys.argv[2] if len(sys.argv) > 2 else f"rawnr_full_{stem}.npz"
    z = np.load(os.path.join(HERE, cap))
    mos = z["mosaic"]
    arw = find_arw(stem)
    with rawpy.imread(str(arw)) as raw:
        vis = raw.raw_image_visible.copy()

    eng = mos[0::2, 0::2].astype(np.float64)
    ref = vis[0::2, 0::2].astype(np.float64)
    eh, ew = eng.shape
    print(f"{stem}  引擎相位 {eng.shape}   rawpy 相位 {ref.shape}")

    def highpass(a, k=32):
        """减掉 k x k 的箱式均值。不去掉常数项,互相关的峰会被亮区带跑 ——
        第一版只给模板去了均值、原图没去,找出来的位置逐位只有 0.13%。"""
        p = np.pad(a, ((1, 0), (1, 0)))
        ii = np.cumsum(np.cumsum(p, 0), 1)
        h, w = a.shape
        ky = np.minimum(np.arange(h) + k // 2, h - 1)
        kx = np.minimum(np.arange(w) + k // 2, w - 1)
        y0 = np.maximum(np.arange(h) - k // 2, 0)
        x0 = np.maximum(np.arange(w) - k // 2, 0)
        s = (ii[np.ix_(ky + 1, kx + 1)] - ii[np.ix_(y0, kx + 1)]
             - ii[np.ix_(ky + 1, x0)] + ii[np.ix_(y0, x0)])
        n = ((ky + 1 - y0)[:, None] * (kx + 1 - x0)[None, :])
        return a - s / n

    cy, cx = eh // 2, ew // 2
    tpl = highpass(eng)[cy:cy + T, cx:cx + T]
    tpl = tpl - tpl.mean()
    refh = highpass(ref)

    H, W = ref.shape
    fr = np.fft.rfft2(refh)
    ft = np.fft.rfft2(tpl, s=(H, W))
    corr = np.fft.irfft2(fr * np.conj(ft), s=(H, W))
    py, px = np.unravel_index(np.argmax(corr), corr.shape)
    print(f"  互相关峰值在相位坐标 ({py}, {px})")

    # 峰值对应"模板左上角"落在 ref 的哪里;换算回 mosaic 原点。
    oy, ox = py - cy, px - cx
    print(f"  推出 mosaic 原点(相位坐标) ({oy}, {ox})  "
          f"=> 原图 ({oy * 2}, {ox * 2})")
    if not (0 <= oy and 0 <= ox and oy + eh <= H and ox + ew <= W):
        print("  ⚠️ 推出的位置越界,峰值多半是假的")
        return 0

    sub = ref[oy:oy + eh, ox:ox + ew]
    same = 100.0 * float(np.mean(sub == eng))
    d = sub - eng
    print(f"\n  该处逐位相同 {same:.4f}%   |差| 中位 "
          f"{float(np.median(np.abs(d))):.4f}   最大 {float(np.abs(d).max()):.1f}")
    # 四个相位都核对一遍,单个相位对上可能是巧合。
    print("\n  四个相位分别核对:")
    for i, (y0, x0) in enumerate([(0, 0), (0, 1), (1, 0), (1, 1)]):
        e = mos[y0::2, x0::2].astype(np.float64)
        s = vis[y0::2, x0::2][oy:oy + e.shape[0], ox:ox + e.shape[1]]
        if s.shape != e.shape:
            continue
        print(f"    相位{i}  逐位相同 {100 * float(np.mean(s == e)):8.4f}%   "
              f"|差| 中位 {float(np.median(np.abs(s - e))):.4f}")
    if same > 99.0:
        print("\n  => 就是它。降噪的输入两边同源,"
              "\n     之前的端到端验证跑的就是 llr 会拿到的那批数字。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
