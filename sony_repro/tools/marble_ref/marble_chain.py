r"""把七段逐位复刻拼成整条 ZcTaskSIMDMarble(色差清理支路),对着 marble_export_marble-q3.npz 的 tile 0
从入口 RGB 一路算到出口 RGB。Clarity 那一支不在这里(产品里已有 sony/clarity.py),Y 直接取引擎 Clarity 之后的平面。

    export PYTHONUTF8=1; python marble_chain.py [marble_export_marble-q3.npz]
"""
import json
import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from blend_b010 import blend_b010  # noqa: E402
from cnr1_b760 import cnr1_b760  # noqa: E402
from cnr2_c480 import cnr2_c480  # noqa: E402
from cnr2_c480 import ctx_params_from_bytes as cnr2_params  # noqa: E402
from cnr3_db40 import cnr3_db40  # noqa: E402
from cnr4_ea10 import cnr4_ea10  # noqa: E402
from cnr4_ea10 import ctx_params_from_bytes as cnr4_params  # noqa: E402
from fin_5f20 import fin_5f20  # noqa: E402
from gamut import gamut_fwd, gamut_inv, load_luts  # noqa: E402
from setup_6290 import setup_6290  # noqa: E402


def f32(x):
    return np.asarray(x, dtype=np.float32)


def rgb_to_ycc(r, g, b):
    """0x14038aa40:8 像素一组的 float32 路径(和的结合顺序按二进制)。"""
    R, G, B = f32(r), f32(g), f32(b)
    def one(kr, kg, kb, off):
        t = (G * f32(kg) + R * f32(kr)) + B * f32(kb)
        v = np.trunc(t / f32(2048.0)) + f32(off)
        return np.clip(v, 0, 65535).astype(np.uint16)
    y = one(2884, 3523, 625, 4096)
    c1 = one(-1707, -2404, 4113, 32768)
    c2 = one(6860, -6848, -9, 32768)
    return y, c1, c2


def replicate_pad6(p):
    return np.pad(p, 6, mode="edge")


def marble_chroma(r, g, b, rect, luts, ctx2, ctx4, ctx3, y_after_clarity=None, amount=1.0, factor=4):
    """整条链。rect=(x0,y0,x1,y1) 是 tile 的内矩形;返回出口 RGB(三平面 uint16)。"""
    x0, y0, x1, y1 = rect
    W, H = x1 - x0, y1 - y0
    r, g, b = gamut_fwd(r, g, b, rect, luts)
    y, c1, c2 = rgb_to_ycc(r, g, b)
    c1_orig, c2_orig = c1.copy(), c2.copy()
    ys, c1s, c2s = setup_6290(y, c1, c2, x0, y0, W, H)
    tmp = c2s.copy()
    d0, d1, d2 = cnr1_b760(ys, c1s, c2s, factor)
    p0, p1, p2 = replicate_pad6(d0), replicate_pad6(d1), replicate_pad6(d2)
    q0, q1, q2 = cnr2_c480(p0, p1, p2, ctx2, factor)
    s0, s1, s2 = cnr3_db40(q0, q1, q2, ctx3, factor)
    b2, b1 = cnr4_ea10(s0, s1, s2, None, tmp, ctx4, factor)
    yfin = ys if y_after_clarity is None else y_after_clarity
    yf, c1f, c2f = fin_5f20(yfin, b2, b1, x0, y0, W, H, r.shape[1], r.shape[0])
    ro, go, bo = blend_b010(yf, c1f, c1_orig, c2f, c2_orig, amount)
    inner = (x0 + 16, y0 + 16, x1 - 16, y1 - 16)
    return gamut_inv(ro, go, bo, inner, luts), inner


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "marble_export_marble-q3.npz")
    z = np.load(path)
    info = json.loads(str(z["info"]))
    luts = load_luts(z)
    rect = tuple(int(v) for v in info["in_meta"]["meta"][12:16])
    ctx_raw = z["step14_cnr4_ea10_pre_ctx"]
    ctx2 = cnr2_params(ctx_raw)
    ctx4 = cnr4_params(ctx_raw)
    ctx3 = {"W": ctx2["W"], "H": ctx2["H"], "p54": struct.unpack_from("<i", bytes(ctx_raw.tobytes()), 0x54)[0],
            "p58": struct.unpack_from("<i", bytes(ctx_raw.tobytes()), 0x58)[0]}
    (ro, go, bo), inner = marble_chroma(z["in_set0"], z["in_set1"], z["in_set2"], rect, luts, ctx2, ctx4, ctx3,
                                        y_after_clarity=z["step25_attach_b640_post_set0"], amount=1.0)
    xa, ya, xb, yb = inner
    sl = (slice(ya, yb), slice(xa, xb))
    for k, ours in (("out_set0", ro), ("out_set1", go), ("out_set2", bo)):
        ref = z[k]
        d = np.abs(ours[sl].astype(np.int32) - ref[sl].astype(np.int32))
        print(f"{k}: mismatches {(d > 0).sum()} / {d.size}  max|diff| {d.max()}  ({100 * (d == 0).mean():.4f}% exact)")
    # 中间量交叉核对
    y, c1, c2 = rgb_to_ycc(*gamut_fwd(z["in_set0"], z["in_set1"], z["in_set2"], rect, luts))
    for k, ours in (("step1_prep_aa40_post_set0", y), ("step1_prep_aa40_post_set1", c1), ("step1_prep_aa40_post_set2", c2)):
        d = np.abs(ours.astype(np.int32) - z[k].astype(np.int32))
        print(f"  ycc {k}: mismatches {(d > 0).sum()} max {d.max()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
