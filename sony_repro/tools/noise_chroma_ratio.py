"""噪声是偏**彩色**还是偏**亮度** —— 量一下,别停在感觉上。

用户的观察:即使把降噪整个关掉,llr 的画面和 Edit 的也不一样,llr 更像彩色噪点、
Edit 更像亮度噪点。这条观察很要紧,因为它把差异指到了降噪**以外**:RAW 域降噪
交出来的是四个 Bayer 平面,还没有颜色;颜色是 demosaic 造出来的。

Bayer 噪声变成"彩噪"还是"亮噪",取决于插值让相邻通道的噪声**相关**还是各走各的。
Edit 真正的 demosaic 是 `ZcTaskSIMDITP`(见 notes 3),llr 用的是 LibRaw 的 AHD,
这一环从没复刻过。

量法:挑平坦块(避开结构,否则量到的是画面不是噪声),转 YCbCr,高通掉低频,比较
色度与亮度的噪声能量。比值越高越"彩"。

⚠️ 不做逐像素对齐,只比统计量 —— llr 与机内 JPEG 之间有镜头畸变校正的位移
(notes 2.12),对齐不了;而这个比值本身不依赖对齐。

⚠️ 参照那张 JPEG 是什么,决定了整份读数怎么解释,所以**用 exiftool 确认过再用**:
`/mnt/e/temp_photo/DSC03036.JPG` 的 `Software` 是 `Edit 4.0.00.10311`,是用户在
Edit 里**把降噪关掉**导出的,不是相机直出。曾经把它当成机内 JPEG,于是写下
「机内管线不等于 Edit、不能当索尼参照」的警告 —— 正好写反了:它恰恰是最权威的
参照。相机直出才是那个不能用的东西(机内另有一套降噪)。

⚠️⚠️ 而且**这个路径下的文件被替换过**:同一个文件名,先前是相机直出,后来换成了
Edit 关降噪的导出。所以「上次用这张图得到的结论」不能直接沿用,路径相同不等于
图相同。每次比对前先 `exiftool -Software -FileModifyDate` 看一眼,别信记忆。
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
from PIL import Image

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")
sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")

from aniso_stage import find_arw, render  # noqa: E402

TILE = 64
#: Rec.601,和相机 JPEG 的 YCbCr 一致。
M = np.array([[0.299, 0.587, 0.114],
              [-0.168736, -0.331264, 0.5],
              [0.5, -0.418688, -0.081312]], np.float32)


def blocks(img: np.ndarray, ntile: int = 900) -> np.ndarray:
    """挑最平坦的一批块。噪声要在没有结构的地方量。"""
    h, w, _ = img.shape
    ys = range(0, h - TILE, TILE)
    xs = range(0, w - TILE, TILE)
    cand = []
    for y in ys:
        for x in xs:
            t = img[y:y + TILE, x:x + TILE]
            lum = t @ M[0]
            # 用块内的低频起伏当"有没有结构"的度量:先 4x4 平均再看方差。
            small = lum.reshape(TILE // 4, 4, TILE // 4, 4).mean(axis=(1, 3))
            cand.append((float(np.var(small)), y, x))
    cand.sort()
    return np.array([[y, x] for _, y, x in cand[:ntile]])


def ratio(img: np.ndarray, label: str) -> None:
    """色度噪声 / 亮度噪声。高 = 偏彩噪。"""
    picked = blocks(img)
    sy, sc = [], []
    for y, x in picked:
        t = img[y:y + TILE, x:x + TILE]
        ycc = t @ M.T
        # 高通:减掉 4x4 的局部均值,只留下像素级的噪声。
        for i, acc in ((0, sy), (1, sc), (2, sc)):
            ch = ycc[..., i]
            lo = np.repeat(np.repeat(
                ch.reshape(TILE // 4, 4, TILE // 4, 4).mean(axis=(1, 3)), 4, 0), 4, 1)
            acc.append(float(np.var(ch - lo)))
    y_sigma = float(np.sqrt(np.mean(sy)))
    c_sigma = float(np.sqrt(np.mean(sc)))
    print(f"  {label:<28} 亮度 σ={y_sigma:.5f}  色度 σ={c_sigma:.5f}  "
          f"色度/亮度 = {c_sigma / max(y_sigma, 1e-9):6.3f}")


def main() -> int:
    stem = sys.argv[1] if len(sys.argv) > 1 else "DSC03036"
    arw = find_arw(stem)
    if arw is None:
        print(f"找不到 {stem}.ARW")
        return 1
    print(f"{stem}:平坦块上的噪声构成(色度/亮度越高 = 越像彩色噪点)\n")

    jpg = Path(str(arw).replace(".ARW", ".JPG"))
    if jpg.exists():
        im = np.asarray(Image.open(jpg).convert("RGB"), np.float32) / 255.0
        ratio(im, "Edit 关掉降噪导出")

    # 第一行刻意关掉 llr 自己的色度降噪 —— 那是**纯 demosaic 的基线**,要的就是
    # 它。其余三行开着,因为那才是用户屏幕上真正看到的东西。
    for model, chroma, label in (("passthrough", False, "llr 不降噪(无色度降噪)"),
                                 ("passthrough", True, "llr 不降噪"),
                                 ("sony", True, "llr + Sony RawNR"),
                                 ("wavelet", True, "llr + 小波")):
        try:
            img = render(arw, denoise_model=model, chroma=chroma)
        except Exception as exc:  # noqa: BLE001 — 少一路不该拖垮整份报告
            print(f"  {label}: 跑不出来 {exc}")
            continue
        ratio(np.asarray(img, np.float32), label)

    print("\n  第一行是 Edit 自己关掉降噪的结果,两边条件对齐,可以直接比。"
          "\n  「llr 不降噪(无色度降噪)」高于它,说明 demosaic 就把 Bayer 噪声变得"
          "\n  更偏彩 —— 那一段差距降噪够不着,降噪交出的是四个 Bayer 平面,"
          "\n  那时还没有颜色。"
          "\n  ⚠️ 带色度清理的三行远低于 Edit,但这不一定是「llr 压过头」:还要先"
          "\n  弄清 Edit 的降噪开关有没有连 Marble 的色度清理一起关掉。没弄清之前"
          "\n  别拿这三行下结论。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
