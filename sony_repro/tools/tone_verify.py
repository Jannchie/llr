r"""把反汇编读出来的造表算法跑一遍,和 Edit.exe 抓下来的表 / LUT 逐位对。

模型(RVA 见 PIPELINE.md):

    gain_lo = 18 + contrast − shadows        作用在算子表索引 [0, 471)
    gain_hi = 18 + contrast + highlights     作用在算子表索引 [471, 1025)
    gain 钳到 [0, 35];k = floor(gain),f = gain − k
    T[i] = clamp(round(FAM[k][i] + (FAM[k+1][i] − FAM[k][i]) * f), 0, 65535)

    FAM = RVA 0x484700 的 37 条静态曲线,每条 1025 个 int32。**FAM[18] 就是恒等**
    (i*64),所以三个滑块全零时 gain=18、表恒等 —— 这是整套解读最硬的自证。

    然后 tone LUT:
        b = 分段线性插值(i*128; X, Y) >> 2
        e = min(b >> 6, 1023);  f = (b − (e<<6)) / 64
        LUT[i] = trunc(T[e]*(1−f) + T[e+1]*f) >> 2

用法(先跑 tone_probe.py 攒 toneprobe/)::

    python tone_verify.py
    python tone_verify.py --sweep=looksweep --raw=../../samples/a7v_donor.ARW
    python tone_verify.py --sweep=comboprobe --raw=../../samples/DSC01157.ARW
"""
import os
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pe_scan as P  # noqa: E402

FAM_RVA, FAM_N, FAM_LEN = 0x484700, 37, 1025
SPLIT = 471                  # 两段的分界,来自两次调用的 [0,0x1d7) / [0x1d7,0x401)
NEUTRAL = 18.0               # 0x4df1c8 = 18.0,也是恒等曲线的下标
GAIN_PER_STEP = 5.0          # 0x4df208 = 90.0,90/18 = 5 —— 一个档位挪一格
GAIN_MAX = 35.0              # 0x4df1e8
OUT_MAX = 65535.0            # 0x4df2a0
BIAS = 0.5                   # 0x4deb28 —— 所以是四舍五入,不是截断
PROBE = "toneprobe"


def family():
    blob = P.load()
    off = P.rva_to_off(blob, FAM_RVA)
    a = np.frombuffer(blob[off:off + FAM_N * FAM_LEN * 4], dtype="<i4")
    return a.reshape(FAM_N, FAM_LEN).astype(np.float64)


def segment(fam, gain, lo, hi, out):
    g = min(max(gain, 0.0), GAIN_MAX)
    k = int(np.floor(g))
    f = g - k
    v = fam[k, lo:hi] + (fam[k + 1, lo:hi] - fam[k, lo:hi]) * np.float32(f)
    out[lo:hi] = np.clip(np.trunc(v.astype(np.float32) + np.float32(BIAS)), 0.0, OUT_MAX)


def operator_table(fam, contrast, highlights, shadows):
    t = np.zeros(FAM_LEN)
    segment(fam, NEUTRAL + contrast - shadows, 0, SPLIT, t)
    segment(fam, NEUTRAL + contrast + highlights, SPLIT, FAM_LEN, t)
    return t.astype(np.int64)


def tone_lut(x, y, table):
    """引擎 0x1903e0 的非负半区。x/y 是 128 点控制曲线,table 是上面那张算子表。

    全程按引擎的 **float32** 语义算 —— 用 float64 插值再取整会差到 7/16384,
    因为每一步都是 `cvttss2si`(截断)而不是就近。
    """
    f32 = np.float32
    v = (np.arange(1, 32768, dtype=np.int64) * 128).astype(np.float64)
    xs, ys = x.astype(np.float64), y.astype(np.float64)

    j = np.searchsorted(xs, v, side="right")        # 第一个 X[j] > v
    j = np.clip(j, 1, len(xs) - 1)
    x0, x1 = xs[j - 1], xs[j]
    y0, y1 = ys[j - 1], ys[j]
    dx = (x1 - x0).astype(f32)
    t = (x1 - v).astype(f32) / dx
    val = (f32(1.0) - t) * y1.astype(f32) + y0.astype(f32) * t
    b = np.trunc(val).astype(np.int64)
    over = v > xs[-1]                                # 超出最后一个控制点 -> 钳住
    b[over] = np.int64(ys[-1])
    b >>= 2

    e = b >> 6
    top = e >= 1023                                  # 引擎在这里两端都取 table[1023]
    e_lo = np.where(top, 1023, e)
    e_hi = np.where(top, 1023, e + 1)
    frac = (b - (e << 6)).astype(f32) * f32(1.0 / 64.0)
    c = table[e_lo].astype(f32) * (f32(1.0) - frac) + table[e_hi].astype(f32) * frac
    out = np.zeros(32768, dtype=np.int64)
    out[1:] = np.trunc(c).astype(np.int64) >> 2
    return out


