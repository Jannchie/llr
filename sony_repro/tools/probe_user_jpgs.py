"""用户那两张图到底是什么 —— 尺寸、有没有缩放、JPEG 质量。

在拿它们做任何比较之前要先确认这些。若两张尺寸不同,或其中一张被缩放过,
`aniso.py` 量到的差异就可能只是重采样的低通/振铃,而不是降噪算子的差异 ——
双线性/Lanczos 缩放本身就是**可分离**的,会伪造出正是要找的那个签名。
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PIL import Image

PATHS = [
    "/mnt/c/Users/Jannchie/Downloads/DSC03036-llr.jpg",
    "/mnt/c/Users/Jannchie/Downloads/DSC03036-直出.jpg",
    "/mnt/e/temp_photo/DSC03036.JPG",
]


def qtable_sig(im):
    q = getattr(im, "quantization", None)
    if not q:
        return "无量化表"
    return " | ".join(f"表{k}:首4={list(v[:4])}" for k, v in sorted(q.items()))


def main():
    for p in PATHS:
        path = Path(p)
        if not path.exists():
            print(f"{path.name}: 不存在")
            continue
        im = Image.open(path)
        ex = im.getexif()
        soft = ex.get(0x0131, "")
        print(f"{path.name}")
        print(f"  尺寸 {im.size}  模式 {im.mode}  字节 {path.stat().st_size}")
        print(f"  Software={soft!r}  EXIF 项数={len(ex)}")
        print(f"  {qtable_sig(im)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
