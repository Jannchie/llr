"""从 ARW 里直接读出 LinearMatrix16 的节点表。

这推翻了此前「节点表由 Sony 内部重算的白平衡决定、只能逐图 frida dump」的判断:
**它根本不是算出来的,是相机在拍摄时写进 RAW 的标定数据。**

来龙去脉(反汇编 + 实测确认):

* Edit.exe 的 ``+0x179390`` 是个纯解包函数,把一段 276 字节的位流拆成整个
  LinearMatrix16 参数区(节点位置表 + 96 个系数 + 尾部标志),不做任何计算。
* 那 276 字节原样来自 ARW —— 加密的 SR2SubIFD 里的 **tag 0x780f**
  (type=undefined, count=276)。
* 全部 65 张实测:本模块离线解出的节点表与 frida 从引擎内存 dump 到的
  **逐位一致**(最大差 0)。

于是整条链路不再需要 frida,worker 里离线跑即可。

寻址路径::

    IFD0 tag 0xc634 (DNGPrivateData)
      └─ SR2Private IFD
           0x7200 SR2SubIFDOffset
           0x7201 SR2SubIFDLength
           0x7221 SR2SubIFDKey      ← 解密用
      └─ 解密后的 SR2SubIFD
           0x780f  276 字节位流     ← 节点表在此

位流格式:直接当 69 个小端 uint32。前 6 个是节点位置表与标志,
``d[6+i]`` 各装两个 14 位定点系数(高位在前),定点单位恒为 1/1024。
"""
from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

__all__ = ["SR2_PARAM_TAG", "decrypt", "read_sr2_tag", "unpack_param_block",
           "linear_matrix_coeff", "data_ifds", "read_ifd", "look_curve", "LOOK_ORDER"]

SR2_PARAM_TAG = 0x780F
_Q = np.float32(1.0 / 1024.0)

LOOK_INDEX_TAG = 0x74C0   # uint32[10]:十份 SR2DataIFD 的偏移
CURVE_X_TAG = 0x7805      # 色调曲线控制点 x(非等距)
CURVE_Y_TAG = 0x7806      # ...对应的 y
LOOK_NAME_TAG = 0x7770    # 该外观自报的名字

TONE_INDEX_WHITE = 8192        # 白点所在的 LUT 索引
CURVE_X_SCALE = 128.0          # tag 0x7805 的单位:每个 LUT 索引 128
CURVE_Y_FULL = 16.0 * 16384.0  # tag 0x7806 满刻度

# Sony 自己的顺序,与 CreativeStyle 标签一致
LOOK_ORDER = ("ST", "VV", "NT", "PT", "FL", "VV2", "IN", "SH", "BW", "SE")

# TIFF 字段类型 -> 单元字节数
_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}
# ...以及能直接转成 numpy 的那些
_NP_FMT = {1: "B", 3: "H", 4: "I", 8: "h", 9: "i", 11: "f"}


def decrypt(data: bytes, start: int, length: int, key: int) -> bytes:
    """Sony SR2SubIFD 解密(与 exiftool ``Sony.pm`` 的 ``Decrypt`` 等价)。

    密钥流按 **大端** uint32 生成并异或;返回整份 ``data`` 的副本,
    其中 ``[start, start+length)`` 已就地解密。
    """
    words = length // 4
    pad = [0] * 0x80
    k = key
    for i in range(4):
        lo = (k & 0xFFFF) * 0x0EDD + 1
        hi = (k >> 16) * 0x0EDD + (k & 0xFFFF) * 0x02E9 + (lo >> 16)
        k = ((hi & 0xFFFF) << 16) + (lo & 0xFFFF)
        pad[i] = k
    pad[3] = ((pad[3] << 1) | ((pad[0] ^ pad[2]) >> 31)) & 0xFFFFFFFF
    for i in range(4, 0x7F):
        pad[i] = (((pad[i - 4] ^ pad[i - 2]) << 1) | ((pad[i - 3] ^ pad[i - 1]) >> 31)) & 0xFFFFFFFF

    vals = list(struct.unpack_from(f">{words}I", data, start))
    i = 0x7F
    for j in range(words):
        pad[i & 0x7F] = pad[(i + 1) & 0x7F] ^ pad[(i + 65) & 0x7F]
        vals[j] ^= pad[i & 0x7F]
        i += 1
    out = bytearray(data)
    struct.pack_into(f">{words}I", out, start, *vals)
    return bytes(out)


