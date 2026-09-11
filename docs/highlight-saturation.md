# Highlight Saturation：设计文档

## 结论

在 `PROCESS_SHADER` 的 display-referred 半段——tone curve 之后、Color Grading 之前（`passes.ts:785`–`787` 之间）——加一个 Oklab 色度缩放块：`C_out = C_in * (1 + amount * w(L) * g(C, L))`，`w` 是 Oklab L 上的 smoothstep 高光权重，`g` 复用 HSL 的近中性门。两个 recipe slider（`highlightSat`、`highlightSatRange`），跟在 Color 组的 Saturation 后面；出界由现有 `gamutMap`（`passes.ts:476`）兜底，不新增 pass；amount=0 时按 uniform 分支整块跳过，是精确 no-op。

放在 display-referred 的原因：高光失色的三处主要来源都在 view transform 里或之前——per-channel 硬 clamp（`passes.ts:380`–`382`、`770`）、per-channel 凹曲线压缩通道比、Sony ChromaSuppres 主动衰减（`passes.ts:269`–`277`）。任何放在 view transform *之前*的色度增益都会被这些环节再次吃掉；放在之后，L 天然归一到 [0,1]，且 `gamutMap` 恰好在下游。

不能修的部分：sensor 通道饱和后由 LibRaw 在 white level 处按通道 clip（`fit_profile.py:135`，默认 `highlight_mode`），这类像素到浏览器时已是中性白，任何 shader 控件都只能"染色"而不能"恢复"。这是 worker 侧的未来工作（末节）。

---

## 1. 现状审计：高光在哪里失去色度

**Why**：先分清"数据在上游丢了"和"shader 自己扔掉了"，控件只能救后者。

### 1.1 上游丢失（RAW / DCP）

- LibRaw `postprocess(use_camera_wb=True, output_bps=16)` 的默认 `highlight_mode` 是 clip：WB 后三通道一起截到 1.0，sensor 上任一通道饱和的区域输出趟平成 (1,1,1)（`apps/worker/src/llr_worker/fit_profile.py:135`–`147`；Sony 路径 `sony/itp.py:233` 的 `demosaic_rawpy` 同样受此约束）。uint16/65535 的输出也不可能 >1。
- 因此 shader 注释里的"scene-linear headroom above 1.0"（`passes.ts:446`–`448`）来自 camera→ProPhoto 矩阵和 HueSatMap 的 value scale，**不是**未截断的 sensor 数据。
- 无 profile 的 fallback 路径同样没有 headroom，代码已自述（`cli.py:1481`–`1485`）。

### 1.2 Shader 内扔掉的（可救）

按 `main()` 顺序，scene-referred 半段是**保色度**的：

- Exposure/Highlights/Shadows/Clarity 是一个 log-luma 比例乘回 RGB（`passes.ts:660`），`toneRegions` 只作用于 Y（`tonal-model.ts:104`–`114`），hue/chroma 比不变。
- Dehaze 反而加饱和（`passes.ts:663`–`669`）。
- Vibrance/Saturation、HSL 都在 Oklab 里缩放 a/b（`passes.ts:671`–`687`、`694`–`755`），仅 `max(…, 0)` 一处非线性。
- `applyHsvTable` 在 [0,1] 上查表后按 `max(valueIn, 1.0)` 把 headroom 乘回去（`passes.ts:449`–`466`），不压白。

失色集中在 view transform 及其后：

| 位置 | 机制 | 性质 |
|---|---|---|
| `viewTransform` `passes.ts:379`–`383` | profile LUT 输入 `clamp(s.r, 0, 1)` 逐通道 | **硬 clip**。(1.5, 1.2, 0.2) → (1, 1, 0.2)，饱和度从 0.87 掉到 0.8，hue 也偏；这是灯丝、黄色招牌变白的主因 |
| 同上 | 凹的 per-channel 曲线 | **软去饱和**。肩部把通道间的比压扁，这是 ACR/相机的"胶片感"，有意为之 |
| `sonyChroma` `passes.ts:246`、`254`–`255`、`269`–`277`、`357` | encode 前 clamp；Cb/Cr ±0.5 clamp；ChromaSuppres 在 hiY 以上把色度线性衰减到 0；YCC 回 RGB 再 clamp | **引擎忠实复现**（`sony/chromasuppres.py` 顶部有推导），Sony 路径高光去饱和是设计而非 bug；3-D LUT "高级"更进一步（`i18n.ts:180`） |
| `passes.ts:770` | tone curve 前 `clamp(c, 0, 1)` | 对无 profile 曲线的 fallback（`passes.ts:389` 返回未截断值）这是唯一硬 clip 点 |
| `passes.ts:816` `gamutMap` | 向等亮度灰去饱和 | **保亮度、保 hue**，但它跑在上面几处 clamp 之后，几乎永远看不到 >1 的输入，只处理 ProPhoto→sRGB 的广色域溢出 |

