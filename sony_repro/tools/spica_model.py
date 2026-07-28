r"""Spica 的**完整**复现 —— 全部来自反汇编,不是拟合。

  ZcTaskSIMDSpica  0x39da60   逐像素主循环 0x39e0f0 .. 0x39e3da
  方向卷积         0x39ead0   四个方向各一段手工展开的 AVX
  增益             0x35a270   三条梯形曲线取 min
  梯形             0x35a1d0   (a,b,c,d,v_out,v_in)

链路:

    lo,hi  = 9 点十字的 min/max        rng = hi-lo      mid = (hi+lo)>>1
    rng < cfg[8] -> M = 0(直接查 LUT[0]),否则 9 位 LBP:
        bit b = (p[b] - lo >= rng>>1)
        bit 顺序 (y-2,x)(y-1,x)(y,x-2)(y,x-1)(y,x)(y,x+1)(y,x+2)(y+1,x)(y+2,x)
        bit8 置位则 M = ~M & 0xff                      <- 折叠
    idx,dx,dy = LUT[M]                                 <- 512 项,每项 8 字节
    S      = Σ w[k]·I[tap k 按 (dx,dy) 镜像]           (float32,权重和 512)
    detail = S - float(I0<<9)                          (xmm11 实测 = 1.0)
    G      = min(T_d(|detail/128|), T_r(rng), T_m(mid)) * cfg[0xc4] * (1/2048)
    tmp    = clamp(I0 - trunc(((G/512)·detail)·(-1/512)), 0, 16383)   -> **临时缓冲**
    out    = clamp(round(tmp·w + I0·(1-w)), 0, 32767)   <- 0x39e416 起的回写趟
             w = (100-st[0x2c4])/50 · (st[0x208]+100)/100 · cfg[0xc]

主循环写的是 0x35474b 新分配的临时缓冲,**不是 task 的平面**。平面上看到的是
那趟混合回写的结果。漏掉它会得到恰好 2 倍的修正量,而且逐位只有 75.6% ——
把 w 硬塞成 xmm11=0.5 能凑对幅度,但 `round(I0-w·trunc(y))` 和 `I0-trunc(w·y)`
不是一回事,差的那 24% 就在这里。

处理范围是平面**内缩 4**,再各留 3 的滤波边:x,y ∈ [4+3, 边长-4-3)。
task+0x30..0x3c 读出来是内缩 8,和实测对不上,别用它 —— 标量尾的位置能
双重印证内缩 4:rem = (x1-x0-6)&7 = 2 时尾巴落在 x1-5, x1-4。

镜像不是「翻转权重表」而是「翻转采样格点」:tap (ty,tx) 取 in[y+ty*dy, x+tx*dx]。
`dx=dy=1` 那一支的 lane 分组我逐条对过 —— 权重偏移 0x08/0x28/0x44 组恰好是
tap 1..8 / 9..16 / 16..23,其中 0x28 组最后一路像素被 vpblendw 清零(所以 tap 16
的权重被载入两次却只算一次)。

浮点求和顺序也照抄:三组 lane 先 C+(A+B),再两次蝶形横向归约,最后
`(H + (L + tap0)) + tap24`。产物最大 16383×1657 > 2^24,float32 会丢位,顺序有意义。

`--scan` 扫混合权重 w。注意 **w=0.5 处是个极尖锐的峰,但那只说明标度对**:
早先把它误认成 xmm11=0.5、省掉整趟混合,一样能得到这个峰,却卡在逐位 75.6%。

用法:  python spica_model.py [--w 0.5] [--scan] [--npz tiles_SIMDSpica.npz]
"""
import json
import os
import sys

import numpy as np

SCR = os.path.dirname(os.path.abspath(__file__))
WHITE = 16383

# 半径 3 的菱形,row-major。这个顺序就是权重表 tap 0..24 的顺序(已按反汇编核对)。
TAPS = [(dy, dx) for dy in range(-3, 4) for dx in range(-3, 4) if abs(dy) + abs(dx) <= 3]
assert len(TAPS) == 25

