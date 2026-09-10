"""从反汇编里把**抽头几何**捞出来:哪些行在按位移读平面。

`0x3a1b00`(R/B)的抽头集是已知的 —— 行 y-4,y-2,y,y+2,y+4,列同样间距 2。
所以先在它身上跑一遍,确认这个提取规则能把已知答案捞出来;规则立住了,
再拿去跑 `0x3a0c30`(绿)。**先验证工具,再用工具**,否则读出来的东西没有对错。

看两类东西:
  * `vmovup*/vmovap*/vadd*/vsub*` 里带位移的内存操作数 —— 直接的抽头读取
  * `lea` 出来的行指针 —— 核常常先把每行的基址算好再用小位移取列

⚠️ 抽头值可能被预载进寄存器再复用(§6 说这正是当初卡住的原因),
所以"内存位移"只是线索的一半;另一半是这些位移**成组出现的模式**。
本工具报模式,不下结论。
"""
import re
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MEM = re.compile(r"(?:ymm|xmm)?\w*\s+ptr\s+\[(\w+)\s*([+-])\s*(0x[0-9a-f]+|\d+)\]")
LINE = re.compile(r"^(0x[0-9a-f]+)\s+(\S+)\s+(.*)$")


def parse(path):
    rows = []
    for ln in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        m = LINE.match(ln.strip())
        if m:
            rows.append((int(m.group(1), 16), m.group(2), m.group(3)))
    return rows


def main():
    for path, name in ((sys.argv[1], sys.argv[2]) if len(sys.argv) > 2 else
                       [("/home/jannchie/llr/tmp/rb_filt.asm", "R/B 0x3a1b00"),
                        ("/home/jannchie/llr/tmp/green_filt.asm", "绿 0x3a0c30")]):
        rows = parse(path)
        print(f"\n=== {name}  ({len(rows)} 条指令)")

        mn = Counter(op for _a, op, _s in rows)
        print("  指令频次(前 14):")
        print("   ", ", ".join(f"{k}×{v}" for k, v in mn.most_common(14)))

        # lea 出来的基址位移 —— 常是行指针
        leas = [s for _a, op, s in rows if op == "lea"]
        ld = Counter()
        for s in leas:
            m = MEM.search(s)
            if m:
                v = int(m.group(3), 0) * (1 if m.group(2) == "+" else -1)
                ld[v] += 1
        if ld:
            print(f"  lea 的位移({len(leas)} 条 lea):"
                  f"{sorted(ld.items(), key=lambda kv: kv[0])[:24]}")

        # 向量取数的位移
        vd = Counter()
        for _a, op, s in rows:
            if not op.startswith("v"):
                continue
            if "ptr" not in s:
                continue
            m = MEM.search(s)
            if m and m.group(1) not in ("rsp", "rbp", "rip"):
                v = int(m.group(3), 0) * (1 if m.group(2) == "+" else -1)
                vd[v] += 1
        print(f"  向量内存位移(非栈/非 rip),按值排序:")
        for v, c in sorted(vd.items()):
            print(f"    {v:+6d}  ×{c}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
