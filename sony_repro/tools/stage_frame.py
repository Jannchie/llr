r"""把引擎在任意一个阶段边界上的整幅画面拼出来。

tile 在整幅里的位置在 task 的 `+0x48..0x54`,平面对应的是外扩过的 `+0x18..0x24`,
两者之差就是平面内的偏移 —— 所有 ZcTask 共用这套基类字段,所以任一阶段都能拼。
有了它就能拿引擎的中间结果逐像素对我们的复刻,不用再靠整幅统计量猜。

用法: python stage_frame.py <ARW> <任务名>:<in|out> [任务名:when ...] -- [秒数]
例:   python stage_frame.py x.ARW ZcTaskMainGamma:out ZcTaskYCC2RGB:out
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
STEP = 4
W, H = 7008, 4672

args = [a for a in sys.argv[2:] if a != "--"]
arw = sys.argv[1]
secs = 30.0
if args and args[-1].replace(".", "").isdigit():
    secs = float(args.pop())
with open(os.path.join(SCR, "task_execs.json"), encoding="utf-8") as f:
    EXECS = json.load(f)
SPECS = {a: EXECS[a.split(":")[0]]["rva"] for a in args}

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
const SPECS = SPECSJSON, STEP = STEPV;

function emit(task, tag) {
  const set = task.add(8).readPointer();
  if (set.isNull()) return;
  const pad = [task.add(0x18).readS32(), task.add(0x1c).readS32()];
  const dst = [task.add(0x48).readS32(), task.add(0x4c).readS32(),
               task.add(0x50).readS32(), task.add(0x54).readS32()];
  const w = dst[2] - dst[0], h = dst[3] - dst[1];
  if (w <= 0 || h <= 0) return;
  const ox = dst[0] - pad[0], oy = dst[1] - pad[1];
  const nw = Math.ceil(w / STEP), nh = Math.ceil(h / STEP);
  const buf = new ArrayBuffer(nw * nh * 3 * 2);
  const o = new Uint16Array(buf);
  for (let k = 0; k < 3; k++) {
    const p = set.add(8 + k * 8).readPointer();
    // planar set 的平面数不是恒为 3:ZcTaskRawNRSIMD 和 ZcTaskSIMDITP 的**入口**
    // 只有 1 个。这里原先是 return,于是这两级抓出来一片空白,看着像钩子没生效
    // —— 而执行普查明明数得到次数。跳过缺的平面,对应通道留 0。
    if (p.isNull()) continue;
    const stride = p.add(0x14).readS32(), data = p.add(0x20).readPointer();
    const ph = p.add(12).readS32();
    for (let j = 0; j < nh; j++) {
      if (oy + j * STEP >= ph) break;
      const row = new Uint16Array(data.add((oy + j * STEP) * stride).readByteArray(stride));
      for (let i = 0; i < nw; i++) o[(j * nw + i) * 3 + k] = row[ox + i * STEP];
    }
  }
  send({tag: tag, dst: dst, nw: nw, nh: nh}, buf);
}

for (const spec in SPECS) {
  const when = spec.split(':')[1];
  Interceptor.attach(base.add(SPECS[spec]), {
    onEnter(a) { this.task = a[1]; if (when === 'in') emit(a[1], spec); },
    onLeave() { if (when === 'out') emit(this.task, spec); }
  });
}
send({info: 'armed ' + Object.keys(SPECS).length});
""".replace("SPECSJSON", json.dumps(SPECS)).replace("STEPV", str(STEP))

frames = {k: np.zeros((H // STEP + 2, W // STEP + 2, 3), np.uint16) for k in SPECS}
hits = dict.fromkeys(SPECS, 0)


def on_message(msg, data):
    if msg["type"] != "send":
        print("  !", msg, flush=True)
        return
    p = msg["payload"]
    if "info" in p:
        print("  ", p, flush=True)
        return
    a = np.frombuffer(data, dtype="<u2").reshape(p["nh"], p["nw"], 3)
    x, y = p["dst"][0] // STEP, p["dst"][1] // STEP
    frames[p["tag"]][y:y + p["nh"], x:x + p["nw"]] = a
    hits[p["tag"]] += 1


subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
time.sleep(1.0)
pid = frida.spawn([EXE, arw])
session = frida.attach(pid)
script = session.create_script(JS)
script.on("message", on_message)
script.load()
frida.resume(pid)
time.sleep(secs)

print("\n拼上的块数:", hits)
out = {k.replace(":", "_"): v for k, v in frames.items()}
path = os.path.join(SCR, "stage_frames.npz")
np.savez_compressed(path, step=np.array([STEP, W, H]), **out)
print("SAVED", path)
try:
    session.detach()
    frida.kill(pid)
except Exception:
    pass