# 分类用的 9 点十字,顺序 = LBP 的 bit0..bit8
CROSS = [(-2, 0), (-1, 0), (0, -2), (0, -1), (0, 0), (0, 1), (0, 2), (1, 0), (2, 0)]

# 三组 AVX lane 各自对应的 tap 下标。None 表示该路像素被清零。
LANE_A = [1, 2, 3, 4, 5, 6, 7, 8]           # 权重 [rbx+0x08]
LANE_B = [9, 10, 11, 12, 13, 14, 15, None]  # 权重 [rbx+0x28]
LANE_C = [16, 17, 18, 19, 20, 21, 22, 23]   # 权重 [rbx+0x44]

F = np.float32


def sh(a, dy, dx):
    return np.roll(np.roll(a, -dy, 0), -dx, 1)


def trapezoid(x, a, b, c, d, v_out, v_in):
    """0x35a1d0。分支顺序照抄,别改成 np.select 的「先算全部再挑」——
    b > c 时(rng 和 mid 曲线就是)平台段根本进不去,写成区间条件会错。"""
    x = x.astype(F)
    out = np.full(x.shape, F(v_out), F)
    m2 = (a <= x) & (x < b)
    if m2.any():
        t = (F(b) - x[m2]) / F(b - a)
        out[m2] = F(v_in) * (F(1) - t) + F(v_out) * t
    m3 = (a <= x) & (x >= b) & (x < c)
    out[m3] = F(v_in)
    m4 = (a <= x) & (x >= b) & (x >= c) & (x < d)
    if m4.any():
        u = (F(d) - x[m4]) / F(d - c)
        out[m4] = F(v_out) * (F(1) - u) + F(v_in) * u
    return out


class Cfg:
    """spica_gaincfg.json 里那块引擎配置。"""

    def __init__(self, path=None):
        with open(path or os.path.join(SCR, "spica_gaincfg.json"), encoding="utf-8") as f:
            g = json.load(f)
        d = dict(g["dump"])
        self.rng_thr = int(d["8"] if "8" in d else d[8])
        self.gain = d.get("196", d.get(0xC4))
        self.curve = {n: [d.get(str(o + 4 * k), d.get(o + 4 * k)) for k in range(6)]
                      for n, o in (("dpos", 0x44), ("dneg", 0x64), ("rng", 0x84), ("mid", 0xA4))}
        self.lut = np.array(g["lut"], np.int32)[:, :3]   # (idx, dx, dy)


def load_wtab():
    W = np.zeros((100, 25), F)
    with open(os.path.join(SCR, "spica_wtab.json"), encoding="utf-8") as f:
        for k, v in json.load(f)["tables"].items():
            W[int(k)] = v
    return W


def convolve(a, W, idx, dxy):
    """float32,按 0x39ead0 的实际求和顺序。"""
    S = np.zeros(a.shape, F)
    for sy in (1, -1):
        for sx in (1, -1):
            sel = (dxy[0] == sx) & (dxy[1] == sy)
            if not sel.any():
                continue

            def lane(taps):
                v = np.zeros((8,) + a.shape, F)
                for j, t in enumerate(taps):
                    if t is None:
                        continue
                    ty, tx = TAPS[t]
                    v[j] = W[idx, t] * sh(a, ty * sy, tx * sx)
                return v

            vA, vB, vC = lane(LANE_A), lane(LANE_B), lane(LANE_C)
            v = vC + (vA + vB)                       # ymm4 + (ymm3 + ymm1)
            t = v + v[[2, 3, 0, 1, 6, 7, 4, 5]]      # vshufps 0x4e
            u = t + t[[1, 0, 3, 2, 5, 4, 7, 6]]      # vshufps 0xb1
            L, H = u[0], u[4]
            t0 = W[idx, 0] * sh(a, -3 * sy, 0)
            t24 = W[idx, 24] * sh(a, 3 * sy, 0)
            S[sel] = ((H + (L + t0)) + t24)[sel]
    return S


