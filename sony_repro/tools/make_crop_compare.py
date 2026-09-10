"""把结论做成一张能用眼睛验的图:同一块平坦区,三种处理并排。

数字已经说了话(2.0-2.7px 带 1.87 -> 0.88),但用户看到的是像素,所以结论也要
以像素的形式交付一次。取一块**平坦且够暗**的区域 —— 噪点在暗部最明显,而
色调曲线在暗部的斜率又最陡,两件事叠在一起正是用户在 200% 下看到的那个位置。

三格:用户导出的那张(降噪没开)、开了降噪的同一块、机内直出的同一块。

⚠️ 离线渲的图没有裁剪与镜头畸变校正,和 web 端成品有几十像素的几何差,所以
三张不是逐点对齐的。这里比的是**噪声的质感**,不是同一颗像素,所以只要取到
同一片景物就够;标题里也写明了这一点,免得看图的人以为可以逐点对照。
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
from PIL import Image, ImageDraw, ImageFont

#: PIL 自带的位图字体没有汉字字形,标题会整排画成方框。Windows 的黑体经
#: /mnt/c 就在手边,拿不到时退回默认字体并把标题降级为 ASCII —— 宁可标题难看,
#: 也不要交出一张写满方框、让人以为图坏了的对照图。
FONT_CANDIDATES = [
    "/mnt/c/Windows/Fonts/msyh.ttc",
    "/mnt/c/Windows/Fonts/msyhl.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]


def load_font(size=22):
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size), True
            except OSError:
                continue
    return ImageFont.load_default(), False

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")
from aniso import TILE  # noqa: E402

W601 = np.array([0.299, 0.587, 0.114], np.float32)
CROP = 340          # 取多大一块
ZOOM = 2            # 200%
PAD = 14


def pick_dark_flat(y, crop=CROP):
    """既平坦又偏暗的一块 —— 噪点在那里最明显。

    在标准差最低的那批里再按亮度排序,而不是直接找最暗的:最暗的往往是纯黑
    的死角,那里什么也看不出来。
    """
    h, w = y.shape
    step = crop
    cand = []
    for cy in range(crop, h - 2 * crop, step):
        for cx in range(crop, w - 2 * crop, step):
            b = y[cy:cy + crop, cx:cx + crop]
            m = float(b.mean())
            if m < 12 or m > 110:      # 太黑看不见,太亮噪点被压住
                continue
            cand.append((float(b.std()), -m, cy, cx))
    if not cand:
        return crop, crop
    cand.sort()
    return cand[0][2], cand[0][3]


def main():
    llr_off = Path("/mnt/c/Users/Jannchie/Downloads/DSC03036-llr.jpg")
    llr_on = Path("/home/jannchie/llr/tmp/DSC03036-offline-wavelet.jpg")
    ooc = Path("/mnt/c/Users/Jannchie/Downloads/DSC03036-直出.jpg")
    out = Path("/home/jannchie/llr/tmp/DSC03036-denoise-compare.png")

    imgs = []
    for p in (llr_off, llr_on, ooc):
        if not p.exists():
            raise SystemExit(f"缺 {p}")
        imgs.append(np.asarray(Image.open(p).convert("RGB"), np.uint8))

    cy, cx = pick_dark_flat(imgs[0].astype(np.float32) @ W601)
    print(f"取样位置 (y={cy}, x={cx}),{CROP}x{CROP},放大 {ZOOM}x")

    font, cjk = load_font()
    labels = ["llr 现状:降噪未开(你导出的那张)",
              "llr 打开降噪(Auto,ISO 2000 -> 满强度)",
              "机内直出 JPG"] if cjk else [
        "llr now: denoise OFF (your export)",
        "llr with denoise ON (Auto, ISO 2000 -> full)",
        "camera JPEG"]
    tiles = []
    for a in imgs:
        c = a[cy:cy + CROP, cx:cx + CROP]
        im = Image.fromarray(c).resize((CROP * ZOOM, CROP * ZOOM), Image.NEAREST)
        tiles.append(im)

    tw, th = tiles[0].size
    bar = 38
    canvas = Image.new("RGB", (tw * 3 + PAD * 4, th + bar + PAD * 2), (22, 22, 24))
    d = ImageDraw.Draw(canvas)
    for i, (im, lab) in enumerate(zip(tiles, labels)):
        x = PAD + i * (tw + PAD)
        canvas.paste(im, (x, PAD + bar))
        d.text((x, PAD + 6), lab, fill=(232, 232, 236), font=font)
    canvas.save(out)
    print(f"写出 {out}  ({canvas.size[0]}x{canvas.size[1]})")
    print("放大用的是 NEAREST,不引入任何插值 —— 看到的就是原像素。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
