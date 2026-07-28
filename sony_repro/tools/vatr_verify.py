r"""验证静态那边的结论:Vatr 的成品曲线 = ARW 里 `0x781b`/`0x781c` 的分段三次 Bézier。

`notes/static-vatr-dro.md` §5 给出生成式,并在 §8 点名要动态这边核对
「`vatr+0x7c4` 的 104 个 float」。`vatr_param.py` 抓下来的正好就是这块,
于是这一步纯离线就能做完 —— 不用再开 Edit.exe。

    knot[i]  = tag0x781b[i] / 1024      (10 项)
    ctrl[i]  = tag0x781c[i] / 1024      (28 项 = 9 段 x 3 + 1)
    step     = vatr[0x70] / 104
    对 i = 0..103:
        xx = i * step
        j  = 第一个使 xx < knot[j+1] 的 j,否则 9
        u  = (xx - knot[j]) / (knot[j+1] - knot[j])
        curve[i] = min(Bezier3(ctrl[3j..3j+3], u), vatr[0x70])

用法(WSL 的 python 即可):
    python vatr_verify.py            # 遍历 tools/vatrparam/*.bin
"""
import os
import struct
import sys

import numpy as np

SCR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(SCR))
from sony_repro.sr2 import read_sr2_tag  # noqa: E402

SRC = ["/mnt/e/temp_photo", "/mnt/e/temp_photo/10160623", "/mnt/e/temp_photo/10760723",
       "/mnt/e/10960725"]


def find_arw(stem):
    for d in SRC:
        p = os.path.join(d, stem + ".ARW")
        if os.path.exists(p):
            return p
    return None


def build_curve(knots, ctrl, top, n=104):
    step = top / n
    out = np.empty(n, np.float64)
    for i in range(n):
        xx = i * step
        j = 9
        for t in range(9):
            if xx < knots[t + 1]:
                j = t
                break
        d = knots[j + 1] - knots[j]
        u = 0.0 if d == 0 else (xx - knots[j]) / d
        b = 3 * j
        v = ((1 - u) ** 3 * ctrl[b] + 3 * (1 - u) ** 2 * u * ctrl[b + 1]
             + 3 * u ** 2 * (1 - u) * ctrl[b + 2] + u ** 3 * ctrl[b + 3])
        out[i] = min(v, top)
    return out


def main():
    d = os.path.join(SCR, "vatrparam")
    print(f"{'图':<11} {'flag':>5} {'vatr[0x70]':>11} {'最大差':>10} {'RMS':>10}  说明")
    for f in sorted(os.listdir(d)):
        if not f.endswith(".bin"):
            continue
        stem = f[:-4]
        arw = find_arw(stem)
        if not arw:
            print(f"{stem:<11} 找不到 ARW")
            continue
        blob = open(os.path.join(d, f), "rb").read()
        eng = np.frombuffer(blob, "<f4", count=104, offset=0x7c4).astype(np.float64)
        top = struct.unpack_from("<f", blob, 0x70)[0]
        try:
            kb = read_sr2_tag(arw, 0x781B)
            cb = read_sr2_tag(arw, 0x781C)
        except Exception as e:
            print(f"{stem:<11} 读标签失败: {e}")
            continue
        knots = np.frombuffer(kb, "<u2", count=10).astype(np.float64) / 1024.0
        ctrl = np.frombuffer(cb, "<u2", count=28).astype(np.float64) / 1024.0
        flag = 0 if not knots.any() else 1
        ours = build_curve(knots, ctrl, top)
        dif = np.abs(ours - eng)
        note = "AUTO 用 RAW 曲线" if flag else "0x781b 全零 -> 回落 level=5(本式不适用)"
        print(f"{stem:<11} {flag:>5} {top:>11.5f} {dif.max():>10.6f} "
              f"{np.sqrt((dif ** 2).mean()):>10.6f}  {note}")


if __name__ == "__main__":
    main()
