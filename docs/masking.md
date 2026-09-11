# Masking / 局部调整：Phase 1 设计

> **实施状态（2026-09-12）**：§9 的 1–7 步已落地（`rendering/masks.ts`、`composables/useMaskEditor.ts`、`passes.ts` / `pipeline-renderer.ts`、`App.vue` 蒙版 tab、assistant `set_edit.masks[]`、`test_xmp.py`）。与本文的偏差：
> - **预览是黑白 matte 不是红色叠加**：`u_maskPreview >= 0` 时 `disp = vec3(wPrev)`——实现中改的，权重直接可读，红色在彩色内容上看不清。顺带效应：预览开着时直方图读的也是 matte。
> - **`packMasks(groups, crop, srcW, srcH, {temperature, tint}, previewId)`**，返回值直接用 `EditParams` 的字段名（`masks / maskGroups / maskUse / maskPreview / imgFromTex / imgAspect`）以便 `...` 展开；ΔWB 需要全局 temp/tint，所以是参数而不是 renderer 侧算。
> - `radial.angle` 存**角度**（与 `crop.angle` 一致），打包时转弧度；`color.hue/hueWidth` 仍存 Oklab 弧度，UI 与 assistant 以角度进出。
> - 模糊 mask 的 log 位移带上局部曝光（`maskLx + dExpo`），否则 Clarity 把局部曝光当细节放大。
> - **色度门调参**（§10）：颜色选区默认门与 `sky` 用 `MASK_COLOR_C0/C1 = 0.03/0.08`，`skin` 用 `MASK_SKIN_C0/C1 = 0.04/0.10`（都是 C/L 比），不再借 HSL 的 0.012/0.03 与 vibrance 的 SKIN_C0/C1——mixer 的门偏低是为了让低饱和色仍吃滑块，选区要的相反：DSC01157 暗部 bokeh 的噪声斑、DSC04568 暖光白盘边缘半选，都是门太低。代价：叶片（C/L≈0.04）在颜色选区里只有部分权重。
> - UI：luminance 组件暴露 `featherLo/featherHi` 两个羽化（i18n `mask.range.featherLo/Hi`），radial 暴露 feather + angle，linear 只有手柄；assistant schema 的 `feather` 对 luminance 同时设两端、对 radial 是 0..100。
> - 未做：`llr:MaskCount` 结构化字段（`cli.py` 有未提交的外部改动，JSON blob 已带 masks）；P3 一遍（headless 无 P3）；帧时间只在 SwiftShader（软件 GL）上量到 8 组 ≈ 1.7× 无蒙版，16 ms 目标要在真 GPU 上看。
> - 已知限制：`shadows` 的亮度轴是 scene-referred（§2.1，`toneRegions` 同轴），室内暗场景（fl_test）会几乎全选；整屋钨丝灯（fl_test）下 `skin` 的色相窗放过全帧，matte 变成色度噪声斑——Phase 2 guided filter 的事。

## 结论

- **数据**：`Snapshot.masks?: MaskGroup[]`（缺省 → 空）。一个 group = ≤4 个解析式 component（luminance / color / linear / radial，各带 add / subtract / intersect + invert）+ 一组局部滑块（exposure, temperature, tint, saturation, vibrance, highlights, shadows, clarity, dehaze, hue）。上限 8 组。
- **权重**：全部在 `PROCESS_SHADER` 内逐像素算，无新纹理；luminance range 用**逐像素**（4-tap 邻域均值）而不是 ≤256px 的模糊 mask——后者 σ≈3% 画幅，做选区会把亮部溢到暗部一圈。gradient 存在**oriented image-norm 空间**（与 `xf.guides` 同一坐标系，`crop.ts:38`），shader 用一个 `u_imgFromTex` mat3 把 `v_texCoord` 转过去，因此跟随 crop / 拉直 / 旋转 / 透视。
- **融合**：在 WB 之后、tonal block 之前（`passes.ts:601`–`609` 之间）算出所有 group 的 w，然后**混参数不混结果**：`p_px = p_global + Σ w_g·Δp_g`，现有 block 只跑一遍。temperature/tint 例外——WB 是线性矩阵，混矩阵 `M0 + Σ w_g (M_g − M0)` 等价于混结果，CPU 每组算一个 ΔM。
- **预算**：现有 fragment uniform 已占 109 / 224 vec4（WebGL2 保底值），8 组打包还要 144 行，放不下。用一个 **std140 UBO**（2.3 KB，每帧 `bufferSubData`），不占 uniform 寄存器也不占纹理单元。CPU 端跳过"调整全零 / disabled / 无 component"的组；`u_maskGroups == 0` 时整块不执行，默认输出逐 bit 不变。
- **不做**：Contrast / Blacks / Tone curve（display-referred LUT bake，见 §3.3），brush、AI 主体/天空、guided filter（Phase 2）。
- **UI**：新 rail tab「蒙版」：group 列表 → 选中组的 component 参数 → 红色 overlay 开关（view-only `EditParams.maskPreview`，同 803a66b 的 `hsatPreview` 模式）→ 局部滑块。gradient 手柄放在一个与 crop overlay 同构的 SVG 上，坐标映射复用 `outFrameToImagePx` / `imagePxToOutFrame`。
- **Assistant**：`set_edit.masks[]`，以 preset 名为主入口：`{preset:"sky", adjust:{exposure:-0.5}}`。

