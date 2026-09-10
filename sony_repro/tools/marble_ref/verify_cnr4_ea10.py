r"""Verify marble_ref/cnr4_ea10.py against the captured ground truth.

    export PYTHONUTF8=1; python marble_ref/verify_cnr4_ea10.py [npz]
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cnr4_ea10 import cnr4_ea10, ctx_params_from_bytes  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
NPZ = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, '..', 'marble_export_marble-q3.npz')


def report(name, got, ref, mask=None):
    got = got.astype(np.int64)
    ref = ref.astype(np.int64)
    d = np.abs(got - ref)
    if mask is not None:
        d = d[mask]
    n = d.size
    bad = int((d != 0).sum())
    print('  %-34s mismatches %7d / %d (%.4f%% exact)  max|diff| %d' % (name, bad, n, 100.0 * (n - bad) / n, int(d.max()) if n else 0))
    return bad


def main():
    z = np.load(NPZ)
    pre = 'step14_cnr4_ea10_pre_'
    post = 'step14_cnr4_ea10_post_'
    in0, in1, in2 = (z[pre + 'set%d' % k] for k in range(3))
    tmp = z[pre + 'tmp']
    c1_orig = z['step6_cnr1_b760_pre_set1']
    ref_b2, ref_b1 = z[post + 'b2'], z[post + 'b1']
    params = ctx_params_from_bytes(z[pre + 'ctx'])
    print('ctx params:', params)
    post_ctx = np.asarray(z[post + 'ctx']).tobytes()
    r1_post = int.from_bytes(post_ctx[0x7c:0x80], 'little', signed=True)
    r2_post = int.from_bytes(post_ctx[0x80:0x84], 'little', signed=True)

    H, W2 = ref_b2.shape

    # 1) plain run: uninitialised temp planes assumed zero
    b2, b1 = cnr4_ea10(in0, in1, in2, c1_orig, tmp, params)
    print('ctx side effects: +0x7c got %d (engine %d), +0x80 got %d (engine %d)' % (params['r1_0x7c'], r1_post, params['r2_0x80'], r2_post))
    print('[A] temp planes initialised to 0 (engine: uninitialised heap memory)')
    report('b2 full', b2, ref_b2)
    report('b1 full', b1, ref_b1)

    # region that the engine actually computes (rows >= 2, cols >= 1)
    mask = np.ones((H, W2), bool)
    mask[:2, :] = False
    mask[:, 0] = False
    print('[B] excluding rows 0..1 and column 0 (never written by the upsampler -> heap garbage)')
    report('b2 computed region', b2, ref_b2, mask)
    report('b1 computed region', b1, ref_b1, mask)

    # 2) inject the heap garbage of U1 recovered from the engine output (b2 == U1 verbatim)
    u1_init = np.zeros((H, W2 + 12), np.int64)
    u1_init[:2, :W2] = ref_b2[:2]
    u1_init[:, 0] = ref_b2[:, 0]
    b2g, b1g = cnr4_ea10(in0, in1, in2, c1_orig, tmp, params, u1_init=u1_init)
    print('[C] U1 garbage (rows 0..1, col 0) taken from engine b2; U2 garbage still unknown (=0)')
    report('b2 full', b2g, ref_b2)
    report('b1 full', b1g, ref_b1)
    d = (b1g.astype(np.int64) != ref_b1.astype(np.int64))
    print('  b1 remaining mismatch positions: rows0-1: %d, col0: %d, elsewhere: %d' % (int(d[:2].sum()), int(d[2:, 0].sum()), int(d[2:, 1:].sum())))

    # 3) coverage diagnostic: how often is the blend (alpha > 0) actually active in this capture?
    from cnr4_ea10 import upsample_plane
    U1 = upsample_plane(in1, H, W2, 4)[:, :W2]
    U2 = upsample_plane(in2, H, W2, 4)[:, :W2]
    r1, r2 = params['r1_0x7c'], params['r2_0x80']
    s1 = 0x800000 - np.clip((np.abs(U1 - 32768) - (params['lo1'] << 8)) * r1, 0, 0x800000)
    c2 = np.clip((U2 - 32768 - (params['lo2'] << 8)) * r2, 0, 0x800000)
    k = (np.minimum(s1, c2) + 0x8000) >> 16
    print('[D] blend coverage: pixels with alpha>0 (k>0) in computed region = %d / %d ; k max = %d' % (int((k[mask] > 0).sum()), int(mask.sum()), int(k[mask].max())))
    if not (k[mask] > 0).any():
        print('    -> this capture never triggers the tmp/U2 blend (b1 == U2 everywhere); the alpha path is decoded from the disassembly only.')


if __name__ == '__main__':
    main()
