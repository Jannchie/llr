r"""把 SSCS 的输入整幅抓下来 —— 它是验证前面所有阶段的靶子。

SSCS 拿到的三个平面已经走完 LinearMatrix16 → MainGamma → RGB2YCC → ChromaSuppres
→ YGamma → YCC2RGB → ITP,所以离线复刻只要能对上这一幅,前面整条链就都对了。

Edit.exe 有单实例转发:上一次的进程还活着时,新 spawn 的会把文件交给它然后自己退出,
钩子就永远不触发。所以每次先清干净。

用法: python sscs_frame.py <arw> [wait_sec]
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
let done = false;

Interceptor.attach(base.add(0x38618D), {
  onEnter() {
    if (done) return;
    done = true;
    const planes = [this.context.rbx, this.context.rsi,
                    this.context.rsp.add(0x40).readPointer()];
    try {
      const w = planes[0].add(8).readU32(), h = planes[0].add(0xc).readU32();
      const stride = planes[0].add(0x14).readU32();
      send({info: 'plane ' + w + 'x' + h + ' stride=' + stride});
      send({ok: 'shape', w: w, h: h, stride: stride});
      planes.forEach(function (pl, i) {
        send({ok: 'px' + i}, pl.add(0x20).readPointer().readByteArray(h * stride));
      });
    } catch (e) { send({err: '' + e}); }
  }
});
send({info: 'hook installed'});
"""


def main():
    arw = sys.argv[1]
    wait = float(sys.argv[2]) if len(sys.argv) > 2 else 120.0
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sscs_frame.npz")

    subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True)
    time.sleep(1.0)

    pid = frida.spawn([EXE, arw])
    session = frida.attach(pid)
    script = session.create_script(JS)
    got, shape = {}, {}

    def on_msg(m, d):
        if m["type"] != "send":
            print("ERR", m, flush=True)
            return
        p = m["payload"]
        if p.get("info"):
            print("info:", p["info"], flush=True)
        elif p.get("err"):
            print("ERR:", p["err"], flush=True)
        elif p["ok"] == "shape":
            shape.update(p)
        else:
            got[p["ok"]] = bytes(d)
            print(f"  {p['ok']} {len(got[p['ok']])} bytes", flush=True)

    script.on("message", on_msg)
    script.load()
    frida.resume(pid)

    deadline = time.time() + wait
    while time.time() < deadline and len(got) < 3:
        time.sleep(0.2)

    if len(got) == 3:
        h, w, stride = shape["h"], shape["w"], shape["stride"]
        planes = [np.frombuffer(got[f"px{i}"], dtype="<u2").reshape(h, stride // 2)[:, :w]
                  for i in range(3)]
        np.savez_compressed(out, p0=planes[0], p1=planes[1], p2=planes[2])
        print(f"SAVED {out}  {w}x{h}", flush=True)
        for i, p in enumerate(planes):
            print(f"  plane{i} min={p.min()} max={p.max()} 中位={int(np.median(p))} "
                  f"p99={int(np.percentile(p, 99))}", flush=True)
        lo, hi = 2380, 7934
        over = (planes[0] > lo) & (planes[1] > lo) & (planes[2] > lo)
        full = (planes[0] >= hi) & (planes[1] >= hi) & (planes[2] >= hi)
        print(f"SSCS 触发 {over.mean()*100:.1f}%,完全去色 {full.mean()*100:.1f}%", flush=True)
    else:
        print("NOTHING captured", flush=True)

    try:
        session.detach()
        frida.kill(pid)
    except Exception:
        pass


if __name__ == "__main__":
    main()
