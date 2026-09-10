r"""手动档「量」滑块 → RawNR 强度。

各档 tile 的外扩不同(量=100 时更大),所以按**绝对坐标**对齐:像素 (0,0) 的整幅坐标 = 位置(meta 0x48/0x4c)
− 内矩形起点(meta 0x30/0x34)。在重叠区里:
  1. (out_a − in)/(out_b − in) 的中位比 —— 若「量」只是写回混合比 s,该比值 = s_a/s_b,且与像素无关;
  2. 直接检验 out(量) == trunc(s·out(100) + (1−s)·in),s 试 量/100、0.4+0.6·量/100、0.5+0.5·量/100。
'auto' 读 tmp/rawnr_export_DSC03036-cap16.npz(自动档,ISO 2000 → 强度 1.0)。

    bash -lc "cd ~/llr/apps/worker && uv run python ../../sony_repro/tools/rawnr_amount_check.py auto m0-5 m50-5 m100-5 m50-0 m50-10"
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np  # noqa: E402

from llr_worker.sony.rawnr_simd import apply_strength  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = os.path.join(HERE, "..", "..", "tmp")


def load(suf):
    """{(abs_x0, abs_y0, w, h): (in, out)},坐标是像素 (0,0) 在整幅里的位置。"""
    tiles = {}
    if suf == "auto":
        z = np.load(os.path.join(TMP, "rawnr_export_DSC03036-cap16.npz"))
        for i in range(3):
            if f"out{i}" not in z:
                continue
            rect, pos = z[f"meta{i}_rect"], z[f"meta{i}_pos"]
            a = z[f"in{i}"]
            tiles[(int(pos[0]) - int(rect[0]), int(pos[1]) - int(rect[1]), a.shape[1], a.shape[0])] = (a, z[f"out{i}"])
        return tiles
    z = np.load(os.path.join(HERE, f"tiles_RawNRSIMD_export_{suf}.npz"))
    for t in ("t0", "t1", "t2"):
        if f"{t}_out" in z:
            m = z[f"{t}_meta"]
            a = z[f"{t}_in"][..., 0]
            tiles[(int(m[0x48 // 4]) - int(m[0x30 // 4]), int(m[0x4c // 4]) - int(m[0x34 // 4]), a.shape[1], a.shape[0])] = (a, z[f"{t}_out"][..., 0])
    return tiles


def overlap(k1, k2, margin=48):
    x1, y1, w1, h1 = k1
    x2, y2, w2, h2 = k2
    X0, Y0 = max(x1, x2) + margin, max(y1, y2) + margin
    X1, Y1 = min(x1 + w1, x2 + w2) - margin, min(y1 + h1, y2 + h2) - margin
    if X1 - X0 < 200 or Y1 - Y0 < 200:
        return None
    return (slice(Y0 - y1, Y1 - y1), slice(X0 - x1, X1 - x1)), (slice(Y0 - y2, Y1 - y2), slice(X0 - x2, X1 - x2))


def amount_of(suf):
    return 1.0 if suf == "auto" else int(suf.split("-")[0][1:]) / 100.0


def main():
    sufs = sys.argv[1:]
    data = {s: load(s) for s in sufs}
    for suf in sufs:
        for k, (a, b) in data[suf].items():
            print(f"  {suf:<8} abs {k}  out==in {np.mean(a == b) * 100:6.2f}%")
    print("\n重叠区里 (out_a − in)/(out_b − in) 的中位比(|out_b − in| ≥ 8):")
    for i, sa in enumerate(sufs):
        for sb in sufs[i + 1:]:
            for ka, (a, oa) in data[sa].items():
                for kb, (b, ob) in data[sb].items():
                    ov = overlap(ka, kb)
                    if ov is None:
                        continue
                    r1, r2 = ov
                    A, OA, B, OB = [v[r].astype(np.float32) for v, r in ((a, r1), (oa, r1), (b, r2), (ob, r2))]
                    if not np.array_equal(A, B):
                        print(f"  {sa} vs {sb}: 重叠 {A.shape} 但入口不同(相同像素 {np.mean(A == B) * 100:.1f}%),对齐不对")
                        continue
                    da, db = OA - A, OB - B
                    m = np.abs(db) >= 8
                    if m.sum() < 100:
                        continue
                    q = da[m] / db[m]
                    print(f"  {sa:<8} / {sb:<8} 重叠 {A.shape}  中位比 {np.median(q):.4f}  p10/p90 {np.percentile(q, 10):.3f}/{np.percentile(q, 90):.3f}  out_a==out_b {np.mean(OA == OB) * 100:.2f}%")
    print("\n写回检验 out(量) == trunc(s·out(ref) + (1−s)·in),ref = 量 100 或 auto:")
    refs = [s for s in sufs if amount_of(s) == 1.0]
    for suf in sufs:
        if amount_of(suf) == 1.0:
            continue
        v = amount_of(suf)
        for ref in refs:
            for ka, (a, oa) in data[suf].items():
                for kb, (b, ob) in data[ref].items():
                    ov = overlap(ka, kb)
                    if ov is None:
                        continue
                    r1, r2 = ov
                    A, OA, B, OB = [x[r].astype(np.float32) for x, r in ((a, r1), (oa, r1), (b, r2), (ob, r2))]
                    if not np.array_equal(A, B):
                        continue
                    for s_try in sorted({v, 0.4 + 0.6 * v, 0.5 + 0.5 * v}):
                        pred = apply_strength(OB, A, s_try)
                        d = np.abs(pred - OA)
                        print(f"  {suf:<8} vs {ref:<8} s={s_try:.2f}: 逐位 {np.mean(pred == OA) * 100:8.4f}%  max|d| {d.max():4.0f}  mean|d| {d.mean():.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