def _ifd_entries(buf: bytes, pos: int, endian: str):
    """读一个 TIFF IFD,产出 ``(tag, type, count, value_offset, size)``。

    ``value_offset`` 对内联值(<=4 字节)指向条目内部,与外置值一视同仁。
    """
    (n,) = struct.unpack_from(endian + "H", buf, pos)
    for i in range(n):
        o = pos + 2 + i * 12
        tag, typ, cnt = struct.unpack_from(endian + "HHI", buf, o)
        size = _TYPE_SIZE.get(typ, 1) * cnt
        vpos = o + 8 if size <= 4 else struct.unpack_from(endian + "I", buf, o + 8)[0]
        yield tag, typ, cnt, vpos, size


def _find_tag(buf: bytes, pos: int, endian: str, tag: int):
    for t, typ, cnt, vpos, size in _ifd_entries(buf, pos, endian):
        if t == tag:
            return typ, cnt, vpos, size
    return None


def _decrypted_sr2(path: str | Path) -> tuple[bytes, int, str]:
    """解密 SR2SubIFD -> (整份 buf, SR2SubIFD 偏移, 字节序)。

    解密是整块做的,所以要读多个 tag 的调用方走这里一次就够。
    """
    buf = Path(path).read_bytes()
    if buf[:2] == b"II":
        endian = "<"
    elif buf[:2] == b"MM":
        endian = ">"
    else:
        raise KeyError("不是 TIFF 结构,无法定位 SR2Private")

    (ifd0,) = struct.unpack_from(endian + "I", buf, 4)
    priv = _find_tag(buf, ifd0, endian, 0xC634)          # DNGPrivateData
    if priv is None:
        raise KeyError("IFD0 没有 tag 0xc634 (SR2Private)")
    # 标准里 DNGPrivateData 是 BYTE 数组,Sony 却拿它存一个 uint32 —— 那就是
    # SR2Private IFD 的绝对偏移。count=4 使它内联,所以还要再解一次引用。
    _, _, priv_val, _ = priv
    (priv_pos,) = struct.unpack_from(endian + "I", buf, priv_val)

    got = {}
    for t in (0x7200, 0x7201, 0x7221):                   # offset / length / key
        e = _find_tag(buf, priv_pos, endian, t)
        if e is None:
            raise KeyError(f"SR2Private 缺 tag 0x{t:04x}")
        _, _, vpos, _ = e
        got[t] = struct.unpack_from(endian + "I", buf, vpos)[0]

    return decrypt(buf, got[0x7200], got[0x7201], got[0x7221]), got[0x7200], endian


def read_sr2_tag(path: str | Path, tag: int = SR2_PARAM_TAG) -> bytes:
    """取出解密后 SR2SubIFD 中某个 tag 的原始数据。

    找不到该 tag(或这根本不是带 SR2Private 的 Sony RAW)时抛 ``KeyError``。
    """
    dec, sub_pos, endian = _decrypted_sr2(path)
    e = _find_tag(dec, sub_pos, endian, tag)
    if e is None:
        raise KeyError(f"SR2SubIFD 没有 tag 0x{tag:04x}")
    _, _, vpos, size = e
    return dec[vpos:vpos + size]


