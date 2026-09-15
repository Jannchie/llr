r"""手动档「量」≥ 50 时引擎到底改了什么:attach 到 Edit,一次导出里同时抓
  (1) 全部 ZcTask exec 的执行普查(按 **vtable** 归类,不按 RVA —— RawNRSIMD 的 exec 与
      SIMDSpica 的 vtable 槽共用 0x39fab0,按 RVA 数会混);
  (2) RawNRSIMD 第一次进入时的参数块:`task->[0x68]` 的 +0xc0000..+0xc0140 标量,以及
      +0xd0 + k*0x20000 的六张 32768 项表;
  (3) RawNRSIMD 前 K 块 tile 的入口/出口平面(1 平面)+ meta(export_tile_dump 同格式)。

    python highiso_amount_probe.py <导出文件名> <off|auto|manual> [--sliders 量,色彩,边缘] [--tiles 0,1,2] [--suffix xxx] [--dro off]
                                   [ZcTaskSIMDITP:1:3 ZcTaskSIMDMarble:3 ...]   # 顺带抓这些 task 前 2 块 tile 的入口/出口

产物:tmp/highiso/probes/highiso_probe_<suffix>.npz(大文件,不进仓库)(t<i>_in/out/meta、params、tbl0..5、census json)。
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
RAWNR = "ZcTaskRawNRSIMD"

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
const EXECS = EXECSJS;          // name -> {rva, vt}
const EXTRA = EXTRAJS;          // cls -> {np, np_out, n}
const WANT = WANTJS;
const vtName = {};
for (const n in EXECS) vtName[base.add(EXECS[n].vt).toString()] = n;
const counts = {}, sizes = {};
const seen = new Set();
let rawSeq = 0, gotParams = false;

function planes(task, nplane) {
  const set = task.add(8).readPointer();
  if (set.isNull()) return null;
  const meta = [];
  for (let o = 0; o < 0x60; o += 4) meta.push(task.add(o).readS32());
  const descs = [];
  for (let k = 0; k < nplane; k++) {
    const p = set.add(8 + k * 8).readPointer();
    if (p.isNull()) return null;
    descs.push({w: p.add(8).readS32(), h: p.add(12).readS32(), stride: p.add(0x14).readS32(), data: p.add(0x20)});
  }
  const w = descs[0].w, h = descs[0].h;
  if (w <= 0 || h <= 0 || w > 4096 || h > 4096) return null;
  const buf = new ArrayBuffer(w * h * nplane * 2);
  const o = new Uint16Array(buf);
  for (let k = 0; k < nplane; k++) {
    const d = descs[k];
    const raw = new Uint16Array(d.data.readPointer().readByteArray(d.h * d.stride));
    const per = d.stride >> 1;
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) o[(y * w + x) * nplane + k] = raw[y * per + x];
  }
  return {buf: buf, w: w, h: h, meta: meta, descs: descs.map(d => [d.w, d.h, d.stride])};
}

for (const name in EXECS) {
  const rva = EXECS[name].rva;
  if (seen.has(rva)) continue;
  seen.add(rva);
  try {
    Interceptor.attach(base.add(rva), {
      onEnter(a) {
        let cls = name;
        try { cls = vtName[a[0].readPointer().toString()] || (name + '?'); } catch (e) {}
        counts[cls] = (counts[cls] || 0) + 1;
        if (!sizes[cls]) {
          try {
            const set = a[1].add(8).readPointer();
            const p = set.add(8).readPointer();
            sizes[cls] = [p.add(8).readS32(), p.add(12).readS32()];
            const rect = []; for (let o = 0x30; o < 0x40; o += 4) rect.push(a[1].add(o).readS32());
            const pos = []; for (let o = 0x48; o < 0x58; o += 4) pos.push(a[1].add(o).readS32());
            sizes[cls].push(rect, pos);
          } catch (e) { sizes[cls] = ['?']; }
        }
        if (cls in EXTRA) {
          const ex = EXTRA[cls];
          ex.seq = (ex.seq || 0);
          this.eidx = ex.seq++;
          if (this.eidx < ex.n) {
            this.etask = a[1]; this.ecls = cls;
            const p = planes(a[1], ex.np);
            if (p) send({tag: 'in', cls: cls, idx: this.eidx, w: p.w, h: p.h, np: ex.np, meta: p.meta, descs: p.descs}, p.buf); else this.ecls = null;
          }
          return;
        }
        if (cls !== 'RAWNRJS') return;
        this.idx = rawSeq++;
        if (!gotParams) {
          try {
            const blk = a[1].add(0x68).readPointer();
            if (!blk.isNull()) {
              gotParams = true;
              send({tag: 'params'}, blk.add(0xc0000).readByteArray(0x140));
              for (let k = 0; k < 6; k++) send({tag: 'tbl' + k}, blk.add(0xd0 + k * 0x20000).readByteArray(32768 * 4));
            }
          } catch (e) { send({info: 'params: ' + e}); }
        }
        this.hit = WANT.indexOf(this.idx) >= 0;
        if (this.hit) {
          this.task = a[1];
          const p = planes(a[1], 1);
          if (p) send({tag: 'in', idx: this.idx, w: p.w, h: p.h, np: 1, meta: p.meta, descs: p.descs}, p.buf); else this.hit = false;
        }
      },
      onLeave() {
        if (this.ecls) {
          const ex = EXTRA[this.ecls];
          const p = planes(this.etask, ex.np_out);
          if (p) send({tag: 'out', cls: this.ecls, idx: this.eidx, w: p.w, h: p.h, np: ex.np_out, meta: p.meta, descs: p.descs}, p.buf);
          return;
        }
        if (!this.hit) return;
        const p = planes(this.task, 1);
        if (p) send({tag: 'out', idx: this.idx, w: p.w, h: p.h, np: 1, meta: p.meta, descs: p.descs}, p.buf);
      }
    });
  } catch (e) { send({info: name + ' 挂不上 ' + e}); }
}
rpc.exports = { report() { return {counts: counts, sizes: sizes}; } };
send({info: 'armed ' + seen.size});
"""


