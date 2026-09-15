r"""高感图的 llr 离线成品:关降噪 / 自动(=手动 50)等几版,补镜头畸变校正,存成 .npy。

    cd apps/worker && uv run python ../../sony_repro/tools/highiso_render.py <ARW> <outdir> [--variants off,auto,nomarble]

链路与 `e2e_pipeline.render` 一致(shader 的 numpy 镜像:RawNR → ITP → 分段矩阵 → 色调 →
RGB2YCC/ChromaSuppres/YGamma → Marble 色差清理),**不含 Sharpness / Spica**(离线链没有,
web 端有;比细带幅度时记得这一条)。ISO ≥ 1600 时 RawNR 的写回强度是 1.0,
所以「自动」和「手动 50/75/100」在 llr 里是同一张图(cli.manual_strength 在 50 以上钳住)。

和 `e2e_pipeline.render` 的两处不同,都只为省内存(33 MP 全幅、float64 中间量会把
Windows 的提交额吃穿):逐像素那一段按行条带跑;`prepare_linear(store_cache=False)`。
`e2e_pipeline` 里写死的是 WSL 路径,导入后改成本机仓库根。
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
from lens_apply import apply_distortion, parse_lens_corr  # noqa: E402

from llr_worker.cli import prepare_linear, read_exiftool_metadata, sony_lens_corrections  # noqa: E402

E.ROOT = HERE.parent.parent
STRIP = 256


def tone_chain(c: np.ndarray, cp: dict, with_chroma: bool = True) -> np.ndarray:
    """`e2e_pipeline.render` 从 `prepare_linear` 出口到 Marble 之前的那一段,按条带。"""
    pts = cp.get("profileToneCurve")
    out = np.empty(c.shape, np.float32)
    grid = np.linspace(0, 1, 2048)
    lut = None
    if pts:
        p = np.asarray(pts, np.float64)
        lut = np.interp(grid, p[:, 0], p[:, 1])
    srgb_basis = cp["kind"] == "sony"
    cross, gain = cp.get("profileChromaCross"), cp.get("profileChromaGain")
    for y0 in range(0, c.shape[0], STRIP):
        s = c[y0:y0 + STRIP].astype(np.float32)
        if lut is not None:
            s = s @ E.PROPHOTO_TO_SRGB.T if srgb_basis else s
            s = np.interp(np.clip(s, 0, 1), grid, lut).astype(np.float32)
            if with_chroma and cross and gain:
                s = E.sony_chroma(s, np.asarray(cross), np.asarray(gain),
                                  cp.get("profileLumaPivot", 0.0),
                                  cp.get("profileLumaContrast", 1.0),
                                  cp.get("profileChromaSaturation", 1.0),
                                  cp.get("profileChromaSuppres"),
                                  cp.get("profileLumaLut"), False,
                                  cp.get("profileLumaLutAdvanced"),
                                  cp.get("profileLumaContrastAdvanced")).astype(np.float32)
            s = s @ E.SRGB_TO_PROPHOTO.T if srgb_basis else s
        out[y0:y0 + STRIP] = E.srgb_encode(np.clip(s, 0, 1) @ E.PROPHOTO_TO_SRGB.T).astype(np.float32)
    return out


def render(arw: Path, nr: str | None, marble, strength: float = 1.0, amount_ui: float = 50.0):
    r = prepare_linear(arw, {"profileId": "sony"}, E.ROOT, None, False, half_size=False,
                       denoise_model=nr, store_cache=False, denoise_strength=strength,
                       denoise_amount_ui=amount_ui)
    cp = r.color_profile
    img = tone_chain(r.linear, cp)
    del r
    gc.collect()
    if marble is not None:
        from llr_worker.sony.marble import apply_marble_chroma_nr, calib_from_sr2
        calib = calib_from_sr2(arw)   # the shot's SR2 tags 0x794a..0x795e (thresholds + decimation factor)
        print(f"      marble calib: {calib}", flush=True)
        img = apply_marble_chroma_nr(img, iso=marble[0], chroma_slider=marble[1], calib=calib)
    return img, cp


def main() -> int:
    arw = Path(sys.argv[1])
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    variants = ["off", "auto"]
    if "--variants" in sys.argv:
        variants = sys.argv[sys.argv.index("--variants") + 1].split(",")
    exif = read_exiftool_metadata(arw)
    iso = int(exif.get("ISO") or 100)
    lens = parse_lens_corr(sony_lens_corrections(exif))
    print(f"{arw.name}: ISO {iso}, 镜头表 {'有' if lens else '无'}", flush=True)
    for v in variants:
        amount = 50.0
        if v == "off":
            nr, marble = None, None
        elif v == "auto":
            nr, marble = "sony", (iso, 5)
        elif v.startswith("m") and v[1:].isdigit():   # m75 / m100: 手动量(RawNR 部分,不含 web 端的 YNR)
            nr, marble, amount = "sony", (iso, 5), float(v[1:])
        elif v == "nomarble":
            nr, marble = "sony", None
        elif v == "marbleonly":
            nr, marble = None, (iso, 5)
        else:
            raise SystemExit(f"未知变体 {v}")
        img, cp = render(arw, nr, marble, amount_ui=amount)
        print(f"  {v}: 渲染 {img.shape} look={cp.get('creativeLook')} nr={nr} marble={marble}", flush=True)
        if lens:
            img, s = apply_distortion(img, lens["distortion"])
            img = img.astype(np.float32)
            print(f"      畸变校正 s={s:.6f}", flush=True)
        np.save(out / f"llr_{v}.npy", np.clip(img, 0, 1).astype(np.float16))
        print(f"      -> {out / f'llr_{v}.npy'}", flush=True)
        del img
        gc.collect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
