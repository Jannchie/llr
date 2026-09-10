r"""「色彩降噪」滑块 → Marble 色差清理强度:按 tile 位置配对各档捕获,量细节带残留,并给每档拟合
llr chromanr 的 amount(levels 5 / eps 1e-2,即现行默认)。

    bash -lc "cd ~/llr/apps/worker && uv run python ../../sony_repro/tools/marble_slider_fit.py auto m50-0 m50-2 m50-5 m50-8 m50-10 [--fit]"

每个 suffix 读 tiles_SIMDMarble_export_<suffix>.npz('auto' → tiles_SIMDMarble_export.npz,
'nroff' → ..._nroff.npz)。tile 按 meta[0x48..0x4c](整幅位置)配对。

输出两张表:
  1. 每个位置 × 每档:细节带残留比 fine(out)/fine(in)(R−G / B−G),以及 out 的细节带 MAD 绝对值
     (引擎自身噪声底约 6.6 / 9.1,到底了就分不出档);
  2. --fit:每档对 amount ∈ {0,0.1,…,1.0} 的残差 MAD(fine / all,R−G+B−G 平均),取最小者。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np  # noqa: E402

from llr_worker.sony.chromanr import apply_chroma_nr  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ALIAS = {"auto": "tiles_SIMDMarble_export.npz", "nroff": "tiles_SIMDMarble_export_nroff.npz"}


def hp(x):
    k = np.zeros_like(x)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            k += np.roll(np.roll(x, dy, 0), dx, 1)
    return x - k / 9


def mad(v):
    return float(np.median(np.abs(v - np.median(v))) * 1.4826)


def load(suf):
    z = np.load(os.path.join(HERE, ALIAS.get(suf, f"tiles_SIMDMarble_export_{suf}.npz")))
    tiles = {}
    for t in ("t0", "t1", "t2"):
        if f"{t}_out" not in z:
            continue
        m = z[f"{t}_meta"]
        x0, y0, x1, y1 = m[0x30 // 4:0x40 // 4]
        s = (slice(y0 + 16, y1 - 16), slice(x0 + 16, x1 - 16))
        tiles[(int(m[0x48 // 4]), int(m[0x4c // 4]))] = (z[f"{t}_in"].astype(np.float32), z[f"{t}_out"].astype(np.float32), s)
    return tiles


def fine(a, s):
    return [mad(hp(a[..., c] - a[..., 1])[s]) for c in (0, 2)]


def main():
    sufs = [a for a in sys.argv[1:] if not a.startswith("--")]
    data = {suf: load(suf) for suf in sufs}
    positions = sorted(set().union(*[set(d) for d in data.values()]))
    print("表 1:细节带残留比 fine(out)/fine(in)  [R−G, B−G],括号里是 out 的细节带 MAD 绝对值")
    print(f"{'位置':<14}" + "".join(f"{s:>24}" for s in sufs))
    for pos in positions:
        row = f"{str(pos):<14}"
        for suf in sufs:
            if pos not in data[suf]:
                row += f"{'-':>24}"
                continue
            a, b, s = data[suf][pos]
            fi, fo = fine(a, s), fine(b, s)
            row += f"  {fo[0] / fi[0]:.3f}/{fo[1] / fi[1]:.3f} ({fo[0]:4.1f}/{fo[1]:4.1f})"
        print(row)
    if "--grid" in sys.argv:
        # levels × amount 一起扫(eps 1e-2 sub 8):低档位像是半径在变,不是混合比。
        print("\n表 3:levels × amount 网格,格子里是 残差 fine/all(两色差平均);每档标出最小 all")
        levels = [1, 2, 3, 4, 5, 6]
        amounts = [0.7, 0.8, 0.9, 1.0]
        for suf in sufs:
            best = None
            print(f"{suf}")
            for lv in levels:
                cells = []
                for amt in amounts:
                    acc = np.zeros(2)
                    n = 0
                    for pos, (a, b, s) in data[suf].items():
                        ours = apply_chroma_nr(a / 16383.0, levels=lv, guide=True, eps=1e-2, subsample=8, amount=amt) * 16383.0
                        for c in (0, 2):
                            do = ours[..., c] - ours[..., 1]
                            de = b[..., c] - b[..., 1]
                            acc += [mad((hp(do) - hp(de))[s]), mad((do - de)[s])]
                            n += 1
                    acc /= max(n, 1)
                    cells.append(f"a{amt:.1f}:{acc[0]:5.1f}/{acc[1]:5.1f}")
                    if best is None or acc[1] < best[2]:
                        best = (lv, amt, acc[1], acc[0])
                print(f"   L{lv}  " + "  ".join(cells))
            print(f"   → best L{best[0]} a{best[1]:.1f}  all {best[2]:.1f} fine {best[3]:.1f}")
        return 0
    if "--fit" not in sys.argv:
        return 0
    print("\n表 2:对引擎出口的残差 MAD(fine / all,两色差平均)按 amount 扫,levels 5 eps 1e-2 sub 8")
    amounts = [round(0.1 * i, 1) for i in range(11)]
    for suf in sufs:
        best = None
        line = []
        for amt in amounts:
            acc = np.zeros(2)
            n = 0
            for pos, (a, b, s) in data[suf].items():
                ours = a if amt == 0 else apply_chroma_nr(a / 16383.0, levels=5, guide=True, eps=1e-2, subsample=8, amount=amt) * 16383.0
                for c in (0, 2):
                    do = ours[..., c] - ours[..., 1]
                    de = b[..., c] - b[..., 1]
                    acc += [mad((hp(do) - hp(de))[s]), mad((do - de)[s])]
                    n += 1
            acc /= max(n, 1)
            line.append(f"{amt:.1f}:{acc[0]:5.1f}/{acc[1]:5.1f}")
            if best is None or acc[1] < best[1]:
                best = (amt, acc[1], acc[0])
        print(f"{suf:<10} best amount {best[0]:.1f} (all {best[1]:.1f}, fine {best[2]:.1f})")
        print("           " + "  ".join(line))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