---

## 1. 数据模型

**Why**：LR 的 mask = 选区（可组合）× 局部滑块。选区参数化后是纯数据，export、XMP、assistant、undo 全部免费跟随。

```ts
// rendering/masks.ts — 与 tonal-model.ts 同一模式：TS 是 tested mirror，常量经 MASK_GLSL 注入 shader
export type MaskOp = "add" | "subtract" | "intersect";
export type MaskComponent =
  | { type: "luminance"; op: MaskOp; invert: boolean; lo: number; hi: number; featherLo: number; featherHi: number } // 0..100, 感知轴
  | { type: "color";     op: MaskOp; invert: boolean; hue: number; hueWidth: number; chromaLo: number; chromaHi: number } // Oklab 弧度 / C/L 比
  | { type: "linear";    op: MaskOp; invert: boolean; x0: number; y0: number; x1: number; y1: number }   // image-norm，x0y0 处 w=1
  | { type: "radial";    op: MaskOp; invert: boolean; cx: number; cy: number; rx: number; ry: number; angle: number; feather: number };
export type MaskAdjust = { exposure: number; temperature: number; tint: number; saturation: number; vibrance: number;
  highlights: number; shadows: number; clarity: number; dehaze: number; hue: number }; // exposure EV，其余 -100..100
export type MaskGroup = { id: string; name?: string; enabled: boolean; invert: boolean; components: MaskComponent[]; adjust: MaskAdjust };
export const MASK_GROUPS = 8, MASK_COMPS = 4, GROUP_STRIDE = 18; // vec4 行：hdr + adjA + adjB + ΔWB×3 + 4×3
```

坐标空间决定：gradient 的位置存 **oriented image-norm**（`ix/iw, iy/ih`，y-down，90° 旋转和翻转之后、透视之前）。理由：`xf.guides` 已经用这个空间（`crop.ts:38`），`outFrameToImagePx`（`crop.ts:256`）/ `imagePxToOutFrame`（`crop.ts:265`）已经是它与屏幕之间的双向映射，crop 面板和 assistant 的 crop schema 也以它为单位（`App.vue:1856`）。radial 的 `rx, ry` 各按自身轴归一（rx=ry=0.5 是内切画幅的椭圆），rotation 在等比空间里做（shader 把 y 乘 `ih/iw`），所以旋转是真旋转。

**Trade-off**：透视之前的空间意味着做了 keystone 校正后 radial 在画面上是圆锥曲线而不是椭圆；换成透视之后的空间又会让改透视时蒙版离开内容。选前者，与 guides 一致，手柄绘制用 `imagePxToOutFrame` 采样多边形就是精确的。

---

## 2. 权重计算

### 2.1 Luminance range：逐像素，不用模糊 mask

**Why**：现有 `u_mask_lum` 是 ≤256px 长边、σ=8px 的高斯（`passes.ts:820`–`868`，`pipeline-renderer.ts:418`），Highlights/Shadows 用它是为了"区域整体移动、局部对比保留"（`passes.ts:634`–`640`）。选区需要的是相反的性质——天空边上的树枝不能被算进天空。σ≈3% 画幅的模糊会让 -1 EV 的天空在树线上拖出一圈暗边。但纯单像素又会在阴影里按噪声抖（HSL 选区的同一问题，`passes.ts:695`–`702`）。

