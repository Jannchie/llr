r"""导出那一刻的执行普查:attach 到已运行的 Edit.exe,挂全部 ZcTask exec,然后用
edit_export.py 触发一次导出,数每个 task 跑了几次、第一次的平面 0 有多大。

以前所有普查都是「打开文件」那一刻的预览路径;导出走的才是全分辨率。

    python export_census.py <nr-mode> <文件名> [--jpg]
"""
import json
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import frida  # noqa: E402
import win32gui  # noqa: E402
import win32process  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
const EXECS = EXECSJS;
const counts = {}, sizes = {};
for (const name in EXECS) {
  const rva = EXECS[name];
  try {
    Interceptor.attach(base.add(rva), {
      onEnter(a) {
        counts[name] = (counts[name] || 0) + 1;
        if (!sizes[name]) {
          try {
            const set = a[1].add(8).readPointer();
            const p = set.add(8).readPointer();
            sizes[name] = [p.add(8).readS32(), p.add(12).readS32()];
            const rect = []; for (let o = 0x30; o < 0x40; o += 4) rect.push(a[1].add(o).readS32());
            const pos = []; for (let o = 0x48; o < 0x58; o += 4) pos.push(a[1].add(o).readS32());
            sizes[name].push(rect, pos);
          } catch (e) { sizes[name] = ['?']; }
        }
      }
    });
  } catch (e) { send({info: name + ' 挂不上'}); }
}
rpc.exports = { report() { return {counts: counts, sizes: sizes}; } };
send({info: 'armed ' + Object.keys(EXECS).length});
"""


def edit_pid():
    found = []

    def cb(h, _):
        if win32gui.GetWindowText(h) == "Edit" and win32gui.GetClassName(h).startswith("Afx"):
            found.append(win32process.GetWindowThreadProcessId(h)[1])
        return True
    win32gui.EnumWindows(cb, None)
    return found[0]


def main():
    mode, name = sys.argv[1], sys.argv[2]
    with open(os.path.join(HERE, "task_execs.json"), encoding="utf-8") as f:
        execs = {k: v["rva"] for k, v in json.load(f).items()}
    pid = edit_pid()
    session = frida.attach(pid)
    script = session.create_script(JS.replace("EXECSJS", json.dumps(execs)))
    script.on("message", lambda m, d: print("  ", m.get("payload", m), flush=True))
    script.load()
    time.sleep(0.5)
    cmd = [sys.executable, os.path.join(HERE, "edit_export.py"), mode, name] + (["--jpg"] if "--jpg" in sys.argv else [])
    print("触发导出:", " ".join(cmd[2:]), flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-400:])
    time.sleep(2.0)
    rep = script.exports_sync.report() if hasattr(script, "exports_sync") else script.exports.report()
    session.detach()
    rows = sorted(rep["counts"].items(), key=lambda kv: -kv[1])
    print("\n导出期间跑过的 task(次数 | 第一次的平面0 宽x高 | rect | pos):")
    for k, n in rows:
        s = rep["sizes"].get(k, [])
        print(f"  {k:<32} x{n:<5} {s}")
    print("\n没跑的(与颜色/降噪有关的):", [k for k in execs if k not in rep["counts"] and any(t in k for t in ("NR", "Nr", "Marble", "ITP", "Spica", "Sharp", "Vatr", "3DLut", "Gamma", "YCC"))])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
