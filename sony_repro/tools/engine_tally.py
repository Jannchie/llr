r"""对**引擎自己的成品**做一次总账:每张的残差,并按 DRO 分组。

判据必须是 Edit.exe 的输出而不是机内 JPEG —— 目标是复刻 Edit,相机和 Edit 本来
就不必一致(实测两者差得比我们与 Edit 的差还大)。
像素按**引擎的**色度筛,不按我们的,否则换配置时点集会跟着变。

先用 dump_finals.py 抓好 tmp/final_<stem>.npz,再跑本工具。
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")
import e2e_pipeline as E  # noqa: E402
from engine_final_check import align, to_grid  # noqa: E402
from llr_worker.cli import read_exiftool_metadata  # noqa: E402

M = np.array([[0.299, 0.587, 0.114], [-0.168736, -0.331264, 0.5],
              [0.5, -0.418688, -0.081312]], np.float32)
FULL = 16383
TMP = Path("/home/jannchie/llr/tmp")
SRC = Path("/mnt/e/10960725")
def boxblur(x, k=9):
    pad = np.pad(x, ((k // 2, k // 2), (k // 2, k // 2), (0, 0)), mode="edge")
    cs = np.pad(pad.cumsum(0).cumsum(1), ((1, 0), (1, 0), (0, 0)))
    return (cs[k:, k:] - cs[:-k, k:] - cs[k:, :-k] + cs[:-k, :-k]) / (k * k)


def blur_rmse(a, b, ok):
    m = ok[..., None].repeat(3, -1)
    return float(np.sqrt(((boxblur(a) - boxblur(b))[m] ** 2).mean()) * 255)


def shot_meta(stem):
    """走 worker 自己的读取路径 —— 想知道管线看到了什么,就问管线,别另起一套。"""
    ex = read_exiftool_metadata(SRC / f"{stem}.ARW")
    return str(ex.get("CreativeStyle")), str(ex.get("DynamicRangeOptimizer") or "Off")


def main():
    stems = sys.argv[1:] or sorted(p.stem.replace("final_", "") for p in TMP.glob("final_*.npz"))
    rows = []
    for stem in stems:
        f = TMP / f"final_{stem}.npz"
        if not f.exists():
            continue
        eng = np.load(f)["ZcTaskSIMDMarble_out"]
        ok = eng.max(-1) > 0
        eng8 = np.clip(eng / FULL, 0, 1) * 255
        ours, _ = E.render(SRC / f"{stem}.ARW", "sony")
        rot, _ = align((ours * 255).astype(np.float32), eng8, eng.shape[:2])
        a = to_grid(rot, eng.shape[:2])[ok] / 255.0
        b = eng8[ok] / 255.0
        ra, rb = np.hypot(a @ M[1], a @ M[2]), np.hypot(b @ M[1], b @ M[2])
        ya = a @ M[0]
        sel = (rb > 0.08) & (ya > 0.06) & (ya < 0.9)
        dh = np.degrees(np.arctan2((b @ M[2])[sel], (b @ M[1])[sel])
                        - np.arctan2((a @ M[2])[sel], (a @ M[1])[sel]))
        # 颜色的中位数已经对上,剩下的 RMSE 是低频(颜色)还是高频(锐化/降噪/
        # 去马赛克)?两边同样模糊再比 —— 若 RMSE 塌下去,残差就是细节层的,
        # 那是另一类问题,不该继续按颜色去调。
        full = to_grid(rot, eng.shape[:2]) / 255.0
        rmse_lo = blur_rmse(full, eng8 / 255.0, ok)

        look, dro = shot_meta(stem)
        rows.append(dict(stem=stem, look=look, dro=dro, rmse_lo=rmse_lo,
                         chroma=float(np.median(rb[sel] / np.maximum(ra[sel], 1e-6))),
                         hue=float(np.median((dh + 180) % 360 - 180)),
                         luma=float(np.median((b @ M[0])[sel] - ya[sel])),
                         rmse=float(np.sqrt(((a - b) ** 2).mean()) * 255)))
        r = rows[-1]
        print("%-10s %-4s DRO=%-5s 色度 %.4f  色相 %+6.2f  亮度 %+.4f  RMSE %5.1f (模糊后 %4.1f)"
              % (stem, r["look"], r["dro"], r["chroma"], r["hue"], r["luma"],
                 r["rmse"], r["rmse_lo"]),
              flush=True)

    for label, group in (("DRO 关", [r for r in rows if r["dro"].lower() == "off"]),
                         ("DRO 开", [r for r in rows if r["dro"].lower() != "off"])):
        if not group:
            continue
        print(f"\n=== {label} ({len(group)} 张)")
        for k, ref in (("chroma", 1.0), ("hue", 0.0), ("luma", 0.0),
                       ("rmse", 0.0), ("rmse_lo", 0.0)):
            v = np.array([r[k] for r in group])
            print(f"  {k:<7} 中位 {np.median(v):+.4f}   中位绝对偏差 {np.median(np.abs(v - ref)):.4f}"
                  f"   范围 {v.min():+.4f}..{v.max():+.4f}")


if __name__ == "__main__":
    main()
