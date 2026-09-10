r"""导出时刻抓 ZcTask3DLut(RVA 0x36f340,「高级色彩复制」那一级)的表:描述符 + 数据块 + 若干 tile 入/出。

反汇编开头:
    lv   = [[task+0x68] + 0xc8]
    desc = [lv + 0x49920]            # 描述符
    +0x20,+0x28 (imul → 步长), +0x2c/+0x30 (14-x / 16-x 的移位量), +0x34,+0x38 (维度), +0x48 = 数据指针(+0x10000 后使用)
描述符前 0x60 字节按 int32 和 int64 两种读法都存;数据从 [desc+0x48] 起 dump DATA_BYTES 字节。

    python export_3dlut_capture.py <导出文件名> [--nr auto|off] [--tiles 0,1,2] [--bytes 8388608]
产物 tools/lut3d_export_<name>.npz
"""
import json
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import frida  # noqa: E402
import numpy as np  # noqa: E402
import win32gui  # noqa: E402
import win32process  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RVA = 0x36F340

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
const WANT = WANTJS, NP = 3, DATA_BYTES = BYTESJS;
let seq = 0, dumped = false;
function grab(task, tag, idx) {
  const set = task.add(8).readPointer(); if (set.isNull()) return;
  const meta = []; for (let o = 0; o < 0x60; o += 4) meta.push(task.add(o).readS32());
  const descs = [];
  for (let k = 0; k < NP; k++) { const p = set.add(8 + k * 8).readPointer(); if (p.isNull()) return;
    descs.push({w: p.add(8).readS32(), h: p.add(12).readS32(), stride: p.add(0x14).readS32(), data: p.add(0x20)}); }
  const w = descs[0].w, h = descs[0].h; if (w <= 0 || h <= 0 || w > 4096 || h > 4096) return;
  const buf = new ArrayBuffer(w * h * NP * 2); const o = new Uint16Array(buf);
  for (let k = 0; k < NP; k++) { const d = descs[k]; const raw = new Uint16Array(d.data.readPointer().readByteArray(d.h * d.stride)); const per = d.stride >> 1;
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) o[(y * w + x) * NP + k] = raw[y * per + x]; }
  send({tag: tag, idx: idx, w: w, h: h, np: NP, meta: meta}, buf);
}
Interceptor.attach(base.add(RVAJS), {
  onEnter(a) {
    this.idx = seq++; this.hit = WANT.indexOf(this.idx) >= 0; this.task = a[1];
    if (!dumped) {
      dumped = true;
      try {
        const lv = a[1].add(0x68).readPointer().add(0xc8).readPointer();
        const desc = lv.add(0x49920).readPointer();
        const i32 = [], i64 = [];
        for (let o = 0; o < 0x60; o += 4) i32.push(desc.add(o).readS32());
        for (let o = 0; o < 0x60; o += 8) i64.push(desc.add(o).readPointer().toString());
        const data = desc.add(0x48).readPointer();
        send({tag: 'desc', lv: lv.toString(), desc: desc.toString(), i32: i32, i64: i64, data: data.toString()});
        // 找这块数据的内存范围,避免读越界
        const r = Process.findRangeByAddress(data);
        send({info: 'data range ' + (r ? (r.base + ' size ' + r.size + ' ' + r.protection) : 'unknown')});
        // 堆块可能跨 Process.findRangeByAddress 报的范围,先按要的长度读,失败再退到范围内。
        let n = DATA_BYTES, buf = null;
        try { buf = data.readByteArray(n); } catch (e) {
          if (r) { n = r.base.add(r.size).sub(data).toInt32(); buf = data.readByteArray(n); send({info: 'fell back to range: ' + n}); }
        }
        send({tag: 'data', n: n}, buf);
        // 也把 lv+0x49920 前后一段读出来,看看有没有相邻的描述符
        send({tag: 'lvnear'}, lv.add(0x49900).readByteArray(0x100));
      } catch (e) { send({info: 'desc dump failed: ' + e}); }
    }
    if (this.hit) grab(a[1], 'in', this.idx);
  },
  onLeave() { if (this.hit) grab(this.task, 'out', this.idx); }
});
send({info: 'armed'});
"""


def edit_pid():
    found = []

    def cb(h, _):
        if win32gui.GetWindowText(h) == "Edit" and win32gui.GetClassName(h).startswith("Afx"):
            found.append(win32process.GetWindowThreadProcessId(h)[1])
        return True
    win32gui.EnumWindows(cb, None)
    return found[0]


def main():
    args = sys.argv[1:]
    name = args.pop(0)
    tiles, nr, nbytes = [0, 1, 2], "auto", 8 * 1024 * 1024
    i = 0
    while i < len(args):
        if args[i] == "--tiles":
            tiles = [int(x) for x in args[i + 1].split(",")]
        elif args[i] == "--nr":
            nr = args[i + 1]
        elif args[i] == "--bytes":
            nbytes = int(args[i + 1])
        i += 2
    st = {}
    info = {}

    def on_message(msg, data):
        if msg["type"] != "send":
            print("  !", msg, flush=True)
            return
        p = msg["payload"]
        if "info" in p:
            print("  ", p["info"], flush=True)
            return
        tag = p["tag"]
        if tag == "desc":
            info.update(p)
            print("   desc", p, flush=True)
        elif tag == "data":
            st["lut_data"] = np.frombuffer(data, np.uint8).copy()
            print(f"   data {p['n']} bytes", flush=True)
        elif tag == "lvnear":
            st["lv_near"] = np.frombuffer(data, np.uint8).copy()
        else:
            a = np.frombuffer(data, "<u2").reshape(p["h"], p["w"], p["np"]).copy()
            st[f"t{p['idx']}_{tag}"] = a
            st[f"t{p['idx']}_meta"] = np.array(p["meta"], np.int32)
            print(f"   {tag} tile{p['idx']} {p['w']}x{p['h']}", flush=True)

    session = frida.attach(edit_pid())
    script = session.create_script(JS.replace("WANTJS", json.dumps(tiles)).replace("RVAJS", str(RVA)).replace("BYTESJS", str(nbytes)))
    script.on("message", on_message)
    script.load()
    r = subprocess.run([sys.executable, os.path.join(HERE, "edit_export.py"), nr, name],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-300:])
    deadline = time.time() + 180
    while time.time() < deadline and not all(f"t{i}_out" in st for i in tiles):
        time.sleep(0.5)
    time.sleep(1.0)
    session.detach()
    st["desc"] = np.array(json.dumps(info))
    path = os.path.join(HERE, f"lut3d_export_{name}.npz")
    np.savez_compressed(path, **st)
    print("SAVED", path, sorted(k for k in st))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
