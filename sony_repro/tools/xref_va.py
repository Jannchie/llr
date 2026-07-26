r"""找出 .text 里所有指向某个 VA/RVA 的引用(RIP 相对寻址)。

要回答「谁构造了 ZcTask3DLut」,就去找谁把它的 vftable 地址装进对象 —— 那条
`lea rax, [rip+disp]` 的目标就是 vftable 的 VA。同一套办法也能找常量表的用处。

**必须用 Windows 的 Python 跑**(capstone/pefile 在那边):
    python xref_va.py 0x467710            # 按 RVA
    python xref_va.py --va 0x140467710
"""
import sys

import capstone
import pefile

from scan_disp import sweep, text_section

BIN = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        raise SystemExit(__doc__)

    pe = pefile.PE(BIN, fast_load=True)
    base = pe.OPTIONAL_HEADER.ImageBase
    sec = text_section(pe)
    data = sec.get_data()
    start = base + sec.VirtualAddress
    wanted = {int(a, 0) if "--va" in sys.argv else base + int(a, 0) for a in args}
    print("找 VA:", [hex(v) for v in sorted(wanted)])

    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True
    hits = 0
    for ins in sweep(md, data, start):
        for op in ins.operands:
            # RIP 相对:目标 = 下一条指令地址 + disp
            if (op.type == capstone.x86.X86_OP_MEM
                    and op.mem.base == capstone.x86.X86_REG_RIP
                    and ins.address + ins.size + op.mem.disp in wanted):
                print("0x%x (rva 0x%x)  %-7s %s"
                      % (ins.address, ins.address - base, ins.mnemonic, ins.op_str))
                hits += 1
                break
            if op.type == capstone.x86.X86_OP_IMM and op.imm in wanted:
                print("0x%x (rva 0x%x)  %-7s %s  [imm]"
                      % (ins.address, ins.address - base, ins.mnemonic, ins.op_str))
                hits += 1
                break
    print(f"\n{hits} 处")


if __name__ == "__main__":
    main()
