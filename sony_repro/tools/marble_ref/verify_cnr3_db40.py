import sys, os
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cnr3_db40 import cnr3_db40

NPZ = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'marble_export_marble-q3.npz')
z = np.load(NPZ)
ctx = np.frombuffer(z['step12_cnr3_db40_pre_ctx'].tobytes(), dtype=np.int32)
params = dict(W=int(ctx[0x28 // 4]), H=int(ctx[0x2c // 4]), p54=int(ctx[0x54 // 4]), p58=int(ctx[0x58 // 4]))
print('ctx params', params, 'ctx changed:', bool((z['step12_cnr3_db40_pre_ctx'] != z['step12_cnr3_db40_post_ctx']).any()))
ins = [z[f'step12_cnr3_db40_pre_set{k}'] for k in range(3)]
gts = [z[f'step12_cnr3_db40_post_set{k}'] for k in range(3)]
outs = cnr3_db40(*ins, params, factor=4)
H6, W6 = ins[0].shape
core = (slice(6, H6 - 6 + 6 - 0), slice(6, W6))  # placeholder, fixed below
W = params['W']; H = params['H']
xe = (W - 1 + 4) // 4 + 6; ye = (H - 1 + 4) // 4 + 6
core = (slice(6, ye), slice(6, xe))
ok = True
for k in range(3):
    o = outs[k].astype(np.int64); g = gts[k].astype(np.int64)
    d = np.abs(o - g)
    mm = int((d[core] != 0).sum()); n = d[core].size
    print(f'plane{k}: core mismatches {mm}/{n} ({100.0*(n-mm)/n:.4f}% exact), max|diff| {int(d[core].max())}')
    # border
    bmask = np.ones(g.shape, bool); bmask[core] = False
    print(f'        border: gt min/max {int(g[bmask].min())}/{int(g[bmask].max())}, our min/max {int(o[bmask].min())}/{int(o[bmask].max())}, mismatches {int((d[bmask]!=0).sum())}')
    if k > 0 and mm: ok = False
print('BIT-EXACT (chroma planes, core):', ok)