**结论**：不存在"tone mapper 主动向白去饱和"的独立环节；去饱和是 per-channel clamp + per-channel 曲线的副产物，加上 Sony 路径的有意抑制。`gamutMap` 是现成的、可复用的 gamut 保护，位置正好在新块下游。

**Trade-off**：不动 `viewTransform` 的 clamp。改它（例如改成 luminance-preserving 的 clip）会改变相机 look 和 Sony bit-exact 对齐，超出本功能范围。

---

## 2. 控件设计

**Why**：用户要的是"只在高光提色度、不染色、白不发脏"。乘法色度缩放天然不染色（C=0 → 0），权重按 L 划范围，近中性门挡噪点。

### 2.1 插入点

`passes.ts:785` 之后（curve LUT 已应用，`c ∈ [0,1]` display-linear ProPhoto）、`787` Color Grading 之前。理由：

- L 有界：display-linear ProPhoto 白的 Oklab L = 1.000（`PROPHOTO_LINEAR_TO_LMS` 各行和 ≈1，`color-spaces.ts:30`–`34`），0.18 灰 L=0.565，0.5→0.79，0.8→0.93。阈值直接落在感知轴上。
- Whites/Contrast 已经把像素推到哪里，这里看到的就是哪里；Whites +100 推到 clip 的白 L=1、C=0，控件对它无效——正确。
- Color Grading 保持在最后，它读 `ppLuma(c)`（`passes.ts:793`），色度缩放不改亮度，不扰动它的 region 权重。
- Sony 路径 ChromaSuppres 在 `viewTransform` 内已执行完，这里可以把它衰掉的色度补回来，而不破坏引擎复现（默认 0 不动）。

### 2.2 空间与权重

Oklab（`proPhotoToOklab`/`oklabToProPhoto` 已有，`color-spaces.ts:289`–`300`），与 Vibrance/HSL 同一空间。

```glsl
// after the curve block, before grading
if (u_hsatAmount != 0.0) {
  vec3 lab = proPhotoToOklab(c);
  float C = length(lab.yz);
  float w = smoothstep(u_hsatLo, u_hsatLo + HSAT_SOFT, lab.x);
  float g = smoothstep(HSL_SEL_S0, HSL_SEL_S1, hslChromaRatio(C, lab.x));
  float wp = w * g;
  if (u_hsatPreview == 1) { outColor = vec4(vec3(srgbEncode(wp)), 1.0); return; }
  lab.yz *= 1.0 + u_hsatAmount * wp;
  c = max(oklabToProPhoto(lab), 0.0);
}
```

- `L` 归一：Oklab L 本身，不再 normalise。
- `u_hsatLo`：range slider 0..100 线性映射到 L₀ ∈ [0.55, 0.95]，默认 50 → 0.75；`HSAT_SOFT = 0.15` 常量，默认全强度点 L=0.90（display-linear ≈0.73），与 Color Grading 高光区 0.55→0.85 display-luma（`passes.ts:24`–`25`，Oklab L≈0.82→0.95）相邻略宽。
- 缩放形式与 Saturation slider 一致（`u_saturation = 1 + s/100`，`App.vue:1131`）：+100 色度 ×2，-100 高光全去色（"洗干净白"也是真实需求）。不用 `exp2`——它 -100 只到 ×0.5，且与相邻 slider 语义不一致。
- 近中性门 `g`：复用 `hslChromaRatio` 和 `HSL_SEL_S0/S1`（`hsl-bands.ts:51`–`53`、`137`）。高光处 L≈1，门等价于 C ∈ [0.012, 0.030] 的 smoothstep，恰好挡住亮部色度噪声被 ×2 放大成彩色颗粒。纯白 C=0 无论如何不动。
- Gamut：`oklabToProPhoto` 后 `max(…, 0)`（同 `passes.ts:686`），再由 `passes.ts:815`–`816` 的 `gamutMap` 向等亮度灰收回去。近白处 sRGB 边界几乎没有色度余量，控件自然失效——这正是"白不能被染色"的物理含义；P3 显示有更多余量，行为一致。
- 连续性：两个 smoothstep 相乘，无分段，无 banding。
- No-op：`u_hsatAmount == 0` 是 uniform 分支，跳过整个 Oklab 往返，逐 bit 等于当前输出。

**Trade-off**：

- 常量 softness 而非第三个 slider：两个 smoothstep 已给出连续过渡，先不暴露。要加时是一个 uniform 加一个 recipe key，路径与 range 相同。
- 不按 hue 分（不是 HSL 的替代品）；要"只提黄色高光"用它加 HSL Saturation 叠加即可。
- 每像素多一次 Oklab 往返（≈ Vibrance 的成本），仅在 slider 非零时。

