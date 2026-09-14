"""生成 apps/web/public/sony-tone-family.bin —— 浏览器端重建色调曲线用的 37 条算子曲线。

和 make_lut3d_asset.py 一样,"资产由脚本产出、脚本留在仓库里":真身是 worker 的
`llr_worker/sony/data/tone_family.npz`(tone.py 头注释说它是什么、从哪来),这里只是
把它摊成前端能直接 `new Uint16Array(buf)` 的字节序,让创意外观的对比度/高光/阴影
三个滑块在浏览器里就能按引擎自己的构造重算曲线(rendering/sony-look.ts),不再
每动一下就向 worker 要一次 profile。

布局:uint16 小端,C 序 `family[gain][index]`,37 x 1025 = 75850 字节。

用法: cd apps/worker && uv run python ../../sony_repro/tools/make_tone_family_asset.py
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "worker" / "src"))
from llr_worker.sony.tone import TUNE_GAIN_ROWS, TUNE_TABLE_LEN, _family  # noqa: E402

OUT = ROOT / "apps" / "web" / "public" / "sony-tone-family.bin"


def main() -> int:
    family = _family()
    assert family.shape == (TUNE_GAIN_ROWS, TUNE_TABLE_LEN), family.shape
    assert np.array_equal(family, np.round(family)) and family.min() >= 0 and family.max() <= 65535
    buf = np.ascontiguousarray(family, "<u2").tobytes()
    OUT.write_bytes(buf)

    back = np.frombuffer(OUT.read_bytes(), "<u2").reshape(TUNE_GAIN_ROWS, TUNE_TABLE_LEN)
    assert np.array_equal(back, family), "写回读不一致"
    print(f"写出 {OUT}  ({len(buf)} 字节 = {TUNE_GAIN_ROWS} x {TUNE_TABLE_LEN} x uint16)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
