r"""ITP 有两个入口,先定哪个真跑:`ZcTaskITP`(`0x35af00`)还是
`ZcTaskSIMDITP`(`0x3ae920`)。

`static-itp-spica.md` §2.1 的骨架读的是 `0x35af00`。但 `task_execs.json` 里
`ZcTaskSIMDITP` 是**另一个函数**(`0x3ae920`)—— 与降噪那边一模一样的格局:
`ZcTaskRawNR` 与 `ZcTaskRawNRSIMD` 是两份实现,实际跑的是 SIMD 那份,而笔记一度
按标量版推。别再犯一次:先数触发次数,再决定去读谁。

顺带记下每次调用时 `task->planes[0]` 的宽高与平面数 —— 预览路径的调用宽度小得多
(那张图上 1114),要靠它把预览挡在外面。

用法(Windows 的 Python,要 frida)::

    python itp_which.py <ARW> [--secs 60]
"""
import os
import subprocess
import sys
import time

import frida

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
CANDIDATES = {"ZcTaskITP/OpenCLITP": 0x35AF00, "ZcTaskSIMDITP": 0x3AE920,
              "ZcTaskArgoITP": 0x3C1A40}

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
const count = {};

// planar set 里数得出几个平面:逐个读指针,直到读不出合理的平面描述符。
// (+8 宽 +0xc 高 +0x14 行距 +0x20 数据,与 tile_dump.py 同一套)
function planes(task) {
  try {
    const set = task.add(8).readPointer();
    if (set.isNull()) return null;
    const out = [];
    for (let k = 0; k < 8; k++) {
      const p = set.add(8 + k * 8).readPointer();
      if (p.isNull()) break;
      const w = p.add(8).readS32(), h = p.add(0xc).readS32();
      const stride = p.add(0x14).readS32();
      if (w <= 0 || h <= 0 || w > 20000 || h > 20000) break;
      if (stride < w * 2 || stride > w * 16) break;
      out.push([w, h, stride]);
    }
    return out;
  } catch (e) { return null; }
}

SITESJS.forEach(function (ent) {
  const name = ent[0], rva = ent[1];
  count[name] = 0;
  Interceptor.attach(base.add(rva), {
    onEnter(a) {
      const n = ++count[name];
      if (n > 4) return;                       // 每块 tile 都调,头几次就够
      this.name = name; this.n = n; this.task = a[1];
      send({tag: 'enter', name: name, n: n, planes: planes(a[1])});
    },
    onLeave() {
      if (!this.name) return;
      send({tag: 'leave', name: this.name, n: this.n, planes: planes(this.task)});
    }
  });
});
send({info: 'armed'});
"""


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    arw = os.path.abspath(sys.argv[1])
    secs = float(sys.argv[sys.argv.index("--secs") + 1] if "--secs" in sys.argv else 60)
    sites = [[k, v] for k, v in CANDIDATES.items()]
    js = JS.replace("SITESJS", repr(sites).replace("'", '"'))

    hits = {}

    def on_message(msg, data):
        if msg["type"] != "send":
            print("  !", msg, flush=True)
            return
        p = msg["payload"]
        if "info" in p:
            print("  ", p["info"], flush=True)
            return
        hits.setdefault(p["name"], 0)
        if p["tag"] == "enter":
            hits[p["name"]] += 1
        pl = p["planes"]
        shape = ("解析不出" if pl is None else
                 f"{len(pl)} 个平面 " + " ".join(f"{w}x{h}" for w, h, _ in pl))
        print(f"   {p['name']:<22} #{p['n']} {p['tag']:<5}  {shape}", flush=True)

    subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
    time.sleep(1.0)
    pid = frida.spawn([EXE, arw])
    try:
        session = frida.attach(pid)
        script = session.create_script(js)
        script.on("message", on_message)
        script.load()
        frida.resume(pid)
        time.sleep(secs)
    finally:
        subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)

    print("\n  触发次数(只数了头 4 次的回报):")
    for k in CANDIDATES:
        print(f"    {k:<22} {hits.get(k, 0)}")
    if not hits:
        print("  一个都没触发 —— 这条路径不走 ITP,或者 RVA 不对")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
