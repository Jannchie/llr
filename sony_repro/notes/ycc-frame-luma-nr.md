# M/S 尺寸(YCbCr)帧:Edit 的降噪走的是另一条链(2026-09-16)

* 素材:`DSC01143.ARW`(ILCE-7CM2,**S 尺寸** Lossless Compressed RAW 2,3504×2336,ISO 6400)。
  库里的副本 `~/.cache/llr/sessions/090592c3-…/source.arw`;Edit 导出 `E:\temp_photo\DSC01143-{off,auto}.TIF`(16 位,DRO 机内)。
* 起因:用户导出后放大看眼镜边缘有锯齿。

## 1. 这种文件不是马赛克

S/M 尺寸的 ARW 是机内 demosaic + 缩小后的 **YCbCr 4:2:2 LJPEG**(`sonycc.cpp`),LibRaw 解出来是 (H, W, 4) 的
RGBG 平面、`raw_pattern=None`。LibRaw 把每个列对共享的 Cb/Cr **直接复制**再逐像素 BT.601 转 RGB,可见区 99.9998% 的
列对 (0-1, 2-3, …) R−G / B−G 完全相等 —— 彩色边缘以 2 像素为台阶。Edit 自己的导出也带同样的列对(列对内/跨列对
R−G 差分 2.3 vs 11.9),只是被后面的色度级软化。
`apps/worker/src/llr_worker/sony/ycc_frame.py` 在 postprocess 之前把色度插回像素网格(libjpeg 的 3/4:1/4 三角
滤波,采样点居中 —— 用机内 JPEG 的色度梯度核对过,居中的相关 0.488 高于左/右对齐的 0.439/0.443)。
**这一步只解决色度台阶,不是锯齿的主因。**

## 2. 锯齿的主因:这种帧上 llr 没有任何亮度降噪

* llr 的 RAW 域降噪(RawNR / 小波)只接受 2×2 Bayer,这种帧直接跳过(`denoise … skipped, sensor CFA is not 2x2 Bayer`),
  ISO 6400 的亮度颗粒原样进入 Sharpness/Spica 锐化 → 极硬的颗粒(`tmp/highiso/ycc_variants/B_noLumaNR.jpg`)。
* 用户于是把显示端的双边亮度降噪拉到 100 / 细节 10:双边滤波把颗粒抹成色块,再被锐化 → 水彩状、边缘锯齿
  (`tmp/highiso/ycc_variants/A_user.jpg`,对比图 `edit_vs_llr.png`,与用户的 `DSC01143 (2).jpg` 逐字节相同)。
* Edit 自动档在同一片子上:脸颊 Y 高通 std 19.0 → 11.4、窗帘 14.5 → 8.0(/255,5×5 高通),色度几乎归零;边缘干净。

## 3. Edit 在这种帧上跑的链(`highiso_amount_probe.py … auto`,执行普查)

```
TileDiv → DemosaicPackedRGB → Vatr → SIMDGeometricTransformCorrection → SSCS → SIMDLinearMatrix16 → MainGamma
→ RGB2YCC → AreaCompSIMD → ChromaSuppres → SIMDSharpness → YGamma → SIMDBSNR_Y → SIMDSpica → YCC2RGB → SIMDMarble
```

* **没有 RawNR、没有 ITP**:demosaic 是 `ZcTaskDemosaicPackedRGB`(把 4:2:2 解回 RGB),降噪只有 **`ZcTaskSIMDBSNR_Y`**
  (亮度,YGamma 之后、Spica 之前,在色调映射后的 Y 上跑,0..16383)。
* 抓到的 tile:`tmp/highiso/probes/highiso_probe_ycc2.npz`(`SIMDBSNR_Y_t{0,1,2}_{in,out}`,3 平面 356×500,平面 0 = Y;
  另有 YGamma / SIMDSharpness / DemosaicPackedRGB 的入口出口)。
* `lumanr._bsnr_strip`(静态读出的 `ZcTaskBSNR_Y`)用 **文件标签算出的参数** 对这些 tile 不匹配(逐位 0.5%)——
  但原因不在算法,在参数:见 §3.1。

### 3.1 `ZcTaskSIMDBSNR_Y` 解出(`tools/bsnr_simd_probe.py`)

`ZcTaskSIMDBSNR_Y::execute`(`0x14039bf40`)取的参数与标量版完全相同:阈值表 `calib+0x200d0`、权重表 `calib+0x800d0`、
gain `+0xc00ec`、limit `+0xc00f8`、强度淡出(本路径上 `[r8]->+0x144 != 3`,恒为 0);核心 `0x14039b3e0`:
`0x14039b6f0` 建 3×3 二项式低通(AVX,常量 [1,2,1]/[2,4,2],逐段截断 → 与整数 `/16` 相同)与高通(`psubsw`),
`0x14039c080` 半径 4 的盒均值(`/81` 魔数 `0x51eb851f`,`sar 3`),`0x14039b9b0` 3×3 sigma 滤波(SSE,`pcmpgtw`)。
**唯一的差别**:sigma 滤波把中心行整段做向量比较,中心通道先乘 `[1,0,1,0,…]` 清零再参与比较——于是 `ref < thr`
(近黑像素)时多算一个值为 0 的第九个 tap。加上这一条后 `lumanr._bsnr_strip` 对引擎 **逐位**。

