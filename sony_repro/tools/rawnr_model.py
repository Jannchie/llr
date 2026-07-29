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

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "apps", "worker", "src"))

from llr_worker.sony.rawnr import (  # noqa: E402
    BASE_COEFF_TAG,
    SLOPE_COEFF_TAG,
    STRENGTH_TAGS,
    noise_model,
)
from rawnr_ref import threshold_table  # noqa: E402
from sony_repro.sr2 import _decrypted_sr2, read_ifd  # noqa: E402

# 出厂模块只建阈值曲线,不碰 GAIN / LIMIT —— 那是滤波器的参数,不是噪声模型的,
# 所以这两组标签留在这里读。
GAIN = (0x78CC, 0x78CD, 0x78CE)
LIMIT = (0x78CF, 0x78D0, 0x78D1)
PROBES = (0, 256, 1024, 2048, 8192)


def model(path, plane=0):
    """lo/hi/base/slope **由 `llr_worker.sony.rawnr` 算**,这里只补它不管的项。

    这样这张 ISO 横扫就不是另一份平行实现,而是对出货那份的实测检查 ——
    改坏了 `rawnr.py`,这里会跟着变。
    """
    nm = noise_model(path)
    if nm is None:
        raise SystemExit(f"{path}:读不到噪声模型标签")
    dec, sub, endian = _decrypted_sr2(path)
    f = read_ifd(dec, sub, endian)
    return {
        "lo": nm.lo, "hi": nm.hi, "base": nm.base, "slope": nm.slope,
        "strength": f[STRENGTH_TAGS[plane]],
        "base_k": f[BASE_COEFF_TAG], "slope_k": f[SLOPE_COEFF_TAG],
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