def read_ifd(buf: bytes, pos: int, endian: str) -> dict[int, object]:
    """一个 IFD -> ``{tag: 值}``。标量给 int,数组给 ndarray,未知类型给 bytes。"""
    out: dict[int, object] = {}
    for tag, typ, cnt, vpos, size in _ifd_entries(buf, pos, endian):
        fmt = _NP_FMT.get(typ)
        if fmt is None:
            out[tag] = buf[vpos:vpos + size]
            continue
        v = np.frombuffer(buf, dtype=np.dtype(endian + fmt), count=cnt, offset=vpos)
        out[tag] = int(v[0]) if cnt == 1 else v.copy()
    return out


def data_ifds(path: str | Path) -> list[dict[int, object]]:
    """十份 SR2DataIFD —— 每种创意外观一份完整标定,顺序就是 Sony 的外观索引。

    **相机把全部十种外观的标定写进每一张 RAW**,不只是拍摄时选的那一种。
    入口是 SR2SubIFD 的 tag ``0x74c0``(uint32[10],十个绝对偏移)。所以一张
    FL 拍的片子,照样带着 VV2 该长什么样 —— 换外观不需要重拍。

    每份里与颜色复刻直接相关的:

    ``0x780f``
        LinearMatrix16 的 276 字节位流
    ``0x7805`` / ``0x7806``
        色调曲线的 128 个控制点(x 非等距)。``x/128`` 是 LUT 索引(白点 8192),
        ``y/16`` 是输出(满刻度 16384)。标度的锚点是曲线的饱和位置 ``x = 2**20``,
        而 ``2**20/128 = 8192`` 正好落在此前独立测出的白点上。
    ``0x7770``
        该外观自报的名字,如 ``Standard`` / ``FL`` / ``VV2``。
    """
    dec, sub_pos, endian = _decrypted_sr2(path)
    e = _find_tag(dec, sub_pos, endian, LOOK_INDEX_TAG)
    if e is None:
        raise KeyError(f"SR2SubIFD 没有 tag 0x{LOOK_INDEX_TAG:04x}(外观索引)")
    _, cnt, vpos, _ = e
    offsets = struct.unpack_from(f"{endian}{cnt}I", dec, vpos)
    return [read_ifd(dec, o, endian) for o in offsets]


def look_curve(ifd: dict[int, object], n: int = TONE_INDEX_WHITE + 1) -> np.ndarray:
    """一份 DataIFD 的色调曲线 -> ``n`` 项归一化查找表(display-encoded)。"""
    x = np.asarray(ifd[CURVE_X_TAG], dtype=np.float64) / CURVE_X_SCALE
    y = np.asarray(ifd[CURVE_Y_TAG], dtype=np.float64) / CURVE_Y_FULL
    return np.interp(np.linspace(0.0, TONE_INDEX_WHITE, n), x, y)


def _decode14(x: int) -> float:
    """14 位定点 -> float。

    符号位是 bit13;负值的编码是 ``-((~x | 1) & 0x3fff)``,不是常规补码 ——
    照抄引擎的 ``and/not/and`` 三连,别自作主张换成 ``~x + 1``。
    """
    if (x >> 13) & 1:
        return -(((~x) | 1) & 0x3FFF)
    return x & 0x3FFF


def unpack_param_block(block: bytes) -> np.ndarray:
    """276 字节位流 -> ``(6, 16)`` 节点系数。

    位流当 69 个小端 uint32 读;``d[6+i]`` 装一对系数,高 14 位
    (``>> 0x12``)排在低 14 位(``>> 2``)之前。
    """
    if len(block) < 276:
        raise ValueError(f"参数块只有 {len(block)} 字节,至少需要 276")
    d = struct.unpack_from("<69I", block, 0)
    raw = np.empty(96, dtype=np.float32)
    for i in range(48):
        v = d[6 + i]
        raw[2 * i] = _decode14(v >> 0x12)
        raw[2 * i + 1] = _decode14(v >> 2)
    return (raw * _Q).reshape(6, 16)


def linear_matrix_coeff(path: str | Path) -> np.ndarray:
    """ARW -> ``(6, 16)`` 节点系数,可直接喂给 ``SegmentedMatrix``。"""
    return unpack_param_block(read_sr2_tag(path))
