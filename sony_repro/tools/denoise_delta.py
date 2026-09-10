"""差分法:在**真实全分辨率管线**上验证降噪,不需要引擎捕获。

到目前为止的"逐位 99.85%"都是拿引擎自己的输入算的,而 `mosaic_input_check.py`
查出那份 mosaic 是预览路径的降采样(值域 525~1454,真实 raw 是 291~16383)。要在
llr 真正跑的尺寸和输入上验证,本来得抓导出那一刻的调用 —— 可 Edit 会缓存渲染
结果,重开同一张图根本不再跑降噪,而全分辨率只在导出时才有。

绕过去的办法是**相减**:

    Edit 开降噪 − Edit 关降噪  =  Edit 的降噪做了什么
    llr  开降噪 − llr  关降噪  =  llr  的降噪做了什么

色调曲线的差、demosaic 的差、镜头畸变的位移,在两张图里是同一份,相减就抵消了。
剩下的就是降噪本身,而且是在全分辨率、真实输入上。

需要 Edit 的两张导出(同一张 ARW,除降噪外设置完全一致)。只有关降噪那张时,这个
工具只报 llr 一侧,并把需要的东西说清楚。
"""
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
from PIL import Image

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")
sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")

from aniso_stage import find_arw, render  # noqa: E402

REC601 = np.array([0.299, 0.587, 0.114], np.float32)


def stats(delta: np.ndarray, label: str) -> None:
    """降噪"拿掉"了多少东西,以及拿掉的是亮度还是颜色。"""
    y = delta @ REC601
    c = delta - y[..., None]
    print(f"    {label:<24} |Δ| 均值 {float(np.mean(np.abs(delta))):.5f}   "
          f"p99 {float(np.percentile(np.abs(delta), 99)):.5f}   "
          f"亮度 σ {float(np.std(y)):.5f}   色度 σ {float(np.std(c)):.5f}")


def main() -> int:
    stem = sys.argv[1] if len(sys.argv) > 1 else "DSC03036"
    arw = find_arw(stem)
    if arw is None:
        print(f"找不到 {stem}.ARW")
        return 1

    off = np.asarray(render(arw, denoise_model="passthrough", chroma=True), np.float32)
    on = np.asarray(render(arw, denoise_model="sony", chroma=True), np.float32)
    wav = np.asarray(render(arw, denoise_model="wavelet", chroma=True), np.float32)
    print(f"{stem}  llr 渲染 {off.shape}\n")
    print("  llr 侧的降噪效果(开 − 关):")
    stats(on - off, "Sony RawNR")
    stats(wav - off, "小波")
    stats(on - wav, "两者之差")

    edit_off = Path(str(arw).replace(".ARW", ".JPG"))
    edit_on = Path(str(arw).replace(".ARW", "-nr.JPG"))
    print(f"\n  Edit 侧:")
    print(f"    关降噪 {edit_off}  {'有' if edit_off.exists() else '缺'}")
    print(f"    开降噪 {edit_on}  {'有' if edit_on.exists() else '缺'}")
    if not (edit_off.exists() and edit_on.exists()):
        print("\n  还差一张 **Edit 开着降噪** 的导出(其余设置与关降噪那张完全一致),"
              "\n  放成上面那个 -nr.JPG 的名字即可。有了它就能把两边的降噪效果直接"
              "\n  相减比对 —— 那是目前唯一能在真实全分辨率输入上验证降噪的办法。")
        return 0

    a = np.asarray(Image.open(edit_off).convert("RGB"), np.float32) / 255.0
    b = np.asarray(Image.open(edit_on).convert("RGB"), np.float32) / 255.0
    h, w = min(a.shape[0], b.shape[0]), min(a.shape[1], b.shape[1])
    print("\n  Edit 侧的降噪效果(开 − 关):")
    stats(b[:h, :w] - a[:h, :w], "Edit RawNR")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
