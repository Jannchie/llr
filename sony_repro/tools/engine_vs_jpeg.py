r"""引擎的最终画面 vs 机内 JPEG:一直拿 JPEG 当真值,这个假设到底成不成立?

前面所有的「色度比」都是拿复刻去比机内 JPEG。可 JPEG 是**相机**渲的,
Edit.exe 渲的才是我们要复刻的东西。现在两份都有了,先把它们自己比一次。
"""
import os

import numpy as np
from PIL import Image

SCR = os.path.dirname(os.path.abspath(__file__))
JPG = r"E:\10960725\DSC03015.JPG"
FULL = 16383

z = np.load(os.path.join(SCR, "engine_frame.npz"))
step, W, H = z["step"]
eng_in, eng_out = z["in"], z["out"]
nh, nw = H // step, W // step
eng_in, eng_out = eng_in[:nh, :nw], eng_out[:nh, :nw]

jpg = np.asarray(Image.open(JPG), np.float32)
print("引擎帧 %dx%d(1/%d)   JPEG %dx%d" % (nw, nh, step, jpg.shape[1], jpg.shape[0]))
jpg = np.asarray(Image.fromarray(jpg.astype(np.uint8)).resize((nw, nh), Image.BILINEAR), np.float32)

for name, a in (("engine_in", eng_in), ("engine_out", eng_out)):
    img = (np.clip(a / FULL, 0, 1) * 255).astype(np.uint8)
    Image.fromarray(img).save(os.path.join(SCR, "%s.png" % name))
Image.fromarray(jpg.astype(np.uint8)).save(os.path.join(SCR, "engine_jpeg.png"))


def chroma_stats(name, ours, theirs):
    m = np.array([[0.299, 0.587, 0.114], [-0.168736, -0.331264, 0.5],
                  [0.5, -0.418688, -0.081312]], np.float32)

    def ycc(x):
        a = x.astype(np.float32) / 255
        return a @ m[0], a @ m[1], a @ m[2]

    y_o, cb_o, cr_o = ycc(ours)
    y_t, cb_t, cr_t = ycc(theirs)
    r_o, r_t = np.hypot(cb_o, cr_o), np.hypot(cb_t, cr_t)
    sel = (r_o > 0.02) & (y_o > 0.06) & (y_o < 0.9)
    ratio = r_t[sel] / np.maximum(r_o[sel], 1e-6)
    dh = np.degrees(np.arctan2(cr_t, cb_t) - np.arctan2(cr_o, cb_o))
    dh = (dh + 180) % 360 - 180
    print("  %-12s 色度比 中位 %.4f  p25 %.4f  p75 %.4f   色相移 %+.2f°   亮度差 %+.4f"
          % (name, np.median(ratio), np.percentile(ratio, 25), np.percentile(ratio, 75),
             np.median(dh[sel]), np.median(y_t[sel] - y_o[sel])))


print("\n以机内 JPEG 为基准(1.0 = 完全对上):")
chroma_stats("引擎最终", (np.clip(eng_out / FULL, 0, 1) * 255), jpg)
chroma_stats("引擎 Marble 前", (np.clip(eng_in / FULL, 0, 1) * 255), jpg)
print()
for name, a in (("引擎最终", eng_out), ("Marble 前", eng_in)):
    print("  %-10s 平均 RGB %s" % (name, (np.clip(a / FULL, 0, 1) * 255).reshape(-1, 3).mean(0).round(1)))
print("  %-10s 平均 RGB %s" % ("机内 JPEG", jpg.reshape(-1, 3).mean(0).round(1)))
