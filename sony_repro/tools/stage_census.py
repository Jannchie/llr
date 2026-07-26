r"""一次把全部 ZcTask* 的执行函数钩上,数各跑了多少次,并按首次命中排出先后。

不知道哪些阶段在执行路径上,就没法判断该复刻哪一段 —— 之前 ZcTask3DLut 白查一场
就是这么来的。执行函数一律在 vftable +0x38(见 dump_execs.py)。

用法: python stage_census.py <ARW> [秒数]
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
    TASKS = json.load(f)
# 几个类的 vftable 是共用的(OpenCLITP 与 ITP 同址),同一 RVA 只挂一次
POINTS = {}
for name, info in sorted(TASKS.items()):
    POINTS.setdefault(info["rva"], name)
POINTS = {name: rva for rva, name in POINTS.items()}

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
const P = POINTS;
const count = {}, order = [];
for (const name in P) {
  Interceptor.attach(base.add(P[name]), { onEnter() {
    count[name] = (count[name] || 0) + 1;
    if (count[name] === 1) order.push(name);
  }});
}
rpc.exports.summary = () => [count, order];
send({info: 'armed ' + Object.keys(P).length});
"""

# 单实例转发:活着的 Edit.exe 会把新进程的文件接过去,钩子永远不触发
subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
time.sleep(1.0)

pid = frida.spawn([EXE, sys.argv[1]])
session = frida.attach(pid)
script = session.create_script(JS.replace("POINTS", json.dumps(POINTS)))
script.on("message", lambda m, d: m["type"] == "send" and print("  ", m["payload"], flush=True))
script.load()
frida.resume(pid)
time.sleep(float(sys.argv[2]) if len(sys.argv) > 2 else 20.0)

count, order = script.exports_sync.summary()
print("\n按首次命中的先后:")
for i, name in enumerate(order):
    print("  %2d. %-40s %d 次" % (i + 1, name, count[name]))
print("\n一次都没跑的:")
print("  " + ", ".join(sorted(set(POINTS) - set(order))))
try:
    session.detach()
    frida.kill(pid)
except Exception:
    pass
