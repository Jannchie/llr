r"""整条主管线离线跑一遍,对机内 JPEG —— 接入 RGB2YCC 到底值不值。

顺序严格照着前端 shader(`passes.ts` 的 `viewTransform`)来,一步都不能挪:

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
from llr_worker.sony.lut3d import apply_lut3d_float  # noqa: E402

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


def sony_chroma(s, cross, gain, pivot=0.0, contrast=1.0, sat=1.0, suppress=None, lut=None,
                lut3d=False, lut_advanced=None, contrast_advanced=None):
    """passes.ts 的 sonyChroma:display-linear 进,display-linear 出。

    **这份要和 worker 的 `sony/chroma.py` 保持一致。** 曾经不一致过一次:
    给主管线加上 YGamma 时忘了同步这里,于是这套研究工具量出来的亮度整体偏
    +0.021,看着像"复刻偏暗",其实只是工具没跟上。

    `suppress` 就是 profile 上的 `profileChromaSuppres`(`{hiY, loY, slopeHi,
    slopeLo}`,None 表示这张片子读不到那四个 SR2 tag)。ChromaSuppres 夹在
    RGB2YCC 和 YGamma 中间,读的是 **YGamma 之前** 的亮度;中间调是平的
    255/256(不是恒等),hiY 以上线性衰减到零。推导和实测见 worker 的
    `sony/chromasuppres.py`。

    `lut` 就是 profile 上的 `profileLumaLut`(16384 项,进出都在引擎的 0..16383
    刻度上)。YGamma 先查表再做 pivot/contrast,Standard/Neutral 那张表在 8192
    以上是 0.90625 斜率的高光拐点 —— 少了它,DSC03036 的 0.7~0.9 亮度带整体偏
    +0.013~+0.028 且高光糊掉。索引用 trunc 不插值,和引擎的整数级一致。

    `lut3d` 就是 Edit「色彩复制 = 高级」,默认关,和 Edit 默认的「标准」一致。
    **这一档不止一件事**:

      * YGamma 换成高级档自己的表和自己的对比度 —— `lut_advanced` 和
        `contrast_advanced`,对应 profile 上的 `profileLumaLutAdvanced` /
        `profileLumaContrastAdvanced`。对比度两族都是 17280/16384,连 FL 这种
        自己的 0x780e 就是 16384 的外观也一样;
      * 然后才是 ZcTask3DLut(worker 的 `sony/lut3d.py`,notes/static-3dlut.md),
        位置在 YGamma 之后、回程之前。

    这里直接调 worker 的**逐位复刻**,而不是照抄 shader 的浮点近似:这条链路是拿
    去对 Edit 导出的,要量的是「这一级本身对不对」,不是 shader 的三线性舍入(那
    一项 shader 侧另有 ≤2/16383 的说明)。

    少给后两个参数,量到的就是「只加了三维表」那一版 —— 高光带会比 Edit 的高级档
    亮 0.02~0.03,色度也高一截,lut3d_ab.py 的第三列就是这么发现漏了半级的。
    """
    if lut3d:
        if lut_advanced is not None:
            lut = lut_advanced
        if contrast_advanced is not None:
            contrast = contrast_advanced
    e = srgb_encode(s)
    r, g, b = e[..., 0], e[..., 1], e[..., 2]
    y = (r * 2432 + g * 4864 + b * 896) / 8192
    u, v = r - g, b - g
    v2 = np.where(u >= 0, cross[1], cross[3]) * u + v
    u2 = np.where(v >= 0, cross[0], cross[2]) * v + u
    cr = np.clip(np.where(u2 >= 0, gain[1], gain[3]) * u2, -0.5, 0.5) * sat
    cb = np.clip(np.where(v2 >= 0, gain[0], gain[2]) * v2, -0.5, 0.5) * sat
    if suppress:
        y16 = y * 16383.0
        f = np.full(y16.shape, 255.0)
        f = np.where(y16 < suppress["loY"],
                     255.0 - np.floor((suppress["loY"] - y16) * suppress["slopeLo"] / 4096.0), f)
        f = np.where(y16 > suppress["hiY"],
                     255.0 - np.floor((y16 - suppress["hiY"]) * suppress["slopeHi"] / 4096.0), f)
        f = np.clip(f, 0.0, 255.0) / 256.0
        cr, cb = cr * f, cb * f
    if lut is not None:
        t = np.asarray(lut)
        y = t[np.clip(np.trunc(y * 16383.0), 0, len(t) - 1).astype(np.intp)] / 16383.0
    y = np.clip((y - pivot) * contrast + pivot, 0.0, 1.0)   # YGamma:只动 Y
    if lut3d:
        y, cb, cr = apply_lut3d_float(y, cb, cr)
    return srgb_decode(np.stack([
        y + 1.4020 * cr, y - 0.7141 * cr - 0.3441 * cb, y + 1.7720 * cb], -1))


def render(path, profile_id, with_chroma=True, half_size=True, denoise_model=None,
           denoise_tweaks=(50.0, 50.0), lut3d=False, exposure_ev=0.0, marble=None):
    """半尺寸是默认,因为色彩对比不在乎分辨率,而它快一倍。

    量**噪声**时必须 `half_size=False`:半尺寸是 LibRaw 的 Bayer 合并,一个四元组
    出一个像素,根本没走插值 demosaic —— 而 demosaic 恰好是色度噪声的来源。
    同理 `denoise_model` 不给就是**完全不降噪**,拿它去比降噪效果会得出空结论。

    ⚠️ **这条链路不做镜头畸变校正,而 Edit 做。** 上线的 llr 是做的(web 端
    lens.ts,`lensDistortion` 默认 100),但这里从 `prepare_linear` 直接接色调链,
    绕过了它。实测 DSC02995 的位移场:纯径向、中心≈0、四角 47.5px、切向 RMS
    只有 0.62px —— 就是 ARW 里那张 `DistortionCorrParams` 表。

    后果按测法分:
      * **平坦区的 MAD 幅度**(色差标定、噪声比值)对错位不敏感,基本不受影响;
      * **任何逐像素/相关性的比较全部作废** —— 不补几何的话,最细带的相关会被
        打到 0.00,读起来像"llr 全是噪声",其实是那一带压根没对上。
      * 顺带,连幅度也会被夸大:未补几何时量到 llr 的亮度细带是 Edit 的
        1.6~1.9 倍,补上之后只有 1.2~1.3 倍。
    要做结构性对比,用 `luma_gap.py` 的位移场补偿(它两侧各 warp 一半,免得
    双线性插值的低通只算在一边)。见 measured-chroma-gap.md §2.12。

    `lut3d` 透传给 `sony_chroma`,连同 profile 上高级档自己的那张 YGamma 表和
    对比度:对着一份「色彩复制 = 高级」导出的 Edit 成片时必须打开,否则量到的是
    少了一整级的差。
    """
    r = prepare_linear(path, {"profileId": profile_id}, ROOT, None, False,
                       half_size=half_size, denoise_model=denoise_model,
                       denoise_tweaks=denoise_tweaks)
    cp = r.color_profile
    c = r.linear.astype(np.float32)
    if exposure_ev:
        # Edit 的曝光补偿:ITP 出口的线性 RGB 直接乘 2^EV(measured-chroma-gap §2.29),没有肩部。
        c = c * np.float32(2.0 ** exposure_ev)
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
                            cp.get("profileChromaSaturation", 1.0),
                            cp.get("profileChromaSuppres"),
                            cp.get("profileLumaLut"), lut3d,
                            cp.get("profileLumaLutAdvanced"),
                            cp.get("profileLumaContrastAdvanced")).astype(np.float32)
        c = s @ SRGB_TO_PROPHOTO.T if srgb_basis else s
    out = srgb_encode(np.clip(c, 0, 1) @ PROPHOTO_TO_SRGB.T).astype(np.float32)
    if marble is not None:
        # 引擎最后一级 SIMDMarble 的色差清理(sony/marble.py,逐位):marble=(iso, 色彩降噪档 0..10)
        from llr_worker.sony.marble import apply_marble_chroma_nr
        out = apply_marble_chroma_nr(out, iso=marble[0], chroma_slider=marble[1])
    return out, cp


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
