r"""不用 Ghidra,直接从 PE 的 MSVC RTTI 里导出每个 ZcTask* 的执行函数 RVA。

Ghidra 那份(`dump_execs.py`)要一整套 Ghidra + JDK 才能跑,而这件事本身只是走一遍
RTTI:

    ".?AVZcTaskXxx@@" 字符串 -> TypeDescriptor(-0x10)
      -> CompleteObjectLocator(它的 +0x0c 是 TypeDescriptor 的 RVA)
      -> vftable(紧跟在指向该 COL 的那个指针之后)
      -> **slot 7(+0x38)就是执行函数**

产物 `task_execs.json` 是 stage_frame.py / stage_census.py 等一切活体工具的前提,
**要提交进仓库** —— 之前没提交,结果 Ghidra 一没了整条活体验证链全断。

必须用 Windows 的 Python 跑(要 pefile):
    python sony_repro/tools/dump_execs_pe.py
"""
import json
import os
import struct

import pefile

BIN = r'C:\Program Files\Sony\Imaging Edge\Edit.exe'
SCR = os.path.dirname(os.path.abspath(__file__))
EXEC_SLOT = 0x38
PREFIX = b'.?AVZcTask'


def main():
    pe = pefile.PE(BIN)
    base = pe.OPTIONAL_HEADER.ImageBase
    size = pe.OPTIONAL_HEADER.SizeOfImage
    img = bytearray(size)
    for s in pe.sections:
        d = s.get_data()
        img[s.VirtualAddress:s.VirtualAddress + len(d)] = d
    img = bytes(img)

    def u32(rva):
        return struct.unpack_from('<I', img, rva)[0]

    def u64(rva):
        return struct.unpack_from('<Q', img, rva)[0]

    # 1) 类名字符串 -> TypeDescriptor 的 RVA(名字在 TD 的 +0x10)
    types = {}
    pos = 0
    while (pos := img.find(PREFIX, pos)) != -1:
        end = img.find(b'\x00', pos)
        name = img[pos:end].decode('ascii', 'replace')
        cls = name[4:].split('@')[0]          # ".?AVZcTaskFoo@@" -> "ZcTaskFoo"
        types[pos - 0x10] = cls
        pos = end

    # 2) 扫全镜像找 COL:它的 +0x0c 是 TypeDescriptor 的 RVA
    cols = {}
    for rva in range(0, size - 0x18, 4):
        td = u32(rva + 0x0c)
        if td in types and u32(rva) in (0, 1):   # signature: 0=x86 1=x64
            cols[rva] = types[td]

    # 3) vftable 紧跟在指向 COL 的那个指针之后
    out = {}
    for rva in range(0, size - 8, 8):
        v = u64(rva)
        if not base <= v < base + size:
            continue
        cls = cols.get(v - base)
        if cls is None:
            continue
        vft = rva + 8
        fn = u64(vft + EXEC_SLOT)
        if not base <= fn < base + size:
            continue
        out[cls] = {'rva': fn - base, 'vftable_rva': vft}

    path = os.path.join(SCR, 'task_execs.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=1, sort_keys=True)
    for k in sorted(out):
        print('%-40s rva=0x%x' % (k, out[k]['rva']))
    print(f'\nWROTE {path} ({len(out)})')


if __name__ == '__main__':
    main()
