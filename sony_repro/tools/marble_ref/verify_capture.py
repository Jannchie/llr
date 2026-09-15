r"""产品版 sony/marble.py 逐 helper 对任意一份 export_marble_capture.py 的捕获(因子 4 或 8 自动识别)。

    cd apps/worker && uv run python ../../sony_repro/tools/marble_ref/verify_capture.py <marble_export_*.npz> [--calib sr2|ctx]

每一级都吃**引擎自己的入口**(不是我们上一级的出口),所以报的是该 helper 本身是否逐位;
最后再从 tile 入口 RGB 一路算到出口 RGB(亮度用引擎 Clarity 之后的平面),报整条链。
标定默认取 cnr2 入口 ctx 里的值(--calib ctx);--calib sr2 时从 npz 里的 calib 块按 SR2 标签的同一布局
(calib+0x109c..+0x10c4)读,两者应一致。
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "worker" / "src"))
from llr_worker.sony import marble as M  # noqa: E402


def i32(buf, off):
    return int(np.frombuffer(np.asarray(buf, np.uint8).tobytes(), "<i4")[off // 4])


def i16(buf, off):
    return int(np.frombuffer(np.asarray(buf, np.uint8).tobytes(), "<i2")[off // 2])


def report(name, got, exp, region=None):
    g = np.asarray(got).astype(np.int64)
    e = np.asarray(exp).astype(np.int64)
    if region is not None:
        g, e = g[region], e[region]
    d = np.abs(g - e)
    bad = int((d > 0).sum())
    print(f"  {name:<28} {100.0 * (d.size - bad) / d.size:9.4f}% exact  mismatches {bad}/{d.size}  max|d| {int(d.max())}")
    return bad


def main():
    path = sys.argv[1]
    calib_src = sys.argv[sys.argv.index("--calib") + 1] if "--calib" in sys.argv else "ctx"
    z = np.load(path)
    info = json.loads(str(z["info"]))
    steps = {}
    for k in sorted((k for k in info if k.endswith("_args")), key=lambda k: int(k.split("_", 1)[0][4:])):
        steps.setdefault(k.split("_", 1)[1].rsplit("_args", 1)[0], k.split("_", 1)[0])   # first exec only
    cnr1 = next(n for n in ("cnr1_b760", "cnr1_c070") if n in steps)
    cnr4 = next(n for n in ("cnr4_ea10", "cnr4_fe70") if n in steps)
    s_setup, s_cnr1, s_cnr2, s_cnr3, s_cnr4 = (steps[n] for n in ("setup_6290", cnr1, "cnr2_c480", "cnr3_db40", cnr4))
    factor = int(info[f"{s_cnr1}_{cnr1}_args"]["args"][0], 16)
    ctx = z[f"{s_cnr2}_cnr2_c480_pre_ctx"]
    ctx_calib = {
        "p30": i32(ctx, 0x30), "p34": i32(ctx, 0x34), "p38": i32(ctx, 0x38), "p3c": i32(ctx, 0x3C),
        "p40": i32(ctx, 0x40), "p44": i32(ctx, 0x44), "p48": i32(ctx, 0x48),
        "base_4c": i32(ctx, 0x4C), "base_50": i32(ctx, 0x50), "base_54": i32(ctx, 0x54), "base_58": i32(ctx, 0x58),
        "lo1": i32(ctx, 0x68), "hi1": i32(ctx, 0x6C), "lo2": i32(ctx, 0x70), "hi2": i32(ctx, 0x74),
        "strength": i32(ctx, 0x78), "factor": factor,
    }
    cb = z["calib"]  # calib+0x1000..+0x1200
    sr2_calib = M.calib_from_tags({t: i16(cb, 0x9C + 2 * i) for i, t in enumerate(M.MARBLE_SR2_TAGS)})
    # tag order 0x794a..0x795e maps to +0x109c, +0x10a0.., +0x109e: fix the two odd ones
    sr2_calib_fixed = M.calib_from_tags(
        {0x794A: i16(cb, 0x9C), 0x795E: i16(cb, 0x9E),
         **{0x794B + i: i16(cb, 0xA0 + 2 * i) for i in range(19)}})
    print(f"{Path(path).name}: {cnr1}/{cnr4}, factor {factor}, slider esi ctx-derived")
    print("  ctx  calib:", {k: ctx_calib[k] for k in ("p34", "p38", "p40", "p48", "base_4c", "factor")})
    print("  calib block:", {k: sr2_calib_fixed[k] for k in ("p34", "p38", "p40", "p48", "base_4c", "factor", "enabled")},
          "x5c/x60/x64", (sr2_calib_fixed["x5c"], sr2_calib_fixed["x60"], sr2_calib_fixed["x64"]))
    calib = sr2_calib_fixed if calib_src == "sr2" else ctx_calib
    # the slider: the ctx has p4c already scaled; recover esi from base vs p4c
    params = M.slider_params(calib, 5)
    if params["p4c"] != i32(ctx, 0x4C):
        print(f"  ! ctx p4c {i32(ctx, 0x4C)} != slider 5 gain {params['p4c']} -- non-default slider?")
    params["p4c"], params["p50"] = i32(ctx, 0x4C), i32(ctx, 0x50)
    params["p54"], params["p58"] = i32(ctx, 0x54), i32(ctx, 0x58)

    x0, y0, x1, y1 = [int(v) for v in info["in_meta"]["meta"][12:16]]
    sl = (slice(y0, y1), slice(x0, x1))
    H, W = y1 - y0, x1 - x0
    W2 = W // 2
    print(f"  tile rect ({x0},{y0})-({x1},{y1}) -> {W}x{H}")

    total = 0
    # --- setup: gamut_fwd + YCC + 2:1 pair mean, against setup_6290's output ---
    r, g, b = z["in_set0"][sl], z["in_set1"][sl], z["in_set2"][sl]
    rw, gw, bw = M.gamut_fwd(r, g, b)
    y, c1, c2 = M.rgb_to_ycc(rw, gw, bw)
    c1h = ((c1[:, 0:2 * W2:2].astype(np.uint32) + c1[:, 1:2 * W2:2]) >> 1).astype(np.uint16)
    c2h = ((c2[:, 0:2 * W2:2].astype(np.uint32) + c2[:, 1:2 * W2:2]) >> 1).astype(np.uint16)
    ys_e, c1s_e, c2s_e = (z[f"{s_setup}_setup_6290_post_set{k}"] for k in range(3))
    total += report("setup Y", y, ys_e)
    total += report("setup C1 (pair mean)", c1h, c1s_e)
    total += report("setup C2 (pair mean)", c2h, c2s_e)

    # --- cnr1: the f x f box, on the engine's setup output ---
    f = factor
    hs, ws = (H + f - 1) // f, (W + f - 1) // f
    ysh = 2 * int(np.log2(f))

    def box(p, bw, shift):
        ph = np.zeros((hs * f, ws * bw), np.uint32)
        ph[:p.shape[0], :p.shape[1]] = p
        s = ph.reshape(hs, f, ws, bw).sum(axis=(1, 3))
        v = (s + (1 << (shift - 1))) >> shift
        return np.where(v == 0, 1, v).astype(np.uint16)

    d0, d1, d2 = box(ys_e, f, ysh), box(c1s_e, f // 2, ysh - 1), box(c2s_e, f // 2, ysh - 1)
    for k, ours in enumerate((d0, d1, d2)):
        total += report(f"{cnr1} plane{k}", ours, z[f"{s_cnr1}_{cnr1}_post_set{k}"])

    # --- pad (scratch_a70) + cnr2 on the engine's padded planes ---
    pad = 6
    p0e, p1e, p2e = (z[f"{s_cnr2}_cnr2_c480_pre_set{k}"] for k in range(3))
    p0, p1, p2 = (np.pad(z[f"{s_cnr1}_{cnr1}_post_set{k}"], pad, mode="edge") for k in range(3))
    total += report("scratch_a70 pad (Y)", p0, p0e)
    q1, q2 = M.cnr2_threshold_mean(p0e, p1e, p2e, params)
    core = (slice(4, hs + 8), slice(4, ws + 8))
    total += report("cnr2 C1 (written core)", q1, z[f"{s_cnr2}_cnr2_c480_post_set1"], core)
    total += report("cnr2 C2 (written core)", q2, z[f"{s_cnr2}_cnr2_c480_post_set2"], core)

    # --- cnr3 on the engine's cnr2 output ---
    q1e, q2e = z[f"{s_cnr3}_cnr3_db40_pre_set1"], z[f"{s_cnr3}_cnr3_db40_pre_set2"]
    s1, s2 = M.cnr3_blur(q1e, params["p54"]), M.cnr3_blur(q2e, params["p58"])
    core3 = (slice(6, hs + 6), slice(6, ws + 6))   # cnr3 writes the core only; the ring stays stale
    total += report("cnr3 C1", s1, z[f"{s_cnr3}_cnr3_db40_post_set1"], core3)
    total += report("cnr3 C2", s2, z[f"{s_cnr3}_cnr3_db40_post_set2"], core3)

    # --- cnr4: upsample + protect on the engine's cnr3 output ---
    s1e, s2e = z[f"{s_cnr4}_{cnr4}_pre_set1"], z[f"{s_cnr4}_{cnr4}_pre_set2"]
    tmp_e = z[f"{s_cnr4}_{cnr4}_pre_tmp"]
    cell = (slice(pad, pad + hs + 1), slice(pad, pad + ws + 1))
    u1 = M.cnr4_upsample(s1e[cell], hs, ws, f)[:H, :W2]
    u2 = M.cnr4_upsample(s2e[cell], hs, ws, f)[:H, :W2]
    b2, b1 = M.cnr4_protect(u1, u2, tmp_e, params)
    v4 = (slice(f // 2, H), slice(f // 4, W2))   # rows/cols the engine never writes are excluded
    total += report(f"{cnr4} b2 (C1)", b2, z[f"{s_cnr4}_{cnr4}_post_b2"], v4)
    total += report(f"{cnr4} b1 (C2)", b1, z[f"{s_cnr4}_{cnr4}_post_b1"], v4)

    # --- whole chain from the tile input, luma from the engine's Clarity output ---
    c1n, c2n = M.marble_ycc_planes(y, c1, c2, params)
    eng_c1 = np.repeat(z[f"{s_cnr4}_{cnr4}_post_b2"], 2, axis=1)
    eng_c2 = np.repeat(z[f"{s_cnr4}_{cnr4}_post_b1"], 2, axis=1)
    v = (slice(16, H - 16), slice(16, W - 16))
    total += report("chain C1 clean", c1n, eng_c1, v)
    total += report("chain C2 clean", c2n, eng_c2, v)
    yc = z[f"{steps['fin_5f20']}_fin_5f20_pre_set0"]   # Y after the engine's Clarity
    amount = 1.0
    ro, go, bo = M.ycc_to_rgb(yc, c1n, c2n) if amount == 1.0 else (None, None, None)
    ro, go, bo = M.gamut_inv(ro, go, bo)
    for nm, a, k in (("R", ro, "out_set0"), ("G", go, "out_set1"), ("B", bo, "out_set2")):
        total += report(f"chain out {nm}", a, z[k][sl], v)
    print("RESULT:", "BIT-EXACT" if total == 0 else f"{total} mismatches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
