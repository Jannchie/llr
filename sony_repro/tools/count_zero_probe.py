"""`count == 0` 时引擎写的是什么 —— 问捕获,别猜。

`rawnr_simd.filt` 在没有抽头通过阈值时返回 0(`total / max(count, 1)`)。转录时
把它当作"如实保留",单测也只是记录它。**那是错的判断**:在真实照片上它开火在
0.45% 的像素上,把它们写成 raw level 0 —— 比黑电平还黑,图上就是一地黑点
(`sony_nr_audit.py`)。平坦块的频谱和结构块的细节量都看不见这个,所以它一路
活到了默认设置里。

捕获(`rawnr_kern_probe.py` 抓的 `ref`/`out` 同源)是能直接回答这个的:找出我这边
`count == 0` 的位置,看引擎在同一批位置上写了什么。四种候选,读数完全不同:

  * 写 0                 -> 与当前转录相同,那 0.45% 的黑点就是引擎自己的行为
  * 写中心值 `centre`     -> sigma 滤波器的常规做法(中心抽头无条件计入)
  * 写 `base`            -> 混合基准本身
  * NaN / 未定义          -> SIMD 除零,那就得看它后续怎么被钳位

⚠️ 这份捕获是平场测试片,可能一个 `count == 0` 都没有。那本身就是个结论:
说明**捕获从未覆盖过这条分支**,当初"端到端解释 100%"的成绩里不包含它,
所以那个成绩从来就不能为这条分支背书。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")

HERE = os.path.dirname(os.path.abspath(__file__))
S, TAPS = 2, 2
FILT_MARGIN = S * TAPS
W = 5


def shifted(a, dy, dx, r):
    h, w = a.shape
    return a[r + dy:h - r + dy, r + dx:w - r + dx]


def count_and_parts(d, ref, tbl0, tbl3, offset):
    """复算 `filt` 的内部量,重点是 `count` 与两个候选参考值。"""
    r = FILT_MARGIN
    thr_c = shifted(tbl0[np.clip(ref, 0, 16383).astype(np.int32)].astype(np.float32), 0, 0, r)
    m9 = sum(shifted(ref, dy, dx, r) for dy in (-S, 0, S) for dx in (-S, 0, S)) / np.float32(9.0)
    centre = shifted(ref, 0, 0, r)
    w = np.float32(float(tbl3[0]) / 1024.0)
    base = w * centre + (np.float32(1.0) - w) * m9
    total = np.zeros_like(base)
    count = np.zeros_like(base)
    for dy in [k * S for k in (-2, -1, 0, 1, 2)]:
        for dx in [k * S for k in (-2, -1, 0, 1, 2)]:
            v = shifted(ref, dy, dx, r)
            ok = (np.abs(base - v) < thr_c).astype(np.float32)
            total += ok * v
            count += ok
    return count, total, base, centre, thr_c


def main():
    for cap, name in (("rawnr_kern_fl_test_rb_s0w0.npz", "R/B"),
                      ("rawnr_kern_fl_test_g_s0w0.npz", "绿")):
        z = np.load(os.path.join(HERE, cap))
        d, ref, out = z["detail"], z["ref"], z["out"]
        tbl0, tbl3 = z["tbl0"], z["tbl3"]
        gain, limit = int(z["gain"][0]), int(z["limit"][0])
        off = float(z["offset"][0])
        h, wd = ref.shape

        count, total, base, centre, thr_c = count_and_parts(d, ref, tbl0, tbl3, off)
        sl = (slice(W - FILT_MARGIN, h - W - FILT_MARGIN),
              slice(W - FILT_MARGIN, wd - W - FILT_MARGIN))
        eng = out[W:h - W, W:wd - W]
        cnt = count[sl]
        zero = cnt == 0

        print(f"\n=== {cap}  ({name},offset={off:.0f})")
        print(f"  比对区 {eng.shape}  count 分布: 最小 {cnt.min():.0f} "
              f"中位 {np.median(cnt):.0f} 最大 {cnt.max():.0f}")
        print(f"  count == 0 的点:{int(zero.sum())} / {zero.size} "
              f"({100 * zero.mean():.4f}%)")
        if not zero.any():
            print("  ⚠️ 这份捕获**一个都没有** —— 那条分支从未被验证过,"
                  "「端到端解释 100%」不为它背书。")
            continue
        boost = np.clip(shifted(d, 0, 0, FILT_MARGIN) * np.float32(gain / 256.0),
                        -limit, limit)[sl]
        got = eng[zero]
        for label, cand in (("0", np.zeros_like(got)),
                            ("centre", centre[sl][zero]),
                            ("base", base[sl][zero])):
            pred = np.clip(cand + boost[zero] - np.float32(off), 0.0, 16383.0)
            same = float(np.mean(pred == got)) * 100
            print(f"  候选 {label:<8} 逐位相同 {same:7.3f}%  "
                  f"|误差| 中位 {float(np.median(np.abs(pred - got))):.4g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
