r"""把绿色核用掩码去查的那张表 dump 出来。

反汇编(`disasm_kernel.py`)显示判据不是 `vcmpps`,而是

    vandnps  ymm1, ymm12, ymm0    ; 清符号位 -> |Δ|
    vsubps   ymm2, ymm1, ymm8     ; |Δ| − thr
    vmovmskps ecx, ymm2           ; 把 8 个符号位提到通用寄存器
    shl      rcx, 5               ; ×32,当索引
    lea      rdx, [rip + 0x208784]

也就是说:**8 位接纳掩码 ×32 去查一张表**,而不是像我们这样直接累加再除以 count。
每项 32 字节 = 8 个 float,正好一个 ymm。

要是表里存的不是精确的 1/count(比如是 `rcpps` 那种近似,或者别的加权),就能解释
一个"数值上极接近、逐位却不同"的残差 —— 九个假设全否之后,这是第一条来自代码
本身的线索。

表的 RVA:`lea` 在 0x3a1445,下一条在 0x3a144c,所以 0x3a144c + 0x208784 =
0x5A9BD0。

用法::

    python dump_mask_table.py <ARW> [--rva 0x5A9BD0] [--items 256]
"""
import os
import subprocess
import sys
import time

import frida
import numpy as np

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
TABLE_RVA = 0x5A9BD0
ITEM = 32          # 每项 32 字节 = 8 个 float

#: 表要**等核真的跑起来**再读。spawn 之后立刻读全是 0 —— 它在运行时才填,
#: 不是地址算错(lea 长 7 字节,0x3a144c + 0x208784 = 0x5A9BD0 没问题)。
RVA_GREEN = 0x3A0C30

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
let done = false;
Interceptor.attach(base.add(HOOKJS), {
  onEnter() {
    if (done) return;
    done = true;
    send({tag: 'tbl'}, base.add(RVAJS).readByteArray(BYTESJS));
    send({tag: 'end'});
  }
});
send({tag: 'armed'});
"""


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    arw = os.path.abspath(sys.argv[1])
    rva = int(sys.argv[sys.argv.index("--rva") + 1], 0) if "--rva" in sys.argv else TABLE_RVA
    items = int(sys.argv[sys.argv.index("--items") + 1] if "--items" in sys.argv else 256)
    nbytes = items * ITEM
    js = (JS.replace("HOOKJS", str(RVA_GREEN)).replace("RVAJS", str(rva))
            .replace("BYTESJS", str(nbytes)))

    got, done = {}, []

    def on_message(msg, data):
        if msg["type"] != "send":
            print("  !", msg, flush=True)
            return
        if msg["payload"].get("tag") == "tbl" and data:
            got["tbl"] = np.frombuffer(data, "<f4").copy()
        elif msg["payload"].get("tag") == "end":
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

    if "tbl" not in got:
        raise SystemExit("没读到")
    t = got["tbl"].reshape(items, ITEM // 4)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       f"mask_table_{rva:x}.npz")
    np.savez_compressed(out, table=t)
    print(f"读到 {items} 项 x {ITEM // 4} float -> {out}\n")

    print("  掩码  popcount   表内容(前 8 个 float)")
    for i in list(range(6)) + [7, 15, 31, 63, 127, 255]:
        pc = bin(i).count("1")
        vals = "  ".join(f"{v:9.6f}" for v in t[i])
        print(f"    {i:3d}     {pc}      {vals}")

    # 若它是 1/count 一类的东西,和 popcount 对一下就知道。
    print("\n  与 1/popcount 对照(取每项第 0 个 float):")
    for i in range(1, 9):
        idx = (1 << i) - 1          # popcount = i 的一个代表
        v = float(t[idx][0])
        inv = 1.0 / i
        print(f"    掩码 {idx:3d}(popcount={i})  表值 {v:10.6f}   "
              f"1/{i} = {inv:10.6f}   之比 {v / inv if inv else 0:8.5f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
