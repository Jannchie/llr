# Sony Imaging Edge —— 官方 Edit/Viewer 引擎逆向笔记

对 `C:\Program Files\Sony\Imaging Edge` 下 `Edit.exe` / `Viewer.exe`
(版本 **4.0.00.10311**)做静态逆向的结果。目的:吃透 Sony 官方 RAW 显影
管线,指导 LLR 的颜色/色调复刻。

> 证据来源:二进制里未混淆的 MSVC RTTI 类型描述符(`.?AV…`)、资源字符串、
> 磁盘数据资产。**流水线的"阶段名"是 Sony 自己的**;**顺序**是结合命名与影像
> 领域知识的推断,未经反汇编逐指令验证,凡推断处已标注。

---

## 0. 结论速览

- 引擎叫 **`sony_zhacai`**(命名空间),静态编译进 `Edit.exe`(编辑器,含
  `sony_granada` 参数/文档层)和 `Viewer.exe`。**没有独立可链接的解码 DLL,
  没有命令行接口** —— 想用它只能 GUI 自动化或进程内 hook。
- 完整管线 = 黑电平 → 遮蔽校正 → RAW 域降噪 → 去马赛克(Trinity)→ 白平衡 →
  线性色彩矩阵 → 基础 3D-LUT → 主 gamma/色调曲线 → 色相饱和 → Creative 3D-LUT →
  DCV 反卷积锐化 → 锐化/YC 域降噪 → 输出色彩空间。**DRO 是旁路的空间自适应模块。**
- **颜色/色调链全是逐像素运算 → 可被 3D-LUT 精确表示 → LLR 可拟合到逐字节等价。**
- **默认去马赛克 = Trinity = 经典的方向性色差插值算法,不是神经网络**(见 §7)。
  结构完全暴露(`cgen`/`ggen`/`edge`/`corr` buffer + 反 zipper LPF),**可复刻**;
  bit-exact 需反汇编取精确 kernel(无加密权重,可行)。
- **神经网络是"额外增强层",不是基础解码**:`sdm_nn`(SuperDemosaic 的 NN 精修)、
  DRUNet 降噪、srsw 超分 —— 各 88MB **加密** ONNX 模型。这些是可选的高端路径,不可复刻。

---

## 1. 模块与命名空间

| 命名空间 | 角色 |
|---|---|
| `sony_zhacai` | 成像核心引擎。所有 `Zc*` 类:任务图节点、LUT、缓冲、调度。 |
| `sony_granada` | Edit 的参数/文档/编辑层。`GfDocument` 及其 `Modify*` 编辑操作、枚举。 |
| `sony_llvc3_dec` | ARW 无损压缩解码器(样张是 "Sony Lossless Compressed RAW 2")。 |
| `sony_escalibolg` | 辅助模块(用途未定,推断为某类容器/信号)。 |

调度基础设施:`ZcExecutor` / `ZcExecutorManager` / `ZcThread` /
`ZcTransaction(Manager)` —— 任务图 + 线程池 + 事务化重算(改一个滑块只重跑
受影响的下游节点,与 ARCHITECTURE.md 里 LLR 的 LRU 缓存思路一致)。
`ZcTask*` 有三套实现后缀:标量、`SIMD`(CPU 向量化)、`OpenCL`/Vulkan(GPU)。
`TileDev*` / `TileDiv*` 按格式(ARW Full/Half/Rough/CompRaw、AXR、NonARW)做分块并行。

内部构建路径(从 Viewer 泄漏):`C:\Jenkins_git\RAW_Private\TrinityComposite\src\RD\RdPlanar.h`。

---

## 2. 完整解码/渲染流水线(推断顺序)

每一级标注是否**逐像素**(可被 LUT 复刻)还是**空间性**(不可)。

| # | 阶段 | 主要类 | 性质 | 说明 |
|---|---|---|---|---|
| 1 | 解码/预处理 | `ZcTaskPreprocess`, `ZcARW`, `ARWParser`, `ARW10toSR2Converter`, `sony_llvc3_dec` | — | 无损压缩 ARW 解包、黑电平、线性化。`resbuf_bayer`。 |
| 2 | 遮蔽校正 | `ZcShadingCorrection`, `ZcTaskShadeCorrection` | 空间 | 镜头暗角/遮蔽。 |
| 3 | RAW 域降噪 | `ZcTaskRawNrPBX`, `RawNRSIMD`, `RawNRHalf` | 空间 | 去马赛克**之前**在 Bayer 上降噪(LLR 的 `denoise.py` 同思路)。 |
| 4 | **去马赛克** | `ZcTaskDemosaicTrinity`(+ `Rough`/`Half`/`PackedRGB`/`CompRaw`)、`TrinityLPF` | 空间 | **Trinity** 是高质量算法;`Rough` 供预览。反 zipper 由 `ModifyTrinityZipper` 控。 |
| 5 | 白平衡 | `ZcWhiteBalanceFactory` | 逐像素 | 每通道增益。 |
| 6 | 色彩矩阵(**二次多项式**)| `ZcTaskLinearMatrix` / `LinearMatrix16` / `SIMDLinearMatrix` | 逐像素 | 见下方公式。**不是普通 3×3**。 |

