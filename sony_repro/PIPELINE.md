# Imaging Edge Edit.exe 渲染管线逆向笔记

基于 `C:\Program Files\Sony\Imaging Edge\Edit.exe`,引擎命名空间 `sony_zhacai`。
RVA 相对模块基址(ASLR 下每次不同,用 `Process.getModuleByName('Edit.exe').base`)。

## 0. 自动化前提(重要)

Edit.exe **接受命令行传图**:

```
Edit.exe <path-to.ARW>
```

配合 `frida.spawn([EXE, arw])` 即可在渲染前装好钩子、全自动完成一次完整解码,
**无需任何 GUI 操作**,单张约 6 秒。此前卡住整个逆向的「必须人工载图」问题由此消除。

## 1. GPU 并不参与颜色处理

实测拦截 OpenGL:

- `wglGetProcAddress` 全程只被请求 1 个函数 → 用的是 **OpenGL 1.1 固定管线**
- 全部纹理上传均为 `GL_BGRA / GL_UNSIGNED_BYTE` 的 1024x1024 图块
- uniform 上传数量为 **0**

结论:**GPU 只负责把 CPU 算完的 8-bit 成品贴上屏幕**,整条影像管线都在 CPU 上。

## 2. 任务类与 vtable

无符号表(仅 9 个导出),但保留完整 **MSVC RTTI**。由 RTTI 名字符串可反查
每个 `ZcTask*` 类的 vtable:

```
TypeDescriptor      = 名字符串偏移 - 0x10
COL.pTypeDescriptor = COL + 0x0c   (4 字节 RVA)
vtable              = 指向 COL 的那个 qword 位置 + 8
```

所有 `ZcTask*` 的 vtable 布局一致,**slot 7 即执行函数**。

## 3. 实际执行顺序(预览质量路径)

```
Preprocess → TileDivRough → DemosaicRough → Vatr
→ SIMDGeometricTransformCorrection
→ SIMDLinearMatrix16          ← 分段色彩矩阵
→ MainGamma                   ← 色调 1D LUT
→ RGB2YCC → TileDiv → RawNRSIMD → ChromaSuppres
→ YGamma → YCC2RGB
→ SIMDITP → SSCS → AreaCompSIMD → SIMDSharpness → SIMDSpica → SIMDMarble
```

**未触发**:`ZcTask3DLut`、`ZcTaskLinearMatrix`(非 16 位)、`ZcTaskLinearMatrix16`
(非 SIMD)、`ZcTaskToneCurve`、`ZcTaskEffect`、`ZcTaskHueSaturation`、`ZcTaskDither` 等。

> 早前读不到矩阵系数,是因为钩的是 `ZcTaskLinearMatrix`(**不在执行路径上**),
> 其字段自然恒为 0。真正在跑的是 `SIMDLinearMatrix16`(RVA 0x37e530)。

## 4. 渲染设置块 `lv`

各 task 的第二个参数 `a[1]` 经固定指针链取得:

```
lv = *(*(a[1] + 0x68) + 0xc8)
```

| 偏移 | 内容 |
|---|---|
| `+0x118f6` | MainGamma 的 tone LUT,32768 x int16 |
| `+0x91998` | LinearMatrix16 参数区(见下) |
| `+0x91bb8` | 参数区内 `+0x220`:使能标志 |

## 5. LinearMatrix16(已完全解出,逐位验证)

它**不是**一个全局 3x3 矩阵,而是**按色相分段**的一组矩阵。

### 5.1 参数区布局(相对 `lv+0x91998`)

```
+0x004  17 x int16    节点表 0, 0, 64, 128, ..., 960
+0x028  6 x 16 float32  节点系数(恒为 1/1024 的整数倍)
+0x1a8  16 x float32  全 1
+0x214  3 x float32   三个偏移量,被 vbroadcastss 成向量(实测为 0)
+0x220  4 x int16     标志 (1, 1, 2, 0)
+0x228  1024 项矩阵数组,步长 72 字节
          前 9 个 float = 3x3 矩阵
          后 9 个 float = I - M     (实测逐位吻合)
```

### 5.2 节点矩阵

六行节点系数依次是 `m01, m02, m10, m12, m20, m21`,**存表时取负**;
对角元不存,由「每行和为 1」推出 —— 即矩阵**保持中性灰不变**:

```
m01 = -c[0][bin]   m02 = -c[1][bin]
m10 = -c[2][bin]   m12 = -c[3][bin]
m20 = -c[4][bin]   m21 = -c[5][bin]
mii = 1 - (该行两个非对角元之和)
```

