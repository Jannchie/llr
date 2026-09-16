r"""DRO 的成品差距:llr 各档 vs Edit 各档 vs 机内 JPEG,按亮度分段量。

    cd apps/worker && uv run --with tifffile python ../../sony_repro/tools/dro_gap.py <ARW> <workdir> \
        [--variants off,auto,global,m0,m50,m99] \
        [--edit off=DSC02857-nroff-drooff.TIF,auto=...,m0=...,m50=...,m100=...] [--editdir E:\temp_photo] \
        [--jpg E:\temp_photo\10160623\DSC02857.JPG] [--render-only] [--png]

llr 侧是 `highiso_render.tone_chain` 那条离线链(shader 的 numpy 镜像,不含 Sharpness/Spica),
在 `prepare_linear` 出口、色调链之前按 shader `passes.ts` 的 DRO 段插一步:

    Ylog = log2(BT.601(c) * droLumaWhite)
    m    = grid ? dro_local_mean(grid, x, y, Ylog) : Ylog
    c   *= table[m / droLogCeiling]

变体:off(不做)/ auto(RAW 曲线 + 网格,生产路径)/ global(RAW 曲线、无网格,回退路径)/
m<N>(引擎内置预设 level N + 网格,手动档)。渲染结果存 `llr_<variant>.npy`(sRGB 编码,float16)。

对比:先补镜头畸变、估整数位移,然后按参考图的 Y 分桶,报 llr − 参考 的 Y 均值差(/255)、
L* 均值差,以及 16 px 低频差图的 MAD(局部结构对不对)。
"""
import gc
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "apps" / "worker" / "src"))
import e2e_pipeline as E  # noqa: E402
import highiso_render as HR  # noqa: E402
from highiso_gap import YBINS, best_shift, load_tiff, srgb_to_lab  # noqa: E402
from lens_apply import apply_distortion, parse_lens_corr  # noqa: E402

from llr_worker.cli import prepare_linear, read_exiftool_metadata, sony_lens_corrections  # noqa: E402
from llr_worker.sony.dro import (  # noqa: E402
    DRO_LOG_CEILING, DRO_LUMA_WHITE, DroGrid, dro_gain_table, dro_level_gain_table, dro_local_mean,
)

E.ROOT = HERE.parent.parent
W601 = np.array([0.299, 0.587, 0.114], np.float32)
STRIP = 256


def grid_from_json(g: dict | None) -> DroGrid | None:
    if not g:
        return None
    shape = (g["ny"], g["nx"], g["bins"])
    return DroGrid(num=np.asarray(g["num"], np.float64).reshape(shape),
                   den=np.asarray(g["den"], np.float64).reshape(shape),
                   uv=tuple(float(v) for v in g["uv"]))


def apply_dro(c: np.ndarray, table: list[float], grid: DroGrid | None) -> np.ndarray:
    """passes.ts 的 DRO 段,按条带。c 是线性 ProPhoto (h,w,3)。"""
    h, w = c.shape[:2]
    tab = np.asarray(table, np.float64)
    at = np.linspace(0.0, 1.0, len(tab))
    xs = (np.arange(w, dtype=np.float64) + 0.5) / w
    out = np.empty_like(c)
    for y0 in range(0, h, STRIP):
        s = c[y0:y0 + STRIP].astype(np.float64)
        luma = np.maximum(s @ W601.astype(np.float64), 1e-9)
        ylog = np.log2(luma * DRO_LUMA_WHITE)
        if grid is not None:
            ys = (np.arange(y0, y0 + s.shape[0], dtype=np.float64) + 0.5) / h
            xx, yy = np.broadcast_arrays(xs[None, :], ys[:, None])
            m = dro_local_mean(grid, xx, yy, ylog)
        else:
            m = ylog
        g = np.interp(np.clip(m / DRO_LOG_CEILING, 0.0, 1.0), at, tab)
        out[y0:y0 + STRIP] = (s * g[..., None]).astype(c.dtype)
    return out


def render(arw: Path, variants: list[str], out: Path, lens) -> None:
    r = prepare_linear(arw, {"profileId": "sony"}, E.ROOT, None, False, half_size=False,
                       denoise_model=None, store_cache=False)
    cp = r.color_profile
    lin = r.linear
    del r
    gc.collect()
    grid = grid_from_json(cp.get("profileDroGrid"))
    auto_table = cp.get("profileDroGain")
    print(f"  look={cp.get('creativeLook')} grid={'有' if grid else '无'} "
          f"auto曲线={'有' if auto_table else '无'} white={cp.get('droLumaWhite')} "
          f"ceiling={cp.get('droLogCeiling')}", flush=True)
    if auto_table is None:
        auto_table = dro_gain_table(arw)
    for v in variants:
        if v == "off":
            c = lin
        elif v == "auto":
            c = apply_dro(lin, auto_table, grid)
        elif v == "global":
            c = apply_dro(lin, auto_table, None)
        elif v.startswith("m") and v[1:].isdigit():
            c = apply_dro(lin, dro_level_gain_table(int(v[1:])), grid)
        else:
            raise SystemExit(f"未知变体 {v}")
        img = HR.tone_chain(c, cp)
        del c
        gc.collect()
        if lens:
            img, s = apply_distortion(img, lens["distortion"])
            img = img.astype(np.float32)
        np.save(out / f"llr_{v}.npy", np.clip(img, 0, 1).astype(np.float16))
        print(f"  {v}: -> llr_{v}.npy {img.shape}", flush=True)
        del img
        gc.collect()