def main():
    fam = family()
    # FAM[18] 是恒等,但**末项除外**:i*64 到 1024 是 65536,超出 uint16,存成 65535。
    ident = np.arange(FAM_LEN) * 64
    got = fam[18].astype(np.int64)
    assert np.array_equal(got[:-1], ident[:-1]), "FAM[18] 前 1024 项必须是 i*64"
    assert got[-1] == 65535, f"FAM[18] 末项应是 65535,实际 {got[-1]}"
    print(f"曲线族 {fam.shape},FAM[18] = i*64(末项钳到 65535)✓\n")

    # 这批图自己的机内设置(exiftool 读出来的),基准点就靠它
    base = {"contrast": 0, "highlights": -6, "shadows": 1}
    cases = {
        "FL_base": {},
        "FL_contrast+5": {"contrast": 5},
        "FL_contrast+9": {"contrast": 9},
        "FL_contrast-9": {"contrast": -9},
        "FL_highlights+9": {"highlights": 9},
        "FL_shadows-9": {"shadows": -9},
    }
    print(f"{'情形':18} {'gain_lo':>8} {'gain_hi':>8}   算子表差   tone LUT 差")
    worst_t = worst_l = 0
    for name, over in cases.items():
        p = os.path.join(PROBE, name + ".npz")
        if not os.path.exists(p):
            print(f"  {name}: 没有抓过,跳过")
            continue
        z = np.load(p)
        s = {**base, **over}
        t = operator_table(fam, s["contrast"], s["highlights"], s["shadows"])
        dt = int(np.abs(t - z["b0_t"][:FAM_LEN].astype(np.int64)).max())
        lut = tone_lut(z["b0_x"].astype(np.float64), z["b0_y"].astype(np.float64), t)
        dl = int(np.abs(lut - z["lut"].astype(np.int64)).max())
        worst_t, worst_l = max(worst_t, dt), max(worst_l, dl)
        print(f"  {name:18} {NEUTRAL + s['contrast'] - s['shadows']:8.1f}"
              f" {NEUTRAL + s['contrast'] + s['highlights']:8.1f}"
              f" {dt:10d} {dl:12d}")
    print(f"\n最坏:算子表 {worst_t}   tone LUT {worst_l}")


FIELD = {"c": "contrast", "h": "highlights", "s": "shadows",
         "contrast": "contrast", "highlights": "highlights", "shadows": "shadows"}


def parse_tag(tag):
    """``h+9_c+5`` / ``contrast+5`` / ``base`` -> {字段: 档位}。看不懂就返回 None。"""
    if tag == "base":
        return {}
    out = {}
    for part in tag.split("_"):
        for i, ch in enumerate(part):
            if ch in "+-" and i:
                key, val = part[:i], part[i:]
                if key not in FIELD:
                    return None
                out[FIELD[key]] = int(val)
                break
        else:
            return None
    return out


def sweep(fam, sweep_dir, raw):
    """拿实测的 tone LUT 逐张对 —— 只用 RAW 自己的曲线标签 + 三个滑块值,不用任何实测形状。"""
    import subprocess
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "..", "apps", "worker", "src"))
    from llr_worker.sony.sr2 import look_calibrations
    from llr_worker.sony.tone import LOOK_ORDER

    def ex(tag):
        s = subprocess.run(["exiftool", "-s3", "-" + tag, raw],
                           capture_output=True, text=True).stdout.strip()
        return 0 if not s or s == "Normal" else int(s)

    base = {"contrast": ex("Contrast"), "highlights": ex("Highlights"),
            "shadows": ex("Shadows")}
    cals = look_calibrations(raw)
    names = [c.name for c in cals] if hasattr(cals[0], "name") else []
    print(f"{os.path.basename(raw)} 自带设置 {base}   {len(cals)} 个外观")

    worst, n_ok, n_bad = 0, 0, 0
    for fn in sorted(os.listdir(sweep_dir)):
        if not fn.endswith(".npy"):
            continue
        look, _, tag = fn[:-4].partition("_")
        over = parse_tag(tag)
        if over is None:
            print(f"  {fn}: 看不懂的档位名,跳过")
            continue
        idx = LOOK_ORDER.index(look) if look in LOOK_ORDER else (
            names.index(look) if look in names else None)
        if idx is None or idx >= len(cals):
            print(f"  {fn}: 这个文件里没有 {look},跳过")
            continue
        cal = cals[idx]
        s = {**base, **over}
        t = operator_table(fam, s["contrast"], s["highlights"], s["shadows"])
        pred = tone_lut(cal.curve_x.astype(np.float64), cal.curve_y.astype(np.float64), t)
        got = np.load(os.path.join(sweep_dir, fn)).astype(np.int64)[:32768]
        d = int(np.abs(pred - got).max())
        worst = max(worst, d)
        n_ok += d == 0
        n_bad += d != 0
        if d:
            print(f"  {fn:26} 最大差 {d}")
    print(f"  逐位相同 {n_ok} 张,有差 {n_bad} 张,最坏 {worst}\n")


if __name__ == "__main__":
    _sweep = next((a.split("=")[1] for a in sys.argv if a.startswith("--sweep=")), None)
    if _sweep:
        _raw = next(a.split("=")[1] for a in sys.argv if a.startswith("--raw="))
        sweep(family(), _sweep, os.path.abspath(_raw))
    else:
        main()
