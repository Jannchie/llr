r"""ZcTaskVatr(= DRO)的参数块:整段 dump,靠横比多张图找出「Auto 档位」住在哪。

反汇编 0x140366920 开头给出取值路径 —— **不是** `lv = *(*(task+0x68)+0xc8)` 那一块,
而是同一个对象里的另一段:

    P = *(task + 0x68) + 0xC01C0
    P[0x48] / P[0x4c] / P[0x50]   三个 float,被 shufps 广播成向量(疑似三通道)
    P[0x74]                        一个 float;若 *(param3+8)[0x238] == 1 则改用常量
                                   (0x238 正是 §7.8.5 记的 VatrFloat 守卫 = 编辑参数 0x8023)
    P[0x960] / P[0x964] / P[0x968] 又三个 float
    P[0xd8]                        int,后面拿来算 (n-2) / (n-1),像是某个表的长度

`DynamicRangeOptimizer` 相机只写 `Auto`,档位是引擎按画面自己定的 —— 所以这块里
一定有一个量是**随画面变**的。横比多张图,只看哪几格会动。

用法(Windows 的 Python):
    python vatr_param.py <ARW> [<ARW> ...] [--n 0x1000]
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
    RVA = json.load(f)["ZcTaskVatr"]["rva"]

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
let done = false;
Interceptor.attach(base.add(RVAJS), { onEnter(a) {
  if (done) return;
  done = true;
  try {
    const P = a[1].add(0x68).readPointer().add(0xC01C0);
    const opts = a[2].add(8).readPointer();
    send({ok: 1, guard: opts.add(0x238).readS32(), mode: a[2].add(0x18).readU8()},
         P.readByteArray(NJS));
  } catch (e) { send({err: '' + e}); }
}});
send({info: 'armed'});
""".replace("RVAJS", str(RVA))


def probe(arw, n, wait=60.0):
    subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
    time.sleep(1.0)
    pid = frida.spawn([EXE, str(arw)])
    got = {}
    try:
        session = frida.attach(pid)
        script = session.create_script(JS.replace("NJS", str(n)))

        def on_msg(m, d):
            if m["type"] != "send":
                return
            p = m["payload"]
            if p.get("err"):
                print("   ERR", p["err"], flush=True)
            elif p.get("ok"):
                got["blob"] = bytes(d)
                got["guard"] = p["guard"]
                got["mode"] = p["mode"]

        script.on("message", on_msg)
        script.load()
        frida.resume(pid)
        deadline = time.time() + wait
        while time.time() < deadline and "blob" not in got:
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


def main():
    n = int(sys.argv[sys.argv.index("--n") + 1], 0) if "--n" in sys.argv else 0x1000
    files = [a for a in sys.argv[1:] if not a.startswith("--") and a.endswith(".ARW")]
    out = os.path.join(SCR, "vatrparam")
    os.makedirs(out, exist_ok=True)
    for arw in files:
        stem = os.path.splitext(os.path.basename(arw))[0]
        g = probe(arw, n)
        if "blob" not in g:
            print(f"{stem}: 没抓到", flush=True)
            continue
        with open(os.path.join(out, f"{stem}.bin"), "wb") as f:
            f.write(g["blob"])
        print(f"{stem}: guard={g['guard']} mode={g['mode']} {len(g['blob'])} 字节", flush=True)


if __name__ == "__main__":
    main()
