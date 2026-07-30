# ZcTaskITP 与 ZcTaskSpica:它们各自在干什么

* 反编译产物:`tools/itp_decomp.c`、`tools/spica_decomp.c`
* 这两个在 `_limitations` 里被列为未复刻且从没查过用途。
  本篇只给**定性结论**,并把已经确认的骨架写下来。

---

# 一、ZcTaskSpica(RVA `0x358be0`,1961 字节)——**边缘自适应细锐化**

结论有把握,因为它的强度参数与 Sharpness **共用同两个设置字段**。

## 1.1 骨架

只动 **plane 0**,`uint16`,处理区是内缩 3 像素的矩形。分两遍:

```c
ctx  = *DAT_1405a8b18;                       /* 全局 Spica 参数单例 */
mode = 依 arw->vt[0xf8]() / vt[0xe8]() / vt[0x100]() / arw[0x144] 选 1/2/4/0x11
tbl  = FUN_140153c50(mode);
iso  = arw->vt[0xe0]();                      /* ISO */

/* ISO 三段线性插值:断点 tbl[0xc0] / tbl[0xc4] / tbl[0xc8],值 tbl[0xcc..0xd4] */
isoGain = piecewise_linear(iso);

/* ---- 第一遍:算细节修正,写进临时平面 tmp ---- */
for (y = y0+3; y < y1-3; ++y) for (x = x0+3; x < x1-3; ++x) {
    cls = this->vt[0x58](this, plane, &lo, &hi);      /* 纹理/边缘分类 -> 索引 */
    /* 由 cls 取两张表:ctx[0x28c8] 里每项 8 字节,ctx[0x28d0] 里每项 0x68 字节 */
    v   = this->vt[0x60](this, plane, x, y);          /* 方向滤波结果,9 位定点 */
    s   = plane[y][x];

    if (ctx[0x38] == 0) {
        tmp[y][x] = clamp((int)v >> 9, 0, 0x3fff);    /* 直通 */
    } else {
        d  = (v - (s << 9)) * isoGain;                /* 高频分量 */
        d2 = d * DAT_140466798;
        if (ctx[0x3c] != 0) {
            /* 三次 FUN_14035a1d0(软限幅曲线),取最小值 */
            lim = min(lim1, lim2, lim3) * ctx[0xc4] * DAT_140466790;
        } else lim = DAT_1404df248;
        tmp[y][x] = clamp(s - (int)(lim * DAT_1404667a4 * d * DAT_140466794), 0, 0x3fff);
    }
}

/* ---- 第二遍:按权重混回原图 ---- */
w = ((100 - st[0x2c4]) / 50.0f)              /* ← 与 Sharpness 互补 */
  * ((st[0x208] + 100) / 100.0f)             /* ← 与 Sharpness 同一个强度 S */
  * ctx[0x0c];

for (...) plane[y][x] = clamp(tmp[y][x] * w + plane[y][x] * (1 - w), 0, 32767);
```

## 1.2 最重要的一条:Spica 与 Sharpness 是同一把锐化的两端

```
ZcTaskSharpness :  权重 ∝  st[0x2c4] / 50
ZcTaskSpica     :  权重 ∝ (100 - st[0x2c4]) / 50
两者都再乘        (st[0x208] + 100) / 100
```

`st[0x208]` = 编辑参数 `0x801a`(锐化强度),`st[0x2c4]` = `0x901d`。
**`0x2c4` 是「细节 ↔ 轮廓」的分配器**:大 → 走 Sharpness(7×7 二项式,粗尺度),
小 → 走 Spica(方向滤波 + 纹理分类,细尺度)。

对 PIPELINE §7.9.5 那条「RMSE 12.5,模糊后掉到 6.8,说明一半是高频」的残差:
**只补 Sharpness 一端可能只解决一部分**,得看 `0x2c4` 实际取值。

## 1.3 没解出

* `this->vt[0x58]`(`FUN_140359390`,689 字节)—— 纹理/边缘分类器,输出一个索引。
* `this->vt[0x60]`(`FUN_1403599a0`,860 字节)—— 方向滤波,输出 9 位定点值。
* `FUN_14035a1d0` —— 软限幅曲线(被调三次,参数分别取 ctx 的
  `+0x48/0x4c/0x54/0x58`(正向)或 `+0x68/0x6c/0x74/0x78`(负向)、以及 `+0x94/0x98`)。