真正不同的是参数。hook 核心入口直接读实参(两张 S 尺寸帧:ISO 6400 / 200;量 0/10/25/50/70/100;边缘 0/50/75/100):

| 量 | 强度 s(标签 32) | 权重 | 阈值 base/plateau |
|---|---|---|---|
| 0 | 0 | 0 | 0 / 0 |
| 10 | 8 | 143 | 6 / 28 |
| 25 | 22 | 358 | 16 / 77 |
| **50(=自动)** | **44** | **716** | 33 / 156 |
| 70 | 52 | 839 | 39 / 185 |
| 100 | 64 | 1024 | 48 / 228 |

全部等于 Bayer 路径的公式(static-rawnr.md §7.3)套在一个**重映射后的 t**上:`t_eff = 40 + 60·t/100`(t ≥ 0)、
`40 + 140·t/100`(t < 0),即中性点在 t = +40 而不是 0 —— 自动档在这种帧上滤得和 Bayer 帧手动 70 一样重。
`s = trunc(tag·(1 + t_eff/100))`,权重 `trunc((t_eff+100)/200·1024)`。回注增益 `= trunc(min(tag,256)·0.6)`
(192 → 115,443 → 153),再过边缘滑块的 `for_edge_slider`(75 → 76,100 → 0,0 → 256 / limit 8184)。
ISO 200 与 6400 两张帧常数相同;是否与 M 尺寸不同未测(手头没有 M 尺寸帧)。
降噪「关」时核心不进入。YNR 在这种帧上照跑(量 > 50),混合百分比用的是**原始 t**(70 → P=40 逐位)。

验证:`bsnr_simd_ycc*.npz` 11 组、10 块 tile、1.8 M 像素,`lumanr.apply_ycc_luma_nr` 从文件标签出发 **0 像素误差**。
固定在 `apps/worker/tests/fixtures/bsnr_ycc_tile.npz`(4 块裁切)与 `apps/web/src/rendering/__tests__/lumanr_ycc.json`。

### 3.2 落地

* worker:`sony/lumanr.py` `ycc_luma_nr_block / ycc_luma_nr_params / apply_ycc_luma_nr`;YCC 帧的 profile 多一块
  `profileLumaNr`(标签 + bias/gainScale),马赛克帧没有——块的有无就是开关。
* 浏览器:`rendering/sony-lumanr.ts` 按量/边缘滑块建参数,`passes.ts BSNR_SHADER`(单 pass,11×11 读取,整数运算)
  接在 YNR 的位置(锐化之后、Spica 之前),`denoise.enabled` 门控(与 Marble 相同,引擎「关」时不进这一级)。
* 成品(`tmp/highiso/ycc_variants/F_bsnr_auto.jpg`,对比图 `bsnr_result.png`):眼镜边缘干净,与 Edit 自动档同貌。
  Y 高通 std:脸颊 llr 27.0 → 16.2(×0.60),Edit 19.0 → 11.4(×0.60);窗帘 23.2 → 13.0(×0.56),Edit 14.5 → 8.0(×0.55)
  —— 降噪比逐点相同,剩下 ~1.4× 的差距在进入这一级**之前**(llr 在锐化后的 Y 就比 Edit 噪;见 §4)。

## 4. 进入 BSNR_Y 之前 llr 的 Y 比 Edit 噪 ~1.4×:是 GTC 的重采样(已解决)

无论降噪开关,llr 成品的 Y 高通 std 都是 Edit 的 1.4–1.6 倍(关:27.0 vs 19.0;开:16.2 vs 11.4),而且**随半径增大**
(按 292 px 网格,中心 ×1.2 → 边角 ×1.8)。逐级排除(`highiso_probe_ycc_gtc.npz`,抓 Vatr / GTC / SSCS / LinearMatrix16):

| 级 | 对亮度高通噪声 | 结论 |
|---|---|---|
| DemosaicPackedRGB | 与 LibRaw 解码(经色度插值)G 通道 hp-std 38.6 vs 38.1,列对色度无成对 | 引擎自己也做色度插值,噪声相同 |
| Vatr | 33.06 → 33.06 | 只是增益 |
| **SIMDGeometricTransformCorrection** | **33.1 → 23.5 / 46.0 → 30.5 / 39.4 → 29.0(×0.66–0.74)** | **就是它** |
| SSCS | 逐位不变 | 高光去饱和 |
| SIMDSharpness | ×1.9;llr 的公式(amp 0.0488)对 tile 99.998% 在 ±1 内 | 一致 |
| Spica | 成品上关掉只降 8% | 不是主因 |