> **色彩矩阵实测公式**(反汇编 `ZcTaskLinearMatrix` `0x140382ba0`):对每个输出通道 o
> ```
> out_o = clamp( (a_o·R² + b_o·G² + c_o·B²        // 二次项,float 系数
>               +  d_o·R  + e_o·G  + f_o·B )·k      // 线性 3×3,int16 系数
>               + offset )
> ```
> 只有平方项,无 RG/GB/RB 交叉项。18 个系数(9 线性 int16 + 9 二次 float)存于**每图**
> 参数块偏移 `0x18a4`–`0x18d8`(由 ARW 相机数据 + 白平衡现算,非静态常量),clamp 到
> `[-0x7fff, 0x7fff]`(或压缩档 `[-0x1000, 0x1ff8]`)。**结构可读;系数需从运行时内存或每 ARW 取。**
| 7 | 基础色彩 LUT | `ZcCameraLutMaker4L` / `Argus`, `ZcMainLutMaker` → `"beggining 3D-LUT"` | 逐像素 | 相机基础色彩科学,按机型/代际生成 3D-LUT。 |
| 8 | 主 gamma / 色调 | `ZcTaskMainGamma`(`NonRAW`)、`ZcTaskToneCurve`, `ZcToneCurve`, `ZcTaskYGamma` | 逐像素 | 色调响应曲线。高光滚降在这。 |
| 9 | 色相/饱和 | `ZcTaskHueSaturation` / `SIMDHueSaturation` | 逐像素 | 全局 + 按色相。 |
| 10 | **Creative 3D-LUT** | `ZcTask3DLut`, `ZcLut3D` → `"creative 3D-LUT"` / `"abstract creative 3D-LUT"` | 逐像素 | Creative Look/Style 的观感(见 §5)。 |
| 11 | DCV 反卷积 | `ZcTaskDCV` + `dcv_psf_dst.bin` | 空间 | 基于点扩散函数(PSF)的去卷积锐化 / 细节还原。 |
| 12 | 锐化 | `ZcTaskSharpness` / `SIMDSharpness` | 空间 | 半径/强度/阈值/over-undershoot;Clarity 归在锐化下。 |
| 13 | YC 域降噪 | `RGB2YCC` → `ZcTaskYNR`, `BSNR_Y`/`SIMDBSNR_Y`, `ChromaSuppres`, `SSCS` → `YCC2RGB` | 空间 | 亮度/色度降噪在 YCbCr 域。 |
| 14 | 计算算子(代号) | `ZcTaskSpica`(`OpenCL`/`SIMD`)、`Marble`、`Vatr(Float)`、`AreaComp` | 空间 | GPU/SIMD 重算子;`AreaComp`=区域补偿(推断即 DRO 的局部实现)。 |
| 15 | ITP / HDR 路径 | `ZcTaskITP`/`ArgoITP`/`OpenCLITP`/`SIMDITP`(Vulkan) | 空间 | ICtCp/HLG/HDR 处理路径。 |
| 16 | 几何校正 | `GeometricTransformCorrection`(+SIMD)、`ModifyColorAberrationCorrection` | 空间 | 畸变 / 倍率色差。 |
| 17 | 输出 | `ZcPrivateColorGamma`, `ZcTaskRGBConversion`, `ZcTaskDither`, `ZcTaskEffect` | 逐像素 | 转输出色彩空间 + 抖动。ICC 见 §6。 |

伴随的 LUT/companding:`ZcCCompLut`、`ZcYcCompLut`、`ZcYcLUT`(`"ycComp 3D-LUT"`)、
`ZcLut2D`、`ZcNonLinearities`。

**Pixel Shift 合成(PSMS)**:`CompositPSMSImage`(4from16 / 1fromAll)、
`Tether_Settings/TrinityNshoots` —— "Trinity" 也是像素位移多拍合成引擎的名字,
`DemosaicTrinity` 是其单帧分支。

