"""绿色滤波核的第一次调用(which=0)对应 Bayer 的哪一个绿相位。

两个绿相位的基准表不同(`green_base_verify.py`),所以这个映射搞反了,生产代码就
会给每个平面配错的表 —— 分数会从 99.85% 掉到 56%,而且是那种「看着像有点噪」
而不是「明显坏掉」的错法,很难在图上发现。

判据是相关:核收到的 `ref` 是 analysis 的输出,基本就是该相位的低通版本,与它
自己那个相位的相关应当接近 1,与另外三个明显更低。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
#: pack_bayer 的相位顺序:RGGB 下 (0,0)=R、(0,1)=G、(1,0)=G、(1,1)=B。
PHASES = [(0, 0), (0, 1), (1, 0), (1, 1)]


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "rawnr_kern_fl_test_g_s0w0.npz"
    z = np.load(os.path.join(HERE, name))
    mos, ref, ref2 = z["mosaic"], z["ref"], z["ref2"]
    which = int(z["which"][0]) if "which" in z.files else -1
    rect = z["rect"].tolist() if "rect" in z.files else None
    print(f"{name}  which={which}  rect={rect}  mosaic={mos.shape}  ref={ref.shape}")

    for label, a in (("ref (自身平面)", ref), ("ref2 (另一平面)", ref2)):
        print(f"\n  {label} 与四个相位的相关:")
        for i, (y0, x0) in enumerate(PHASES):
            p = mos[y0::2, x0::2].astype(np.float64)
            h = min(p.shape[0], a.shape[0])
            w = min(p.shape[1], a.shape[1])
            # 边缘几圈是核的 margin,不参与比较。
            pa = p[8:h - 8, 8:w - 8].ravel()
            aa = a[8:h - 8, 8:w - 8].astype(np.float64).ravel()
            r = float(np.corrcoef(pa, aa)[0, 1])
            tag = "  ← 就是它" if r > 0.99 else ""
            print(f"    相位 {i} (y0={y0}, x0={x0})  相关 {r:+.6f}{tag}")
    # 相关性区分不开(两个绿相位本身就高度相关,读数只差万分之七),所以再用一个
    # 决定性的判据:从 mosaic 重建两个绿平面,跑 analysis_green,看算出来的 ref
    # 跟捕获的 ref 是不是逐点重合。重合的那个才是自身平面。
    from llr_worker.sony.rawnr_simd import OFFSET_GREEN, analysis_green
    g1, g2 = mos[0::2, 1::2].astype(np.float32), mos[1::2, 0::2].astype(np.float32)
    print("\n  用 analysis_green 重建,并**扫描对齐偏移** —— 不去猜 analysis 吃掉")
    print("  几圈,让数据自己说;真正的自身平面应当在某个偏移上逐位重合。")
    n = 32
    for label, (a, b) in (("相位1 为自身", (g1, g2)), ("相位2 为自身", (g2, g1))):
        _, rc = analysis_green(a, b, OFFSET_GREEN)
        best = None
        for dy in range(-3, 4):
            for dx in range(-3, 4):
                got = rc[n:-n, n:-n]
                want = ref[n + dy:n + dy + got.shape[0], n + dx:n + dx + got.shape[1]]
                if want.shape != got.shape:
                    continue
                err = np.abs(got.astype(np.float64) - want.astype(np.float64))
                cand = (float(np.median(err)), (dy, dx),
                        100.0 * float(np.mean(err == 0)))
                if best is None or cand[0] < best[0]:
                    best = cand
        med, sh, exact = best
        print(f"    {label}:  最佳偏移 {sh}   |误差| 中位 {med:10.6f}   "
              f"逐位相同 {exact:.3f}%")
    print("\n  RGGB 下相位 1 与 2 都是绿色;which=0 认到哪一个,"
          "生产代码里第一副表就配给哪一个。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
