r"""反汇编绿色滤波核 `0x3a0c30`,专找**判据**那几条指令。

九个假设全部被实测否掉(notes 2.19.4),说明靠猜已经到头。早期放弃读反汇编,是因为
它把行基址与列基址预算进栈槽(`0x3a1001..0x3a10c3`),静态看不出抽头几何 —— 但几何
现在已经全解出来了(权重图逐点测过),这次要找的只是判据:

  * 比较用的是哪条指令(`vcmpps` 的谓词决定 < 还是 <=);
  * 阈值参与比较之前还被动过什么(乘、加、min/max);
  * 累加链外面还有没有**第二遍**循环(那会对应"迭代"或"补足"之类的兜底);
  * `vblendvps` / `vmaxps` / `vminps` 出现在哪 —— 限幅一类的操作会以它们现身。

frida 自带 `Instruction.parse`,不需要另外的反汇编器。

用法(Windows 的 Python,要 frida)::

    python disasm_kernel.py <ARW> [--rva 0x3a0c30] [--count 400]
"""
import os
import subprocess
import sys
import time

import frida

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
RVA_GREEN = 0x3A0C30

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
const start = base.add(RVAJS);
const out = [];
let p = start;
for (let i = 0; i < COUNTJS; i++) {
  let ins;
  try { ins = Instruction.parse(p); } catch (e) { break; }
  out.push([p.sub(base).toString(16), ins.mnemonic, ins.opStr]);
  p = ins.next;
  if (ins.mnemonic === 'ret' && i > 32) break;
}
send({tag: 'disasm', n: out.length}, null);
// 分批送,一次性 send 大数组容易被截断。
for (let i = 0; i < out.length; i += 64) {
  send({tag: 'chunk', rows: out.slice(i, i + 64)});
}
send({tag: 'end'});
"""


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    arw = os.path.abspath(sys.argv[1])
    rva = int(sys.argv[sys.argv.index("--rva") + 1], 0) if "--rva" in sys.argv else RVA_GREEN
    count = int(sys.argv[sys.argv.index("--count") + 1] if "--count" in sys.argv else 400)
    js = JS.replace("RVAJS", str(rva)).replace("COUNTJS", str(count))

    rows, done = [], []

    def on_message(msg, data):
        if msg["type"] != "send":
            print("  !", msg, flush=True)
            return
        p = msg["payload"]
        if p.get("tag") == "chunk":
            rows.extend(p["rows"])
        elif p.get("tag") == "end":
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
        deadline = time.time() + 30
        while time.time() < deadline and not done:
            time.sleep(0.2)
    finally:
        subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        f"disasm_{rva:x}.txt")
    with open(path, "w", encoding="utf-8") as f:
        for off, mn, ops in rows:
            f.write(f"{off:>8}  {mn:<12} {ops}\n")
    print(f"{len(rows)} 条指令 -> {path}")

    # 判据相关的指令挑出来直接看。
    keys = ("vcmp", "vblend", "vmax", "vmin", "vandp", "vsub", "vdiv", "vrcp",
            "cvt", "jmp", "jn", "je", "jb", "ja", "jl", "jg")
    print("\n  判据相关的指令:")
    for off, mn, ops in rows:
        if any(k in mn for k in keys):
            print(f"    {off:>8}  {mn:<12} {ops}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
