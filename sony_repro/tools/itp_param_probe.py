r"""在判据 crit(0x35f1c0) / b8(0x361950) / aniso(0x3b2be0) 入口读参数块的真实内容。"""
import os, subprocess, sys, time, struct, json
import frida
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
let n = 0;
function blk(p, len) { try { return Array.from(new Uint8Array(p.readByteArray(len))); } catch (e) { return null; } }
Interceptor.attach(base.add(0x35f1c0), { onEnter(a) {
  if (n++ > 6) return;
  send({who: 'crit', p6: a[6].toString(), blk: blk(a[6], 0x40), rva: a[6].sub(base).toString()});
}});
Interceptor.attach(base.add(0x361950), { onEnter(a) {
  send({who: 'b8', p5: a[5].sub(base).toString(), blk5: blk(a[5], 0x40), p6: a[6].sub(base).toString(), blk6: blk(a[6], 0x40)});
}});
Interceptor.attach(base.add(0x3b2be0), { onEnter(a) {
  send({who: 'aniso', p5: a[4].sub(base).toString(), blk: blk(a[4], 0x40)});
}});
"""
def main():
    arw = os.path.abspath(sys.argv[1])
    subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False); time.sleep(0.8)
    pid = frida.spawn([EXE, arw]); seen = []
    try:
        s = frida.attach(pid); sc = s.create_script(JS)
        sc.on("message", lambda m, d: seen.append(m["payload"]) if m["type"] == "send" else print(m))
        sc.load(); frida.resume(pid)
        t0 = time.time()
        while time.time() - t0 < 60 and len(seen) < 8: time.sleep(0.2)
    finally:
        subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
    for p in seen:
        for k, v in p.items():
            if isinstance(v, list) and v:
                b = bytes(v)
                print(p["who"], k, "flag+floats(+1):", b[0], [round(x, 5) for x in struct.unpack("<12f", b[1:49])], " floats(+0):", [round(x, 5) for x in struct.unpack("<4f", b[0:16])])
            elif k != "who":
                print(p["who"], k, v)
main()
