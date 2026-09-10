"""`detail` 的 gain 到底该不该从 `0x78cc` tag 算。

`rawnr_full_probe.py` 一次抓齐的四路里,gain **全是 256**。而 `denoise.py` 现在
是从 DetailRestore tag 的 fraction 算的:`round(fraction * 256)`,在 DSC03036 上
得 249。差 3% 的细节恢复量。

模块注释早就写着「SIMD 路径实测 256,而 tag 读到 480 和 268」,但那条结论没有落到
代码里 —— 这里把 tag 的值和引擎实测的值并排放出来判个明白。
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from llr_worker.sony.rawnr import detail_restore, noise_model

SAMPLES = Path("/home/jannchie/llr/samples")


def main() -> int:
    for name in ("fl_test", "DSC03036", "DSC01157", "DSC04568"):
        p = SAMPLES / f"{name}.ARW"
        if not p.exists():
            continue
        dr = detail_restore(p)
        nm = noise_model(p)
        frac = float(dr.fraction) if dr is not None else float("nan")
        print(f"  {name:<10} detail_restore={dr}  → gain=round(frac*256)="
              f"{round(frac * 256) if dr is not None else '—'}   noise={nm}")
    print("\n  引擎实测(rawnr_full_fl_test.npz 的四路):gain 全为 256。")
    print("  若 fl_test 的 tag 也算出 249 之类,就说明 gain 与 tag 无关,"
          "应当固定 256。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
