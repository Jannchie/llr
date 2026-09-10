r"""抓 ZcTask3DLut(RVA 0x36f340,「高级色彩复制」)的**全部**表 + 若干 tile。

export_3dlut_capture.py 只 dump 了 [desc+0x48](1D 色度压缩曲线),漏了真正的 3D 表;
这份补上 [desc+0x18](33*33*33*48 字节的 cell store),外加 [desc+0x10] / [desc+0x08]。
表的含义见 notes/static-3dlut.md,模型见 lut3d_model.py。

    python lut3d_capture_grid.py <名字> [--nr auto|off] [--tiles 0,1,2,3]

前提:Edit.exe 已打开目标 ARW,「高级色彩复制」已勾上。
产物 tools/lut3d_export_<名字>.npz(grid18 / blk10 / data48 / blk08 / t*_in / t*_out / desc)。
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

TOOLS = os.path.dirname(os.path.abspath(__file__))
RVA = 0x36F340

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
const WANT = WANTJS, NP = 3;
let seq = 0, dumped = false;
function readBlock(p, want, tag, extra) {
  const r = Process.findRangeByAddress(p);
  let n = want;
  if (r) { const avail = r.base.add(r.size).sub(p).toInt32(); if (avail < n) n = avail; }
  const info = {tag: tag, n: n, addr: p.toString(),
                range: r ? (r.base.toString() + '+' + r.size + ' ' + r.protection) : 'unknown'};
  if (extra) Object.assign(info, extra);
  send(info, p.readByteArray(n));
}
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
        send({tag: 'desc', lv: lv.toString(), desc: desc.toString(), i32: i32, i64: i64});
        const GRID = 33 * 33 * 33 * 48;              // 35937 cells x 3 outputs x 8 corners x int16
        readBlock(desc.add(0x18).readPointer(), GRID, 'grid18');
        readBlock(desc.add(0x10).readPointer(), GRID, 'blk10');
        readBlock(desc.add(0x48).readPointer(), 8 * 1024 * 1024, 'data48');
        readBlock(desc.add(0x08).readPointer(), 256 * 1024, 'blk08');
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
    tiles, nr = [0, 1, 2, 3], "auto"
    i = 0
    while i < len(args):
        if args[i] == "--tiles":
            tiles = [int(x) for x in args[i + 1].split(",")]
        elif args[i] == "--nr":
            nr = args[i + 1]
        i += 2
    st, info = {}, {}

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
        elif tag in ("grid18", "blk10", "data48", "blk08"):
            st[tag] = np.frombuffer(data, np.uint8).copy()
            info[tag] = {k: p[k] for k in ("n", "addr", "range")}
            print(f"   {tag} {p['n']} bytes @ {p['addr']} range {p['range']}", flush=True)
        elif tag == "lvnear":
            st["lv_near"] = np.frombuffer(data, np.uint8).copy()
        else:
            a = np.frombuffer(data, "<u2").reshape(p["h"], p["w"], p["np"]).copy()
            st[f"t{p['idx']}_{tag}"] = a
            st[f"t{p['idx']}_meta"] = np.array(p["meta"], np.int32)
            print(f"   {tag} tile{p['idx']} {p['w']}x{p['h']}", flush=True)

    session = frida.attach(edit_pid())
    script = session.create_script(JS.replace("WANTJS", json.dumps(tiles)).replace("RVAJS", str(RVA)))
    script.on("message", on_message)
    script.load()
    r = subprocess.run([sys.executable, os.path.join(TOOLS, "edit_export.py"), nr, name],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=TOOLS)
    print(r.stdout.strip()[-2000:] if r.stdout.strip() else r.stderr[-2000:])
    deadline = time.time() + 240
    while time.time() < deadline and not all(f"t{i}_out" in st for i in tiles):
        time.sleep(0.5)
    time.sleep(1.0)
    session.detach()
    st["desc"] = np.array(json.dumps(info))
    path = os.path.join(TOOLS, f"lut3d_export_{name}.npz")
    np.savez_compressed(path, **st)
    print("SAVED", path, sorted(st))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
