r"""整条主管线离线跑一遍,对机内 JPEG —— 接入 RGB2YCC 到底值不值。

顺序严格照着前端 shader(`passes.ts` 的 `viewTransformLR`)来,一步都不能挪:

    worker: 相机 RGB -> 分段矩阵 -> 线性 ProPhoto
    shader: 转到 sRGB 原色 -> profile 曲线(逐通道) -> **RGB2YCC** -> 转回 ProPhoto
            -> 转到 sRGB -> sRGB 编码

RGB2YCC 要在曲线自己的编码域里跑(曲线下发前已把 sRGB 编码撤掉了,所以这里得先编码
回去),这正是引擎里它所处的位置。

用法: python e2e_pipeline.py <stem> [stem ...]
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")
from llr_worker.cli import prepare_linear  # noqa: E402
from llr_worker.dcp import D50_TO_D65, XYZ_D50_TO_PROPHOTO, XYZ_D65_TO_SRGB  # noqa: E402

ROOT = Path("/home/jannchie/llr")
SRC = Path("/mnt/e/10960725")
PROPHOTO_TO_SRGB = (XYZ_D65_TO_SRGB @ D50_TO_D65
                    @ np.linalg.inv(XYZ_D50_TO_PROPHOTO)).astype(np.float32)
SRGB_TO_PROPHOTO = np.linalg.inv(PROPHOTO_TO_SRGB).astype(np.float32)


def srgb_encode(c):
    c = np.clip(c, 0, 1)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def srgb_decode(c):
    c = np.clip(c, 0, 1)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def sony_chroma(s, cross, gain, pivot=0.0, contrast=1.0, sat=1.0):
    """passes.ts 的 sonyChroma:display-linear 进,display-linear 出。

    **这份要和 worker 的 `sony/chroma.py` 保持一致。** 曾经不一致过一次:
    给主管线加上 YGamma 时忘了同步这里,于是这套研究工具量出来的亮度整体偏
    +0.021,看着像"复刻偏暗",其实只是工具没跟上。
    """
    e = srgb_encode(s)
    r, g, b = e[..., 0], e[..., 1], e[..., 2]
    y = (r * 2432 + g * 4864 + b * 896) / 8192
    u, v = r - g, b - g
    v2 = np.where(u >= 0, cross[1], cross[3]) * u + v
    u2 = np.where(v >= 0, cross[0], cross[2]) * v + u
    cr = np.clip(np.where(u2 >= 0, gain[1], gain[3]) * u2, -0.5, 0.5) * sat
    cb = np.clip(np.where(v2 >= 0, gain[0], gain[2]) * v2, -0.5, 0.5) * sat
    y = np.clip((y - pivot) * contrast + pivot, 0.0, 1.0)   # YGamma:只动 Y
    return srgb_decode(np.stack([
        y + 1.4020 * cr, y - 0.7141 * cr - 0.3441 * cb, y + 1.7720 * cb], -1))


def render(path, profile_id, with_chroma=True):
    r = prepare_linear(path, {"profileId": profile_id}, ROOT, None, False, half_size=True)
    cp = r.color_profile
    c = r.linear.astype(np.float32)
    pts = cp.get("profileToneCurve")
    if pts:
        p = np.asarray(pts, np.float64)
        lut = np.interp(np.linspace(0, 1, 2048), p[:, 0], p[:, 1])
        srgb_basis = cp["kind"] == "sony"
        s = c @ PROPHOTO_TO_SRGB.T if srgb_basis else c
        s = np.interp(np.clip(s, 0, 1), np.linspace(0, 1, 2048), lut).astype(np.float32)
        cross, gain = cp.get("profileChromaCross"), cp.get("profileChromaGain")
        if with_chroma and cross and gain:
            s = sony_chroma(s, np.asarray(cross), np.asarray(gain),
                            cp.get("profileLumaPivot", 0.0),
                            cp.get("profileLumaContrast", 1.0),
                            cp.get("profileChromaSaturation", 1.0)).astype(np.float32)
        c = s @ SRGB_TO_PROPHOTO.T if srgb_basis else s
    return srgb_encode(np.clip(c, 0, 1) @ PROPHOTO_TO_SRGB.T).astype(np.float32), cp


def stats(name, ours, theirs, mask):
    m = np.array([[0.299, 0.587, 0.114], [-0.168736, -0.331264, 0.5],
                  [0.5, -0.418688, -0.081312]], np.float32)
    yo, cbo, cro = (ours @ m[0])[mask], (ours @ m[1])[mask], (ours @ m[2])[mask]
    yt, cbt, crt = (theirs @ m[0])[mask], (theirs @ m[1])[mask], (theirs @ m[2])[mask]
    ratio = np.hypot(cbt, crt) / np.maximum(np.hypot(cbo, cro), 1e-6)
    dh = np.degrees(np.arctan2(crt, cbt) - np.arctan2(cro, cbo))
    dh = (dh + 180) % 360 - 180
    rmse = float(np.sqrt(((ours - theirs) ** 2).mean()) * 255)
    print("  %-16s 色度比 %.4f   色相差 %+.2f 度   亮度差 %+.4f   RMSE %.1f/255"
          % (name, np.median(ratio), np.median(dh), np.median(yt - yo), rmse))


def main():
    for stem in sys.argv[1:]:
        jpg_path = SRC / f"{stem}.JPG"
        with_ycc, cp = render(SRC / f"{stem}.ARW", "sony")
        without, _ = render(SRC / f"{stem}.ARW", "sony", with_chroma=False)
        dcp, _ = render(SRC / f"{stem}.ARW", "standard")
        h, w = with_ycc.shape[:2]
        jpg = np.asarray(Image.open(jpg_path).resize((w, h), Image.BILINEAR), np.float32) / 255.0
        mask = ((jpg.max(-1) - jpg.min(-1) > 0.10) & (jpg.max(-1) > 0.15)
                & (jpg.max(-1) < 0.95))
        look = cp.get("creativeLook") or "?"
        print(f"\n=== {stem} / {look}  (mask {mask.sum()} px)")
        stats("矩阵+曲线", without, jpg, mask)
        stats("再加 RGB2YCC", with_ycc, jpg, mask)
        stats("DCP 对照", dcp, jpg, mask)


if __name__ == "__main__":
    main()
