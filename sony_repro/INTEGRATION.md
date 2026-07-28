# 接入主管线

**已接入。** 代码在 `apps/worker/src/llr_worker/sony/`(算法从本仓库移植,英文注释)
与 `apps/web/src/rendering/passes.ts`,走前端设置面板的「颜色引擎」下拉选择。
本文档记录当初的可行性分析,以及接入后**实测复现的结论** —— 其中一条推翻了下面 §2.3。

## 1. 两条链路的对位

| | 主管线 | Sony Edit |
|---|---|---|
| 解码 | rawpy/LibRaw,`output_color=raw`,`gamma=(1,1)`,`use_camera_wb` | Preprocess → DemosaicRough |
| 色彩 | DCP:ForwardMatrix → XYZ(D50) → ProPhoto,再 HueSatMap / LookTable | **SIMDLinearMatrix16** 一个分段矩阵,就这一步 |
| 视图变换 | 前端 `viewTransform`,用 DCP profile tone curve 作 `u_profile_lut` | **MainGamma** 一张 32768xint16 的 1D LUT |
| 之后 | 曲线 / 调色 / gamut map / sRGB 编码(display-referred) | RGB2YCC → NR → YGamma → ITP → SSCS → AreaComp → Sharpness |
| 交付 | worker 出 scene-linear ProPhoto(D50) f16,前端渲染 | 引擎直接出 8-bit,GPU 只贴图 |

对位很干净:**LinearMatrix16 对应 DCP 的色彩段,MainGamma 对应前端的 view transform。**

## 2. 三个已验证的对接事实

### 2.1 输入空间完全一致(相对差中位 0.20%)

把 kernel `0x37f8e0` 抓到的、Sony 真正喂给矩阵的 R/G/B 平面,与
`fit_profile.postprocess_camera_native()` 的输出整幅对齐后比较(DSC03015):

```
逐像素相对差   中位 0.20%   p90 3.10%      (p90 的差来自 demosaic 算法与缩放,不是空间差)
R/G 比值之比   中位 0.9994                  白平衡一致
B/G 比值之比   中位 0.9968
最佳对角增益   [0.9943, 0.9946, 0.9907]     ~= 1,不需要任何通道校正
```

**所以 `SegmentedMatrix.apply()` 可以直接插在 `apply_dcp_profile()` 的第一行之前**,
输入就是现成的 `camera_rgb`,不需要任何空间换算。分段矩阵每行和为 1,对整体缩放不变,
色相索引也只看比值 —— 尺度差异天然无害。

### 2.2 tone LUT 的横轴白点 = 8192

LUT 索引的物理单位此前未知。以 `postprocess_camera_native` 的 1.0(LibRaw 白电平)
为基准扫描,四张图独立给出同一个答案:

| | 最佳白点 | 平均绝对差 |
|---|---|---|
| DSC03015 | 8192 | 0.0147 |
| DSC02962 | 8192 | 0.0178 |
| DSC02976 | 8192 | 0.0398 |
| DSC03022 | 8192 | 0.0615 |

与另一条独立测量吻合:kernel 平面 / rawpy 的全局尺度实测 8166。
即 **LUT 索引 = camera RGB x 8192**,输出 `/16384` 即 display 值。
曲线在索引 ~6000 处饱和,也就是 Sony 的显示白比 LibRaw 白电平低约 0.45 EV。

### 2.3 色彩精度:与 DCP 互有胜负,不是压倒性优势

> **这一节原先的结论是错的,接入后重测才发现。** 原表里 DCP 那一列用的是 **ST**
> profile,而那些图是 FL/VV2 —— 拿错 profile 作对照,把 DCP 一方压低了一倍有余。
> 下表是同一批图、同一 mask、两条路径都走 `prepare_linear`(DCP 自动匹配到
> FL/VV2)的公平重测。

| | sony 路径 | DCP 路径(正确匹配) |
|---|---|---|
| DSC03015 (VV2) | **中位 3.73° / p90 9.31° / RMSE 8.6** | 5.51° / 14.23° / **8.3** |
| DSC02962 (FL) | 8.58° / 27.55° / **6.7** | **7.70° / 20.24°** / 7.8 |
| DSC02976 (FL) | **2.01° / 6.72° / 16.1** | 7.61° / 15.05° / 22.3 |
| DSC03022 (VV2) | 8.28° / **35.49°** / **27.7** | **7.01°** / 38.80° / 28.9 |

sony 列与原表几乎逐项相同(3.73 vs 3.71 等),说明**接入链路忠实**——烘焙进
worker 的曲线与逐图 dump 的真值等效。变的只是对照组。

色相误差本身在低饱和场景下并不可靠。直接看图更有说服力:同一张 DSC01157(暗绿叶片),
DCP 明显过亮发灰,sony 的暗部密度和整体基调更贴近直出。

