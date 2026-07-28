r"""逐像素真值链 + 同一次调用的整块平面 —— 一次抓齐,不再有配对问题。

`spica_trace.py` 已经证明:分类链(rng/mid/idx/方向)4000/4000 精确,卷积 S 也
**逐位精确**(xd·128 == S-512·I0)。但和 `tiles_SIMDSpica.npz` 对不上 ——
那份 npz 抓的是第 12 次 ZcTaskSIMDSpica 调用,trace 抓的是第一次,
Spica 一张图要跑 35 次,不同次的参数不同。教训:**逐像素 trace 和整块 dump
必须锁在同一次调用上**,否则一切数值对照都是假的。

用法:  python spica_trace2.py <ARW> [--task 0] [--n 6000] [--planes 3] [--secs 150]
输出 spica_trace2.npz:  rows(json 串) / in / out / descs
"""
import json
import os
import struct
import subprocess
import sys
import time

import frida
import numpy as np

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
SCR = os.path.dirname(os.path.abspath(__file__))
FILTER, GAIN = 0x39EAD0, 0x35A270
HALF = 4

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
const N = NJS, HALF = HALFJS, WANT = WANTJS, NPLANE = NPLANEJS;
const pend = {};
const rows = [];
let seq = 0, active = false, sent = false;

function grab(task, tag) {
  const set = task.add(8).readPointer();
  if (set.isNull()) return;
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
  send({tag: tag, w: w, h: h, np: NPLANE,
        descs: descs.map(d => [d.w, d.h, d.stride])}, buf);
}

Interceptor.attach(base.add(TASKJS), {
  onEnter(a) {
    this.hit = (seq++ === WANT);
    if (this.hit) { this.task = a[1]; active = true; grab(a[1], 'in'); }
  },
  onLeave() {
    if (!this.hit) return;
    grab(this.task, 'out');
    active = false;
    if (!sent) { sent = true; send({rows: rows}); }
  }
});

Interceptor.attach(base.add(FILTERJS), {
  onEnter(a) {
    if (!active || rows.length >= N) return;
    const x = a[8].toInt32(), y = a[9].toInt32();
    const patch = [];
    try {
      for (let r = 1; r <= 7; r++) {
        const p = a[r], line = [];
        for (let c = -HALF; c <= HALF; c++) line.push(p.add((x + c) * 2).readU16());
        patch.push(line);
      }
    } catch (e) { return; }
    pend[this.threadId] = {x: x, y: y, idx: a[10].toInt32(),
                           dx: a[11].toInt32() << 24 >> 24,
                           dy: a[12].toInt32() << 24 >> 24, patch: patch};
  }
});

Interceptor.attach(base.add(GAINJS), {
  onEnter(a) {
    this.rec = null;
    if (!active || rows.length >= N) return;
    const rec = pend[this.threadId];
    if (!rec) return;
    delete pend[this.threadId];
    rec.xd = a[4].and(0xffffffff).toString();
    rec.rng = a[5].and(0xffffffff).toString();
    rec.mid = a[6].and(0xffffffff).toString();
    this.rec = rec;
    this.g = a[0];          // G 是这次调用**写出来**的,只能 onLeave 读
  },
  onLeave() {
    if (!this.rec) return;
    try { this.rec.G = this.g.readFloat(); } catch (e) {}
    rows.push(this.rec);
  }
});
send({info: 'armed'});
"""


def f32(bits):
    return struct.unpack("<f", struct.pack("<I", int(bits, 0) & 0xFFFFFFFF))[0]


def main():
    arw = sys.argv[1]

    def opt(f, d):
        return sys.argv[sys.argv.index(f) + 1] if f in sys.argv else d

    want, n = int(opt("--task", "0")), int(opt("--n", "6000"))
    nplane, secs = int(opt("--planes", "3")), float(opt("--secs", "150"))
    with open(os.path.join(SCR, "task_execs.json"), encoding="utf-8") as f:
        rva = json.load(f)["ZcTaskSIMDSpica"]["rva"]
    js = (JS.replace("FILTERJS", str(FILTER)).replace("GAINJS", str(GAIN))
            .replace("TASKJS", str(rva)).replace("WANTJS", str(want))
            .replace("NPLANEJS", str(nplane)).replace("NJS", str(n))
            .replace("HALFJS", str(HALF)))
    store, got = {}, {}

    def on_message(msg, data):
        if msg["type"] != "send":
            print("  !", msg, flush=True)
            return
        p = msg["payload"]
        if "info" in p:
            print("  ", p, flush=True)
        elif "rows" in p:
            got["rows"] = p["rows"]
            print(f"   逐像素 {len(p['rows'])} 条", flush=True)
        else:
            store[p["tag"]] = np.frombuffer(data, "<u2").reshape(
                p["h"], p["w"], p["np"]).copy()
            store["descs"] = np.array(p["descs"], np.int32)
            print(f"   {p['tag']} {p['w']}x{p['h']}x{p['np']}", flush=True)

    subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
    time.sleep(1.0)
    pid = frida.spawn([EXE, arw])
    session = frida.attach(pid)
    script = session.create_script(js)
    script.on("message", on_message)
    script.load()
    frida.resume(pid)
    deadline = time.time() + secs
    while time.time() < deadline and not ("out" in store and "rows" in got):
        time.sleep(0.3)
    try:
        session.detach()
        frida.kill(pid)
    except Exception:
        pass

    if "out" not in store or "rows" not in got:
        print("没抓全", list(store), list(got))
        return
    for r in got["rows"]:
        r["xd"], r["rng"], r["mid"] = f32(r["xd"]), f32(r["rng"]), f32(r["mid"])
    store["rows"] = np.array([json.dumps(got["rows"])])
    store["source"] = np.array([os.path.abspath(arw), f"task#{want}"])
    path = os.path.join(SCR, "spica_trace2.npz")
    np.savez_compressed(path, **store)
    print("SAVED", path)


if __name__ == "__main__":
    main()
