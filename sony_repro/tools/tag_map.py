r"""把 SR2 标签 → 标定块偏移的映射整张提出来。

解析器(RVA 约 `0x15f5c2..0x162a63`)是一条直线,每个标签一段固定模式:

    mov  edx, 0x78xx            ; 标签号
    call [rax+0x70]             ; 元素个数
    ...
    lea  r12, [rsi+0xdec]       ; 目标 = 标定块 + 这个偏移
    call [rax+0x20]             ; 逐个读 int16
    mov  [r12], ax

所以「标签常量之后最近的那个 `lea r*,[rsi+disp]`」就是它的落点。这不是完整反汇编,
是就着这个模式扫一遍 —— 够用,而且比逐个手读快得多。

之所以值得整张提:`0x787e`/`0x787f`(ChromaSuppres)那几个是逐个手读出来的,
而饱和度那几个(`0x7842`/`0x7844`/`0x7845`)一直只有猜测。有了映射就能直接去找
**谁读这个偏移**,而不是继续猜格式。

用法::

    python tag_map.py                      # 默认范围
    python tag_map.py 0x15f400 0x163200
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pe_scan as P  # noqa: E402

DEFAULT = (0x15F400, 0x163200)
TAG = re.compile(r"^\s*([0-9a-f]+):.*\bmov\s+e[a-z]x,0x(78[0-9a-f]{2})\b")
DST = re.compile(r"^\s*([0-9a-f]+):.*\blea\s+r[0-9a-z]+,\[rsi\+0x([0-9a-f]+)\]")


def main():
    args = sys.argv[1:]
    if len(args) not in (0, 2):
        raise SystemExit("要么不给范围,要么给起止两个 —— 只给一个会被悄悄忽略")
    lo, hi = (int(a, 0) for a in args) if args else DEFAULT
    lines = P.disasm(P.load(), lo, hi - lo).splitlines()

    pending = None
    out = []
    for ln in lines:
        m = TAG.match(ln)
        if m:
            # 同一个标签会连着出现两三次(取个数、再取个数、逐个读),只认第一次
            tag = int(m.group(2), 16)
            if pending is None or pending[0] != tag:
                pending = (tag, int(m.group(1), 16))
            continue
        if pending is None:
            continue
        m = DST.match(ln)
        if m:
            out.append((pending[0], pending[1], int(m.group(2), 16)))
            pending = None

    print(f"扫 {lo:#x}..{hi:#x},认出 {len(out)} 个标签\n")
    print(f"{'标签':>8} {'代码处':>10} {'标定块偏移':>12}   {'与上一个的间距':>14}")
    prev = None
    for tag, at, off in out:
        gap = f"{off - prev:#x}" if prev is not None and off > prev else ""
        print(f"  {tag:#06x} {at:#10x} {off:#12x}   {gap:>14}")
        prev = off


if __name__ == "__main__":
    main()
