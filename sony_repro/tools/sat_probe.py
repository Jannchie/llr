r"""量一量:我们的两步复刻与机内 JPEG 之间,到底差了什么。

两步 = LinearMatrix16 + MainGamma。机内 JPEG 还多走了 RGB2YCC、SSCS 饱和、
AreaComp 与锐化。拿一张 DRO 关、微调全零的图对齐后逐像素比,就能看出缺的那部分
是什么形式的变换 —— 是整体拉饱和,还是随亮度/色相而变。

用法: python sat_probe.py <ARW> [style]
"""
import sys
from pathlib import Path

import numpy as np
import rawpy
from PIL import Image

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "apps/worker/src"))
from llr_worker.fit_profile import postprocess_camera_native  # noqa: E402
from llr_worker.sony.linear_matrix import SegmentedMatrix  # noqa: E402
from llr_worker.sony.sr2 import look_calibrations, unpack_param_block  # noqa: E402
from llr_worker.sony.tone import LOOK_ORDER, tone_curve  # noqa: E402

# 这条路径不经过 ProPhoto:矩阵输出本来就落在 Rec.709 原色上,曲线也定义在那里


def srgb_encode(x):
    x = np.clip(x, 0.0, 1.0)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * x ** (1 / 2.4) - 0.055)


def srgb_decode(y):
    y = np.clip(y, 0.0, 1.0)
    return np.where(y <= 0.04045, y / 12.92, ((y + 0.055) / 1.055) ** 2.4)


def render(arw, style, half=True):
    """我们的两步复刻 -> 8-bit sRGB。"""
    with rawpy.imread(str(arw)) as raw:
        cam = postprocess_camera_native(raw, half_size=half)
    cal = look_calibrations(arw)[LOOK_ORDER.index(style)]
    m = SegmentedMatrix(unpack_param_block(cal.param_block))
    rec709 = m.apply(cam.astype(np.float32))

    lut = tone_curve(cal, style)                      # display-encoded, [0,1]
    x = np.linspace(0.0, 1.0, lut.size)
    out = np.empty_like(rec709)
    for c in range(3):
        out[..., c] = np.interp(np.clip(rec709[..., c], 0, 1), x, lut)
    return (np.clip(out, 0, 1) * 255).round().astype(np.uint8)


def load_jpeg(path, shape):
    img = Image.open(path).convert("RGB")
    img = img.resize((shape[1], shape[0]), Image.LANCZOS)
    return np.asarray(img)


def saturation(rgb8):
    """HSV 的 S,以及 V —— 用来看饱和差异是否随亮度变化。"""
    a = rgb8.astype(np.float32) / 255
    mx, mn = a.max(-1), a.min(-1)
    return np.divide(mx - mn, mx, out=np.zeros_like(mx), where=mx > 1e-6), mx


def main(arw, style=None):
    arw = Path(arw)
    jpg = arw.with_suffix(".JPG")
    if not jpg.exists():
        jpg = arw.with_suffix(".jpg")
    if style is None:
        import subprocess
        out = subprocess.run(["exiftool", "-s3", "-CreativeStyle", str(arw)],
                             capture_output=True, text=True).stdout.strip().splitlines()
        style = out[0] if out else "ST"
    print(f"{arw.name}  外观={style}")

    ours = render(arw, style)
    theirs = load_jpeg(jpg, ours.shape)
    print(f"尺寸 {ours.shape}")

    s_o, v_o = saturation(ours)
    s_t, v_t = saturation(theirs)
    # 只看有颜色、不过曝的像素,饱和度在近黑近白处没有意义
    mask = (s_o > 0.05) & (v_o > 0.05) & (v_o < 0.95) & (v_t < 0.95)
    print(f"参与统计的像素 {mask.mean()*100:.1f}%")

    print(f"\n饱和度  ours 中位 {np.median(s_o[mask]):.4f}   engine 中位 {np.median(s_t[mask]):.4f}")
    ratio = s_t[mask] / np.maximum(s_o[mask], 1e-6)
    print(f"逐像素比值 engine/ours: 中位 {np.median(ratio):.4f}  p10 {np.percentile(ratio,10):.4f}"
          f"  p90 {np.percentile(ratio,90):.4f}")

    print("\n按我们的饱和度分箱(看是否恒定倍率):")
    edges = [0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.55, 0.7, 1.01]
    for lo, hi in zip(edges[:-1], edges[1:]):
        b = mask & (s_o >= lo) & (s_o < hi)
        if b.sum() < 500:
            continue
        print(f"  S∈[{lo:.2f},{hi:.2f})  n={b.sum():7d}  ours={np.median(s_o[b]):.4f}"
              f"  engine={np.median(s_t[b]):.4f}  比 {np.median(s_t[b]/np.maximum(s_o[b],1e-6)):.4f}")

    print("\n按亮度分箱(看是否与亮度相关):")
    for lo, hi in [(0.05, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 0.95)]:
        b = mask & (v_o >= lo) & (v_o < hi)
        if b.sum() < 500:
            continue
        print(f"  V∈[{lo:.2f},{hi:.2f})  n={b.sum():7d}  比 {np.median(s_t[b]/np.maximum(s_o[b],1e-6)):.4f}")

    print("\n亮度本身(看曲线是否已经对上):")
    for lo, hi in [(0.05, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 0.95)]:
        b = mask & (v_o >= lo) & (v_o < hi)
        if b.sum() < 500:
            continue
        print(f"  V∈[{lo:.2f},{hi:.2f})  ours={np.median(v_o[b]):.4f}  engine={np.median(v_t[b]):.4f}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
