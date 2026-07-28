r"""把 Spica 的两遍分开看 —— 重建卡在逐位 22.7% 的最大嫌疑。

`ZcTaskSIMDSpica` 里有两处 `call 0x39ead0`(`0x39df5b` 和 `0x39e2c1`),对应
§7.11.4 说的「分两遍」。此前所有抓取都没区分,重建也只按单遍建模。用返回地址
就能把两遍分开,然后看它们到底哪里不同:

* 输入平面是不是同一个(`args[1..7]` 的行指针基址)
* idx / 朝向的分布是否不同
* 第二遍读的是不是第一遍写出的临时平面

用法: python spica_pass2.py <ARW> [--hits 4000] [--secs 120]
"""
import collections
import json
import os
import subprocess
import sys
import time

import frida

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
SCR = os.path.dirname(os.path.abspath(__file__))
FILTER = 0x39EAD0


def _opt(flag, default):
    return type(default)(sys.argv[sys.argv.index(flag) + 1]) if flag in sys.argv else default


JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
const HITS = HITSJS;
let n = 0;
Interceptor.attach(base.add(FILTERJS), {
  onEnter(a) {
    if (n++ >= HITS) return;
    send({ret: this.returnAddress.sub(base).toString(16),
          x: a[8].toInt32(), y: a[9].toInt32(), idx: a[10].toInt32(),
          dx: a[11].toInt32(), dy: a[12].toInt32(),
          p1: a[1].toString(), p4: a[4].toString(),
          tab: a[13].toString(), out: a[14].toString()});
  }
});
send({info: 'armed'});
"""


def main():
    arw = sys.argv[1]
    hits = _opt("--hits", 4000)
    secs = _opt("--secs", 120.0)
    js = JS.replace("FILTERJS", str(FILTER)).replace("HITSJS", str(hits))
    rows = []

    def on_message(msg, _d):
        if msg["type"] != "send":
            print("  !", msg, flush=True)
            return
        p = msg["payload"]
        if "info" in p:
            print("  ", p, flush=True)
        else:
            rows.append(p)

    subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
    time.sleep(1.0)
    pid = frida.spawn([EXE, arw])
    session = frida.attach(pid)
    script = session.create_script(js)
    script.on("message", on_message)
    script.load()
    frida.resume(pid)
    deadline = time.time() + secs
    while time.time() < deadline and len(rows) < hits:
        time.sleep(0.3)
    try:
        session.detach()
        frida.kill(pid)
    except Exception:
        pass

    print(f"\n{len(rows)} 次调用")
    by = collections.defaultdict(list)
    for r in rows:
        by[r["ret"]].append(r)
    print("按返回地址分组(= 哪一处 call):")
    for ret, rs in sorted(by.items(), key=lambda kv: -len(kv[1])):
        idxs = collections.Counter(r["idx"] for r in rs)
        dirs = collections.Counter((r["dx"], r["dy"]) for r in rs)
        # 行指针的高 24 位 ≈ 平面所在的分配块
        planes = {r["p1"][:9] for r in rs}
        outs = {r["out"][:9] for r in rs}
        print(f"\n  返回地址 +0x{ret}   {len(rs)} 次")
        print(f"    idx 取值 {len(idxs)} 种,最常见 {dict(idxs.most_common(4))}")
        print(f"    朝向 {dict(dirs.most_common())}")
        print(f"    输入平面前缀 {sorted(planes)[:4]}  ({len(planes)} 种)")
        print(f"    输出指针前缀 {sorted(outs)[:4]}  ({len(outs)} 种)")

    if len(by) >= 2:
        ks = sorted(by, key=lambda k: -len(by[k]))
        a, b = by[ks[0]], by[ks[1]]
        pa = {r["p1"] for r in a}
        pb = {r["p1"] for r in b}
        print(f"\n两处 call 的输入行指针是否有交集: {len(pa & pb)} 个共同值"
              f" (各 {len(pa)} / {len(pb)})")
        print("=> " + ("读的是同一批平面" if pa & pb else
                       "**读的是不同的平面** —— 第二遍很可能吃的是第一遍的输出"))

    out = os.path.join(SCR, "spica_pass2.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rows, f)
    print("SAVED", out)


if __name__ == "__main__":
    main()
