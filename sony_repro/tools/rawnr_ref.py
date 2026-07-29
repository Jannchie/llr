r"""`ZcTaskRawNRHalf` 的逐位参考实现 —— 静态反汇编读出来的,**没有对过引擎**。

为什么对不了:`RawNRHalf`(`0x384cb0`)不在执行路径上,跑的是 `RawNRSIMD`
(`0x39fab0`),而后者是**另一套实现**(float32 AVX2,支撑集 ±5/±6),不是这个的
向量化孪生。所以这份代码是「Sony 这一族降噪的手法」,不是「Edit.exe 渲染出的像素」。
详见 PIPELINE.md 7.13。

每个平面走两步(`0x384cb0` 里三遍,平面 0/1/2):

    work = impulse_clamp(src)                      # 0x385af0
    dst  = sigma_filter(work, thr, offset, gain, limit)   # 0x3857e0

阈值表由 ARW 自带的噪声模型建(`0x78c5..0x78cb`),见 apps/worker 的 sony/rawnr.py。

用法::

    python rawnr_ref.py            # 跑自检
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "apps", "worker", "src"))

from llr_worker.sony.rawnr import ENGINE_FULL_SCALE, NoiseModel  # noqa: E402

LEVEL_MAX = 0x7FFF          # 阈值表的下标域
OUT_MAX = ENGINE_FULL_SCALE  # 输出钳位,也是引擎的满刻度

# 逐平面的参数,来自 calib(标签见 sony/rawnr.py 的注释)
#   OFFSET  plane0 = c[0x1018]-c[0x1020]   plane1 = c[0x1018]-c[0x1028]-c[0x101c]
#           plane2 = c[0x1018]-c[0x1024]
#   GAIN    c[0x1048] / c[0x104c] / c[0x1050]      细节增益,8.8 定点
#   LIMIT   c[0x1054] / c[0x1058] / c[0x105c]      细节限幅


def shifted(a, dy, dx, r=2):
    """把 a 平移 (dy, dx) —— 只在内部区域取值,所以不补边。r 是四周留出的边距。

    这一族工具都要它,所以放在这里一份,`rawnr_fit` / `rawnr_simd` 直接导入。
    """
    h, w = a.shape
    return a[r + dy:h - r + dy, r + dx:w - r + dx]


def impulse_clamp(src):
    """`0x385af0`:把中心像素钳进上/下/左/右**中间两位**之间。

    四个邻居排序后取 arr[1]..arr[2],即四邻域的内两分位。没有任何 sigma 余量 ——
    比 llr 的 `_clamp_impulses`(钳进 min-k*σ .. max+k*σ)激进得多,它清的是
    最细尺度上的孤立点,而不是噪声。
    """
    a = src.astype(np.int64)
    stack = np.stack([shifted(a, -1, 0), shifted(a, 1, 0),
                      shifted(a, 0, -1), shifted(a, 0, 1)])
    stack.sort(axis=0)
    out = a.copy()
    core = shifted(a, 0, 0)
    out[2:-2, 2:-2] = np.clip(core, stack[1], stack[2])
    return out


def _detail(a):
    """(细节, 低通)。

    引擎算的是 ``hp = 12c - 2*(上下左右) - (四对角)``,再 ``sar 4`` **向零取整**
    (`cdq; and edx,0xf; add; sar` 就是这个惯用法,不是向下取整)。于是
    ``lo = c - hp/16`` 恰好是 3x3 二项式核 ``[[1,2,1],[2,4,2],[1,2,1]]/16``。
    """
    c = shifted(a, 0, 0)
    n4 = (shifted(a, -1, 0) + shifted(a, 1, 0)
          + shifted(a, 0, -1) + shifted(a, 0, 1))
    diag = (shifted(a, -1, -1) + shifted(a, -1, 1)
            + shifted(a, 1, -1) + shifted(a, 1, 1))
    hp = 12 * c - 2 * n4 - diag
    d = np.sign(hp) * (np.abs(hp) >> 4)     # 向零取整,不能用 >> 4
    return d, c - d


def sigma_filter(src, thr_table, offset, gain, limit):
    """`0x3857e0`:按局部电平查阈值,只对通过阈值的邻居求均值,再补回细节。

    支撑集是 ``dy, dx in {-2,-1,1,2}`` 共 16 个点 —— **穿过中心的一整行和一整列都不取**
    (源码里 ``dx==0`` 被编译期消掉,``dy==0`` 留下一条运行期判断)。

    参考值 ``ref`` 用的是**低通**而不是中心像素,这是这套手法的关键:拿噪声像素自己
    当比较基准的话,阈值判断本身就被噪声带偏了。
    """
    a = src.astype(np.int64)
    d, lo = _detail(a)
    ref = np.clip(lo + offset, 0, LEVEL_MAX)
    thr = thr_table[ref]

    total = np.zeros_like(ref)
    count = np.zeros_like(ref)
    for dy in (-2, -1, 1, 2):
        for dx in (-2, -1, 1, 2):
            v = shifted(a, dy, dx)
            ok = np.abs(v - ref) < thr
            total += np.where(ok, v, 0)
            count += ok
    mean = (ref + total) // (count + 1)

    # imul 之后是 sar 8 —— 算术右移,负数向下取整,和 numpy 的 >> 一致
    boost = np.clip((d * gain) >> 8, -limit, limit)
    out = a.copy()
    out[2:-2, 2:-2] = np.clip(mean + boost - offset, 0, OUT_MAX)
    return out


def threshold_table(lo, hi, base, slope):
    """引擎那张 32768 项表。base/slope 已经把平面强度折进去了。

    公式**走出厂的 `llr_worker.sony.rawnr`**,不在这里另写一份 —— 理由同
    `tone_verify.py`:这个工具的价值就在于验的是出货代码,验本地副本等于没验。
    """
    return NoiseModel(lo=lo, hi=hi, base=base, slope=slope).threshold(
        np.arange(1 << 15, dtype=np.int64))


def denoise_plane(src, table, offset=0, gain=256, limit=1023):
    return sigma_filter(impulse_clamp(src), table, offset, gain, limit)


# ── 自检 ───────────────────────────────────────────────────────────────────
# 对不了引擎,那就检查这些性质:它们全是从反汇编独立读出的结论,任何一条不成立
# 都说明我读错了。


def _check_lowpass_is_binomial():
    """给一个单位冲激,低通必须精确等于 3x3 二项式核。"""
    a = np.zeros((9, 9), dtype=np.int64)
    a[4, 4] = 16 * 64                      # 乘 16*64 让整数除法不掉精度
    _, lo = _detail(a)
    want = np.array([[1, 2, 1], [2, 4, 2], [1, 2, 1]], dtype=np.int64) * 64
    got = lo[1:4, 1:4]
    assert np.array_equal(got, want), f"低通不是二项式核:\n{got}"
    return "低通 = [[1,2,1],[2,4,2],[1,2,1]]/16  ✓"


def _check_flat_field_is_identity():
    """常量场上算子必须什么都不做 —— 细节为 0,所有邻居都通过阈值。"""
    a = np.full((16, 16), 4000, dtype=np.int64)
    table = threshold_table(0, 2048, 3, 27)
    out = denoise_plane(a, table)
    core = out[2:-2, 2:-2]
    assert np.all(core == 4000), f"常量场被改动了:{np.unique(core)}"
    return "常量场恒等  ✓"


def _check_step_edge_is_not_blurred_across():
    """阶跃两侧只要离边 2 像素以上就必须逐位不变 —— 阈值判断的全部意义在此。

    紧贴边的那一列**会**动:低通跨过了边,细节 ``d`` 因此很大,而 ``d`` 被 LIMIT
    钳在 ±1023,补不回整个落差。这不是缺陷,是 LIMIT 的用途 —— GAIN>256 放大细节
    会在边上过冲,LIMIT 就是那道光晕限幅。所以这里查的是「不跨边混合」,
    而不是「逐位重建」。
    """
    a = np.zeros((32, 32), dtype=np.int64)
    a[:, 16:] = 8000
    table = threshold_table(0, 2048, 3, 27)
    full = denoise_plane(a, table, gain=452, limit=1023)
    out = full[2:-2, 2:-2]
    x = np.arange(2, 30)                       # 每一列对应的原始 x
    far_left, far_right = out[:, x <= 13], out[:, x >= 18]
    assert np.all(far_left == 0), "左侧被右侧污染"
    assert np.all(far_right == 8000), "右侧被左侧污染"
    # 过渡带单调,而且**窄** —— 落差还在是前两条断言的必然结果,不算证据;
    # 真正要查的是有多少列被拖进了中间地带。
    row = out[16]
    assert np.all(np.diff(row) >= 0), "过渡带不单调"
    blurred = int(np.count_nonzero((row > 0) & (row < 8000)))
    assert blurred <= 2, f"过渡带被拖宽到 {blurred} 列"
    return f"阶跃:离边 ≥2px 逐位不变,过渡带单调且只占 {blurred} 列  ✓"


def _check_noise_is_actually_reduced():
    """平坦区加白噪声,输出的标准差必须下降;阈值越大降得越多。"""
    rng = np.random.default_rng(0)
    # 同一份噪声喂两套阈值,不然比的是两份不同的随机数
    noisy = np.clip(4000 + rng.normal(0, 12, (256, 256)), 0, OUT_MAX).astype(np.int64)
    a = noisy[2:-2, 2:-2]
    rows, stds = [], []
    for name, (lo, hi, base, slope) in (
            ("ISO 100 ", (0, 2048, 3, 27)),
            ("ISO 1250", (0, 2560, 11, 68))):
        table = threshold_table(lo, hi, base, slope)
        b = denoise_plane(noisy, table)[2:-2, 2:-2]
        stds.append(b.std())
        rows.append(f"  {name}  阈值@4000 = {table[4000]:2d}   "
                    f"std {a.std():5.2f} -> {b.std():5.2f}   "
                    f"|改动| 中位 {np.median(np.abs(b - a)):.0f}")
        assert b.std() < a.std(), f"{name} 没有降噪"
    # 阈值越大放进来的邻居越多,平滑必须更强 —— 光「都降了」证明不了这一点
    assert stds[1] < stds[0], f"阈值加大反而降噪更弱:{stds}"
    return "噪声被削减,且阈值越大削得越多:\n" + "\n".join(rows)


def _check_gain_is_a_detail_boost():
    """GAIN 是细节增益:标签值 452 意味着细节被放大 1.77 倍,不是衰减。"""
    rng = np.random.default_rng(1)
    a = np.clip(4000 + rng.normal(0, 200, (128, 128)), 0, OUT_MAX).astype(np.int64)
    table = threshold_table(0, 2048, 3, 27)
    unit = denoise_plane(a, table, gain=256)
    boosted = denoise_plane(a, table, gain=452)
    d_unit = (unit[2:-2, 2:-2] - a[2:-2, 2:-2]).std()
    d_boost = (boosted[2:-2, 2:-2] - a[2:-2, 2:-2]).std()
    assert d_boost > d_unit, "GAIN 增大反而改动更小"
    return (f"GAIN 256 -> 452 使改动量从 {d_unit:.2f} 增到 {d_boost:.2f}"
            "(细节被放大,不是衰减)  ✓")


def main():
    for check in (_check_lowpass_is_binomial, _check_flat_field_is_identity,
                  _check_step_edge_is_not_blurred_across,
                  _check_noise_is_actually_reduced,
                  _check_gain_is_a_detail_boost):
        print(check())


if __name__ == "__main__":
    main()
