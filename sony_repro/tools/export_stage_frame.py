r"""导出时刻的 stage_frame:attach,挂若干 <Task>:<in|out>,触发导出,把 35 块 tile 按
task 的 +0x48 位置拼成整幅(stage_frame.py 同格式:键 `<Task>_<in|out>` (H,W,3) uint16,`step`)。

    python export_stage_frame.py <导出文件名> <Task:in|out> ... [--step 1] [--out 名字] [--nr auto|off]
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
W, H = 7008, 4672

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
const SPECS = SPECSJSON, STEP = STEPV;
function emit(task, tag) {
  const set = task.add(8).readPointer();
  if (set.isNull()) return;
  const descs = [];
  for (let k = 0; k < 3; k++) {
    const p = set.add(8 + k * 8).readPointer();
    if (p.isNull()) return;
    descs.push({w: p.add(8).readS32(), h: p.add(12).readS32(), stride: p.add(0x14).readS32(), data: p.add(0x20).readPointer()});
  }
  const ext = [], dst = [], rect = [];
  for (let o = 0x18; o < 0x28; o += 4) ext.push(task.add(o).readS32());
  for (let o = 0x30; o < 0x40; o += 4) rect.push(task.add(o).readS32());
  for (let o = 0x48; o < 0x58; o += 4) dst.push(task.add(o).readS32());
  const ox = dst[0] - ext[0], oy = dst[1] - ext[1];
  const w = dst[2] - dst[0], h = dst[3] - dst[1];
  if (w <= 0 || h <= 0) return;
  const nw = Math.ceil(w / STEP), nh = Math.ceil(h / STEP);
  const o = new Uint16Array(nw * nh * 3);
  for (let k = 0; k < 3; k++) {
    const d = descs[k];
    const ph = d.h, stride = d.stride;
    for (let j = 0; j < nh; j++) {
      if (oy + j * STEP >= ph) break;
      const row = new Uint16Array(d.data.add((oy + j * STEP) * stride).readByteArray(stride));
      for (let i = 0; i < nw; i++) o[(j * nw + i) * 3 + k] = row[ox + i * STEP];
    }
  }
  send({tag: tag, dst: dst, nw: nw, nh: nh}, o.buffer);
}
for (const spec in SPECS) {
  const [name, when] = spec.split(':');
  Interceptor.attach(base.add(SPECS[spec]), {
    onEnter(a) { this.task = a[1]; if (when === 'in') emit(a[1], spec); },
    onLeave() { if (when === 'out') emit(this.task, spec); }
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

    def take(flag, default):
        if flag in args:
            i = args.index(flag)
            v = args[i + 1]
            del args[i:i + 2]
            return v
        return default
    step = int(take("--step", "1"))
    out_name = take("--out", "stage_frames_export")
    nr = take("--nr", "auto")
    with open(os.path.join(HERE, "task_execs.json"), encoding="utf-8") as f:
        execs = json.load(f)
    specs = {a: execs[a.split(":")[0]]["rva"] for a in args}
    frames = {k: np.zeros((H // step + 2, W // step + 2, 3), np.uint16) for k in specs}
    hits = dict.fromkeys(specs, 0)

    def on_message(msg, data):
        if msg["type"] != "send":
            print("  !", msg, flush=True)
            return
        p = msg["payload"]
        if "info" in p:
            print("  ", p, flush=True)
            return
        a = np.frombuffer(data, dtype="<u2").reshape(p["nh"], p["nw"], 3)
        x, y = p["dst"][0] // step, p["dst"][1] // step
        fr = frames[p["tag"]]
        hh = min(p["nh"], fr.shape[0] - y)
        ww = min(p["nw"], fr.shape[1] - x)
        fr[y:y + hh, x:x + ww] = a[:hh, :ww]
        hits[p["tag"]] += 1

    session = frida.attach(edit_pid())
    script = session.create_script(JS.replace("SPECSJSON", json.dumps(specs)).replace("STEPV", str(step)))
    script.on("message", on_message)
    script.load()
    r = subprocess.run([sys.executable, os.path.join(HERE, "edit_export.py"), nr, name],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-300:])
    # 等每个阶段都拼满 35 块(或 3 分钟超时)再 detach —— 文件先落地,管线还在跑。
    deadline = time.time() + 180
    while time.time() < deadline and not all(v >= 35 for v in hits.values()):
        time.sleep(0.5)
    time.sleep(1.5)
    session.detach()
    print("拼上的块数:", hits)
    out = {k.replace(":", "_"): v for k, v in frames.items()}
    path = os.path.join(HERE, f"{out_name}.npz")
    np.savez_compressed(path, step=np.array([step, W, H]), **out)
    print("SAVED", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
