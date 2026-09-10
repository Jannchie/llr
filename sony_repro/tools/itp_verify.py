r"""拿 `itp_dump_probe.py` 的 dump 逐级验证 `llr_worker.sony.itp`。

每一级都用引擎自己的输入喂,隔离误差;最后再从马赛克端到端跑一遍。判据看「最大差」:
逐位相同率会被 float32 累加顺序拉低(两个九抽头 FIR 与其下游),那不是算子错。

用法(Windows 或 WSL 都行,只要能 import llr_worker)::

    python itp_verify.py <itp_dump_*.npz ...>
"""
import json
import sys

import numpy as np

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'apps', 'worker', 'src'))
from llr_worker.sony import itp as R

F = np.float32


def load(path):
    z = np.load(path)
    meta = json.loads(str(z["meta"]))
    P = {k: z[k] for k in z.files if k != "meta"}
    return meta, P


def find(P, prefix):
    for k in P:
        if k.startswith(prefix):
            return P[k]
    raise KeyError(prefix)


def cmp(name, got, want, rect, inner=8):
    x0, y0, x1, y1 = rect
    a = got[y0 + inner:y1 - inner, x0 + inner:x1 - inner].astype(np.float64)
    b = want[y0 + inner:y1 - inner, x0 + inner:x1 - inner].astype(np.float64)
    d = np.abs(a - b)
    rel = d.max() / max(np.abs(b).max(), 1e-9)
    flag = "✅" if rel < 1e-5 else ("⚠️" if rel < 1e-3 else "❌")
    print(f"  {flag} {name:<34} max|d|={d.max():.4g}  rel={rel:.2e}  exact={np.mean(d == 0) * 100:6.2f}%")
    return d.max()


