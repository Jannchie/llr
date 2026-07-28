r"""横比 vatr_param.py 抓下来的参数块:哪几格随图变?

`DynamicRangeOptimizer=Auto` 时档位由引擎按画面自定,相机没记。所以要找的是
「随画面变的那一格」。全同的格子是标定,不用管。

用法: python vatr_diff.py [vatrparam 目录]
"""
import os
import struct
import sys

SCR = os.path.dirname(os.path.abspath(__file__))


def main():
    d = sys.argv[1] if len(sys.argv) > 1 else os.path.join(SCR, "vatrparam")
    names = sorted(f for f in os.listdir(d) if f.endswith(".bin"))
    blobs = {}
    for f in names:
        with open(os.path.join(d, f), "rb") as fh:
            blobs[f[:-4]] = fh.read()
    n = min(len(b) for b in blobs.values())
    stems = list(blobs)
    print("文件:", stems)
    print(f"\n{'偏移':>8} " + " ".join("%-14s" % s for s in stems))
    ndiff = 0
    for o in range(0, n - 3, 4):
        vals_i = [struct.unpack_from("<i", b, o)[0] for b in blobs.values()]
        vals_f = [struct.unpack_from("<f", b, o)[0] for b in blobs.values()]
        if len(set(vals_i)) == 1:
            continue
        ndiff += 1
        def fmt(i, f):
            if i == 0:
                return "0"
            if 1e-6 < abs(f) < 1e8:
                return "%.6g" % f
            return "%d" % i
        print(f"  +0x{o:04x} " + " ".join("%-14s" % fmt(i, f)
                                          for i, f in zip(vals_i, vals_f)))
    print(f"\n共 {ndiff} 个 dword 有差异(总 {n // 4} 个)")


if __name__ == "__main__":
    main()