**How**：选区亮度取自 HSL 已有的 4-tap 双线性邻域均值（`passes.ts:716`–`722`，抽成函数两处共用），经全局 WB 后取 `ppLuma`，加全局 exposure（忽略 shoulder，与 `u_maskShift` 同一近似，`pipeline-renderer.ts:2216`–`2220`），再走 `srgbEncode(clamp(Y,0,1))`——即 `toneRegions` 给区域权重用的同一条感知轴（`tonal-model.ts:108`–`110`）。lo/hi/feather 都在这条 0..1 轴上，滑块 0..100 直接映射。

**Trade-off**：不含 Highlights/Shadows 的反馈（它们和局部参数在同一个 block 里）；LR 也是这样。"Smoothness"（更宽邻域）留到 Phase 2 的 guided filter，不用模糊 mask 凑。

### 2.2 Color range

复用 `hueWindow`（`hsl-bands.ts:141`）套一层 smoothstep 去掉三角形的折点，色度门复用 `hslChromaRatio` + `HSL_SEL_S0/S1`（`hsl-bands.ts:51`–`53`, `137`），选区颜色同样来自 4-tap 邻域的 Oklab（`passes.ts:722`）。中心/宽度以 Oklab 弧度存，preset 直接取 `HSL_CENTERS`（`hsl-bands.ts:14`）和 `SKIN_HUE/SKIN_HUE_HALF`（`hsl-bands.ts:93`–`94`）。

### 2.3 Linear / radial 与坐标

`v_texCoord`（`passes.ts:545`）是经 `u_texXform` 变换后的 source UV（含透视逆变换和 FLIP_Y，`crop.ts:283`–`296`），只差 orientation/flip 这一步就是 image-norm——`sourceNormToImageNorm`（`crop.ts:180`）是仿射，CPU 按当前 `crop` 算成一个 mat3 传 `u_imgFromTex`，shader 每像素一次 mat-vec。lens 畸变校正后的位置才是"画面上的位置"，所以用 `v_texCoord` 而不是 `lensUV`（`passes.ts:559`–`566`）。

```glsl
// MASK_GLSL（masks.ts 生成）。base 指向 component 的 3 行：h=(type, op, invert, ·), a, b
float maskComponent(int base, vec2 pImg, float pLum, vec3 labSel) {
  vec4 h = m[base], a = m[base + 1], b = m[base + 2];
  int type = int(h.x); float w;
  if (type == 0) w = smoothstep(a.x - a.z, a.x, pLum) * (1.0 - smoothstep(a.y, a.y + a.w, pLum));
  else if (type == 1) w = smoothstep(0.0, 1.0, hueWindow(atan(labSel.z, labSel.y), a.x, a.y))
                        * smoothstep(a.z, a.w, hslChromaRatio(length(labSel.yz), labSel.x));
  else {
    vec2 q = (pImg - a.xy) * vec2(1.0, u_imgAspect);                     // 等比单位（以 iw 为 1）
    if (type == 2) { vec2 d = (a.zw - a.xy) * vec2(1.0, u_imgAspect);
                     w = 1.0 - smoothstep(0.0, 1.0, dot(q, d) / dot(d, d)); }
    else { float c = cos(b.y), s = sin(b.y);
           q = vec2(c * q.x + s * q.y, -s * q.x + c * q.y) / (a.zw * vec2(1.0, u_imgAspect));
           w = 1.0 - smoothstep(1.0 - b.x, 1.0, length(q)); }
  }
  return h.z > 0.5 ? 1.0 - w : w;
}
```

### 2.4 组合

第一个 component 直接取 w；之后 add：`1 − (1−w)(1−wc)`，subtract：`w(1−wc)`，intersect：`w·wc`；组级 invert 最后 `1−w`。全是乘法，同一 op 内与顺序无关。组与组之间**参数相加**（两组各 +1 EV 重叠处 +2 EV，与 LR 一致）。

---

## 3. 局部调整的融合

### 3.1 位置与方式

**Why**：highlight-saturation 审计已确认 scene-referred 半段保色度、view transform 之后才失色（`highlight-saturation.md` §1.2）。这 10 个滑块的全局版本都是"标量参数进逐像素公式"（exposure `passes.ts:617`–`620`、highlights/shadows `648`–`649`、clarity `657`、dehaze `664`–`669`、vibrance/saturation `672`–`687`），把参数换成逐像素值，公式一次跑完——比 `mix(f(p), f(p+Δ), w)` 少 8 倍求值，且 `toneRegions` 本来就以逐像素 `amt` 工作（`tonal-model.ts:110`）。

**How**：在 `c = max(u_wbMatrix * c, 0.0)`（`passes.ts:601`）处改为：