`compraw.hdr.*` 内部参数:`detailgain`、`mixgainplus`、`mixgainminus`、`drangestep`
—— 压缩 RAW 的 HDR/动态范围合并调参。

---

## 3. 完整编辑操作清单(`GfDocument::Modify*`,41 个)

从 Edit.exe RTTI 完整提取,按 UI 面板归组:

**曝光/影调**
`ModifyExposure` · `ModifyGain` · `ModifyContrast` · `ModifyFade`(对比度下的褪色/哑光)
· `ModifyToneCurve` · `ModifyLevelBlack` · `ModifyLevelShadow` · `ModifyLevelHighlight`
· `ModifyLevelWhite`

**DRO(动态范围优化,空间自适应)**
`ModifyDROMode` · `ModifyDROHigh` · `ModifyDROLow` · `ModifyDROShadowDetail`

**白平衡**
`ModifyWhiteBalanceAsShot` · `ModifyWhiteBalancePreset` · `ModifyWhiteBalanceColorTemp`
· `ModifyWhiteBalanceGrayArea`(取灰点)· `ModifyWhiteBalanceColorRendering`

**色彩**
`ModifyColorMode`(Creative Look/Style)· `ModifySaturation` · `ModifyHue`
· `ModifyHueTurnBlock`(按色相分块调整,类 HSL)

**清晰度/锐化/细节**(→ 阶段 11–12)
`ModifyClarity` · `ModifyRadius` · `ModifyStrength` · `ModifyThreshold` · `ModifyFineness`
· `ModifyOverShoot` · `ModifyUnderShoot` · `ModifyTrinityZipper`(反 zipper 伪纹)

**降噪**(→ 阶段 3/13)
`ModifyNrAmount` · `ModifyNrChroma` · `ModifyNrEdge` · `ModifyNrParams`

**镜头校正**
`ModifyDistortionCorrection` · `ModifyColorAberrationCorrection` · `ModifyPeripheral`(周边光量)

**几何**
`ModifyCropping` · `SuppressCropping` · `ModifyInclination`(水平校正)· `CopyOriginal`

### 滑块是怎么实现的(反汇编实测)

反编译了这些 `Modify*` 方法(`0x1401337e8` 起一批)。架构:

- **命令层极薄,不含数学。** 每个标量滑块签名都是 `Modify<X>(int, RenderBehavior,
  RenderQuality, bool&, unsigned long, int)` —— 只吃一个 `int`(滑块值)。setter 是
  **字节级相同的样板**:把值包进一个 `std::function` lambda,交给统一入队函数
  `FUN_1401349f0` 触发事务化重渲染。Exposure/Contrast/四段 Level/Fade/Gain/Saturation/
  Hue/Clarity 全是这一套。
- **例外**:`ModifyToneCurve(curve_channel, ZcToneCurve, …)` 传的是整条曲线对象,
  **逐通道**(RGB/R/G/B)原样进管线。
- **数学在渲染任务里**,滑块值只是参数,按像素消费:
  - Exposure / Gain → 线性增益(WB/曝光)
  - Contrast / Highlight / Shadow / White / Black / Fade → 合成进 `ZcTaskMainGamma` /
    `ZcTaskToneCurve` 的一条 **1D 传递曲线**
  - ToneCurve → 用户曲线,逐通道 LUT
  - Saturation / Hue → `ZcTaskHueSaturation`(逐像素)
  - **Clarity → 局部对比度,空间性**(依赖模糊亮度掩膜)—— 唯一不是纯逐像素的

**含义**:除 Clarity 外,所有亮度/色调/色彩滑块都是**逐像素传递函数** → 和颜色/色调一样
**可 bit-exact 复刻**(喂灰阶/色块、在给定滑块值下读输出传递函数即可)。这与去马赛克/Clarity
的空间性形成对比。

### 深挖:应用是 1D LUT,合成是表驱动(反汇编实测)

- **应用端** `ZcTaskMainGamma`(`0x14036d220`):逐像素 `floor` 取整 + 相邻两项**线性插值**,
  查一张 1D LUT(参数块偏移 `0x118f6`)。**所有色调滑块最终塌成这一张表。**
- **合成端** `0x14036e920`(造表):**深度表驱动**,不是干净公式 —— 把滑块值与**相机专属
  色调标定表**(`lVar10+0xf00..0xf0c`)按 ISO/曝光比例插值,再**串联多张 LUT**
  (`0x118f6`→`0x318fc`→`0x79962`→`0x89962`),且含 Ghidra 无法恢复的跳转表。
  → **把它抄成"逐位一致的干净公式"不现实。**

