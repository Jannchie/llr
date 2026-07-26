r"""引擎解包的那 276 字节,到底藏在解密后的 SR2SubIFD 的哪里?

十种外观的 tag `0x780f` 都对不上(前 24 字节头部全同,从系数区起分歧),
所以要么它在别的 tag 里、要么根本不在文件里(是算出来的)。
先在**整个解密后的字节流**里直接搜 —— 搜得到就是读错了 tag,搜不到才需要找算法。

用法: python find276.py <ARW> <src276.npz>
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sony_repro.sr2 import data_ifds, read_ifd, read_sr2_tag  # noqa: E402

LOOK_ORDER = ("ST", "VV", "NT", "PT", "FL", "VV2", "IN", "SH", "BW", "SE")


def decrypted_stream(arw):
    """read_sr2_tag 内部会解密整段;这里借它的路子拿到整段明文。"""
    import inspect

    src = inspect.getsource(read_sr2_tag)
    print("read_sr2_tag 的实现:\n" + "\n".join("    " + ln for ln in src.splitlines()[:40]))
    return None


def main():
    arw = Path(sys.argv[1])
    eng = np.load(sys.argv[2])["block_0"]
    target = bytes(eng)

    # 先看看各外观的 0x780f 与目标的差在哪一段
    ifds = data_ifds(arw)
    print("目标 276 字节  头 24: %s" % list(target[:24]))
    for i, name in enumerate(LOOK_ORDER):
        v = ifds[i].get(0x780F)
        if v is None:
            continue
        a = np.frombuffer(bytes(v), np.uint8)
        d = np.flatnonzero(a != eng)
        print("  %-3s 不同的字节数 %3d,范围 %s..%s"
              % (name, len(d), d.min() if len(d) else "-", d.max() if len(d) else "-"))

    # 再在每个 IFD 的全部 tag 里找有没有别的 276 字节块
    print("\n各 IFD 里其它长度 >= 276 的 tag:")
    for i, ifd in enumerate(ifds):
        for t, v in sorted(ifd.items()):
            if isinstance(v, (bytes, bytearray)) and len(v) >= 276 and t != 0x780F:
                a = np.frombuffer(bytes(v[:276]), np.uint8)
                print("  IFD%d(%s) tag %s len=%d  前24相同=%s"
                      % (i, LOOK_ORDER[i], hex(t), len(v), bool((a[:24] == eng[:24]).all())))
        if i == 0:
            print("  (只列第 0 个 IFD 的,其余同构)")
            break

    decrypted_stream(arw)


if __name__ == "__main__":
    main()