### 5.3 展开成 1024 项(**环形**插值)

```
bin    = idx // 64
M[idx] = lerp( K[bin], K[(bin + 1) % 16], (idx - 64 * bin) / 64 )
```

末段 960..1023 **插回节点 0**(色相角本就是圆),不是钳位也不是外推。
实机全量核对:1024 项 x 9 个元素,与内存中的数组最大差 **0.0**。

### 5.4 逐像素索引 = 色相角

kernel `0x37f8e0` 的做法:

```asm
vmovups   ymm1, [rdi]              ; 索引平面,逐像素一个 float
vcmpgeps  ymm0, ymm1, [上限]
vblendvps ymm2, ymm1, [上限], ymm0 ; 钳位
vcvttss2si eax, xmm2               ; 截断成整数
lea       rax, [rbx + rbx*8]       ; idx * 9
vmovss    xmm1, [param + rax*8 + 0x228]
```

索引平面由 `0x3810b0` 生成,是一个 **0..1023 的色相角**(整圈 1024)。
其中用的是定点 atan 近似,常量:多项式系数 `366 / 109 / 37`,`x0.875`,
`x1/512`,象限偏移 `±256 / ±512 / ±768 / ±1024`。

实测(4 张图共 55 万高彩度像素)确认索引**只依赖色度方向**:按八分区标定后
残差中位 1/1024。本仓库改用真实 `atan2` + 标定 LUT 等价复现
(`data/hue_index_lut.npz`,`tools/build_hue_lut.py`),精度随彩度提升:

| 彩度 | 残差中位 | p90 |
|---|---|---|
| 0.25~0.40 | 6 | 17 |
| 0.40~0.60 | 2 | 8 |
| 0.60~0.80 | 1 | 5 |
| 0.80~1.00 | 1 | 2 |

低彩度处误差**无害**:所有分段矩阵每行和都为 1,中性色恒等映射。

### 5.5 节点表从哪来:**ARW 里的原厂标定数据**

参数区的唯一写入方是 `+0x179390`,而它是个**纯解包函数**,不做任何计算:
从 `rdx+0xae` 读 23x12=276 字节,当 69 个小端 uint32,按位域拆开写进参数区。
`d[0..5]` 是节点位置表与标志,`d[6+i]` 各装两个 14 位定点系数(高位在前),
定点单位常量在 `+0x4DEB78`,值恰为 1/1024。负数编码是
`-((~x | 1) & 0x3fff)`,**不是常规补码**。

那 276 字节原样来自 ARW 的加密 SR2SubIFD,**tag 0x780f**:

```
IFD0 tag 0xc634 (DNGPrivateData,内联 uint32 = SR2Private IFD 偏移)
  └─ SR2Private:  0x7200 offset / 0x7201 length / 0x7221 key
  └─ 解密后 SR2SubIFD:  tag 0x780f (undefined, count=276)
```

解密同 exiftool `Sony.pm` 的 `Decrypt`(大端 uint32 异或密钥流)。
**65 张实测:离线解出的表与引擎内存逐位一致(最大差 0)。**
实现在 `sony_repro/sr2.py`;`SegmentedMatrix.from_arw()` 一行取用,不需要 frida。

> 早前「表 = f(创意外观, 白平衡),规则未解」是误判 —— 它不是任何东西的函数,
> 是相机逐帧写进 RAW 的标定数据。当时看到的怪象(45/56 张表完全相同、
> 零星取值不随白平衡单调、连拍相邻帧跳表)由此全部解释。

## 6. Tone LUT 由创意外观决定(**不是**按图自适应)

65 张实测:tone LUT 只有 **2 条**不同的曲线,且

- 按 `CreativeStyle` 分组,**组内 100% 一致**(15 → 曲线0 峰值 16365;16 → 曲线1 峰值 16368)
- `DynamicRangeOptimizer` 改变(0 vs 3)**不影响**这条曲线

> 早前「tone LUT 按图自适应」的判断来自两张创意外观不同的图,是误判。
> 结论修正:曲线 = **外观的出厂曲线** + **机内微调**,两者都不随场景变化。

曲线的**输出是 display-encoded 的**(原点斜率 ~12,正是 sRGB 的 toe),
横轴白点 8192,曲线饱和于索引 8084~8140 —— 都在白点之下,
即 Sony 的显示白**没有**高光余量。

### 6.1 曲线也在 ARW 里,而且十种外观各一份

