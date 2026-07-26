r"""解包函数实际读到的那 276 字节,原样抓下来。

FUN_140179390(param_1, param_2) 从 `param_2 + 0xae` 读 23x12=276 字节。
我们一直喂的是 DataIFD 里 tag 0x780f 的载荷,但解出来的表与引擎内存对不上。
把引擎真正读的字节抓出来,和十种外观的 0x780f 逐字节比,就知道差在哪。
顺带把 param_2 前后一段也抓下来,好认出这块结构是什么。

用法: python src276_probe.py <ARW> [秒数]
"""
import os
import subprocess
import sys
import time

import frida
import numpy as np

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
SCR = os.path.dirname(os.path.abspath(__file__))
RVA = 0x179390

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
Interceptor.attach(base.add(RVA), {
  onEnter(a) {
    const src = a[1];
    send({what: 'block', ptr: src.toString()}, src.add(0xae).readByteArray(276));
    // 前后各留一段,好认出这块结构;0xae 前面通常是头部
    send({what: 'around', ptr: src.toString()}, src.readByteArray(0x400));
  }
});
send({info: 'armed'});
""".replace("RVA", str(RVA))

got = {}


def on_message(msg, data):
    if msg["type"] != "send":
        print("  !", msg, flush=True)
        return
    p = msg["payload"]
    if "info" in p:
        print("  ", p, flush=True)
        return
    key = "%s_%d" % (p["what"], sum(1 for k in got if k.startswith(p["what"])))
    got[key] = np.frombuffer(data, dtype=np.uint8).copy()
    print("  %-10s ptr=%s  %d 字节  前16: %s"
          % (key, p["ptr"], len(got[key]), got[key][:16].tolist()), flush=True)


subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
time.sleep(1.0)
pid = frida.spawn([EXE, sys.argv[1]])
session = frida.attach(pid)
script = session.create_script(JS)
script.on("message", on_message)
script.load()
frida.resume(pid)
time.sleep(float(sys.argv[2]) if len(sys.argv) > 2 else 20.0)
np.savez(os.path.join(SCR, "src276.npz"), **got)
print("\nSAVED src276.npz (%d 个数组)" % len(got))
try:
    session.detach()
    frida.kill(pid)
except Exception:
    pass
