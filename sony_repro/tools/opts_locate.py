r"""改机内滑块 -> dump opts -> 和基线 diff,定位每个滑块落在 opts 的哪个偏移。"""
import json, os, shutil, struct, subprocess, sys, tempfile, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import frida
import numpy as np
from slider_probe import find_offsets

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
        s = session.create_script(JS.replace("HOOKV", str(HOOK)).replace("NV", str(N)))
        def on_msg(m, d):
            if m["type"] == "send" and m["payload"].get("ok"): got["blob"] = bytes(d)
        s.on("message", on_msg); s.load(); frida.resume(pid)
        t0 = time.time()
        while time.time() - t0 < wait and "blob" not in got: time.sleep(0.2)
    finally:
        try: frida.kill(pid)
        except Exception: pass
    return got.get("blob")

src = sys.argv[1]
offs = find_offsets(src)
print("文件偏移:", {k: hex(v) for k, v in offs.items()}, flush=True)
base = np.frombuffer(capture(src), "<i4")
print("基线 ok", flush=True)
work = os.path.join(SCR, "opts_work.ARW")
for spec in sys.argv[2:]:
    field, val = spec.split("="); val = int(val)
    shutil.copyfile(src, work)
    with open(work, "r+b") as f:
        f.seek(offs[field]); f.write(struct.pack("<i", val))
    blob = capture(work)
    if blob is None:
        print(f"{field}={val}: 没抓到", flush=True); continue
    cur = np.frombuffer(blob, "<i4")
    d = [(i*4, int(base[i]), int(cur[i])) for i in range(N//4) if base[i] != cur[i]]
    print(f"{field}={val}: " + ", ".join(f"+0x{o:03x} {a}->{b}" for o, a, b in d), flush=True)
