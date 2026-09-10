r"""同一块裁片,两种 demosaic 各渲一次并排存 PNG:肉眼验收用。

左:LibRaw AHD(旧路径,LLR_DEMOSAIC=libraw);右:Sony ITP(新默认)。两边都关掉
RAW 降噪(passthrough),色度清理按默认开着 —— 只看 demosaic 这一环带来的差别。
默认裁 notes §7 里用户报「编织纹」的那一块(DSC01157,全分辨率 (2100, 3300) 起 240x240),
另存一份 3 倍放大。

用法::

    bash run_py.sh itp_ab_crop.py [stem] [y] [x] [size]
"""
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
from PIL import Image

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")
sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")

from aniso_stage import find_arw, render  # noqa: E402
import llr_worker.cli as cli  # noqa: E402

TMP = Path("/home/jannchie/llr/tmp")


def main() -> int:
    stem = sys.argv[1] if len(sys.argv) > 1 else "DSC01157"
    y = int(sys.argv[2]) if len(sys.argv) > 2 else 2100
    x = int(sys.argv[3]) if len(sys.argv) > 3 else 3300
    n = int(sys.argv[4]) if len(sys.argv) > 4 else 240
    arw = find_arw(stem)
    if arw is None:
        print(f"找不到 {stem}.ARW")
        return 1
    crops = []
    for which in ("libraw", "itp"):
        cli.SONY_DEMOSAIC = which
        img = np.asarray(render(arw, denoise_model="passthrough", chroma=True), np.float32)
        crop = np.clip(img[y:y + n, x:x + n] * 255.0 + 0.5, 0, 255).astype(np.uint8)
        crops.append(crop)
        print(f"  {which}: 整幅 {img.shape}, 裁片均值 {crop.mean():.1f}")
    side = np.concatenate(crops, axis=1)
    out = TMP / f"{stem}-itp-ab-{y}-{x}.png"
    Image.fromarray(side).save(out)
    Image.fromarray(side).resize((side.shape[1] * 3, side.shape[0] * 3), Image.NEAREST).save(
        TMP / f"{stem}-itp-ab-{y}-{x}-x3.png")
    print(f"  写到 {out}(左 LibRaw,右 ITP)及 3 倍放大版")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
