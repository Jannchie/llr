r"""**已作废,别用。** 结论见文末;取而代之的是 build_tuning_ops.py。

把捐赠机身独有外观(FL2/FL3)的微调形状并入 worker 的 look_tuning.npz。

**为什么要先比对再合并。** `look_tuning.npz` 里那十个是在 ILCE-7CM2 上测的;
FL2/FL3 只有 a7 V 才有,只能在捐赠机身上测。跨机身搬形状要有依据 ——
所以 sweep 时把 **FL 也一起测了**当对照组:如果同一个 FL 在两台机身上测出的
形状一致,说明这些形状是索尼的算子而不是逐机身标定,FL2/FL3 才敢搬。

`extract_donor_looks.py` 已经证明**色调曲线跨机身逐字节相同**(色度参数则不是)。
微调形状是引擎对同一条曲线做的增量,预期同理 —— 但预期不是证据,这里量给出来。

用法(Windows 的 python,先跑 look_sweep.py + export_tuning.py):
    python look_sweep.py <捐赠.ARW> --looks=FL,FL2,FL3
    python look_sweep.py <捐赠.ARW> --looks=FL,FL2,FL3 --fields=contrast \
        --values=-9,1,2,3,4,5,6,7,8,9
    python export_tuning.py look_tuning_a7v.npz --looks=FL,FL2,FL3
    python merge_donor_tuning.py [--write]

**为什么作废。** 对照组跑出来是分裂的:highlights/shadows 两个方向加 contrast
负向,两台机身差 0.07~0.57/16384(通用),但 contrast 正向差 182 —— 上面那个
`worst > 1.0` 的闸门会一票否决整批,而其中五个其实完全可以用。

顺着这条线查下去,发现整个前提就搭错了。微调根本不是「逐外观的形状」,而是作用在
外观曲线**之后**的一个输出域算子:把增量改成以基线输出值为自变量重采样,十个外观
塌到 0.15/16384(而它们各自的基线曲线彼此差到 3133)。所以 FL2/FL3 缺的不是
「别人家的形状」,而是「本来就不该按外观存」—— 换成算子之后它们自动就有了,
一个字节都不用搬。

留着这个文件是因为那次对照组本身是有价值的证据(它证明了五个算子跨机身通用,
也定位到 contrast 正向是唯一的例外),但它的合并动作已经是错的了。
"""
import os

import numpy as np

SCR = os.path.dirname(os.path.abspath(__file__))
DONOR = os.path.join(SCR, "look_tuning_a7v.npz")
# 逐外观的 7CM2 实测存档。worker 现在装的是算子而不是形状了,拿那个比不了。
SHIPPED = os.path.join(SCR, "look_tuning_perlook_7cm2.npz")
CONTROL = "FL"            # 两边都有的那个外观
NEW = ("FL2", "FL3")
SCALE = 16384.0           # 满刻度,报告用这个单位才和 export_tuning 对得上


def main():
    with np.load(DONOR) as z:
        donor = {k: z[k].astype(np.float64) for k in z.files}
    with np.load(SHIPPED) as z:
        shipped = {k: z[k] for k in z.files}

    print(f"捐赠 {len(donor)} 条,已有 {len(shipped)} 条\n")

    print(f"对照组 {CONTROL} —— 同一外观,两台机身各测一次:")
    worst = 0.0
    for key in sorted(k for k in donor if k.startswith(CONTROL + "_")):
        if key not in shipped:
            print(f"  {key:24} 已有文件里没有,无法对照")
            continue
        a, b = donor[key], shipped[key].astype(np.float64)
        if a.shape != b.shape:
            print(f"  {key:24} 形状不同 {a.shape} vs {b.shape}")
            worst = float("inf")
            continue
        d = np.abs(a - b).max() * SCALE
        amp = np.abs(b).max() * SCALE
        worst = max(worst, d)
        print(f"  {key:24} 最大差 {d:7.3f}   该形状幅度 {amp:7.1f}"
              f"   相对 {100 * d / max(amp, 1e-9):5.2f}%")
    print(f"\n对照组最大差 {worst:.3f}/16384")

    add = sorted(k for k in donor if k.split("_")[0] in NEW)
    print(f"\n捐赠机身独有的 {len(add)} 条: {add}")
    print("\n合并动作已删除 —— 见文件开头。这些形状不需要搬,"
          "\n用 build_tuning_ops.py 建的算子对所有外观通用。")


if __name__ == "__main__":
    main()
