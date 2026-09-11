r"""spawn Edit 打开一张 ARW,把编辑参数结构体 opts 的头 0x400 字节 dump 出来。"""
import json, os, struct, subprocess, sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import frida
import numpy as np

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
SCR = os.path.dirname(os.path.abspath(__file__))
HOOK = json.load(open(os.path.join(SCR, "task_execs.json"), encoding="utf-8"))["ZcTaskSIMDSharpness"]["rva"]
N = 0x400

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
let done = false;
Interceptor.attach(base.add(HOOKV), { onEnter(a) {
  if (done) return; done = true;
  try { send({ok:1}, a[2].add(8).readPointer().readByteArray(NV)); }
  catch (e) { send({err:''+e}); }
}});
send({info:'armed'});
"""

def capture(arw, wait=90.0):
    subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
    time.sleep(1.0)
    pid = frida.spawn([EXE, str(arw)], stdio="pipe")
    got = {}
    try:
        session = frida.attach(pid)
        script = session.create_script(JS.replace("HOOKV", str(HOOK)).replace("NV", str(N)))
        def on_msg(m, d):
            if m["type"] != "send": return
            if m["payload"].get("ok"): got["blob"] = bytes(d)
            elif m["payload"].get("err"): print("   ERR", m["payload"]["err"], flush=True)
        script.on("message", on_msg)
        script.load()
        frida.resume(pid)
        t0 = time.time()
        while time.time() - t0 < wait and "blob" not in got:
            time.sleep(0.2)
    finally:
        try: frida.kill(pid)
        except Exception: pass
    return got.get("blob")

blob = capture(sys.argv[1])
if blob is None:
    raise SystemExit("没抓到 opts")
np.save(sys.argv[2], np.frombuffer(blob, "u1"))
i32 = np.frombuffer(blob, "<i4")
wanted = {-6: "机内highlights", 1: "机内shadows/clarity", -30: "面板高光", 5: "面板阴影", 10: "面板清晰"}
for off in range(0, N, 4):
    v = int(i32[off // 4])
    if v in wanted:
        print(f"  +0x{off:03x} = {v:6d}   ({wanted[v]})")