与节点表同理,曲线根本不用 dump。SR2SubIFD 的 tag **`0x74c0`** 是 uint32[10],
指向十份平行的 **SR2DataIFD**(顺序 ST/VV/NT/PT/FL/VV2/IN/SH/BW/SE),
每份自带 `0x780f`(矩阵)、`0x7805`/`0x7806`(128 点曲线)、`0x7770`(外观名)。
**与拍摄时选了哪一种无关。**

标度:`x/128` = LUT 索引,`y/16` = 输出。锚点是曲线的饱和位置 `x = 2^20`,
而 `2^20 / 128 = 8192` 正好落在此前独立测出的白点上。
让引擎轮流按十种外观渲染同一张图,**十条曲线全部命中,最大差 8/16384 = 0.05%**。

### 6.2 机内微调是独立的第二层

Highlights(`0x2033`)/ Shadows(`0x2032`)/ Fade(`0x2034`)是 MakerNotes 里的
**明文 int32**,引擎把它们叠在出厂曲线之上:

| 字段 | 峰值位置 | 单档幅度 | 作用区间 |
|---|---|---|---|
| 高光 − / + | 1537 / 972 | 89.3 / 103.7 | 中调为主 |
| 阴影 − / + | 164 / 73 | 116.0 / 81.6 | index 1–441 |
| 淡出 | — | **0.0** | 对色调曲线完全无作用 |

对档位**严格线性**(±9 定出的单位形状回推中间各档,残差 3/16384),
但正负形状不同、每种外观各一套 → 40 条实测形状。超出 ±9 引擎当非法值,渲染同 0。

**决定性验证:** 一张微调全零的图,改成 Highlights −6 / Shadows +1 重渲染,
与另一批相隔数月、机内就设着这两个值的照片**逐位相同(最大差 0)**。
这同时解开了 `fl_test` 那桩悬案 —— 它不是 ISO 的问题,是它的微调为零而那批不是。

> **切换外观靠改 MakerNotes 的字符串字段 `0xb020`**(值用 DataIFD 自报的拼写,
> 是 `Standard` 不是 `ST`)。只改 `0x0037` / `0xb029` 那两个数值字段**毫无效果**。
> 这三个字段的文件偏移**逐文件变化**,每次都要用 `exiftool -v3` 现找。
> 见 `tools/look_sweep.py` —— 一张图跑五十次,十种外观 × 微调档位全测完。

## 6.5 已接入主管线

见 **`INTEGRATION.md`**。要点:

- Sony 喂给矩阵的像素与 `postprocess_camera_native()` 的输出是**同一个空间**
  (整幅对齐后逐像素相对差中位 0.20%,白平衡一致到 0.3%)
- 代码在 `apps/worker/src/llr_worker/sony/`,前端「颜色引擎」下拉切换
- 与**正确匹配**的 DCP 路径相比互有胜负(原先「2~9 度 vs 9~15 度」的对照
  拿 ST profile 比 FL/VV2 的图,不成立);sony 的 RMSE 普遍更好
- **不再烘焙任何曲线常量** —— 逐图从 RAW 现读,微调按 exif 现叠
- BW / SE 整条回落 DCP:它们的色彩矩阵是**单位阵**,去色全在未复刻的 YCC 段

### 6.6 剩余缺口在色度

与机内 JPEG 逐像素比(9 张 DRO 关、微调零的 VV2):**亮度已完全对上**
(各区间差 <2%),缺的**只有色度**。色度增益强烈随色相变化(实测 0.64~1.45),
带几度色相旋转;合并中位约 1.2,但单张之间差异极大(DSC03015 是 1.23,
DSC03022 只有 0.88)—— **不是一个常数增益**。

这个缺口的来源已经查清,是 §7 的 RGB2YCC —— **不是 SSCS**(见 §7.4)。

## 7. YCC 段:饱和度真正的调节处

`MainGamma` 之后管线转进 YCbCr,而**这一对变换并不互逆** —— 差额就是各创意外观的
饱和度与色相风格。实现在 `sony_repro/ycc.py`。

### 7.1 RGB2YCC(RVA 0x36d690)不是 BT.601

```
Y  = (R*2432 + G*4864 + B*896) >> 13          # 权重在 lv+0x218f6,紧跟 tone LUT 之后
u  = R - G;   v = B - G                        # 色差取的是绿色差,不是 Y 差
v2 = cross[u >= 0 ? p1 : p3] * u + v           # 交叉耦合,两边都看对方的符号
u2 = cross[v >= 0 ? p0 : p2] * v + u           # 而且用的都是尚未修改的 u / v
Cr = gain[u2 >= 0 ? p5 : p7] * u2              # 增益也按符号分岔
Cb = gain[v2 >= 0 ? p4 : p6] * v2
```

