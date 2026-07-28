r"""测 Spica 的 ISO 增益 `xmm11`(= `0x35a450(sensorVal, preset)` 的返回值)。

xmm11 读不到(frida 拿不到 xmm 寄存器,挂 `0x39db97` 会把 Edit.exe 挂死),但它
可以**从像素反解**:引擎传给增益函数的 `args[4]` 就是 `detail/128`,而
`detail = (S - 512·I0)·xmm11`,S 能自己从 `0x39ead0` 的行指针算。所以

    xmm11 = args[4] · 128 / (S - 512·I0)

每张图一个常量,取中位数即可。逐张 ARW 依次 spawn(Edit.exe 单实例,不能并发)。

用法:  python spica_iso.py <ARW...> [--n 300] [--secs 90]
输出 spica_iso.json: [{file, iso, xmm11, n, spread}]
"""
import json
import os
import struct
import subprocess
import sys
import time

import frida
import numpy as np

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
SCR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCR)
from spica_model import F, LANE_A, LANE_B, LANE_C, TAPS, load_wtab  # noqa: E402

FILTER, GAIN, HALF = 0x39EAD0, 0x35A270, 4

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
const N = NJS, HALF = HALFJS;
const pend = {}, rows = [];
let done = false;

Interceptor.attach(base.add(FILTERJS), {
  onEnter(a) {
    if (done) return;
    const x = a[8].toInt32(), patch = [];
    try {
      for (let r = 1; r <= 7; r++) {
        const p = a[r], line = [];
        for (let c = -HALF; c <= HALF; c++) line.push(p.add((x + c) * 2).readU16());
        patch.push(line);
      }
    } catch (e) { return; }
    pend[this.threadId] = {idx: a[10].toInt32(),
                           dx: a[11].toInt32() << 24 >> 24,
                           dy: a[12].toInt32() << 24 >> 24, patch: patch};
  }
});

Interceptor.attach(base.add(GAINJS), {
  onEnter(a) {
    if (done) return;
    const rec = pend[this.threadId];
    if (!rec) return;
    delete pend[this.threadId];
    rec.xd = a[4].and(0xffffffff).toString();
    rows.push(rec);
    if (rows.length >= N) { done = true; send({rows: rows}); }
  }
});
send({info: 'armed'});
"""


def f32(bits):
    return struct.unpack("<f", struct.pack("<I", int(bits, 0) & 0xFFFFFFFF))[0]


def conv(P, W, idx, dx, dy):
    """0x39ead0 的 float32 求和顺序,和 spica_model.convolve 一致。"""
    def px(ty, tx):
        return int(P[3 + ty, HALF + tx])

    def lane(taps):
        v = np.zeros(8, F)
        for j, t in enumerate(taps):
            if t is not None:
                ty, tx = TAPS[t]
                v[j] = F(W[idx, t]) * F(px(ty * dy, tx * dx))
        return v

    v = lane(LANE_C) + (lane(LANE_A) + lane(LANE_B))
    t = v + v[[2, 3, 0, 1, 6, 7, 4, 5]]
    u = t + t[[1, 0, 3, 2, 5, 4, 7, 6]]
    return ((u[4] + (u[0] + F(W[idx, 0]) * F(px(-3 * dy, 0))))
            + F(W[idx, 24]) * F(px(3 * dy, 0)))


def measure(arw, js, secs, W):
    got = {}

    def on_message(msg, _data):
        if msg["type"] == "send" and "rows" in msg["payload"]:
            got.update(msg["payload"])

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
        return None

    vals = []
    for r in got["rows"]:
        P = np.array(r["patch"], np.int64)
        d_raw = float(conv(P, W, r["idx"], r["dx"], r["dy"])) - 512.0 * int(P[3, HALF])
        if abs(d_raw) < 4096:            # 太小的话反解噪声大
            continue
        vals.append(f32(r["xd"]) * 128.0 / d_raw)
    return np.array(vals)


def main():
    def opt(f, d):
        return sys.argv[sys.argv.index(f) + 1] if f in sys.argv else d

    n, secs = int(opt("--n", "300")), float(opt("--secs", "90"))
    files = [a for a in sys.argv[1:] if a.lower().endswith(".arw")]
    js = (JS.replace("FILTERJS", str(FILTER)).replace("GAINJS", str(GAIN))
            .replace("NJS", str(n)).replace("HALFJS", str(HALF)))
    W = load_wtab()

    out = []
    for arw in files:
        iso = subprocess.run(["exiftool", "-q", "-s3", "-ISO", arw],
                             capture_output=True, text=True, check=False).stdout.strip()
        v = measure(arw, js, secs, W)
        if v is None or not len(v):
            print(f"  {os.path.basename(arw)}  ISO {iso}: 没抓到", flush=True)
            continue
        med, spread = float(np.median(v)), float(np.percentile(v, 95) - np.percentile(v, 5))
        out.append({"file": os.path.basename(arw), "iso": int(iso), "xmm11": med,
                    "n": len(v), "spread": spread})
        print(f"  {os.path.basename(arw)}  ISO {iso:>5}:  xmm11 = {med:.6f}"
              f"   (n={len(v)}, 5~95% 跨度 {spread:.2e})", flush=True)

    out.sort(key=lambda r: r["iso"])
    print("\nISO -> xmm11:")
    for r in out:
        print(f"  {r['iso']:>5}  {r['xmm11']:.6f}")
    with open(os.path.join(SCR, "spica_iso.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    print("SAVED spica_iso.json")


if __name__ == "__main__":
    main()