残余误差来自尚未复刻的 SSCS 饱和、AreaComp,以及 **DRO**(机内直出会提亮暗部),
色相索引用的也是标定 LUT 而非引擎的定点 atan。

> ⚠️ 本文这两处(以及下面「其余差距来自 AreaComp 与 DRO」)测于 DRO 解出之前。
> **DRO 现在已经不是差距来源**:双边网格连同曲线都已解出并接进 `apps/worker` 和
> shader,整帧对引擎 **RMSE 0.075/255、97.4% 逐位相同**
> (PIPELINE.md §7.10.4.6 / §7.10.5)。这两段的数字没有重跑。
>
> 手动档位也已解出并落地(§7.10.7):引擎的 10 条内置预设已从运行时结构体抓下来
> 烘进 `sony/dro_presets.py`,UI 提供 `关 / 自动 / Lv1..Lv5`。
> 「机内开了 DRO 但 RAW 没曲线」这个旧缺口一并补上了。

顺带解答了一个悬念:Sony **没有**单独的 camera→标准空间转换矩阵。16 个节点矩阵全部
贴着单位阵(对角 0.96~1.03,非对角 ±0.17),整条色彩链就这一个近单位的分段微调 —— 也就是说
ILCE-7CM2 的原生基色本就接近 Rec.709,Adobe 绕道 XYZ 反而引入了误差。

## 3. 唯一的结构性冲突:逐通道曲线的所在空间

MainGamma 是**逐通道**曲线,作用在 camera/Rec.709-ish 空间;
前端的 `u_profile_lut` 也是逐通道,但作用在 **ProPhoto**。同一条曲线在不同基色下逐通道
应用,结果并不相同,直接复用会有偏差。

**实际解法**(比原计划更省):不加第二个 view transform,而是给 `viewTransform`
加一个 `u_profileCurveSrgb` 开关 —— 曲线所在的基色是 **profile 的属性**,不是用户的
外观选择,所以它跟着 `colorProfile.kind` 走:

```glsl
vec3 s = (u_profileCurveSrgb == 1) ? PROPHOTO_TO_SRGB * c : c;
s = vec3(sample(s.r), sample(s.g), sample(s.b));
return (u_profileCurveSrgb == 1) ? SRGB_TO_PROPHOTO * s : s;
```

曲线本身走现成的 `u_profile_lut` 通道,**协议零改动**。

两点与原计划不同:

* **不需要把 domain 放宽到 [0, 2]。** 原文说「饱和点在白点之上」是错的:65 张实测
  两条曲线分别饱和于索引 8084 与 8140,都在白点 8192 **之下**,`clamp(c, 0, 1)`
  一点内容都没丢。
* **下发的 y 必须先解 sRGB 编码。** Sony 的 LUT 输出是 display-**encoded** 的
  (原点斜率 ~12,正是 sRGB 的 toe),而前端 view transform 的契约是输出
  display-**linear**,最后统一做一次 sRGB 编码。不解就会 gamma 两次。
  解开后往返恒等,默认参数下整条链精确还原 `sony_lut(matrix(camera_rgb))`。

## 4. 曾经的卡点:节点表 —— 已解决(接入时直接用上)

**原本的判断是错的:节点表根本不是算出来的,是相机写进 RAW 的标定数据。**

线索来自反汇编。参数区 `lv+0x91998` 的唯一写入方是 `+0x179390`,而它是个**纯解包函数**:
从 `rdx+0xae` 读 23x12=276 字节,当 69 个小端 uint32,按位域拆成整个参数区,
不含任何计算。`d[6+i]` 各装两个 14 位定点系数,单位恒为 1/1024(常量就写在
`+0x4DEB78`,值 0.0009765625)。

那 276 字节原样来自 ARW —— 加密的 SR2SubIFD 里的 **tag 0x780f**
(type=undefined, count=276)。寻址路径:

```
IFD0 tag 0xc634 (DNGPrivateData,内联的 uint32 = SR2Private IFD 偏移)
  └─ SR2Private:  0x7200 offset / 0x7201 length / 0x7221 key
  └─ 解密后的 SR2SubIFD:  tag 0x780f = 那 276 字节
```

解密算法与 exiftool `Sony.pm` 的 `Decrypt` 相同(按大端 uint32 异或密钥流)。

**全部 65 张实测:离线解出的节点表与 frida 从引擎内存 dump 到的逐位一致(最大差 0)。**

实现见 `sony_repro/sr2.py`,测试见 `tests/test_sr2.py`。
`SegmentedMatrix.from_arw(path)` 一行到位,**worker 里不需要 frida**。

