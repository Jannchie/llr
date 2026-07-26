r"""核对 RGB2YCC 八参数的三种来源:成品 0x7841 / 基准 0x7842 / 混合结果。

反汇编 `0x14036dce0` 给出两条路:
    calib+0x1c != 0  ->  直接 copy 标定块 +0xddc 的成品(= tag 0x7841)
    否则              ->  p[i] = base[i] + (sum d_k[i]*w_k) >> 10

偏移与标签一一对应(每个 8 short = 0x10 字节):
    0xddc=0x7841 成品   0xdec=0x7842 基准
    0xdfc/0xe0c/0xe1c/0xe2c = 0x7843..0x7846 四组增量
    0xe3c=0x7847        0xe44=0x7848 四个权重(short)

这个工具跑一遍素材,看成品与混合结果是否一致、我们只用基准差多少。
"""
import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")
from llr_worker.sony.chroma import unpack_params  # noqa: E402
from llr_worker.sony.sr2 import _decrypted_sr2, _find_tag  # noqa: E402

SRC = Path("/mnt/e/10960725")
FINAL, BASE = 0x7841, 0x7842
DELTAS = (0x7843, 0x7844, 0x7845, 0x7846)
WEIGHTS = (0x7847, 0x7848)


def shorts(dec, pos, endian, tag):
    e = _find_tag(dec, pos, endian, tag)
    if e is None:
        return None
    _, cnt, vpos, _ = e
    return np.array(struct.unpack_from(f"{endian}{cnt}h", dec, vpos), np.int64)


def main():
    stems = sys.argv[1:] or sorted(p.stem for p in SRC.glob("*.ARW"))
    same_final = diff = missing = 0
    for stem in stems:
        try:
            dec, pos, endian = _decrypted_sr2(SRC / f"{stem}.ARW")
        except KeyError:
            continue
        final, base = shorts(dec, pos, endian, FINAL), shorts(dec, pos, endian, BASE)
        if final is None or base is None:
            missing += 1
            continue
        d = np.stack([shorts(dec, pos, endian, t) for t in DELTAS])
        w = [shorts(dec, pos, endian, t) for t in WEIGHTS]
        # >> 是算术右移(向下取整),和 sar 一致 —— 不能用 //1024 之外的写法糊过去
        blend = base + ((d * w[1][:, None]).sum(0) >> 10)

        # 混合与成品差 ±1 时,解包后还看得见吗?gain=((p>>3)&0xff)/128,
        # 只有跨 8 的边界才会变 —— 若从来不变,那两条路等价,用混合就行。
        if not np.array_equal(unpack_params(blend), unpack_params(final)):
            print(f"{stem}: **解包后不等** blend={blend.tolist()} final={final.tolist()}")

        tag_note = "w7847==w7848" if np.array_equal(w[0], w[1]) else f"7847={w[0]} 7848={w[1]}"
        if np.array_equal(final, base):
            same_final += 1
            state = "成品==基准"
        else:
            diff += 1
            state = f"成品!=基准 最大差 {int(np.abs(final - base).max())}"
        agree = "混合==成品" if np.array_equal(blend, final) else \
            f"混合!=成品 差 {(final - blend).tolist()}"
        print(f"{stem}  w={w[1].tolist()}  {state}  {agree}  ({tag_note})", flush=True)

    print(f"\n成品==基准 {same_final} 张,不等 {diff} 张,缺标签 {missing} 张")


if __name__ == "__main__":
    main()
