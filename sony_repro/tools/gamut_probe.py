r"""输出色彩空间转换:哪一个矩阵在跑,以及那三张运行时生成的 LUT 长什么样。

`ZcTaskMarble` 开头按 `settings+0x44` 二选一调 FUN_140196170 / FUN_140196500,
两者都是「去 gamma 查表 -> 3x3(行和为 1) -> 钳 14 位 -> 再 gamma 查表」。
三张表由 FUN_140193530 惰性生成,静态文件里是空的,必须实机抓。

用法: python gamut_probe.py <ARW> [秒数]
"""
import os

import subprocess
import sys
import time

import frida
import numpy as np

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
SCR = os.path.dirname(os.path.abspath(__file__))
CONV = {"gamutA_0x196170": 0x196170, "gamutB_0x196500": 0x196500}
LUTS = {"encode_0x61bd60": 0x61BD60, "decodeB_0x63bd60": 0x63BD60, "decodeA_0x65bd60": 0x65BD60}
N = 16384

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
const CONV = CONVJSON, LUTS = LUTSJSON, N = NENT;
const count = {}, samples = [];
let dumped = null;

function planeStats(p, rect) {
  const x0 = rect.add(8).readS32(), y0 = rect.add(12).readS32();
  const x1 = rect.add(16).readS32(), y1 = rect.add(20).readS32();
  const stride = p.add(0x14).readS32(), data = p.add(0x20).readPointer();
  const yb = Math.min(y1, y0 + 32);
  if (yb <= y0 || x1 <= x0) return null;
  const buf = data.add(y0 * stride).readByteArray((yb - y0) * stride);
  return {a: new Uint16Array(buf), per: stride >> 1, x0: x0, x1: x1, rows: yb - y0};
}

function trio(p0, p1, p2, rect) {
  const s = [planeStats(p0, rect), planeStats(p1, rect), planeStats(p2, rect)];
  if (!s[0] || !s[1] || !s[2]) return null;
  let n = 0, m = [0, 0, 0], d01 = 0, d21 = 0;
  for (let y = 0; y < s[0].rows; y++)
    for (let x = s[0].x0; x < s[0].x1; x++) {
      const i = y * s[0].per + x;
      const a = s[0].a[i], b = s[1].a[i], c = s[2].a[i];
      m[0] += a; m[1] += b; m[2] += c;
      d01 += Math.abs(a - b); d21 += Math.abs(c - b); n++;
    }
  return {mean: [m[0] / n, m[1] / n, m[2] / n], d01: d01 / n, d21: d21 / n};
}

for (const name in CONV) {
  Interceptor.attach(base.add(CONV[name]), {
    onEnter(a) {
      count[name] = (count[name] || 0) + 1;
      this.n = name; this.p = [a[0], a[1], a[2]]; this.rect = a[3];
      this.before = samples.length < 6 ? trio(a[0], a[1], a[2], a[3]) : null;
    },
    onLeave() {
      if (!this.before) return;
      const after = trio(this.p[0], this.p[1], this.p[2], this.rect);
      if (after) samples.push([this.n, this.before, after]);
      if (!dumped) {
        // rpc 不能直接回传 ArrayBuffer 的字典(会挂住),转成普通数组
        dumped = {};
        for (const l in LUTS) {
          const v = new Int16Array(base.add(LUTS[l]).readByteArray(N * 2));
          dumped[l] = Array.prototype.slice.call(v);
        }
      }
    }
  });
}
rpc.exports.summary = () => [count, samples];
rpc.exports.luts = () => dumped;
send({info: 'armed'});
"""

subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
time.sleep(1.0)
pid = frida.spawn([EXE, sys.argv[1]])
session = frida.attach(pid)
script = session.create_script(
    JS.replace("CONVJSON", repr(CONV).replace("'", '"'))
      .replace("LUTSJSON", repr(LUTS).replace("'", '"')).replace("NENT", str(N)))
script.on("message", lambda m, d: m["type"] == "send" and print("  ", m["payload"], flush=True))
script.load()
frida.resume(pid)
time.sleep(float(sys.argv[2]) if len(sys.argv) > 2 else 25.0)

count, samples = script.exports_sync.summary()
print("\n调用次数:", count)
for name, b, a in samples[:4]:
    print("  %s 均值 %s -> %s   |R-G| %.1f->%.1f (x%.3f)  |B-G| %.1f->%.1f (x%.3f)"
          % (name, ["%.0f" % x for x in b["mean"]], ["%.0f" % x for x in a["mean"]],
             b["d01"], a["d01"], a["d01"] / max(b["d01"], 1e-6),
             b["d21"], a["d21"], a["d21"] / max(b["d21"], 1e-6)))

luts = script.exports_sync.luts()
if luts:
    out = {}
    for name, raw in luts.items():
        v = np.array(raw, dtype=np.int32)
        # 编码表存的是 uint16,越过 32767 会被 Int16Array 读成负数
        out[name] = v if "decode" in name else v & 0xFFFF
        print("\n%s  [0]=%d [1]=%d [1024]=%d [8192]=%d [16383]=%d  max=%d"
              % (name, v[0], v[1], v[1024], v[8192], v[16383], v.max()))
    np.savez(os.path.join(SCR, "gamut_luts.npz"), **out)
    print("\nSAVED gamut_luts.npz")
try:
    session.detach()
    frida.kill(pid)
except Exception:
    pass