```glsl
vec3 cPre = c;
vec3 cSel = wbNeighbourhood(lensUV, lensGain);               // 抽出的 4-tap（passes.ts:716-722）
float pLum = srgbEncode(clamp(ppLuma(cSel) * exp2(u_exposure), 0.0, 1.0));
vec3 labSel = proPhotoToOklab(cSel);
vec2 pImg = (u_imgFromTex * vec3(v_texCoord, 1.0)).xy;
float dExpo = 0.0, dHi = 0.0, dSh = 0.0, dClar = 0.0, dHaze = 0.0, dSat = 0.0, dVib = 0.0, dHue = 0.0, wPrev = 0.0; mat3 dWb = mat3(0.0);
for (int g = 0; g < u_maskGroups; g++) {                      // u_maskGroups 是 uniform：分支一致，0 时整段不跑
  int base = g * GROUP_STRIDE; vec4 hdr = m[base]; float w = 0.0;
  for (int k = 0; k < int(hdr.x); k++) {
    int cb = base + 6 + k * 3; float wc = maskComponent(cb, pImg, pLum, labSel); float op = m[cb].y;
    w = k == 0 ? wc : op == 0.0 ? 1.0 - (1.0 - w) * (1.0 - wc) : op == 1.0 ? w * (1.0 - wc) : w * wc;
  }
  if (hdr.y > 0.5) w = 1.0 - w;
  if (g == u_maskPreview) wPrev = w;
  vec4 A = m[base + 1], B = m[base + 2];
  dExpo += w * A.x; dHi += w * A.y; dSh += w * A.z; dClar += w * A.w;
  dHaze += w * B.x; dSat += w * B.y; dVib += w * B.z; dHue += w * B.w;
  dWb += w * mat3(m[base + 3].xyz, m[base + 4].xyz, m[base + 5].xyz);   // CPU 已按列主序打包
}
c = max((u_wbMatrix + dWb) * cPre, 0.0);
```

之后各 block 用 `u_exposure + dExpo`、`u_highlights + dHi`、`u_shadows + dSh`、`u_clarity + dClar`、`u_dehaze + dHaze`、`max(u_saturation + dSat, 0.0)`、`max(u_vibrance + dVib, 0.0)`；hue 在 vibrance/saturation 的 Oklab 往返里旋转 `lab.yz` 角 `dHue`（-100..100 → ±0.5 rad，与 HSL 的 `hAdj * 0.5` 同刻度，`passes.ts:745`）。现有的跳过门（`passes.ts:610`, `664`, `672`）各 OR 一位 `u_maskUse` 位域（CPU 在打包时按"哪些参数被任何组触碰"算出），门的精确性不变。

| 滑块 | 全局实现 | 局部 Δ 单位 | 融合 |
|---|---|---|---|
| exposure | `l + expoShoulder(l+E) − expoShoulder(l)` `passes.ts:618` | EV | 参数相加，shoulder 按逐像素 E |
| highlights / shadows | `toneRegions(Y, hi, sh, …)` `passes.ts:648` | /100 | 参数相加；`u_tonalActive` 计入 mask |
| clarity | `clarityShift(pixLx, maskLx, k)` `passes.ts:657` | /100 | 参数相加；仍需 `u_hasMask` |
| dehaze | `passes.ts:664–669` | /100 | 参数相加 |
| saturation / vibrance | Oklab 缩放 `u_saturation·(1+(u_vibrance−1)·w…)` `passes.ts:684` | /100 | 加到 scale 上，下限 0 |
| hue | （无全局）| ±0.5 rad | 同 HSL 的 `lab.yz` 旋转 `passes.ts:747` |
| temperature / tint | `computeWbMatrix` 亮度归一 Bradford `color-spaces.ts:233–243` | ±100 ↦ ±4000 K；tint 1:1 | CPU：`ΔM_g = M(T+ΔT, tint+Δt) − M0`，shader 混矩阵 |

**Trade-off**：局部 WB 的 ΔM 以全局 temp/tint 为基点，全局一动所有组的 ΔM 重算（8 次 `computeWbMatrix`，微秒级，无妨）。temperature 用 Kelvin 线性映射而非 mired，与全局滑块一致，代价是暖端比冷端"走得慢"。

### 3.2 Preview overlay

