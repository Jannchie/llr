r"""导出时刻的 tile_dump:attach 到已运行的 Edit,挂若干 task 的 exec,用 edit_export.py
触发一次导出,把每个 task 前 K 块 tile 的入口/出口平面存成 tile_dump.py 同格式的 npz
(`t<i>_in / t<i>_out (h,w,planes) uint16, t<i>_meta, t<i>_descs, source`)。

    python export_tile_dump.py <导出文件名> <Task[:planes[:planes_out]]> ... [--tiles 0,1,2] [--nr auto|off|manual]
                               [--sliders 量,色彩降噪,边缘降噪] [--suffix xxx]
--sliders 原样交给 edit_export.py(手动档);--suffix 让产物叫 tiles_<Task>_export_<suffix>.npz,免得覆盖。

例:python export_tile_dump.py DSC03036-v1 ZcTaskSIMDSharpness ZcTaskSIMDSpica ZcTaskSIMDMarble
产物:tiles_<Task去掉ZcTask>_export.npz
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

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
const SPECS = SPECSJS, WANT = WANTJS;
function grab(task, tag, name, idx, nplane) {
  const set = task.add(8).readPointer();
  if (set.isNull()) return;
  const meta = [];
  for (let o = 0; o < 0x60; o += 4) meta.push(task.add(o).readS32());
  const descs = [];
  for (let k = 0; k < nplane; k++) {
    const p = set.add(8 + k * 8).readPointer();
    if (p.isNull()) return;
    descs.push({w: p.add(8).readS32(), h: p.add(12).readS32(), stride: p.add(0x14).readS32(), data: p.add(0x20)});
  }
  const w = descs[0].w, h = descs[0].h;
  if (w <= 0 || h <= 0 || w > 4096 || h > 4096) return;
  const buf = new ArrayBuffer(w * h * nplane * 2);
  const o = new Uint16Array(buf);
  for (let k = 0; k < nplane; k++) {
    const d = descs[k];
    const raw = new Uint16Array(d.data.readPointer().readByteArray(d.h * d.stride));
    const per = d.stride >> 1;
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) o[(y * w + x) * nplane + k] = raw[y * per + x];
  }
  send({tag: tag, name: name, idx: idx, w: w, h: h, np: nplane, meta: meta, descs: descs.map(d => [d.w, d.h, d.stride])}, buf);
}
for (const name in SPECS) {
  const s = SPECS[name];
  let seq = 0;
  Interceptor.attach(base.add(s.rva), {
    onEnter(a) {
      this.idx = seq++;
      this.hit = WANT.indexOf(this.idx) >= 0;
      if (this.hit) { this.task = a[1]; grab(a[1], 'in', name, this.idx, s.np); }
    },
    onLeave() { if (this.hit) grab(this.task, 'out', name, this.idx, s.np_out); }
  });
}
send({info: 'armed ' + Object.keys(SPECS).length});
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
    args = [a for a in sys.argv[1:]]
    name = args.pop(0)
    tiles = [0, 1, 2]
    nr = "auto"
    sliders = None
    suffix = ""
    specs = {}
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--tiles":
            tiles = [int(x) for x in args[i + 1].split(",")]
            i += 2
            continue
        if a == "--nr":
            nr = args[i + 1]
            i += 2
            continue
        if a == "--sliders":
            sliders = args[i + 1]
            i += 2
            continue
        if a == "--suffix":
            suffix = "_" + args[i + 1]
            i += 2
            continue
        parts = a.split(":")
        specs[parts[0]] = {"np": int(parts[1]) if len(parts) > 1 else 3,
                           "np_out": int(parts[2]) if len(parts) > 2 else (int(parts[1]) if len(parts) > 1 else 3)}
        i += 1
    with open(os.path.join(HERE, "task_execs.json"), encoding="utf-8") as f:
        execs = json.load(f)
    for k in specs:
        specs[k]["rva"] = execs[k]["rva"]
    stores = {k: {} for k in specs}

    def on_message(msg, data):
        if msg["type"] != "send":
            print("  !", msg, flush=True)
            return
        p = msg["payload"]
        if "info" in p:
            print("  ", p["info"], flush=True)
            return
        a = np.frombuffer(data, "<u2").reshape(p["h"], p["w"], p["np"]).copy()
        st = stores[p["name"]]
        st[f"t{p['idx']}_{p['tag']}"] = a
        st[f"t{p['idx']}_meta"] = np.array(p["meta"], np.int32)
        st[f"t{p['idx']}_descs"] = np.array(p["descs"], np.int32)
        print(f"   {p['name']} {p['tag']} tile{p['idx']} {p['w']}x{p['h']}x{p['np']}", flush=True)

    session = frida.attach(edit_pid())
    script = session.create_script(JS.replace("SPECSJS", json.dumps(specs)).replace("WANTJS", json.dumps(tiles)))
    script.on("message", on_message)
    script.load()
    cmd = [sys.executable, os.path.join(HERE, "edit_export.py"), nr, name]
    if sliders:
        cmd += ["--sliders", sliders]
    r = subprocess.run(cmd,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-300:])
    # 文件大小稳定不等于管线跑完(TIFF 头先落地),等所有要的 tile 都到齐再 detach。
    deadline = time.time() + 180
    while time.time() < deadline and not all(f"t{i}_out" in st for st in stores.values() for i in tiles):
        time.sleep(0.5)
    time.sleep(1.0)
    session.detach()
    for k, st in stores.items():
        st["source"] = np.array([f"export:{name}", k])
        path = os.path.join(HERE, f"tiles_{k.replace('ZcTask', '')}_export{suffix}.npz")
        np.savez_compressed(path, **st)
        print("SAVED", path, sorted(x for x in st if x.endswith("_out")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
