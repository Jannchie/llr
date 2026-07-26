r"""接上 YGamma,对**引擎自己的成品**是变好还是变坏?

判据必须是 Edit.exe 的输出,不是机内 JPEG —— 目标是复刻 Edit,相机和 Edit 本来就
不必一致。像素按**引擎的**色度筛,两种配置才比同一批点(这个坑踩过一次)。

YGamma 逐位复刻式(ygamma_verify.py 已证 100%):
    y = trunc(clamp(lut[Y] * contrast, 0, 16383))
lut 与恒等差 <=16/16383,这里按恒等近似,只留 contrast=1.0546875。

用法: python ygamma_impact.py DSC02961 DSC03015
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import e2e_pipeline as E  # noqa: E402
from engine_final_check import align, to_grid  # noqa: E402

M = np.array([[0.299, 0.587, 0.114], [-0.168736, -0.331264, 0.5],
              [0.5, -0.418688, -0.081312]], np.float32)
FULL = 16383
CONTRAST = 1.0546875          # 标定块 +0x91964,实测三张一致


def render(path, ygamma):
    """照 e2e_pipeline 的顺序,但在 YCC 域里多一步 YGamma(只动 Y)。

    `E.render(..., with_chroma=False)` 返回的已经是 **sRGB 编码后**的画面,
    而 RGB2YCC 正是跑在这个域里,所以直接拿来当 `e`,末尾也**不要**再解码 ——
    第一版在这里多解了一次,整整差一个 gamma,亮度差报成 +0.23。
    """
    e, cp = E.render(path, "sony", with_chroma=False)
    cross = np.asarray(cp["profileChromaCross"], np.float32)
    gain = np.asarray(cp["profileChromaGain"], np.float32)
    r, g, b = e[..., 0], e[..., 1], e[..., 2]
    y = (r * 2432 + g * 4864 + b * 896) / 8192
    u, v = r - g, b - g
    v2 = np.where(u >= 0, cross[1], cross[3]) * u + v
    u2 = np.where(v >= 0, cross[0], cross[2]) * v + u
    cr = np.clip(np.where(u2 >= 0, gain[1], gain[3]) * u2, -0.5, 0.5)
    cb = np.clip(np.where(v2 >= 0, gain[0], gain[2]) * v2, -0.5, 0.5)
    if ygamma:
        y = np.minimum(y * CONTRAST, 1.0)
    return np.clip(np.stack(
        [y + 1.4020 * cr, y - 0.7141 * cr - 0.3441 * cb, y + 1.7720 * cb], -1), 0, 1)


def main():
    for stem in sys.argv[1:]:
        z = np.load(f"/home/jannchie/llr/tmp/final_{stem}.npz")
        eng = z["ZcTaskSIMDMarble_out"]
        ok = eng.max(-1) > 0
        eng8 = np.clip(eng / FULL, 0, 1) * 255
        print(f"\n=== {stem}  (对引擎成品)")
        for label, yg in (("不接 YGamma", False), ("接上 YGamma", True)):
            ours = render(Path(f"/mnt/e/10960725/{stem}.ARW"), yg)
            rot, _ = align((ours * 255).astype(np.float32), eng8, eng.shape[:2])
            a = to_grid(rot, eng.shape[:2])[ok] / 255.0
            b = eng8[ok] / 255.0
            rb = np.hypot(b @ M[1], b @ M[2])
            ya = a @ M[0]
            sel = (rb > 0.08) & (ya > 0.06) & (ya < 0.9)      # 按引擎的色度筛
            ra = np.hypot(a @ M[1], a @ M[2])
            dh = np.degrees(np.arctan2((b @ M[2])[sel], (b @ M[1])[sel])
                            - np.arctan2((a @ M[2])[sel], (a @ M[1])[sel]))
            print("  %-12s 色度比 %.4f   色相 %+.2f 度   亮度差 %+.4f   RMSE %.1f/255"
                  % (label, np.median(rb[sel] / np.maximum(ra[sel], 1e-6)),
                     np.median((dh + 180) % 360 - 180),
                     np.median((b @ M[0])[sel] - ya[sel]),
                     np.sqrt(((a - b) ** 2).mean()) * 255))


if __name__ == "__main__":
    main()
