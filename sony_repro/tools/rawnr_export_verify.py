r"""全分辨率闭环:导出时抓到的 RawNR tile(入口/出口),只用 ARW 标签驱动生产代码重算。

`export_rawnr_capture.py` 抓的是导出路径上真实的 1024 tile(含 halo)。这里不喂引擎的
任何中间量:阈值曲线、细节增益/限幅、ISO 强度全部来自 `sony/rawnr.py` 读标签,走的是
`denoise.get_denoiser("sony")` 这条生产入口,然后与引擎写回的出口平面逐位比。

    bash run_py.sh rawnr_export_verify.py <npz> <ARW>
"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np  # noqa: E402

from llr_worker.denoise import get_denoiser, pack_bayer, unpack_bayer  # noqa: E402
from llr_worker.sony.rawnr import detail_restore, noise_model  # noqa: E402
from llr_worker.sony.rawnr_simd import iso_strength  # noqa: E402

BLACK, WHITE = 512.0, 16383.0
CFA_BY_PHASE = {  # 平面 (0,0),(0,1),(1,0),(1,1) 的颜色,按 tile 原点相对 RGGB 的奇偶
    (0, 0): ["R", "G", "G", "B"], (0, 1): ["G", "R", "B", "G"],
    (1, 0): ["G", "B", "R", "G"], (1, 1): ["B", "G", "G", "R"],
}


def detect_phase(m):
    """两个绿相位均值最接近;R 与 B 里 R 通常更低(这台机器 WB 下)。只用来选 cfa 表。"""
    means = {(i, j): float(m[i::2, j::2].mean()) for i in (0, 1) for j in (0, 1)}
    best = None
    for ph, order in CFA_BY_PHASE.items():
        g = [k for k, c in zip([(0, 0), (0, 1), (1, 0), (1, 1)], order) if c == "G"]
        d = abs(means[g[0]] - means[g[1]])
        if best is None or d < best[0]:
            best = (d, ph)
    return best[1], means


def main() -> int:
    z = np.load(sys.argv[1])
    arw = sys.argv[2]
    curve = noise_model(arw)
    restore = detail_restore(arw)
    iso = float(sys.argv[3]) if len(sys.argv) > 3 else 2000.0
    s = iso_strength(iso)
    print(f"阈值曲线 {curve}  细节 {restore}  ISO {iso} → strength {s}")
    den = get_denoiser("sony")
    detail = None if restore is None else (restore.fraction, restore.limit_in_thresholds(curve))
    black = np.array([BLACK] * 4, np.float32)
    span = WHITE - BLACK
    for k in range(3):
        if f"in{k}" not in z:
            continue
        mi, mo = z[f"in{k}"], z[f"out{k}"]
        rect, pos = z.get(f"meta{k}_rect"), z.get(f"meta{k}_pos")
        h, w = mi.shape[0] & ~1, mi.shape[1] & ~1
        mi, mo = mi[:h, :w], mo[:h, :w]
        ph, means = detect_phase(mi.astype(np.float64))
        cfa = CFA_BY_PHASE[ph]
        planes = ((pack_bayer(mi).astype(np.float32) - black) / span).astype(np.float32)
        out = den(planes, None, cfa, curve, detail, sensor_levels=(black, WHITE), strength=s)
        got = unpack_bayer(np.rint(out * span + black).astype(np.int64))
        t = 24
        d = np.abs(got[t:-t, t:-t] - mo[t:-t, t:-t].astype(np.int64))
        changed = np.mean(mi[t:-t, t:-t] != mo[t:-t, t:-t])
        print(f"tile{k} {w}x{h} pos {pos} rect {rect} 相位 {ph} cfa {cfa} 均值 {[round(v) for v in means.values()]}")
        print(f"   引擎改动像素 {changed * 100:.2f}%   我们 vs 引擎出口:逐位相同 {np.mean(d == 0) * 100:.4f}%  max|d| {d.max()}  n>0 {(d > 0).sum()}/{d.size}")
        for phk, (i, j) in {"rb0": (0, 0), "g0": (0, 1), "g1": (1, 0), "rb1": (1, 1)}.items():
            dd = d[i::2, j::2]
            print(f"      {phk}: 逐位 {np.mean(dd == 0) * 100:.4f}%  max {dd.max()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
