r"""导出时刻抓 ZcTaskChromaSuppres(RVA 0x36e920):tile 入/出 + 标定块 + 引擎实际查的四张 LUT。

attach 到已运行的 Edit,用 edit_export.py 触发一次导出。除了 tile_dump 同格式的
`t<i>_in / t<i>_out (h,w,3) uint16, t<i>_meta`,还存:

    params   json 串:lv[0xc](tag 0x74a5)、lv[0x2c](机型码)、lv[0xf00..0xf0e] 八个 short、
             lv[0x18ee](sc 定点),以及从 vt[0xd8] 那次调用的返回值(calib)读到的同一组,
             用来确认 calib 与 lv 是不是同一块内存
    lut_pre1 int16[0x4000]  lv+0x89962   (B → 第一级)
    lut_pre2 int16[0x8000]  lv+0x79962   (第二级,×sc/64)
    lut_tone int16[0x8000]  lv+0x118f6   (tone LUT)
    lut_ygam int16[0x8000]  lv+0x318fc   (YGamma LUT)

    python export_cs_capture.py <导出文件名> [--nr auto|off] [--tiles 0,1,2]
产物 tools/cs_export_<导出文件名>.npz
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
RVA = 0x36E920

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
const WANT = WANTJS;
const NP = 3;
let seq = 0, dumped = false;

function grab(task, tag, idx) {
  const set = task.add(8).readPointer();
  if (set.isNull()) return;
  const meta = [];
  for (let o = 0; o < 0x60; o += 4) meta.push(task.add(o).readS32());
  const descs = [];
  for (let k = 0; k < NP; k++) {
    const p = set.add(8 + k * 8).readPointer();
    if (p.isNull()) return;
    descs.push({w: p.add(8).readS32(), h: p.add(12).readS32(), stride: p.add(0x14).readS32(), data: p.add(0x20)});
  }
  const w = descs[0].w, h = descs[0].h;
  if (w <= 0 || h <= 0 || w > 4096 || h > 4096) return;
  const buf = new ArrayBuffer(w * h * NP * 2);
  const o = new Uint16Array(buf);
  for (let k = 0; k < NP; k++) {
    const d = descs[k];
    const raw = new Uint16Array(d.data.readPointer().readByteArray(d.h * d.stride));
    const per = d.stride >> 1;
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) o[(y * w + x) * NP + k] = raw[y * per + x];
  }
  send({tag: tag, idx: idx, w: w, h: h, np: NP, meta: meta}, buf);
}

function fields(p) {
  const f = [];
  for (let o = 0xf00; o <= 0xf0e; o += 2) f.push(p.add(o).readS16());
  return {x_raw: p.add(0xc).readS32(), model: p.add(0x2c).readS32(), f: f, sc_raw: p.add(0x18ee).readS16(),
          addr: p.toString()};
}

function lut(lv, name, off, n) {
  try { send({tag: 'lut', name: name}, lv.add(off).readByteArray(n * 2)); }
  catch (e) { send({info: 'lut ' + name + ' failed: ' + e}); }
}

// 找到 exec 里那次 call qword ptr [rax+0xd8](取 calib),挂在它的下一条指令上读 rax。
const fn = base.add(RVAJS);
let cur = fn, after = null;
// capstone 碰到解不开的字节会抛错:前进一字节重扫(scan_disp.sweep 的老规矩)。
for (let i = 0; i < 600 && after === null; i++) {
  let ins;
  try { ins = Instruction.parse(cur); } catch (e) { cur = cur.add(1); continue; }
  if (ins.mnemonic === 'call' && ins.opStr.indexOf('0xd8') >= 0) after = ins.next;
  cur = ins.next;
}
if (after === null) {
  // 反汇编(disasm_fn.py 0x14036e920)里那次虚调用是 exec+0xb7 的 `call r8`(41 ff d0),
  // 编译器先把 [rax+0xd8] 装进 r8 再 call。核对一下再挂它的下一条。
  try {
    const ins = Instruction.parse(fn.add(0xb7));
    if (ins.mnemonic === 'call' && ins.opStr === 'r8') after = ins.next;
  } catch (e) {}
}
if (after === null) send({info: 'calib call not found'});
else {
  send({info: 'calib hook at +' + after.sub(fn).toString()});
  Interceptor.attach(after, {
    onEnter() {
      if (this.context._calibSeen) return;
      const calib = this.context.rax;
      send({tag: 'calib', p: fields(calib)});
    }
  });
}

Interceptor.attach(fn, {
  onEnter(a) {
    this.idx = seq++;
    this.hit = WANT.indexOf(this.idx) >= 0;
    this.task = a[1];
    if (!dumped) {
      dumped = true;
      const lv = a[1].add(0x68).readPointer().add(0xc8).readPointer();
      send({tag: 'lv', p: fields(lv)});
      lut(lv, 'pre1', 0x89962, 0x4000);
      lut(lv, 'pre2', 0x79962, 0x8000);
      lut(lv, 'tone', 0x118f6, 0x8000);
      lut(lv, 'ygam', 0x318fc, 0x8000);
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
    tiles = [0, 1, 2]
    nr = "auto"
    i = 0
    while i < len(args):
        if args[i] == "--tiles":
            tiles = [int(x) for x in args[i + 1].split(",")]
            i += 2
        elif args[i] == "--nr":
            nr = args[i + 1]
            i += 2
        else:
            raise SystemExit(f"unknown arg {args[i]}")
    st = {}
    params = {"calib": [], "lv": None}

    def on_message(msg, data):
        if msg["type"] != "send":
            print("  !", msg, flush=True)
            return
        p = msg["payload"]
        if "info" in p:
            print("  ", p["info"], flush=True)
            return
        tag = p["tag"]
        if tag == "lut":
            st[f"lut_{p['name']}"] = np.frombuffer(data, "<i2").copy()
            print(f"   lut {p['name']} {st[f'lut_{p['name']}'].size}", flush=True)
        elif tag == "calib":
            if not params["calib"] or params["calib"][-1] != p["p"]:
                params["calib"].append(p["p"])
                print("   calib", p["p"], flush=True)
        elif tag == "lv":
            params["lv"] = p["p"]
            print("   lv", p["p"], flush=True)
        else:
            a = np.frombuffer(data, "<u2").reshape(p["h"], p["w"], p["np"]).copy()
            st[f"t{p['idx']}_{tag}"] = a
            st[f"t{p['idx']}_meta"] = np.array(p["meta"], np.int32)
            print(f"   {tag} tile{p['idx']} {p['w']}x{p['h']}x{p['np']}", flush=True)

    session = frida.attach(edit_pid())
    script = session.create_script(JS.replace("WANTJS", json.dumps(tiles)).replace("RVAJS", str(RVA)))
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
    st["params"] = np.array(json.dumps(params))
    st["source"] = np.array([f"export:{name}"])
    path = os.path.join(HERE, f"cs_export_{name}.npz")
    np.savez_compressed(path, **st)
    print("SAVED", path, sorted(x for x in st if x.endswith("_out")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
