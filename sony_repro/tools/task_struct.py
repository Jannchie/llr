r"""tile 在整幅画面里的坐标藏在 task 结构体的哪个字段?

已知 `+0x30..0x3c` 是有效矩形(值很小,是 tile 内的边距),Marble 里还用到
`+0x48..0x54` 另一个矩形。把 task 前 0xC0 字节当 int32 打出来,坐标应该是
随 tile 递增的大数。

用法: python task_struct.py <ARW> [秒数]
"""
import subprocess
import sys
import time

import frida

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
RVA = 0x389960  # ZcTaskSIMDMarble 的执行函数

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
const rows = [];
Interceptor.attach(base.add(RVA), {
  onEnter(a) {
    if (rows.length >= 8) return;
    const t = a[1], v = [];
    for (let o = 0; o < 0xC0; o += 4) v.push(t.add(o).readS32());
    rows.push(v);
  }
});
rpc.exports.rows = () => rows;
send({info: 'armed'});
""".replace("RVA", str(RVA))

subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
time.sleep(1.0)
pid = frida.spawn([EXE, sys.argv[1]])
session = frida.attach(pid)
script = session.create_script(JS)
script.on("message", lambda m, d: m["type"] == "send" and print("  ", m["payload"], flush=True))
script.load()
frida.resume(pid)
time.sleep(float(sys.argv[2]) if len(sys.argv) > 2 else 25.0)

rows = script.exports_sync.rows()
print("\n偏移      " + "  ".join("tile%-9d" % i for i in range(len(rows))))
for j in range(0x30):
    vals = [r[j] for r in rows]
    if all(v == 0 for v in vals):
        continue
    print("+0x%-4x  " % (j * 4) + "  ".join("%-13d" % v for v in vals))
try:
    session.detach()
    frida.kill(pid)
except Exception:
    pass