**结论(要"完全一致"的正确做法)**:不要抄公式,**直接提取那张 1D LUT** —— 在给定滑块值下
dump `0x118f6` 表(或喂灰阶读输出曲线),在自己代码里查表插值即逐字节一致。1D LUT 没有
浮点顺序问题,这是唯一稳的路。

### DRO(动态范围优化)—— 可逆,空间算法

`ZcTaskAreaComp`/`AreaCompSIMD`(`0x14039a0d0` / `0x140398a60`,3875–5604 字节)+
`ZcTaskVatr`(`0x140366920`,4845 字节)。经典空间局部色调,**CPU 里、无加密、非神经**,
已全部反编译(72 函数)。`ModifyDROHigh/Low/ShadowDetail` 是参数。**可完整学到/重建**,
但因是空间算法(局部亮度估计→自适应增益→融合),无 1D-LUT 捷径,是独立的多轮重建工程。

---

## 4. 枚举 / 模式

从 RTTI 提取的枚举类型(成员名多数不在二进制里,需反汇编或从资源串反推):

| 枚举 | 命名空间 | 含义 |
|---|---|---|
| `ColorSpace` | sony_granada | 工作/输出色彩空间(见 §6) |
| `WhiteBalancePreset` | sony_granada | 白平衡预设(Daylight/Shade/Cloudy/Tungsten/Fluorescent/Flash…) |
| `DROMode` | sony_granada | DRO 模式(Off/Auto/Lv1–5) |
| `NrOption` | sony_granada | 降噪选项 |
| `RenderQuality` | sony_granada | 渲染质量(预览 Rough vs 全质量) |
| `RenderBehavior` | sony_granada | 渲染行为标志 |
| `color_mode` | sony_zhacai | 引擎内色彩模式 |
| `curve_channel` | sony_zhacai | 色调曲线通道(RGB/各通道) |

---

## 5. Creative Look / Creative Style

Edit 同时支持旧 **Creative Style** 与新 **Creative Look**(`EDIT_EDITCREATIVESTYLE`、
`Strings/PaletteTitle/CreativeLook`)。每个 Look 是阶段 10 的一张 `creative 3D-LUT`,
外加 `ModifyFade` 等参数。已见代号/名称:

`ST`(Standard)· `NT`(Neutral)· `VV`(Vivid)· `VV2`(Vivid 2)· `PT`(Portrait)
· `FL`(Film)· `IN`(Instant)· `SH`(Soft High-key)· `BW`(黑白)
以及 Creative Style 名:Vivid / Neutral / Portrait / Landscape / Autumn leaves 等。

> `vendor/adobe-camera-profiles/Camera/Sony ILCE-7CM2/` 里 Adobe 已按这些代号
> 各出一张 DCP(`… Camera ST/NT/VV/VV2/PT/FL/IN/SH/BW.dcp`)。

---

## 6. 输出色彩空间 / ICC

`ZcPrivateColorGamma` + 目录里的 ICC:
`Sony_sRGB.icc`、`Sony_AdobeRGB_1998.icc` / `AdobeRGB_v2.2(_R1).icc`、`sRGB_v2.2(_R1).icc`、
`WideGamutRGB.icc`、`sony_HLG.icc`(`BT.2100 HLG`)、`Sony_IDS_Internal.icc`(内部空间)。
HLG 有专门的工作色彩空间警告与 gamma 处理(`ApplyGammaToHLGStillImages`)。

---

## 6.5 去马赛克:Trinity(经典)vs SuperDemosaic(混合 NN)

**修正一个易误解点:默认去马赛克是经典算法,不是神经网络。**

泄漏的内部构建路径把两条路分得很清:

```
RAW_Private\CompRaw\SuperDemosaic\src\sdm\vulkan\sdm_nn\sdm_nn_vk.cpp     ← NN 精修(可选)
RAW_Private\CompRaw\SuperDemosaic\src\sdm\vulkan\sdm_pre / sdm_post / sdm_noise_add
RAW_Private\CompRaw\CompRaw\compraw_vulkan\Compraw_NR_VK\demosaic\src\itp_process.cpp  ← 经典去马赛克
RAW_Private\CompRaw\SuperDemosaic\bayer-cas\cam_raw_cas\vulkan\cas_process.cpp         ← Bayer 域 CAS
RAW_Private\CompRaw\SuperDemosaic\src\srsw\vulkan\pict_srsw_process_vk.cpp             ← 超分
```