def load_jpg(path: Path, cache: Path) -> np.ndarray:
    npy = cache / (path.stem + "_jpg.npy")
    if npy.exists():
        return np.load(npy)
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    x = (np.asarray(im, np.float32) / 255.0).astype(np.float16)
    np.save(npy, x)
    return x


def compare(name: str, llr: np.ndarray, ref: np.ndarray, win: int = 3000) -> None:
    """llr 对参考:整数位移对齐后,按参考 Y 分桶报差。"""
    h = min(llr.shape[0], ref.shape[0])
    w = min(llr.shape[1], ref.shape[1])
    a = llr[:h, :w].astype(np.float32)
    b = ref[:h, :w].astype(np.float32)
    cy, cx = h // 2, w // 2
    n = min(win, h, w) // 2
    ya = a[cy - n:cy + n, cx - n:cx + n] @ W601
    yb = b[cy - n:cy + n, cx - n:cx + n] @ W601
    corr, dy, dx = best_shift(ya, yb)
    if dy or dx:
        b = np.roll(b, (-dy, -dx), axis=(0, 1))
    ya, yb = a @ W601, b @ W601
    d = (ya - yb) * 255.0
    la, lb = srgb_to_lab(a)[..., 0], srgb_to_lab(b)[..., 0]
    from scipy import ndimage
    low = ndimage.uniform_filter(d, 16)
    print(f"\n[{name}] 对齐 corr={corr:.4f} shift=({dy},{dx})  "
          f"整幅 ΔY 均值 {d.mean():+.2f} RMS {np.sqrt((d ** 2).mean()):.2f}  "
          f"ΔL* 均值 {(la - lb).mean():+.2f}  16px低频 |ΔY| 中位 {np.median(np.abs(low)):.2f} /255")
    print(f"  {'参考Y段':<12}{'像素%':>7}{'ΔY均值':>9}{'ΔY RMS':>9}{'ΔL*':>8}{'Y比(llr/ref)':>14}")
    for lo, hi in YBINS:
        m = (yb >= lo) & (yb < hi)
        if m.sum() < 1000:
            continue
        ratio = np.median(ya[m] / np.maximum(yb[m], 1e-4))
        print(f"  {lo:.2f}-{min(hi, 1):.2f}    {100 * m.mean():6.1f}  {d[m].mean():+8.2f}  "
              f"{np.sqrt((d[m] ** 2).mean()):8.2f}  {(la[m] - lb[m]).mean():+7.2f}  {ratio:13.3f}")


def main() -> int:
    arw = Path(sys.argv[1])
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    args = sys.argv[3:]

    def opt(flag, default=None):
        return args[args.index(flag) + 1] if flag in args else default

    variants = opt("--variants", "off,auto,global").split(",")
    editdir = Path(opt("--editdir", r"E:\temp_photo"))
    edits = dict(kv.split("=", 1) for kv in opt("--edit", "").split(",") if kv)
    jpg = opt("--jpg")

    exif = read_exiftool_metadata(arw)
    lens = parse_lens_corr(sony_lens_corrections(exif))
    print(f"{arw.name}: DRO={exif.get('DynamicRangeOptimizer')} 镜头表 {'有' if lens else '无'}", flush=True)
    todo = [v for v in variants if not (out / f"llr_{v}.npy").exists()]
    if todo:
        render(arw, todo, out, lens)
    if "--render-only" in args:
        return 0

    llr = {v: np.load(out / f"llr_{v}.npy") for v in variants}
    refs = {}
    for k, fn in edits.items():
        refs[f"Edit {k}"] = load_tiff(editdir / fn, out)
    if jpg:
        refs["机内 JPEG"] = load_jpg(Path(jpg), out)

    if "--png" in args:
        from PIL import Image
        for k, x in {**{f"llr_{v}": a for v, a in llr.items()}, **refs}.items():
            sm = x[::4, ::4].astype(np.float32)
            Image.fromarray((np.clip(sm, 0, 1) * 255).astype(np.uint8)).save(out / f"{k.replace(' ', '_')}.png")

    # 1. Edit 各档之间(DRO 到底改了多少),以及机内 vs Edit 自动
    keys = list(refs)
    if "Edit off" in refs:
        for k in keys:
            if k != "Edit off":
                compare(f"{k} − Edit off", refs[k], refs["Edit off"])
    # 2. llr 各档对同名 Edit 档
    pairs = {"off": "Edit off", "auto": "Edit auto", "global": "Edit auto"}
    for v in variants:
        ref = pairs.get(v) or (f"Edit {v}" if f"Edit {v}" in refs else None)
        if ref in refs:
            compare(f"llr {v} − {ref}", llr[v], refs[ref])
    # 3. llr 自动 对 机内
    if "机内 JPEG" in refs:
        for v in variants:
            compare(f"llr {v} − 机内 JPEG", llr[v], refs["机内 JPEG"])
        if "Edit auto" in refs:
            compare("Edit auto − 机内 JPEG", refs["Edit auto"], refs["机内 JPEG"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
