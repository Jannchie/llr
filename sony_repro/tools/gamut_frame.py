r"""把 FUN_140196170 的入口与出口各整块 dump 下来。

它是管线里最后一次颜色操作,出口那份就是引擎的最终画面 —— 比机内 JPEG 更适合当真值。
入口那份则正好是「我们复刻到哪一步为止」的对照。tile 在整幅里的位置不知道,
留给离线用互相关去找。

用法: python gamut_frame.py <ARW> [秒数]
"""
import os
import subprocess
import sys
import time

import frida
import numpy as np

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
SCR = os.path.dirname(os.path.abspath(__file__))
RVA = 0x196170
WANT = 3  # 抓前几块

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
let taken = 0;
const out = [];

function grab(p) {
  const w = p.add(8).readS32(), h = p.add(12).readS32();
  const stride = p.add(0x14).readS32(), data = p.add(0x20).readPointer();
  return {w: w, h: h, stride: stride, buf: data.readByteArray(h * stride)};
}

Interceptor.attach(base.add(RVA), {
  onEnter(a) {
    if (taken >= WANT) return;
    this.take = true;
    this.p = [a[0], a[1], a[2]];
    this.rect = [a[3].add(8).readS32(), a[3].add(12).readS32(),
                 a[3].add(16).readS32(), a[3].add(20).readS32()];
    this.before = [grab(a[0]), grab(a[1]), grab(a[2])];
  },
  onLeave() {
    if (!this.take || taken >= WANT) return;
    const after = [grab(this.p[0]), grab(this.p[1]), grab(this.p[2])];
    const i = taken++;
    for (let k = 0; k < 3; k++) {
      send({tile: i, plane: k, when: 'in', w: this.before[k].w, h: this.before[k].h,
            stride: this.before[k].stride, rect: this.rect}, this.before[k].buf);
      send({tile: i, plane: k, when: 'out', w: after[k].w, h: after[k].h,
            stride: after[k].stride, rect: this.rect}, after[k].buf);
    }
    out.push(i);
  }
});
send({info: 'armed'});
""".replace("RVA", str(RVA)).replace("WANT", str(WANT))

store = {}


def on_message(msg, data):
    if msg["type"] != "send":
        print("  !", msg, flush=True)
        return
    p = msg["payload"]
    if "info" in p:
        print("  ", p, flush=True)
        return
    key = "t%d_%s_p%d" % (p["tile"], p["when"], p["plane"])
    a = np.frombuffer(data, dtype="<u2").reshape(p["h"], p["stride"] // 2)[:, :p["w"]]
    store[key] = a.copy()
    store.setdefault("meta_%d" % p["tile"], np.array(p["rect"] + [p["w"], p["h"]]))
    print("  %-14s %dx%d rect=%s" % (key, p["w"], p["h"], p["rect"]), flush=True)


subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
time.sleep(1.0)
pid = frida.spawn([EXE, sys.argv[1]])
session = frida.attach(pid)
script = session.create_script(JS)
script.on("message", on_message)
script.load()
frida.resume(pid)
time.sleep(float(sys.argv[2]) if len(sys.argv) > 2 else 25.0)

path = os.path.join(SCR, "gamut_frame.npz")
np.savez_compressed(path, **store)
print("\nSAVED %s (%d 个数组)" % (path, len(store)))
try:
    session.detach()
    frida.kill(pid)
except Exception:
    pass
