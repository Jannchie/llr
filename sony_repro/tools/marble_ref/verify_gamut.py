"""Verify gamut.py against the captured Edit.exe ground truth.

    python marble_ref/verify_gamut.py [marble_export_marble-q3.npz]

Prints mismatch counts / max abs diff per plane, inside the processed rect,
for both the forward (step0) and inverse (step30) calls, plus a check that
pixels outside the rect are passed through untouched, and a self-check of
the analytic LUT generator against the three captured tables.
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gamut  # noqa: E402

DEFAULT_NPZ = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "marble_export_marble-q3.npz",
)


def rect_from_meta(info, key):
    """The rect functor lives at [r9+8..r9+0x14]; the capture stores it as
    meta[12:16] = (x0, y0, x1, y1)."""
    return tuple(info[key]["meta"][12:16])


def report(tag, got, gt, rect, pre):
    x0, y0, x1, y1 = rect
    sl = (slice(y0, y1), slice(x0, x1))
    print(f"  {tag}  rect=(x0={x0}, y0={y0}, x1={x1}, y1={y1})  "
          f"{(y1 - y0) * (x1 - x0)} px")
    for i in range(3):
        d = np.abs(got[i][sl].astype(np.int64) - gt[i][sl].astype(np.int64))
        print(f"    plane{i}: mismatches {int((d != 0).sum()):>8d}  "
              f"max|diff| {int(d.max()) if d.size else 0}")
    # outside-rect behaviour
    for i in range(3):
        m = np.ones(gt[i].shape, bool)
        m[sl] = False
        untouched = np.array_equal(gt[i][m], pre[i][m])
        same = np.array_equal(got[i][m], gt[i][m])
        print(f"    plane{i}: outside rect -- exe left it unchanged: {untouched}"
              f", ours matches: {same}")


def main(path=DEFAULT_NPZ):
    z = np.load(path)
    info = json.loads(str(z["info"]))
    luts = gamut.load_luts(z)

    print(f"npz: {path}")
    print("LUT generator self-check (captured vs analytic, indices 0..0x3fff):")
    for name, gen in (
        ("lut_61bd60", gamut.gen_lut_61bd60),
        ("lut_63bd60", gamut.gen_lut_63bd60),
        ("lut_65bd60", gamut.gen_lut_65bd60),
    ):
        cap = z[name].view("<i2").astype(np.int64)[: gamut.ENC_SIZE]
        d = np.abs(gen()[: gamut.ENC_SIZE] - cap)
        print(f"    {name}: mismatches {int((d != 0).sum())}  max|diff| {int(d.max())}")
    print("    lut_5bbd60: not captured -- regenerated analytically "
          "(validated below by the inverse pass)")

    print("\nforward  fn_140196170  (step0_gamut_fwd)")
    pre = [z[f"step0_gamut_fwd_pre_set{i}"] for i in range(3)]
    gt = [z[f"step0_gamut_fwd_post_set{i}"] for i in range(3)]
    rect = rect_from_meta(info, "step0_gamut_fwd_pre_meta")
    got = gamut.gamut_fwd(pre[0], pre[1], pre[2], rect, luts)
    report("fwd", got, gt, rect, pre)

    print("\ninverse  fn_140196330  (step30_gamut_inv)")
    pre = [z[f"step30_gamut_inv_pre_set{i}"] for i in range(3)]
    gt = [z[f"step30_gamut_inv_post_set{i}"] for i in range(3)]
    rect = rect_from_meta(info, "step30_gamut_inv_pre_meta")
    got = gamut.gamut_inv(pre[0], pre[1], pre[2], rect, luts)
    report("inv", got, gt, rect, pre)

    print("\nround-trip sanity: gamut_inv(gamut_fwd(x)) on the fwd rect")
    a = gamut.gamut_fwd(pre[0], pre[1], pre[2], rect, luts)
    b = gamut.gamut_inv(a[0], a[1], a[2], rect, luts)
    x0, y0, x1, y1 = rect
    sl = (slice(y0, y1), slice(x0, x1))
    for i in range(3):
        d = np.abs(b[i][sl].astype(np.int64) - pre[i][sl].astype(np.int64))
        print(f"    plane{i}: max|diff| {int(d.max())}  mean {float(d.mean()):.4f}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_NPZ)
