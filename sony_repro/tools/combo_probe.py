r"""多个机内滑块同时开时,引擎怎么合成 —— 增量相加还是顺序作用?

三个字段各自的形状都是**单独**测的(look_sweep.py 一次只动一个),组合情形一直
是靠假设。原来的 `apply_tuning` 把增量直接相加,这里把它和「顺序作用」一起对实测比。

结论(FL,ILCE-7CM2,详见 PIPELINE.md 6.2.2):

* Highlights+9 与 Shadows−9:两种模型都精确(差 0)—— 它们作用在不相交的亮度
  区间,这一组区分不了模型。
* Highlights+9 与 Contrast+5:顺序作用差 111,相加差 152,组合幅度 1962。
  **两个都不对。**

> **这个工具的结论仍然有效,当时对它的解释已被推翻。** 当时猜「正对比度是自适应的」,
> 错了。后来在 Edit.exe 里读到引擎的构造(PIPELINE.md 6.2.3):三个滑块**先相加成
> 一个 gain**,再造**唯一一张**表。既然没有先后,复合与相加当然都对不上 —— 111 不是
> 某个算子的性质,是这两个模型的形状都错了。主管线现在两个都不用,111 已归零。
>
> 留着它是因为「两个都不对」这条实测本身是路标:正因为**没有**任何一种合成方式
> 能对上,才逼出了「根本不是合成」这个问题。

用法(Windows 的 python,要有 Edit.exe 和 frida)::

    python combo_probe.py [<ARW>]
"""
import os
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import look_sweep as LS  # noqa: E402

SCR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ARW = os.path.join(SCR, "..", "..", "samples", "DSC01157.ARW")
OUT = os.path.join(SCR, "comboprobe")
LOOK = "FL"
# 一个组合要三次渲染:两个单独的当基准,一个合起来的当答案。
JOBS = [
    ("base", {}),
    ("h+9", {"highlights": 9}),
    ("s-9", {"shadows": -9}),
    ("h+9_s-9", {"highlights": 9, "shadows": -9}),
    ("c+5", {"contrast": 5}),
    ("h+9_c+5", {"highlights": 9, "contrast": 5}),
]
PAIRS = [("h+9_s-9", "h+9", "s-9"), ("h+9_c+5", "h+9", "c+5")]
N = 8193


def capture(arw):
    print("机身:", subprocess.run(["exiftool", "-s3", "-Model", arw],
                                capture_output=True, text=True).stdout.strip())
    os.makedirs(OUT, exist_ok=True)
    offs, names = LS.find_offsets(arw), LS.look_names(arw)
    i = LS.look_codes(names).index(LOOK)
    for tag, tune in JOBS:
        dst = os.path.join(OUT, f"{LOOK}_{tag}.npy")
        if os.path.exists(dst):
            continue
        got = LS.render(arw, offs, names, i, tune or None)
        if got is None:
            raise SystemExit(f"{tag}: 没抓到 tone LUT")
        np.save(dst, got)
        print(f"  {tag:10} 中调={got[1024]:6d}", flush=True)


def curve(tag):
    return np.load(os.path.join(OUT, f"{LOOK}_{tag}.npy")).astype(np.float64)[:N]


def operator(tweaked, base):
    """从一对(基线, 单字段结果)提出输出域算子的增量 U(y)-y。"""
    order = np.argsort(base, kind="stable")
    uniq, first = np.unique(base[order], return_index=True)
    delta = (tweaked - base)[order][first]
    return lambda y: np.interp(np.clip(y, uniq[0], uniq[-1]), uniq, delta)


def main():
    arw = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ARW)
    capture(arw)
    base = curve("base")
    print("\n对实测的最大差(/16384):")
    for combo, a, b in PAIRS:
        ca, cb, truth = curve(a), curve(b), curve(combo)
        Ub = operator(cb, base)
        Ua = operator(ca, base)
        print(f"  {combo}")
        print(f"    增量相加        {np.abs(base + (ca - base) + (cb - base) - truth).max():7.1f}")
        print(f"    顺序作用(先{a:5}) {np.abs(ca + Ub(ca) - truth).max():7.1f}")
        print(f"    顺序作用(先{b:5}) {np.abs(cb + Ua(cb) - truth).max():7.1f}")
        print(f"    (组合幅度 {np.abs(truth - base).max():.0f})")


if __name__ == "__main__":
    main()
