r"""还原 `BASE_GREEN_OTHER[0]` 的累加次序 —— 在每条 vaddps 上分别挂钩。

own 表与 `BASE_GREEN_OTHER[1]` 的次序已经定下来了(g1 三张图全部逐位 100.0000%,
DSC03036 四路全 100.0000%)。只剩 g0 差 0.002%,指向 other[0] 的次序。

那段链子在 `0x3a1253`..`0x3a12be`,正好 12 条对应 other 的 12 个成员:

    3a1253  [r15 + r13]        3a127a  [rax + r15 - 4]     3a129c  [rdx + r15 - 4]
    3a1259  [rax + r15 - 4]    3a1281  [rax + r15]         3a12a3  [rdx + r15]
    3a1267  [rax + r15]        3a128e  [rax + r15]         3a12a9  [rbx + r15]
    3a1274  [r9  + r15]        3a1298  [rax]               3a12be  [rax + r15 - 4]

`rax` 出现七次且中途被重新赋值,所以**静态**读不出它们各指哪一行 —— 但在每条指令
上挂钩读当时的寄存器值就行。寄存器存的是行偏移(字节),对 other 平面还含两平面
基址之差;r15 指向「中心 + 2 个 float」(由 own 那半独立定出)。

于是 (平面, dy, dx) = 解 `reg + imm - (oth_base - own_base)`。

用法(Windows 的 Python,要 frida)。`--site` 必填,一次读一条,读满 12 次::

    python other_order_probe.py <ARW> --site N
"""
import os
import subprocess
import sys
import time

import frida

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
RVA_GREEN = 0x3A0C30
EXEC_RVA = 0x39FAB0
#: (地址, 基址寄存器, 立即数)。地址与操作数从反汇编逐条抄下来。
SITES = [
    (0x3A1253, "r13", 0), (0x3A1259, "rax", -4), (0x3A1267, "rax", 0),
    (0x3A1274, "r9", 0), (0x3A127A, "rax", -4), (0x3A1281, "rax", 0),
    (0x3A128E, "rax", 0), (0x3A1298, "rax", None), (0x3A129C, "rdx", -4),
    (0x3A12A3, "rdx", 0), (0x3A12A9, "rbx", 0), (0x3A12BE, "rax", -4),
]

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
const perThread = {};
let own = null, oth = null, armed = false;
const seen = {};

Interceptor.attach(base.add(EXECJS), {
  onEnter(a) { perThread[this.threadId] = 1; }
});
function dims(d) {
  return {stride: d.add(8).readS32(), data: d.add(0x10).readPointer()};
}
Interceptor.attach(base.add(RVAGJS), {
  onEnter(a) {
    if (!perThread[this.threadId] || own) return;
    own = dims(a[2]); oth = dims(a[3]); armed = true;
    send({tag: 'planes', own: own.data.toString(), oth: oth.data.toString(),
          stride: own.stride});
  }
});
// ⚠️ 一次只挂**一条**(--site N,分 12 次跑)。在同一个函数里挂 12 个 trampoline 会
// **把这个函数破坏掉** —— 连 0x3a0c30 自己的钩子都不再触发,表现成"没抓到平面
// 信息"。抓齐后 detach 也救不了:根本没机会触发。
//
// 分次跑是可比的:取址是 `[reg + r15 + imm]`,而 reg 里已经含「两平面基址差 +
// 行偏移」,所以 **reg + imm − 基址差** 与 r15 无关,跨进程一样。早先按
// `addr − 平面基址` 去算才会得到彼此无关的坐标 —— 那把 r15 的当前位置混了进去。
SITESJS.forEach(function (rva) {
  Interceptor.attach(base.add(rva), {
    onEnter() {
      if (!armed || seen[rva]) return;
      seen[rva] = 1;
      const c = this.context;
      send({tag: 'site', rva: rva,
            regs: {rax: c.rax.toString(), r9: c.r9.toString(),
                   r13: c.r13.toString(), rdx: c.rdx.toString(),
                   rbx: c.rbx.toString(), r15: c.r15.toString()}});
    }
  });
});
send({tag: 'armed'});
"""


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    arw = os.path.abspath(sys.argv[1])
    # 一次只能挂一条,所以 --site 是必填的 —— 见下面 JS 里的 ⚠️。以前不填就把 12 条
    # 全挂上,而那正是会**把目标函数弄坏**的做法:跑满 60 秒,最后打印"没抓到平面
    # 信息",与 RVA 填错时一模一样的报错,没有任何东西指向 --site。
    if "--site" not in sys.argv:
        raise SystemExit("要指定 --site N(0..11):一次只能挂一条,理由见文件内的 ⚠️")
    site = int(sys.argv[sys.argv.index("--site") + 1])
    want = [SITES[site][0]]
    js = (JS.replace("EXECJS", str(EXEC_RVA)).replace("RVAGJS", str(RVA_GREEN))
            .replace("SITESJS", repr(want)))

    got = {"sites": {}}

    def on_message(msg, data):
        if msg["type"] != "send":
            return
        p = msg["payload"]
        if p.get("tag") == "planes":
            got["planes"] = p
        elif p.get("tag") == "site":
            got["sites"][int(p["rva"])] = p["regs"]

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
        while time.time() < deadline and len(got["sites"]) < len(want):
            time.sleep(0.2)
    finally:
        subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)

    if "planes" not in got:
        raise SystemExit("没抓到平面信息")
    pl = got["planes"]
    def to_i(v):
        return int(v, 16) if v.startswith("0x") else int(v)

    own_b, oth_b = to_i(pl["own"]), to_i(pl["oth"])
    stride = int(pl["stride"])
    diff = oth_b - own_b
    print(f"  stride={stride}  两平面基址差={diff}\n")
    print("  #   地址      寄存器  立即数        -> (dy, dx)")
    out = []
    for i, (rva, reg, imm) in enumerate(SITES):
        regs = got["sites"].get(rva)
        if not regs:
            print(f"  {i:2d}  {rva:#x}  {reg:<4}  {'?':>6}   (没抓到)")
            continue
        v = to_i(regs[reg])
        # reg 里含「两平面基址差 + 行偏移×stride」,减掉基址差就只剩行偏移;
        # 再加 imm 得到列。**不要**把 r15 算进来 —— 它是当前像素的位置,每次运行
        # 都不同,混进来就得到一堆彼此无关的坐标。
        d = v - diff + (imm or 0)
        row = round(d / stride)
        col = (d - row * stride) // 4 + 2      # r15 指向中心+2 个 float
        out.append((row, col))
        print(f"  {i:2d}  {rva:#x}  {reg:<4}  {imm!s:>6}   ({row:+d}, {col:+d})")
    if out:
        print("\n  次序:", ", ".join(f"({a:+d},{b:+d})" for a, b in out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
