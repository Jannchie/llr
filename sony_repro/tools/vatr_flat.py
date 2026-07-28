r"""把**每一块 tile 整块**灌成一个均匀值,一次运行拿到整条平场传递曲线。

`vatr_inject.py --pattern block` 用 120px 的色块量出来的曲线是**错的**:
PIPELINE §7.10 实测 Vatr 的空间核到 2052px 仍在起作用,120px 的块里
「邻域均值」根本不是块自己的值,而是整块 tile 的混合。

Vatr 是逐 tile 调的,35 块 tile 互不相干 —— 那就一块 tile 灌一个值,
一次渲染量 35 个点,而且每一点的邻域都等于它自己。

用法(Windows 的 Python):
    python vatr_flat.py <ARW> [--secs 60] [--out 名字]
"""
import json
import os
import subprocess
import sys
import time

import frida
import numpy as np

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
SCR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(SCR, "task_execs.json"), encoding="utf-8") as f:
    RVA = json.load(f)["ZcTaskVatr"]["rva"]

LEVELS = [4, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256, 384, 512, 768, 1024,
          1280, 1536, 2048, 2560, 3072, 4096, 5120, 6144, 7168, 8192, 9216,
          10240, 11264, 12288, 13312, 14336, 15360, 16000, 16383]

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
const LV = LVJS;
let seq = 0;

function planes(task) {
  const set = task.add(8).readPointer();
  if (set.isNull()) return null;
  const out = [];
  for (let k = 0; k < 3; k++) {
    const p = set.add(8 + k * 8).readPointer();
    if (p.isNull()) return null;
    out.push({w: p.add(8).readS32(), h: p.add(12).readS32(),
              stride: p.add(0x14).readS32(), data: p.add(0x20).readPointer()});
  }
  return out;
}

Interceptor.attach(base.add(RVAJS), {
  onEnter(a) {
    this.idx = seq++;
    if (this.idx >= LV.length) return;
    const P = planes(a[1]);
    if (!P) return;
    this.task = a[1];
    this.v = LV[this.idx];
    for (const p of P) {
      const per = p.stride >> 1;
      const row = new Uint16Array(per).fill(this.v);
      for (let y = 0; y < p.h; y++) p.data.add(y * p.stride).writeByteArray(row.buffer);
    }
  },
  onLeave() {
    if (this.v === undefined) return;
    const P = planes(this.task);
    if (!P) return;
    // 中心区域取样,避开边界
    const out = [];
    for (const p of P) {
      const y = p.h >> 1, per = p.stride >> 1;
      const raw = new Uint16Array(p.data.add(y * p.stride).readByteArray(p.stride));
      let s = 0, n = 0;
      for (let x = p.w >> 2; x < (p.w * 3) >> 2; x++) { s += raw[x]; n++; }
      out.push(s / n);
    }
    // 顺带看一行里有没有横向变化(有 -> 边界效应没躲干净)
    const p = P[1], y = p.h >> 1, per = p.stride >> 1;
    const raw = new Uint16Array(p.data.add(y * p.stride).readByteArray(p.stride));
    let lo = 1e9, hi = -1e9;
    for (let x = p.w >> 2; x < (p.w * 3) >> 2; x++) { if (raw[x] < lo) lo = raw[x]; if (raw[x] > hi) hi = raw[x]; }
    send({idx: this.idx, v: this.v, w: p.w, h: p.h, out: out, lo: lo, hi: hi});
  }
});
send({info: 'armed'});
""".replace("RVAJS", str(RVA)).replace("LVJS", json.dumps(LEVELS))


def _opt(flag, default):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


def main():
    arw = sys.argv[1]
    secs = float(_opt("--secs", "60"))
    name = _opt("--out", os.path.splitext(os.path.basename(arw))[0])
    # --const V:所有 tile 灌同一个值。一次运行拿到「同一输入在 35 个不同空间位置
    # 上的增益」,与 --levels 那种「每块 tile 一个值」互补 —— 后者把输入值和
    # tile 的空间上下文混在了一起,单独用会得出错误的曲线。
    if "--const" in sys.argv:
        v = int(_opt("--const", "1024"))
        globals()["LEVELS"] = [v] * 40
        global JS
        JS = JS.replace(json.dumps([4, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256,
                                    384, 512, 768, 1024, 1280, 1536, 2048, 2560, 3072,
                                    4096, 5120, 6144, 7168, 8192, 9216, 10240, 11264,
                                    12288, 13312, 14336, 15360, 16000, 16383]),
                        json.dumps([v] * 40))
    rows = []

    def on_message(msg, data):
        if msg["type"] != "send":
            return
        p = msg["payload"]
        if "info" in p:
            print("  ", p, flush=True)
            return
        rows.append(p)

    subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
    time.sleep(1.0)
    pid = frida.spawn([EXE, arw])
    session = frida.attach(pid)
    script = session.create_script(JS)
    script.on("message", on_message)
    script.load()
    frida.resume(pid)
    deadline = time.time() + secs
    while time.time() < deadline and len(rows) < len(LEVELS):
        time.sleep(0.3)
    try:
        session.detach()
        frida.kill(pid)
    except Exception:
        pass

    rows.sort(key=lambda r: r["v"])
    print(f"\n{'输入':>7} {'tile':>10} {'out G':>10} {'行内 min..max':>16} "
          f"{'增益':>8} {'log2 增量':>10}")
    for r in rows:
        g = r["out"][1] / r["v"] if r["v"] else float("nan")
        print(f"{r['v']:>7} {r['w']}x{r['h']:<6} {r['out'][1]:>10.2f} "
              f"{r['lo']:>7}..{r['hi']:<7} {g:>8.4f} "
              f"{np.log2(g) if g > 0 else 0:>10.4f}")
    os.makedirs(os.path.join(SCR, "vatrprobe"), exist_ok=True)
    with open(os.path.join(SCR, "vatrprobe", f"flat_{name}.json"), "w") as f:
        json.dump(rows, f)


if __name__ == "__main__":
    main()