`u_maskPreview` 是**打包后**的组下标（-1 关）；打包器在 preview 开着时不跳过被选中的组（贡献仍为 0）。末段 `disp = mix(disp, vec3(1.0, 0.15, 0.1), 0.6 * wPrev)` 放在 `gamutMap` 后、`srgbEncode` 前（`passes.ts:816`–`817`）。它是 `EditParams.maskPreview`，`buildPipelineParams(s)` 只在 `!s` 且 UI ref 打开时给非 -1（`App.vue:1120`），export（`App.vue:2020`）和 `capturePreview`（`App.vue:1825` 走 live 但需显式传 `maskPreview: -1`——这是与 hsatPreview 不同的一处：`view_image` 用的也是 `buildPipelineParams()` 无参形式，要加一个 `{ preview: false }` 选项）。

### 3.3 为什么 Contrast / Blacks / Tone curve 不在 Phase 1

它们不是参数进公式，而是 CPU 把 Basic 值烘进 `u_curve_lut`（`App.vue:2021`, `curve.ts buildToneCurveLUT`），shader 在 view transform 之后以 RGBTone 方式查表（`passes.ts:770`–`785`）。局部版本只能混**结果**：`c + Σ w_g (lut_g(c) − lut_0(c))`，每组 5 次 fetch，且 display-referred 混合与 scene-referred 的 w 采样处相隔一个 view transform，需要把 w 带过去。Phase 1.5 路径：curve LUT 纹理由 `LUT_SIZE×1` 扩成 `LUT_SIZE×(1+8)`，每组一行，`bakeCurveLUT` 多烘 8 行（子毫秒），shader 按 `u_maskUse` 的 curve 位进第二个循环。数据模型现在就给 `MaskAdjust` 留 `contrast?: number; blacks?: number`，不实现。

---

## 4. Shader 预算与性能

**Why**：`PROCESS_SHADER` 现有非 sampler uniform 按 GLSL ES 打包规则（标量数组每元素一行）占 **109 行**（`u_hsl_*` 24、`u_lens*` 32、`u_wbMatrix` 3……）；13 个 sampler。WebGL2 保底 `MAX_FRAGMENT_UNIFORM_VECTORS = 224`。8 组 × 18 行 = 144 行 → 253，超。

**How**：`layout(std140) uniform Masks { vec4 m[144]; }`，renderer 构造时 `createBuffer` + `bindBufferBase(UNIFORM_BUFFER, 0)` + `uniformBlockBinding`，`setUniforms` 每帧 `bufferSubData` 一个 2304 B 的 `Float32Array`（由 `packMasks()` 产出，缓存在 `EditParams.masks`）。export 的离屏 renderer 是新实例（`useExport.ts:79`），UBO 随构造器建立而非 `uploadImage`，自然带上。备选是 RGBA32F 数据纹理 + `texelFetch`（有 `u_dro_grid` 先例，`passes.ts:535`），成本相同，多占一个纹理单元（13→14，保底 16）；选 UBO。`passes.spec.ts` 的 registry 正则只看 `uniform … ;`（`passes.spec.ts:26`），block 成员不在其中，新增的 `u_maskGroups / u_maskPreview / u_maskUse / u_imgFromTex / u_imgAspect` 要进 `PASSES[0].uniforms`（`passes.ts:1507`）。

**性能**：共享部分（4-tap、log/srgbEncode、Oklab 往返 + atan）≈ 100 ALU；每 component 10–25 ALU；每组累加 + 3×3 ≈ 25。8 组各 2 个 component ≈ 500 ALU/像素，与 HSL block（~300）或 Sony 3-D LUT（~300+）同量级，预览 ≤2 MP 片元时 <2 ms 集显。零组时 `u_maskGroups == 0`，for 不进、`dWb` 为零矩阵——但 `(u_wbMatrix + dWb) * cPre` 仍多一次矩阵加法：把这行也放进 `if (u_maskGroups > 0)` 分支，否则保留原 `u_wbMatrix * c`，默认路径逐 bit 相同。"全零组跳过"在 CPU 端做（`packMasks` 过滤），shader 不需要逐组分支。

---

## 5. 接线清单

**Why**：masks 不是 recipe key，`describeStep`/`isEdited`/assistant 的按 key 遍历（`App.vue:725`, `538`, `1844`）都不会自动覆盖，必须手写；但 snapshot 整包存、XMP 整包写的部分免费。

