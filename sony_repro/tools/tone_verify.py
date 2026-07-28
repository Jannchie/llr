r"""拿 Edit.exe 抓下来的表 / LUT,对 **worker 出厂的那份代码**。

模型(RVA 见 PIPELINE.md 6.2.3):

    gain_lo = 18 + contrast − shadows        作用在算子表索引 [0, 471)
    gain_hi = 18 + contrast + highlights     作用在算子表索引 [471, 1025)
    gain 钳到 [0, 35];k = floor(gain),f = gain − k
    T[i] = clamp(round(FAM[k][i] + (FAM[k+1][i] − FAM[k][i]) * f), 0, 65535)

    然后 tone LUT:
        b = 分段线性插值(i*128; X, Y) >> 2
        e = min(b >> 6, 1023);  f = (b − (e<<6)) / 64
        LUT[i] = trunc(T[e]*(1−f) + T[e+1]*f) >> 2

**算子表和曲线两段都从 `llr_worker.sony.tone` 里取**,不在这里另写一份。
这个工具存在的意义就是「出厂的代码 == 引擎」;要是它验的是本地副本,
改坏 `tone.py` 它一声不吭 —— 那就等于没验。曲线族则相反,从 **PE 里现读**
(`extract_tone_family.family()`),这样连带把「导出的 npz == 二进制」也一起验了。

两条路径,分别回答两个问题:

* `main()` —— 拿 `toneprobe/` 里抓的**算子表**和整张 LUT,在**引擎的整数域**上对,
  应当**逐位相同**。
* `--sweep` —— 拿 `looksweep/` 之类的实测 LUT,走 `base_curve` + `apply_tuning`
  这条**出厂路径**对。这里会有 ~1/16384 的差:`base_curve` 用 float64 插值,
  而引擎是 float32。这个差是已知的、被记在 `apply_tuning` 的 docstring 里。

用法(先跑 tone_probe.py 攒 toneprobe/)::

    python tone_verify.py
    python tone_verify.py --sweep=looksweep --raw=../../samples/a7v_donor.ARW
    python tone_verify.py --sweep=comboprobe --raw=../../samples/DSC01157.ARW
"""
import os
import subprocess
import sys

import numpy as np

SCR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCR)
sys.path.insert(0, os.path.join(SCR, "..", "..", "apps", "worker", "src"))

from extract_tone_family import family  # noqa: E402
from llr_worker.sony import tone as T  # noqa: E402

PROBE = os.path.join(SCR, "toneprobe")   # 锚在脚本旁边,不看 cwd
SCALE = 16384.0
FIELDS = ("contrast", "highlights", "shadows")
ALIAS = {"c": "contrast", "h": "highlights", "s": "shadows", **{f[0] + f[1:]: f for f in FIELDS}}


def parse_tag(tag):
    """``h+9_c+5`` / ``contrast+5`` / ``base`` -> {字段: 档位}。看不懂就返回 None。"""
    if tag == "base":
        return {}
    out = {}
    for part in tag.split("_"):
        for i, ch in enumerate(part):
            if ch in "+-" and i:
                if part[:i] not in ALIAS:
                    return None
                out[ALIAS[part[:i]]] = int(part[i:])
                break
        else:
            return None
    return out


def tone_lut(x, y, table):
    """引擎 0x1903e0 的非负半区,输入是 128 点控制曲线。

    这一段是**引擎的整数域**,`tone.py` 没有对应物(它从归一化曲线接手),所以这里
    自己实现。全程 float32 —— 用 float64 插值再取整会差到 7/16384,因为每一步都是
    `cvttss2si`(截断)而不是就近。
    """
    f32 = np.float32
    v = (np.arange(1, 32768, dtype=np.int64) * 128).astype(np.float64)
    xs, ys = x.astype(np.float64), y.astype(np.float64)

    j = np.clip(np.searchsorted(xs, v, side="right"), 1, len(xs) - 1)
    dx = (xs[j] - xs[j - 1]).astype(f32)
    t = (xs[j] - v).astype(f32) / dx
    val = (f32(1.0) - t) * ys[j].astype(f32) + ys[j - 1].astype(f32) * t
    b = np.trunc(val).astype(np.int64)
    b[v > xs[-1]] = np.int64(ys[-1])          # 超出最后一个控制点 -> 钳住
    b >>= 2

    e = b >> 6
    lo = table[np.minimum(e, T.TUNE_TABLE_TOP)].astype(f32)   # 顶端引擎两端都取 T[1023]
    hi = table[np.minimum(e + 1, T.TUNE_TABLE_TOP)].astype(f32)
    frac = (b - (e << 6)).astype(f32) * f32(1.0 / T.TUNE_TABLE_STEP)
    c = lo * (f32(1.0) - frac) + hi * frac
    out = np.zeros(32768, dtype=np.int64)
    out[1:] = np.trunc(c).astype(np.int64) >> 2
    return out