* `DAT_1405a8b18` 指向的参数单例的来源(`FUN_14035a600` 初始化,没读)。
* 常量 `DAT_140466790 / 794 / 798 / 1404667a4 / 1404df248` 没 dump。

---

# 二、ZcTaskITP(RVA `0x35af00`,2313 字节)——**多平面自适应滤波 + 由基底重建**

结论把握中等,骨架确定但核心滤波器没读。

## 2.1 骨架

```c
plane = task->planes[0];  w = plane[8]; h = plane[0xc];
calib = arw->vt[0xd8](arw, st[0x1e0]);
if (a[3] == 5) FUN_140172420(&P, calib);
else           FUN_1401724a0(&P, *(task+0x68), st, calib);   /* 组装噪声/增益参数 */

this->vt[0x60](this, task, &s78, &sA0);        /* 从 task 取两组标量 */
this->vt[0x58](this, &W, plane, rect);         /* W = 由 plane0 生成的浮点工作图 */

/* 六张 float 图,全是 (w,h),FUN_1401869f0 分配 */
buf1, bufD, buf50, bufF0, bufE8, bufE0

this->vt[0x110](this, bufF0, bufE8, W, w, h, rect, bufE0, ...);
this->vt[0x118](this, buf1, W,     w, h, bufF0, bufE0, rect, s78, sA0);
this->vt[0x190](this, bufD, buf50, W, w, h, bufE8, rect, 8.0f);

/* 新建 3 平面 uint16 图并写回 */
out.plane0[x] = clamp(bufD [x] + buf1[x], 0, 16383);
out.plane1[x] = clamp(buf1 [x],           0, 16383);
out.plane2[x] = clamp(buf50[x] + buf1[x], 0, 16383);
task->planes = out;                             /* FUN_14016b640 */
```

## 2.2 定性结论

* **`buf1` 是「基底 / 强度」平面**,直接成为输出 plane 1;
  plane 0 和 plane 2 是 **基底 + 各自的差分**(`bufD`、`buf50`)。
  这正是「把三通道拆成 `I` 与两条相对 `I` 的差」再重建的写法 ——
  和类名 **ITP(≈ ICtCp / IPT 的 I + 两个色差)** 一致。
* 由于下游 `ZcTaskSSCS` 把三个平面当 R/G/B 用(权重 32/64/32,plane1 权重加倍),
  **实际语义更像 `(R, G, B) → (G' + (R−G)', G', G' + (B−G)')`** ——
  即 **用处理过的绿/亮度基底重建 R 和 B,同时保住色差**。
  典型用途:亮度降噪后重建彩色、抑制伪色。
* 它排在 `RawNRSIMD` 之后、`SSCS` 之前,与上面这个用途吻合。
* `vt[0x58]` 的入口(`FUN_14035ad60`)把 uint16 平面按 **四个通道系数**
  (`param_5` 的 4 个 int16 × `param_7` × `DAT_140466990..99c`)转成 float —— 
  像是**噪声方差归一化**(把各通道按增益折算到同一噪声尺度)。
* `vt[0x110]` 的标量版 `FUN_140360910`(397 字节,最短、最可读)是
  **按权重图做二选一混合**:
  ```c
  m = max(w1[x], w2[x]);
  out[x] = m * A[x] + (1 - m) * B[x];
  ```
  这坐实了整段是「滤波版 vs 原版,按每像素置信度混合」的结构。

## 2.3 一条能对上的旁证

PIPELINE §7.4 说 SSCS「一个像素都没触发,因为整幅最大值只有 3043 而 lo=2380」。
ITP 就在 SSCS 上游,而且是唯一会**重建**平面数值的一步
(`out = base + delta`,三次 clamp 到 16383)。
**SSCS 看到的那个 3043 的量程是 ITP 定的**,§7.4 里「量程怎么来的还没解出」
这条应该从 ITP 这里继续查。

## 2.5 `vt[0x190]`(色差那一路)的四个基元核 —— 已解

`vt[0x190]` = `0x140363500` **不是滤波器,是编排器**。它一上来分配四个 float 缓冲
(`0x14035474b` 建对象、`0x1401869f0` 定尺寸 = arg5 × arg6 = w × h),
用**同一个源** `W`(arg4)填满,然后继续分配更多缓冲往下算。

四次派发都长一个样,只有槽号和目的缓冲不同:

