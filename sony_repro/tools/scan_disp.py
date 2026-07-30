r"""在 Edit.exe 的 .text 里找所有访问某个结构偏移的指令。

Ghidra 装没了,但要回答「谁写 calib+0xe44」这类问题,全量反汇编 + 按位移过滤
其实更直接:光源权重是 `[reg + 0xe44]` 的 16 位访问,全二进制里这样的点不会多。

**必须用 Windows 的 Python 跑**(capstone/pefile 装在那边):
    python sony_repro/tools/scan_disp.py 0xe44 0xe46 0xe48 0xe4a
    python sony_repro/tools/scan_disp.py --write 0xe44      # 只看写入
    python sony_repro/tools/scan_disp.py --range 0xc0000 0xc0200   # 整段的位移词汇表

`--range` 是为「这一带到底有哪些字段被访问过」这类问题准备的。逐个猜位移时,
猜错了看不出区别 —— 没命中和「这个偏移不存在」是同一个输出。列出整段实际出现过
的位移就能分开这两种情况:色彩降噪那三个字段就是这么确认「只有写、没有读」的。
"""
import sys

import capstone
import pefile

BIN = r'C:\Program Files\Sony\Imaging Edge\Edit.exe'


def text_section(pe):
    for s in pe.sections:
        if s.Name.rstrip(b'\x00') == b'.text':
            return s
    raise SystemExit('no .text')


def sweep(md, data, start):
    """线性扫描整段 .text。

    capstone 的 disasm 碰到解不开的字节就**直接停**,不是跳过 —— 第一版扫描器
    因此在离 .text 开头不远处就哑了,报 0 命中,而那些指令明明存在。
    停下来就前进一个字节重来,代价是偶尔会解出对不齐的垃圾指令,对「按位移过滤」
    这个用途无所谓。
    """
    off = 0
    while off < len(data):
        last = off
        for ins in md.disasm(data[off:], start + off):
            yield ins
            last = ins.address - start + ins.size
        off = max(last, off + 1)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    only_write = '--write' in sys.argv
    if '--range' in sys.argv:
        if len(args) != 2:
            raise SystemExit('--range 要两个参数:下界 上界(半开)')
        lo, hi = (int(a, 0) for a in args)
        wanted = None
    else:
        lo = hi = None
        wanted = {int(a, 0) for a in args}
        if not wanted:
            raise SystemExit(__doc__)

    pe = pefile.PE(BIN, fast_load=True)
    base = pe.OPTIONAL_HEADER.ImageBase
    sec = text_section(pe)
    data = sec.get_data()
    start = base + sec.VirtualAddress

    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True
    hits = 0
    for ins in sweep(md, data, start):
        for op in ins.operands:
            if op.type != capstone.x86.X86_OP_MEM:
                continue
            if wanted is None:
                if not lo <= op.mem.disp < hi:
                    continue
            elif op.mem.disp not in wanted:
                continue
            # 写入 = 内存操作数在第一位(Intel 语法的目的操作数)
            is_write = ins.operands[0].type == capstone.x86.X86_OP_MEM
            if only_write and not is_write:
                continue
            print('0x%x  %-7s %-44s  disp=0x%x %s'
                  % (ins.address, ins.mnemonic, ins.op_str, op.mem.disp,
                     'WRITE' if is_write else 'read'))
            hits += 1
            break
    print(f'\n{hits} 处 (.text @ 0x{start:x}, {len(data)} 字节)')


if __name__ == '__main__':
    main()
