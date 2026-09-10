r"""对比两个外观的 SR2DataIFD 标签,找哪几个随外观变(用来定位 YGamma LUT 的来源)。

    bash -lc "cd ~/llr/apps/worker && uv run python ../../sony_repro/tools/look_tag_diff.py /tmp/DSC03036.ARW 0 4"
"""
import struct
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np  # noqa: E402

from llr_worker.sony import sr2  # noqa: E402

FMT = {1: "B", 3: "H", 4: "I", 6: "b", 8: "h", 9: "i"}


def dump(dec, pos, endian):
    out = {}
    for tag, typ, cnt, vpos, _size in sr2._ifd_entries(dec, pos, endian):
        fmt = FMT.get(typ)
        if fmt is None:
            out[tag] = (typ, cnt, None)
            continue
        out[tag] = (typ, cnt, struct.unpack_from(f"{endian}{cnt}{fmt}", dec, vpos))
    return out


def main():
    path, ia, ib = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    dec, sub, endian = sr2._decrypted_sr2(path)
    _typ, cnt, vpos, _ = sr2._find_tag(dec, sub, endian, sr2.LOOK_INDEX_TAG)
    offs = struct.unpack_from(f"{endian}{cnt}I", dec, vpos)
    a, b = dump(dec, offs[ia], endian), dump(dec, offs[ib], endian)
    print(f"look {ia} tags: {[hex(t) for t in a]}")
    for t in a:
        va, vb = a[t], b.get(t)
        if vb is None:
            print(f"  {hex(t)} only in look {ia}")
            continue
        if va[2] != vb[2]:
            sa = list(va[2][:16]) if va[2] else None
            sb = list(vb[2][:16]) if vb[2] else None
            print(f"  {hex(t)} type {va[0]} x{va[1]} DIFFERS")
            print(f"      look {ia}: {sa}{' ...' if va[1] > 16 else ''}")
            print(f"      look {ib}: {sb}{' ...' if vb[1] > 16 else ''}")
            if va[2] and va[1] >= 8:
                arr = np.array(va[2])
                print(f"      look {ia} min/max {arr.min()}/{arr.max()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
