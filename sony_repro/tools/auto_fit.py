"""全自动:用 ARW 内嵌的 Sony JPEG 当真值,拟合 color_profile。零手动、无需 Imaging Edge。"""
import io, sys
import numpy as np
import rawpy, imageio.v2 as imageio

sys.path.insert(0, r"C:\Users\Jannchie\AppData\Local\Temp\claude\C--Users-Jannchie\7ea0518c-c552-43a2-a757-54ce4652233e\scratchpad\sony_repro")
from sony_repro.color_profile import fit, delta_e_stats

ARW = sys.argv[1]
raw = rawpy.imread(ARW)

# 输入:LibRaw 解出的相机原生线性 RGB(用相机白平衡,不自动亮度,线性 gamma)
lin = raw.postprocess(use_camera_wb=True, no_auto_bright=True,
                      output_color=rawpy.ColorSpace.sRGB, gamma=(1, 1),
                      output_bps=16, half_size=True).astype(np.float32) / 65535.0

# 真值:内嵌的 Sony 整幅 JPEG(as-shot Creative Look + DRO)
thumb = raw.extract_thumb()
jpg = imageio.imread(io.BytesIO(thumb.data)).astype(np.float32) / 255.0
print(f"linear {lin.shape}  sony-jpeg {jpg.shape}")

# 对齐:把 JPEG 缩到 linear 尺寸(同源同场景,缩放后全局对齐足够做颜色拟合)
from PIL import Image
tgt = np.asarray(Image.fromarray((jpg * 255).astype(np.uint8)).resize(
    (lin.shape[1], lin.shape[0]), Image.BILINEAR)).astype(np.float32) / 255.0
if tgt.shape[:2] != lin.shape[:2]:
    raise SystemExit(f"尺寸不匹配 {tgt.shape} vs {lin.shape}(可能有旋转/裁剪差异)")

# 采样像素对(去掉极暗/极亮饱和的,减轻 DRO/裁剪影响),拟合
src = lin.reshape(-1, 3); dst = tgt.reshape(-1, 3)
L = src.mean(1)
keep = (L > 0.01) & (L < 0.98) & (dst.max(1) < 0.99)
rng = np.random.default_rng(0)
idx = rng.choice(np.where(keep)[0], size=min(200000, keep.sum()), replace=False)
src, dst = src[idx], dst[idx]

# 基线:只做 3x3 矩阵;再看矩阵+残差3D LUT
from sony_repro.color_profile import fit_matrix, ColorTransform
M = fit_matrix(src, dst)
base = ColorTransform(M).apply(src)
print("仅 3x3 矩阵:      ", {k: round(v, 4) for k, v in delta_e_stats(base, dst).items()})

for S in (9, 17, 33):
    ct = fit(src, dst, lut_size=S)
    pred = ct.apply(src)
    print(f"矩阵+{S}^3 残差LUT: ", {k: round(v, 4) for k, v in delta_e_stats(pred, dst).items()})
