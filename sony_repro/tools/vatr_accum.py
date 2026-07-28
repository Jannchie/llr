r"""三个网格累加函数(`FUN_140188b60` / `0x189220` / `0x1895a0`)到底跑哪一个?

`FUN_1401887b0` 按 `arw->vt[0xf0]()` 的返回值 1/3/4 三选一。先动态确定分支,
再只反汇编那一个 —— 3508 字节里只有一份是活的。

顺带记下参数和调用次数:累加是一次扫完整幅,还是按行/块多次调用,
从调用次数就能看出来。

用法(Windows 的 Python):
    python vatr_accum.py <ARW>
"""
import subprocess
import sys
import time

import frida

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
FNS = {"FUN_140188b60": 0x188B60, "FUN_140189220": 0x189220, "FUN_1401895a0": 0x1895A0,
       "FUN_1401887b0(setup)": 0x1887B0}

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
const FNS = FNSJSON;
const stat = {};
for (const name in FNS) {
  stat[name] = {n: 0, first: null};
  Interceptor.attach(base.add(FNS[name]), { onEnter(a) {
    const s = stat[name];
    s.n++;
    if (s.n > 1) return;
    const args = [];
    for (let i = 0; i < 6; i++) {
      const p = a[i];
      let deref = null;
      try { deref = p.readPointer().toString(); } catch (e) {}
      args.push({raw: p.toString(), deref: deref,
                 i32: (function () { try { return p.toInt32(); } catch (e) { return null; } })()});
    }
    s.first = {args: args, ret: this.returnAddress.sub(base).toString(16)};
  }});
}
setTimeout(function () { send({done: 1, stat: stat}); }, WAITJS);
send({info: 'armed'});
"""


def main():
    arw = sys.argv[1]
    subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
    time.sleep(1.0)
    pid = frida.spawn([EXE, str(arw)])
    got = {}
    try:
        session = frida.attach(pid)
        script = session.create_script(
            JS.replace("FNSJSON", repr(FNS).replace("'", '"')).replace("WAITJS", "25000"))

        def on_msg(m, _d):
            if m["type"] == "send" and m["payload"].get("done"):
                got.update(m["payload"]["stat"])
            elif m["type"] == "error":
                print("   JS ERR", m.get("description"), flush=True)

        script.on("message", on_msg)
        script.load()
        frida.resume(pid)
        deadline = time.time() + 60
        while time.time() < deadline and not got:
            time.sleep(0.3)
    finally:
        try:
            frida.kill(pid)
        except Exception:
            pass

    for name, s in got.items():
        print(f"\n{name} @ rva 0x{FNS[name]:x}: 调用 {s['n']} 次")
        if s["first"]:
            print(f"   返回地址 rva 0x{s['first']['ret']}")
            for i, a in enumerate(s["first"]["args"]):
                print(f"   arg{i} = {a['raw']:>18}  int32={a['i32']:>12}  "
                      f"*p={a['deref']}")


if __name__ == "__main__":
    main()
