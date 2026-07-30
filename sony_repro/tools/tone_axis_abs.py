"""把 tone_axis 的十六帧输出换一个指标重读:**绝对**色差幅度,而不是色差/亮度比值。

为什么要换 —— 见 notes/measured-chroma-gap.md §2.10。原指标是平坦 tile 里的
`(MAD(R-G)+MAD(B-G))/2 / MAD(亮度)`,分母是 llr **自己**的亮度细节。而 llr 的亮度
细节跟 Edit 并不相等(ISO 200 那张:Edit 0.739,llr 1.241,差 1.68 倍),于是同一份
色差被不同的分母除,比值就没法在两条流水线之间比。低 ISO 上两项都小,这个污染最重。

这里直接比分子:llr 的 MAD(R-G)、MAD(B-G) 对 Edit 的同一个量。同一张片、同一批
tile、同一个 detail 算子,只是不再除以各自的亮度。

**R-G 和 B-G 分开报**,不取平均 —— 平均会把「一路欠清、一路过清」抹平成「刚好」。

不重跑引擎,只解析已有的 tmp/tone16.txt(tone_axis.py 的逐档打印里本来就有绝对值)。
"""
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

TXT = Path("/home/jannchie/llr/tmp/tone16.txt")
AMT = Path("/home/jannchie/llr/tmp/tone16_amt.txt")
# 「  L4 e1e-04    亮度    2.547   (R-G)   0.642   (B-G)   0.221   色差/亮度  0.169」
ROW = re.compile(
    r"^\s{2}(\S.*?)\s+亮度\s+([\d.]+)\s+\(R-G\)\s+([\d.]+)\s+\(B-G\)\s+([\d.]+)"
)
HEAD = re.compile(r"^===\s+(\S+)\s+ISO\s+(\d+)")


def parse(path):
    """-> [(stem, iso, {档名: (亮度, R-G, B-G)})],顺序即文件顺序。"""
    frames, cur = [], None
    for line in path.read_text(encoding="utf-8").splitlines():
        h = HEAD.match(line)
        if h:
            cur = (h.group(1), int(h.group(2)), {})
            frames.append(cur)
            continue
        m = ROW.match(line)
        if m and cur is not None:
            # 只取带「亮度 ... (R-G)」的那批行;末尾的汇总行没有这个形状。
            cur[2][m.group(1).strip()] = tuple(float(m.group(i)) for i in (2, 3, 4))
    return frames


def summarise(vals, label):
    v = np.asarray(vals, float)
    lo, hi = float(v.min()), float(v.max())
    print(f"  {label:<10} 中位 {np.median(v):6.2f}   几何均 "
          f"{float(np.exp(np.mean(np.log(np.maximum(v, 1e-9))))):6.2f}"
          f"   最小 {lo:5.2f}   最大 {hi:5.2f}   跨度 {hi / max(lo, 1e-9):6.1f}")
    return v


SETTINGS = ("L4 e1e-04", "L4 e4e-04", "L5 e1e-04", "L5 e4e-04")


def all_settings(frames):
    """四档一起看:每帧哪一档的绝对色差最接近 Edit,那一档跟 ISO 有没有关系。

    零成本 —— tone16.txt 里四档都已经跑过了。若最优档随 ISO 单调移动,那就是
    「缺一个 ISO 项」的直接证据;若四档里最弱的那档在低 ISO 上仍然过清,说明
    扫过的窗口整个偏强,得往更弱的方向再开一档才谈得上拟合。
    """
    print("\n=== 四档 × 绝对色差(R-G 与 B-G 的几何均,llr/Edit)")
    print(f"  {'帧':<10} {'ISO':>5} │ " + " ".join(f"{s:>10}" for s in SETTINGS)
          + " │ 最优档")
    best_of = []
    for stem, iso, d in frames:
        if "Edit" not in d:
            continue
        _, er, eb = d["Edit"]
        cells, cols = [], []
        for s in SETTINGS:
            if s not in d:
                cells.append(float("nan"))
                cols.append(f"{'-':>10}")
                continue
            _, orr, ob = d[s]
            g = float(np.sqrt((orr / max(er, 1e-9)) * (ob / max(eb, 1e-9))))
            cells.append(g)
            cols.append(f"{g:10.2f}")
        k = int(np.nanargmin([abs(np.log(max(c, 1e-9))) for c in cells]))
        print(f"  {stem:<10} {iso:>5} │ " + " ".join(cols) + f" │ {SETTINGS[k]}")
        best_of.append((iso, k, cells[k]))

    a = np.asarray(best_of, float)
    print("\n  每档在全语料上的表现(越接近 1 越像):")
    for j, s in enumerate(SETTINGS):
        v = np.array([np.sqrt((d[s][1] / max(d["Edit"][1], 1e-9))
                              * (d[s][2] / max(d["Edit"][2], 1e-9)))
                      for _, _, d in frames if s in d and "Edit" in d])
        summarise(v, s)
    r = float(np.corrcoef(np.log(a[:, 0]), a[:, 1])[0, 1])
    print(f"\n  最优档序号 vs log(ISO) 相关 {r:+.3f}   "
          f"(档序 0..3 = 由弱到强的顺序: L4e1, L5e1, L4e4, L5e4 并非严格单调,"
          f"仅作趋势看)")
    n_weakest = int((a[:, 1] == 0).sum())
    print(f"  {n_weakest}/{len(a)} 帧选中最弱档 L4 e1e-04"
          + ("  —— 窗口整体偏强,需要往更弱开档" if n_weakest > len(a) / 2 else ""))


