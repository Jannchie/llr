"""Verify marble_ref.cnr2_c480 against the captured ground truth.

    python marble_ref/verify_cnr2_c480.py [path/to/marble_export_*.npz]

Compares plane1 / plane2 of step10_cnr2_c480_post_set* on the region the
binary actually writes (rows 4..hs+7, cols 4..ws+7) and reports what the
never-written padding / plane0 contain.
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cnr2_c480 import cnr2_c480, ctx_params_from_bytes, written_region  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
NPZ = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, '..', 'marble_export_marble-q3.npz')
STEP = sys.argv[2] if len(sys.argv) > 2 else 'step10'


def report(name, got, exp):
    d = got.astype(np.int64) - exp.astype(np.int64)
    bad = np.count_nonzero(d)
    tot = d.size
    print(f'{name}: mismatches {bad}/{tot} ({100.0 * (tot - bad) / tot:.4f}% exact), '
          f'max |diff| {np.abs(d).max()}')
    if bad:
        yy, xx = np.nonzero(d)
        print(f'   first mismatches (row,col,got,exp): '
              f'{[(int(a), int(b), int(got[a, b]), int(exp[a, b])) for a, b in list(zip(yy, xx))[:8]]}')
    return bad


def main():
    z = np.load(NPZ)
    pre = [z[f'{STEP}_cnr2_c480_pre_set{i}'] for i in range(3)]
    post = [z[f'{STEP}_cnr2_c480_post_set{i}'] for i in range(3)]
    ctx_pre = z[f'{STEP}_cnr2_c480_pre_ctx']
    ctx_post = z[f'{STEP}_cnr2_c480_post_ctx']
    params = ctx_params_from_bytes(ctx_pre)
    print('ctx params:', params)
    diff = np.nonzero(ctx_pre != ctx_post)[0]
    print('ctx bytes changed by the call:', diff.tolist() if diff.size else 'none')

    y_out, c1_out, c2_out = cnr2_c480(pre[0], pre[1], pre[2], params, factor=4)
    rs, cs = written_region(pre[0].shape, 4, params)
    print(f'written region: rows {rs.start}..{rs.stop - 1}, cols {cs.start}..{cs.stop - 1}')

    total_bad = 0
    total_bad += report('plane1 (C1) core', c1_out[rs, cs], post[1][rs, cs])
    total_bad += report('plane2 (C2) core', c2_out[rs, cs], post[2][rs, cs])

    # what is in the parts the function never writes?
    mask = np.ones(pre[0].shape, bool)
    mask[rs, cs] = False
    for i in range(3):
        p = post[i]
        same_as_in = np.array_equal(p[mask], pre[i][mask])
        print(f'plane{i} never-written ring ({mask.sum()} px): all zero = {not p[mask].any()}, '
              f'equal to input = {same_as_in}, range {p[mask].min()}..{p[mask].max()}')
    print(f'plane0 (Y) whole plane: equal to input Y = {np.array_equal(post[0], pre[0])}, '
          f'range {post[0].min()}..{post[0].max()} (input {pre[0].min()}..{pre[0].max()})')
    print('RESULT:', 'BIT-EXACT' if total_bad == 0 else f'{total_bad} mismatches')


if __name__ == '__main__':
    main()
