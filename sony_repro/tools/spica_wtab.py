r"""把 Spica 方向滤波的**整个权重表族**拉下来。

`spica_params.py` 已经确认(挂 `0x39ead0` 抓 `args[0..15]`):

* `args[10]` 是分类索引,`args[13]` 是该索引对应的权重表,
  **表地址 = base + idx * 104**(104 字节 = 26 个 float = 一张表);
* 每张表 26 个 float,**只有 1..25 是权重**(偏移 0x04..0x64),取值全为整数、
  **和恒等于 512**;slot 0 是 padding,里面可能是残留垃圾,不能拿它当判据。

所以只要命中一次就能反推 base,然后整片 dump。和为 512 正好当作**表边界的判据**:
连续多张不满足就说明走到表族外面了。

用法(Windows 的 python):
    python spica_wtab.py <ARW> [--span 512] [--secs 120]
输出 spica_wtab.json: {"base":…, "stride":104, "tables":{idx:[25 个权重]}}
"""
import json
import os
import subprocess
import sys
import time

import frida

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
SCR = os.path.dirname(os.path.abspath(__file__))
FILTER = 0x39EAD0
STRIDE, NFLOAT = 104, 26          # 一张表 26 个 float
TARGET_SUM = 512                  # 2^9,和归一化到 9 位定点


def _opt(flag, default):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
const SPAN = SPANJS, STRIDE = STRIDEJS, NFLOAT = NFLOATJS;
let done = false;

Interceptor.attach(base.add(FILTERJS), {
  onEnter(a) {
    if (done) return;
    const idx = a[10].toInt32(), tab = a[13];
    if (tab.isNull() || idx < 0) return;
    done = true;
    // 表族起点:命中的那张往回退 idx 张
    const origin = tab.sub(idx * STRIDE);
    const out = [];
    for (let t = 0; t < SPAN; t++) {
      const p = origin.add(t * STRIDE);
      let row = null;
      try { row = Array.from({length: NFLOAT}, (_, k) => p.add(k * 4).readFloat()); } catch (e) {}
      out.push(row);
    }
    send({origin: origin.toString(), hit_idx: idx, hit_ptr: tab.toString(), tables: out});
  }
});
send({info: 'armed'});
"""


def main():
    arw = sys.argv[1]
    span = int(_opt("--span", "512"))
    secs = float(_opt("--secs", "120"))
    js = (JS.replace("FILTERJS", str(FILTER)).replace("SPANJS", str(span))
            .replace("STRIDEJS", str(STRIDE)).replace("NFLOATJS", str(NFLOAT)))

    got = {}

    def on_message(msg, _data):
        if msg["type"] != "send":
            print("  !", msg, flush=True)
            return
        p = msg["payload"]
        if "info" in p:
            print("  ", p, flush=True)
            return
        got.update(p)

    subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
    time.sleep(1.0)
    pid = frida.spawn([EXE, arw])
    session = frida.attach(pid)
    script = session.create_script(js)
    script.on("message", on_message)
    script.load()
    frida.resume(pid)
    deadline = time.time() + secs
    while time.time() < deadline and "tables" not in got:
        time.sleep(0.3)
    try:
        session.detach()
        frida.kill(pid)
    except Exception:
        pass

    if "tables" not in got:
        print("没命中")
        return

    print(f"命中 idx={got['hit_idx']} @ {got['hit_ptr']},表族起点 {got['origin']}")
    valid = {}
    for t, row in enumerate(got["tables"]):
        if not row or any(v is None for v in row):
            continue
        # slot 0(偏移 0x00)是 padding —— 0x39ead0 只读 0x04..0x64 这 25 个。
        # 早先把「slot 0 恒为 0」当判据,结果把索引 0 和 7 筛掉了:它们的 padding
        # 里是残留浮点垃圾(2.98e-23 / 4.49e-39)。索引 0 正是平坦区(rng < cfg[8])
        # 用的那张,少了它平坦像素会算出几百的误差。
        w = row[1:NFLOAT]
        if sum(w) != TARGET_SUM or not all(float(v).is_integer() for v in w):
            continue
        valid[t] = [int(v) for v in w]

    print(f"和为 {TARGET_SUM} 的合法表: {len(valid)} / {span}(索引 0 是平坦区用的)")
    if valid:
        ks = sorted(valid)
        print(f"  索引范围 {ks[0]} .. {ks[-1]}")
        gaps = [k for k in range(ks[0], ks[-1] + 1) if k not in valid]
        print(f"  区间内缺口 {len(gaps)} 个" + (f": {gaps[:20]}" if gaps else " —— 连续"))
        for k in ks[:3]:
            print(f"  [{k}] {valid[k]}")

    # 原始行也留着,方便事后核对 padding。
    out = os.path.join(SCR, "spica_wtab.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"base": got["origin"], "stride": STRIDE, "hit_idx": got["hit_idx"],
                   "tables": valid, "raw": got["tables"][:8]}, f)
    print("SAVED", out)


if __name__ == "__main__":
    main()
