r"""worker 测试夹具:引擎 Marble tile 0 的一块 320x320(裁掉 8px 边后 rows/cols 100:420)。
    bash -lc "cd ~/llr/apps/worker && uv run python ../../sony_repro/tools/make_marble_fixture_worker.py"
"""
import json
import numpy as np
z = np.load('/home/jannchie/llr/sony_repro/tools/marble_export_marble-q3.npz')
info = json.loads(str(z['info']))
x0, y0, x1, y1 = [int(v) for v in info['in_meta']['meta'][12:16]]
sl = (slice(y0 + 100, y0 + 420), slice(x0 + 100, x0 + 420))
out = {
    'in': np.stack([z['in_set0'][sl], z['in_set1'][sl], z['in_set2'][sl]], -1),
    'out': np.stack([z['out_set0'][sl], z['out_set1'][sl], z['out_set2'][sl]], -1),
    'y_after_clarity': z['step25_attach_b640_post_set0'][100:420, 100:420],
    'source': np.array(['export:marble-q3 DSC03036 ILCE-7CM2 ISO 2000, ZcTaskSIMDMarble tile 0, inner-rect offset (100,100), NR auto, Clarity opts 10']),
}
np.savez_compressed('/home/jannchie/llr/apps/worker/tests/fixtures/marble_tile.npz', **out)
print({k: v.shape for k, v in out.items()})
