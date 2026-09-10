r"""对着 ``marble_export_*.npz`` 的 ground truth 验证 fin_5f20 / blend_b010。

用 Windows 的 Python 跑:

    export PYTHONUTF8=1
    python sony_repro/tools/marble_ref/verify_fin_blend.py
"""

from __future__ import annotations

import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from blend_b010 import blend_b010          # noqa: E402
from fin_5f20 import fin_5f20              # noqa: E402

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAPTURES = ['marble-q3', 'marble-q1', 'marble-q2off']

# 有效区域(tile 的 valid region):行 24..608,列 24..1056
VY0, VY1, VX0, VX1 = 24, 608, 24, 1056


def _stat(name, got, ref):
    got = got.astype(np.int64)
    ref = ref.astype(np.int64)
    d = got - ref
    full_n = int((d != 0).sum())
    full_m = int(np.abs(d).max()) if d.size else 0
    v = d[VY0:VY1, VX0:VX1]
    v_n = int((v != 0).sum())
    v_m = int(np.abs(v).max()) if v.size else 0
    pct = 100.0 * (v.size - v_n) / v.size
    print('    %-4s valid: mismatch=%-7d maxabs=%-6d exact=%.4f%%   |   full: mismatch=%-7d maxabs=%d'
          % (name, v_n, v_m, pct, full_n, full_m))
    return v_n, full_n


def _steps(d, tag):
    out = set()
    for k in d.files:
        m = re.match(r'step(\d+)_' + tag + r'_', k)
        if m:
            out.add(int(m.group(1)))
    return sorted(out)


def verify_fin(d):
    ok = True
    for s in _steps(d, 'fin_5f20'):
        pre = [d['step%d_fin_5f20_pre_set%d' % (s, k)] for k in range(3)]
        post = [d['step%d_fin_5f20_post_set%d' % (s, k)] for k in range(3)]
        H, W = pre[0].shape
        out_h, out_w = post[0].shape
        x0 = (out_w - W) // 2
        y0 = (out_h - H) // 2
        print('  fin_5f20 step%d: src Y %dx%d, C %dx%d -> %dx%d, rect=(%d,%d,%d,%d) halfw=%d'
              % (s, W, H, pre[1].shape[1], pre[1].shape[0], out_w, out_h,
                 x0, y0, x0 + W, y0 + H, (W + 1) // 2))
        got = fin_5f20(pre[0], pre[1], pre[2], x0, y0, W, H, out_w, out_h)
        for name, gt, rf in zip(('Y', 'C1', 'C2'), got, post):
            v_n, _ = _stat(name, gt, rf)
            ok &= v_n == 0
        # rect 内部(含 8px 边框以内的全部被写区域)
        for name, gt, rf in zip(('Y', 'C1', 'C2'), got, post):
            sub_g = gt[y0:y0 + H, x0:x0 + 2 * ((W + 1) // 2)]
            sub_r = rf[y0:y0 + H, x0:x0 + 2 * ((W + 1) // 2)]
            n = int((sub_g.astype(np.int64) != sub_r.astype(np.int64)).sum())
            print('      %-3s written-rect mismatch=%d' % (name, n))
    return ok


def verify_blend(d, amount=1.0):
    ok = True
    for s in _steps(d, 'blend_b010'):
        if 'step%d_blend_b010_post_set0' % s not in d.files:
            continue
        y = d['step%d_blend_b010_pre_set0' % s]
        c1n = d['step%d_blend_b010_pre_arg0' % s]
        c1o = d['step%d_blend_b010_pre_arg1' % s]
        c2n = d['step%d_blend_b010_pre_arg2' % s]
        c2o = d['step%d_blend_b010_pre_arg3' % s]
        ref = [d['step%d_blend_b010_post_set%d' % (s, k)] for k in range(3)]
        print('  blend_b010 step%d: %dx%d, amount=%g' % (s, y.shape[1], y.shape[0], amount))
        got = blend_b010(y, c1n, c1o, c2n, c2o, amount)
        for name, gt, rf in zip(('R', 'G', 'B'), got, ref):
            v_n, _ = _stat(name, gt, rf)
            ok &= v_n == 0
    return ok


def fit_amount(d):
    """在有效区域里扫 amount,报告哪些值能做到 0 失配。"""
    for s in _steps(d, 'blend_b010'):
        if 'step%d_blend_b010_post_set0' % s not in d.files:
            continue
        y = d['step%d_blend_b010_pre_set0' % s]
        args = [d['step%d_blend_b010_pre_arg%d' % (s, k)] for k in range(4)]
        ref = [d['step%d_blend_b010_post_set%d' % (s, k)] for k in range(3)]
        rows = []
        for amount in [0.0, 0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875,
                       0.9, 0.95, 0.99, 0.999, 1.0]:
            got = blend_b010(y, args[0], args[1], args[2], args[3], amount)
            n = sum(int((g.astype(np.int64)[VY0:VY1, VX0:VX1]
                         != r.astype(np.int64)[VY0:VY1, VX0:VX1]).sum())
                    for g, r in zip(got, ref))
            rows.append((amount, n))
        print('  amount fit (step%d, 有效区域三平面失配总数):' % s)
        for amount, n in rows:
            print('      amount=%-6g mismatch=%d%s' % (amount, n, '   <== 完全吻合' if n == 0 else ''))
        return


def main():
    all_ok = True
    for tag in CAPTURES:
        path = os.path.join(TOOLS, 'marble_export_%s.npz' % tag)
        if not os.path.exists(path):
            print('== %s: 缺失,跳过' % tag)
            continue
        print('== %s ==' % tag)
        d = np.load(path)
        all_ok &= verify_fin(d)
        all_ok &= verify_blend(d, 1.0)
        print()
    print('== amount 拟合 (marble-q3) ==')
    fit_amount(np.load(os.path.join(TOOLS, 'marble_export_marble-q3.npz')))
    print()
    print('结论:有效区域全部位精确' if all_ok else '结论:仍有失配')
    return 0 if all_ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
