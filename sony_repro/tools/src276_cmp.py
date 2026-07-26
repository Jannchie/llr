r"""引擎真正解包的那 276 字节 vs ARW 里十种外观的 tag 0x780f 载荷。

解包函数 `+0x179390` 从 `param_2 + 0xae` 读 23x12=276 字节(`tools/src276_probe.py` 抓)。
若与某个外观的 `0x780f` 逐字节相同,那位流解读和数据来源就都没问题,
剩下的差异只能出在别处;若不同,差在哪一字节一目了然。

用法: python src276_cmp.py <ARW> <src276.npz>
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sony_repro.sr2 import data_ifds  # noqa: E402

LOOK_ORDER = ("ST", "VV", "NT", "PT", "FL", "VV2", "IN", "SH", "BW", "SE")
PARAM_TAG = 0x780F


def main():
    arw = Path(sys.argv[1])
    z = np.load(sys.argv[2])
    eng = z["block_0"]
    print("引擎读到的 276 字节  前 24: %s" % eng[:24].tolist())

    ifds = data_ifds(arw)
    for i, name in enumerate(LOOK_ORDER):
        v = ifds[i].get(PARAM_TAG)
        if v is None:
            print("  %-3s 没有 0x780f" % name)
            continue
        a = np.frombuffer(bytes(v), dtype=np.uint8)
        n = min(len(a), len(eng))
        same = int((a[:n] == eng[:n]).sum())
        first = int(np.argmax(a[:n] != eng[:n])) if same < n else -1
        print("  %-3s 长度 %3d  相同 %3d/%d%s"
              % (name, len(a), same, n,
                 "  **逐字节相同**" if same == n else "   首个不同在第 %d 字节 (%d vs %d)"
                 % (first, a[first], eng[first])))

    # 两次调用读到的是不是同一份
    if "block_1" in z:
        print("\n两次调用读到的字节%s" % ("相同" if np.array_equal(eng, z["block_1"]) else "不同"))


if __name__ == "__main__":
    main()
