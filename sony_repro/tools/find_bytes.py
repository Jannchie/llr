r"""在 .text 里按字节模式找指令,给不出交叉引用时用。

滑块的取值函数都长一个样:`cmp r8d, 0x5a` 后按 /10 查十项表再线性插值。
按这个特征字节 `41 83 f8 5a` 一扫,一族兄弟函数就都出来了 —— 比逐个猜偏移快。

必须用 Windows 的 Python 跑:
    python find_bytes.py 41 83 f8 5a
"""
import sys

import pefile

BIN = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"


def main():
    pat = bytes(int(a, 16) for a in sys.argv[1:])
    if not pat:
        raise SystemExit(__doc__)
    pe = pefile.PE(BIN, fast_load=True)
    base = pe.OPTIONAL_HEADER.ImageBase
    for s in pe.sections:
        if s.Name.rstrip(b"\x00") != b".text":
            continue
        data, start = s.get_data(), base + s.VirtualAddress
        i, n = data.find(pat), 0
        while i >= 0:
            print("0x%x (rva 0x%x)" % (start + i, start + i - base))
            n += 1
            i = data.find(pat, i + 1)
        print(f"\n{n} 处")


if __name__ == "__main__":
    main()
