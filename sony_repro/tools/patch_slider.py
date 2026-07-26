r"""把机内滑块改进一份 ARW 副本,给后续的整幅 dump 用。

slider_probe.py 只是"改一下看参数",而要拿引擎的 in/out 逐像素核对公式,得有一份
稳定存在的、改好的文件。滑块是明文 int32,直接改字节 —— 不重排 IFD,也不碰加密段。
外观由 MakerNotes 的**字符串** 0xb020 决定,数值字段单独改无效,所以三个一起写。

必须用 Windows 的 Python 跑(要 exiftool):
    python patch_slider.py <源ARW> <目标ARW> fade=5 saturation=-3 look=SE
"""
import os
import shutil
import struct
import sys

from slider_probe import find_offsets

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sony_repro.sr2 import LOOK_ORDER, data_ifds  # noqa: E402

# 外观三件套:字符串是引擎唯一认的那个,另两个数值字段跟着写以免自相矛盾
STYLE_TAG, COLORMODE_TAG, STYLESTR_TAG = 0x0037, 0xB029, 0xB020
STYLE_BASE, COLORMODE_GAP = 11, 3
_STYLE_SPEC = {STYLE_TAG: (r"1 bytes, int8u\[1\]", "<b"),
               COLORMODE_TAG: (r"4 bytes, int32u\[1\]", "<i")}


def _style_offsets(path):
    import re
    import subprocess
    out = subprocess.run(["exiftool", "-v3", str(path)], capture_output=True, text=True,
                         encoding="utf-8", errors="replace").stdout
    got = {}
    for tag, (spec, _) in list(_STYLE_SPEC.items()) + [(STYLESTR_TAG, (r"16 bytes, string\[16\]", None))]:
        m = re.search(r"- Tag 0x%04x \(%s\):\s*\n[|\s]*([0-9a-f]+):" % (tag, spec), out)
        if m:
            got[tag] = int(m.group(1), 16)
    return got


def patch(src, dst, tune, look=None):
    shutil.copyfile(src, dst)
    offs = find_offsets(src)
    with open(dst, "r+b") as f:
        for k, v in tune.items():
            f.seek(offs[k])
            f.write(struct.pack("<i", v))
        if look:
            i = LOOK_ORDER.index(look)
            # 拼写取自该文件自己的 SR2DataIFD 0x7770,免得拼错(是 Sepia 不是 SE)
            name = bytes(data_ifds(src)[i].get(0x7770, b"")).split(b"\x00")[0]
            so = _style_offsets(src)
            f.seek(so[STYLESTR_TAG])
            f.write(name.ljust(16, b"\x00"))
            f.seek(so[STYLE_TAG])
            f.write(struct.pack("<b", STYLE_BASE + i))
            f.seek(so[COLORMODE_TAG])
            f.write(struct.pack("<i", STYLE_BASE + i + COLORMODE_GAP))
    return dst


def main():
    src, dst = sys.argv[1], sys.argv[2]
    tune, look = {}, None
    for a in sys.argv[3:]:
        k, v = a.split("=")
        if k == "look":
            look = v
        else:
            tune[k] = int(v)
    patch(src, dst, tune, look)
    print(f"{dst}  <- {os.path.basename(src)}  {tune}  look={look}")


if __name__ == "__main__":
    main()