def exif_tweaks(raw):
    """这张图自带的三个档位 —— gain 的**起点**,少了它对不上。"""
    def one(tag):
        s = subprocess.run(["exiftool", "-s3", "-" + tag, raw],
                           capture_output=True, text=True).stdout.strip()
        return 0 if not s or s == "Normal" else int(s)
    return {f: one(f.capitalize()) for f in FIELDS}


def main():
    fam = family()
    print(f"曲线族 {fam.shape},FAM[18] 是恒等(extract_tone_family 里断言)✓\n")

    # 这批图自己的机内设置。tone_probe 抓的都是 DSC01157。
    base = {"contrast": 0, "highlights": -6, "shadows": 1}
    print(f"{'情形':22} {'gain_lo':>8} {'gain_hi':>8}   算子表差   tone LUT 差")
    worst_t = worst_l = 0
    for fn in sorted(os.listdir(PROBE)):
        if not fn.endswith(".npz"):
            continue
        over = parse_tag(fn[:-4].partition("_")[2])
        if over is None:
            print(f"  {fn}: 看不懂的档位名,跳过")
            continue
        z = np.load(os.path.join(PROBE, fn))
        s = {**base, **over}
        table = np.asarray(T._operator_table(*(float(s[f]) for f in
                                               ("highlights", "shadows", "contrast"))))
        dt = int(np.abs(table.astype(np.int64)
                        - z["b0_t"][:T.TUNE_TABLE_LEN].astype(np.int64)).max())
        lut = tone_lut(z["b0_x"].astype(np.float64), z["b0_y"].astype(np.float64), table)
        dl = int(np.abs(lut - z["lut"].astype(np.int64)).max())
        worst_t, worst_l = max(worst_t, dt), max(worst_l, dl)
        print(f"  {fn[:-4]:22} {18 + s['contrast'] - s['shadows']:8.1f}"
              f" {18 + s['contrast'] + s['highlights']:8.1f} {dt:10d} {dl:12d}")
    print(f"\n最坏:算子表 {worst_t}   tone LUT {worst_l}   (引擎整数域,应当都是 0)")


def sweep(sweep_dir, raw):
    """走出厂路径 `base_curve` + `apply_tuning`,对实测 LUT。"""
    from llr_worker.sony.sr2 import look_calibrations

    base = exif_tweaks(raw)
    cals = look_calibrations(raw)
    codes = [c.name for c in cals]
    print(f"{os.path.basename(raw)} 自带设置 {base}   {len(cals)} 个外观")

    worst, n = 0.0, 0
    for fn in sorted(os.listdir(sweep_dir)):
        if not fn.endswith(".npy"):
            continue
        look, _, tag = fn[:-4].partition("_")
        over = parse_tag(tag)
        idx = (T.LOOK_ORDER.index(look) if look in T.LOOK_ORDER
               else codes.index(look) if look in codes else None)
        if over is None or idx is None or idx >= len(cals):
            print(f"  {fn}: 这个文件里没有 {look} 或看不懂档位名,跳过")
            continue
        got = T.apply_tuning(T.base_curve(cals[idx]), **{**base, **over})
        truth = np.load(os.path.join(sweep_dir, fn)).astype(np.float64)[:8193] / SCALE
        e = np.abs(got - truth).max() * SCALE
        worst = max(worst, e)
        n += 1
        if e > 1.0:
            print(f"  {fn:26} 最大差 {e:.2f}")
    print(f"  {n} 张,最大差 {worst:.2f}/16384   (出厂路径,已知 ~1 的 float64/32 差)")


if __name__ == "__main__":
    _sweep = P_ = None
    for _a in sys.argv[1:]:
        if _a.startswith("--sweep="):
            _sweep = _a.split("=", 1)[1]
        elif _a.startswith("--raw="):
            P_ = _a.split("=", 1)[1]
    if _sweep:
        sweep(_sweep, os.path.abspath(P_))
    else:
        main()
