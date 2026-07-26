r"""列出 SR2SubIFD 根层和 DataIFD 的全部标签,或按值搜索。

起因:引擎的 `0x14036dce0` 把四个光源权重当作标定块 `+0xe44..0xe4a` 的 short 读,
而全二进制里**没有一处写这四个 short**(`scan_disp.py --write` 查过);
`0x1401ac7a0` 的序列化器又把 `+0xe3c` 起的一串 short 顺序倒进输出缓冲。
合起来说明这块结构是整段从文件灌进来的,那权重就该在 ARW 里,不是运行时算的。

用法:
    python tag_dump.py DSC02961                    # 列全部标签
    python tag_dump.py DSC02961 --find 1024 340    # 只列含这些值的标签
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")
from llr_worker.sony.sr2 import (  # noqa: E402
    LOOK_INDEX_TAG, _decrypted_sr2, _find_tag, _ifd_entries,
)

SRC = Path("/mnt/e/10960725")
FMT = {1: "B", 2: "s", 3: "H", 4: "I", 6: "b", 7: "B", 8: "h", 9: "i", 11: "f", 12: "d"}


def values(dec, endian, typ, cnt, vpos, size):
    f = FMT.get(typ)
    if f is None or vpos + size > len(dec):
        return None
    if f == "s":
        return bytes(dec[vpos:vpos + size]).split(b"\x00")[0].decode("ascii", "replace")
    return list(struct.unpack_from(f"{endian}{cnt}{f}", dec, vpos))


def dump(dec, pos, endian, label, find):
    print(f"\n=== {label} @ 0x{pos:x}")
    for tag, typ, cnt, vpos, size in _ifd_entries(dec, pos, endian):
        v = values(dec, endian, typ, cnt, vpos, size)
        if find is not None:
            if not isinstance(v, list) or not any(x in find for x in v):
                continue
            print(f"  0x{tag:04x} type={typ} n={cnt}  ** {v}")
        else:
            shown = v if not isinstance(v, list) or len(v) <= 16 else v[:16] + ["..."]
            print(f"  0x{tag:04x} type={typ} n={cnt:<5} {shown}")


def main():
    stem = sys.argv[1]
    find = {int(a) for a in sys.argv[3:]} if "--find" in sys.argv else None
    dec, sub_pos, endian = _decrypted_sr2(SRC / f"{stem}.ARW")
    dump(dec, sub_pos, endian, f"{stem} SR2SubIFD 根层", find)

    e = _find_tag(dec, sub_pos, endian, LOOK_INDEX_TAG)
    if e:
        _, cnt, vpos, _ = e
        for i, o in enumerate(struct.unpack_from(f"{endian}{cnt}I", dec, vpos)[:2]):
            dump(dec, o, endian, f"{stem} DataIFD[{i}]", find)


if __name__ == "__main__":
    main()
