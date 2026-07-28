r"""把 `passes.ts` 的 Sharpness 段和引擎的定点算子对拍。

和 `sharp_check.py` 的分工:那个验证「解出来的算子 == 引擎」,这个验证
「搬进 shader 的那份 == 解出来的算子」。两个问题不一样,Clarity 那次就是
第二问出的错(半个 texel 的错位吃掉 17% 的效果,肉眼完全看不出来)。

三份实现:

* `engine(y16)`  —— 49 taps 的显式核,`tools/sharp_check.py` 的同一份,
  在 0..16383 的整数平面上跑,`floor` 取整。
* `shader(y)`    —— GLSL 那份的逐行转写:归一化到 [0,1],二项式拆成外积
  `Σ_j BIN[j]·(Σ_i BIN[i]·s) / 4096`,四邻单独累加,没有 floor。
* 差异的两个来源因此是**已知且各自可算的**:分解写法的浮点噪声,和省掉的
  floor(一个 1/16383 的量化,比 8-bit 成品细 64 倍)。

跑法(WSL 的 python 即可):
    python sharp_shader_check.py [tiles_SIMDSharpness.npz]
"""
import sys
from pathlib import Path

import numpy as np

SCR = Path(__file__).parent
sys.path.insert(0, str(SCR))
# 参考实现直接用 sharp_check 的那一份 —— 它是对着引擎的真实 tile 验过 99.987% 的
# 那个,再抄一遍就等于给自己造了个可以独自漂移的第二基准:改错一个 tap,这边照样
# 报绿,而这个脚本存在的全部意义就是抓这种事。
from sharp_check import BINOM as BIN  # noqa: E402
from sharp_check import highpass  # noqa: E402

# 引擎平面的满量程,以及死区阈值 1024 换算到归一化亮度。
PLANE_WHITE = 16383.0
DEADZONE = 40.96 * 25 / PLANE_WHITE

# shader 那份的三个权重(apps/web/.../passes.ts 的 SHARPEN_*)。它们和 sharp_check
# 的显式核是同一个算子的两种写法,本脚本要验的正是这一点,所以这里必须独立写出来。
CENTER, BLUR_W, NEAR_W = 25.6, 10.24, 3.84


def _pad(y):
    return np.pad(y, 3, mode="edge")          # GL 的 CLAMP_TO_EDGE 就是 replicate


def engine(y16, amp, c=25.0):
    """引擎那份:整数平面 + floor + 硬死区。核来自 sharp_check。"""
    hp = highpass(y16.astype(np.float64))
    d = np.floor(amp * hp)
    d[np.abs(hp) < 40.96 * c] = 0
    return y16 + d


def shader(y, amp, deadzone=DEADZONE):
    """GLSL 那份:归一化、外积分解、无 floor。"""
    pad = _pad(y)
    h, w = y.shape
    blur = np.zeros_like(y)
    near = np.zeros_like(y)
    for j in range(-3, 4):
        row = np.zeros_like(y)
        for i in range(-3, 4):
            s = pad[3 + j:3 + j + h, 3 + i:3 + i + w]
            row += BIN[i + 3] * s
            if abs(i) + abs(j) == 1:
                near += s
        blur += BIN[j + 3] * row
    hp = CENTER * y - BLUR_W * (blur / 4096.0) - NEAR_W * near
    d = np.where(np.abs(hp) < deadzone, 0.0, amp * hp)
    return y + d


def report(name, ours, ref, scale):
    """ours/ref 都在 ref 的量纲上;scale 是「一个 8-bit 级差」有多大。"""
    diff = np.abs(ours - ref)
    print(f"  {name:<34} 最大 {diff.max():9.5f}   RMS {np.sqrt((diff ** 2).mean()):9.6f}"
          f"   = {diff.max() / scale:6.3f} / {np.sqrt((diff ** 2).mean()) / scale:.4f} 个 8-bit 级")


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else SCR / "tiles_SIMDSharpness.npz"
    amp = 85 / 1024                                   # +4 / +3,calib 1.7 —— 机内默认
    z = np.load(path)
    tiles = sorted(int(k[1:].split("_")[0]) for k in z if k.endswith("_in"))
    step = PLANE_WHITE / 255.0                        # 一个 8-bit 级差,在 14-bit 平面上
    print(f"amp = {amp}   死区 = {DEADZONE:.8f}(归一化) = {DEADZONE * PLANE_WHITE:.1f}(平面)")
    print(f"{len(tiles)} 块引擎真实 tile,内缩 8 像素避开边界\n")

    for t in tiles:
        y16 = z[f"t{t}_in"][..., 0].astype(np.float64)
        eng_out = z[f"t{t}_out"][..., 0].astype(np.float64)
        m = np.zeros(y16.shape, bool)
        m[8:-8, 8:-8] = True

        ref = engine(y16, amp)                        # 解出来的算子,未钳位
        our = shader(y16 / PLANE_WHITE, amp) * PLANE_WHITE   # 搬进 shader 的那份

        # 成品要各自钳位之后才可比。引擎钳到 32767(`DAT_1404667a0`),shader 钳到
        # 1.0 = 16383,因为它写进的是 8-bit 帧 —— 高于满白的过冲反正都是 255。
        # 不钳就比,差的是钳位而不是算子(会看到几百的「最大差」)。
        ref_c = np.clip(ref, 0, 32767)
        our_c = np.clip(our, 0, PLANE_WHITE)

        print(f"tile {t}  ({y16.shape[0]}x{y16.shape[1]}, "
              f"引擎改动了 {100 * ((eng_out - y16)[m] != 0).mean():.1f}% 的像素,"
              f"自身效果 RMS {np.sqrt((((eng_out - y16)[m]) ** 2).mean()) / step:.3f} 个 8-bit 级;"
              f"引擎输出最大 {eng_out.max():.0f})")
        report("shader vs 解出的算子(纯算子)", our[m], ref[m], step)
        report("shader vs 引擎输出(各自钳位后)", our_c[m], eng_out[m], step)
        report("解出的算子 vs 引擎输出(同上)", ref_c[m], eng_out[m], step)
        # 死区两边的像素分类必须完全一致 —— 一个硬阈值上的分歧不是舍入,
        # 是公式搬错了。这条比上面的 RMS 更容易抓到错位。
        same_zone = ((our - y16 == 0) == (ref - y16 == 0))[m].mean()
        print(f"  死区判定一致                       {100 * same_zone:.4f}%\n")


if __name__ == "__main__":
    main()
