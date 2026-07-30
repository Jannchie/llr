r"""读 Edit.exe 里任意 VA 上的 qword 表或 float32/int32 常量。

`dump_vtab.py` 走 pyghidra,而 Ghidra 已经不在了。要回答「`*this + 0x150` 是哪个
函数」「`[rip+0x1800ad]` 是几」这两类问题,pefile 就够 —— 前者查 vftable 的槽,
后者解常量池。反汇编里的位移能直接查到函数名/数值,读核的速度差一个量级。

**必须用 Windows 的 Python 跑**(pefile 装在那边):
    python read_rdata.py 0x1404667b8 --slots 10 --from 0x140   # vftable 的槽
    python read_rdata.py 0x1404df118 --floats 4                # 常量池
"""
import struct
import sys

import pefile

BIN = r'C:\Program Files\Sony\Imaging Edge\Edit.exe'


def load():
    pe = pefile.PE(BIN, fast_load=True)
    base = pe.OPTIONAL_HEADER.ImageBase
    return [(s.Name.rstrip(b'\x00').decode('latin1'),
             base + s.VirtualAddress, s.get_data()) for s in pe.sections]


def read(secs, va, n):
    for nm, start, data in secs:
        if start <= va < start + len(data):
            o = va - start
            return nm, data[o:o + n]
    return None, b''


def opt(name, default):
    if name not in sys.argv:
        return default
    return int(sys.argv[sys.argv.index(name) + 1], 0)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        raise SystemExit(__doc__)
    va = int(args[0], 0)
    secs = load()

    if '--floats' in sys.argv:
        n = opt('--floats', 1)
        nm, raw = read(secs, va, n * 4)
        print(f'0x{va:x}  段 {nm}')
        for i in range(len(raw) // 4):
            b = raw[i * 4:i * 4 + 4]
            print('  0x%x  %-16.9g f32   %-12d i32   %s'
                  % (va + i * 4, struct.unpack('<f', b)[0],
                     struct.unpack('<i', b)[0], b.hex()))
        return

    off0 = opt('--from', 0)
    slots = opt('--slots', 0x20)
    nm, raw = read(secs, va + off0, slots * 8)
    print(f'0x{va:x} + 0x{off0:x}  段 {nm}')
    for i in range(len(raw) // 8):
        q = struct.unpack_from('<Q', raw, i * 8)[0]
        tgt, _ = read(secs, q, 1)
        tag = '  <- 代码' if tgt == '.text' else (f'  ({tgt})' if tgt else '')
        print(f'  +0x{off0 + i * 8:<5x} 0x{q:016x}{tag}')


if __name__ == '__main__':
    main()
