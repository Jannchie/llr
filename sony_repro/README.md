# sony_repro

从 Sony Imaging Edge(`Edit.exe` 4.0.00.10311)逆向出的、**可复刻部分**的实现。
三个互相独立、纯 numpy、可单独测试的模块。逆向依据见
`llr/docs/sony-edit-internals.md`。

| 模块 | 对应 Sony | 保真度 | 复刻方式 |
|---|---|---|---|
| `tone_lut` | `ZcTaskMainGamma`(色调滑块) | **bit-exact** | 复刻 floor+线性插值应用语义;LUT 由灰阶测量提取 |
| `color_profile` | 色彩矩阵 + 3D-LUT + 曲线 | **可拟合到逐字节接近** | 矩阵 + 残差 3D LUT 拟合成对样本 |
| `dro` | **`ZcTaskVatr`**(动态范围优化) | **结构性近似** | 归属已更正(原写 AreaComp);实测主体是逐像素曲线 |

## 关键设计取舍

- **色调滑块不是抄公式,是提表。** Sony 引擎里 Exposure/Contrast/高光/阴影/黑/白/Fade
  最终塌成一张 1D LUT,由 MainGamma 逐像素 `floor` + 相邻两项线性插值应用。引擎里造这张表
  的代码是多张标定 LUT 串联 + 相机数据,**无法干净转写成公式**;但因为终点是 1D LUT,
  只要在给定滑块值下提取该表,查表即 **bit-exact**。`tone_lut` 复刻的就是这个应用语义 +
  从灰阶测量重建表。
- **颜色/色调是逐像素函数 → 3D LUT 可精确表示。** `color_profile` 用矩阵 + 残差 3D LUT
  拟合,采样够密即视觉无损。
- **DRO = `ZcTaskVatr`,而且主体是一条逐像素曲线,不是空间算法。** 两条旧结论都推翻了:
  它不对应 `ZcTaskAreaComp`(那个只在 Cb-Cr 平面做楔形加性修正,整幅色度 ×0.9999),
  「无 1D-LUT 捷径」也不成立 —— 按输入值分箱的中位增益解释掉 76% 的增益方差,
  空间部分只占约 3%。`dro` 目前仍是旧的结构性近似,**不是 bit-exact**,
  且它把空间部分当成了核心。另有一道未解的坎:`DRO=Auto` 时档位由引擎按画面自定,
  相机只写下 `Auto`。
- **去马赛克/细节复刻不了**(GPU 加密的神经/Vulkan 路径),不在本包范围。

## 测试

```bash
cd sony_repro
python -m pytest -q
```

每个模块的测试独立:`tests/test_tone_lut.py`、`tests/test_color_profile.py`、
`tests/test_dro.py`。
