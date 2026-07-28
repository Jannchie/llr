r"""SIMDSharpness 的参数在哪儿:把它读的那个 opts 结构整段抓下来。

反汇编(标量孪生 ZcTaskSharpness @ 0x140388c00,SIMD 版 0x140386da0 前言逐条相同)
给出取值路径 —— 和 YGamma 的 bl/wl 是同一个结构:

    opts = *(param3 + 8)          # param3 = 执行函数的第三个参数
    obj  = *(param3 + 0)
    a = (opts[0x20c] + 100) / 100
    b = (opts[0x210] + 100) / 100
    v = opts[0x214];  c = v < 0 ? (v + 100) * 0.25 : (v + 25)
    lvl = opts[0x208]                       # 主档位
    n = opts[0x2c4] / 50.0
    obj->vfunc[0xd8](opts[0x1e0]) -> tbl;   tbl[0x1060] 是一个 float
    s = obj[0x2275c] (float);  ref = obj[0x1ac] (int)
    若 s != 1 且 lvl > ref:  t = (100-lvl)/(100-ref);  s = 2*(1-t) + s*t
    amp = (lvl + 100) * 0.5 * tbl[0x1060] * n / 1024 * s

用法(Windows 的 Python):
    python sharp_param.py <ARW> [--out 名字]
"""
import json
import os
import subprocess
import sys
import time

import frida

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
SCR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(SCR, "task_execs.json"), encoding="utf-8") as f:
    RVA = json.load(f)["ZcTaskSIMDSharpness"]["rva"]

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
let done = false;
Interceptor.attach(base.add(RVAJS), { onEnter(a) {
  if (done) return;
  done = true;
  try {
    const opts = a[2].add(8).readPointer();
    const obj = a[2].readPointer();
    const out = {opts: {}, obj: {}};
    for (let o = 0x180; o < 0x300; o += 4) out.opts['0x' + o.toString(16)] = opts.add(o).readS32();
    out.obj['0x1ac'] = obj.add(0x1ac).readS32();
    out.obj['0x2275c_f'] = obj.add(0x2275c).readFloat();
    // 虚函数 [vtbl+0xd8](obj, opts[0x1e0]) 返回的表里 +0x1060 是个 float
    const vt = obj.readPointer();
    out.vfn = vt.add(0xd8).readPointer().sub(base).toString();
    send({ok: 1, data: out});
  } catch (e) { send({err: '' + e}); }
}});
send({info: 'armed'});
""".replace("RVAJS", str(RVA))


def main():
    arw = sys.argv[1]
    name = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else "base"
    subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
    time.sleep(1.0)
    pid = frida.spawn([EXE, arw])
    session = frida.attach(pid)
    got = {}

    def on_msg(m, d):
        if m["type"] != "send":
            return
        p = m["payload"]
        if p.get("err"):
            print("ERR", p["err"], flush=True)
        elif p.get("ok"):
            got.update(p["data"])
        else:
            print("  ", p, flush=True)

    script = session.create_script(JS)
    script.on("message", on_msg)
    script.load()
    frida.resume(pid)
    deadline = time.time() + 60
    while time.time() < deadline and not got:
        time.sleep(0.2)
    try:
        session.detach()
        frida.kill(pid)
    except Exception:
        pass
    if not got:
        raise SystemExit("没抓到")
    os.makedirs(os.path.join(SCR, "sharpprobe"), exist_ok=True)
    dest = os.path.join(SCR, "sharpprobe", f"{name}.json")
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(got, f, indent=1)
    print("SAVED", dest)
    nz = {k: v for k, v in got["opts"].items() if v}
    print("obj:", got["obj"], " vfn rva:", got.get("vfn"))
    print("opts 非零项:")
    for k, v in nz.items():
        print(f"  {k:>8} = {v}")


if __name__ == "__main__":
    main()
