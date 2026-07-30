"""把 16 张的逐张结果汇总 —— 关键看**离散度**,不是中位。

参数是在 3 张上扫出来的。要判「是不是只对那几张管用」,得看
每张的「llr / Edit」比值分布,以及它跟 ISO 有没有关系
(引擎里相邻两级 RawNR / Spica 都带 ISO 依赖,这一级一个都没有)。
"""
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

TXT = Path("/home/jannchie/llr/tmp/tone16.txt")
VARIANTS = ("L4 e1e-04", "L4 e4e-04", "L5 e1e-04", "L5 e4e-04")


def main():
    frames, cur = {}, None
    for line in TXT.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^=== (\S+)\s+(?:ISO\s+(\S+))?", line)
        if m:
            cur = m.group(1)
            frames[cur] = {"iso": m.group(2) or "?"}
            continue
        if cur is None:
            continue
        m = re.match(r"^\s{2}(Edit|L\d e\S+)\s+亮度\s+(\S+).*色差/亮度\s+(\S+)", line)
        if m:
            frames[cur][m.group(1).strip()] = float(m.group(3))

    rows = [(k, v) for k, v in frames.items() if "Edit" in v and v["Edit"] > 0]
    print(f"{len(rows)} 张\n")
    print(f"{'片':>9} {'ISO':>6} {'Edit':>7} " + " ".join(f"{v:>11}" for v in VARIANTS))
    print("-" * 66)
    ratios = {v: [] for v in VARIANTS}
    isos = []
    for stem, v in sorted(rows):
        cells = []
        for name in VARIANTS:
            if name in v:
                r = v[name] / v["Edit"]
                ratios[name].append(r)
                cells.append(f"{v[name]:.3f}({r:4.2f}x)")
            else:
                cells.append("        —  ")
        try:
            isos.append(float(v["iso"]))
        except ValueError:
            isos.append(float("nan"))
        print(f"{stem:>9} {v['iso']:>6} {v['Edit']:>7.3f} " + " ".join(cells))

    print("\n各档的 llr/Edit 比值分布:")
    print(f"{'参数':>11} {'中位':>7} {'几何均':>7} {'最小':>7} {'最大':>7} {'最大/最小':>9}")
    for name in VARIANTS:
        a = np.array(ratios[name])
        if not len(a):
            continue
        gm = float(np.exp(np.mean(np.log(a))))
        print(f"{name:>11} {np.median(a):>7.2f} {gm:>7.2f} {a.min():>7.2f} "
              f"{a.max():>7.2f} {a.max() / a.min():>9.1f}")

    a = np.array(ratios["L4 e1e-04"])
    iso = np.array(isos[:len(a)])
    ok = np.isfinite(iso) & np.isfinite(a)
    if ok.sum() > 3:
        r = float(np.corrcoef(np.log(iso[ok]), np.log(a[ok]))[0, 1])
        print(f"\n上线档(L4 e1e-04)的比值 vs ISO:  log-log 相关 {r:+.3f}")
        print("  |r| 大就说明缺一个 ISO 项 —— 引擎里 RawNR / Spica 都有,这一级没有。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
