r"""暗部低频色偏落在哪一级:用导出时抓到的 Marble 入口/出口 tile(降噪 关 vs 手动 50)定位。

    cd apps/worker && uv run --with scipy python ../../sony_repro/tools/highiso_off_vs_auto.py <workdir> qoff q50c

Marble 的入口 = 整条色调链之后、色差清理之前(14 位 sRGB 编码 RGB);出口 ≈ 成品。
按 tile 位置(meta 0x48..)配对两档,报 16px 低频均值差(Rec601,×255),按 Y 分桶:
  * 引擎 入口(50) − 入口(关):色偏若已在这里,就在 Marble 之前(RawNR/ITP/色调链);
  * 引擎 出口(50) − 出口(关):Marble 再贡献多少;
  * 同位置对 llr 的离线成品(llr_off / llr_nomarble / llr_auto,转回横向)——每一级各差多少。
ITP 的 off 档 tile 顺带用 llr 的 itp_tile 核一遍(降噪关时 ITP 是否仍逐位)。
"""
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np  # noqa: E402
from scipy import ndimage  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def probe_path(suf):
    """产物先找 tools/,再找 tmp/highiso/probes/(大文件不进仓库,放 tmp)。"""
    for d in (HERE, os.path.join(HERE, "..", "..", "tmp", "highiso", "probes")):
        p = os.path.join(d, f"highiso_probe_{suf}.npz")
        if os.path.exists(p):
            return p
    raise FileNotFoundError(f"highiso_probe_{suf}.npz")


M601 = np.array([[0.299, 0.587, 0.114], [-0.168736, -0.331264, 0.5], [0.5, -0.418688, -0.081312]], np.float64)
YB = ((0, .03), (.03, .06), (.06, .1), (.1, .15), (.15, .25), (.25, .4), (.4, .7), (.7, 1.01))


def tiles(z, cls):
    out = {}
    for i in range(16):
        if f"{cls}_t{i}_out" not in z:
            continue
        m = z[f"{cls}_t{i}_meta"]
        out[tuple(int(v) for v in m[18:22])] = (z[f"{cls}_t{i}_in"], z[f"{cls}_t{i}_out"], tuple(int(v) for v in m[12:16]))
    return out


def box(x, n=16):
    return np.stack([ndimage.uniform_filter(x[..., i], n, mode="reflect") for i in range(3)], -1)


def ycc14(a):
    return (a.astype(np.float64) / 16383.0) @ M601.T


def report(title, ref_y, d):
    print(f"  {title}")
    for a, b in YB:
        m = (ref_y >= a) & (ref_y < b)
        if m.sum() < 3000:
            continue
        v = d[m].mean(0) * 255
        print(f"    Y∈[{a:.2f},{b:.2f}) n={int(m.sum()):7d}  ΔY {v[0]:+6.2f}  ΔCb {v[1]:+6.2f}  ΔCr {v[2]:+6.2f}")


