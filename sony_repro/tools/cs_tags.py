r"""读 ChromaSuppres 用到的 SR2 根层标签:0x74a5(ISO 轴)、0x787e[3]、0x787f[3]、0x7880、0x7881。

    bash run_py.sh cs_tags.py <ARW> [<ARW> ...]
"""
import struct
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from llr_worker.sony import sr2  # noqa: E402

TAGS = (0x74A5, 0x787E, 0x787F, 0x7880, 0x7881)


def read(path):
    dec, sub_pos, endian = sr2._decrypted_sr2(path)
    out = {}
    for tag, typ, cnt, vpos, _ in sr2._ifd_entries(dec, sub_pos, endian):
        if tag in TAGS:
            if typ in (5, 10):  # RATIONAL / SRATIONAL
                f = "I" if typ == 5 else "i"
                v = struct.unpack_from(f"{endian}{2 * cnt}{f}", dec, vpos)
                out[tag] = [(v[2 * i], v[2 * i + 1]) for i in range(cnt)]
                continue
            fmt = sr2._SCALAR_FMT.get(typ)
            if fmt is None:
                out[tag] = ("?", typ, cnt)
                continue
            out[tag] = list(struct.unpack_from(f"{endian}{cnt}{fmt}", dec, vpos))
    return out


def main():
    for p in sys.argv[1:]:
        print(p)
        for t, v in sorted(read(p).items()):
            print(f"  0x{t:04x}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
