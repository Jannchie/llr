r"""验证静态那边给的 `calib[0x1060]` 来源:RAW 标签 `0x78cd`。

`notes/static-sharpness.md` §4 从标定解析器 `FUN_14015f540` 读出:

    calib[0x104c] = tag 0x78cd
    if (calib[0x104c] > 0x100):
        calib[0x1060] = (calib[0x104c] - 0x100) * 0.003125 + 1.0      # 0.003125 = 1/320

我动态实测的 `tbl[0x1060]` 恰好全是 1/320 的整数倍(见 notes/dynamic-sharpness.md §3.2),
当时只能拟合出一条随 ISO 下降的经验曲线。这里直接拿 ARW 里的 `0x78cd` 复算,
对得上就不需要那条拟合曲线了。

用法(WSL 的 python 即可):
    python sharp_verify.py
"""
import os
import sys

import numpy as np

SCR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(SCR))
from sony_repro.sr2 import read_sr2_tag  # noqa: E402

SRC = ["/mnt/e/temp_photo", "/mnt/e/temp_photo/10160623", "/mnt/e/temp_photo/10760723",
       "/mnt/e/10960725"]

# sharp_amp.py 实测的 tbl[0x1060](图 -> 值)
MEASURED = {
    "DSC02857": 1.7000000476837158, "DSC02954": 1.0, "DSC02865": 1.6968750953674316,
    "DSC06800": 1.7000000476837158, "DSC02947": 1.212499976158142,
    "DSC03015": 1.6906249523162842, "DSC02961": 1.0,
    "DSC02970": 1.7000000476837158, "DSC02977": 1.6062500476837158,
    "DSC02968": 1.553125023841858, "DSC02995": 1.4406249523162842,
    "DSC02981": 1.399999976158142, "DSC02971": 1.306249976158142,
    "DSC03007": 1.2374999523162842, "DSC03006": 1.2062499523162842,
    "DSC02965": 1.1218750476837158, "DSC02963": 1.0750000476837158,
    "DSC02964": 1.021875023841858, "DSC02859": 1.6687500476837158,
    "DSC02953": 1.368749976158142, "DSC02868": 1.321874976158142,
    "DSC02867": 1.0968749523162842,
}


def find_arw(stem):
    for d in SRC:
        p = os.path.join(d, stem + ".ARW")
        if os.path.exists(p):
            return p
    return None


def main():
    print(f"{'图':<11} {'tag 0x78cd':>11} {'复算':>10} {'实测':>10} {'差':>11}")
    bad = 0
    for stem, meas in sorted(MEASURED.items()):
        arw = find_arw(stem)
        if not arw:
            print(f"{stem:<11} 找不到 ARW")
            continue
        try:
            raw = read_sr2_tag(arw, 0x78CD)
        except Exception as e:
            print(f"{stem:<11} 读标签失败: {e}")
            continue
        v = int(np.frombuffer(raw, "<u2", count=1)[0]) if len(raw) >= 2 else None
        pred = (v - 0x100) * 0.003125 + 1.0 if v is not None and v > 0x100 else None
        if pred is None:
            print(f"{stem:<11} {v:>11} {'(<=256)':>10} {meas:>10.6f}   未写,保持初值")
            continue
        d = pred - meas
        bad += abs(d) > 1e-6
        print(f"{stem:<11} {v:>11} {pred:>10.6f} {meas:>10.6f} {d:>+11.7f}")
    print(f"\n不一致 {bad} 张")


if __name__ == "__main__":
    main()