- **Trinity**(`ZcTaskDemosaicTrinity`,+ `Rough`/`Half`/`PackedRGB`/`CompRaw` 变体)=
  **经典方向性色差插值**,CPU/SIMD 与 Vulkan 双实现。这是常规 RAW 显影的基础去马赛克。
- **SuperDemosaic / SDM** = 在经典去马赛克之上叠一层 **`sdm_nn` 神经网络精修**
  (`sdm_pre → sdm_nn → sdm_post`,加 `sdm_noise_add`)+ `bayer-cas` + `srsw` 超分。
  这是 GPU 上的"高端/计算摄影"路径,吃那 4 个加密 ONNX 模型。

**Trinity 算法蓝图**(从 GPU buffer 名 `d_Mat_*` 完整还原,`itp_process.cpp`):

1. **预滤波** `pre_fil_h/v_{101,10201,10601}`、`pre_lpf{,2}_{bg,rg,g}_{h,v}` ——
   对 green 及色差信号(bg=B−G、rg=R−G)做水平/垂直方向的低通,得多尺度候选。
2. **边缘检测** `edge_pre_{r,g,b}`、`edge_{r,g,b,gg}`、`edge_{r,g,b}3` —— 各通道边缘图,
   用于判方向。
3. **Green 生成** `ggen_*`:`dir_dif_dir`(水平/垂直方向判定)、`gsat`(饱和度感知)、
   `sb_sq_max7`、`gs_dratio`/`sb_sbratio`/`sb_amix_ratio`(比例混合)、`glmt_{high,low}`
   (green 限幅,即反 zipper)。
4. **Chroma 生成** `cgen_*`:对 bg/rg 各算水平/垂直候选的 `min`/`max`/`absmin`,
   方向性度量 `fm_*`,输出 `chroma_{bg,rg}_{h,v}`;近中性区单独处理
   `achro_*`(`achro_rate_mb/d`)。
5. **h/v 混合 + 校正** `chvmix_{R,B}`、`ghvmix_G`、`corr_{VTY,HTY,VRmB,HRmB}_abs`、
   `corr_{G,CrCb,hcna}` —— 按方向度量把水平/垂直估计融合,并做残差校正。

这属于 DDFAPD / AMaZE / RCD / DLMMSE 一族的**方向性色差去马赛克**,是 Sony 自调的变体。

### 反汇编实测结论(Ghidra 12 + pyghidra,2026-07)

对 `Edit.exe` 里 `ZcTaskDemosaicTrinity` 的 vtable 做了完整反编译(4 个质量档 run 方法
`0x1403725b0 / 0x140372bc0 / 0x140373540 / 0x1403731d0` 及其调用,共 48 函数、1859 行 C):

- **CPU 代码里的"去马赛克"只有:黑电平 → 白平衡增益 → 2×2 binning。** 每个输出像素读一个
  RGGB 四元组,输出 `R=样本0>>2`、`G=(样本1+样本2)>>3`、`B=样本3>>2`,clamp 到 0x7fff。
  **零方向性插值** —— 全 1859 行里没有任何 min/max/边缘/方向选择逻辑。输出是半分辨率。
- 那份漂亮的方向性蓝图(`cgen`/`ggen`/`edge`/`corr`)的构建路径**全部在 `…\vulkan\…` 下**
  → **全分辨率高质量 Trinity 只在 GPU(Vulkan)路径**,CPU 只有半分辨率 binning 兜底。
- 扫描所有 exe/dll(两种字节序)**找不到任何明文 SPIR-V**;shader 疑似像 ONNX 模型一样
  加密/运行时生成。

**→ 结论:用反汇编做到"Sony 全质量去马赛克的逐像素复刻"不可行。** 可读的 CPU 代码不是真算法
(只是 binning),真算法在 GPU 且 shader 不可得。这条路到此为止。

**因此去马赛克的现实选择**:用开源同族方向性算法(RCD / AMaZE / LMMSE / DLMMSE)做到**视觉
等价**(非 bit-exact);或要 Sony 原厂像素,只能驱动 Viewer 出片。`sdm_nn` 神经精修那层无论如何不可复刻。

---

## 7. 神经网络部分(增强层,不可复刻)

