"""Verify cnr1_b760() against the captured ground truth of Edit.exe 0x14038b760."""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cnr1_b760 import cnr1_b760, cnr1_b760_float_simd  # noqa: E402

NPZ = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "marble_export_marble-q3.npz")


def main():
    d = np.load(NPZ)

    pre_ctx = d["step6_cnr1_b760_pre_ctx"]
    post_ctx = d["step6_cnr1_b760_post_ctx"]
    diff = np.nonzero(pre_ctx != post_ctx)[0]
    if len(diff) == 0:
        print("ctx: unchanged (160/160 bytes identical) -- function writes no ctx field")
    else:
        print("ctx: %d bytes differ at offsets %s" % (len(diff), diff.tolist()))
        i32p = pre_ctx.view(np.int32)
        i32q = post_ctx.view(np.int32)
        for off in sorted(set(int(o) // 4 * 4 for o in diff)):
            print("   +0x%02x: %d -> %d" % (off, i32p[off // 4], i32q[off // 4]))

    y = d["step6_cnr1_b760_pre_set0"]
    c1 = d["step6_cnr1_b760_pre_set1"]
    c2 = d["step6_cnr1_b760_pre_set2"]
    ref = [d["step6_cnr1_b760_post_set%d" % k] for k in range(3)]

    print("\ninput : Y %s  C1 %s  C2 %s" % (y.shape, c1.shape, c2.shape))
    print("expect: " + "  ".join(str(r.shape) for r in ref))

    got = cnr1_b760(y, c1, c2, factor=4)
    print("\n--- integer path (scalar-tail semantics) ---")
    ok = True
    for k, (g, r) in enumerate(zip(got, ref)):
        if g.shape != r.shape:
            print("plane %d: SHAPE MISMATCH %s vs %s" % (k, g.shape, r.shape))
            ok = False
            continue
        bad = int(np.count_nonzero(g != r))
        mx = int(np.abs(g.astype(np.int64) - r.astype(np.int64)).max())
        pct = 100.0 * (g.size - bad) / g.size
        print("plane %d %s: mismatches=%d/%d  max|diff|=%d  exact=%.4f%%"
              % (k, g.shape, bad, g.size, mx, pct))
        ok &= bad == 0

    gotf = cnr1_b760_float_simd(y, c1, c2, factor=4)
    print("\n--- float32 AVX2 transcription (cross-check) ---")
    for k, (g, r) in enumerate(zip(gotf, ref)):
        bad = int(np.count_nonzero(g != r))
        mx = int(np.abs(g.astype(np.int64) - r.astype(np.int64)).max())
        print("plane %d: mismatches=%d/%d  max|diff|=%d" % (k, bad, g.size, mx))
        ok &= bad == 0

    zeros = sum(int(np.count_nonzero(r == 1)) for r in ref)
    print("\noutput samples equal to 1 (possible zero->1 clamps): %d" % zeros)

    print("\nRESULT: %s" % ("BIT-EXACT (100.0000%)" if ok else "MISMATCH"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
