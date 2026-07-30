"""汇总 luma_gap.py 的十六帧输出:llr 多出来的亮度细节,是噪声还是结构?

读 `tmp/luma16.txt` 里的 SUMMARY 行(逐批追加,中途被杀只丢当前一帧)。

判据(§2.12 定的):
  * `a` 是 llr 的细带对 Edit 的细带做最小二乘的斜率。a>1 = 把同样的结构放大了
    (锐化过头),a≈1 = 结构照搬,a<1 = 连 Edit 有的结构都没留全。
  * `σ_r` 是残差,即 llr 有而 Edit 没有的成分。它对 σ_Edit 的比值就是"额外噪声"
    的量级。
  * **结构区 corr 是准入门槛**,不是装饰:低于 0.85 说明这一带压根没对齐,
    那一行的 a 和 σ_r 都不算数。§2.12 里 band0/band1 就是这么被挡掉的。

不按 ISO 分组 —— §2.11 已经证过,在正确的轴上 ISO 那条相关是假象。这里按
**带**分组,因为噪声与锐化的分布随尺度差别很大。
"""
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

TXT = Path("/home/jannchie/llr/tmp/luma16.txt")
ROW = re.compile(r"^SUMMARY (\S+) (\d+) (\d+) ([\d.]+) ([\d.]+) (-?[\d.]+) "
                 r"([\d.]+) (-?[\d.]+) (-?[\d.]+)")
TRUST = 0.85  # 结构区 corr 的准入线


def main():
    rows = []
    for line in TXT.read_text(encoding="utf-8", errors="replace").splitlines():
        m = ROW.match(line)
        if m:
            rows.append((m.group(1), int(m.group(2)), int(m.group(3)),
                         *(float(m.group(i)) for i in range(4, 10))))
    if not rows:
        raise SystemExit(f"没从 {TXT} 解析到 SUMMARY 行")
    stems = sorted({r[0] for r in rows})
    nb = max(r[2] for r in rows) + 1
    print(f"{len(stems)} 帧 × {nb} 带\n")

    for b in range(nb):
        sel = [r for r in rows if r[2] == b]
        a = np.array([[r[3], r[4], r[5], r[6], r[7], r[8]] for r in sel], float)
        # 平坦区 corr(第 5 列)这里不用:分解本来就在平坦区做,准入看的是结构区。
        se, sl, k, sr, _, ce = a.T
        trusted = ce >= TRUST
        tag = ("✅ 可用" if trusted.mean() >= 0.75
               else ("⚠️ 半数以上没对齐" if trusted.mean() < 0.5 else "⚠️ 部分可用"))
        print(f"── band{b}  {2 ** b}px   结构区 corr 中位 {np.median(ce):.2f}"
              f"   过线({TRUST}) {trusted.sum()}/{len(sel)}   {tag}")
        if trusted.sum() == 0:
            print("     (无可用帧,跳过)\n")
            continue
        i = trusted
        print(f"     σ_llr/σ_Edit   中位 {np.median(sl[i] / se[i]):5.2f}"
              f"   区间 [{(sl[i] / se[i]).min():.2f}, {(sl[i] / se[i]).max():.2f}]")
        print(f"     放大 a         中位 {np.median(k[i]):5.2f}"
              f"   区间 [{k[i].min():.2f}, {k[i].max():.2f}]"
              f"   >1 的帧数 {(k[i] > 1).sum()}/{i.sum()}")
        print(f"     独有 σ_r/σ_Edit 中位 {np.median(sr[i] / se[i]):5.2f}"
              f"   区间 [{(sr[i] / se[i]).min():.2f}, {(sr[i] / se[i]).max():.2f}]\n")

    # 逐帧看可用带里最粗的那一条(最可信),便于对上 §2.11 的老表。
    top = max(b for b in range(nb)
              if any(r[2] == b and r[8] >= TRUST for r in rows))
    print(f"逐帧(band{top},{2 ** top}px —— 结构区 corr 最高的一带)")
    print(f"  {'帧':<10} {'ISO':>5} {'σ_E':>7} {'σ_llr':>7} {'比':>5}"
          f" {'a':>6} {'σ_r/σ_E':>8} {'结构corr':>8}")
    sel = sorted((r for r in rows if r[2] == top), key=lambda r: r[1])
    for stem, iso, _b, se, sl, k, sr, _cf, ce in sel:
        mark = "" if ce >= TRUST else "  ⚠️未对齐"
        print(f"  {stem:<10} {iso:>5} {se:7.3f} {sl:7.3f} {sl / max(se, 1e-9):5.2f}"
              f" {k:6.2f} {sr / max(se, 1e-9):8.2f} {ce:8.2f}{mark}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
