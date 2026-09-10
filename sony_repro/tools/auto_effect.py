"""Auto 在这张片上到底给多少 —— 以及那个强度够不够压住格纹。

`iso_strength` 在 ISO 400 以下是 0.4。llr 的 `amount` 是「噪声版与降噪版的
lerp」,所以 Auto 在低感光片子上只上四成降噪。这件事必须量,不能想当然:
用户看到的那张若是低 ISO,那么"打开 Auto"未必就把他看到的东西修掉,
而如果修不掉,这个默认值改了也白改。

按 lerp 的定义在线性域混合两次解码的结果 —— 和 `daemon_linear` 的 cli.py:565
是同一件事,只是在这里离线做,免得为了一个读数去起 daemon。

用法::

    python auto_effect.py [DSC03036 ...]
"""
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")
sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")

from aniso import TILE, flat_tiles, spectrum  # noqa: E402
from aniso_freq import RINGS, ring_ratio  # noqa: E402
from aniso_stage import W601, find_arw, render  # noqa: E402
from llr_worker.sony.rawnr_simd import iso_strength  # noqa: E402


def read_iso(path):
    out = subprocess.run(["exiftool", "-s3", "-ISO", str(path)],
                         capture_output=True, text=True).stdout.strip()
    return float(out.split()[0]) if out else None


def band(y, tiles):
    specs = [spectrum(y[cy:cy + TILE, cx:cx + TILE]) for cy, cx in tiles]
    return [float(np.median([ring_ratio(s, lo, hi)[0] for s in specs]))
            for lo, hi in RINGS]


def main():
    stems = sys.argv[1:] or ["DSC03036"]
    hdr = "  ".join(f"{f'{2/hi:.1f}-{2/lo:.1f}px':>11}" for lo, hi in RINGS)
    for stem in stems:
        arw = find_arw(stem)
        if arw is None:
            print(f"跳过 {stem}")
            continue
        iso = read_iso(arw)
        s = iso_strength(iso) if iso else float("nan")
        print(f"\n=== {stem}  ISO {iso:.0f} -> Auto 强度 {s:.2f}"
              if iso else f"\n=== {stem}  ISO 读不到")

        noisy = render(arw, denoise_model=None, chroma=True)
        clean = render(arw, denoise_model="wavelet", chroma=True)
        tiles = flat_tiles((noisy * 255.0) @ W601, n=16)

        print(f"  {'amount':<22} {hdr}")
        for label, a in [("0.00(等于不降噪)", 0.0), (f"{s:.2f}(Auto)", s),
                         ("1.00(开满)", 1.0)]:
            mix = noisy * (1.0 - a) + clean * a
            row = band((mix * 255.0) @ W601, tiles)
            print(f"  {label:<22} " + "  ".join(f"{v:11.2f}" for v in row))
        print("  用户那张成品                        (同一批 tile 上量得 2.0-2.7px = 1.87)")
    print("\n  最右一列(2.0-2.7px)是格纹所在。1.00 = 各向同性。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
