"""Verify setup_6290 against the captured ground truth."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from setup_6290 import setup_6290, setup_6290_scalar  # noqa: E402

NPZ = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "marble_export_marble-q3.npz")

X0, Y0, X1, Y1 = 8, 8, 1072, 624
W, H = 1064, 616


def report(name, got, exp):
    assert got.shape == exp.shape, f"{name}: shape {got.shape} != {exp.shape}"
    d = got.astype(np.int64) - exp.astype(np.int64)
    bad = int(np.count_nonzero(d))
    n = exp.size
    print(f"  {name:8s} shape={exp.shape}  mismatch={bad}/{n}"
          f"  ({100.0 * (n - bad) / n:.4f}% exact)  maxabsdiff={int(np.abs(d).max())}")
    return bad


def main():
    d = np.load(NPZ)
    y = d["step4_setup_6290_pre_set0"]
    c1 = d["step4_setup_6290_pre_set1"]
    c2 = d["step4_setup_6290_pre_set2"]
    ey = d["step4_setup_6290_post_set0"]
    ec1 = d["step4_setup_6290_post_set1"]
    ec2 = d["step4_setup_6290_post_set2"]

    print(f"input  Y{y.shape} C1{c1.shape} C2{c2.shape}")
    print(f"rect   ({X0},{Y0})-({X1},{Y1})   W={W} H={H}")

    print("\n[vectorised setup_6290]  (a+b)>>1  truncating")
    gy, gc1, gc2 = setup_6290(y, c1, c2, X0, Y0, W, H, X1, Y1)
    total = report("Y", gy, ey) + report("C1", gc1, ec1) + report("C2", gc2, ec2)

    print("\n[scalar transcription]   (must agree with the vectorised one)")
    sy, sc1, sc2 = setup_6290_scalar(y, c1, c2, X0, Y0, W, H, X1, Y1)
    same = (np.array_equal(sy, gy) and np.array_equal(sc1, gc1)
            and np.array_equal(sc2, gc2))
    print(f"  scalar == vectorised : {same}")

    # counter-hypotheses for the chroma rule
    print("\n[counter-hypotheses for chroma]")
    rows = slice(Y0, Y0 + H)
    ev = slice(X0, X0 + W, 2)
    od = slice(X0 + 1, X0 + W, 2)
    a1 = c1[rows, ev].astype(np.uint32)
    b1 = c1[rows, od].astype(np.uint32)
    a2 = c2[rows, ev].astype(np.uint32)
    b2 = c2[rows, od].astype(np.uint32)
    cand = {
        "(a+b)>>1        (truncate)": ((a1 + b1) >> 1, (a2 + b2) >> 1),
        "(a+b+1)>>1      (round .5 up)": ((a1 + b1 + 1) >> 1, (a2 + b2 + 1) >> 1),
        "a               (take even)": (a1, a2),
        "b               (take odd)": (b1, b2),
    }
    for nm, (p1, p2) in cand.items():
        m1 = int(np.count_nonzero(p1.astype(np.uint16) != ec1))
        m2 = int(np.count_nonzero(p2.astype(np.uint16) != ec2))
        print(f"  {nm:32s} C1 mismatch={m1:7d}  C2 mismatch={m2:7d}")

    n_odd = int(np.count_nonzero(((a1 + b1) & 1) | ((a2 + b2) & 1)))
    print(f"\n  pairs whose sum is odd (where rounding mode is observable): "
          f"{n_odd} / {a1.size * 2}")

    print("\nRESULT: " + ("BIT EXACT" if total == 0 and same else "MISMATCH"))
    return 0 if (total == 0 and same) else 1


if __name__ == "__main__":
    sys.exit(main())