GTC 的等效核用 9×9 最小二乘拟合是**纯双线性**(2×2、可分离权重,残差 < 1 LSB),它降噪是因为**每个像素都在非整数相位上重采样**
(随机相位的双线性平均把白噪声压到 ×0.67)。而 llr 这张的畸变表是恒等(`DistortionCorrection: Off`,llr 按机身开关不做畸变),
CA 表虽然解析了但 shader 从不用 —— 所以 llr 在像素网格上原样采样,没有这层平均。

GTC 的映射(平面原点标定:输入平面原点 = src-rect − (2,2),输出 = src-rect;子像素位移由核质心得到,精度 ~0.02 px):

* 红/蓝相对绿的径向位移 = exif `ChromaticAberrationCorrParams`(p·2⁻²¹ + 1,16 结,间距 i/15.2)乘以半径 —— 全半径范围
  逐点相符(R−G 预测 −0.219…+0.060 vs 实测 −0.213…+0.025;B−G 预测 +0.547…+0.898 vs 实测 +0.571…+0.912)。
* 绿本身也被缩放:f_G − 1 = −5.2e-4(tile 拟合)/ −6.0e-4(成品分块配准)= **1/(1 + max 全表 pB) − 1 = −6.10e-4**,
  即 Sony 用**表内最大**的 CA 系数做填充缩放,让走得最远的蓝通道不出画幅;按边界点约束算(−2.4e-4)不符。

落地(`rendering/lens.ts` `LensCorr.caR/caB` + `lensCaFillScale`,`passes.ts` 主 pass 三通道分别取样,`u_lensScale` 叠乘):
成品 `tmp/highiso/ycc_variants/K_ca_auto.jpg`,对比图 `final_vs_edit.png`。Y 高通 std:

| 窗口 | llr 自动 | Edit 自动 | llr 关 | Edit 关 |
|---|---|---|---|---|
| 脸颊 | 12.25(原 16.24)| 11.42 | 20.47(原 27.32)| 19.04 |
| 窗帘 | 8.81(原 13.03)| 7.96 | 15.74(原 23.20)| 14.47 |
| 裙 | 8.27 | 8.42 | 10.57 | 10.47 |
| 墙 | 7.97(原 11.84)| 7.04 | 12.40(原 19.53)| 11.13 |

几何:成品逐通道亚像素配准,G/B 全半径 |Δ| ≤ 0.07 px,R 残差 +0.1…0.2 px(成品配准在暗红通道上噪声大;tile 级 R−G 是相符的)。
剩下 7–13% 的噪声差未追(Spica / 色调链的细微差别),肉眼已同貌。这一改动对所有带 CA 标签的 Sony 帧生效 —— Bayer 帧上 Edit 同样跑 GTC。

## 5. 剩下的 5–10%(2026-09-16 晚)

继续逐级对(`highiso_probe_ycc_spica.npz`,`spica_gaincfg_DSC01143-spcfg.json`):

* **Spica 在本帧逐位**:`tools/spica_model.py` + 本帧 cfg,xmm11 = 0.95(= `spica_iso_gain(6400)`)、w = 0.5,
  在引擎的处理 rect(平面内缩 14)内三块 tile 99.999% 逐位、100% 在 ±1 内。cfg 增益 460.8 = 0.9×512(llr 的 gainScale 0.9)、
  rng 曲线 a = 307.2 = 128 + 179.2(llr 的 rangeShift)—— llr 的 ISO 斜坡在 6400 上是对的。
  (注意 `spica_model.run` 默认处理平面内缩 4+3,引擎只写内缩 10+3 的区域,外圈那一环不算失配。)
* **GTC 的映射逐点对**:用 llr 的映射(1/(1+max pB) 缩放 + 表 CA)在引擎 GTC 输入上做双线性,与引擎输出 G 通道 rms 0.29 LSB、
  R/B 0.4–1.5 LSB;259 个窗口拟合 f_G − 1 = −6.048e-4(rms 0.004 px),公式值 −6.10e-4。
* **亮度权重**:引擎的 Sharpness / BSNR_Y / YNR / Spica 都跑在 RGB2YCC 的 Y = (2432, 4864, 896)/8192 上,
  llr 的这四级原来用 Rec.709 权重。已换成 `passes.ts SONY_YCC_Y`;成品噪声再降 ~2%(脸颊 12.25 → 12.04)。
* 现状(Y 高通 std,自动档):脸颊 12.04 vs Edit 11.42、窗帘 8.76 vs 7.96、裙 8.30 vs 8.42、墙 7.97 vs 7.04。
  按半径:中心 ×1.05,边缘 ×1.10(B ×1.2);亮度比也随半径从 1.02 涨到 1.05(B 1.09)——Edit 的 `Vatr` 在周边压了 1–2%
  的增益(t0 −0.7%、t1 −2.1%),llr 没有这一级;剩下的差别大概率就是它和亮度/斜率的二阶效应,已不值得再追。
