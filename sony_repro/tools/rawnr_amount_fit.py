r"""手动档「量」→ RawNR 写回强度 s(量):把各档 RawNR 出口对同位置的 量=100 出口做
    out(量) ≈ trunc(s·out(100) + (1−s)·in)
求 s 的中位估计,并报告该 s 下的逐位率。tile 外扩随量变,先在 ±40 px 内暴力搜对齐。

    bash -lc "cd ~/llr/apps/worker && uv run python ../../sony_repro/tools/rawnr_amount_fit.py m0-5 m25-5 m50-5 m75-5 m100-5 auto"
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rawnr_amount_check import amount_of, load  # noqa: E402

from llr_worker.sony.rawnr_simd import apply_strength  # noqa: E402


def find_shift(a, b):
    ca = a[a.shape[0] // 2 - 128:a.shape[0] // 2 + 128, a.shape[1] // 2 - 128:a.shape[1] // 2 + 128]
    best = None
    for dy in range(-40, 41):
        for dx in range(-40, 41):
            y0, x0 = a.shape[0] // 2 - 128 + dy, a.shape[1] // 2 - 128 + dx
            if y0 < 0 or x0 < 0 or y0 + 256 > b.shape[0] or x0 + 256 > b.shape[1]:
                continue
            eq = np.mean(ca == b[y0:y0 + 256, x0:x0 + 256])
            if best is None or eq > best[0]:
                best = (eq, dy, dx)
    return best


def aligned(a, b, dy, dx):
    """把 b 裁成和 a 同形、同坐标(b[y+dy, x+dx] ↔ a[y, x]),两边都去掉 48 px 边。"""
    h, w = a.shape
    y0, x0 = max(0, -dy), max(0, -dx)
    y1, x1 = min(h, b.shape[0] - dy), min(w, b.shape[1] - dx)
    ra = (slice(y0 + 48, y1 - 48), slice(x0 + 48, x1 - 48))
    rb = (slice(y0 + dy + 48, y1 + dy - 48), slice(x0 + dx + 48, x1 + dx - 48))
    return ra, rb


def main():
    sufs = [a for a in sys.argv[1:] if not a.startswith("--")]
    data = {s: load(s) for s in sufs}
    refs = [s for s in sufs if s != "auto" and amount_of(s) == 1.0]
    if "--ref" in sys.argv:          # 例如 --ref m50-5:以自动档(=手动 50)为参照量相对强度
        refs = [sys.argv[sys.argv.index("--ref") + 1]]
    for ref in refs:
        print(f"参照 {ref}")
        for suf in sufs:
            if suf == ref:
                continue
            for ka, (a, oa) in data[suf].items():
                for kb, (b, ob) in data[ref].items():
                    if abs(ka[0] - kb[0]) > 64 or abs(ka[1] - kb[1]) > 64:
                        continue
                    sh = find_shift(a, b)
                    if sh is None or sh[0] < 0.999:
                        print(f"  {suf} {ka[:2]} vs {kb[:2]}: 对不齐 ({sh})")
                        continue
                    ra, rb = aligned(a, b, sh[1], sh[2])
                    A, OA, B, OB = [v[r].astype(np.float32) for v, r in ((a, ra), (oa, ra), (b, rb), (ob, rb))]
                    assert np.array_equal(A, B)
                    dF = OB - A
                    m = np.abs(dF) >= 16
                    q = (OA - A)[m] / dF[m]
                    s_med = float(np.median(q))
                    line = f"  {suf:<8} {str(ka[:2]):<14} shift {sh[1:]}: s 中位 {s_med:.4f} (p25/p75 {np.percentile(q, 25):.3f}/{np.percentile(q, 75):.3f})  out==ref {np.mean(OA == OB) * 100:.2f}%"
                    cands = sorted({round(s_med, 2), amount_of(suf), 1 - (1 - amount_of(suf)) ** 2, amount_of(suf) ** 0.5})
                    for st in cands:
                        pred = apply_strength(OB, A, st)
                        line += f"\n        s={st:.3f}: 逐位 {np.mean(pred == OA) * 100:7.3f}%  mean|d| {np.abs(pred - OA).mean():.3f}"
                    print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
