r"""把引擎展开好的 1024 张分段矩阵原样 dump 下来。

kernel 0x37f8e0 里取矩阵是 `[param + idx*9*8 + 0x228]`,所以表基址就是某个参数 +0x228,
1024 段 x 9 个数。哪个参数不确定,就四个都试,用「每行和为 1」当判据认出来。

拿到它就能把两件事分开:节点矩阵对不对(比表),色相索引对不对(比索引)。

用法: python matrix_table.py <ARW> [秒数]
"""
import os
import struct
import subprocess
import sys
import time

import frida
import numpy as np

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
SCR = os.path.dirname(os.path.abspath(__file__))
RVA = 0x37F8E0
N = 1024

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
let done = false;
Interceptor.attach(base.add(RVA), {
  onEnter(a) {
    if (done) return;
    for (let k = 0; k < 4; k++) {
      let p;
      try { p = a[k]; } catch (e) { continue; }
      if (p.isNull() || p.compare(ptr('0x10000')) < 0) continue;
      // 条目步长 72 字节(asm 里是 rax*8 而 rax = idx*9),前 9 个 float 有效,后 9 个是填充
      try {
        send({arg: k}, p.add(0x228).readByteArray(NSEG * 72));
        // 参数区自己的 6x16 系数就在 +0x28,一并抓下来 —— 好分清是读 ARW 错了还是别处
        send({arg: k, coeff: true}, p.add(0x28).readByteArray(96 * 4));
      } catch (e) { /* 读不到就算了 */ }
    }
    done = true;
  }
});
send({info: 'armed'});
""".replace("RVA", str(RVA)).replace("NSEG", str(N))

found = {}


def on_message(msg, data):
    if msg["type"] != "send":
        return
    p = msg["payload"]
    if "info" in p:
        print("  ", p, flush=True)
        return
    if p.get("coeff"):
        c = np.array(struct.unpack("<96f", data)).reshape(6, 16)
        if np.all(np.abs(c) < 2.0):
            found["coeff"] = c
            print("  参数%d +0x28 的 6x16 系数: 首行 %s"
                  % (p["arg"], c[0].round(4).tolist()), flush=True)
        return
    v = np.array(struct.unpack("<%df" % (N * 18), data)).reshape(N, 18)[:, :9].reshape(N, 3, 3)
    s = v.sum(-1)
    ok = np.abs(s - 1.0) < 1e-3
    print("  参数%d: 每行和为1的比例 %.1f%%  首段 %s"
          % (p["arg"], ok.mean() * 100, v[0].ravel().round(3).tolist()), flush=True)
    if ok.mean() > 0.95:
        found["table"] = v


subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
time.sleep(1.0)
pid = frida.spawn([EXE, sys.argv[1]])
session = frida.attach(pid)
script = session.create_script(JS)
script.on("message", on_message)
script.load()
frida.resume(pid)
time.sleep(float(sys.argv[2]) if len(sys.argv) > 2 else 20.0)

if "table" in found:
    np.savez(os.path.join(SCR, "engine_matrix_table.npz"), **found)
    print("\nSAVED engine_matrix_table.npz")
else:
    print("\n没认出表 —— 基址不在这四个参数上")
try:
    session.detach()
    frida.kill(pid)
except Exception:
    pass
