r"""那条 103 点 DRO 曲线,是不是只由参数块里的少数几个标量决定?

若是,离线复刻「Auto 自动定档」就从「重建整套自动判级逻辑」降级成
「估出那几个标量」。这里检验:

  * 各图曲线的形状差异有几个自由度(PCA)
  * 用 P[0x8c] / P[0x90](两个 log2 单位的标量,疑似高光/阴影锚点)
    和 P[0x98..0xb0](六个大整数,疑似直方图累计计数)去回归曲线

用法: python vatr_model.py [vatrparam 目录]
"""
import os
import sys

import numpy as np

SCR = os.path.dirname(os.path.abspath(__file__))
C0, CN = 0x7c8 // 4, 103


def main():
    d = sys.argv[1] if len(sys.argv) > 1 else os.path.join(SCR, "vatrparam")
    names = sorted(f[:-4] for f in os.listdir(d) if f.endswith(".bin"))
    F = {n: np.fromfile(os.path.join(d, n + ".bin"), dtype="<f4") for n in names}
    I = {n: np.fromfile(os.path.join(d, n + ".bin"), dtype="<i4") for n in names}
    x = np.arange(1, CN + 1) / 8.0
    cur = np.stack([F[n][C0:C0 + CN].astype(np.float64) for n in names])
    exc = cur - x                                   # 相对恒等的增量,单位 stop

    print(f"{'图':<10} {'P[0x6c]':>9} {'P[0x8c]':>9} {'P[0x90]':>9} "
          f"{'峰值增量':>9} {'峰值位置(log2)':>14} {'过零(log2)':>11}")
    for i, n in enumerate(names):
        k = int(np.argmax(exc[i]))
        # 峰值之后第一次回到 0
        after = np.where(exc[i][k:] <= 0)[0]
        z = x[k + after[0]] if len(after) else np.nan
        print(f"{n:<10} {I[n][0x6c // 4]:>9} {F[n][0x8c // 4]:>9.4f} "
              f"{F[n][0x90 // 4]:>9.4f} {exc[i][k]:>9.4f} {x[k]:>14.3f} {z:>11.3f}")

    print("\n六个大整数 P[0x98..0xb0](疑似累计直方图计数):")
    for n in names:
        v = [I[n][o // 4] for o in (0x98, 0x9c, 0xa0, 0xa8, 0xac, 0xb0)]
        print(f"  {n:<10} {v}")

    # 形状自由度
    m = exc.mean(0)
    u, s, vt = np.linalg.svd(exc - m, full_matrices=False)
    print("\n增量曲线去均值后的奇异值(相对第一个):",
          np.array2string(s / s[0], precision=4, max_line_width=200))
    print("前 1 / 2 / 3 个主成分解释的方差:",
          ["%.4f" % (np.cumsum(s ** 2)[k] / np.sum(s ** 2)) for k in range(3)])

    # 用 (0x8c, 0x90) 线性回归每个曲线点
    A = np.stack([np.ones(len(names))] + [
        np.array([F[n][o // 4] for n in names], np.float64) for o in (0x8c, 0x90)], 1)
    B, *_ = np.linalg.lstsq(A, exc, rcond=None)
    r = exc - A @ B
    print(f"\n用 (1, P[0x8c], P[0x90]) 回归增量曲线:")
    print(f"  残差 RMS {r.std():.5f} stop   原始 std {(exc - m).std():.5f} stop   "
          f"解释 {100 * (1 - r.var() / (exc - m).var()):.2f}%")
    print(f"  最大残差 {np.abs(r).max():.4f} stop")


if __name__ == "__main__":
    main()
