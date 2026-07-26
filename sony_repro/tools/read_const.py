r"""按虚拟地址读 Edit.exe 里的常量(跨节,不限于 .text)。

RIP 相对的常量几乎都落在 .rdata,只映射 .text 的工具看不到它们。

必须用 Windows 的 Python 跑:
    python sony_repro/tools/read_const.py 0x1404df248 [个数] [f32|i32|u16|i16]
"""
import struct
import sys

import pefile

BIN = r'C:\Program Files\Sony\Imaging Edge\Edit.exe'
FMT = {'f32': ('<f', 4), 'f64': ('<d', 8), 'i32': ('<i', 4), 'u32': ('<I', 4),
       'i16': ('<h', 2), 'u16': ('<H', 2), 'u8': ('<B', 1)}


def image():
    pe = pefile.PE(BIN)
    buf = bytearray(pe.OPTIONAL_HEADER.SizeOfImage)
    for s in pe.sections:
        d = s.get_data()
        buf[s.VirtualAddress:s.VirtualAddress + len(d)] = d
    return pe.OPTIONAL_HEADER.ImageBase, bytes(buf)


def main():
    va = int(sys.argv[1], 0)
    n = int(sys.argv[2], 0) if len(sys.argv) > 2 else 1
    kind = sys.argv[3] if len(sys.argv) > 3 else 'f32'
    fmt, size = FMT[kind]
    base, img = image()
    off = va - base
    for i in range(n):
        (v,) = struct.unpack_from(fmt, img, off + i * size)
        print('0x%x[%d] = %s' % (va, i, repr(v)))


if __name__ == '__main__':
    main()
