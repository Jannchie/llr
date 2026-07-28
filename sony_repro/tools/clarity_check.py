r"""把 Clarity 的**引擎整数版**和**shader 浮点版**摆在同一张图上对拍。

引擎那版是逐条照抄反汇编的定点运算(10bit 域、`>>` 取整、两处 `min` 钳位);
产品那版跑在归一化浮点上,还把 `>>6` / `<<6` 的 16bit 中间层整个省掉了。
两者应当在**取整噪声**的量级上一致 —— 差得更多就说明量纲搬错了,
而量纲错在视觉上往往不明显(强度差一倍照样"看着像 clarity"),只能这样量。

不需要 Edit.exe,不需要 frida,纯离线:
    python clarity_check.py [图片]
不给图就用合成图(正弦 + 硬边 + 噪声),那三样正好分别压 pass2 的高斯、
pass1 的保边阈值和 finish 的滚降。
"""
import sys

import numpy as np

# 全部来自 tools/clarity_calib.py 在 ILCE-7CM2 上的实测(见 PIPELINE.md 7.9.1)
AMP = (0, 32, 108, 184, 260, 336, 412, 488, 564, 646)
EDGE_THRESHOLD_16 = 96      # calib[0x1174],16bit 域
CENTER_MIX_256 = 0          # calib[0x1176]
ROLLOFF_MODE_LO = 2         # calib[0x1168]
ROLLOFF_MODE_HI = 2         # calib[0x1169]
DOWN = 8
GAUSS = np.array([[1, 2, 1], [2, 4, 2], [1, 2, 1]], np.int64)


def _pad(a, r):
    return np.pad(a, r, mode="edge")


