r"""线性反汇编 Edit.exe 的一段地址范围,按给定的函数入口切成多个文件(Marble 精读用)。

    python disasm_range.py <起> <止> <输出目录> [入口地址,逗号分隔]
未给入口时按 int3 填充后的第一条指令自动切段。capstone 解不开时前进一字节重扫。
"""
import os
import struct
import sys

import capstone
import pefile

BIN = r'C:\Program Files\Sony\Imaging Edge\Edit.exe'


def image():
    pe = pefile.PE(BIN)
    buf = bytearray(pe.OPTIONAL_HEADER.SizeOfImage)
    for s in pe.sections:
        d = s.get_data()
        buf[s.VirtualAddress:s.VirtualAddress + len(d)] = d
    return pe.OPTIONAL_HEADER.ImageBase, bytes(buf)


def fmt(ins, base, img):
    disps, rip = [], None
    for op in ins.operands:
        if op.type != capstone.x86.X86_OP_MEM or not op.mem.disp:
            continue
        if op.mem.base == capstone.x86.X86_REG_RIP:
            ea = ins.address + ins.size + op.mem.disp
            o = ea - base
            if 0 <= o + 4 <= len(img):
                f = struct.unpack_from('<f', img, o)[0]
                i = struct.unpack_from('<i', img, o)[0]
                rip = '0x%x = %.9g (f32) / %d (i32)' % (ea, f, i)
                if op.size == 32 and 0 <= o + 32 <= len(img):
                    rip += ' ymm=' + ','.join('%g' % v for v in struct.unpack_from('<8f', img, o)) + ' | i32=' + ','.join(str(v) for v in struct.unpack_from('<8i', img, o))
                elif op.size == 16 and 0 <= o + 16 <= len(img):
                    rip += ' xmm=' + ','.join('%g' % v for v in struct.unpack_from('<4f', img, o)) + ' | i32=' + ','.join(str(v) for v in struct.unpack_from('<4i', img, o)) + ' | i16=' + ','.join(str(v) for v in struct.unpack_from('<8h', img, o))
        else:
            disps.append(op.mem.disp)
    tag = '   ; ' + ' '.join('0x%x' % d for d in sorted(set(disps))) if disps else ''
    if rip:
        tag += '   ; [' + rip + ']'
    return '0x%x  %-8s %s%s' % (ins.address, ins.mnemonic, ins.op_str, tag)


def main():
    lo, hi, outdir = int(sys.argv[1], 0), int(sys.argv[2], 0), sys.argv[3]
    entries = sorted(int(x, 0) for x in sys.argv[4].split(',')) if len(sys.argv) > 4 else []
    os.makedirs(outdir, exist_ok=True)
    base, img = image()
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True
    lines = []
    a = lo
    while a < hi:
        got = False
        for ins in md.disasm(img[a - base:hi - base], a):
            lines.append((ins.address, fmt(ins, base, img), ins.mnemonic))
            a = ins.address + ins.size
            got = True
        if not got:
            lines.append((a, '0x%x  db 0x%02x' % (a, img[a - base]), 'db'))
            a += 1
    # 切段:给定入口优先;否则 int3 之后的第一条
    if not entries:
        prev_int3 = False
        for addr, _, mn in lines:
            if prev_int3 and mn != 'int3':
                entries.append(addr)
            prev_int3 = (mn == 'int3')
        entries = [lo] + entries
    entries = sorted(set(entries))
    idx = 0
    cur = None
    files = []
    for addr, text, mn in lines:
        while idx < len(entries) and addr >= entries[idx]:
            if cur:
                cur.close()
            name = os.path.join(outdir, 'fn_%x.txt' % entries[idx])
            cur = open(name, 'w', encoding='utf-8')
            files.append(name)
            idx += 1
        if cur is None:
            cur = open(os.path.join(outdir, 'fn_%x.txt' % lo), 'w', encoding='utf-8')
            files.append(cur.name)
        if mn == 'int3':
            continue
        cur.write(text + '\n')
    if cur:
        cur.close()
    for f in files:
        n = sum(1 for _ in open(f, encoding='utf-8'))
        print(os.path.basename(f), n)


if __name__ == '__main__':
    main()