**位置**:`C:\ProgramData\Sony\Imaging Edge\OnnxData\`

| 文件 | 大小 | 推断用途 |
|---|---|---|
| `5B5427F6.dat` `9981FFE2.dat` `D9B16073.dat` `FCED1D7F.dat` | 各 ~87.85 MB | 4 个 ONNX 模型(按 hash 命名) |
| `sdmsettings.dat` | ~0 | SDM(学习式去马赛克/超分)配置 |
| `srsettings0000.tbl` | 0.08 MB | 超分(Super-Resolution)设置表 |
| `dcv_psf_dst.bin` | 0.06 MB | DCV 反卷积的点扩散函数 |

**证据串**(Viewer.exe):`onnx model: %s`、`onnxruntime.dll`、`OnnxData`、
`DirectML devices:`、`Failed to vulkan device`、`drunet.dat`、`sdmsettings.dat`、
`process_noise_add`、`Failed to run sdm`、`cas_process`、`Cas table from ARW-param file`。

- `drunet.dat` → **DRUNet**(深度残差 U-Net 降噪网络,Zhang et al. 的 Plug-and-Play
  去噪先验)—— Sony 的高质量降噪是神经网络。
- **SDM = SuperDemosaic**(不是"纯学习去马赛克"):经典方向性去马赛克 + `sdm_nn`
  神经精修的混合体(见 §6.5)。**SRSW/SR** → 超分(`srsettings`)。
- 三套推理后端:**onnxruntime 1.18**(CPU)+ **DirectML**(GPU)+ **Vulkan**。
- **模型加密**:4 个 `.dat` 共享同一 magic 头 `1d 62 19 da ee 92 9e 5d`,其后高熵、
  无明文算子 —— 权重加密存盘,运行时解密(呼应 RTTI 里的
  `TIFFSourceAccessorDecrypted`)。→ 连权重都拿不到。

---

## 8. 对 LLR 的直接启示

把上面映射到 `apps/worker` 的槽位:

| Sony 阶段 | LLR 现状 | 复刻可行性 |
|---|---|---|
| 白平衡 + 线性矩阵 + 基础/creative 3D-LUT + 色相饱和 + 色调曲线(阶段 5–10) | `dcp.py` 套 Adobe DCP;`fit_profile.py` 拟合 HueSatMap 残差 | **逐字节可达**(逐像素→LUT)。这是主战场。 |
| 去马赛克(Trinity)| LibRaw demosaic | 只能开源近似,非 bit-exact。 |
| RAW/YC 降噪(DRUNet 等)| `denoise.py`(经典)| ML,不可复刻,只能近似。 |
| DCV/锐化/Clarity | WebGL 侧 clarity | 观感近似,非 Sony 算法。 |
| DRO(空间自适应)| 无 | 逐像素表不了;`fit_profile.py` 已正确地 `fit_value=False` 放弃 value 轴。 |

**`fit_profile.py` 的瓶颈是训练目标不是语言。** 内嵌 JPEG 里 DRO 已烘死、8-bit、
sRGB 裁切 → tone/高光轴够不着。**升级路径**:用 Imaging Edge 把 `samples/` 的 ARW
显影成 **16-bit AdobeRGB TIFF、DRO 关闭、固定 Creative Look** 作真值 → 阶段 8 的
色调曲线与阶段 5–10 的完整 3-轴校正都能拟合,把"高光/暗部"这条轴救回来。内嵌
JPEG 那条保留作零成本兜底。

---

## 9. 若要更深(可选)

- **提取 `LinearMatrix` 常数 / 基础 LUT**:Ghidra/IDA 反汇编 `ZcTaskLinearMatrix`、
  跟踪 `ZcCameraLutMaker*` 如何从内嵌相机色彩表填 LUT。可得"可读公式",但对逐像素
  段而言,密集探针拟合已能得到等价结果,性价比低。
- **合成探针 ARW**:因 ARW 是 Lossless Compressed RAW 2,不能直接覆写传感器数据;
  需相机切 Uncompressed RAW 拍探针,或实拍 ColorChecker + 灰阶。
- **动态验证**:进程内 hook `GfDocument::Modify*` / `ZcTask*::run` 观察中间缓冲,
  可确证阶段顺序与参数语义(本文顺序为推断)。
- **边界**:去马赛克 + DRO + 神经降噪/超分 = Sony 引擎本体专有,任何语言都拿不到
  bit-exact;要它们只能驱动 Viewer 批处理出片。

---

*方法:MSVC RTTI(`.?AV…`)类型描述符 + 资源字符串 + 磁盘资产静态分析。
顺序与部分代号语义为推断,已在文中标注。*