### 2.3 Preview（"显示作用范围"）

现成机制：hold-to-compare 的 `showOriginal`（`App.vue:370`、`1407`–`1408`）只是切换 `draw()` 的参数（`App.vue:1404`），没有独立 overlay 通道；crop editor 的 overlay 是 SVG，与像素无关。最便宜的做法是上面 GLSL 里的 `u_hsatPreview`：命中时直接输出灰度 `wp`，其余全跳过。

- 作为 `EditParams.hsatPreview: 0|1`，**不进 Snapshot**（view-only，同 `displayGamut` 的地位，`App.vue:433`）；`buildPipelineParams(s)` 只在 `!s`（live）时带上，export 与 `capturePreview`（assistant 的 `view_image`，`App.vue:1824`）永远为 0。
- UI：Color 组内一个 `control-row` + `switch`（`App.vue:2592`–`2598` 的现成样式），或按住 Alt 拖 range slider 时临时置 1。前者一行模板，先做前者。

---

## 3. 管线接线

**Why**：这个 codebase 的 slider 大部分是"按 key 驱动"的，新 recipe key 会自动流过 UI、history、assistant、XMP；只列必须手写的点。

| 层 | 改动 | 位置 |
|---|---|---|
| Shader | 上面的块；`uniform float u_hsatAmount, u_hsatLo; uniform int u_hsatPreview;`；`HSAT_SOFT` 常量 | `passes.ts` main 785 后；uniform 声明区 |
| Uniform 注册 | 三个名字加进 `PASSES[0].uniforms`，否则 `setUniforms` 静默跳过（`passes.spec.ts:16`–`19` 会红） | `passes.ts:1506` |
| EditParams | `hsatAmount: number`（-1..1）、`hsatLo: number`、`hsatPreview: number`；DEFAULT 0 / 0.75 / 0 | `pipeline-renderer.ts:25`、`420` |
| setUniforms | `s("u_hsatAmount", …); s("u_hsatLo", …); i("u_hsatPreview", …)` | `pipeline-renderer.ts:2203` 旁 |
| RecipeKey | 加 `"highlightSat" | "highlightSatRange"` | `App.vue:38` |
| defaultRecipe | `highlightSat: 0, highlightSatRange: 50` | `App.vue:55` |
| groups | Color 组追加两条 spec（-100..100 step 1；0..100 step 1） | `App.vue:76`–`82` |
| buildPipelineParams | `hsatAmount: r.highlightSat / 100, hsatLo: 0.55 + 0.4 * r.highlightSatRange / 100, hsatPreview: s ? 0 : (hsatPreview.value ? 1 : 0)` | `App.vue:1120` |
| i18n | `slider.highlightSat` / `slider.highlightSatRange`（en + zh），`color.hsatPreview` | `i18n.ts:151`、`406` |
| Assistant prompt | "Slider semantics" 句加 `highlightSat`（-100..100，boosts chroma only in highlights without tinting）、`highlightSatRange` | `App.vue:1963` |

自动覆盖、无需改动：

- **Snapshot / 持久化**：`recipe: Recipe` 整体存（`App.vue:584`），`setEditState` 先铺 `defaultRecipe()` 再 assign（`App.vue:673`），旧 session 缺 key 自动落到默认，正是它注释里写的目的。`captureSnapshot`/`defaultSnapshot` 不改。
- **history 标签**：`describeStep` 遍历 recipe keys，用 `slider.<key>` 取名（`App.vue:725`–`727`）；`formatSliderValue` 默认 `+n` 格式即可。
- **编辑标记 / reset**：`isEdited`、`groupEdited`、`resetGroup` 按 key（`App.vue:538`–`542`），`SLIDER_DEFAULTS` 由 `defaultRecipe()` 派生（`App.vue:136`）。
- **Assistant**：`SET_EDIT_SCHEMA.sliders` 由 `SLIDER_SPECS` 生成（`App.vue:1844`），`describeEdit`/`applyAssistantEdit` 同样遍历（`App.vue:1804`、`1883`）。
- **Export**：`buildExportPlan` 用 `buildPipelineParams(settings)`（`App.vue:2020`），`useExport` 走同一个 `renderer.draw(plan.params)`（`useExport.ts:92`）→ 同一 `renderPass`。preview 标志由 `s ? 0` 保证关闭。
- **XMP**：`llr:Settings` JSON blob 整包写入 recipe（`cli.py:1141`）；结构化 `llr:*` 字段是手写列表（`cli.py:958`–`963`），要不要加 `llr:HighlightSat` 是可选的可读性改进。
- **hold-to-compare**：`baselineParams()` 不带这两个字段，回落到 DEFAULT 0（`App.vue:1321`–`1325`）。