def main():
    work = Path(sys.argv[1])
    zo, za = (np.load(probe_path(s)) for s in sys.argv[2:4])
    To, Ta = tiles(zo, "SIMDMarble"), tiles(za, "SIMDMarble")
    common = sorted(set(To) & set(Ta))
    print(f"Marble tile:关 {len(To)} 块,50 {len(Ta)} 块,同位置 {len(common)} 块 {common}")
    llr = {}
    for k in ("llr_off", "llr_nomarble", "llr_auto"):
        p = work / f"{k}.npy"
        if p.exists():
            llr[k] = np.rot90(np.load(p, mmap_mode="r"), -1)   # 转回横向(TIFF orientation 8 的逆)
    if llr:
        print(f"llr 成品(横向){next(iter(llr.values())).shape}")

    acc = {}
    for pos in common:
        io, oo, ro = To[pos]
        ia, oa, ra = Ta[pos]
        x0, y0, x1, y1 = ro
        sl = (slice(y0 + 24, y1 - 24), slice(x0 + 24, x1 - 24))
        halo = ((io.shape[1] - (pos[2] - pos[0])) // 2, (io.shape[0] - (pos[3] - pos[1])) // 2)
        Yo_in, Ya_in = box(ycc14(io[sl])), box(ycc14(ia[sl]))
        Yo_out, Ya_out = box(ycc14(oo[sl])), box(ycc14(oa[sl]))
        ref = Ya_in[..., 0]
        print(f"\n== tile pos {pos}, plane {io.shape[1]}x{io.shape[0]}, halo {halo}, rect {ro} ==")
        report("引擎 Marble 入口:50 − 关", ref, Ya_in - Yo_in)
        report("引擎 Marble 出口:50 − 关", ref, Ya_out - Yo_out)
        report("引擎 Marble 50:出口 − 入口", ref, Ya_out - Ya_in)
        if llr:
            # llr 成品裁同一区域:plane 像素 (py,px) ↔ 整幅 (pos_y0 - halo_y + py, pos_x0 - halo_x + px)
            fy0, fx0 = pos[1] - halo[1] + y0 + 24, pos[0] - halo[0] + x0 + 24
            h, w = Ya_in.shape[:2]
            def crop(a, dy=0, dx=0):
                return np.asarray(a[fy0 + dy:fy0 + dy + h, fx0 + dx:fx0 + dx + w], np.float64)
            # 位移核对(整像素,±6):用引擎入口 Y 对 llr_nomarble 的 Y
            ye = ycc14(ia[sl])[..., 0]
            best = None
            for dy in range(-6, 7):
                for dx in range(-6, 7):
                    c = crop(llr["llr_nomarble"], dy, dx) @ M601[0]
                    r = np.corrcoef(ye[8:-8, 8:-8].ravel(), c[8:-8, 8:-8].ravel())[0, 1]
                    if best is None or r > best[0]:
                        best = (r, dy, dx)
            r, dy, dx = best
            print(f"  llr 对齐:位移 ({dy},{dx}) 相关 {r:.4f}")
            L = {k: box(crop(v, dy, dx) @ M601.T) for k, v in llr.items()}
            report("llr_off − 引擎入口(关)   [色调链,无降噪]", ref, L["llr_off"] - Yo_in)
            report("llr_nomarble − 引擎入口(50)[RawNR+ITP+色调链]", ref, L["llr_nomarble"] - Ya_in)
            report("llr_auto − 引擎出口(50)   [再加 Marble]", ref, L["llr_auto"] - Ya_out)
            report("llr:nomarble − off", ref, L["llr_nomarble"] - L["llr_off"])
        for a, b in YB:
            m = (ref >= a) & (ref < b)
            if m.sum() < 3000:
                continue
            acc.setdefault((a, b), []).append(((Ya_in - Yo_in)[m].mean(0), (Ya_out - Yo_out)[m].mean(0), int(m.sum())))
    print("\n== 各 tile 汇总(按像素数加权):引擎 50 − 关 ==")
    for (a, b), rows in sorted(acc.items()):
        n = sum(r[2] for r in rows)
        din = sum(r[0] * r[2] for r in rows) / n * 255
        dout = sum(r[1] * r[2] for r in rows) / n * 255
        print(f"  Y∈[{a:.2f},{b:.2f}) n={n:8d}  Marble 入口 ΔY {din[0]:+6.2f} ΔCb {din[1]:+6.2f} ΔCr {din[2]:+6.2f} | 出口 ΔY {dout[0]:+6.2f} ΔCb {dout[1]:+6.2f} ΔCr {dout[2]:+6.2f}")

    # ITP:关档 tile 用 llr 重算
    Io = tiles(zo, "SIMDITP")
    if Io:
        import rawpy
        from llr_worker.sony import itp
        arw = sys.argv[4] if len(sys.argv) > 4 else r"C:\Users\Jannchie\Downloads\DSC03692.ARW"
        with rawpy.imread(arw) as raw:
            cwb = [float(v) for v in raw.camera_whitebalance]
            black = float(np.mean(raw.black_level_per_channel))
        wb = (cwb[0], cwb[1], cwb[3] if cwb[3] > 0 else cwb[1], cwb[2])
        gains = itp.wb_gains(wb)
        print("\n== ITP(降噪关)重算 ==")
        for pos, (mi, mo, rect) in list(Io.items())[:3]:
            got = itp.itp_tile(mi[..., 0], gains, black, rect)
            x0, y0, x1, y1 = rect
            t = 12
            row = f"  pos {pos}:"
            for c in range(3):
                d = np.abs(got[c].astype(int) - mo[..., c].astype(int))[y0 + t:y1 - t, x0 + t:x1 - t]
                row += f"  out{c} 逐位 {np.mean(d == 0) * 100:.3f}% max {d.max()}"
            print(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
