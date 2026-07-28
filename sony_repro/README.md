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
- **DRO = `ZcTaskVatr`,已完全解出。** 它不对应 `ZcTaskAreaComp`(那个只在 Cb-Cr
  平面做楔形加性修正,整幅色度 ×0.9999)。形式是 `gain = 2^(Tone(Mlog) − Mlog)`:
  `Tone` 是相机写进 RAW 的分段三次 Bézier(`0x781b`/`0x781c`),`Mlog` 是从 RAW
  Bayer 建的 **8×6×14 双边网格**里插出来的局部对数均值。两半都已接进 `apps/worker`
  和 shader,整帧对引擎 **RMSE 0.075/255、97.4% 逐位相同**。
  手动档位同样解出:引擎的 10 条内置预设已抓下来,UI 提供 `关 / 自动 / Lv1..Lv5`。
  详见 `PIPELINE.md` §7.10.4.6 / §7.10.5 / §7.10.7。

  > 两条**已作废**的旧结论,留着防止重蹈:①「主体是逐像素曲线、空间部分只占 3%」——
  > 那是拿分箱中位增益做的统计,把局部均值误当成了像素自身亮度;②「`DRO=Auto` 的档位
  > 由引擎按画面自定、没记下来」—— 定档的是相机,结果以曲线形式写进了 RAW。
- **机内 Clarity 已完全解出并接通。** 它是创意外观的第六项微调(MakerNotes
  `0x2036`),也是六项里**唯一空间性的**那个 —— 所以它不像另外五项那样折进色调
  曲线,而是在渲染器里重建引擎自己的模糊链:1/8 box 降采样 → 5×5 保边均值 →
  3×3 高斯 → 8× 双线性上采样,再把细节差按档位加回 **YCC 的 Y**(Cb/Cr 不动,
  所以复刻时是加到三通道而非按比例缩放)。强度表和阈值都在**相机标定块**里、
  不在文件里,只能 dump(`tools/clarity_calib.py`)。
  引擎的定点实现与 shader 的浮点实现逐级对拍到 RMS 0.0006(`tools/clarity_check.py`),
  详见 `PIPELINE.md` §7.9.1。
- **机内锐化已解出并接通(粗端)。** 7×7 二项式高通 + **硬死区**(阈值只看高通值、
  与强度无关,所以调强不会让新像素通过 —— 这是 Sony 的锐化在平坦天空上一直安静的原因)。
  和 Clarity 不同,它**不需要 dump**:唯一的标定项就写在 RAW 的 `0x78cd` 里,逐张可读。
  shader 转写在引擎自己的真实 tile 上对拍,死区判定 100% 一致、数值差就是省掉的
  `floor`(`tools/sharp_shader_check.py`),详见 `PIPELINE.md` §7.11。
  **两条已知不足**:细端的 `Spica` 没读(机内默认档下它与粗端同为满权重),
  以及核跨的是三个 sensor 像素,所以缩略预览按自己的尺度锐化、只有导出是准的。
- **去马赛克/细节复刻不了**(GPU 加密的神经/Vulkan 路径),不在本包范围。

## 测试

```bash
cd sony_repro
python -m pytest -q
```

每个模块的测试独立:`tests/test_tone_lut.py`、`tests/test_color_profile.py`、
`tests/test_dro.py`。
