r"""读出 base 的 25 个成员**到底按什么顺序相加**。

绿色卡在 99.994%,六条证据都指向"float32 的固有差异"(残差全在阈值边界内、无 FMA、
线性 vaddps 链、掩码精确 0/1、base 公式已核对、float64 更差)。剩下唯一没钉死的是
**成员累加的顺序** —— 试过四种排法,读数在噪声带里互有胜负,说明我猜的那几种都不是
引擎用的那个。

顺序在反汇编里是这样一串(0x3a18b8 起):

    vmovups ymm0, [rdx + rax]
    vaddps  ymm1, ymm0, [rcx + rax]
    vaddps  ymm2, ymm1, [r8  + rcx]
    ...

九个不同的行指针,静态看不出各自对应哪一行(基址是运行时算进栈槽的)。但**动态可以
读**:在那条指令上挂钩,把这些寄存器和 refOwn / refOther 的数据指针一减,除以 stride
就是行偏移,余数除以 4 就是列偏移。

用法(Windows 的 python,要 frida)::

    python base_order_probe.py <ARW> [--rva 0x3a18b8]
"""
import os
import subprocess
import sys
import time

import frida

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
RVA_GREEN = 0x3A0C30
RVA_SUM = 0x3A18B8
EXEC_RVA = 0x39FAB0

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
const perThread = {};
let own = null, oth = null, done = false;

Interceptor.attach(base.add(EXECJS), {
  onEnter(a) { perThread[this.threadId] = 1; }
});

function dims(desc) {
  return {w: desc.add(0).readS32(), h: desc.add(4).readS32(),
          stride: desc.add(8).readS32(), data: desc.add(0x10).readPointer()};
}

Interceptor.attach(base.add(RVAGJS), {
  onEnter(a) {
    if (!perThread[this.threadId] || own) return;
    own = dims(a[2]);
    oth = dims(a[3]);
    send({tag: 'planes', own: own.data.toString(), oth: oth.data.toString(),
          stride: own.stride, w: own.w, h: own.h});
  }
});

// 累加链上那一串指针。只抓第一次,免得刷屏。
Interceptor.attach(base.add(RVASUMJS), {
  onEnter() {
    if (done || !own) return;
    done = true;
    const c = this.context;
    const regs = {rax: c.rax, rbx: c.rbx, rcx: c.rcx, rdx: c.rdx,
                  rsi: c.rsi, rdi: c.rdi, r8: c.r8, r9: c.r9,
                  r10: c.r10, r11: c.r11, r12: c.r12, r13: c.r13,
                  r14: c.r14, r15: c.r15};
    const out = {};
    for (const k in regs) out[k] = regs[k].toString();
    send({tag: 'regs', regs: out});
  }
});
send({tag: 'armed'});
"""


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    arw = os.path.abspath(sys.argv[1])
    rva = int(sys.argv[sys.argv.index("--rva") + 1], 0) if "--rva" in sys.argv else RVA_SUM
    js = (JS.replace("EXECJS", str(EXEC_RVA)).replace("RVAGJS", str(RVA_GREEN))
            .replace("RVASUMJS", str(rva)))

    got, done = {}, []

    def on_message(msg, data):
        if msg["type"] != "send":
            print("  !", msg, flush=True)
            return
        p = msg["payload"]
        if p.get("tag") == "planes":
            got["planes"] = p
            print(f"   own={p['own']} oth={p['oth']} stride={p['stride']} "
                  f"{p['w']}x{p['h']}", flush=True)
        elif p.get("tag") == "regs":
            got["regs"] = p["regs"]
            done.append(True)

    subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
    time.sleep(1.0)
    pid = frida.spawn([EXE, arw])
    try:
        session = frida.attach(pid)
        script = session.create_script(js)
        script.on("message", on_message)
        script.load()
        frida.resume(pid)
        deadline = time.time() + 60
        while time.time() < deadline and not done:
            time.sleep(0.2)
    finally:
        subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)

    if "regs" not in got or "planes" not in got:
        raise SystemExit(f"没抓到(收到 {sorted(got)})")

    pl = got["planes"]
    own_base = int(pl["own"], 16) if pl["own"].startswith("0x") else int(pl["own"])
    oth_base = int(pl["oth"], 16) if pl["oth"].startswith("0x") else int(pl["oth"])
    stride = int(pl["stride"])
    # 不做范围过滤:行指针可能已经减掉了列偏移(取址是 [reg + r15 + imm]),
    # 落在平面起始之前很正常。第一版按"必须落在平面内"筛,九个里只认出两个。
    h = int(pl["h"])
    print(f"\n  寄存器相对两个平面的偏移(stride={stride}, h={h}):")
    for k, v in sorted(got["regs"].items()):
        val = int(v, 16) if v.startswith("0x") else int(v)
        cells = []
        for name, b in (("own", own_base), ("oth", oth_base)):
            d = val - b
            row = d / stride
            if -64 <= row <= h + 64:
                cells.append(f"{name}{row:+9.3f} 行")
        print(f"    {k:<4} = {val:#018x}   " + "   ".join(cells or ["(不在两个平面附近)"]))
    print("\n  把这些行/列按 vaddps 链的先后排出来,就是成员的累加顺序。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
