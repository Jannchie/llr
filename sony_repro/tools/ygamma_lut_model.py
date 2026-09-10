r"""YGamma 的 LUT(calib+0x318fc)随外观变:用每个外观的 0x780c / 0x780d 建模,对 dump 逐位核。

模型(见 measured-chroma-gap §2.25):
    knee  = 2 * 0x780c            # Y 的 0..16383 尺度;0x780c 在 8192=白 的尺度上
    slope = 0x780d / 16384
    以 16 为步长的 1024 项表:t[k] = knee/16 + trunc((k - knee/16) * slope)   (k ≥ knee/16),否则 k
    lut[i] = t[i >> 4] * 16 + (i & 15)  再钳到 16383
低端那个 −16 的「趾」不建模(≤16/16383)。

    bash -lc "cd ~/llr/apps/worker && uv run python ../../sony_repro/tools/ygamma_lut_model.py /tmp/DSC03036.ARW cs_export_DSC03036-cs2.npz 0 cs_export_fl_test_iso1250-cs.npz 4"
参数:ARW,然后成对的 <dump npz> <look index>。
"""
import os
import struct
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np  # noqa: E402

from llr_worker.sony import sr2  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FMT = {3: "H", 8: "h", 9: "i", 4: "I", 1: "B", 2: "s", 7: "s"}


def look_params(path):
    dec, sub, endian = sr2._decrypted_sr2(path)
    _t, cnt, vpos, _ = sr2._find_tag(dec, sub, endian, sr2.LOOK_INDEX_TAG)
    offs = struct.unpack_from(f"{endian}{cnt}I", dec, vpos)
    rows = []
    for off in offs:
        tags = {t: (typ, c, vp) for t, typ, c, vp, _ in sr2._ifd_entries(dec, off, endian)}

        def rd(t):
            typ, c, vp = tags[t]
            if typ in (2, 7):
                return (dec[vp:vp + c],)
            return struct.unpack_from(f"{endian}{c}{FMT[typ]}", dec, vp)
        name = rd(0x7770)[0].split(b"\x00")[0].decode(errors="replace") if 0x7770 in tags else "?"
        rows.append(dict(name=name, c=rd(0x780C)[0], d=rd(0x780D)[0], e=rd(0x780E)[0], b=rd(0x780B)[0] if 0x780B in tags else None))
    return rows


def model_lut(c, d):
    knee16 = (2 * c) >> 4
    slope = d / 16384.0
    k = np.arange(1024)
    t = np.where(k >= knee16, knee16 + np.trunc((k - knee16) * slope), k).astype(np.int64)
    i = np.arange(16384)
    return np.minimum(t[i >> 4] * 16 + (i & 15), 16383)


def main():
    rows = look_params(sys.argv[1])
    print("looks:")
    for k, r in enumerate(rows):
        print(f"  {k} {r['name']:<9} 0x780c={r['c']:5d} 0x780d={r['d']:5d} 0x780e[0]={r['e']:5d} 0x780b[0]={r['b']}")
    args = sys.argv[2:]
    for j in range(0, len(args), 2):
        z = np.load(os.path.join(HERE, args[j]))
        lut = z["lut_ygam"].astype(np.int64)[:16384]
        r = rows[int(args[j + 1])]
        m = model_lut(r["c"], r["d"])
        d = lut - m
        bad = np.nonzero(d)[0]
        print(f"{args[j]} vs model(look {args[j + 1]} {r['name']}): 逐位 {np.mean(d == 0) * 100:.3f}%  max|d| {np.abs(d).max()}  "
              f"差异区间 {(int(bad.min()), int(bad.max())) if bad.size else '-'}  ≥8192 逐位 {np.mean(d[8192:] == 0) * 100:.3f}%  |d|>16 的像素 {(np.abs(d) > 16).sum()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