def run(a, cfg, W, xmm11=1.0, weight=0.5, rect=None):
    a = a.astype(np.int64)
    st = np.stack([sh(a, i, j) for i, j in CROSS])
    lo, hi = st.min(0), st.max(0)
    rng, mid = hi - lo, (hi + lo) >> 1

    M = np.zeros(a.shape, np.int64)
    for b in range(9):
        M |= ((st[b] - lo) >= (rng >> 1)).astype(np.int64) << b
    M = np.where(M & 0x100, ~M & 0xFF, M)
    M[rng < cfg.rng_thr] = 0                          # 引擎在这里直接查 LUT[0]

    ent = cfg.lut[M]
    idx, dx, dy = ent[..., 0], ent[..., 1], ent[..., 2]

    S = convolve(a.astype(F), W, idx, (dx, dy))
    I0 = a.astype(np.int32)
    detail = (S - (I0 << 9).astype(F)) * F(xmm11)

    xd = np.abs(detail * F(1.0 / 128))
    cv = cfg.curve
    Td = np.where(detail * F(1.0 / 128) > 0,
                  trapezoid(xd, *cv["dpos"]), trapezoid(xd, *cv["dneg"]))
    Tr = trapezoid(rng.astype(F), *cv["rng"])
    Tm = trapezoid(mid.astype(F), *cv["mid"])
    G = np.minimum(np.minimum(Td, Tr), Tm) * F(cfg.gain) * F(1.0 / 2048)

    delta = ((G * F(1.0 / 512)) * detail) * F(-1.0 / 512)
    # 主循环写的是**临时缓冲**(rbx,0x35474b 新分配的),不是 task 的平面
    tmp = np.clip(I0 - np.trunc(delta).astype(np.int32), 0, WHITE)

    # 0x39e416 起的回写趟:plane = clamp(round(tmp·w + plane·(1-w)))
    # 上限是 32767 不是 16383,取整是 vcvtps2dq —— 就近偶数。
    w = F(weight)
    val = np.minimum(np.maximum(tmp.astype(F) * w + I0.astype(F) * (F(1) - w),
                                F(0)), F(32767))
    out = np.rint(val).astype(np.int32)
    if rect is not None:
        # 每行末尾不足 8 个的余数走标量尾(0x39e5e1),那里是 cvttss2si —— **截断**。
        # rem = (x1 - x0 - 6) & 7,SIMD 到 x1-rem-3 为止,剩下的到 x1-3。
        x0, _y0, x1, _y1 = rect
        rem = (x1 - x0 - 6) & 7
        if rem:
            t_ = slice(x1 - rem - 3, x1 - 3)
            out[:, t_] = np.trunc(val[:, t_]).astype(np.int32)
    return out.astype(np.uint16), idx


def main():
    def opt(f, d):
        return float(sys.argv[sys.argv.index(f) + 1]) if f in sys.argv else d

    xmm11, weight = 1.0, opt("--w", 0.5)
    cfg, W = Cfg(), load_wtab()
    npz = sys.argv[sys.argv.index("--npz") + 1] if "--npz" in sys.argv else "tiles_SIMDSpica.npz"
    z = np.load(os.path.join(SCR, npz))
    tiles = sorted(int(k[1:].split("_")[0]) for k in z if k.endswith("_in"))

    cands = np.arange(0.40, 0.62, 0.005) if "--scan" in sys.argv else [weight]
    for c in cands:
        hit = tot = near = 0
        for t in tiles:
            a, b = z[f"t{t}_in"][..., 0], z[f"t{t}_out"][..., 0]
            h_, w_ = a.shape
            x0, y0, x1, y1 = 4, 4, w_ - 4, h_ - 4
            ys, xs = slice(y0 + 3, y1 - 3), slice(x0 + 3, x1 - 3)
            p, _ = run(a, cfg, W, xmm11, c, (x0, y0, x1, y1))
            e = p[ys, xs].astype(np.int32) - b[ys, xs].astype(np.int32)
            hit += int((e == 0).sum()); near += int((np.abs(e) <= 1).sum()); tot += e.size
        print(f"  w={c:.4f}   逐位 {100 * hit / tot:7.3f}%   |误差|<=1 {100 * near / tot:7.3f}%",
              flush=True)


if __name__ == "__main__":
    main()
