r"""导出时刻逐步抓 ZcTaskSIMDMarble(RVA 0x389960)的内部状态:标定块、opts、处理器虚表、每个内部步骤前后的缓冲。

只抓第一次(idx 0)exec 调用那条线程上的东西。产物 tools/marble_export_<name>.npz:
  in_set0..2 / out_set0..2   tile 入口/出口三平面(uint16)
  calib                      calib+0x1000..+0x1200 原始字节;opts:opts+0x200..+0x300
  step<i>_<name>_pre/_post   每个 helper 调用前后:ctx 原始字节、ctx 引用的 p0/b1/b2 缓冲、task 的 set 三平面
  info                       json:各 tag 的指针/尺寸/参数/虚表
用法:python export_marble_capture.py <导出名> [--nr auto|off|manual] [--sliders 量,色彩,边缘]
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
const EXEC = 0x389960, AFTER_CALIB = 0x3899b2;
// [rva, name, 哪个参数是 ctx(-1 无), 是否 dump task 的 set]
const HELPERS = [
  [0x392cc0, 'gamut_fwd', -1, true],
  [0x38aa40, 'prep_aa40', -1, true],
  [0x395dd0, 'amount_5dd0', -1, false],
  [0x396290, 'setup_6290', -1, false],
  [0x38b760, 'cnr1_b760', 1, false],
  [0x38c070, 'cnr1_c070', 1, false],
  [0x390a70, 'scratch_a70', -1, false],
  [0x38c480, 'cnr2_c480', 1, false],
  [0x38db40, 'cnr3_db40', 1, false],
  [0x38ea10, 'cnr4_ea10', 1, false],
  [0x38fe70, 'cnr4_fe70', 1, false],
  [0x390e30, 'clr1_e30', 1, false],
  [0x391490, 'clr2_1490', 0, false],
  [0x391c90, 'clr3_1c90', 0, false],
  [0x3921f0, 'clr4_21f0', 0, false],
  [0x390480, 'clr5_0480', 0, false],
  [0x16b640, 'attach_b640', -1, true],
  [0x395f20, 'fin_5f20', -1, true],
  [0x38b010, 'blend_b010', -1, true],
  [0x392be0, 'gamut_inv', -1, true],
];
let seq = 0, tid = -1, task = null, step = 0;
function planeOf(p) {
  const w = p.add(8).readS32(), h = p.add(12).readS32(), stride = p.add(0x14).readS32();
  const data = p.add(0x20).readPointer(), nbytes = p.add(0x28).readS32();
  return {w: w, h: h, stride: stride, data: data, nbytes: nbytes};
}
function sendPlane(tag, p) {
  try {
    const d = planeOf(p);
    if (d.w <= 0 || d.h <= 0 || d.w > 8192 || d.h > 8192 || d.data.isNull()) { send({info: tag + ' bad plane ' + JSON.stringify([d.w, d.h, d.stride, d.nbytes])}); return; }
    send({tag: tag, w: d.w, h: d.h, stride: d.stride, nbytes: d.nbytes, ptr: d.data.toString(), obj: p.toString()}, d.data.readByteArray(d.h * d.stride));
  } catch (e) { send({info: tag + ' plane dump failed ' + e}); }
}
function sendSet(tag, t) {
  const set = t.add(8).readPointer(); if (set.isNull()) return;
  for (let k = 0; k < 3; k++) { const p = set.add(8 + k * 8).readPointer(); if (!p.isNull()) sendPlane(tag + '_set' + k, p); }
  const meta = []; for (let o = 0; o < 0x60; o += 4) meta.push(t.add(o).readS32());
  send({tag: tag + '_meta', meta: meta, set: set.toString()});
}
function sendCtx(tag, ctx) {
  try {
    send({tag: tag + '_ctx'}, ctx.readByteArray(0xa0));
    for (const [o, nm] of [[0x10, 'tmp'], [0x18, 'b2'], [0x20, 'b1']]) {
      const p = ctx.add(o).readPointer(); if (!p.isNull()) sendPlane(tag + '_' + nm, p);
    }
  } catch (e) { send({info: tag + ' ctx dump failed ' + e}); }
}
function sendClarityCtx(tag, ctx) {
  try {
    send({tag: tag + '_cctx'}, ctx.readByteArray(0x60));
    for (const o of [0, 8]) { const p = ctx.add(o).readPointer(); if (!p.isNull()) sendPlane(tag + '_c' + o, p); }
  } catch (e) { send({info: tag + ' cctx dump failed ' + e}); }
}
Interceptor.attach(base.add(EXEC), {
  onEnter(a) {
    this.idx = seq++;
    if (this.idx !== 0) return;
    tid = this.threadId; task = a[1]; this.hit = true;
    const r8 = a[2];
    const proc = r8.readPointer(), opts = r8.add(8).readPointer();
    send({tag: 'opts', ptr: opts.toString()}, opts.add(0x200).readByteArray(0x100));
    const vt = proc.readPointer(); const slots = [];
    for (let i = 0; i < 64; i++) slots.push(vt.add(i * 8).readPointer().sub(base).toString());
    send({tag: 'vt', slots: slots, proc: proc.toString()});
    for (const [rva, nm] of [[0x61bd60, 'lut_61bd60'], [0x63bd60, 'lut_63bd60'], [0x65bd60, 'lut_65bd60']]) send({tag: nm}, base.add(rva).readByteArray(0x20000));
    sendSet('in', task);
  },
  onLeave(r) { if (this.hit) { sendSet('out', task); send({tag: 'done', ret: r.toInt32()}); } }
});
Interceptor.attach(base.add(AFTER_CALIB), function (a) {
  if (this.threadId !== tid) return;
  const calib = this.context.rax;
  send({tag: 'calib', ptr: calib.toString()}, calib.add(0x1000).readByteArray(0x200));
});
for (const [rva, name, ctxArg, dumpSet] of HELPERS) {
  Interceptor.attach(base.add(rva), {
    onEnter(a) {
      if (this.threadId !== tid) { this.skip = true; return; }
      this.skip = false; this.step = step++; this.name = name;
      const tag = 'step' + this.step + '_' + name;
      this.tag = tag;
      this.args = [a[0], a[1], a[2], a[3]];
      send({tag: tag + '_args', args: this.args.map(x => x.toString())});
      if (ctxArg >= 0) { this.ctx = a[ctxArg]; if (name.startsWith('clr')) sendClarityCtx(tag + '_pre', this.ctx); else sendCtx(tag + '_pre', this.ctx); }
      if (name !== 'amount_5dd0') sendSet(tag + '_pre', task);
      if (name === 'blend_b010') { for (const k of [0, 1, 2, 3]) sendPlane(tag + '_pre_arg' + k, a[k]); }
    },
    onLeave(r) {
      if (this.skip) return;
      const tag = this.tag;
      if (this.ctx) { if (this.name.startsWith('clr')) sendClarityCtx(tag + '_post', this.ctx); else sendCtx(tag + '_post', this.ctx); }
      if (this.name !== 'amount_5dd0') sendSet(tag + '_post', task);
      if (this.name === 'blend_b010') { for (const k of [0, 1, 2, 3]) sendPlane(tag + '_post_arg' + k, this.args[k]); }
      send({tag: tag + '_ret', ret: r.toString()});
    }
  });
}
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
    nr = args[args.index("--nr") + 1] if "--nr" in args else "auto"
    extra = []
    if "--sliders" in args:
        extra = ["--sliders", args[args.index("--sliders") + 1]]
    st, info = {}, {}
    done = []

    def on_message(msg, data):
        if msg["type"] != "send":
            print("  !", msg, flush=True)
            return
        p = msg["payload"]
        if "info" in p:
            print("  ", p["info"], flush=True)
            return
        tag = p["tag"]
        if tag == "done":
            done.append(p["ret"])
            print("   exec returned", p["ret"], flush=True)
        elif data is not None and "w" in p:
            a = np.frombuffer(data, "<u2").reshape(p["h"], p["stride"] // 2)[:, : p["w"]].copy()
            st[tag] = a
            info[tag] = {k: v for k, v in p.items() if k != "tag"}
            print(f"   {tag} {p['w']}x{p['h']} stride {p['stride']}", flush=True)
        elif data is not None:
            st[tag] = np.frombuffer(data, np.uint8).copy()
            info[tag] = {k: v for k, v in p.items() if k != "tag"}
            print(f"   {tag} {len(data)} bytes", flush=True)
        else:
            info[tag] = {k: v for k, v in p.items() if k != "tag"}
            if tag.endswith("_args") or tag.endswith("_ret"):
                print(f"   {tag} {info[tag]}"[:200], flush=True)

    session = frida.attach(edit_pid())
    script = session.create_script(JS)
    script.on("message", on_message)
    script.load()
    r = subprocess.run([sys.executable, os.path.join(HERE, "edit_export.py"), nr, name] + extra,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-300:])
    deadline = time.time() + 180
    while time.time() < deadline and not done:
        time.sleep(0.5)
    time.sleep(1.0)
    session.detach()
    st["info"] = np.array(json.dumps(info))
    st["source"] = np.array([f"export:{name}", "ZcTaskSIMDMarble", f"nr={nr}"])
    path = os.path.join(HERE, f"marble_export_{name}.npz")
    np.savez_compressed(path, **st)
    print("SAVED", path, len(st), "arrays")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