```c
functor = { vptr = 0x140466678, x0, y0, x1, y1 };   /* 从 arg8 的 rect 抄来 */
this->vt[0x150](this, buf_a, W, &functor);
this->vt[0x158](this, buf_b, W, &functor);
this->vt[0x160](this, buf_c, W, &functor);
this->vt[0x168](this, buf_d, W, &functor);
```

> 认出这四个槽的关键是**栈帧**:8 次 push 之后 `lea rbp,[rsp-7]`,所以 `rbp+0x4f`
> 正好落在 rcx 的 home slot —— 那些 `[rbp+0x4f]` 不是别的对象,**就是 `this`**,
> 于是 `+0x150..+0x168` 直接在 `ZcTaskITP::vftable`(`0x1404667b8`)上查得到。
> 一开始误以为是另一个对象的表,白绕了一圈。functor 那张表(`0x140466678`)
> 指向 `0x14012d1b0` 一族十几字节的小 thunk,是框架的区域迭代器,**不含算术**。

四个核都是平铺循环,几百字节,一次读完:

| 槽 | 函数 | 大小 | 算式 |
|---|---|---|---|
| `+0x150` | `0x14035e750` | 272 | `out[y][x] = (in[y][x−2] + 6·in[y][x] + in[y][x+2]) / 8` |
| `+0x158` | `0x14035e860` | 496 | `out[y][x] = (in[y−2][x] + 6·in[y][x] + in[y+2][x]) / 8` |
| `+0x160` | `0x14035ea50` | 288 | `out[y][x−1] = (in[y][x−2] + in[y][x]) / 2` |
| `+0x168` | `0x14035eb70` | 288 | `out[y−1][x] = (in[y−2][x] + in[y][x]) / 2` |

常量都在 `.rdata`,一处不含糊:`6.0` @ `0x1404df118`、`0.125` @ `0x1404deadc`
(前两个核共用)、`0.5` @ `0x1404deb28`(后两个核共用)。
前两个核把首尾各两列/两行**原样拷贝**,不做滤波。

**四个核的抽头间距全是 2,即只取同色邻居。** 这是本节最重要的一条:

* 它独立坐实了 **ITP 的输入是马赛克**,与 `static-rawnr.md` §5.2 量到的
  「RawNRSIMD 跑在马赛克上、逐相位」对得上,也解释了执行链里 1 平面 → 3 平面
  为什么发生在这里(见 `measured-chroma-gap.md` §3)。
* 语义清楚了:前两个是**同色低通**(水平/垂直各一),后两个是把缺失位点
  **按同色两邻居取中点填上**(水平/垂直各一)。四个缓冲 = 水平假设与垂直假设
  各自的低通与插值 —— 这是**方向性 demosaic** 的前端。
* 下游 `vt[0x110]` 的标量版 `FUN_140360910` 已知是
  `m = max(w1, w2); out = m*A + (1−m)*B`(§2.2),正是**按每像素置信度在水平与
  垂直两个假设之间混合**。整条路子由此闭合。

这也解释了 `measured-chroma-gap.md` 量到的现象:色差之所以能干净到 8 位量化以下,
是因为重建**从同色低通开始**(`[1,0,6,0,1]/8`)再插值,而不是先插值再补救。

## 2.4 没解出

* `vt[0x110]` = `FUN_14035ecc0`(1272 字节)—— 置信度权重图怎么算
* `vt[0x118]` = `FUN_140362df0`(1793 字节)—— 产出 base 平面
* `vt[0x190]` = `FUN_140363500`(1490 字节)—— **编排骨架与四个基元核已解,见 §2.5**;
  没解的是四个缓冲之后那几级(它还会再分配至少四个缓冲)
* `FUN_14035d3e0`(2016 字节,meta 表里的 `+0x190`)
* `FUN_1401724a0` —— 参数组装
* **ZcTaskSIMDITP 的这五个槽和标量 ZcTaskITP 的 `vftable`(非 meta)完全同函数**
  (`0x35ad60 / 0x35b810 / 0x35ecc0 / 0x362df0 / 0x363500`),
  所以「标量孪生版更好读」这条对 ITP **不成立** —— 两边是同一份代码,
  真正的 SIMD 差异在 `vftable_meta_ptr` 那一列(`0x3b2300 / 0x3aaa40`)。
  这是个和 SSCS/AreaComp 不同的情形,别照搬那套方法。
