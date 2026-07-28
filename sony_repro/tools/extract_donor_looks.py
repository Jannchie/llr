"""把捐赠机身独有的创意外观抽成数据文件,供没有它们的机身借用。

为什么可行、以及边界在哪 —— 拿 ILCE-7M5(a7 V,12 个外观)与 ILCE-7CM2(10 个)
逐字节比出来的:

* **色调曲线 `0x7805`/`0x7806` 跨机身逐字节相同**(十个共有外观全部命中)。
  曲线是索尼的艺术定义,不是逐机身标定 —— 所以搬过来是精确的。
* **色度参数 `0x7842`/`0x7844`/`0x7845` 逐机身不同**(BW 除外,两边全零)。
  借用捐赠机身的那份,相对目标机身"本该写的值"RMS 差约 52(幅度约 700,即 ~7%)。
* 试过"相对移植"(以 Standard 作锚点搬差值),**反而更差**(RMS 62 vs 52),
  因为两台机身的差不是与外观无关的常量(各列极差最高 246,与差值同量级)。
  所以直接借用就是最好的简单做法,不要自作聪明。
* 分段矩阵 `0x780f` **绝不移植** —— 它在根层、逐张标定,永远取目标文件自己的。

用法:
    python extract_donor_looks.py <捐赠.ARW> [--out <npz>] [--only FL2 FL3]
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps/worker/src"))

from llr_worker.creative_style import normalize_style  # noqa: E402
from llr_worker.sony.sr2 import (  # noqa: E402
    CHROMA_BASE_TAG,
    CHROMA_ILLUMINANT_TAGS,
    CURVE_X_TAG,
    CURVE_Y_TAG,
    LOOK_INDEX_TAG,
    LOOK_NAME_TAG,
    _decrypted_sr2,
    _find_tag,
    _ifd_entries,
)

DEFAULT_OUT = Path(__file__).resolve().parents[2] / \
    "apps/worker/src/llr_worker/sony/data/donor_looks.npz"


def read_looks(path: Path):
    dec, pos, endian = _decrypted_sr2(path)
    e = _find_tag(dec, pos, endian, LOOK_INDEX_TAG)
    if e is None:
        raise SystemExit(f"{path.name}: 没有 0x74c0,不是带外观标定的 Sony RAW")
    _, count, vpos, _ = e
    offsets = struct.unpack_from(f"{endian}{count}I", dec, vpos)

    out = []
    for off in offsets:
        got = {}
        for tag, _typ, cnt, tpos, size in _ifd_entries(dec, off, endian):
            if tag in (CURVE_X_TAG, CURVE_Y_TAG):
                got[tag] = np.array(struct.unpack_from(f"{endian}{cnt}i", dec, tpos), dtype=np.int64)
            elif tag in (CHROMA_BASE_TAG, *CHROMA_ILLUMINANT_TAGS):
                got[tag] = np.array(struct.unpack_from(f"{endian}{cnt}h", dec, tpos), dtype=np.int64)
            elif tag == LOOK_NAME_TAG:
                got[tag] = dec[tpos:tpos + size]
        name = bytes(got.get(LOOK_NAME_TAG, b"")).split(b"\x00")[0].decode("ascii", "replace")
        out.append((normalize_style(name) or name, got))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("donor", type=Path)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--only", nargs="*", default=None,
                    help="只抽这些外观代码;默认抽全部,由调用方决定用哪些")
    args = ap.parse_args()

    looks = read_looks(args.donor)
    print(f"{args.donor.name}: {len(looks)} 个外观 -> {[c for c, _ in looks]}")

    wanted = args.only
    payload: dict[str, np.ndarray] = {}
    names = []
    zero = np.zeros(8, dtype=np.int64)
    for code, got in looks:
        if wanted and code not in wanted:
            continue
        if CURVE_X_TAG not in got or CHROMA_BASE_TAG not in got:
            print(f"  跳过 {code}:缺曲线或色度")
            continue
        names.append(code)
        payload[f"{code}_curve_x"] = got[CURVE_X_TAG]
        payload[f"{code}_curve_y"] = got[CURVE_Y_TAG]
        payload[f"{code}_chroma_base"] = got[CHROMA_BASE_TAG]
        payload[f"{code}_chroma_deltas"] = np.stack(
            [got.get(t, zero) for t in CHROMA_ILLUMINANT_TAGS])

    if not names:
        raise SystemExit("没抽到任何外观")
    payload["names"] = np.array(names)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(args.out, **payload)
    print(f"写出 {args.out} ({args.out.stat().st_size} 字节),含 {names}")


if __name__ == "__main__":
    main()