**Trade-off**：range 用 0..100 而不是直接暴露 L₀，是为了和面板其它 slider 一致，代价是映射常数 `0.55 + 0.4·x` 只在 `buildPipelineParams` 一处。

---

## 4. 验证

**Why**：像素是 GPU 产物，纯函数测试只保证权重形状，视觉验收要按 `.claude/skills/verify` 驱动。

### 4.1 单元测试（`apps/web/src/rendering/__tests__/`）

按 `hsl-bands.ts` 的模式，把权重/缩放做成 TS mirror 并从中生成 GLSL 常量（新文件 `highlight-sat.ts`，约 30 行：`HSAT_SOFT`、`hsatRangeToL0(range)`、`hsatWeight(L, lo)`、`hsatScale(C, L, amount, lo)`）：

- `hsatScale(C, L, 0, lo) === 1` 对任意输入（no-op）。
- L ≤ lo 时 scale 为 1；L ≥ lo + SOFT 且 C 大时 scale = 1 + amount。
- C = 0 时输出 C 仍为 0；C ∈ [0, 0.012] 内 scale 为 1（近中性门）。
- 单调：固定 C、amount>0，scale 随 L 非减；沿 L 扫描无跳变（相邻步差 < 阈值）。
- `passes.spec.ts` 的 uniform 注册测试会自动覆盖三个新 uniform。

### 4.2 视觉验收（samples/）

| 文件 | 为什么 | 看什么 |
|---|---|---|
| `fl_test.ARW`（ISO 1250, 1/30, 01:40 拍摄） | 夜景，最接近用户场景 | 灯具/招牌 +60：颗粒亮部颜色变浓、hue 不变；纯白光源仍是白，不出现彩边或颜色噪点 |
| `a7v_donor.ARW`（1/8, f2.8, 黄昏） | 暖色高光 + 大面积中间调 | 中间调/阴影像素逐 bit 不变（截图 diff 应只在亮部有差） |
| `DSC01157.ARW`（默认样张，日光） | 回归 | amount=0 时与改动前 export 逐 bit 相同；-100 时高光去色、白不变灰 |
| 任一 Sony 文件切到 Standard look | ChromaSuppres 路径 | 默认 0 时 Sony 复现不变；+100 能补回被抑制的高光色 |

验收标准：高光颜色变强；白不发脏（颗粒/偏色）；阴影不动；光源仍"发光"（亮度不降——`gamutMap` 保亮度，色度缩放本身不改 L）。Preview 开关下灰度图应只在高光亮起，边缘无阶梯。P3 与 sRGB 显示各跑一次。

**Trade-off**：没有夜景 sample 带饱和 LED 招牌；如 `fl_test` 不够典型，需要补一张。

---

## 5. 泛化：Luma-vs-Sat 曲线

**Why**：Highlight Saturation 是 `scale(L)` 的一个两参数族；把它换成一条曲线就是 Capture One / darktable 的 "Lum vs Sat"。

**How**：`w(L)` 换成 1D LUT 查表 `texture(u_lumsat_lut, lutCoord(lab.x)).r`，其余（Oklab 往返、近中性门、`max`、`gamutMap`）不变。LUT 由 `curve.ts` 的 `curveToLUT`（`curve.ts:244`）从 `CurvePoint[]` 烘出，UI 复用 `useToneCurve` 的编辑画布。默认 slider 版本在曲线视图里就是一条 smoothstep 形状的点集。

**Trade-off**：多一张 LUT 纹理与一个 curve 编辑器状态，值得等到有第二个 L 相关的色度需求（例如阴影去色）再做；先用两 slider 版本验证核心是否成立。

---

## 6. 未来 worker 工作：RAW 高光重建与通道裁切提示

对 §1.1 那类像素，shader 无能为力。两件事都在 worker：一是 LibRaw `highlight_mode`（blend/rebuild，`fit_profile.py:135` 目前是默认 clip）或 RawTherapee 式的按邻域通道比重建，需要注意 DCP 拟合（`fit_profile.postprocess_camera_native` 同一入口，注释明说渲染与拟合必须一致）与 Sony ITP 路径（`sony/itp.py:233`，以 8192 为白点 bit-exact）都建立在 clip 语义上，改动要么只作用于非 Sony 路径，要么作为 profile 之后的独立步骤；二是通道裁切提示：在 decode 时统计 sensor 层各通道 ≥ white level 的 mask，随 `render-linear` 元数据下发一张低分辨率 R8 纹理，浏览器可用与 §2.3 相同的 preview 机制显示"这里的色彩信息在 RAW 里已经没有了"，让用户知道 Highlight Saturation 对这些区域不会有效果。