def main(paths):
    for path in paths:
        meta, P = load(path)
        rect = meta["exec"][0]["rect"]
        print(f"\n== {path}  rect={rect}")
        mos = P["mosaic"]
        W_eng = find(P, "b002_orch_110_enter_a3")
        # 增益:从 dump 反解(WB 标签的公式另在 wb_gains 里,验证见 itp_dump 分析)
        H, Wd = W_eng.shape
        gains = []
        for i, j in ((0, 0), (0, 1), (1, 0), (1, 1)):
            m = mos[20:H - 20, 20:Wd - 20][i::2, j::2].astype(np.float64).ravel() - 512.0
            w = W_eng[20:H - 20, 20:Wd - 20][i::2, j::2].astype(np.float64).ravel()
            gains.append(float(np.dot(m, w) / np.dot(m, m)))
        gains = np.array([np.round(g * 2048) / 2048 for g in gains], F)
        print("  gains x2048:", (gains * 2048).astype(int))
        W = R.convert(mos, gains, 512.0)
        cmp("convert W", W, W_eng, rect, inner=0)
        W = W_eng.astype(F)  # 后面每级都用引擎自己的输入,隔离误差

        cmp("lp_h", R.lp_h(W), find(P, "b020_vt150_lpH_leave"), rect)
        cmp("lp_v", R.lp_v(W), find(P, "b021_vt158_lpV_leave"), rect)
        cmp("mid_h", R.mid_h(W), find(P, "b022_vt160_midH_leave"), rect)
        cmp("mid_v", R.mid_v(W), find(P, "b023_vt168_midV_leave"), rect)

        green = R.green_mask(H, Wd)
        cmp("cost_h |v|*green", R.cost_h(W, green), find(P, "b003_vt68_costH_leave_a2"), rect)
        cmp("cost_v |v|*green", R.cost_v(W, green), find(P, "b005_vt70_costV_leave_a2"), rect)

        aH = find(P, "b004_vta0_aggr_leave_a1").astype(F)
        aV = find(P, "b006_vta0_aggr_leave_a1").astype(F)
        cmp("aggregate H", R.aggregate(find(P, "b003_vt68_costH_leave_a2").astype(F), rect), aH, rect)
        cmp("aggregate V", R.aggregate(find(P, "b005_vt70_costV_leave_a2").astype(F), rect), aV, rect)
        e1, e2 = R.green_candidates(W, green)
        cmp("green cand H (e1)", e1, find(P, "b012_vte8_leave_a1"), rect)
        cmp("green cand V (e2)", e2, find(P, "b012_vte8_leave_a2"), rect)

        m1 = find(P, "b007_vtb0_crit_leave_a1").astype(F)
        m2 = find(P, "b008_vtb0_crit_leave_a1").astype(F)
        m3 = find(P, "b009_vtb8_leave_a1").astype(F)
        cmp("m1 == m2", m1, m2, rect)
        d1 = find(P, "b013_vtd0_leave_a1").astype(F)
        d2 = find(P, "b013_vtd0_leave_a2").astype(F)
        f1 = find(P, "b011_vtf0_leave_a1").astype(F)
        f2 = find(P, "b011_vtf0_leave_a2").astype(F)
        cmp("d1 = 0.5(f1+e1) (vtd0)", (F(0.5) * (f1 + e1)).astype(F), d1, rect)
        cmp("d2 = 0.5(f2+e2) (vtd0)", (F(0.5) * (f2 + e2)).astype(F), d2, rect)
        cmp("blend (vtf8)", R.lerp(d1, d2, m1), find(P, "b014_vtf8_blend_leave_a1"), rect)

        u = find(P, "b015_vtc0_enter_a2").astype(F)
        cmp("u = W*green", np.where(R.green_mask(H, Wd), W, F(0)), u, rect)
        cmp("malvar", R.malvar(u, green), find(P, "b015_vtc0_leave_a1"), rect)

        an = find(P, "b017_aniso_leave_a1").astype(F)
        cmp("anisotropy", R.anisotropy(u, rect), an, rect, inner=12)
        bl = find(P, "b014_vtf8_blend_leave_a1").astype(F)
        mv = find(P, "b015_vtc0_leave_a1").astype(F)
        base_eng = find(P, "b018_vt108_leave_a1").astype(F)
        cmp("base (vt108)", R.lerp(bl, mv, np.maximum(m3, an)), base_eng, rect)

        t = [find(P, f"b024_vt178_leave_a{k}").astype(F) for k in (1, 2, 3, 4)]
        lpH = find(P, "b020_vt150_lpH_leave").astype(F); lpV = find(P, "b021_vt158_lpV_leave").astype(F)
        mdH = find(P, "b022_vt160_midH_leave").astype(F); mdV = find(P, "b023_vt168_midV_leave").astype(F)
        tt = R.colour_diff_fields(lpH, lpV, mdH, mdV, rect)
        for k in range(4):
            cmp(f"vt178 t{k+1}", tt[k], t[k], rect)
        bD = find(P, "b025_vt180_leave_a1").astype(F)
        b50 = find(P, "b026_vt180_leave_a1").astype(F)
        cmp("bD (vt180)", R.lerp(t[0], t[1], m2), bD, rect)
        cmp("b50 (vt180)", R.lerp(t[2], t[3], m2), b50, rect)

        cmp("m1 (crit)", R.direction_weight(aH, aV), m1, rect)
        cmp("m3 (b8)", R.confidence(aH, aV), m3, rect)
        ff1, ff2 = R.base_candidates(W, green)
        cmp("f1 (vtf0)", ff1, f1, rect)
        cmp("f2 (vtf0)", ff2, f2, rect)
        o0, o1, o2 = R.pack(base_eng, bD, b50)
        cmp("out0", o0, P["zz_out0"], rect, inner=0)
        cmp("out1", o1, P["zz_out1"], rect, inner=0)
        cmp("out2", o2, P["zz_out2"], rect, inner=0)
        # 端到端:只给马赛克 + 增益
        E0, E1, E2 = R.itp_tile(mos, gains, 512.0, tuple(rect))
        for k, (got, want) in enumerate(((E0, P["zz_out0"]), (E1, P["zz_out1"]), (E2, P["zz_out2"]))):
            cmp(f"E2E out{k}", got, want, rect, inner=12)


if __name__ == "__main__":
    main(sys.argv[1:])
