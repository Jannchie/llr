r"""把「哪个设置位打开哪个阶段」列成一张表。

流水线是在 `0x14017ef50` 一带拼出来的:一串 `call 0x14017fXXX` 的小桩,每个桩
`new` 一个具体的 ZcTask,有些桩前面带一句 `cmp dword ptr [rsi+X], 0` 当守卫。
顺着 桩 -> 构造函数 -> `lea rax,[rip+...]` 装 vftable 这条链,就能把桩认成任务名
(vftable 的 RVA 在 `task_execs.json` 里)。

这比"跑一遍看普查"多给一样东西:**没被执行的阶段也在表上,并且写着它要什么条件**。
`ZcTask3DLut` 就是这么找到守卫 `[rsi+0x248]` 的。

必须用 Windows 的 Python 跑:
    python chain_map.py [起始VA] [字节数]
"""
import json
import os
import sys

import capstone
import pefile

from scan_disp import sweep

BIN = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
SCR = os.path.dirname(os.path.abspath(__file__))
BUILDER, LENGTH = 0x14017EF50, 0x300


def load():
    pe = pefile.PE(BIN, fast_load=True)
    base = pe.OPTIONAL_HEADER.ImageBase
    for s in pe.sections:
        if s.Name.rstrip(b"\x00") == b".text":
            return base, base + s.VirtualAddress, s.get_data()
    raise SystemExit("no .text")


def disasm(md, data, start, addr, n):
    """反汇编一段。用 scan_disp 的 sweep,因为 capstone 碰到解不开的字节会**直接停**
    —— 第一版没走 sweep,扫 0x2000 字节只认出三个桩就哑了。"""
    off = addr - start
    if not 0 <= off < len(data):
        return []
    return list(sweep(md, data[off:off + n], addr))


def vftable_of(md, data, start, fn):
    """构造函数开头装的那个 vftable 的 VA,认不出来就 None。"""
    for ins in disasm(md, data, start, fn, 0x60):
        for op in ins.operands:
            if (ins.mnemonic == "lea" and op.type == capstone.x86.X86_OP_MEM
                    and op.mem.base == capstone.x86.X86_REG_RIP):
                return ins.address + ins.size + op.mem.disp
        if ins.mnemonic == "ret":
            break
    return None


def main():
    addr = int(sys.argv[1], 0) if len(sys.argv) > 1 else BUILDER
    n = int(sys.argv[2], 0) if len(sys.argv) > 2 else LENGTH
    base, start, data = load()
    with open(os.path.join(SCR, "task_execs.json"), encoding="utf-8") as f:
        by_vft = {base + v["vftable_rva"]: k for k, v in json.load(f).items()}

    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True
    guard = None
    print(f"{'桩':<14} {'任务':<34} 守卫")
    for ins in disasm(md, data, start, addr, n):
        if ins.mnemonic == "cmp" and ins.operands[0].type == capstone.x86.X86_OP_MEM:
            m = ins.operands[0].mem
            if m.base != capstone.x86.X86_REG_RIP:
                guard = "[%s+0x%x] != %s" % (ins.reg_name(m.base), m.disp,
                                             ins.op_str.rsplit(", ", 1)[-1])
            continue
        if ins.mnemonic != "call" or ins.operands[0].type != capstone.x86.X86_OP_IMM:
            continue
        thunk = ins.operands[0].imm
        name = None
        for k in disasm(md, data, start, thunk, 0x80):
            if k.mnemonic == "call" and k.operands[0].type == capstone.x86.X86_OP_IMM:
                vft = vftable_of(md, data, start, k.operands[0].imm)
                if vft in by_vft:
                    name = by_vft[vft]
                    break
        if name:
            print(f"0x{thunk:x}  {name:<34} {guard or '(无条件)'}")
        guard = None


if __name__ == "__main__":
    main()
