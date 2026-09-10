"""第二个绿相位的基准表是第一个的什么变换。

`green_base_verify.py` 在 w0 上拿到 99.85% 逐位相同,同一张表放到 w1 只有
56.4% —— 基准表**依赖相位**。两个绿相位在 Bayer 上的几何关系相反,所以合理的
猜测是某种镜像;而另一个平面的偏移还要额外带一个 ±1 的平移,因为两个相位在原图
上错开半格,谁在谁的左上是反过来的。

与其再跑八次捕获,先把这些变换枚举一遍 —— 只要有一个也打到 99% 以上,就说明
两个相位共用同一套几何,只是取向不同。都打不到,再老老实实对 w1 重测。
"""
import itertools
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from green_base_verify import (  # noqa: E402
    BASE_OTHER,
    BASE_OWN,
    MARGIN,
    run,
)

HERE = os.path.dirname(os.path.abspath(__file__))

#: 平面内的对称操作。绿相位平面是规则方格,这四个加上另一平面的整数平移,
#: 覆盖了「同一套几何、不同取向」的全部可能。
FLIPS = {
    "原样": lambda p: p,
    "上下翻": lambda p: (-p[0], p[1]),
    "左右翻": lambda p: (p[0], -p[1]),
    "点对称": lambda p: (-p[0], -p[1]),
    "转置": lambda p: (p[1], p[0]),
}


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "green_impulse_fl_test_w1_noise.npz"
    z = np.load(os.path.join(HERE, name))
    ref, other, out = z["ref"], z["ref2"], z["out"]
    tbl0, tbl3 = z["tbl0"], z["tbl3"]
    off = float(z["offset"][0])
    w = float(tbl3[0]) / 1024.0
    h, wd = ref.shape
    r, W = MARGIN, 6
    eng = out[W:h - W, W:wd - W]
    sl = (slice(W - r, h - W - r), slice(W - r, wd - W - r))
    print(f"{name}  {ref.shape}\n")

    rows = []
    shifts = [(0, 0), (-1, 0), (0, -1), (-1, -1), (1, 0), (0, 1), (1, 1)]
    for (fname, f), (sy, sx) in itertools.product(FLIPS.items(), shifts):
        own = [f(p) for p in BASE_OWN]
        oth = [(f(p)[0] + sy, f(p)[1] + sx) for p in BASE_OTHER]
        # 变换后可能超出安全边界,超了就跳过而不是悄悄截断。
        if max(max(abs(a), abs(b)) for a, b in own + oth) > MARGIN:
            continue
        got = run(ref, other, tbl0, w, off, own, oth)[sl]
        rows.append((float(np.mean(got == eng)) * 100.0, fname, (sy, sx)))

    rows.sort(reverse=True)
    print("  变换                另一平面平移    逐位相同")
    for pct, fname, sh in rows[:12]:
        print(f"    {fname:<8}          {str(sh):<10}   {pct:8.4f}%")
    best = rows[0]
    print(f"\n  最好的是「{best[1]} + 另一平面平移 {best[2]}」= {best[0]:.4f}%")
    if best[0] < 99.0:
        print("  ⚠️ 没有一个变换打到 99% —— 两个相位不共用同一套几何,"
              "得对 w1 重新逐点探。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