def edit_pid():
    found = []

    def cb(h, _):
        if win32gui.GetWindowText(h) == "Edit" and win32gui.GetClassName(h).startswith("Afx"):
            found.append(win32process.GetWindowThreadProcessId(h)[1])
        return True
    win32gui.EnumWindows(cb, None)
    if not found:
        raise SystemExit("Edit 主窗口没找到")
    return found[0]


def main():
    args = list(sys.argv[1:])
    name, mode = args[0], args[1]
    tiles = [0, 1, 2]
    sliders = None
    suffix = name
    if "--tiles" in args:
        tiles = [int(x) for x in args[args.index("--tiles") + 1].split(",")]
    if "--sliders" in args:
        sliders = args[args.index("--sliders") + 1]
    if "--suffix" in args:
        suffix = args[args.index("--suffix") + 1]
    dro = args[args.index("--dro") + 1] if "--dro" in args else None
    n_extra = int(args[args.index("--n") + 1]) if "--n" in args else 2
    extra = {}
    for a in args:
        if a.startswith("ZcTask"):
            parts = a.split(":")
            np_in = int(parts[1]) if len(parts) > 1 else 3
            extra[parts[0]] = {"np": np_in, "np_out": int(parts[2]) if len(parts) > 2 else np_in, "n": n_extra}
    with open(os.path.join(HERE, "task_execs.json"), encoding="utf-8") as f:
        execs = {k: {"rva": v["rva"], "vt": v["vftable_rva"]} for k, v in json.load(f).items()}
    store = {}

    def on_message(msg, data):
        if msg["type"] != "send":
            print("  !", msg, flush=True)
            return
        p = msg["payload"]
        if "info" in p:
            print("  ", p["info"], flush=True)
            return
        if p["tag"] == "params":
            store["params"] = np.frombuffer(data, "<i4").copy()
            nz = [(hex(0xC0000 + 4 * i), int(v)) for i, v in enumerate(store["params"]) if v]
            print("   参数块非零项:", nz, flush=True)
        elif p["tag"].startswith("tbl"):
            t = np.frombuffer(data, "<i4").copy()
            store[p["tag"]] = t
            print(f"   {p['tag']} [0]={t[0]} [2048]={t[2048]} [4096]={t[4096]} max={t.max()}", flush=True)
        else:
            a = np.frombuffer(data, "<u2").reshape(p["h"], p["w"], p["np"]).copy()
            pre = "" if "cls" not in p else p["cls"].replace("ZcTask", "") + "_"
            store[f"{pre}t{p['idx']}_{p['tag']}"] = a
            store[f"{pre}t{p['idx']}_meta"] = np.array(p["meta"], np.int32)
            store[f"{pre}t{p['idx']}_descs"] = np.array(p["descs"], np.int32)
            print(f"   {pre or 'RawNR '}{p['tag']} tile{p['idx']} {p['w']}x{p['h']}x{p['np']}", flush=True)

    session = frida.attach(edit_pid())
    script = session.create_script(JS.replace("EXECSJS", json.dumps(execs)).replace("WANTJS", json.dumps(tiles)).replace("RAWNRJS", RAWNR).replace("EXTRAJS", json.dumps(extra)))
    script.on("message", on_message)
    script.load()
    time.sleep(0.5)
    cmd = [sys.executable, os.path.join(HERE, "edit_export.py"), mode, name]
    if sliders:
        cmd += ["--sliders", sliders]
    if dro:
        cmd += ["--dro", dro]
    print("触发导出:", " ".join(cmd[2:]), flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-400:], flush=True)
    deadline = time.time() + 240
    want = [f"t{i}_out" for i in tiles] + [f"{k.replace('ZcTask', '')}_t{i}_out" for k, v in extra.items() for i in range(v["n"])]
    while time.time() < deadline and not all(w in store for w in want):
        time.sleep(0.5)
    time.sleep(2.0)
    rep = script.exports_sync.report()
    session.detach()
    rows = sorted(rep["counts"].items(), key=lambda kv: -kv[1])
    print("\n导出期间跑过的 task(按 vtable 归类;次数 | 第一次的平面0 宽x高 | rect | pos):")
    for k, n in rows:
        print(f"  {k:<32} x{n:<5} {rep['sizes'].get(k, [])}")
    nr_like = [k for k in execs if k not in rep["counts"] and any(t in k for t in ("NR", "Nr", "Marble", "Spica", "Sharp", "Vatr"))]
    print("没跑的(降噪/锐化相关):", nr_like)
    store["census"] = np.array([json.dumps(rep)])
    store["source"] = np.array([f"export:{name}", mode, sliders or ""])
    out_dir = os.path.join(HERE, "..", "..", "tmp", "highiso", "probes")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"highiso_probe_{suffix}.npz")
    np.savez_compressed(path, **store)
    print("SAVED", path, sorted(k for k in store if k.endswith("_out")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
