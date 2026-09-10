"""生成 apps/web/public/sony-lut3d.bin —— 浏览器端那张 3D 表。

和 `make_chroma_fixture.py` 一样,是"资产由脚本产出、脚本留在仓库里"的一环:
表本身是从引擎里 dump 出来的静态数据(见 notes/static-3dlut.md),worker 侧存成
`llr_worker/sony/data/lut3d_points.npz`,这里只是把它摊成 shader 能直接
`texImage3D` 的字节序。**只有 worker 的 npz 是真身**,重抓表之后先更新它,再跑这个。

布局(shader 的 `u_sony_lut3d` 就是按这个上传的):

    int16 小端,byte = ((iu*33 + iv)*33 + iy)*3 + c 处的两字节
    iu = u 轴(≈R−Y,曲线压缩后的 5 位整数),iv = v 轴(≈B−Y),iy = Y>>9
    c  = 0:Y'  1:u'  2:v'

也就是 numpy 的 C 序 `points[iu][iv][iy][c]` 原样落盘。上传时
`texImage3D(TEXTURE_3D, 0, RGB16I, 33, 33, 33, 0, RGB_INTEGER, SHORT, buf)`
—— width 是变化最快的 iy,depth 是最慢的 iu,所以纹理坐标是 (iy, iv, iu)。

用法: cd apps/worker && uv run python ../../sony_repro/tools/make_lut3d_asset.py
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")
from llr_worker.sony.lut3d import GRID_N, lut3d_points  # noqa: E402

OUT = Path("/home/jannchie/llr/apps/web/public/sony-lut3d.bin")


def main() -> int:
    points = lut3d_points()
    assert points.shape == (GRID_N, GRID_N, GRID_N, 3), points.shape
    # `<i2` 而不是 `points.tobytes()`:落盘的字节序必须是小端,而不是"跑这个脚本的
    # 机器碰巧是什么序"。
    buf = np.ascontiguousarray(points, "<i2").tobytes()
    OUT.write_bytes(buf)

    back = np.frombuffer(OUT.read_bytes(), "<i2").reshape(GRID_N, GRID_N, GRID_N, 3)
    assert np.array_equal(back, points), "写回读不一致"
    print(f"写出 {OUT}  ({len(buf)} 字节 = {GRID_N}^3 x 3 x int16)")
    print(f"  Y' ∈ [{points[..., 0].min()}, {points[..., 0].max()}]  "
          f"u' ∈ [{points[..., 1].min()}, {points[..., 1].max()}]  "
          f"v' ∈ [{points[..., 2].min()}, {points[..., 2].max()}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
