r"""把某个阶段的**若干块 tile 原分辨率**的入口/出口整块抓下来。

`stage_frame.py` 是 STEP=4 抽样的 —— 拼整幅够用,但研究锐化/降噪这种高频算法时
抽样本身就把要看的东西抹掉了。这里不抽样,只抓少数几块 tile,但一个像素不少。

平面描述符: +8 宽 +0xc 高 +0x14 行距 +0x20 数据; 第 i 个平面 = *(set + 8 + i*8)。
task 的 +0x18..0x24 是外扩矩形(平面坐标原点),+0x30..0x3c 是有效矩形,
+0x48..0x54 是 tile 在整幅里的位置。

用法(必须用 Windows 的 Python,要 frida):
    python tile_dump.py <ARW> <任务名> [--tiles 0,17] [--planes 3] [--secs 40]
                        [--out 名字]
输出 tiles_<名字>.npz:  t<i>_in / t<i>_out (h,w,planes) uint16, t<i>_meta
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


def _opt(flag, default):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
const WANT = WANTJS, NPLANE = NPLANEJS;
let seq = 0;

function grab(task, tag, idx) {
  const set = task.add(8).readPointer();
  if (set.isNull()) return;
  const meta = [];
  for (let o = 0; o < 0x60; o += 4) meta.push(task.add(o).readS32());
  const descs = [];
  for (let k = 0; k < NPLANE; k++) {
    const p = set.add(8 + k * 8).readPointer();
    if (p.isNull()) return;
    descs.push({w: p.add(8).readS32(), h: p.add(12).readS32(),
                stride: p.add(0x14).readS32(), data: p.add(0x20)});
  }
  const w = descs[0].w, h = descs[0].h;
  if (w <= 0 || h <= 0 || w > 4096 || h > 4096) return;
  const buf = new ArrayBuffer(w * h * NPLANE * 2);
  const o = new Uint16Array(buf);
  for (let k = 0; k < NPLANE; k++) {
    const d = descs[k];
    const raw = new Uint16Array(d.data.readPointer().readByteArray(d.h * d.stride));
    const per = d.stride >> 1;
    for (let y = 0; y < h; y++)
      for (let x = 0; x < w; x++) o[(y * w + x) * NPLANE + k] = raw[y * per + x];
  }
  send({tag: tag, idx: idx, w: w, h: h, np: NPLANE, meta: meta,
        descs: descs.map(d => [d.w, d.h, d.stride])}, buf);
}

Interceptor.attach(base.add(RVAJS), {
  onEnter(a) {
    this.idx = seq++;
    this.hit = WANT.indexOf(this.idx) >= 0;
    if (this.hit) { this.task = a[1]; grab(a[1], 'in', this.idx); }
  },
  onLeave() { if (this.hit) grab(this.task, 'out', this.idx); }
});
send({info: 'armed'});
"""


def main():
    arw, task = sys.argv[1], sys.argv[2]
    tiles = [int(x) for x in _opt("--tiles", "17").split(",")]
    nplane = int(_opt("--planes", "3"))
    secs = float(_opt("--secs", "40"))
    name = _opt("--out", task.replace("ZcTask", ""))
    with open(os.path.join(SCR, "task_execs.json"), encoding="utf-8") as f:
        rva = json.load(f)[task]["rva"]

    js = (JS.replace("WANTJS", json.dumps(tiles)).replace("NPLANEJS", str(nplane))
            .replace("RVAJS", str(rva)))
    store = {}

    def on_message(msg, data):
        if msg["type"] != "send":
            print("  !", msg, flush=True)
            return
        p = msg["payload"]
        if "info" in p:
            print("  ", p, flush=True)
            return
        a = np.frombuffer(data, "<u2").reshape(p["h"], p["w"], p["np"])
        store[f"t{p['idx']}_{p['tag']}"] = a.copy()
        store[f"t{p['idx']}_meta"] = np.array(p["meta"], np.int32)
        store[f"t{p['idx']}_descs"] = np.array(p["descs"], np.int32)
        print(f"   {p['tag']} tile{p['idx']} {p['w']}x{p['h']}x{p['np']}", flush=True)

    subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
    time.sleep(1.0)
    pid = frida.spawn([EXE, arw])
    session = frida.attach(pid)
    script = session.create_script(js)
    script.on("message", on_message)
    script.load()
    frida.resume(pid)
    deadline = time.time() + secs
    while time.time() < deadline:
        if all(f"t{i}_out" in store for i in tiles):
            break
        time.sleep(0.2)
    try:
        session.detach()
        frida.kill(pid)
    except Exception:
        pass

    # 来源必须跟着数据走。第一版没存,结果 tiles_SIMDSharpness.npz 事后无法确认
    # 是哪张 ARW —— 拿它和另一张图的 Spica dump 配对时,位置对上了、内容差 14411,
    # 白查一轮才想明白是两张图。
    store["source"] = np.array([os.path.abspath(arw), task])
    path = os.path.join(SCR, f"tiles_{name}.npz")
    np.savez_compressed(path, **store)
    print("SAVED", path, list(store))


if __name__ == "__main__":
    main()
