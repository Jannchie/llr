"""坐实诊断:用户导出的那张 llr,是不是"降噪没开"的样子。

`aniso_stage` 已经量到离线链路上不降噪是 1.49、降噪是 0.93,而用户的成品是
1.87。方向对得上,但 1.87 与 1.49 之间还差 0.38,不能就此认定 —— 那 0.38 也
可能来自别的原因,而如果它来自别的原因,改降噪开关就修不好用户看到的东西。

所以这里把离线的两版**存成和用户那张同规格的 JPEG**,四张放在同一批 tile 位置
上一起量。要看的是曲线的**形状**:若不降噪那版与用户成品在各频带上同起同落,
剩下的常数差就是曝光等设置的事;若形状不同,那就是另一个原因,得回去重查。

⚠️ 四张图必须同尺寸、同 tile 位置,否则比的是画面不是算子。用户那两张已经
核对过是 7008x4672、同一张量化表(probe_user_jpgs.py)。离线渲的也是全尺寸,
但**没有裁剪与畸变校正**,所以画面内容与 web 端成品有几十像素的位移 ——
tile 位置一律取自用户的 llr 成品,离线两版按同坐标取,内容大体相同即可,
因为这里比的是噪声的方向性,不是逐点差。

用法::

    python aniso_confirm.py            # 默认 DSC03036
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
from PIL import Image

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")
sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")

from aniso import TILE, flat_tiles, spectrum  # noqa: E402
from aniso_freq import RINGS, ring_ratio  # noqa: E402
from aniso_stage import W601, find_arw, render  # noqa: E402

OUT = Path("/home/jannchie/llr/tmp")
NTILE = 16
USER_LLR = Path("/mnt/c/Users/Jannchie/Downloads/DSC03036-llr.jpg")
USER_OOC = Path("/mnt/c/Users/Jannchie/Downloads/DSC03036-直出.jpg")


def save_jpeg(rgb, path):
    """按用户那张的规格存 —— quality=100、无子采样,把编码器的贡献压到最小。"""
    a = np.clip(rgb * 255.0 + 0.5, 0, 255).astype(np.uint8)
    Image.fromarray(a).save(path, quality=100, subsampling=0)
    return path


def main():
    stem = sys.argv[1] if len(sys.argv) > 1 else "DSC03036"
    arw = find_arw(stem)
    if arw is None:
        raise SystemExit(f"找不到 {stem}.ARW")
    OUT.mkdir(parents=True, exist_ok=True)

    made = []
    for label, kw in [("offline-nodn", dict(denoise_model=None, chroma=True)),
                      ("offline-wavelet", dict(denoise_model="wavelet", chroma=True))]:
        p = OUT / f"{stem}-{label}.jpg"
        if not p.exists():
            save_jpeg(render(arw, **kw), p)
        made.append(p)

    paths = [USER_LLR, made[0], made[1], USER_OOC]
    ys, names = [], []
    for p in paths:
        if not p.exists():
            print(f"缺 {p}")
            continue
        ys.append(np.asarray(Image.open(p).convert("RGB"), np.float32) @ W601)
        names.append(p.name)

    # tile 位置取自用户的成品,其余三张按同坐标读。
    tiles = flat_tiles(ys[0], n=NTILE)
    hdr = "  ".join(f"{f'{2/hi:.1f}-{2/lo:.1f}px':>11}" for lo, hi in RINGS)
    print(f"  {'图':<30} {hdr}")
    rows = []
    for name, y in zip(names, ys):
        specs = [spectrum(y[cy:cy + TILE, cx:cx + TILE]) for cy, cx in tiles]
        row = [float(np.median([ring_ratio(s, lo, hi)[0] for s in specs]))
               for lo, hi in RINGS]
        rows.append(row)
        print(f"  {name:<30} " + "  ".join(f"{v:11.2f}" for v in row))

    if len(rows) >= 2:
        a, b = np.array(rows[0]), np.array(rows[1])
        print(f"\n  用户成品 vs 离线不降噪:相关 {np.corrcoef(a, b)[0,1]:+.3f},"
              f" 均差 {float((a-b).mean()):+.2f}")
        if len(rows) >= 3:
            c = np.array(rows[2])
            print(f"  用户成品 vs 离线降噪  :相关 {np.corrcoef(a, c)[0,1]:+.3f},"
                  f" 均差 {float((a-c).mean()):+.2f}")
        print("\n  与「不降噪」高度相关而与「降噪」不相关 = 用户那张确实没开降噪。")
    print(f"\n  离线两版存在 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
