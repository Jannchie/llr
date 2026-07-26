r"""反汇编 Edit.exe 里的一段地址范围。Ghidra 的替代品(轻量,够用)。

**必须用 Windows 的 Python 跑**:
    python sony_repro/tools/disasm_fn.py 0x14036dce0 [字节数]
"""
import struct
import sys

import capstone
import pefile

BIN = r'C:\Program Files\Sony\Imaging Edge\Edit.exe'


def load():
    pe = pefile.PE(BIN, fast_load=True)
    base = pe.OPTIONAL_HEADER.ImageBase
    for s in pe.sections:
        if s.Name.rstrip(b'\x00') == b'.text':
            return base + s.VirtualAddress, s.get_data()
    raise SystemExit('no .text')


def main():
    addr = int(sys.argv[1], 0)
    n = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0x400
    start, data = load()
    off = addr - start
    if not 0 <= off < len(data):
        raise SystemExit(f'0x{addr:x} 不在 .text (0x{start:x}..0x{start + len(data):x})')

    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True
    for ins in md.disasm(data[off:off + n], addr):
        # 把内存操作数的位移标出来,找结构体偏移时最有用
        disps, rip = [], None
        for op in ins.operands:
            if op.type != capstone.x86.X86_OP_MEM or not op.mem.disp:
                continue
            if op.mem.base == capstone.x86.X86_REG_RIP:
                # RIP 相对:有效地址 = 下一条指令 + 位移。常量池里多是 float,
                # 顺手解出来 —— 不然只看到一个没意义的偏移。
                ea = ins.address + ins.size + op.mem.disp
                o = ea - start
                if 0 <= o + 4 <= len(data):
                    f = struct.unpack_from('<f', data, o)[0]
                    rip = '0x%x = %.9g (f32) / %d (i32)' % (
                        ea, f, struct.unpack_from('<i', data, o)[0])
            else:
                disps.append(op.mem.disp)
        tag = '   ; ' + ' '.join('0x%x' % d for d in sorted(set(disps))) if disps else ''
        if rip:
            tag += '   ; [' + rip + ']'
        print('0x%x  %-8s %s%s' % (ins.address, ins.mnemonic, ins.op_str, tag))
        if ins.mnemonic in ('ret', 'jmp') and ins.address - addr > n * 0.6:
            break


if __name__ == '__main__':
    main()
