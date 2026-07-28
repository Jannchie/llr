r"""往 SIMDSharpness 的**输入缓冲区里写测试图**,直接测出它的冲激响应与传递曲线。

拿真实画面做最小二乘拟合永远说不清「核是这样」还是「刚好拟合出来是这样」——
既然任务是原地改 `*(task+8)` 的平面,那就在 onEnter 里把平面整块换成自造的图案,
onLeave 再读回来。**输入完全受控,输出就是算子本身**,没有任何统计推断。

图案:
  impulse  平坦底色 + 网格状孤立冲激(振幅逐格变化)-> 冲激响应 + 传递曲线
  step     竖直阶跃,对比度逐条变化                -> 边缘过冲形状
  flat     纯平场(可加常数)                       -> 确认零输入零输出

用法(Windows 的 Python):
    python sharp_inject.py <ARW> [--pattern impulse] [--tile 17] [--bg 4000]
                           [--patch 0x208=6,0x2c4=50] [--out 名字]
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
    RVA = json.load(f)["ZcTaskSIMDSharpness"]["rva"]

# 网格间距要大于核半径的两倍,否则相邻冲激的响应会叠在一起
SPACING = 48
AMPS = [16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256,
        320, 384, 448, 512, 640, 768, 1024, 1536, 2048, 3072, 4096,
        -16, -24, -32, -40, -48, -56, -64, -80, -96, -112, -128, -160, -192,
        -224, -256, -320, -384, -448, -512, -640, -768, -1024, -1536, -2048,
        -3072, -4096]

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
const TILE = TILEJS, PATCH = PATCHJS;
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

function readPlane(p) {
  const buf = new ArrayBuffer(p.w * p.h * 2);
  const o = new Uint16Array(buf);
  const raw = new Uint16Array(p.data.readByteArray(p.h * p.stride));
  const per = p.stride >> 1;
  for (let y = 0; y < p.h; y++)
    for (let x = 0; x < p.w; x++) o[y * p.w + x] = raw[y * per + x];
  return buf;
}

function writePlane(p, arr) {
  const per = p.stride >> 1;
  const row = new Uint16Array(per);
  for (let y = 0; y < p.h; y++) {
    for (let x = 0; x < per; x++) row[x] = x < p.w ? arr[y * p.w + x] : arr[y * p.w + p.w - 1];
    p.data.add(y * p.stride).writeByteArray(row.buffer);
  }
}

Interceptor.attach(base.add(RVAJS), {
  onEnter(a) {
    const idx = seq++;
    this.hit = (idx === TILE);
    if (!this.hit) return;
    this.task = a[1];
    const opts = a[2].add(8).readPointer();
    for (const k in PATCH) opts.add(parseInt(k)).writeS32(PATCH[k]);
    const pr = {};
    for (const o of [0x1e0, 0x208, 0x20c, 0x210, 0x214, 0x2c4])
      pr['0x' + o.toString(16)] = opts.add(o).readS32();
    const P = planes(a[1]);
    if (!P) { this.hit = false; return; }
    const p = P[0];
    const arr = new Uint16Array(p.w * p.h);
    PATTERNJS
    writePlane(p, arr);
    this.inbuf = arr.buffer;
    this.geom = {w: p.w, h: p.h, stride: p.stride};
    this.params = pr;
  },
  onLeave() {
    if (!this.hit || sent) return;
    sent = true;
    const P = planes(this.task);
    send({tag: 'in', geom: this.geom, params: this.params}, this.inbuf);
    send({tag: 'out', geom: this.geom}, readPlane(P[0]));
  }
});
send({info: 'armed'});
"""

