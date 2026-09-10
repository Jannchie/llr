r"""导出时刻抓一块**全分辨率** RawNR tile 的入口/出口平面(attach,不 spawn)。

以前的 RawNR 捕获全是打开文件时的预览路径。导出会重跑整条管线(35 块 1024 tile),
attach 上去挂 `ZcTaskRawNRSIMD::exec`,拿第一块 tile 的 task 平面 0 进出两版,连同 tile
位置(`+0x48`)一起存下来;离线用**纯从 ARW 标签出发**的生产代码重算,逐位比。

    python export_rawnr_capture.py <文件名>   # 会触发一次 NR=自动 的 TIFF 导出
"""
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
EXEC_RVA = 0x39FAB0

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
let taken = 0;
function plane0(t) {
  const set = t.add(8).readPointer(); const p = set.add(8).readPointer();
  const w = p.add(8).readS32(), h = p.add(12).readS32(), stride = p.add(0x14).readS32(), data = p.add(0x20).readPointer();
  return {w: w, h: h, stride: stride, bytes: data.readByteArray(h * stride)};
}
Interceptor.attach(base.add(EXECJS), {
  onEnter(a) {
    if (taken >= NTAKE) return;
    this.take = true; this.task = a[1]; this.idx = taken++;
    const rect = [], pos = [];
    for (let o = 0x30; o < 0x40; o += 4) rect.push(a[1].add(o).readS32());
    for (let o = 0x48; o < 0x58; o += 4) pos.push(a[1].add(o).readS32());
    const p = plane0(a[1]);
    send({tag: 'in', idx: this.idx, w: p.w, h: p.h, stride: p.stride, rect: rect, pos: pos}, p.bytes);
  },
  onLeave() {
    if (!this.take) return;
    const p = plane0(this.task);
    send({tag: 'out', idx: this.idx, w: p.w, h: p.h, stride: p.stride}, p.bytes);
  }
});
send({info: 'armed'});
""".replace("EXECJS", hex(EXEC_RVA)).replace("NTAKE", "3")


def edit_pid():
    found = []

    def cb(h, _):
        if win32gui.GetWindowText(h) == "Edit" and win32gui.GetClassName(h).startswith("Afx"):
            found.append(win32process.GetWindowThreadProcessId(h)[1])
        return True
    win32gui.EnumWindows(cb, None)
    return found[0]


def main():
    name = sys.argv[1]
    store, meta = {}, {}

    def on_message(msg, data):
        if msg["type"] != "send":
            print("  !", msg, flush=True)
            return
        p = msg["payload"]
        if "info" in p:
            print("  ", p["info"], flush=True)
            return
        a = np.frombuffer(data, "<u2").reshape(p["h"], p["stride"] // 2)[:, :p["w"]].copy()
        store[f"{p['tag']}{p['idx']}"] = a
        if p["tag"] == "in":
            meta[p["idx"]] = {"rect": p["rect"], "pos": p["pos"]}
        print(f"   {p['tag']}{p['idx']} {p['w']}x{p['h']}", flush=True)

    session = frida.attach(edit_pid())
    script = session.create_script(JS)
    script.on("message", on_message)
    script.load()
    cmd = [sys.executable, os.path.join(HERE, "edit_export.py"), "auto", name]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-300:])
    time.sleep(1.0)
    session.detach()
    out = os.path.join(HERE, f"rawnr_export_{name}.npz")
    np.savez_compressed(out, **store, **{f"meta{i}_rect": np.array(m["rect"]) for i, m in meta.items()},
                        **{f"meta{i}_pos": np.array(m["pos"]) for i, m in meta.items()})
    print("写出", out, sorted(store))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
