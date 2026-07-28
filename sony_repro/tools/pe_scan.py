r"""在 Edit.exe 里按常量找代码,并把某段 RVA 反汇编出来。

这台机器上没有 Ghidra / IDA,只有 objdump。好在我们要找的东西通常有一个**很特别的
立即数**(结构体里那种 `+0x118f6` 的偏移),直接在 `.text` 里扫这四个字节就能定位到
用它的指令,再把周围反汇编出来读。比全量反编译便宜得多。

地址一律用 **RVA**,和 frida 里 `base.add(...)` 的写法一致。

用法::

    python pe_scan.py const 0x118f6            # 找用到这个常量的地方
    python pe_scan.py const 0x118f6 --width=2  # 按 2 字节找(短偏移/立即数)
    python pe_scan.py dis 0x36d220 0x400       # 反汇编 RVA 起 0x400 字节
    python pe_scan.py func 0x1761f2            # 用 .pdata 定出函数边界,整个反汇编
    python pe_scan.py where 0x1761f2           # 只报边界,不反汇编
    python pe_scan.py sections
"""
import os
import struct
import subprocess
import sys
import tempfile

EXE = r"/mnt/c/Program Files/Sony/Imaging Edge/Edit.exe"
if not os.path.exists(EXE):
    EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"


def load():
    with open(EXE, "rb") as f:
        return f.read()


def sections(blob):
    """-> [(名字, RVA, 虚拟大小, 文件偏移, 原始大小)]"""
    e_lfanew = struct.unpack_from("<I", blob, 0x3C)[0]
    nsec, opt_size = struct.unpack_from("<H", blob, e_lfanew + 6)[0], \
        struct.unpack_from("<H", blob, e_lfanew + 20)[0]
    first = e_lfanew + 24 + opt_size
    out = []
    for i in range(nsec):
        o = first + i * 40
        name = blob[o:o + 8].rstrip(b"\x00").decode("ascii", "replace")
        vsize, rva, rsize, roff = struct.unpack_from("<IIII", blob, o + 8)
        out.append((name, rva, vsize, roff, rsize))
    return out


def image_base(blob):
    e_lfanew = struct.unpack_from("<I", blob, 0x3C)[0]
    return struct.unpack_from("<Q", blob, e_lfanew + 24 + 24)[0]


def rva_to_off(blob, rva):
    for _name, srva, vsize, roff, rsize in sections(blob):
        if srva <= rva < srva + max(vsize, rsize):
            return roff + (rva - srva)
    raise ValueError(f"RVA {rva:#x} 不在任何节里")


def off_to_rva(blob, off):
    for _name, srva, _vsize, roff, rsize in sections(blob):
        if roff <= off < roff + rsize:
            return srva + (off - roff)
    raise ValueError(f"文件偏移 {off:#x} 不在任何节里")


def scan_const(blob, value, width=4, secname=".text"):
    """在某一节里找这个小端常量,返回它出现处的 RVA 列表。"""
    pat = value.to_bytes(width, "little")
    hits = []
    for name, srva, _vsize, roff, rsize in sections(blob):
        if secname and name != secname:
            continue
        data = blob[roff:roff + rsize]
        i = data.find(pat)
        while i >= 0:
            hits.append(srva + i)
            i = data.find(pat, i + 1)
    return hits


def runtime_functions(blob):
    """.pdata 里的 RUNTIME_FUNCTION 表 -> [(起, 止)]。

    x64 的 PE 必须为每个非叶函数登记异常展开信息,所以这张表就是一份**免费的函数
    边界清单** —— 没有 IDA 也能准确切出函数,不必靠「扫到 ret 为止」去猜。
    """
    for name, srva, _vsize, roff, rsize in sections(blob):
        if name != ".pdata":
            continue
        out = []
        for o in range(roff, roff + rsize, 12):
            begin, end, _unwind = struct.unpack_from("<III", blob, o)
            if begin == 0 and end == 0:
                break
            out.append((begin, end))
        return sorted(out)
    return []


def enclosing(blob, rva):
    """包含这个 RVA 的函数 (起, 止);找不到就返回 None。"""
    for begin, end in runtime_functions(blob):
        if begin <= rva < end:
            return begin, end
    return None