def amount_axis():
    """在 amount 轴上找每帧的最优点,再看它跟 ISO 的关系。

    指标是**绝对**色差:R-G 与 B-G 各自对 Edit 的比,取几何均(两路分开量再合,
    不先平均 —— 平均会把「一路欠清一路过清」抹平)。目标是 1.0。

    每帧的最优 amount 用相邻两档在 log 域线性插值求,不是取网格上最近的那一档 ——
    网格只有六档,直接取最近点会把 ISO 趋势量化成阶梯。
    """
    frames = parse(AMT)
    if not frames:
        raise SystemExit(f"没从 {AMT} 解析到任何帧")
    keys = sorted((k for k in frames[0][2] if k.startswith("a0") or k == "a1.00"),
                  key=lambda s: float(s[1:]))
    amts = [float(k[1:]) for k in keys]
    print(f"\n=== amount 轴({len(frames)} 帧,levels=4 eps=1e-04 subsample=8)")
    print(f"  {'帧':<10} {'ISO':>5} │ " + " ".join(f"{a:>6.2f}" for a in amts)
          + " │ 最优 amount")
    rows = []
    for stem, iso, d in frames:
        if "Edit" not in d or any(k not in d for k in keys):
            continue
        _, er, eb = d["Edit"]
        g = []
        for k in keys:
            _, orr, ob = d[k]
            g.append(float(np.sqrt((orr / max(er, 1e-9)) * (ob / max(eb, 1e-9)))))
        lg = np.log(np.maximum(g, 1e-9))
        # 在 log(比值) 穿过 0 的地方插值。比值随 amount 单调下降,所以从强端往弱端
        # 找第一次由 >0 变 <0 的区间。
        best = None
        for i in range(len(amts) - 1):
            if (lg[i] >= 0 >= lg[i + 1]) or (lg[i] <= 0 <= lg[i + 1]):
                t = lg[i] / (lg[i] - lg[i + 1]) if lg[i] != lg[i + 1] else 0.0
                best = amts[i] + t * (amts[i + 1] - amts[i])
                break
        if best is None:
            # 整条曲线都在一侧:最优点在网格外,取最接近 1 的端点并标出来。
            j = int(np.argmin(np.abs(lg)))
            best = amts[j]
            mark = "↓外" if lg[j] > 0 else "↑外"
        else:
            mark = ""
        print(f"  {stem:<10} {iso:>5} │ " + " ".join(f"{v:6.2f}" for v in g)
              + f" │ {best:6.2f} {mark}")
        rows.append((iso, best, 1 if mark else 0))

    a = np.asarray(rows, float)
    inside = a[a[:, 2] == 0]
    print(f"\n  {len(inside)}/{len(a)} 帧的最优点落在 amount 网格**内**"
          + ("" if len(inside) == len(a) else "  —— 其余标了 ↑外/↓外"))
    print(f"  最优 amount   中位 {np.median(a[:, 1]):.2f}   "
          f"最小 {a[:, 1].min():.2f}   最大 {a[:, 1].max():.2f}")
    r = float(np.corrcoef(np.log(a[:, 0]), a[:, 1])[0, 1])
    print(f"  最优 amount vs log(ISO) 相关 {r:+.3f}")
    # 线性拟合 amount = k*log2(ISO/100) + b,这是引擎里 RawNR/Spica 那类
    # 「按 ISO 分段插值」最简单的连续版本。
    x = np.log2(a[:, 0] / 100.0)
    k, b = np.polyfit(x, a[:, 1], 1)
    pred = np.clip(k * x + b, 0.0, 1.0)
    resid = a[:, 1] - pred
    print(f"  拟合 amount = {k:+.4f}·log2(ISO/100) {b:+.4f}"
          f"   残差 RMS {float(np.sqrt(np.mean(resid ** 2))):.3f}"
          f"   (常数模型 {float(np.std(a[:, 1])):.3f})")
    print("  按档位查表:", "  ".join(
        f"ISO{int(100 * 2 ** e)}→{np.clip(k * e + b, 0, 1):.2f}" for e in range(0, 6)))

    # 最优 amount 的跨度只有 1.5 倍,但比值对 amount 很陡(0.8→1.0 就动 2 倍),
    # 所以「参数通用」不等于「结果一致」。要决定上线取哪个常数,得直接量**结果**。
    def curve(d, ev):
        return [float(np.sqrt((d[k][1] / max(ev[0], 1e-9)) * (d[k][2] / max(ev[1], 1e-9))))
                for k in keys]

    def ratio_at(d, ev, amt):
        """在 amount 网格上按 log 域线性插值出该帧的比值。"""
        g = np.log(np.maximum(curve(d, ev), 1e-9))
        return float(np.exp(np.interp(amt, amts, g)))

    print("\n  候选常数 amount 在全语料上的**结果**(llr/Edit,越接近 1 越像):")
    print(f"  {'amount':>7} │ {'几何均':>7} {'中位':>7} {'最小':>6} {'最大':>6}"
          f" {'跨度':>6} │ {'|log| 均':>8}")
    best_c, best_s = None, None
    for amt in [round(0.60 + 0.05 * i, 2) for i in range(9)]:
        v = np.array([ratio_at(d, (d["Edit"][1], d["Edit"][2]), amt)
                      for _, _, d in frames if "Edit" in d])
        score = float(np.mean(np.abs(np.log(v))))
        if best_s is None or score < best_s:
            best_c, best_s = amt, score
        print(f"  {amt:7.2f} │ {float(np.exp(np.mean(np.log(v)))):7.2f}"
              f" {float(np.median(v)):7.2f} {v.min():6.2f} {v.max():6.2f}"
              f" {v.max() / max(v.min(), 1e-9):6.1f} │ {score:8.3f}")
    print(f"\n  按 |log(比值)| 的均值,最优常数 amount = {best_c:.2f}(得分 {best_s:.3f})")

    # 同一把尺子量 ISO 线性模型,才知道那一项值不值得加。
    v_iso = np.array([ratio_at(d, (d["Edit"][1], d["Edit"][2]),
                               float(np.clip(k * np.log2(iso / 100.0) + b, 0, 1)))
                      for _, iso, d in frames if "Edit" in d])
    s_iso = float(np.mean(np.abs(np.log(v_iso))))
    print(f"  同尺子下的 ISO 线性模型得分 {s_iso:.3f}"
          f"   —— 比最优常数{'好' if s_iso < best_s else '差'} {abs(s_iso - best_s):.3f}")
    return 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "amount":
        return amount_axis()
    frames = parse(TXT)
    if not frames:
        raise SystemExit(f"没从 {TXT} 解析到任何帧")
    setting = sys.argv[1] if len(sys.argv) > 1 else "L4 e1e-04"

    print(f"共 {len(frames)} 帧,档位 = {setting}\n")
    print(f"  {'帧':<10} {'ISO':>5} │ {'亮度 Edit':>9} {'llr':>7} {'llr/Edit':>8} │"
          f" {'R-G 比':>7} {'B-G 比':>7} │ {'旧(比值)':>9}")
    rows = []
    for stem, iso, d in frames:
        if "Edit" not in d or setting not in d:
            print(f"  {stem:<10} {iso:>5} │ 缺档")
            continue
        ey, er, eb = d["Edit"]
        oy, orr, ob = d[setting]
        rr, rb = orr / max(er, 1e-9), ob / max(eb, 1e-9)
        # 旧指标:各自除以各自的亮度,再比
        old = ((orr + ob) / 2 / max(oy, 1e-9)) / max((er + eb) / 2 / max(ey, 1e-9), 1e-9)
        print(f"  {stem:<10} {iso:>5} │ {ey:9.3f} {oy:7.3f} {oy / max(ey, 1e-9):8.2f} │"
              f" {rr:7.2f} {rb:7.2f} │ {old:9.2f}")
        rows.append((iso, oy / max(ey, 1e-9), rr, rb, old))

    a = np.asarray(rows, float)
    print("\n  llr/Edit,越接近 1 越像:")
    summarise(a[:, 2], "R-G 绝对")
    summarise(a[:, 3], "B-G 绝对")
    summarise(a[:, 4], "旧比值")
    print("\n  分母本身(亮度细节 llr/Edit) —— 这一列不是 1 就说明旧比值不可比:")
    summarise(a[:, 1], "亮度")

    li = np.log(a[:, 0])
    for j, lab in ((2, "R-G 绝对"), (3, "B-G 绝对"), (4, "旧比值"), (1, "亮度")):
        r = float(np.corrcoef(li, np.log(np.maximum(a[:, j], 1e-9)))[0, 1])
        print(f"  {lab:<10} vs ISO 的 log-log 相关 {r:+.3f}")

    all_settings(frames)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