| 层 | 改动 | 位置 |
|---|---|---|
| 类型/打包 | `rendering/masks.ts`：类型、`defaultAdjust()`、`packMasks(groups, crop, dims, preview)` → `{ data, count, use, previewIndex }`、`imgFromTex(crop)`、TS 权重镜像、`MASK_GLSL`、`MASK_PRESETS` | 新文件 |
| Shader | §2/§3 块；UBO 声明；5 个新 uniform 进 registry | `passes.ts:601`, `816`, `1507` |
| EditParams | `masks: Float32Array; maskGroups: number; maskUse: number; maskPreview: number; imgFromTex: Float32Array; imgAspect: number`；DEFAULT 空/0/0/-1/identity/1 | `pipeline-renderer.ts:25`, `420` |
| setUniforms | `bufferSubData` + 5 个 uniform；`tonalActive` 计入 `maskUse` | `pipeline-renderer.ts:2124`, `2210` |
| 状态 | `const masks = reactive<MaskGroup[]>([])`；`maskPreview = ref(false)`；`selectedMask = ref<string|null>` | `App.vue:140` 附近 |
| Snapshot | `masks?: MaskGroup[]`；capture `structuredClone(masks)`；`setEditState` `masks.splice(0, masks.length, ...(s.masks ?? []))`；`defaultSnapshot` `masks: []` | `App.vue:583`, `648`, `670`, `615` |
| History/persist | `masks` 加进 deep watch 数组 | `App.vue:1613` |
| describeStep | `if (!same(prev.masks ?? [], next.masks ?? [])) parts.push(t("panel.masks"))` | `App.vue:723` |
| tabEdited | `masks: masks.length > 0` | `App.vue:904` |
| resetRecipe | `masks.splice(0)` | `App.vue:1780` |
| buildPipelineParams | `...packMasks(s?.masks ?? masks, cropOf(s), [iw, ih], !s && maskPreview.value ? selectedMask : null)` | `App.vue:1120` |
| baselineParams | 不带 masks → DEFAULT 空，hold-to-compare 自动无蒙版 | `App.vue:1321` |
| Export | `buildExportPlan` 用 `buildPipelineParams(settings)`，`useExport` 走同一 `draw(plan.params)` → 自动跟随；`s` 非空保证 preview 关 | `App.vue:2020`, `useExport.ts:92` |
| XMP | `llr:Settings` 是 `json.dumps(settings)` 整包（`cli.py:1141`），`masks` 免费落盘；结构化字段可选加 `llr:MaskCount`。`test_xmp.py` 的 `default_settings()` 不带 masks 即"旧快照"，合法；给 `edited_settings()` 加一组并断言 blob 回读相等 | `cli.py:948`, `test_xmp.py:25`, `60` |
| Tabs | `EditTab` 加 `"masks"`；`EDIT_TABS` 在 curve 后插一项；panel `v-if="editTab === 'masks' && activeSource"` | `App.vue:51`, `869`, `2665` 附近 |
| i18n | `tab.masks`、`panel.masks`、`mask.add/remove/enable/invert/overlay/newGroup`、`mask.type.{luminance,color,linear,radial}`、`mask.op.{add,subtract,intersect}`、`mask.range.{lo,hi,feather}`、`mask.color.{hue,width}`、`mask.preset.{highlights,shadows,midtones,sky,skin,vignette}`、`slider.hue`；其余局部滑块复用 `slider.<key>` | `i18n.ts:13`（en 定义 `MessageKey`，`:269`）、zh 段 |
| Tests | `masks.spec.ts`：pack/unpack 往返；权重 ∈[0,1]、单调、feather 连续；全零组被过滤、`use` 位正确；`imgFromTex` 对 4 个 orientation × flip 是 `imageNormToTexcoord` 的逆 | `rendering/__tests__/` |

---

## 6. UI

**Why**：rail 一次只显示一个 tab（`App.vue:48`–`50`），蒙版内容多，独占一个 tab 最自然；overlay 只在这个 tab 且组被选中时出现。