`cross = (short >> 2) / 256`(9 位有符号),`gain = ((short >> 3) & 0xff) / 128`
(内部是 `/64` 再 `*0.5`)。**按符号分岔的增益产生随色相变化的增益,交叉耦合产生色相
旋转** —— 正是 §6.6 实测到的那两样东西。

### 7.2 八个参数就在 ARW 里

`0x7842` 是基准,`0x7843`/`0x7844`/`0x7845`/`0x7846` 是四组光源增量,按权重插值:

```
p[i] = 0x7842[i] + (Σ_k 0x784(3+k)[i] * w[k]) >> 10        # 1024 = 1.0
```

实机核对:DSC03015/VV2 抓到的基准与三组增量,与离线读出的 tag **逐位相同**;
该图权重是 `(1024,0,0,0)` 而第一组全零,所以结果就是基准本身。

**BW 的八个值全是 0** → 增益为零 → `Cb = Cr = 0` → `R = G = B = Y`。
黑白外观的去色就发生在这里,不需要任何额外机制。(SE 的这组值与 ST 相同,
它的染色在别处。)

### 7.3 YCC2RGB(RVA 0x3713e0)是标准 BT.601

定点系数 14020 / 7141 / 3441 / 17720,除以 10000,输出钳在 14 位。
色度以 0x8000 为中点存成 uint16。**回程没有任何风格化** —— 全部风格都在正向那一步。

### 7.4 SSCS 已完全解出,但它不动画面

`ZcTaskSSCS`(RVA 0x3860f0)是**高光去饱和**,float 与整数两条路径同构:

```
若 R > lo0 且 G > lo1 且 B > lo2:
    t = min(128, (G - lo1) * 128 / (hi - lo1))
    s = 由 min(R, B) 同样归一化(超过 hi 记 128)
    k = 128 - s * t / 128                       # k/128 是保留下来的色度比例
    Y'= (32R + 64G + 32B - k*(R-G)*32 - k*(B-G)*32) / 128
    R'= Y' + k*(R-G)/128;  B'= Y' + k*(B-G)/128;  G' = Y'
```

参数在渲染设置块里:`+0xf82` 是 hi、`+0xf84/86/88` 是三个 lo,`+0x1207..0x120a`
四个 byte 按 `+0x84..0x90` 的分派累加成三个权重。实测 hi=7934,lo 全是 2380,
权重 `[32,64,32]/128` 正是亮度权重(所以三个平面就是 R/G/B)。

**但实测它一个像素都没改**:抓下 SSCS 入口的整幅(1080x632, uint16),值域只有
0..82,远在 lo=2380 之下,触发率 **0.0%**。而且那一幅与最终画面的相关系数约等于 0,
它处理的根本不是我们看到的图像。**复刻默认渲染不需要 SSCS。**

### 7.5 ChromaSuppres(RVA 0x36e920):只管两头

```
f = 255,  Y < lo 时按 (lo-Y) 线性衰减, Y > hi 时按 (Y-hi) 衰减, 钳在 [0,255]
Cb, Cr  *= f / 255
```
lo/hi 随 ISO 插值。**中间调 f=255,完全不动** —— 它是降噪用的,不是风格。

### 7.6 现状:方向对了,量还差两成

只把 §7.1 与 §7.3 接在 MainGamma 之后(DSC03015/VV2,对机内 JPEG):

| | 色度比(1.0 = 对上) | 平均 RGB |
|---|---|---|
| 矩阵 + 曲线 | 1.2321 | 59.1 52.8 47.0 |
| **再加 YCC 段** | **0.8162** | **60.6 53.0 43.1** |
| 引擎 | — | 59.5 51.4 43.9 |

平均 RGB 明显更近(B 通道尤其),但色度从欠两成变成过两成 —— 中间还少一步压制。
`ChromaSuppres` 与 `SSCS` 都已排除,剩下的嫌疑是 **SIMDITP**(RVA 0x3ae920,
4008 字节 AVX)与 **AreaCompSIMD**。**在找到它之前不要把这一段接进主管线。**

## 8. 3D-LUT:已定位、已抓到数据,但**默认路径不执行**