def engine(y16, clarity):
    """逐条照抄 ZcTaskMarble 的定点实现。y16 是 16bit(= 10bit << 6)平面。"""
    clr = max(0, 10 * clarity)
    amp = AMP[clr // 10]
    h, w = y16.shape

    # FUN_140396810:8x8 box,`(sum + 32) >> 6`,结果为 0 时置 1
    sh, sw = -(-h // DOWN), -(-w // DOWN)
    padded = np.zeros((sh * DOWN, sw * DOWN), np.int64)
    padded[:h, :w] = y16
    small = (padded.reshape(sh, DOWN, sw, DOWN).sum((1, 3)) + 32) >> 6
    small[small == 0] = 1

    # FUN_140396a20:5x5 步长 2(跨度 ±4),丢掉离中心超过阈值的样本
    p = _pad(small, 4)
    total = np.zeros_like(small)
    count = np.zeros_like(small)
    for dy in range(-4, 5, 2):
        for dx in range(-4, 5, 2):
            s = p[4 + dy:4 + dy + sh, 4 + dx:4 + dx + sw]
            ok = np.abs(small - s) <= EDGE_THRESHOLD_16
            total += np.where(ok, s, 0)
            count += ok
    edge = total // np.maximum(count, 1)

    # FUN_140396cb0:3x3 高斯,再按 A/256 混回中心
    p = _pad(edge, 1)
    conv = np.zeros_like(edge)
    for dy in range(-1, 2):
        for dx in range(-1, 2):
            conv += GAUSS[dy + 1, dx + 1] * p[1 + dy:1 + dy + sh, 1 + dx:1 + dx + sw]
    blur = ((edge << 4) * CENTER_MIX_256 + (256 - CENTER_MIX_256) * conv + 0x800) >> 12

    # FUN_140396f40:8x 双线性上采样,权重 (2d+1)/16
    p = _pad(blur, 1)
    base = np.zeros((h, w), np.int64)
    d = np.arange(DOWN)
    wx = (2 * d + 1)[None, :]     # 右邻的权重,左邻拿 16 - wx
    wy = (2 * d + 1)[:, None]
    for gy in range(sh):
        for gx in range(sw):
            tl, tr = p[1 + gy, 1 + gx], p[1 + gy, 2 + gx]
            bl, br = p[2 + gy, 1 + gx], p[2 + gy, 2 + gx]
            top = tl * (16 - wx) + tr * wx
            bot = bl * (16 - wx) + br * wx
            # 两轴的权重各 16,合起来 256 —— 只除这一次
            blk = np.trunc((top * (16 - wy) + bot * wy) / 256.0 + 0.5)
            ys, xs = gy * DOWN, gx * DOWN
            sub = blk[:min(DOWN, h - ys), :min(DOWN, w - xs)]
            base[ys:ys + sub.shape[0], xs:xs + sub.shape[1]] = sub

    # FUN_140395b90:合成。滚降模式 2 -> min(4a, 4(1023-a), 512)
    a = y16 >> 6
    b = base >> 6
    diff = a - b
    hi = np.minimum(4 * a, 512) if ROLLOFF_MODE_HI == 2 else np.full_like(a, 512)
    lo = np.minimum(4 * (1023 - a), 512) if ROLLOFF_MODE_LO == 2 else np.full_like(a, 512)
    roll = np.minimum(hi, lo)
    num = roll * amp * diff + (a << 19)
    out = np.where(num > 0, ((num >> 18) + 1) >> 1, -(((-num >> 18) + 1) >> 1))
    return np.clip(out, 0, 1023)


def shader(y, clarity):
    """产品那版:归一化浮点,和 passes.ts 的 CLARITY_* 一一对应。"""
    amp = AMP[max(0, 10 * clarity) // 10] / 1024.0
    thr = EDGE_THRESHOLD_16 / 65472.0
    mix = CENTER_MIX_256 / 256.0
    h, w = y.shape
    sh, sw = -(-h // DOWN), -(-w // DOWN)

    padded = np.zeros((sh * DOWN, sw * DOWN))
    padded[:h, :w] = y
    small = padded.reshape(sh, DOWN, sw, DOWN).mean((1, 3))

    p = _pad(small, 4)
    total = np.zeros_like(small)
    count = np.zeros_like(small)
    for dy in range(-4, 5, 2):
        for dx in range(-4, 5, 2):
            s = p[4 + dy:4 + dy + sh, 4 + dx:4 + dx + sw]
            ok = np.abs(small - s) <= thr
            total += np.where(ok, s, 0.0)
            count += ok
    edge = total / np.maximum(count, 1)

    p = _pad(edge, 1)
    conv = np.zeros_like(edge)
    for dy in range(-1, 2):
        for dx in range(-1, 2):
            conv += GAUSS[dy + 1, dx + 1] * p[1 + dy:1 + dy + sh, 1 + dx:1 + dx + sw]
    blur = conv / 16.0 * (1 - mix) + edge * mix

    # LINEAR 采样 + 半个 base texel 的对齐修正,和 CLARITY_COMPOSE_SHADER 同式。
    # 引擎在块内第 d 个像素给右邻的权重是 (2d+1)/16;GL 的 texel 中心在整数 +0.5,
    # 于是 (x+0.5)/8 只走到 (2d+1)/16 而少了那半个 texel。
    yy = (np.arange(h) + 0.5) / DOWN + 0.5
    xx = (np.arange(w) + 0.5) / DOWN + 0.5
    base = _bilinear(blur, yy, xx)

    roll = np.clip(np.minimum(y, 1.0 - y) / 0.125, 0.0, 1.0)
    return np.clip(y + roll * amp * (y - base), 0.0, 1.0)


def _bilinear(img, ys, xs):
    h, w = img.shape
    y0 = np.clip(np.floor(ys - 0.5), 0, h - 1).astype(int)
    x0 = np.clip(np.floor(xs - 0.5), 0, w - 1).astype(int)
    y1, x1 = np.minimum(y0 + 1, h - 1), np.minimum(x0 + 1, w - 1)
    fy = np.clip(ys - 0.5 - y0, 0, 1)[:, None]
    fx = np.clip(xs - 0.5 - x0, 0, 1)[None, :]
    top = img[np.ix_(y0, x0)] * (1 - fx) + img[np.ix_(y0, x1)] * fx
    bot = img[np.ix_(y1, x0)] * (1 - fx) + img[np.ix_(y1, x1)] * fx
    return top * (1 - fy) + bot * fy


def synthetic(n=256, noise=0.0):
    """正弦(压高斯)+ 硬边(压保边阈值),再横跨整个亮度轴(压滚降)。

    噪声单独给,而且默认关掉 —— 保边阈值只有 1.5 个 10bit level,噪声一旦到同一
    量级,`|c-s| <= thr` 就会在整数版和浮点版之间**逐样本翻转**,量出来的差全是
    那个翻转,盖掉真正想查的量纲错误。要看它的影响就单独开一档。
    """
    yy, xx = np.mgrid[0:n, 0:n]
    img = 0.5 + 0.35 * np.sin(xx / 9.0) * np.cos(yy / 13.0)
    img[:, n // 2:] += 0.12                       # 硬边
    img *= np.linspace(0.05, 1.4, n)[:, None]     # 亮度轴,两端会被滚降压住
    if noise:
        img = img + np.random.default_rng(7).normal(0, noise, img.shape)
    return np.clip(img, 0, 1)


def compare(img, label):
    y16 = np.round(img * 1023).astype(np.int64) << 6
    print(f"{label}  ({img.shape[0]}x{img.shape[1]})")
    print(f"  {'档位':>4}  {'增益':>7}  {'最大差':>8}  {'RMS 差':>8}  {'引擎自身改动':>12}")
    for clarity in (1, 3, 5, 9):
        raw = engine(y16, clarity)
        delta = np.abs(raw / 1023.0 - shader(img, clarity))
        moved = np.abs(raw - (y16 >> 6)).max() / 1023.0
        print(f"  {clarity:>4}  {AMP[clarity] / 1024:>7.4f}  {delta.max():>8.5f}"
              f"  {np.sqrt((delta ** 2).mean()):>8.5f}  {moved:>12.4f}")
    print()


def main():
    if len(sys.argv) > 1:
        from PIL import Image
        img = np.asarray(Image.open(sys.argv[1]).convert("L"), np.float64) / 255.0
        compare(img, sys.argv[1])
    else:
        compare(synthetic(), "合成图,无噪")
        compare(synthetic(noise=0.004), "合成图,噪声 4/1023(压在保边阈值上)")
    print("无噪那档的差应当停在取整噪声的量级(~1/1023 = 0.001),那是判据;"
          "\n有噪那档只是说明保边判定有多敏感,不作数。"
          "\n最右列是引擎自己把像素挪动了多少 —— 差要远小于它,对拍才有意义。")


if __name__ == "__main__":
    main()
