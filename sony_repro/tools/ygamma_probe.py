r"""把 YGamma 用到的那张 LUT 和四个标量从引擎内存里直接读出来。

反汇编 `0x36f030` 的开头给出取值路径:

    calib    = [[task+0x68] + 0xc8]
    pivot    = (int16) calib[0x91962]
    contrast = (float) calib[0x91964]
    opts     = [param_3 + 8]           # 注意 `mov rdi, r8`:这是**第三个参数**,
    bl       = (int16) opts[0x2ac]     # 不是 task+0x68 —— 我第一版就读错了这里,
    wl       = (int16) opts[0x2b0]     # 拿到两个巧合相等的垃圾值 22/22
    lut[i]   = (uint16) calib[0x318fc + i*2]

`0x318fc` 全二进制里只有读、没有字面写入 —— 和光源权重一个套路,所以它多半也是
整块灌进来的。读出来才知道它到底是什么(恒等?黑电平表?还是又一条曲线)。

必须用 Windows 的 Python 跑:
    python sony_repro/tools/ygamma_probe.py <ARW>
"""
import json
import os
import subprocess
import sys
import time

import frida
import numpy as np

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
SCR = os.path.dirname(os.path.abspath(__file__))
N = 16384

with open(os.path.join(SCR, "task_execs.json"), encoding="utf-8") as f:
    RVA = json.load(f)["ZcTaskYGamma"]["rva"]

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
let done = false;
Interceptor.attach(base.add(RVAV), {
  onEnter(a) {
    if (done) return;
    done = true;
    const task = a[1];
    const calib = task.add(0x68).readPointer().add(0xc8).readPointer();
    const opts = a[2].add(8).readPointer();
    send({
      pivot: calib.add(0x91962).readS16(),
      contrast: calib.add(0x91964).readFloat(),
      bl: opts.add(0x2ac).readS16(),
      wl: opts.add(0x2b0).readS16(),
    }, calib.add(0x318fc).readByteArray(NV * 2));
  }
});
send({info: 'armed'});
""".replace("RVAV", str(RVA)).replace("NV", str(N))

got = {}


def on_message(msg, data):
    if msg["type"] != "send":
        print("  !", msg, flush=True)
        return
    p = msg["payload"]
    if "info" in p:
        print("  ", p, flush=True)
        return
    got.update(p)
    got["lut"] = np.frombuffer(data, "<u2").copy()
    print("scalars:", {k: v for k, v in p.items()}, flush=True)


subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
time.sleep(1.0)
pid = frida.spawn([EXE, sys.argv[1]])
session = frida.attach(pid)
script = session.create_script(JS)
script.on("message", on_message)
script.load()
frida.resume(pid)
time.sleep(float(sys.argv[2]) if len(sys.argv) > 2 else 25.0)

if "lut" in got:
    lut = got.pop("lut")
    path = os.path.join(SCR, "ygamma_lut.npz")
    np.savez_compressed(path, lut=lut, **{k: np.array(v) for k, v in got.items()})
    d = np.arange(N) - lut.astype(np.int64)
    print(f"\nLUT: 前16 {lut[:16].tolist()}")
    print(f"     恒等偏差 最小 {d.min()} 最大 {d.max()} 非零 {(d != 0).sum()}/{N}")
    print("SAVED", path)
else:
    print("\n没抓到 —— 钩子没触发?")
try:
    session.detach()
    frida.kill(pid)
except Exception:
    pass