PATTERNS = {
    "impulse": r"""
    const BG = BGJS, SP = SPJS, AMPS = AMPSJS;
    for (let i = 0; i < arr.length; i++) arr[i] = BG;
    let n = 0;
    for (let y = SP; y < p.h - SP; y += SP)
      for (let x = SP; x < p.w - SP; x += SP) {
        const v = BG + AMPS[n % AMPS.length]; n++;
        arr[y * p.w + x] = v < 0 ? 0 : (v > 16383 ? 16383 : v);
      }
    """,
    "step": r"""
    const BG = BGJS, SP = SPJS, AMPS = AMPSJS;
    for (let i = 0; i < arr.length; i++) arr[i] = BG;
    let n = 0;
    for (let x0 = SP; x0 + SP < p.w; x0 += SP) {
      const v = BG + AMPS[n % AMPS.length]; n++;
      const c = v < 0 ? 0 : (v > 16383 ? 16383 : v);
      for (let y = 0; y < p.h; y++)
        for (let x = x0; x < x0 + (SP >> 1); x++) arr[y * p.w + x] = c;
    }
    """,
    "flat": r"""
    const BG = BGJS;
    for (let i = 0; i < arr.length; i++) arr[i] = BG;
    """,
    # 关掉死区之后算子是严格线性的,那就别再靠孤立冲激去读那些极小的系数
    # (它们只有 1~2 个 LSB)。灌一整幅白噪声再最小二乘,信息量高好几个数量级。
    "noise": r"""
    const BG = BGJS, R = SPJS;      // 复用 --spacing 传噪声幅度
    let st = 12345;
    for (let i = 0; i < arr.length; i++) {
      st = (st * 1103515245 + 12345) & 0x7fffffff;
      const v = BG + ((st >>> 8) % (2 * R + 1)) - R;
      arr[i] = v < 0 ? 0 : (v > 16383 ? 16383 : v);
    }
    """,
    "ramp": r"""
    const BG = BGJS;
    for (let y = 0; y < p.h; y++)
      for (let x = 0; x < p.w; x++) arr[y * p.w + x] = (x * 16383 / p.w) | 0;
    """,
}


def _opt(flag, default):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


def main():
    arw = sys.argv[1]
    pat = _opt("--pattern", "impulse")
    tile = int(_opt("--tile", "17"))
    bg = int(_opt("--bg", "4000"))
    sp = int(_opt("--spacing", str(SPACING)))
    name = _opt("--out", pat)
    patch = {}
    if "--patch" in sys.argv:
        for kv in _opt("--patch", "").split(","):
            k, v = kv.split("=")
            patch[str(int(k, 0))] = int(v, 0)
    amps = AMPS
    if "--amps" in sys.argv:
        spec = _opt("--amps", "")
        if ":" in spec:      # 起:止:步
            lo, hi, st = (int(x) for x in spec.split(":"))
            amps = list(range(lo, hi + 1, st))
        else:
            amps = [int(x) for x in spec.split(",")]

    body = (PATTERNS[pat].replace("BGJS", str(bg)).replace("SPJS", str(sp))
            .replace("AMPSJS", json.dumps(amps)))
    js = (JS.replace("PATTERNJS", body).replace("RVAJS", str(RVA))
            .replace("TILEJS", str(tile)).replace("PATCHJS", json.dumps(patch)))

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
        store[p["tag"]] = np.frombuffer(data, "<u2").reshape(g["h"], g["w"]).copy()
        if "params" in p:
            store["params"] = p["params"]
            print("   opts:", p["params"], flush=True)
        print(f"   {p['tag']} {g['w']}x{g['h']} stride {g['stride']}", flush=True)

    subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
    time.sleep(1.0)
    pid = frida.spawn([EXE, arw])
    session = frida.attach(pid)
    script = session.create_script(js)
    script.on("message", on_message)
    script.load()
    frida.resume(pid)
    deadline = time.time() + 60
    while time.time() < deadline and "out" not in store:
        time.sleep(0.2)
    try:
        session.detach()
        frida.kill(pid)
    except Exception:
        pass
    if "out" not in store:
        raise SystemExit("没抓到")
    os.makedirs(os.path.join(SCR, "sharpprobe"), exist_ok=True)
    dest = os.path.join(SCR, "sharpprobe", f"inj_{name}.npz")
    np.savez_compressed(dest, inp=store["in"], out=store["out"],
                        meta=np.array([bg, sp, tile]),
                        amps=np.array(amps),
                        params=np.array(json.dumps(store.get("params", {})), dtype=object))
    print("SAVED", dest)


if __name__ == "__main__":
    main()