> 顺带解释了之前那些反常现象:56 张里 45 张节点表完全相同、零星取值不随白平衡单调、
> 连拍相邻帧却会跳表 —— 因为它压根不是白平衡的函数,是相机逐帧写的标定数据。
> 之前对 (R/G, B/G) 做回归拟合当然拟不出来。

## 5. 落地结果

| 位置 | 内容 |
|---|---|
| `apps/worker/.../sony/sr2.py` | 解密 + 位流解包 + `look_calibrations()` 读十份平行标定 |
| `apps/worker/.../sony/linear_matrix.py` | 分段矩阵 + `data/hue_index_lut.npz` |
| `apps/worker/.../sony/tone.py` | 曲线重建(x/128, y/16)+ 机内微调叠加 |
| `apps/worker/.../sony/profile.py` | 接入层:矩阵 → Rec709→ProPhoto(D50),曲线打包 |
| `apps/worker/.../sony/data/look_tuning.npz` | 40 条微调单位形状(145 KB) |
| `apps/worker/.../sony/clarity.py` | 机内 Clarity(第六项微调)的强度表与模糊链几何,烘焙自机身标定 |
| `apps/worker/.../sony/sharpness.py` | 机内锐化:两条档位阶梯 + 死区,标定从 RAW 的 `0x78cd` 逐张读 —— **不需要 frida** |
| `apps/worker/.../cli.py` | `ColorRenderer` / `resolve_color_renderer` / `render_color` |
| `apps/web/.../passes.ts` | `u_profileCurveSrgb`;`CLARITY_*` 三个小 pass(降采样 / 保边均值 / 高斯)+ `SONY_POST_SHADER`(锐化与 Clarity 合成,按引擎的先后顺序) |
| `apps/web/.../pipeline-renderer.ts` | 两级 post 的 FBO 链,挂在 `renderPass()` 里 —— 预览 / 导出 / 直方图共用那一个入口,不必各接一次 |
| `apps/web/App.vue` | 「颜色引擎」下拉 + 创意外观显示 + 回落提示 + 六个微调滑块 |

测试 `apps/worker/tests/test_sony.py`,含对 `samples/` 里真实 ARW 的断言。

研究工具在 `tools/`:`look_sweep.py`(切外观 + 扫微调档位,一张图跑五十次)、
`export_tuning.py`(归纳成单位形状)、`sat_probe.py` / `chroma_probe.py` /
`chroma_batch.py`(与机内 JPEG 逐像素比,定位剩余缺口)。

### 十种外观全部离线可得,不再烘焙任何常量

**「按外观烘焙曲线」那个设计已经废弃 —— 它本身就是错的。** 烘焙进去的所谓
「FL 曲线」其实混进了那批照片的机内 Highlights −6,被当成了外观的定义。
这正是 `samples/fl_test`(同为 FL 但微调为零)那桩悬案的成因。

现在曲线**逐图从 RAW 现读**:SR2SubIFD 的 tag `0x74c0` 指向十份平行的
SR2DataIFD,每种创意外观一份完整标定(矩阵 + 曲线),与拍摄时选了哪一种无关。
让引擎轮流按十种外观渲染同一张照片,十条曲线**全部命中,最大差 8/16384 = 0.05%**。

机内微调按 exif 现叠:Highlights / Shadows 对档位严格线性,±9 定出的单位形状
回推中间各档残差 3/16384;正负形状不同、每种外观各一套,共 40 条实测形状存在
`sony/data/look_tuning.npz`。Fade 实测对色调曲线**完全无作用**。

### 仍未复刻的:SSCS 饱和段

与机内 JPEG 逐像素比(9 张 DRO 关、微调为零的 VV2):**亮度已经完全对上**
(各区间差不到 2%),缺口**只在色度**。色度增益强烈随色相变化(实测 0.64~1.45),
伴随几度色相旋转;合并中位约 1.2,但单张之间差异极大(DSC03015 是 1.23,
DSC03022 只有 0.88),**不能当成一个常数增益**。

候选参数是 DataIFD 的 `0x7842` / `0x7844` / `0x7845`(各 int16[8])。强证据:
BW 外观下这些值**全是 0**;后四个值 VV=1026,778,1096 / NT=770,866,922 /
FL=664,658,660 / IN=436,702,402 —— 与各外观实际饱和度的高低完全一致。
但格式未解(值 /1024 都小于 1,而实测需要大于 1,基准不明),
下一步应该去反汇编 `ZcTaskSSCS`,而不是继续猜格式。

**BW 与 Sepia 整条回落 DCP:** 它们的色彩矩阵是**单位阵**,去色完全发生在
未复刻的 YCC 段,靠现有两步渲染出来只会是一张带错曲线的彩色图。

其余差距来自 AreaComp 与 **DRO**;`ZcTask3DLut` 在默认路径不执行(见 PIPELINE.md §7)。
