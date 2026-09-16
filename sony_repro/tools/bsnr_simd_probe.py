r"""抓 `ZcTaskSIMDBSNR_Y` 核心(0x14039b3e0)第一次进入时的实参:两张 0x8000 项 int32 表
(阈值表 r8 = calib+0x200d0、权重表 [rsp+0x20] = calib+0x800d0)、gain / limit / strength,
以及它处理的源平面(入口 + 出口,原地写回)。M/S 尺寸(YCbCr)帧上 Edit 的亮度降噪就是这一级,
见 notes/ycc-frame-luma-nr.md。

    python bsnr_simd_probe.py <导出文件名> <off|auto|manual> [--sliders 量,色彩,边缘] [--suffix xxx] [--n 3]

前提:Edit 已经打开了目标 ARW(keep_edit.py)。产物:tmp/highiso/probes/bsnr_simd_<suffix>.npz。
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
CORE_RVA = 0x39b3e0

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
const N = NJS;
let seq = 0;
function plane(desc) {
  const w = desc.add(8).readS32(), h = desc.add(12).readS32(), stride = desc.add(0x14).readS32();
  const data = desc.add(0x20).readPointer();
  return {w: w, h: h, stride: stride, buf: data.readByteArray(h * stride)};
}
Interceptor.attach(base.add(CORE_RVA), {
  onEnter(a) {
    this.idx = seq++;
    if (this.idx >= N) return;
    const sp = this.context.rsp;
    // x64 调用约定:rcx=平面, rdx=偏置, r8=阈值表, r9=项数; 栈上 [rsp+0x28]=权重表,
    // [rsp+0x30]=项数, [rsp+0x38]=gain, [rsp+0x40]=limit, [rsp+0x48]=strength(f32), [rsp+0x50]=rect
    if (this.idx === 0) {
      const thr = a[2].readByteArray(0x8000 * 4);
      const wt = sp.add(0x28).readPointer().readByteArray(0x8000 * 4);
      send({tag: 'tbl', name: 'thr'}, thr);
      send({tag: 'tbl', name: 'weight'}, wt);
      send({tag: 'scalars', bias: a[1].toInt32(), n: a[3].toInt32(), gain: sp.add(0x38).readS32(),
            limit: sp.add(0x40).readS32(), strength: sp.add(0x48).readFloat()});
    }
    const rect = sp.add(0x50).readPointer();
    const r = [];
    for (let o = 0; o < 0x20; o += 4) r.push(rect.add(o).readS32());
    this.desc = a[0];
    const p = plane(a[0]);
    send({tag: 'in', idx: this.idx, w: p.w, h: p.h, stride: p.stride, rect: r}, p.buf);
  },
  onLeave() {
    if (this.idx >= N) return;
    const p = plane(this.desc);
    send({tag: 'out', idx: this.idx, w: p.w, h: p.h, stride: p.stride}, p.buf);
  }
});
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
    sliders = args[args.index("--sliders") + 1] if "--sliders" in args else None
    suffix = args[args.index("--suffix") + 1] if "--suffix" in args else name
    n = int(args[args.index("--n") + 1]) if "--n" in args else 3
    store = {}

    def on_message(msg, data):
        if msg["type"] != "send":
            print("  !", msg, flush=True)
            return
        p = msg["payload"]
        if p["tag"] == "tbl":
            t = np.frombuffer(data, "<i4").copy()
            store[p["name"]] = t
            print(f"   {p['name']}: [0]={t[0]} [1000]={t[1000]} [4096]={t[4096]} [8192]={t[8192]} max={t.max()}", flush=True)
        elif p["tag"] == "scalars":
            for k in ("bias", "n", "gain", "limit", "strength"):
                store[k] = np.array([p[k]])
            print("   scalars:", {k: p[k] for k in ("bias", "n", "gain", "limit", "strength")}, flush=True)
        else:
            a = np.frombuffer(data, "<u2").reshape(p["h"], p["stride"] // 2)[:, :p["w"]].copy()
            store[f"t{p['idx']}_{p['tag']}"] = a
            if "rect" in p:
                store[f"t{p['idx']}_rect"] = np.array(p["rect"], np.int32)
                print(f"   tile{p['idx']} in {p['w']}x{p['h']} rect {p['rect'][:8]}", flush=True)

    session = frida.attach(edit_pid())
    script = session.create_script(JS.replace("NJS", str(n)).replace("CORE_RVA", hex(CORE_RVA)))
    script.on("message", on_message)
    script.load()
    time.sleep(0.5)
    cmd = [sys.executable, os.path.join(HERE, "edit_export.py"), mode, name]
    if sliders:
        cmd += ["--sliders", sliders]
    print("触发导出:", " ".join(cmd[2:]), flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-400:], flush=True)
    deadline = time.time() + 120
    while time.time() < deadline and not all(f"t{i}_out" in store for i in range(n)):
        time.sleep(0.5)
    time.sleep(1.0)
    session.detach()
    out_dir = os.path.join(HERE, "..", "..", "tmp", "highiso", "probes")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"bsnr_simd_{suffix}.npz")
    np.savez_compressed(path, **store)
    print("SAVED", path, sorted(store))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
