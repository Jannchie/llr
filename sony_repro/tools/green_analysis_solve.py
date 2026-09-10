"""解绿色的 analysis 核 `0x3a26f0` —— 彩边的真正根源。

`rawnr_e2e_verify.py` 把偏差定位到了这里:红蓝的 analysis 逐位 100% 相同,绿色的
只有 0.5%,误差中位 7.3 个 level。滤波核喂上引擎自己的 ref 时四路都是 99.85%+,
所以问题**不在滤波**。红蓝对、绿色错,三个通道在高光边缘当然会分开 —— 那就是
品红/绿彩边和绿色平面暗块的来路。

现在的实现(12 个自身抽头 + 4 个另一平面抽头加权 3,除以 28)是从反汇编读出来的
猜测,从没验证过。

这次不必再 hook:`rawnr_full_probe.py` 的捕获里同时有 analysis 的**输入**(马赛克)
和**输出**(detail),而 analysis 是纯线性的(没有阈值、没有分支),所以直接最小二乘
就能把权重解出来。真实画面有空间相关,但 raw 噪声本身就是很好的高频激励,而候选
只有 50 个、样本近 20 万。

判据是系数落在简单分数上(引擎用的是整数权重除以一个整数除数),以及残差趋近于零。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
R = 2                       # 候选半径
ORIGIN = [(0, 0), (0, 1), (1, 0), (1, 1)]


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "rawnr_full_fl_test.npz"
    slot = sys.argv[2] if len(sys.argv) > 2 else "g0"
    z = np.load(os.path.join(HERE, name))
    mos = z["mosaic"].astype(np.float64)
    planes = [mos[y0::2, x0::2] for y0, x0 in ORIGIN]
    own, other = (planes[1], planes[2]) if slot == "g0" else (planes[2], planes[1])
    target = z[f"{slot}_detail"].astype(np.float64)
    h, w = own.shape
    m = 8                   # 留出边界,引擎在那儿的填充规则未知

    def col(a, dy, dx):
        return a[m + dy:h - m + dy, m + dx:w - m + dx].ravel()

    names, cols = [], []
    for tag, arr in (("own", own), ("oth", other)):
        for dy in range(-R, R + 1):
            for dx in range(-R, R + 1):
                names.append(f"{tag}({dy:+d},{dx:+d})")
                cols.append(col(arr, dy, dx))
    A = np.stack(cols, axis=1)
    y = target[m:h - m, m:w - m].ravel()
    print(f"{name}  slot={slot}  样本 {y.size}  候选 {A.shape[1]}")

    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    pred = A @ coef
    resid = y - pred
    var = float(np.var(y))
    print(f"  残差:|误差| 中位 {np.median(np.abs(resid)):.6g}  "
          f"最大 {np.abs(resid).max():.6g}   解释 "
          f"{100 * (1 - np.var(resid) / var):.6f}%")

    print("\n  系数(只列 |c| > 1/500 的):")
    for nm, c in sorted(zip(names, coef), key=lambda t: -abs(t[1])):
        if abs(c) > 1 / 500:
            print(f"    {nm:<14} {c:+.6f}   ×28 = {c * 28:+8.4f}"
                  f"   ×16 = {c * 16:+8.4f}")
    big = np.abs(coef) > 1 / 500
    print(f"\n  |c|>1/500 的有 {int(big.sum())} 个,其余 {int((~big).sum())} 个"
          f"最大 {np.abs(coef[~big]).max():.6g}")
    print(f"  系数和 {coef.sum():.6f}(detail 是中心减低通,应当接近 0)")

    # 现有实现的预测,作对照。
    cur = dict.fromkeys(names, 0.0)
    cur["own(+0,+0)"] = 24.0 / 28.0
    for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        cur[f"own({dy:+d},{dx:+d})"] = -2.0 / 28.0
    for dy, dx in ((-1, -1), (-1, 1), (1, -1), (1, 1)):
        cur[f"own({dy:+d},{dx:+d})"] = -1.0 / 28.0
    for dy, dx in ((0, 0), (-1, 0), (0, -1), (-1, -1)):
        cur[f"oth({dy:+d},{dx:+d})"] = -3.0 / 28.0
    cur_v = np.array([cur[n] for n in names])
    cur_pred = A @ cur_v
    print(f"\n  现有实现(12 自身 + 4 跨平面×3 /28)的残差:"
          f"|误差| 中位 {np.median(np.abs(y - cur_pred)):.6g}   解释 "
          f"{100 * (1 - np.var(y - cur_pred) / var):.6f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
