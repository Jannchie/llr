r"""SIMDSharpness 的强度是怎么算出来的:把公式里每一项实测出来。

反汇编给出(SIMD 版 0x140386da0,标量孪生 0x140388c00 逐条相同):

    opts = *(param3+8);  obj = *(param3+0)
    lvl  = opts[0x208]                       # Imaging Edge 的锐度滑块,默认 0
    n    = opts[0x2c4] / 50.0
    tbl  = obj->vftable[0xd8](obj, opts[0x1e0])      # 按模式取一张标定表
    s    = obj[0x2275c] (float);  ref = obj[0x1ac] (int)
    若 s != 1 且 lvl > ref:  t = (100-lvl)/(100-ref);  s = 2*(1-t) + s*t
    amp  = (lvl+100) * 0.5 * tbl[0x1060] * n / 1024 * s

钩在 `call [rax+0xd8]` 的下一条指令(RVA 0x386f05)上直接读 rax —— 不去主动调那个
虚函数,免得引入副作用。

用法: python sharp_amp.py <ARW>
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
AFTER_CALL = 0x386F05      # SIMD 版里虚调用之后的那条指令

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
let a = null, done = false;

Interceptor.attach(base.add(RVAJS), { onEnter(x) {
  if (done) return;
  const opts = x[2].add(8).readPointer(), obj = x[2].readPointer();
  a = {};
  for (const o of [0x1e0, 0x208, 0x20c, 0x210, 0x214, 0x2c4])
    a['opts_0x' + o.toString(16)] = opts.add(o).readS32();
  a['obj_0x1ac'] = obj.add(0x1ac).readS32();
  a['obj_0x2275c'] = obj.add(0x2275c).readFloat();
}});

Interceptor.attach(base.add(ACJS), { onEnter() {
  if (done || !a) return;
  done = true;
  const tbl = this.context.rax;
  a['tbl'] = tbl.sub(base).toString();
  a['tbl_0x1060'] = tbl.add(0x1060).readFloat();
  // 顺带把这张表 +0x1040..+0x10a0 一段抓下来,看看邻居都是些什么
  const near = [];
  for (let o = 0x1040; o < 0x10a0; o += 4) near.push(tbl.add(o).readFloat());
  a['tbl_near'] = near;
  send({ok: 1, data: a});
}});
send({info: 'armed'});
""".replace("RVAJS", str(RVA)).replace("ACJS", str(AFTER_CALL))


def probe(arw, wait=60.0):
    subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
    time.sleep(1.0)
    pid = frida.spawn([EXE, str(arw)])
    got = {}
    try:
        session = frida.attach(pid)
        script = session.create_script(JS)
        script.on("message", lambda m, d: m["type"] == "send" and (
            got.update(m["payload"]["data"]) if m["payload"].get("ok") else None))
        script.load()
        frida.resume(pid)
        deadline = time.time() + wait
        while time.time() < deadline and not got:
            time.sleep(0.2)
        try:
            session.detach()
        except Exception:
            pass
    finally:
        try:
            frida.kill(pid)
        except Exception:
            pass
    return got


def amp_of(g):
    lvl, ref, s = g["opts_0x208"], g["obj_0x1ac"], g["obj_0x2275c"]
    if s != 1.0 and lvl > ref:
        t = (100 - lvl) / (100 - ref)
        s = 2 * (1 - t) + s * t
    return (lvl + 100) * 0.5 * g["tbl_0x1060"] * (g["opts_0x2c4"] / 50.0) / 1024.0 * s


def main():
    for arw in sys.argv[1:]:
        g = probe(arw)
        if not g:
            print(f"{os.path.basename(arw)}: 没抓到", flush=True)
            continue
        print(f"{os.path.basename(arw)}:")
        print("   ", {k: v for k, v in g.items() if k != "tbl_near"})
        print(f"    -> amp = {amp_of(g):.6f}")
        print("    tbl[0x1040..0x10a0):", ["%.4g" % v for v in g["tbl_near"]])


if __name__ == "__main__":
    main()
