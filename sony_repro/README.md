# sony_repro

从 Sony Imaging Edge(`Edit.exe` 4.0.00.10311)逆向出的、**可复刻部分**的实现。
三个互相独立、纯 numpy、可单独测试的模块。逆向依据见
`llr/docs/sony-edit-internals.md`。

| 模块 | 对应 Sony | 保真度 | 复刻方式 |
|---|---|---|---|
| `tone_lut` | `ZcTaskMainGamma`(色调滑块) | **bit-exact** | 复刻 floor+线性插值应用语义;LUT 由灰阶测量提取 |
| `color_profile` | 色彩矩阵 + 3D-LUT + 曲线 | **可拟合到逐字节接近** | 矩阵 + 残差 3D LUT 拟合成对样本 |
| `dro` | `ZcTaskAreaComp`(动态范围优化) | **结构性近似** | 空间局部色调重建,pending 完整反汇编 |

## 关键设计取舍

- **色调滑块不是抄公式,是提表。** Sony 引擎里 Exposure/Contrast/高光/阴影/黑/白/Fade
  最终塌成一张 1D LUT,由 MainGamma 逐像素 `floor` + 相邻两项线性插值应用。引擎里造这张表
  的代码是多张标定 LUT 串联 + 相机数据,**无法干净转写成公式**;但因为终点是 1D LUT,
  只要在给定滑块值下提取该表,查表即 **bit-exact**。`tone_lut` 复刻的就是这个应用语义 +
  从灰阶测量重建表。
- **颜色/色调是逐像素函数 → 3D LUT 可精确表示。** `color_profile` 用矩阵 + 残差 3D LUT
  拟合,采样够密即视觉无损。
- **DRO 是空间算法,无 1D-LUT 捷径。** `dro` 按真实结构(局部亮度估计 → 自适应增益 →
  保色应用)重建,参数对齐 Sony 的 High/Low/ShadowDetail/Mode,但**不是 bit-exact**。
- **去马赛克/细节复刻不了**(GPU 加密的神经/Vulkan 路径),不在本包范围。

## 测试

```bash
cd sony_repro
python -m pytest -q
```

每个模块的测试独立:`tests/test_tone_lut.py`、`tests/test_color_profile.py`、
`tests/test_dro.py`。
