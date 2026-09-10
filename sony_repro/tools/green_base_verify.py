"""拿测出来的比较基准去**逐位重算**绿色核的捕获。这是最终判据。

前面几步把基准拆到了这个地步:

  * 形式是 `base = w·centre + (1−w)·m25`,w = tbl3/1024 = 1/2 —— 中心权重实测
    0.525±0.015,与 0.5+0.5/25=0.52 吻合(`green_base_center.py`);
  * m25 的每个成员权重实测 0.0202 ≈ (1−w)/25 = 0.02(`green_base_probe.py`);
  * 25 个成员里 **13 个在自身平面、12 个在另一个绿平面**,后者由 refOther 整体
    抬高读出的 k=12.00 定死(`green_base_isolate.py` 的 ringother),再逐点定位。

于是不必再猜几何意义 —— 权重表已经完整。若它是对的,白噪声捕获应当逐位重现;
之前红蓝的 3x3 基准在这份捕获上只有 51.7%,25 个抽头的均值只有 40.1%。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
S, TAPS = 2, 2          # 抽头:间距 2 的 5x5
LEVEL_MAX = 262143.0

#: 比较基准的 25 个成员,平面内偏移 (dy, dx)。自身平面 13 个(含中心),另一个绿
#: 平面 12 个。逐点探出来的,不是推出来的 —— 形状并不规则,别照着「菱形」之类
#: 的直觉去改。
BASE_OWN = [(0, 0), (0, -2), (0, 2),
            (-2, 0),
            (-1, -1), (-1, 0), (-1, 1),
            (1, -1), (1, 0), (1, 1),
            (2, -1), (2, 0), (2, 1)]
#: 另一平面那 12 个点**依赖相位**:两个绿相位的自身平面表一模一样,另一平面表
#: 却是两副。核入口的 flagA/flagB 恰好在两次调用间互换(w0 是 0,−1;w1 是 −1,0),
#: 多半就是这个开关。形状都不规则,别照直觉去「修正」成对称的。
BASE_OTHER_W0 = [(-2, 0), (-2, 1),
                 (-1, -1), (-1, 0), (-1, 1), (-1, 2),
                 (1, 0), (1, 1),
                 (2, -1), (2, 0), (2, 1), (2, 2)]
BASE_OTHER_W1 = [(-1, -1), (-1, 0),
                 (0, -2), (0, -1), (0, 0), (0, 1),
                 (1, -2), (1, -1), (1, 0), (1, 1),
                 (2, -1), (2, 0)]
BASE_OTHER = BASE_OTHER_W0
MARGIN = 4              # 抽头到 ±4,基准到 ±2 —— 取大的


def sh(a: np.ndarray, dy: int, dx: int, r: int) -> np.ndarray:
    h, w = a.shape
    return a[r + dy:h - r + dy, r + dx:w - r + dx]


def run(ref: np.ndarray, other: np.ndarray, tbl0: np.ndarray,
        w: float, off: float, base_own, base_other,
        detail: np.ndarray | None = None,
        gain: float = 256.0, limit: float = 1023.0) -> np.ndarray:
    r = MARGIN
    centre = sh(ref, 0, 0, r)
    m = sum(sh(ref, dy, dx, r) for dy, dx in base_own)
    if base_other:
        m = m + sum(sh(other, dy, dx, r) for dy, dx in base_other)
    n = len(base_own) + len(base_other)
    base = np.float32(w) * centre + np.float32(1.0 - w) * (m / np.float32(n))
    thr = tbl0[np.clip(centre, 0, 16383).astype(np.int32)].astype(np.float32)

    total = np.zeros_like(base)
    count = np.zeros_like(base)
    offs = [k * S for k in range(-TAPS, TAPS + 1)]
    for dy in offs:
        for dx in offs:
            v = sh(ref, dy, dx, r)
            ok = (np.abs(base - v) < thr).astype(np.float32)
            total += ok * v
            count += ok
    mean = np.where(count > 0.0, total / np.maximum(count, 1.0), centre)
    # 白噪声捕获里 detail 是被钩子清零的,真实画面捕获里不是 —— 那时输出还要
    # 叠上 DetailRestore 的 boost。
    if detail is not None:
        boost = np.clip(sh(detail, 0, 0, r) * np.float32(gain / 256.0),
                        -limit, limit)
        mean = mean + boost
    return np.clip(mean - np.float32(off), 0.0, LEVEL_MAX)


def score(got: np.ndarray, eng: np.ndarray, label: str) -> None:
    err = got - eng
    bad = err != 0
    var = float(np.var(eng))
    expl = 100.0 * (1.0 - float(np.var(err)) / var) if var > 0 else float("nan")
    print(f"    {label:<34} 逐位相同 {100 * float(np.mean(~bad)):7.4f}%   "
          f"|误差| 中位 {float(np.median(np.abs(err))):8.4g}  "
          f"最大 {float(np.abs(err).max()):9.4g}   解释 {expl:7.3f}%")


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "green_impulse_fl_test_w0_noise.npz"
    z = np.load(os.path.join(HERE, name))
    ref, other, out = z["ref"], z["ref2"], z["out"]
    tbl0, tbl3 = z["tbl0"], z["tbl3"]
    off = float(z["offset"][0])
    w = float(tbl3[0]) / 1024.0
    det = z["detail"] if "detail" in z.files else None
    gain = float(z["gain"][0]) if "gain" in z.files else 256.0
    limit = float(z["limit"][0]) if "limit" in z.files else 1023.0
    kw = {"detail": det, "gain": gain, "limit": limit}
    h, wd = ref.shape
    r = MARGIN
    W = 6   # 比对时再往里收一圈,避开核自己的边界处理
    eng = out[W:h - W, W:wd - W]
    sl = (slice(W - r, h - W - r), slice(W - r, wd - W - r))
    base_other = BASE_OTHER_W1 if "_w1_" in name else BASE_OTHER_W0
    phase = "w1" if "_w1_" in name else "w0"
    print(f"{name}  {ref.shape}  w={w:.4f}  offset={off:.0f}  相位 {phase}")
    print(f"  基准成员:自身平面 {len(BASE_OWN)} 个 + 另一平面 {len(base_other)} 个 "
          f"= {len(BASE_OWN) + len(base_other)}\n")

    print("  候选基准:")
    score(run(ref, other, tbl0, w, off, BASE_OWN, base_other, **kw)[sl], eng,
          f"实测权重表({phase}:13 自身 + 12 另一平面)")
    # 交叉对照:把另一个相位的表拿过来。若它也打高分,说明这两副表其实没区别,
    # 前面「相位不共用几何」的结论就站不住。
    cross = BASE_OTHER_W0 if phase == "w1" else BASE_OTHER_W1
    score(run(ref, other, tbl0, w, off, BASE_OWN, cross, **kw)[sl], eng,
          "换成另一个相位的另一平面表")
    # 对照组:之前试过的两个,用来确认这份捕获上的分数与历史可比。
    m9 = [(dy, dx) for dy in (-S, 0, S) for dx in (-S, 0, S)]
    score(run(ref, other, tbl0, w, off, m9, [], **kw)[sl], eng, "红蓝的 3x3(间距 2)")
    m25 = [(dy * S, dx * S) for dy in range(-2, 3) for dx in range(-2, 3)]
    score(run(ref, other, tbl0, w, off, m25, [], **kw)[sl], eng, "25 个抽头的均值")
    score(run(ref, other, tbl0, w, off, BASE_OWN, [], **kw)[sl], eng,
          "只取自身平面那 13 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
