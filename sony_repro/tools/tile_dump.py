r"""把某个阶段的**若干块 tile 原分辨率**的入口/出口整块抓下来。

`stage_frame.py` 是 STEP=4 抽样的 —— 拼整幅够用,但研究锐化/降噪这种高频算法时
抽样本身就把要看的东西抹掉了。这里不抽样,只抓少数几块 tile,但一个像素不少。

平面描述符: +8 宽 +0xc 高 +0x14 行距 +0x20 数据; 第 i 个平面 = *(set + 8 + i*8)。
task 的 +0x18..0x24 是外扩矩形(平面坐标原点),+0x30..0x3c 是有效矩形,
+0x48..0x54 是 tile 在整幅里的位置。

用法(必须用 Windows 的 Python,要 frida):
    python tile_dump.py <ARW> <任务名> [--tiles 0,17] [--planes 3] [--secs 40]
                        [--out 名字] [--planes-out N]
输出 tiles_<名字>.npz:  t<i>_in / t<i>_out (h,w,planes) uint16, t<i>_meta

`--planes-out` 给**改变平面数**的阶段用,默认跟着 `--planes`。ITP 就是这一种:
入口 1 个平面(马赛克),出口 3 个(它新建三平面写回 `task->planes`)。两头用同一个
平面数去读,出口会当成 1 平面读走三分之一 —— 形状仍然合理,不会报错。

`--min-h` 把**预览路径**挡在外面,序号只在够大的调用上递增。Edit 打开文件先渲一遍
预览,那批 tile 高 684~696,全分辨率的高 1132 —— 宽度两者都在 1108~1134,**分不开**,
只有高度分得开。不加这个,`--tiles 0,1` 抓到哪一批全看运气(实测同一个工具在三张图
上一张抓到全分辨率、两张抓到预览),而预览的值域被压过,拿去做数值比对是白做。
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
const WANT = WANTJS, NPLANE = NPLANEJS, NPLANE_OUT = NPLANEOUTJS;
let seq = 0;

function grab(task, tag, idx, nplane) {
  const set = task.add(8).readPointer();
  if (set.isNull()) return;
  const meta = [];
  for (let o = 0; o < 0x60; o += 4) meta.push(task.add(o).readS32());
  const descs = [];
  for (let k = 0; k < nplane; k++) {
    const p = set.add(8 + k * 8).readPointer();
    if (p.isNull()) return;
    descs.push({w: p.add(8).readS32(), h: p.add(12).readS32(),
                stride: p.add(0x14).readS32(), data: p.add(0x20)});
  }
  const w = descs[0].w, h = descs[0].h;
  if (w <= 0 || h <= 0 || w > 4096 || h > 4096) return;
  const buf = new ArrayBuffer(w * h * nplane * 2);
  const o = new Uint16Array(buf);
  for (let k = 0; k < nplane; k++) {
    const d = descs[k];
    const raw = new Uint16Array(d.data.readPointer().readByteArray(d.h * d.stride));
    const per = d.stride >> 1;
    for (let y = 0; y < h; y++)
      for (let x = 0; x < w; x++) o[(y * w + x) * nplane + k] = raw[y * per + x];
  }
  send({tag: tag, idx: idx, w: w, h: h, np: nplane, meta: meta,
        descs: descs.map(d => [d.w, d.h, d.stride])}, buf);
}

// 够不够大 —— 序号只在够大的调用上递增,否则预览那批会把 0,1 占掉。
function bigEnough(task) {
  try {
    const set = task.add(8).readPointer();
    if (set.isNull()) return false;
    const p = set.add(8).readPointer();
    if (p.isNull()) return false;
    return p.add(0xc).readS32() >= MINHJS;
  } catch (e) { return false; }
}

Interceptor.attach(base.add(RVAJS), {
  onEnter(a) {
    if (!bigEnough(a[1])) return;
    this.idx = seq++;
    this.hit = WANT.indexOf(this.idx) >= 0;
    if (this.hit) { this.task = a[1]; grab(a[1], 'in', this.idx, NPLANE); }
  },
  onLeave() { if (this.hit) grab(this.task, 'out', this.idx, NPLANE_OUT); }
});
send({info: 'armed'});
"""


def main():
    arw, task = sys.argv[1], sys.argv[2]
    tiles = [int(x) for x in _opt("--tiles", "17").split(",")]
    nplane = int(_opt("--planes", "3"))
    nplane_out = int(_opt("--planes-out", str(nplane)))
    min_h = int(_opt("--min-h", "0"))
    secs = float(_opt("--secs", "40"))
    name = _opt("--out", task.replace("ZcTask", ""))
    with open(os.path.join(SCR, "task_execs.json"), encoding="utf-8") as f:
        rva = json.load(f)[task]["rva"]

    js = (JS.replace("WANTJS", json.dumps(tiles)).replace("NPLANEJS", str(nplane))
            .replace("NPLANEOUTJS", str(nplane_out)).replace("RVAJS", str(rva))
            .replace("MINHJS", str(min_h)))
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
