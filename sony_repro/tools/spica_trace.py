r"""⚠️ 这个脚本不锁调用次数。Spica 一张图跑 35 次,它抓的和 tile dump 抓的
很可能不是同一次 —— 要和整块平面对照请用 `spica_trace2.py`。

逐像素抓 Spica 的**真值链**,不依赖 tile dump 配对。

同时挂两个点,它们在同一线程上严格交替(0x39e2c1 调卷积,0x39e377 调增益):

  0x39ead0  onEnter: args[1..7] = y-3..y+3 的行指针,args[8]=x, [9]=y,
                     [10]=idx, [11]=dx, [12]=dy   -> 直接读出 7x9 邻域
  0x35a270  onEnter: args[4]=|detail|/128 的带符号原值, [5]=rng, [6]=mid

有了邻域就能自己算 lo/hi/rng/mid/M/idx/dx/dy/S,和引擎的一一对照,
**每一级单独定位**,不会被「dump 的是不是同一张图」这种问题污染。

用法:  python spica_trace.py <ARW> [--n 4000] [--secs 120]
输出 spica_trace.json
"""
import json
import os
import struct
import subprocess
import sys
import time

import frida

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
SCR = os.path.dirname(os.path.abspath(__file__))
FILTER, GAIN = 0x39EAD0, 0x35A270
HALF = 4                      # 每行取 x-4 .. x+4

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
const N = NJS, HALF = HALFJS;
const pend = {};              // 线程 -> 还没配到增益的卷积记录
const rows = [];
let done = false;

Interceptor.attach(base.add(FILTERJS), {
  onEnter(a) {
    if (done) return;
    const tid = this.threadId;
    const x = a[8].toInt32(), y = a[9].toInt32();
    const patch = [];
    try {
      for (let r = 1; r <= 7; r++) {
        const p = a[r];
        const line = [];
        for (let c = -HALF; c <= HALF; c++) line.push(p.add((x + c) * 2).readU16());
        patch.push(line);
      }
    } catch (e) { return; }
    pend[tid] = {x: x, y: y, idx: a[10].toInt32(),
                 dx: a[11].toInt32() << 24 >> 24, dy: a[12].toInt32() << 24 >> 24,
                 patch: patch};
  }
});

Interceptor.attach(base.add(GAINJS), {
  onEnter(a) {
    if (done) return;
    const rec = pend[this.threadId];
    if (!rec) return;
    delete pend[this.threadId];
    rec.xd = a[4].and(0xffffffff).toString();
    rec.rng = a[5].and(0xffffffff).toString();
    rec.mid = a[6].and(0xffffffff).toString();
    rows.push(rec);
    if (rows.length >= N) { done = true; send({rows: rows}); }
  }
});
send({info: 'armed'});
"""


def f32(bits):
    return struct.unpack("<f", struct.pack("<I", int(bits, 0) & 0xFFFFFFFF))[0]


def main():
    arw = sys.argv[1]

    def opt(f, d):
        return sys.argv[sys.argv.index(f) + 1] if f in sys.argv else d

    n, secs = int(opt("--n", "4000")), float(opt("--secs", "120"))
    js = (JS.replace("FILTERJS", str(FILTER)).replace("GAINJS", str(GAIN))
            .replace("NJS", str(n)).replace("HALFJS", str(HALF)))
    got = {}

    def on_message(msg, _data):
        if msg["type"] != "send":
            print("  !", msg, flush=True)
            return
        p = msg["payload"]
        if "info" in p:
            print("  ", p, flush=True)
        else:
            got.update(p)

    subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
    time.sleep(1.0)
    pid = frida.spawn([EXE, arw])
    session = frida.attach(pid)
    script = session.create_script(js)
    script.on("message", on_message)
    script.load()
    frida.resume(pid)
    deadline = time.time() + secs
    while time.time() < deadline and "rows" not in got:
        time.sleep(0.3)
    try:
        session.detach()
        frida.kill(pid)
    except Exception:
        pass

    if "rows" not in got:
        print("没抓到")
        return
    rows = got["rows"]
    for r in rows:
        r["xd"], r["rng"], r["mid"] = f32(r["xd"]), f32(r["rng"]), f32(r["mid"])
    print(f"{len(rows)} 条")
    for r in rows[:3]:
        print(f"  (x={r['x']},y={r['y']}) idx={r['idx']} d=({r['dx']},{r['dy']}) "
              f"xd={r['xd']:.4f} rng={r['rng']:.0f} mid={r['mid']:.0f}")
    out = os.path.join(SCR, "spica_trace.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"source": os.path.abspath(arw), "half": HALF, "rows": rows}, f)
    print("SAVED", out)


if __name__ == "__main__":
    main()
