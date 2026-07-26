r"""改写 ARW 里**加密的** SR2SubIFD 标定,再原样加回去。

为什么需要它:改 MakerNotes 的外观字段只换得动 tone 曲线,换不动颜色 —— 引擎的
`0x14036dce0` 看到 `calib+0x1c != 0` 就直接抄根层那份"相机已混合好的"八个 short
(`0x7841`),压根不看你选了哪个外观。所以"把某张 FL 的照片当成 Sepia 渲染"这种
实验,必须改根层标定本身,而它在加密段里。

好在 Sony 那套加密是 **XOR 流**:`sr2.decrypt` 跑两遍就回到原文,于是
解密 -> 就地改 -> 再跑一遍 -> 写回,长度不变、IFD 偏移不变。

用法:
    python patch_sr2.py <源> <目标> --from-look 9        # 把根层换成第 9 份外观的标定
    python patch_sr2.py <源> <目标> --tag 0x780e=16384,16384,...
    python patch_sr2.py <源> <目标> --rename 0x7841=0x78fe   # 把某个标签藏起来
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")
sys.path.insert(0, r"\\wsl.localhost\Ubuntu-24.04\home\jannchie\llr\apps\worker\src")
from llr_worker.sony.sr2 import (  # noqa: E402
    LOOK_INDEX_TAG, _decrypted_sr2, _find_tag, decrypt,
)

# 根层里"这张照片拍摄时那个外观"的标定。换外观就是把这些整体换掉。
LOOK_TAGS = (0x7841, 0x7842, 0x7843, 0x7844, 0x7845, 0x7846, 0x7770, 0x7805, 0x7806)


def _sr2_region(path):
    """-> (原始字节, 解密后的整块, 加密区起点/长度/密钥, SR2SubIFD 位置, 字节序)"""
    buf = Path(path).read_bytes()
    endian = "<" if buf[:2] == b"II" else ">"
    (ifd0,) = struct.unpack_from(endian + "I", buf, 4)
    _, _, priv_val, _ = _find_tag(buf, ifd0, endian, 0xC634)
    (priv_pos,) = struct.unpack_from(endian + "I", buf, priv_val)
    got = {}
    for t in (0x7200, 0x7201, 0x7221):
        _, _, vpos, _ = _find_tag(buf, priv_pos, endian, t)
        got[t] = struct.unpack_from(endian + "I", buf, vpos)[0]
    dec, sub, _ = _decrypted_sr2(path)
    return buf, bytearray(dec), (got[0x7200], got[0x7201], got[0x7221]), sub, endian


def _entry_pos(dec, sub, endian, tag):
    """某个标签的 IFD 条目起点(12 字节/条),用来改标签号本身。"""
    (n,) = struct.unpack_from(endian + "H", dec, sub)
    for i in range(n):
        o = sub + 2 + i * 12
        if struct.unpack_from(endian + "H", dec, o)[0] == tag:
            return o
    return None


def patch(src, dst, from_look=None, tags=None, renames=None):
    _, dec, (start, length, key), sub, endian = _sr2_region(src)

    # 改标签号 = 让引擎找不到它。用来关掉 `0x7841` 那条捷径,逼引擎去按外观混合
    # —— 根层标签本来就不是按标签号排序的,所以改了号也不会破坏遍历。
    for old, new in (renames or {}).items():
        pos = _entry_pos(dec, sub, endian, old)
        if pos is None:
            print(f"  0x{old:04x}: 不存在,跳过")
            continue
        struct.pack_into(endian + "H", dec, pos, new)
        print(f"  0x{old:04x} -> 0x{new:04x}")

    if from_look is not None:
        _, cnt, vpos, _ = _find_tag(dec, sub, endian, LOOK_INDEX_TAG)
        offs = struct.unpack_from(f"{endian}{cnt}I", dec, vpos)
        for tag in LOOK_TAGS:
            a = _find_tag(dec, sub, endian, tag)
            b = _find_tag(dec, offs[from_look], endian, tag)
            if a is None or b is None:
                print(f"  0x{tag:04x}: 缺,跳过")
                continue
            _, _, apos, asize = a
            _, _, bpos, bsize = b
            if asize != bsize:
                print(f"  0x{tag:04x}: 大小不一致 {asize} vs {bsize},跳过")
                continue
            dec[apos:apos + asize] = dec[bpos:bpos + bsize]
            print(f"  0x{tag:04x}: 换成外观 {from_look} 的 {asize} 字节")

    for tag, values in (tags or {}).items():
        _, cnt, vpos, size = _find_tag(dec, sub, endian, tag)
        if len(values) != cnt:
            raise SystemExit(f"0x{tag:04x} 要 {cnt} 个值,给了 {len(values)}")
        struct.pack_into(f"{endian}{cnt}h", dec, vpos, *values)
        print(f"  0x{tag:04x}: 写入 {values}")

    # 再跑一遍解密就是加密 —— 流密码是对合的
    Path(dst).write_bytes(decrypt(bytes(dec), start, length, key))
    print(f"-> {dst}")


def main():
    src, dst = sys.argv[1], sys.argv[2]
    look, tags, renames = None, {}, {}
    args = sys.argv[3:]
    for i, a in enumerate(args):
        if a == "--from-look":
            look = int(args[i + 1])
        elif a == "--tag":
            k, v = args[i + 1].split("=")
            tags[int(k, 0)] = [int(x) for x in v.split(",")]
        elif a == "--rename":
            k, v = args[i + 1].split("=")
            renames[int(k, 0)] = int(v, 0)
    patch(src, dst, look, tags, renames)


if __name__ == "__main__":
    main()
