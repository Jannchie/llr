r"""读 highiso_amount_probe.py 的产物,回答「量 ≥ 50 引擎改了什么」并核对本 ISO 上各级是否仍逐位。

    cd apps/worker && uv run python ../../sony_repro/tools/highiso_probe_analyze.py <ARW> q50 q75 q100

  1. 参数块 / 阈值表 / 混合表 各档并排;执行普查差异;tile 外扩(meta rect)。
  2. RawNR:用 llr 的核(sony/rawnr_simd)在抓到的入口 tile 上重算 —— q50 用 ARW 标签的曲线,
     其它档用抓到的 tbl0(阈值)/ tbl3(混合)/ 参数块里的 gain、limit,报逐位率。
  3. q50 顺带抓的 ITP、Marble tile:各用 llr 的实现重算,报逐位率;Marble 再报入口→出口的
     暗部低频色度均值位移(引擎 vs llr),对应成品上量到的「降噪把暗部往青里推」。
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def probe_path(suf):
    """产物先找 tools/,再找 tmp/highiso/probes/(大文件不进仓库,放 tmp)。"""
    for d in (HERE, os.path.join(HERE, "..", "..", "tmp", "highiso", "probes")):
        p = os.path.join(d, f"highiso_probe_{suf}.npz")
        if os.path.exists(p):
            return p
    raise FileNotFoundError(f"highiso_probe_{suf}.npz")


sys.path.insert(0, HERE)
from rawnr_export_verify import CFA_BY_PHASE, detect_phase  # noqa: E402

from llr_worker.denoise import pack_bayer, unpack_bayer  # noqa: E402
from llr_worker.sony import rawnr_simd as simd  # noqa: E402
from llr_worker.sony.rawnr import detail_restore, noise_model  # noqa: E402

BLACK, WHITE = 512.0, 16383.0
IDX = (0, 256, 512, 1024, 2048, 3072, 4096, 8192, 16383)


def load(suf):
    return np.load(probe_path(suf))


def rawnr_rerun(mi, table, blend, gain, limit, strength=1.0):
    """mi: uint16 马赛克(含黑电平)-> llr 核的出口马赛克(int)。"""
    h, w = mi.shape[0] & ~1, mi.shape[1] & ~1
    mi = mi[:h, :w]
    ph, means = detect_phase(mi.astype(np.float64))
    cfa = CFA_BY_PHASE[ph]
    raw = np.clip(pack_bayer(mi).astype(np.float32), 0, WHITE)
    order = {c: i for i, c in enumerate(cfa)}
    gi = [i for i, c in enumerate(cfa) if c == "G"]
    red, blue, g0, g1 = order["R"], order["B"], gi[0], gi[1]
    pad = simd.PHASE_MARGIN
    padded = [np.pad(raw[..., k], pad, mode="reflect") for k in range(4)]
    kw = dict(gain=int(gain), limit=int(limit), blend=int(blend))
    out = np.empty_like(raw)
    out[..., g0], out[..., g1] = simd.denoise_greens(padded[g0], padded[g1], table, **kw)
    out[..., red] = simd.denoise_phase_rb(padded[red], table, **kw)
    out[..., blue] = simd.denoise_phase_rb(padded[blue], table, **kw)
    out = simd.apply_strength(out, raw, strength)
    return unpack_bayer(np.rint(out).astype(np.int64)), ph, cfa


def score(got, mo, mi, t=24):
    d = np.abs(got[t:-t, t:-t] - mo[t:-t, t:-t].astype(np.int64))
    ch = np.mean(mi[t:-t, t:-t] != mo[t:-t, t:-t])
    dd = (mo[t:-t, t:-t].astype(np.int64) - mi[t:-t, t:-t].astype(np.int64))
    per = {k: float(np.mean(d[i::2, j::2] == 0) * 100) for k, (i, j) in {"p00": (0, 0), "p01": (0, 1), "p10": (1, 0), "p11": (1, 1)}.items()}
    return float(np.mean(d == 0) * 100), int(d.max()), ch, float(np.median(np.abs(dd))), per


def main():
    arw = sys.argv[1]
    sufs = sys.argv[2:]
    Z = {s: load(s) for s in sufs}
    curve = noise_model(arw)
    restore = detail_restore(arw)
    print(f"ARW 标签:{curve}  {restore}")

    print("\n1a. 参数块 +0xc0000 起,非零项(各档并排)")
    keys = sorted({i for z in Z.values() for i, v in enumerate(z["params"]) if v})
    print(f"  {'偏移':<10}" + "".join(f"{s:>10}" for s in sufs))
    for i in keys:
        print(f"  +{0xC0000 + 4 * i:x}   " + "".join(f"{int(Z[s]['params'][i]):>10}" for s in sufs))
    print("\n1b. 表:tbl0(阈值)/ tbl3(混合权重)在若干电平处")
    for name in ("tbl0", "tbl1", "tbl2", "tbl3", "tbl4", "tbl5"):
        print(f"  {name}: " + " | ".join(f"{s} " + " ".join(f"{int(Z[s][name][i])}" for i in IDX) for s in sufs))
    for s in sufs:
        t = Z[s]["tbl0"]
        mine = curve.threshold(np.arange(len(t)))
        print(f"  {s}: tbl0 / 标签曲线 的比 @2048 = {t[2048] / max(mine[2048], 1):.3f},  tbl0 == 标签曲线 逐项 {np.mean(t == mine) * 100:.2f}%")
    print("\n1c. 执行普查(次数)")
    cen = {s: json.loads(str(Z[s]["census"][0]))["counts"] for s in sufs}
    names = sorted({k for c in cen.values() for k in c})
    for k in names:
        print(f"  {k:<40}" + "".join(f"{cen[s].get(k, 0):>8}" for s in sufs))
    print("\n1d. RawNR tile 的 meta:rect(有效矩形) 与 pos(整幅位置)")
    for s in sufs:
        for i in range(3):
            m = Z[s].get(f"t{i}_meta")
            if m is not None:
                print(f"  {s} t{i}: shape {Z[s][f't{i}_in'].shape[:2]} rect {list(m[12:16])} pos {list(m[18:22])}")

    print("\n2. RawNR 重算(llr 核 + 该档抓到的表/参数)")
    for s in sufs:
        z = Z[s]
        p = z["params"]
        gain, limit = int(p[(0xC00E8 - 0xC0000) // 4]), int(p[(0xC00F4 - 0xC0000) // 4])
        blend = int(z["tbl3"][2048])
        table = z["tbl0"].astype(np.float32)
        for i in range(3):
            if f"t{i}_out" not in z:
                continue
            mi, mo = z[f"t{i}_in"][..., 0], z[f"t{i}_out"][..., 0]
            got, ph, cfa = rawnr_rerun(mi, table, blend, gain, limit)
            h, w = got.shape
            bit, mx, ch, med, per = score(got, mo[:h, :w], mi[:h, :w])
            print(f"  {s} t{i} 相位 {ph} gain {gain} limit {limit} blend {blend}: 引擎改动 {ch * 100:.1f}% |Δ|中位 {med:.0f} | llr 逐位 {bit:.3f}% max {mx} | " + " ".join(f"{k} {v:.2f}" for k, v in per.items()))
        # 只换一个量的对照:量 50 的表 + 本档的 blend,或本档的表 + 512
        if s != sufs[0] and "t0_out" in z:
            mi, mo = z["t0_in"][..., 0], z["t0_out"][..., 0]
            for lab, tb, bl in (("q50表+本档blend", Z[sufs[0]]["tbl0"].astype(np.float32), blend), ("本档表+blend512", table, 512)):
                got, _, _ = rawnr_rerun(mi, tb, bl, gain, limit)
                h, w = got.shape
                bit, mx, _, _, per = score(got, mo[:h, :w], mi[:h, :w])
                print(f"     对照 {lab}: 逐位 {bit:.3f}% max {mx}")

    z = Z[sufs[0]]
    if "SIMDITP_t0_out" in z:
        print("\n3a. ITP 重算(q50)")
        import rawpy
        from llr_worker.sony import itp
        with rawpy.imread(arw) as raw:
            cwb = [float(v) for v in raw.camera_whitebalance]
            black = float(np.mean(raw.black_level_per_channel))
        wb = (cwb[0], cwb[1], cwb[3] if cwb[3] > 0 else cwb[1], cwb[2])
        gains = itp.wb_gains(wb)
        for i in range(2):
            if f"SIMDITP_t{i}_out" not in z:
                continue
            mos = z[f"SIMDITP_t{i}_in"][..., 0]
            m = z[f"SIMDITP_t{i}_meta"]
            rect = tuple(int(v) for v in m[12:16])
            got = itp.itp_tile(mos, gains, black, rect)
            x0, y0, x1, y1 = rect
            t = 12
            row = f"  ITP t{i} {mos.shape[1]}x{mos.shape[0]} rect {rect} pos {list(m[18:22])}:"
            for c in range(3):
                want = z[f"SIMDITP_t{i}_out"][..., c]
                d = np.abs(got[c].astype(int) - want.astype(int))[y0 + t:y1 - t, x0 + t:x1 - t]
                row += f"  out{c} 逐位 {np.mean(d == 0) * 100:.3f}% max {d.max()}"
            print(row)
    if "SIMDMarble_t0_out" in z:
        print("\n3b. Marble 重算(q50):色度路径 + 入口→出口的暗部均值位移")
        from llr_worker.sony import marble as M
        params = M.slider_params(M.CALIB_7CM2, 5)
        W601 = np.array([0.299, 0.587, 0.114])
        M601 = np.array([[0.299, 0.587, 0.114], [-0.168736, -0.331264, 0.5], [0.5, -0.418688, -0.081312]])
        for i in range(2):
            if f"SIMDMarble_t{i}_out" not in z:
                continue
            inp, ref = z[f"SIMDMarble_t{i}_in"], z[f"SIMDMarble_t{i}_out"]
            m = z[f"SIMDMarble_t{i}_meta"]
            x0, y0, x1, y1 = [int(v) for v in m[12:16]]
            sl = (slice(y0 + 32, y1 - 32), slice(x0 + 32, x1 - 32))
            rw, gw, bw = M.gamut_fwd(inp[..., 0], inp[..., 1], inp[..., 2])
            y, c1, c2 = M.rgb_to_ycc(rw, gw, bw)
            c1n, c2n = M.marble_ycc_planes(y, c1, c2, params)
            ro, go, bo = M.ycc_to_rgb(y, c1n, c2n)
            ro, go, bo = M.gamut_inv(ro, go, bo)
            ours = np.stack([ro, go, bo], -1).astype(np.int32)
            d = np.abs(ours[sl] - ref[sl].astype(np.int32))
            print(f"  Marble t{i} rect {[x0, y0, x1, y1]} pos {list(m[18:22])}: 对引擎出口(Y 取入口的,即不含 Clarity)逐位 {np.mean(d == 0) * 100:.2f}%  max {d.max()}  按通道 max {[int(d[..., c].max()) for c in range(3)]}  |Δ|均值 {[round(float(d[..., c].mean()), 2) for c in range(3)]}")
            # 引擎出口的 Y 和入口的 Y(Marble 自己的 Y 定义)差多少 —— Clarity 那一半
            rwo, gwo, bwo = M.gamut_fwd(ref[..., 0], ref[..., 1], ref[..., 2])
            ye, c1e, c2e = M.rgb_to_ycc(rwo, gwo, bwo)
            dy = ye[sl].astype(np.int64) - y[sl].astype(np.int64)
            print(f"     引擎出口 Y − 入口 Y(Marble YCC,16 位):均值 {dy.mean():+.1f} 中位 {np.median(dy):+.0f} |.|p99 {np.percentile(np.abs(dy), 99):.0f}")
            dc1 = c1e[sl].astype(np.int64) - c1n[sl].astype(np.int64)
            dc2 = c2e[sl].astype(np.int64) - c2n[sl].astype(np.int64)
            print(f"     引擎出口 C1/C2 − llr 清理后 C1/C2:均值 {dc1.mean():+.2f}/{dc2.mean():+.2f}  |.|均值 {np.abs(dc1).mean():.2f}/{np.abs(dc2).mean():.2f}  max {np.abs(dc1).max()}/{np.abs(dc2).max()}")
            # 暗部低频位移(Rec601,/255 标度;输入是 14 位 sRGB 编码)
            ycc_in = (inp[sl].astype(np.float64) / 16383.0) @ M601.T
            ycc_eng = (ref[sl].astype(np.float64) / 16383.0) @ M601.T
            ycc_our = (ours[sl].astype(np.float64) / 16383.0) @ M601.T
            for lo, hi in ((0, 0.06), (0.06, 0.12), (0.12, 0.25), (0.25, 0.5), (0.5, 1.01)):
                mk = (ycc_in[..., 0] >= lo) & (ycc_in[..., 0] < hi)
                if mk.sum() < 2000:
                    continue
                de = (ycc_eng - ycc_in)[mk].mean(0) * 255
                do = (ycc_our - ycc_in)[mk].mean(0) * 255
                print(f"     Y∈[{lo:.2f},{hi:.2f}) n={int(mk.sum()):7d}  引擎 出口−入口 ΔY {de[0]:+.2f} ΔCb {de[1]:+.2f} ΔCr {de[2]:+.2f} | llr ΔY {do[0]:+.2f} ΔCb {do[1]:+.2f} ΔCr {do[2]:+.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
