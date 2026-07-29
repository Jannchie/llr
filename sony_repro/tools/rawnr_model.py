r"""把每张 ARW 自带的降噪噪声模型读出来,看它随 ISO 怎么走。

这批系数是**相机逐张写进 RAW 的**(随 ISO / 机型),不是 Edit.exe 算的 ——
所以「照搬」是免费的:不需要自己建噪声模型,直接读标签。
公式与 tag→calib 的映射见 `notes/static-rawnr.md`。

阈值的自变量是**局部低通电平**(不是中心像素),域 0..0x7fff;
阈值本身在引擎的 14 位刻度上。

用法::

    python rawnr_model.py <ARW> [<ARW> ...]
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rawnr_ref import threshold_table  # noqa: E402
from sony_repro.sr2 import _decrypted_sr2, read_ifd  # noqa: E402

LO, HI, BASE_K, SLOPE_K = 0x78C5, 0x78C6, 0x78C7, 0x78C8
STRENGTH = (0x78C9, 0x78CA, 0x78CB)
GAIN = (0x78CC, 0x78CD, 0x78CE)
LIMIT = (0x78CF, 0x78D0, 0x78D1)
PROBES = (0, 256, 1024, 2048, 8192)


def model(path, plane=0):
    dec, sub, endian = _decrypted_sr2(path)
    f = read_ifd(dec, sub, endian)
    strength = f[STRENGTH[plane]]
    return {
        "lo": f[LO], "hi": f[HI],
        "base": strength * f[BASE_K] * 3 >> 8,
        "slope": strength * f[SLOPE_K] * 3 >> 8,
        "strength": strength, "base_k": f[BASE_K], "slope_k": f[SLOPE_K],
        "gain": f[GAIN[plane]], "limit": f[LIMIT[plane]],
    }


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    rows = []
    for path in sys.argv[1:]:
        m = model(path)
        iso, body = subprocess.run(["exiftool", "-s3", "-ISO", "-Model", path],
                                   capture_output=True, text=True).stdout.split()
        table = threshold_table(m["lo"], m["hi"], m["base"], m["slope"])
        rows.append((int(iso), os.path.basename(path), body, m,
                     [int(table[v]) for v in PROBES]))

    print(f"{'ISO':>6} {'文件':16} {'机身':10} {'lo':>4} {'hi':>5} {'bk':>3} {'sk':>4} "
          f"{'str':>4} {'GAIN':>5} {'LIM':>5}   阈值 @ 电平 "
          + "/".join(str(v) for v in PROBES))
    for iso, name, body, m, thr in sorted(rows):
        print(f"{iso:>6} {name:16} {body:10} {m['lo']:>4} {m['hi']:>5} "
              f"{m['base_k']:>3} {m['slope_k']:>4} {m['strength']:>4} "
              f"{m['gain']:>5} {m['limit']:>5}   {thr}")
    print("\nGAIN 是细节增益(8.8 定点,256 = 原样),LIMIT 是补回细节时的光晕限幅。")


if __name__ == "__main__":
    main()