def xrefs(blob, target, secname=".text"):
    """谁引用了这个 RVA。

    只认三种最有用的编码:``e8`` call rel32、``e9`` jmp rel32、``48 8d 0d`` 之类的
    ``lea reg,[rip+disp32]``。都是 4 字节相对位移,起点是**下一条指令**,所以直接
    按「目标 - 位移 = 下一条指令地址」反推,再验一下操作码。够用了 —— 这不是完整的
    反汇编器,是一把够钝但够快的刀。
    """
    out = []
    for name, srva, _vsize, roff, rsize in sections(blob):
        if name != secname:
            continue
        data = blob[roff:roff + rsize]
        for i in range(len(data) - 4):
            disp = struct.unpack_from("<i", data, i)[0]
            nxt = srva + i + 4
            if nxt + disp != target:
                continue
            op = data[i - 1]
            if op in (0xE8, 0xE9) and i >= 1:
                out.append(("call" if op == 0xE8 else "jmp", srva + i - 1))
            elif i >= 3 and data[i - 2] == 0x8D and (data[i - 3] & 0xF8) == 0x48:
                out.append(("lea", srva + i - 3))
    return out


def disasm(blob, rva, length):
    """用 objdump 反汇编一段;先切出来当裸二进制喂进去,免得 PE 解析出岔子。"""
    off = rva_to_off(blob, rva)
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        f.write(blob[off:off + length])
        tmp = f.name
    try:
        r = subprocess.run(
            ["objdump", "-D", "-b", "binary", "-m", "i386:x86-64", "-M", "intel",
             f"--adjust-vma={rva:#x}", tmp],
            capture_output=True, text=True, check=True)
        return "\n".join(ln for ln in r.stdout.splitlines()
                         if ln.strip() and not ln.startswith(("\n", "In archive")))
    finally:
        os.unlink(tmp)


def main():
    blob = load()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "sections"
    if cmd == "sections":
        print(f"ImageBase {image_base(blob):#x}")
        for name, rva, vsize, roff, rsize in sections(blob):
            print(f"  {name:10} RVA {rva:#010x} 虚拟 {vsize:#010x}  "
                  f"文件 {roff:#010x} 原始 {rsize:#010x}")
    elif cmd == "const":
        value = int(sys.argv[2], 0)
        width = next((int(a.split("=")[1]) for a in sys.argv if a.startswith("--width=")), 4)
        sec = next((a.split("=")[1] for a in sys.argv if a.startswith("--sec=")), ".text")
        hits = scan_const(blob, value, width, sec)
        print(f"{value:#x}(宽 {width})在 {sec} 里出现 {len(hits)} 次:")
        for h in hits:
            print(f"  RVA {h:#x}")
    elif cmd == "xref":
        target = int(sys.argv[2], 0)
        for kind, rva in xrefs(blob, target):
            span = enclosing(blob, rva)
            where = f"函数 {span[0]:#x}" if span else "不在 .pdata 里"
            print(f"  {kind:4} @ {rva:#x}   ({where})")
    elif cmd == "data":
        rva, kind = int(sys.argv[2], 0), sys.argv[3]
        n = int(sys.argv[4], 0) if len(sys.argv) > 4 else 1
        fmt, size = {"f64": ("<d", 8), "f32": ("<f", 4), "i32": ("<i", 4),
                     "u32": ("<I", 4), "i16": ("<h", 2), "u16": ("<H", 2)}[kind]
        off = rva_to_off(blob, rva)
        vals = [struct.unpack_from(fmt, blob, off + i * size)[0] for i in range(n)]
        for i, v in enumerate(vals):
            print(f"  {rva + i * size:#x}  {v!r}")
    elif cmd == "dis":
        print(disasm(blob, int(sys.argv[2], 0), int(sys.argv[3], 0)))
    elif cmd in ("func", "where"):
        rva = int(sys.argv[2], 0)
        span = enclosing(blob, rva)
        if span is None:
            raise SystemExit(f"{rva:#x} 不在 .pdata 登记的任何函数里")
        begin, end = span
        print(f"; 函数 {begin:#x}..{end:#x}({end - begin} 字节),含 {rva:#x}")
        if cmd == "func":
            print(disasm(blob, begin, end - begin))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
