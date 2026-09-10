"""demosaic 到底占了 llr 与 Edit 剩余差距的多少 —— 换算法看它动多少。

现状:降噪算子已经复刻到 float32 的极限(红蓝逐位 100.0000%,绿色 99.994%,残差
全部落在阈值边界内)。可成品与 Edit 仍差 |Δ| 0.0209(已校正几何),其中约六成能被
两边各自的噪声解释,剩下约 0.008 没有着落。

头号嫌疑是 demosaic:llr 走 LibRaw 的 AHD,Edit 走 `ZcTaskSIMDITP`。但复刻 ITP 是
一个不亚于 RawNR 的独立工程,投进去之前先量清楚它值多少 —— **换掉 demosaic
算法**,看差距动不动。

  * 换算法差距明显变化 => demosaic 是主因,ITP 值得做;
  * 几乎不动 => 剩下的 0.008 在别处,先别碰 ITP。

⚠️ 这里比的是**同一条 rawpy 管线内换算法**,不是 llr 的完整链路 —— 目的是看
"demosaic 这一步的选择能带来多大差异",不是求绝对差距。所以三种算法都用同一组
postprocess 参数,只有 demosaic_algorithm 不同。
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import rawpy
from PIL import Image

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")
sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")

from aniso_stage import find_arw  # noqa: E402
from lens_apply import apply_distortion, parse_lens_corr  # noqa: E402

REC601 = np.array([0.299, 0.587, 0.114], np.float32)
ALGOS = [("AHD(llr 现用)", rawpy.DemosaicAlgorithm.AHD),
         ("VNG", rawpy.DemosaicAlgorithm.VNG),
         ("DHT", rawpy.DemosaicAlgorithm.DHT),
         ("LINEAR", rawpy.DemosaicAlgorithm.LINEAR)]


def main() -> int:
    stem = sys.argv[1] if len(sys.argv) > 1 else "DSC03036"
    arw = find_arw(stem)
    jpg = Path(str(arw).replace(".ARW", ".JPG"))
    edit = np.asarray(Image.open(jpg).convert("RGB"), np.float32) / 255.0
    ey = edit @ REC601
    from llr_worker.cli import read_exiftool_metadata, sony_lens_corrections
    lc = parse_lens_corr(sony_lens_corrections(read_exiftool_metadata(arw)))
    print(f"{stem}  Edit 导出 {edit.shape}\n")
    print("  算法              |Δ亮度| 均值   相对 AHD")

    base = None
    for label, algo in ALGOS:
        try:
            with rawpy.imread(str(arw)) as raw:
                rgb = raw.postprocess(
                    demosaic_algorithm=algo, use_camera_wb=True,
                    no_auto_bright=True, output_bps=16)
        except Exception as exc:  # noqa: BLE001 — 少一种算法不该拖垮整份报告
            print(f"    {label:<16} 跑不出来 {exc}")
            continue
        img = rgb.astype(np.float32) / 65535.0
        if lc is not None:
            img, _ = apply_distortion(img, lc["distortion"])
        h = min(img.shape[0], edit.shape[0])
        w = min(img.shape[1], edit.shape[1])
        y = (img[:h, :w] @ REC601)
        # 只比亮度,且各自减去中位数 —— 这条 rawpy 管线的色调与 llr/Edit 都不同,
        # 要看的是**结构差异**,不是整体明暗。
        d = float(np.mean(np.abs((y - np.median(y))
                                 - (ey[:h, :w] - np.median(ey[:h, :w])))))
        if base is None:
            base = d
        print(f"    {label:<16} {d:.5f}      {d / base:6.3f}x")

    print("\n  各算法之间差得远 => demosaic 是剩余差距的主因,ITP 值得做;"
          "\n  差不多 => 剩下的 0.008 不在 demosaic,先别碰 ITP。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
