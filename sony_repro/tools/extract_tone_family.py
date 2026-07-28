r"""把 Edit.exe 里那 37 条色调算子曲线导给 worker。

引擎不是「每个滑块一条实测形状」,而是一族静态曲线 + 一个由滑块算出来的下标:

    gain_lo = 18 + contrast − shadows      作用在算子表 [0, 471)
    gain_hi = 18 + contrast + highlights   作用在算子表 [471, 1025)

`FAM[18]` 逐项等于 `i*64`,也就是恒等 —— 三个滑块全零时表恒等。这条是整套解读
最硬的自证,所以这里当断言写死。

值域 0..65535,存成 uint16 正好,不必用 int32。

用法::

    python extract_tone_family.py [--write]
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pe_scan as P  # noqa: E402

RVA, N, LEN = 0x484700, 37, 1025
IDENTITY = 18          # FAM[18] 就是恒等,这条是整套解读的自证
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "..", "apps", "worker", "src", "llr_worker",
                   "sony", "data", "tone_family.npz")


def family():
    """直接从 Edit.exe 里读那 37 条曲线,顺带把该成立的都断言掉。

    这里是曲线族的**唯一**归属地 —— RVA 是逐版本的,抄成两份就一定会有一份过期,
    而过期的那份会以「模型崩了」的样子报错。`tone_verify.py` 从这里 import。
    """
    blob = P.load()
    off = P.rva_to_off(blob, RVA)
    fam = np.frombuffer(blob[off:off + N * LEN * 4], dtype="<i4").reshape(N, LEN)

    assert fam.min() >= 0 and fam.max() <= 65535, "值域应当落在 uint16 内"
    ident = np.arange(LEN) * 64
    # 末项 i*64 = 65536 越界,引擎存的是 65535
    assert np.array_equal(fam[IDENTITY][:-1], ident[:-1]), "FAM[18] 前 1024 项必须是 i*64"
    assert fam[IDENTITY][-1] == 65535, "FAM[18] 末项应是 65535"
    assert (np.diff(fam, axis=1) >= 0).all(), "每条都应当单调不减"
    return fam


def main():
    fam = family()
    slope = fam[:, 1].astype(float) / 64.0
    print(f"{N} 条 x {LEN} 项,值域 {fam.min()}..{fam.max()}")
    print("低端斜率(相对恒等):")
    for k in sorted({*range(0, N, 4), IDENTITY}):
        print(f"  k={k:2d} {slope[k]:5.2f}" + ("   <- 恒等" if k == IDENTITY else ""))
    print("\n注意族的间距**不均匀**:k>18 每步约 "
          f"{abs(fam[IDENTITY + 1, 1] - fam[IDENTITY, 1])},k<18 每步约 "
          f"{abs(fam[IDENTITY - 1, 1] - fam[IDENTITY, 1])}。"
          "\n正对比度「非线性」和「跨机身不一致」都是这个不均匀造成的,不是两回事。")

    if "--write" not in sys.argv:
        print("\n(只看不写。加 --write 才导出)")
        return
    np.savez_compressed(OUT, family=fam.astype(np.uint16))
    print(f"\n-> {os.path.abspath(OUT)}  {os.path.getsize(OUT) / 1024:.0f} KB")


if __name__ == "__main__":
    main()