字符串区 `+0x4dde28` 附近有五张 3D-LUT 的标识(紧邻着就是完整的创意外观名表:
Standard / Neutral / Portrait / Landscape / Clear / Deep / Light / Sunset /
Nightview / Autumnleaves / Sepia / Vivid / Real / AdobeRGB):

```
main 3D-LUT / creative 3D-LUT / abstract creative 3D-LUT
beggining 3D-LUT / ycComp 3D-LUT
```

### 8.1 对象与构造

构造函数 **`+0x177930`**(唯一调用点 `+0x175482`,**无条件**),每次渲染调 2 次,
每次建 5 个对象,每个 0x98 字节:

```
+0x00  vtable        +0x70  名字(std::string)
+0x08 / +0x10 / +0x18 / +0x40 / +0x48 / +0x58 / +0x60 / +0x68  数据缓冲区
+0x20 = (33, 33)   +0x28 = (33, 5)   +0x30 = (5, 511)   +0x38 = 2047
```

**网格是 33x33x33**(33³ = 35937)。构造时全清零,数据随后填入,渲染结束即释放 ——
想抓必须在渲染进行中轮询(见 `tools/` 外的 scratchpad 脚本 `grab_lut_buffers.py`)。

`+0x08` 的布局已看清:每 3 个 int16 一组,第一分量走 `0, 512, 1024, ..., 16383`
(33 个点,值域 14 位),走完一轮后换下一层。最高位是标志位(第 2、3 分量恒为 1)。
完整语义尚未确定。

实测五张里**只有两组不同的数据**:`main` 与 `beggining` 逐位相同,
`creative` 与 `abstract creative` 几乎相同(平均差 2.78)。

### 8.2 但它不执行

钩 `ZcTask3DLut` 的 slot7(`+0x36f2c0`)与 `SIMDLinearMatrix16` 的 slot7 对照:

```
SIMDLinearMatrix16.slot7   36 次(每 tile 一次)
ZcTask3DLut.slot7           0 次
```

即**默认预览路径上 3D-LUT 根本不跑**,对象只是被建好、填好、然后释放。
所以复刻默认渲染并不需要它;§5 之后残余的 2~9 度色相误差来自别处
(SSCS 饱和、AreaComp,以及本仓库用标定 LUT 近似的色相索引)。

要继续推进,得先找到触发条件(某个创意外观?导出/全质量路径?),否则逆向它没有验证靶子。

## 9. 工具位置

仓库内 `tools/`:

| 脚本 | 用途 |
|---|---|
| `analyze_batch.py` | 批量结果的标定表 / tone LUT 统计 |
| `correlate_meta.py` | 与 EXIF(创意外观、DRO、白平衡)关联 |
| `fit_matrix_model.py` | 检验节点表的生成模型(PCA / 流形维度) |
| `build_hue_lut.py` | 标定色相角 -> 索引的 LUT |
| `fit_from_tiff.py` | 用导出的 16bit TIFF 拟合整体 profile(保底路径) |
| `compare_profile.py` | 三联对比图 |
| `ycc_probe.py` | 抓 RGB2YCC 的八个色度参数、亮度权重与插值前的标定块 |
| `sscs_probe.py` | 抓 SSCS 的阈值与权重 |
| `sscs_frame.py` | 抓 SSCS 入口处的整幅中间帧(验证前面整条链的靶子) |
| `ycc_check.py` | 离线走一遍 YCC 段,与机内 JPEG 比色度 |
| `frame_check.py` | 与 `sscs_frame.py` 抓到的中间帧逐像素比 |

> **每次 spawn 前要先 `taskkill /F /IM Edit.exe`**:Edit.exe 有单实例转发,上一次的
> 进程还活着时,新 spawn 的会把文件交给它然后自己退出,钩子永远不触发 —— 表现为
> "hooks installed" 之后毫无动静。

会话 scratchpad 下(逆向用,不入库):

| 脚本 | 用途 |
|---|---|
| `sony_spawn_dump.py` | spawn + 命令行载图,抓 tone LUT 与参数块 |
| `batch_dump.py` | 批量逐张抓节点表 + tone LUT |
| `kernel_probe.py` | 抓 kernel 的 RGB / 索引平面与完整参数区 |
| `gl_spawn_dump.py` | 拦截 OpenGL 上传与纹理 |
| `dump_module.py` / `scan_strings.py` / `find_vtables.py` | 模块映像、字符串、RTTI -> vtable |
| `trace_pipeline.py` | 挂钩全部 task slot7,记录执行顺序 |
| `offline_disasm.py` / `dump_consts.py` | 离线反汇编与常量解析(capstone) |
