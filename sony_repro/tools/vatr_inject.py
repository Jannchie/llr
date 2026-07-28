r"""往 ZcTaskVatr 的输入平面里灌受控图案,直接测出它的传递函数。

Vatr 的曲线是**渲染开始前**按整幅统计算好的(住在参数块 `P+0x7c8`,103 个 float),
所以在某一块 tile 的 onEnter 里改输入**不会改变曲线** —— 正好把「同一条曲线下,
输出如何依赖输入」单独隔离出来。

图案(三个平面写同一份,所以是中性灰,排除通道耦合):
  block   大块均匀色阶(块内远离边界处 邻域 == 自身)-> 纯逐像素曲线
  patch   背景 B 上摆一串小方块 V(V 逐块变化)     -> 增益对邻域的依赖
  bgscan  整幅背景 B,中间一个固定 V 的方块         -> 同 V 不同 B

用法(Windows 的 Python):
    python vatr_inject.py <ARW> --pattern block --tile 17 [--secs 60]
    python vatr_inject.py <ARW> --pattern patch --bg 2000 --psize 16
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

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
const TILE = TILEJS;
let seq = 0, sent = false;

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

function readAll(P) {
  const w = P[0].w, h = P[0].h;
  const buf = new ArrayBuffer(w * h * 3 * 2);
  const o = new Uint16Array(buf);
  for (let k = 0; k < 3; k++) {
    const p = P[k];
    const raw = new Uint16Array(p.data.readByteArray(p.h * p.stride));
    const per = p.stride >> 1;
    for (let y = 0; y < h; y++)
      for (let x = 0; x < w; x++) o[(y * w + x) * 3 + k] = raw[y * per + x];
  }
  return buf;
}

Interceptor.attach(base.add(RVAJS), {
  onEnter(a) {
    const idx = seq++;
    this.hit = (idx === TILE);
    if (!this.hit) return;
    this.task = a[1];
    const P = planes(a[1]);
    if (!P) { this.hit = false; return; }
    const w = P[0].w, h = P[0].h;
    const arr = new Uint16Array(w * h);
    PATTERNJS
    for (let k = 0; k < 3; k++) {
      const p = P[k], per = p.stride >> 1;
      const row = new Uint16Array(per);
      for (let y = 0; y < h; y++) {
        for (let x = 0; x < per; x++) row[x] = arr[y * w + Math.min(x, w - 1)];
        p.data.add(y * p.stride).writeByteArray(row.buffer);
      }
    }
    const ib = new ArrayBuffer(w * h * 2);
    new Uint16Array(ib).set(arr);
    this.inbuf = ib;
    this.geom = {w: w, h: h};
  },
  onLeave() {
    if (!this.hit || sent) return;
    sent = true;
    send({tag: 'in', geom: this.geom}, this.inbuf);
    send({tag: 'out', geom: this.geom}, readAll(planes(this.task)));
  }
});
send({info: 'armed'});
"""

# 色阶用等比 + 等差混合,低端也要有足够的点
LEVELS = [8, 16, 24, 32, 48, 64, 96, 128, 192, 256, 384, 512, 768, 1024, 1536,
          2048, 3072, 4096, 6144, 8192, 10240, 12288, 14336, 16000]

PATTERNS = {
    "block": r"""
    const LV = LVJS, BS = BSJS;
    let n = 0;
    for (let by = 0; by < h; by += BS)
      for (let bx = 0; bx < w; bx += BS) {
        const v = LV[n % LV.length]; n++;
        for (let y = by; y < Math.min(by + BS, h); y++)
          for (let x = bx; x < Math.min(bx + BS, w); x++) arr[y * w + x] = v;
      }
    """,
    "patch": r"""
    const LV = LVJS, BG = BGJS, PS = PSJS, SP = BSJS;
    for (let i = 0; i < arr.length; i++) arr[i] = BG;
    let n = 0;
    for (let by = SP; by + SP < h; by += SP)
      for (let bx = SP; bx + SP < w; bx += SP) {
        const v = LV[n % LV.length]; n++;
        for (let y = by; y < by + PS; y++)
          for (let x = bx; x < bx + PS; x++) arr[y * w + x] = v;
      }
    """,
}


def _opt(flag, default):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


def main():
    arw = sys.argv[1]
    pat = _opt("--pattern", "block")
    tile = int(_opt("--tile", "17"))
    bg = int(_opt("--bg", "2000"))
    bs = int(_opt("--bsize", "120"))
    ps = int(_opt("--psize", "16"))
    secs = float(_opt("--secs", "60"))
    name = _opt("--out", pat)
    lv = LEVELS
    if "--levels" in sys.argv:
        lv = [int(x) for x in _opt("--levels", "").split(",")]

    body = (PATTERNS[pat].replace("LVJS", json.dumps(lv)).replace("BGJS", str(bg))
            .replace("BSJS", str(bs)).replace("PSJS", str(ps)))
    js = (JS.replace("PATTERNJS", body).replace("RVAJS", str(RVA))
            .replace("TILEJS", str(tile)))

    store = {}

    def on_message(msg, data):
        if msg["type"] != "send":
            print("  !", msg, flush=True)
            return
        p = msg["payload"]
        if "info" in p:
            print("  ", p, flush=True)
            return
        g = p["geom"]
        if p["tag"] == "in":
            store["in"] = np.frombuffer(data, "<u2").reshape(g["h"], g["w"]).copy()
        else:
            store["out"] = np.frombuffer(data, "<u2").reshape(g["h"], g["w"], 3).copy()
        print(f"   {p['tag']} {g['w']}x{g['h']}", flush=True)

    subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
    time.sleep(1.0)
    pid = frida.spawn([EXE, arw])
    session = frida.attach(pid)
    script = session.create_script(js)
    script.on("message", on_message)
    script.load()
    frida.resume(pid)
    deadline = time.time() + secs
    while time.time() < deadline and "out" not in store:
        time.sleep(0.2)
    try:
        session.detach()
        frida.kill(pid)
    except Exception:
        pass
    if "out" not in store:
        raise SystemExit("没抓到")
    os.makedirs(os.path.join(SCR, "vatrprobe"), exist_ok=True)
    dest = os.path.join(SCR, "vatrprobe", f"inj_{name}.npz")
    np.savez_compressed(dest, inp=store["in"], out=store["out"],
                        levels=np.array(lv), meta=np.array([bg, bs, ps, tile]))
    print("SAVED", dest)


if __name__ == "__main__":
    main()
