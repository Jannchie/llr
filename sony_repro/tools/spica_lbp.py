r"""验证 `args[10]`(选权重表的那个索引)就是标量分类器 `FUN_140359390` 的输出。

标量那份是死代码(执行路径上零命中,见 PIPELINE §7.11.4),但它的算法应当和 SIMD
内联的那份同构。这里不做假设,直接对着**运行时的真实像素**验:

`args[1..7]` 是 7 行的行首指针(间距恰好 = stride),所以在 onEnter 里就能把该像素
的 7x7 邻域读出来 —— **不需要和 tile dump 配对**,自洽。然后离线按标量那份的
写法算一遍 LBP,和 `args[10]` 逐个比对。

标量分类器(9 点 LBP,位序按空间排):
    在 5x5 十字取 9 点求 min/max;  thr = min + (max - min) / 2   (有符号除 2)
    bit 0,1 = (y-2,x) (y-1,x);  bit 2..6 = (y, x-2 … x+2);  bit 7,8 = (y+1,x) (y+2,x)
    if (mask & 0x100)  mask = ~mask & 0xff
    if (max - min < 阈值)  return 0

用法(Windows 的 python):
    python spica_lbp.py <ARW> [--hits 3000] [--secs 120]
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
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
const HITS = HITSJS;
let n = 0;

Interceptor.attach(base.add(FILTERJS), {
  onEnter(a) {
    if (n++ >= HITS) return;
    const x = a[8].toInt32(), y = a[9].toInt32(), idx = a[10].toInt32();
    if (x < 3) return;
    const rows = [];
    for (let k = 1; k <= 7; k++) {
      let arr = null;
      try {
        const p = a[k];
        if (!p.isNull()) {
          arr = [];
          for (let d = -3; d <= 3; d++) arr.push(p.add((x + d) * 2).readU16());
        }
      } catch (e) {}
      rows.push(arr);
    }
    send({x: x, y: y, idx: idx, dx: a[11].toInt32(), dy: a[12].toInt32(), rows: rows});
  }
});
send({info: 'armed'});
"""


def lbp(rows, center_row):
    """按标量分类器的写法算 9 点 LBP。rows 是 7 行 x 7 列,列中心在下标 3。"""
    c = center_row
    pts = [rows[c - 2][3], rows[c - 1][3],                       # bit 0,1  垂直上
           rows[c][1], rows[c][2], rows[c][3], rows[c][4], rows[c][5],   # bit 2..6 水平
           rows[c + 1][3], rows[c + 2][3]]                       # bit 7,8  垂直下
    lo, hi = min(pts), max(pts)
    rng = hi - lo
    thr = rng // 2 if rng >= 0 else -((-rng) // 2)               # 有符号除 2,向零取整
    mask = 0
    for b, v in enumerate(pts):
        if v - lo >= thr:
            mask |= 1 << b
    if mask & 0x100:
        mask = (~mask) & 0xFF
    return mask, rng


def main():
    arw = sys.argv[1]
    hits = int(_opt("--hits", "3000"))
    secs = float(_opt("--secs", "120"))
    js = JS.replace("FILTERJS", str(FILTER)).replace("HITSJS", str(hits))

    rows = []

    def on_message(msg, _data):
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

    good = [r for r in rows if r["rows"] and all(r["rows"])]
    print(f"\n{len(rows)} 次调用,{len(good)} 次 7 行全部读到")
    if not good:
        return

    # 7 行窗口里哪一行是中心行不确定,全试一遍 —— 命中率最高的那行就是
    print("\n中心行  与 args[10] 一致的比例")
    best = None
    for c in (2, 3, 4):
        hit = sum(1 for r in good if lbp(r["rows"], c)[0] == r["idx"])
        pct = 100 * hit / len(good)
        print(f"  rows[{c}]   {pct:8.4f}%   ({hit}/{len(good)})")
        if best is None or pct > best[1]:
            best = (c, pct)
    print(f"\n最佳:中心行 rows[{best[0]}],一致 {best[1]:.4f}%")

    c = best[0]
    mism = [(r, lbp(r["rows"], c)) for r in good if lbp(r["rows"], c)[0] != r["idx"]]
    print(f"不一致 {len(mism)} 例,前 6 例(算得 / 实际 / 动态范围):")
    for r, (m, rng) in mism[:6]:
        print(f"  x={r['x']:5d} y={r['y']:4d}  算得 {m:3d}  实际 {r['idx']:3d}  range={rng}")

    print("\nargs[10] 的取值分布(前 12):")
    for k, v in collections.Counter(r["idx"] for r in good).most_common(12):
        print(f"  idx {k:3d}: {v} 次")
    print(f"  取值范围 {min(r['idx'] for r in good)} .. {max(r['idx'] for r in good)}")

    out = os.path.join(SCR, "spica_lbp.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rows, f)
    print("SAVED", out)


if __name__ == "__main__":
    main()
