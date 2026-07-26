r"""每个阶段自己的入口和出口各量一次 —— 同一块 tile,不需要配对。

这些任务都是原地改 `*(task+8)` 那个 planar set,所以 onEnter/onLeave 看到的是同一块
数据的前后两份。`stage_pair.py` 那条靠缓冲区地址配对的路走不通,这条绕开了它。

平面描述符: +8 宽 +0xc 高 +0x14 行距 +0x20 数据; 第 i 个平面 = *(set + 8 + i*8)。
量的是中央区域,躲开 ITP 会改的 10 像素边界。

用法: python stage_delta.py <ARW> [秒数]
"""
import json
import os
import subprocess
import sys
import time

import frida

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
SCR = os.path.dirname(os.path.abspath(__file__))
WATCH = ["ZcTaskRGB2YCC", "ZcTaskChromaSuppres", "ZcTaskYGamma", "ZcTaskYCC2RGB",
         "ZcTaskRawNRSIMD", "ZcTaskSIMDITP", "ZcTaskSSCS", "ZcTaskAreaCompSIMD",
         "ZcTaskSIMDSharpness", "ZcTaskSIMDSpica", "ZcTaskSIMDMarble"]

with open(os.path.join(SCR, "task_execs.json"), encoding="utf-8") as f:
    ALL = json.load(f)
POINTS = {n: ALL[n]["rva"] for n in WATCH}

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
const P = POINTS;
const acc = {};

function measure(task) {
  const set = task.add(8).readPointer();
  if (set.isNull()) return null;
  const planes = [];
  for (let i = 0; i < 3; i++) {
    const p = set.add(8 + i * 8).readPointer();
    if (p.isNull()) return null;
    const w = p.add(8).readS32(), h = p.add(12).readS32();
    const stride = p.add(0x14).readS32(), data = p.add(0x20).readPointer();
    if (data.isNull() || w <= 0 || h <= 0) return null;
    planes.push({w: w, h: h, stride: stride, data: data});
  }
  const w = planes[0].w, h = planes[0].h;
  const y0 = Math.max(16, (h >> 1) - 32), y1 = Math.min(h - 16, y0 + 64);
  const x0 = Math.max(16, w >> 2), x1 = Math.min(w - 16, x0 + (w >> 1));
  if (y1 <= y0 || x1 <= x0) return null;
  const rows = [];
  for (const p of planes) {
    const buf = p.data.add(y0 * p.stride).readByteArray((y1 - y0) * p.stride);
    rows.push(new Uint16Array(buf));
  }
  let n = 0, s = [0, 0, 0], d01 = 0, d21 = 0, mx = 0;
  const per = planes[0].stride >> 1;
  for (let y = 0; y < y1 - y0; y++) {
    for (let x = x0; x < x1; x++) {
      const i = y * per + x;
      const a = rows[0][i], b = rows[1][i], c = rows[2][i];
      s[0] += a; s[1] += b; s[2] += c;
      d01 += Math.abs(a - b); d21 += Math.abs(c - b);
      if (a > mx) mx = a; if (b > mx) mx = b; if (c > mx) mx = c;
      n++;
    }
  }
  return {n: n, w: w, h: h, set: set.toString(),
          mean: [s[0] / n, s[1] / n, s[2] / n], d01: d01 / n, d21: d21 / n, max: mx};
}

for (const name in P) {
  Interceptor.attach(base.add(P[name]), {
    onEnter(a) { this.task = a[1]; this.before = measure(a[1]); },
    onLeave() {
      if (!this.before) return;
      const after = measure(this.task);
      if (!after) return;
      const e = acc[name] || (acc[name] = []);
      if (e.length < 40) e.push([this.before, after]);
    }
  });
}
rpc.exports.summary = () => acc;
send({info: 'armed ' + Object.keys(P).length});
"""

subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
time.sleep(1.0)

pid = frida.spawn([EXE, sys.argv[1]])
session = frida.attach(pid)
script = session.create_script(JS.replace("POINTS", json.dumps(POINTS)))
script.on("message", lambda m, d: m["type"] == "send" and print("  ", m["payload"], flush=True))
script.load()
frida.resume(pid)
time.sleep(float(sys.argv[2]) if len(sys.argv) > 2 else 30.0)

acc = script.exports_sync.summary()
print()
for name in WATCH:
    runs = acc.get(name)
    if not runs:
        print("%-22s (没抓到)" % name)
        continue
    # 同一 run 的前后才能比,均值和比值必须取自同一块 tile
    ratios = sorted((a["d01"] + a["d21"]) / max(b["d01"] + b["d21"], 1e-6) for b, a in runs)
    r_med = ratios[len(ratios) // 2]
    before, after = runs[len(runs) // 2]
    print("%-22s %2d 块  色度 x%.4f  (四分位 %.4f ~ %.4f)"
          % (name, len(runs), r_med, ratios[len(ratios) // 4], ratios[-len(ratios) // 4]))
    print("%-22s     中位块 %dx%d  均值 %s -> %s   |p0-p1| %.1f->%.1f  |p2-p1| %.1f->%.1f"
          % ("", before["w"], before["h"],
             ["%.0f" % x for x in before["mean"]], ["%.0f" % x for x in after["mean"]],
             before["d01"], after["d01"], before["d21"], after["d21"]))
try:
    session.detach()
    frida.kill(pid)
except Exception:
    pass
