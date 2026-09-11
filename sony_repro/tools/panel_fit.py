r"""把 panel_sweep.py 抓到的 tone LUT 对 llr 的 tone.apply_tuning 比,验证「面板/5」。

WSL 里跑(要 llr worker 的环境):
    cd apps/worker && uv run python ../../sony_repro/tools/panel_fit.py <ARW> <sweep.npz> [高光=-30 阴影=5 对比度=0]

后面的 面板=值 是扫描时**没在动的**那两个滑块停在哪(默认全 0);扫哪个滑块由
npz 里的键名决定(panel_sweep 的 `<标签><±值>.tone`)。每个点打印 面板/5 直接代入
的 max|diff|(应为 0),再用 0.02 档的网格找零差区间,看有没有别的解。
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "worker" / "src"))
from llr_worker.sony import sr2, tone  # noqa: E402
from llr_worker.sony.profile import PANEL_PER_STOP  # noqa: E402

FIELD = {"高光": "highlights", "阴影": "shadows", "对比度": "contrast"}


def main():
    arw, npz = sys.argv[1], sys.argv[2]
    fixed = {"highlights": 0.0, "shadows": 0.0, "contrast": 0.0}
    for a in sys.argv[3:]:
        k, v = a.split("=")
        if k in FIELD:
            fixed[FIELD[k]] = float(v) / PANEL_PER_STOP
    # 扫描是在拍摄时的外观上做的;DSC02961 是 FL(look=<代码> 可换)
    look = next((a.split("=")[1] for a in sys.argv[3:] if a.startswith("look=")), "FL")
    cal = {c.name: c for c in sr2.look_calibrations(arw)}[look]
    base = tone.base_curve(cal, tone.TONE_INDEX_WHITE + 1)
    z = np.load(npz)
    grid = np.arange(-25.0, 25.001, 0.02)
    for k in sorted(z.files):
        if not k.endswith(".tone"):
            continue
        label = k[:-5].rstrip("+-0123456789")
        if label not in FIELD:
            continue
        panel = float(k[len(label):-5])
        eng = z[k][: tone.TONE_INDEX_WHITE + 1].astype(np.float64)
        kw = dict(fixed)
        kw[FIELD[label]] = panel / PANEL_PER_STOP
        direct = np.abs(tone.apply_tuning(base, **kw) * 16384.0 - eng).max()
        zeros = []
        for g in grid:
            kw[FIELD[label]] = g
            if np.abs(tone.apply_tuning(base, **kw) * 16384.0 - eng).max() == 0:
                zeros.append(float(g))
        span = f"[{min(zeros):+.2f}, {max(zeros):+.2f}]" if zeros else "无"
        print(f"{label} 面板 {panel:+5.0f} -> 档位 {panel / PANEL_PER_STOP:+6.2f}  "
              f"max|diff| {direct:5.0f}/16384   零差档位区间 {span}", flush=True)


if __name__ == "__main__":
    main()
