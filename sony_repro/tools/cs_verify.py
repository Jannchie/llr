r"""用 export_cs_capture.py 的产物核对 ChromaSuppres 模型(PIPELINE §7.5 / notes/static-chromasuppres.md)。

两条路:
  1. 从 dump 的 lv 参数 + 四张 LUT 按反编译算 hiY/loY/slope,逐像素套公式,和引擎出口比逐位率;
  2. 不看公式,直接从 tile 量 f(Y) = 256·(Cr_out−32768)/(Cr_in−32768),按 Y 分桶,看折点在哪。

    bash -lc "cd ~/llr/apps/worker && uv run python ../../sony_repro/tools/cs_verify.py cs_export_<name>.npz [...]"
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def derive(params, lut, anchors=None):
    """hiY / loY / slope_lo / slope_hi,照反编译一步不差。anchors 缺省用 lv 里读到的。"""
    lv = params["lv"]
    x = lv["x_raw"] / 300.0
    f = anchors if anchors is not None else lv["f"]
    A0, A1, A2, B0, B1, B2, slo, ratio = f
    A, B = float(A0), float(B0)
    if -2.0 <= x < -1.0:
        A = (A1 - A2) * (x + 2.0) + A2
        B = (B1 - B2) * (x + 2.0) + B2
    if -1.0 <= x < 0.0:
        B = (B0 - B1) * (x + 1.0) + B1
        A = (A0 - A1) * (x + 1.0) + A1
    A, B = int(A), int(B)
    ramp = 0.0
    if -2.0 <= x <= -1.0:
        ramp = x + 2.0
    if -1.0 <= x <= 0.0:
        ramp = -x
    sc = lv["sc_raw"] * (1.0 / 64.0)
    t = min(B, 0x3FFF)
    t = int(int(lut["pre2"][int(lut["pre1"][t])]) * sc)
    t = min(t, 0x7FFF)
    hiY = int(lut["ygam"][int(lut["tone"][t])])
    loY = 0 if B == 0 else (hiY * ratio) // B if hiY * ratio >= 0 else -((-hiY * ratio) // B)
    slope_lo = int(slo / sc)
    slope_hi = int(A / sc) + int(ramp * 150.0)
    hiY += int(ramp * -1200.0)
    return dict(x=x, A=A, B=B, ramp=ramp, sc=sc, hiY=hiY, loY=loY, slope_lo=slope_lo, slope_hi=slope_hi)


def model(tile_in, d):
    y = tile_in[..., 0].astype(np.int16).astype(np.int64)
    f = np.full(y.shape, 255, np.int64)
    lo = y < d["loY"]
    hi = y > d["hiY"]
    f[lo] = 255 - (((d["loY"] - y[lo]) * d["slope_lo"]) >> 12)
    f[hi] = 255 - (((y[hi] - d["hiY"]) * d["slope_hi"]) >> 12)
    f = np.clip(f, 0, 255)
    out = np.empty_like(tile_in)
    out[..., 0] = np.maximum(y, 0).astype(np.uint16)
    for c in (1, 2):
        v = (tile_in[..., c].astype(np.int64) - 0x8000) * f
        v = (v + np.where(v < 0, 0xFF, 0)) >> 8      # 向零取整
        out[..., c] = (v + 0x8000).astype(np.uint16)
    return out


def measure(tile_in, tile_out):
    y = tile_in[..., 0].astype(np.int16).astype(np.int64)
    ci = tile_in[..., 1].astype(np.int64) - 0x8000
    co = tile_out[..., 1].astype(np.int64) - 0x8000
    m = np.abs(ci) >= 256
    if m.sum() < 100:
        return
    fm = co[m] * 256.0 / ci[m]
    yb = y[m]
    print("     Y 分桶 → 实测 f(中位数):")
    edges = list(range(0, 16384, 1024))
    for a in edges:
        sel = (yb >= a) & (yb < a + 1024)
        if sel.sum() > 20:
            print(f"       Y∈[{a:5d},{a + 1024:5d}) n={sel.sum():7d} f={np.median(fm[sel]):7.2f}  p5={np.percentile(fm[sel], 5):7.2f} p95={np.percentile(fm[sel], 95):7.2f}")


def main():
    paths = [a for i, a in enumerate(sys.argv[1:]) if a.endswith(".npz")]
    for path in paths:
        z = np.load(path if os.path.isabs(path) else os.path.join(HERE, path))
        params = json.loads(str(z["params"]))
        lut = {k[4:]: z[k] for k in z.files if k.startswith("lut_")}
        print(path)
        print("   lv:", params["lv"])
        print("   calib:", params["calib"])
        anchors = None
        if params["calib"]:
            anchors = params["calib"][0]["f"]
        if "--anchors" in sys.argv:   # 逗号分隔的八个数:A0,A1,A2,B0,B1,B2,slope_lo,ratio(标签 0x787e/0x787f/0x7880/0x7881)
            anchors = [int(v) for v in sys.argv[sys.argv.index("--anchors") + 1].split(",")]
        d = derive(params, lut, anchors)
        print("   derived:", d)
        idx = sorted({int(k[1:].split("_")[0]) for k in z.files if k.endswith("_out")})
        for i in idx:
            a, b = z[f"t{i}_in"], z[f"t{i}_out"]
            m = model(a, d)
            eq = (m == b)
            print(f"   tile{i} {a.shape[1]}x{a.shape[0]}: 逐位 Y {eq[..., 0].mean() * 100:.4f}%  Cr {eq[..., 1].mean() * 100:.4f}%  Cb {eq[..., 2].mean() * 100:.4f}%"
                  f"  maxdiff Cr {np.abs(m[..., 1].astype(int) - b[..., 1].astype(int)).max()} Cb {np.abs(m[..., 2].astype(int) - b[..., 2].astype(int)).max()}"
                  f"  Y range {a[..., 0].astype(np.int16).min()}..{a[..., 0].astype(np.int16).max()}  Y>hiY: {(a[..., 0].astype(np.int16) > d['hiY']).mean() * 100:.2f}%")
            if i == idx[0]:
                measure(a, b)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
