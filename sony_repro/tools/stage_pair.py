r"""同一块 tile,在"曲线之后"与"ITP 之后"各抓一份 —— 不需要知道它在原图哪里。

我们的复刻在 MainGamma 那一点是准的(十种外观最大差 0.05%),所以只要拿到同一块内存
在两处的内容,就能把 RGB2YCC → ChromaSuppres → YGamma → YCC2RGB → ITP 这一整段
单独隔离出来验证,而不必跟整幅对齐。

先对指针:MainGamma 与 SSCS 若共用缓冲区,同一块 tile 的 data 指针会重合。

用法: python stage_pair.py <arw> [wait_sec]
"""
import os
import subprocess
import sys
import time

import frida
import numpy as np

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
const planeOf = new NativeFunction(base.add(0x152710), 'pointer', ['pointer', 'int']);
let gamma = 0, sscs = 0;

function describe(desc) {
  return {data: desc.add(0x20).readPointer().toString(),
          w: desc.add(8).readU32(), h: desc.add(0xc).readU32(),
          stride: desc.add(0x14).readU32()};
}

// MainGamma 入口:曲线刚刚写完的那一份
Interceptor.attach(base.add(0x36d220), {
  onEnter(a) {
    if (gamma >= 40) return;
    try {
      const img = a[1].add(8).readPointer();
      const d = [0, 1, 2].map(function (i) { return describe(planeOf(img, i)); });
      const p = planeOf(img, 0).add(0x20).readPointer();
      send({ok: 'gamma', i: gamma++, planes: d}, p.readByteArray(Math.min(d[0].h, 64) * d[0].stride));
    } catch (e) { send({err: 'gamma ' + e}); }
  }
});

// SSCS:ITP 之后
Interceptor.attach(base.add(0x38618D), {
  onEnter() {
    if (sscs >= 40) return;
    try {
      const descs = [this.context.rbx, this.context.rsi,
                     this.context.rsp.add(0x40).readPointer()];
      const d = descs.map(describe);
      send({ok: 'sscs', i: sscs++, planes: d},
           descs[0].add(0x20).readPointer().readByteArray(Math.min(d[0].h, 64) * d[0].stride));
    } catch (e) { send({err: 'sscs ' + e}); }
  }
});
send({info: 'hooks installed'});
"""


def main():
    arw = sys.argv[1]
    wait = float(sys.argv[2]) if len(sys.argv) > 2 else 120.0

    subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True)
    time.sleep(1.0)

    pid = frida.spawn([EXE, arw])
    session = frida.attach(pid)
    script = session.create_script(JS)
    rec = {"gamma": [], "sscs": []}

    def on_msg(m, d):
        if m["type"] != "send":
            return
        p = m["payload"]
        if p.get("info"):
            print("info:", p["info"], flush=True)
        elif p.get("err"):
            print("ERR:", p["err"], flush=True)
        else:
            rec[p["ok"]].append((p, bytes(d)))

    script.on("message", on_msg)
    script.load()
    frida.resume(pid)

    last, deadline = (0, 0), time.time() + wait
    while time.time() < deadline:
        time.sleep(1.0)
        now = (len(rec["gamma"]), len(rec["sscs"]))
        if now == last and now[1] >= 20:
            break
        last = now

    for kind in ("gamma", "sscs"):
        print(f"\n{kind}: {len(rec[kind])} 次", flush=True)
        for p, raw in rec[kind][:6]:
            d = p["planes"][0]
            a = np.frombuffer(raw, dtype="<u2")
            print(f"  #{p['i']:2d} {d['w']}x{d['h']} data={d['data']} "
                  f"前 64 行 max={a.max()} 中位={int(np.median(a))}", flush=True)

    g = {p["planes"][0]["data"] for p, _ in rec["gamma"]}
    s = {p["planes"][0]["data"] for p, _ in rec["sscs"]}
    both = g & s
    print(f"\nMainGamma 用了 {len(g)} 个缓冲区,SSCS 用了 {len(s)} 个,重合 {len(both)}", flush=True)
    if both:
        print("  重合的:", sorted(both)[:5], flush=True)

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stage_pair.npz")
    np.savez_compressed(out, **{
        f"{k}_{i}": np.frombuffer(raw, dtype="<u2")
        for k in ("gamma", "sscs") for i, (_, raw) in enumerate(rec[k])
    }, meta=np.array([str([(p["i"], p["planes"]) for p, _ in rec[k]]) for k in ("gamma", "sscs")]))
    print("SAVED", out, flush=True)

    try:
        session.detach()
        frida.kill(pid)
    except Exception:
        pass


if __name__ == "__main__":
    main()
