r"""抓 SSCS 的参数:四个 int16 阈值、四个 byte 权重与它们的分派。

SSCS 从一个虚调用 (*(*param_3)+0xd8)() 拿参数块,函数入口拿不到,所以钩子下在
那句调用之后 (RVA 0x38618d),此时 rax 就是参数块、rbx 是第 0 个平面描述符。

用法: python sscs_probe.py <arw> [wait_sec]
"""
import json
import os
import sys
import time

import frida

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
AFTER_VCALL = 0x38618D   # SSCS slot7 里 call [r8+0xd8] 的下一条指令
DUMP = 0x1400            # 覆盖 0xf82 的阈值与 0x1207 的权重

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
send({info: 'base=' + base});
let done = false;

Interceptor.attach(base.add(0x38618D), {
  onEnter() {
    if (done) return;
    done = true;
    const p = this.context.rax;      // 参数块
    const plane0 = this.context.rbx; // 三个平面的描述符
    const plane1 = this.context.rsi;
    const plane2 = this.context.rsp.add(0x40).readPointer();
    try {
      send({info: 'SSCS param=' + p + ' planes=' + plane0 + ' ' + plane1 + ' ' + plane2});
      send({ok: 'plane0'}, plane0.readByteArray(0x30));
      send({ok: 'param'}, p.readByteArray(DUMP_PLACEHOLDER));
      // 三个平面的头 4096 个 uint16 —— 尺度只能从真实像素上读出来
      [plane0, plane1, plane2].forEach(function (pl, i) {
        send({ok: 'px' + i}, pl.add(0x20).readPointer().readByteArray(8192));
      });
    } catch (e) { send({err: '' + e}); }
  }
});
send({info: 'hook installed'});
""".replace("DUMP_PLACEHOLDER", str(DUMP))


def main():
    arw = sys.argv[1]
    wait = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sscs_param.bin")

    pid = frida.spawn([EXE, arw])
    session = frida.attach(pid)
    script = session.create_script(JS)
    got = {}

    def on_msg(m, d):
        if m["type"] != "send":
            print("ERR", m, flush=True)
            return
        p = m["payload"]
        if p.get("info"):
            print("info:", p["info"], flush=True)
        elif p.get("err"):
            print("ERR:", p["err"], flush=True)
        else:
            got[p["ok"]] = bytes(d)

    script.on("message", on_msg)
    script.load()
    frida.resume(pid)

    deadline = time.time() + wait
    while time.time() < deadline and "param" not in got:
        time.sleep(0.2)

    if "param" in got:
        with open(out, "wb") as f:
            f.write(got["param"])
        print("SAVED", out, len(got["param"]), flush=True)
        import struct
        b = got["param"]
        hi, lo0, lo1, lo2 = struct.unpack_from("<4h", b, 0xF82)
        w = struct.unpack_from("<4B", b, 0x1207)
        route = struct.unpack_from("<4i", b, 0x84)
        print(f"阈值 hi=0x f82:{hi}  lo=[{lo0}, {lo1}, {lo2}]", flush=True)
        print(f"权重 0x1207..0x120a = {list(w)}   分派 0x84..0x90 = {list(route)}", flush=True)
        g = [0.0, 0.0, 0.0]
        for val, r in zip(w, route):
            g[r if r in (0, 1) else 2] += val
        print(f"=> g0={g[0]} g1={g[1]} g2={g[2]}  和={sum(g)}", flush=True)
        if "plane0" in got:
            pl = struct.unpack_from("<8i", got["plane0"], 0)
            print(f"plane0 头部 int32: {list(pl)}", flush=True)
        import numpy as np
        for i in range(3):
            if f"px{i}" not in got:
                continue
            a = np.frombuffer(got[f"px{i}"], dtype="<u2")
            print(f"plane{i} 像素 n={a.size} min={a.min()} max={a.max()} "
                  f"中位={int(np.median(a))} p95={int(np.percentile(a, 95))}"
                  f"  头8={a[:8].tolist()}", flush=True)
        if all(f"px{i}" in got for i in range(3)):
            p = [np.frombuffer(got[f"px{i}"], dtype="<u2").astype(int) for i in range(3)]
            over = (p[0] > lo0) & (p[1] > lo1) & (p[2] > lo2)
            full = (p[0] >= hi) & (p[1] >= hi) & (p[2] >= hi)
            print(f"触发去饱和的像素 {over.mean()*100:.1f}%,其中完全去色 {full.mean()*100:.1f}%",
                  flush=True)
        json.dump({"hi": hi, "lo": [lo0, lo1, lo2], "w": list(w),
                   "route": list(route), "g": g},
                  open(out.replace(".bin", ".json"), "w"))
    else:
        print("NOTHING captured", flush=True)

    try:
        session.detach()
        frida.kill(pid)
    except Exception:
        pass


if __name__ == "__main__":
    main()