**How**：
- 面板结构复用 HSL / Grading panel 的模板（`App.vue:2626`–`2663`）：`panel-head` + 列表行（enable `switch` 样式 `App.vue:2594`–`2597`、名称、删除）+「添加」下拉（4 种类型 + 6 个 preset，与 curve 的 preset 按钮同款 `App.vue:2706`）。
- 选中组：每个 component 一行（type 图标、op 三选、invert），展开后是它的参数 `SliderRow`；然后 overlay `switch`；然后 10 个局部 `SliderRow`，reset-value 为 0。
- **手柄**：新增 `useMaskEditor` composable + 一个 `<svg class="mask-overlay">`，与 crop overlay 同 `:style="{ transform: displayTransform, width: imageW, height: imageH }"`（`App.vue:2228`），`viewBox` 取 `cropOutputRect(crop, iw, ih)`（正常视图的输出窗，`App.vue:1458`），`v-show="editTab === 'masks' && !cropMode && selectedGradient"`。屏幕 → 输出帧像素照抄 `overlayPoint`（`useCropEditor.ts:218`–`225`），→ image-norm 用 `outFrameToImagePx` 再 `/iw, /ih`（guides 手柄的同一条路，`useCropEditor.ts:292`–`296`）；反向绘制用 `imagePxToOutFrame`（`useCropEditor.ts:113`–`120`）。linear 画两端点 + 连线 + 垂直方向的 0/100 参考线；radial 画中心 + 4 个轴点，轮廓用 `imagePxToOutFrame` 采 32 点的 polygon（透视下也精确）。手柄尺寸乘 `ofPerScreen`（`useCropEditor.ts:172`–`175`）。拖动结束调 `flushPendingHistory` 的 `onDragEnd` 模式。

**Trade-off**：不做 LR 的"在画面上直接拖出新 gradient"，新建时给画幅中央的默认几何，用手柄调整——省一个拖拽模式，Phase 2 再加。

---

## 7. Presets

**Why**：assistant 与新手都需要"天空 / 肤色 / 暗角"一步到位；preset 就是一个 `MaskGroup` 模板，与 `CURVE_PRESETS`（`curve.ts:188`–`198`，键是标识符、标签走 `curvePreset.<key>`）同形。

```ts
export const MASK_PRESETS = {
  highlights: { components: [{ type: "luminance", lo: 65, hi: 100, featherLo: 20, featherHi: 0 }] },
  shadows:    { components: [{ type: "luminance", lo: 0, hi: 35, featherLo: 0, featherHi: 20 }] },
  midtones:   { components: [{ type: "luminance", lo: 30, hi: 70, featherLo: 20, featherHi: 20 }] },
  sky:        { components: [{ type: "color", hue: HSL_CENTERS[5], hueWidth: 0.9, chromaLo: HSL_SEL_S0, chromaHi: HSL_SEL_S1 },
                             { type: "luminance", op: "intersect", lo: 40, hi: 100, featherLo: 15, featherHi: 0 }] },
  skin:       { components: [{ type: "color", hue: SKIN_HUE, hueWidth: SKIN_HUE_HALF, chromaLo: SKIN_C0, chromaHi: SKIN_C1 }] },
  vignette:   { components: [{ type: "radial", cx: 0.5, cy: 0.5, rx: 0.55, ry: 0.55, angle: 0, feather: 0.6, invert: true }],
                adjust: { exposure: -0.7 } },
} satisfies Record<string, MaskPreset>;   // 省略处取 op:"add"、invert:false、adjust 全零
```

**Trade-off**：`sky` 只认蓝天；灰白天空（`a7v_donor.ARW`）要靠 `highlights` 或 linear——assistant 的 system prompt 里写明这一句。

---

## 8. Assistant

**Why**：模型的典型请求是"把天空压暗一点"，它需要的是 preset 名 + 少量 adjust，不是几何参数。

**How**：`SET_EDIT_SCHEMA`（`App.vue:1838`）加：

```ts
masks: { type: "array", description: "Replaces the whole mask list. Start from a preset, or give components.",
  items: { type: "object", properties: {
    preset: { type: "string", enum: Object.keys(MASK_PRESETS) },
    name: { type: "string" }, invert: { type: "boolean" },
    components: { type: "array", items: { type: "object", properties: { type: { enum: ["luminance","color","linear","radial"] },
      op: { enum: ["add","subtract","intersect"] }, invert: { type: "boolean" },
      lo: num(0,100), hi: num(0,100), feather: num(0,100), hue: num(-180,180,"degrees"), hueWidth: num(5,120),
      x0: num(0,1), y0: num(0,1), x1: num(0,1), y1: num(0,1), cx: num(0,1), cy: num(0,1), rx: num(0.02,1), ry: num(0.02,1), angle: num(-180,180) } } },
    adjust: { type: "object", properties: { exposure: num(-5,5), ...9 × num(-100,100) } } } } }
```

