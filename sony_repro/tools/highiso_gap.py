r"""高感图:llr 成品对 Edit 成品,量「颜色偏差」与「残余噪声的尺度」。

    cd apps/worker && uv run --with tifffile python ../../sony_repro/tools/highiso_gap.py <workdir> \
        --edit off=DSC03692-off16.TIF,auto=DSC03692-auto16.TIF,m50=...,m75=...,m100=... [--editdir E:\temp_photo] \
        [--llr off=llr_off.npy,auto=llr_auto.npy] [--win 2400] [--png]

三组表:
  A. 对齐:llr 已补镜头畸变(highiso_render.py),这里再估整数位移(中央窗口 Y 相关)。
  B. 颜色偏差:按 Edit 的 Y 分桶,Cb/Cr 均值差(llr − Edit)与 CIELAB a*/b* 均值差,
     分别在原图和 16 px 盒均值后的低频图上量 —— 前者含噪声引起的均值偏移,后者是纯色偏。
  C. 噪声尺度:高斯差分金字塔(σ = 0.5,1,2,4,8,16 px)上各带的 MAD·1.4826,Y / Cb / Cr,
     对每张图、对 llr−Edit 的差图、对「降噪开−关」差分。
  D. Edit 各档之间:m75−m50、m100−m50、m50−auto 的逐像素差与各带 MAD。
所有像素都是 0..1 的 sRGB 编码值;YCbCr 用 Rec.601 权重(与 colour_check.py 一致)。
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np  # noqa: E402
from scipy import ndimage  # noqa: E402

W601 = np.array([0.299, 0.587, 0.114], np.float32)
M601 = np.array([[0.299, 0.587, 0.114], [-0.168736, -0.331264, 0.5], [0.5, -0.418688, -0.081312]], np.float32)
SIGMAS = (0.5, 1.0, 2.0, 4.0, 8.0, 16.0)
BANDS = ("≤1px", "1-2px", "2-4px", "4-8px", "8-16px", "16-32px", ">32px")
YBINS = ((0, .05), (.05, .1), (.1, .2), (.2, .35), (.35, .5), (.5, .7), (.7, .9), (.9, 1.01))


def load_tiff(path: Path, cache: Path) -> np.ndarray:
    npy = cache / (path.stem + ".npy")
    if npy.exists():
        return np.load(npy)
    import subprocess
    import tifffile
    x = tifffile.imread(str(path))
    if x.dtype == np.uint16:
        x = (x.astype(np.float32) / 65535.0).astype(np.float16)
    else:
        x = (x.astype(np.float32) / 255.0).astype(np.float16)
    # 竖拍导出的像素仍是横向、只打 Orientation 标签(colour_check.load_edit_tiff 同一条)
    o = subprocess.run(["exiftool", "-q", "-T", "-n", "-Orientation", str(path)],
                       capture_output=True, text=True).stdout.strip()
    k = {"1": 0, "3": 2, "6": 3, "8": 1}.get(o, 0)
    if k:
        x = np.ascontiguousarray(np.rot90(x, k))
    print(f"  {path.name}: orientation {o or '?'} -> rot90 x{k}, {x.shape}")
    np.save(npy, x)
    return x


def ycc(rgb: np.ndarray) -> np.ndarray:
    return rgb.astype(np.float32) @ M601.T


def srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    c = rgb.astype(np.float32)
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]], np.float32)
    xyz = lin @ m.T / np.array([0.95047, 1.0, 1.08883], np.float32)
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    L = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1])
    b = 200 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], -1)


def best_shift(a: np.ndarray, b: np.ndarray, rng: int = 6) -> tuple:
    """b[y+dy, x+dx] ↔ a[y, x] 的最佳整数位移(相关)。"""
    best = None
    ac = a[rng:-rng, rng:-rng].ravel()
    for dy in range(-rng, rng + 1):
        for dx in range(-rng, rng + 1):
            bb = b[rng + dy:b.shape[0] - rng + dy, rng + dx:b.shape[1] - rng + dx].ravel()
            c = np.corrcoef(ac, bb)[0, 1]
            if best is None or c > best[0]:
                best = (c, dy, dx)
    return best


def mad(v: np.ndarray) -> float:
    v = np.asarray(v, np.float32).ravel()
    return float(np.median(np.abs(v - np.median(v))) * 1.4826)


def bands(x: np.ndarray) -> list:
    """高斯差分:g(0.5)… 依次相减。x 是 (h,w)。返回各带图。"""
    prev = x.astype(np.float32)
    out = []
    for s in SIGMAS:
        g = ndimage.gaussian_filter(prev, s, mode="reflect") if s > 0 else prev
        out.append(prev - g)
        prev = g
    out.append(prev)
    return out


def band_mads(img_ycc: np.ndarray) -> np.ndarray:
    """(3 通道 × 7 带) 的 MAD,单位 /255。"""
    r = np.zeros((3, len(BANDS)), np.float32)
    for c in range(3):
        for i, b in enumerate(bands(img_ycc[..., c])):
            r[c, i] = mad(b) * 255.0
    return r


def fmt_row(name: str, r: np.ndarray) -> str:
    return f"{name:<22}" + "".join(f"{v:7.3f}" for v in r)


def table_bands(title: str, rows: dict) -> None:
    print(f"\n{title}(MAD·1.4826,/255)")
    for c, lab in enumerate(("Y", "Cb", "Cr")):
        print(f"  [{lab}]{'':<17}" + "".join(f"{b:>7}" for b in BANDS))
        for name, r in rows.items():
            print("  " + fmt_row(name, r[c]))


def crop_center(x: np.ndarray, win: int, cy: int | None = None, cx: int | None = None) -> np.ndarray:
    h, w = x.shape[:2]
    cy = h // 2 if cy is None else cy
    cx = w // 2 if cx is None else cx
    return x[cy - win // 2:cy + win // 2, cx - win // 2:cx + win // 2]


def darkest_flat(y: np.ndarray, win: int = 768, step: int = 128) -> tuple:
    """在 8 px 盒均值后的 Y 上找「最平且偏暗」的 win×win 窗口(用来看噪声)。"""
    ys = ndimage.uniform_filter(y.astype(np.float32), 8)
    best = None
    for r in range(0, y.shape[0] - win, step):
        for c in range(0, y.shape[1] - win, step):
            blk = ys[r:r + win, c:c + win]
            m, s = float(blk.mean()), float(blk.std())
            if 0.03 < m < 0.35:
                score = s + 0.02 * m
                if best is None or score < best[0]:
                    best = (score, r, c, m, s)
    return best


def main() -> int:
    work = Path(sys.argv[1])
    work.mkdir(parents=True, exist_ok=True)
    args = sys.argv[2:]

    def opt(name, default=None):
        return args[args.index(name) + 1] if name in args else default

    editdir = Path(opt("--editdir", r"E:\temp_photo"))
    win = int(opt("--win", "2400"))
    edits = {}
    for kv in opt("--edit", "").split(","):
        if kv:
            k, v = kv.split("=")
            edits[k] = load_tiff(editdir / v, work)
    llrs = {}
    for kv in opt("--llr", "").split(","):
        if kv:
            k, v = kv.split("=")
            llrs[k] = np.load(work / v)
    for k, v in edits.items():
        print(f"Edit {k}: {v.shape}")
    for k, v in llrs.items():
        print(f"llr  {k}: {v.shape}")

    ref = edits["auto"] if "auto" in edits else next(iter(edits.values()))
    h, w = ref.shape[:2]
    for k in llrs:
        if llrs[k].shape[:2] != (h, w):
            print(f"  ⚠️ llr {k} 尺寸 {llrs[k].shape[:2]} ≠ Edit {(h, w)},裁公共区域")
    hh = min([h] + [v.shape[0] for v in llrs.values()])
    ww = min([w] + [v.shape[1] for v in llrs.values()])

    # ---- A. 对齐 ----
    dy = dx = 0
    if llrs:
        la = llrs["auto"] if "auto" in llrs else next(iter(llrs.values()))
        ec = crop_center(ref[:hh, :ww], min(win, 1600)).astype(np.float32) @ W601
        lc = crop_center(la[:hh, :ww], min(win, 1600)).astype(np.float32) @ W601
        c, dy, dx = best_shift(ec, lc)
        print(f"\nA. 对齐:Edit[y,x] ↔ llr[y+{dy}, x+{dx}],中央 Y 相关 {c:.4f}")
    # 角落也看一眼(畸变表在边角可能差半像素)
    if llrs:
        for name, (cy, cx) in (("左上", (900, 900)), ("右下", (hh - 900, ww - 900))):
            ec = crop_center(ref[:hh, :ww], 1200, cy, cx).astype(np.float32) @ W601
            lc = crop_center(la[:hh, :ww], 1200, cy, cx).astype(np.float32) @ W601
            c2, dy2, dx2 = best_shift(ec, lc, 4)
            print(f"   {name} 1200²:位移 ({dy2},{dx2}) 相关 {c2:.4f}")

    def aligned(x, is_llr):
        if is_llr:
            return x[max(0, dy):hh + min(0, dy), max(0, dx):ww + min(0, dx)]
        return x[max(0, -dy):hh - max(0, dy), max(0, -dx):ww - max(0, dx)]

    E = {k: aligned(v, False) for k, v in edits.items()}
    L = {k: aligned(v, True) for k, v in llrs.items()}
    H, Wd = next(iter(E.values())).shape[:2]
    print(f"   公共区域 {H}x{Wd}")

    # 中央窗口 + 暗平坦窗口
    yref = np.asarray(crop_center(E[next(iter(E))], min(win, H, Wd)), np.float32) @ W601
    wins = {"中央": (H // 2, Wd // 2, min(win, H, Wd))}
    df = darkest_flat(np.asarray(E[next(iter(E))][..., :], np.float32) @ W601 if H * Wd < 40e6 else yref)
    if df is not None:
        _, r, c_, m, s = df
        wins["暗平坦"] = (r + 384, c_ + 384, 768)
        print(f"   暗平坦窗口 768² @ ({r},{c_}),Y 均值 {m:.3f},8px 盒 std {s:.4f}")
    del yref

    for wname, (cy, cx, wsz) in wins.items():
        print(f"\n=================== 窗口「{wname}」{wsz}² @ ({cy},{cx}) ===================")
        Ew = {k: crop_center(v, wsz, cy, cx).astype(np.float32) for k, v in E.items()}
        Lw = {k: crop_center(v, wsz, cy, cx).astype(np.float32) for k, v in L.items()}
        Ey = {k: ycc(v) for k, v in Ew.items()}
        Ly = {k: ycc(v) for k, v in Lw.items()}
        import gc
        gc.collect()

        # ---- B. 颜色偏差 ----
        if "auto" in Ey and "auto" in Ly:
            ea, la_ = Ey["auto"], Ly["auto"]
            ea_l, la_l = srgb_to_lab(Ew["auto"]), srgb_to_lab(Lw["auto"])
            box = lambda x: ndimage.uniform_filter(x, 16, mode="reflect")  # noqa: E731
            ea16 = np.stack([box(ea[..., i]) for i in range(3)], -1)
            la16 = np.stack([box(la_[..., i]) for i in range(3)], -1)
            eal16 = np.stack([box(ea_l[..., i]) for i in range(3)], -1)
            lal16 = np.stack([box(la_l[..., i]) for i in range(3)], -1)
            print("\nB. 颜色偏差 llr − Edit(自动档),按 Edit Y 分桶;单位:Cb/Cr ×255,a*/b* 是 Lab 单位")
            print(f"  {'Y 桶':<12}{'n':>9} | {'ΔY':>7}{'ΔCb':>7}{'ΔCr':>7} | {'Δa*':>7}{'Δb*':>7} | 16px低频: {'ΔCb':>7}{'ΔCr':>7}{'Δa*':>7}{'Δb*':>7} | {'|C|比':>6}{'|C|比16':>8}")
            for lo, hi in YBINS:
                m = (ea[..., 0] >= lo) & (ea[..., 0] < hi)
                if m.sum() < 2000:
                    continue
                d = (la_ - ea)[m].mean(0) * 255
                dl = (la_l - ea_l)[m].mean(0)
                d16 = (la16 - ea16)[m].mean(0) * 255
                dl16 = (lal16 - eal16)[m].mean(0)
                cr = np.hypot(la_[..., 1], la_[..., 2])[m].mean() / max(np.hypot(ea[..., 1], ea[..., 2])[m].mean(), 1e-6)
                cr16 = np.hypot(la16[..., 1], la16[..., 2])[m].mean() / max(np.hypot(ea16[..., 1], ea16[..., 2])[m].mean(), 1e-6)
                print(f"  [{lo:.2f},{hi:.2f}) {int(m.sum()):9d} | {d[0]:+7.2f}{d[1]:+7.2f}{d[2]:+7.2f} | {dl[1]:+7.2f}{dl[2]:+7.2f} |           {d16[1]:+7.2f}{d16[2]:+7.2f}{dl16[1]:+7.2f}{dl16[2]:+7.2f} | {cr:6.3f}{cr16:8.3f}")
            # 同样看「关」对「关」,把降噪的贡献和色彩链本身的贡献分开
            if "off" in Ey and "off" in Ly:
                eo, lo_ = Ey["off"], Ly["off"]
                eo16 = np.stack([box(eo[..., i]) for i in range(3)], -1)
                lo16 = np.stack([box(lo_[..., i]) for i in range(3)], -1)
                print("  —— 关降噪对关降噪(16px 低频 ΔCb/ΔCr,×255):")
                for lo, hi in YBINS:
                    m = (eo[..., 0] >= lo) & (eo[..., 0] < hi)
                    if m.sum() < 2000:
                        continue
                    d16 = (lo16 - eo16)[m].mean(0) * 255
                    d = (lo_ - eo)[m].mean(0) * 255
                    print(f"  [{lo:.2f},{hi:.2f}) {int(m.sum()):9d} | 原图 ΔY {d[0]:+6.2f} ΔCb {d[1]:+6.2f} ΔCr {d[2]:+6.2f} | 16px ΔCb {d16[1]:+6.2f} ΔCr {d16[2]:+6.2f}")
                del eo16, lo16
            del ea_l, la_l, ea16, la16, eal16, lal16

        # ---- C. 噪声尺度 ----
        rows = {}
        for k, v in Ey.items():
            rows[f"Edit {k}"] = band_mads(v)
        for k, v in Ly.items():
            rows[f"llr {k}"] = band_mads(v)
        table_bands("C1. 各图自身的各带 MAD", rows)
        rows = {}
        if "auto" in Ey and "auto" in Ly:
            rows["llr auto − Edit auto"] = band_mads(Ly["auto"] - Ey["auto"])
        if "off" in Ey and "off" in Ly:
            rows["llr off − Edit off"] = band_mads(Ly["off"] - Ey["off"])
        if "off" in Ey and "auto" in Ey:
            rows["Edit auto − off"] = band_mads(Ey["auto"] - Ey["off"])
        if "off" in Ly and "auto" in Ly:
            rows["llr auto − off"] = band_mads(Ly["auto"] - Ly["off"])
        if "nomarble" in Ly and "auto" in Ly:
            rows["llr auto − nomarble"] = band_mads(Ly["auto"] - Ly["nomarble"])
            rows["llr nomarble − Edit auto"] = band_mads(Ly["nomarble"] - Ey["auto"])
        if rows:
            table_bands("C2. 差图的各带 MAD", rows)
        if "off" in Ey and "auto" in Ey and "off" in Ly and "auto" in Ly:
            print("\nC3. 「降噪开−关」差分,两边逐带相关(形状是否一致)")
            de, dl = Ey["auto"] - Ey["off"], Ly["auto"] - Ly["off"]
            for c, lab in enumerate(("Y", "Cb", "Cr")):
                be, bl = bands(de[..., c]), bands(dl[..., c])
                print(f"  [{lab}] " + " ".join(f"{BANDS[i]}:{np.corrcoef(be[i].ravel(), bl[i].ravel())[0, 1]:.2f}" for i in range(len(BANDS))))

        # ---- D. Edit 各档之间 ----
        keys = [k for k in ("auto", "m25", "m50", "m75", "m100") if k in Ey]
        if len(keys) >= 2:
            print("\nD. Edit 各档之间的逐像素差(/255):|Δ| 均值、p99、相同像素比例(8 位量化后)")
            base = "m50" if "m50" in Ey else keys[0]
            rows = {}
            for k in keys:
                if k == base:
                    continue
                d = Ey[k] - Ey[base]
                q_same = float(np.mean(np.all(np.rint(Ew[k] * 255) == np.rint(Ew[base] * 255), -1)))
                print(f"  {k:>5} − {base}: |ΔY| 均值 {np.abs(d[..., 0]).mean() * 255:.3f}  p99 {np.percentile(np.abs(d[..., 0]), 99) * 255:.3f}  "
                      f"|ΔCb| {np.abs(d[..., 1]).mean() * 255:.3f}  |ΔCr| {np.abs(d[..., 2]).mean() * 255:.3f}  8bit 相同 {q_same * 100:.1f}%")
                rows[f"{k} − {base}"] = band_mads(d)
            table_bands("D2. 各档差图的各带 MAD", rows)

        if "--png" in args:
            from PIL import Image
            zoom = 2
            n = 400
            tiles = []
            names = []
            for k in ("off", "auto", "m75", "m100"):
                if k in Ew:
                    tiles.append(crop_center(Ew[k], n)); names.append(f"Edit {k}")
            for k in ("off", "auto"):
                if k in Lw:
                    tiles.append(crop_center(Lw[k], n)); names.append(f"llr {k}")
            row = np.concatenate([np.kron(t, np.ones((zoom, zoom, 1), np.float32)) for t in tiles], 1)
            Image.fromarray(np.clip(row * 255, 0, 255).astype(np.uint8)).save(work / f"crop_{wname}.png")
            print(f"\n  PNG -> {work / f'crop_{wname}.png'}  顺序:{names}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
