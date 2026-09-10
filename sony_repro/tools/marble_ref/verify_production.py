r"""产品版 sony/marble.py 对引擎 tile:裁掉 8 px 边后整块处理,亮度用引擎 Clarity 之后的平面,比较有效区。
    bash -lc "cd ~/llr/apps/worker && uv run python ../../sony_repro/tools/marble_ref/verify_production.py"
"""
import json, sys, time
import numpy as np
from llr_worker.sony import marble as M

z = np.load('/home/jannchie/llr/sony_repro/tools/marble_export_marble-q3.npz')
info = json.loads(str(z['info']))
x0, y0, x1, y1 = [int(v) for v in info['in_meta']['meta'][12:16]]
sl = (slice(y0, y1), slice(x0, x1))
r, g, b = z['in_set0'][sl], z['in_set1'][sl], z['in_set2'][sl]
t = time.time()
params = M.slider_params(M.CALIB_7CM2, 5)
rw, gw, bw = M.gamut_fwd(r, g, b)
y, c1, c2 = M.rgb_to_ycc(rw, gw, bw)
c1n, c2n = M.marble_ycc_planes(y, c1, c2, params)
print('chain time', f'{time.time()-t:.2f}s', 'amount', M.blend_amount(2000, 5))
# 与引擎的半分辨率清理结果(cnr4 出口)比:b2 -> set1(C1), b1 -> set2(C2)
eng_c1 = np.repeat(z['step14_cnr4_ea10_post_b2'], 2, axis=1); eng_c2 = np.repeat(z['step14_cnr4_ea10_post_b1'], 2, axis=1)
v = (slice(16, 600), slice(16, 1048))
for nm, a, e in (('C1 clean', c1n, eng_c1), ('C2 clean', c2n, eng_c2)):
    d = np.abs(a[v].astype(np.int32) - e[v].astype(np.int32)); print(nm, 'mismatch', (d>0).sum(), '/', d.size, 'max', d.max())
yc = z['step25_attach_b640_post_set0']
ro, go, bo = M.ycc_to_rgb(yc, c1n, c2n)   # amount 1.0
ro, go, bo = M.gamut_inv(ro, go, bo)
for nm, a, k in (('R', ro, 'out_set0'), ('G', go, 'out_set1'), ('B', bo, 'out_set2')):
    e = z[k][sl]
    d = np.abs(a[v].astype(np.int32) - e[v].astype(np.int32)); print(nm, 'mismatch', (d>0).sum(), '/', d.size, 'max', d.max(), f'({100*(d==0).mean():.4f}%)')