`applyAssistantEdit`（`App.vue:1880`）：有 `preset` 就 `structuredClone(MASK_PRESETS[preset])` 再覆盖 `components`/`adjust`；hue 以角度进出，内部转弧度；`numOr` 逐字段夹紧。`describeEdit`（`App.vue:1800`）原样返回 `masks`（hue 转回角度）。system prompt（`App.vue:1963`）加一句："masks: use presets (sky needs a blue sky; for grey skies use highlights or a linear gradient from the top); adjust values are local deltas added to the global sliders."

**Trade-off**：数组整体替换而不是按 id 修补，与 curve 通道的"整条替换"语义一致（`App.vue:1849`），schema 小；模型改一组要先 `get_edit`。

---

## 9. 实施计划

1. `rendering/masks.ts` + `masks.spec.ts`：类型、presets、`packMasks`、`imgFromTex`、TS 权重镜像、`MASK_GLSL`。纯逻辑，无 UI。
2. Shader + renderer：UBO、5 个 uniform、§3.1 块、门位、preview 混色；`passes.spec.ts` 绿；浏览器里空 masks 与改动前 export 逐 bit 对比。
3. `App.vue` 状态与管线：`masks`/Snapshot/capture/set/default/describeStep/tabEdited/reset/watch/`buildPipelineParams`；用一个硬编码 preset 验证 export 与预览一致。
4. 「蒙版」tab 面板：列表、component 参数、局部滑块、overlay 开关、preset 菜单；i18n en/zh。
5. `useMaskEditor` + SVG 手柄：linear 两点、radial 中心 + 轴点 + 旋转。
6. Assistant schema / describe / apply / prompt；`test_xmp.py` 加 masks 用例；`ARCHITECTURE.md` Render 段加一句。
7. §10 验证 + 调 preset 常数。

---

## 10. 验证

单元：§5 的 `masks.spec.ts`；`passes.spec.ts` registry；`test_xmp.py` 往返。像素按 `.claude/skills/verify` 驱动：

| 文件 | 内容 | 用它验什么 |
|---|---|---|
| `DSC01157.ARW`（默认样张，叶片水珠） | 绿叶 + 高光水珠 | **回归**：无 mask 时 export 与改动前逐 bit 相同；`highlights` preset + saturation +40 只动水珠；`color` 绿 + hue ±50 叶色变、背景虚化不受影响 |
| `a7v_donor.ARW`（灰天、建筑） | 大面积灰白天空 | "把天空压暗"主用例：`highlights` 或顶部 linear，exposure −1，树枝边缘无暗晕（对照 σ=8 模糊 mask 若用会有的晕）；改 crop / 拉直 / 90° 旋转后 gradient 跟着天空走 |
| `DSC01634.ARW`（紫色花、绿虚化） | 蓝紫主体 | `color` 蓝紫 + radial intersect：只有中间那朵动；overlay 红色与花轮廓吻合 |
| `DSC04568.ARW`（食物、暖光） | 橙色主体 | `skin` preset + temperature −40：橙色变冷、白盘子不变（色度门） |
| `fl_test.ARW`（ISO 1250 室内人像） | 噪点 + 暖光 + 皮肤 | `shadows` preset + exposure +1：暗部无颗粒状选区闪烁（4-tap 起效）；`vignette` preset；`skin` + exposure +0.3 |

验收：默认逐 bit 不变；preview 与 export 一致（同一 `packMasks` 输出）；8 组全开在 1:1 预览下拖滑块仍是实时（帧时间 < 16 ms，用 `[pipeline]` 日志的 GPU 型号记录）；undo/redo 与刷新后重载都还原 masks；旧 IndexedDB 会话（无 `masks`）加载为空且无报错；sRGB 与 P3 各跑一遍。

---

## 11. Phase 2

- **Brush**：R8 mask 纹理（source UV 空间，与 `u_mask_lum` 同构），component 类型 `"brush"` 引用纹理索引；export 需同一纹理按全分辨率重采样。
- **AI 主体 / 天空**：worker 跑分割模型，走 `render-linear` 元数据下发低分辨率 R8，浏览器当 brush 纹理用（与 `highlight-saturation.md` §6 的通道裁切提示同一条管道）。
- **Per-group LUT**：§3.3 的 `LUT_SIZE×(1+8)` 曲线纹理，解锁 contrast / blacks / 局部曲线。
- **Guided-filter 精修**：对 luminance/color 权重做一次以图像为引导的滤波，替代 LR 的 "Smoothness"；`slider-formula-gaps.md` 里掩码高斯→guided filter 的那个 pass 与此共用。
- 画面上直接拖出 gradient；组内 component 拖动排序；group 重命名的 XMP 结构化字段。
