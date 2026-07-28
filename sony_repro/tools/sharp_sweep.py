r"""机内锐度档位 -> 引擎参数的映射:改 MakerNotes 逐档跑一遍。

手上 87 张素材的 `Sony:0x2006 Sharpness` **全是 +4**,没有对照组,所以只能自造:
`patch_slider.patch` 改的是明文 int32,不动加密段也不重排 IFD。

已知落点(sharp_amp.py):
    Sony 0x2006 Sharpness      -> opts[0x208]  (同时也写进 obj[0x1ac])
    Sony 0x2035 SharpnessRange -> opts[0x2c4]

用法(Windows 的 Python):
    python sharp_sweep.py <源ARW> sharpness -9:9
    python sharp_sweep.py <源ARW> sharpnessrange -5:5
"""
import os
import sys

SCR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCR)
from patch_slider import patch          # noqa: E402
from sharp_amp import amp_of, probe     # noqa: E402

WORK = os.path.join(SCR, "sharpsweep_work.ARW")


def main():
    src, field, spec = sys.argv[1], sys.argv[2], sys.argv[3]
    if ":" in spec:
        lo, hi = (int(x) for x in spec.split(":"))
        vals = list(range(lo, hi + 1))
    else:
        vals = [int(x) for x in spec.split(",")]

    print(f"{'档位':>6} {'opts[0x208]':>12} {'opts[0x2c4]':>12} {'obj[0x1ac]':>11} "
          f"{'tbl[0x1060]':>12} {'amp':>10}")
    for v in vals:
        patch(src, WORK, {field: v}, None)
        g = probe(WORK)
        if not g:
            print(f"{v:>6}  没抓到", flush=True)
            continue
        print(f"{v:>6} {g['opts_0x208']:>12} {g['opts_0x2c4']:>12} {g['obj_0x1ac']:>11} "
              f"{g['tbl_0x1060']:>12.4f} {amp_of(g):>10.6f}", flush=True)


if __name__ == "__main__":
    main()
