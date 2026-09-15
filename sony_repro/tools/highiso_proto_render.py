r"""最小原型:两处修正各渲一版,不改生产代码(monkeypatch)。

    cd apps/worker && uv run python ../../sony_repro/tools/highiso_proto_render.py <ARW> <outdir> [--variants fixA,fixAB]

  fixA : RawNR 入口**不把黑电平以下的值钳到黑**。生产的 `denoise._pack_normalise` 是
         clip((x − black)/scale, 0, 1),ISO 25600 时 7% 的像素在黑电平以下,被钳后 sigma 滤波的
         均值往上偏(+3.3 级,R 相位最重)→ 暗部偏亮偏红。引擎喂的是原始值(含负噪声),
         同一 tile 上直接跑核就是 100% 逐位;换成不钳的 normalise 即可。
  fixAB: 再把 Marble 的阈值标定换成本片导出时抓到的 ctx(p34/p38/p40/p48/p4c/p50),
         生产里是 ISO 2000 那张的常量 CALIB_7CM2。
输出 llr_<variant>.npy(float16,已补镜头畸变),与 highiso_render.py 同格式。
"""
import gc
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "apps" / "worker" / "src"))
import highiso_render as R  # noqa: E402
from lens_apply import apply_distortion, parse_lens_corr  # noqa: E402

import llr_worker.denoise as D  # noqa: E402
from llr_worker.cli import read_exiftool_metadata, sony_lens_corrections  # noqa: E402
from llr_worker.sony import marble as M  # noqa: E402

#: 本片(ILCE-7CM2 ISO 25600)导出时 cnr2 的 ctx(export_marble_capture DSC03692-mc)。
MARBLE_CTX_25600 = {"p34": -7, "p38": 2222, "p3c": -1, "p40": 184, "p44": -1, "p48": 487,
                    "base_4c": 640, "base_50": 640, "base_54": 128, "base_58": 128}


def _pack_normalise_noclip(mosaic, black, scale):
    p = D.pack_bayer(mosaic).astype(np.float32)
    return (p - np.asarray(black, np.float32)) / np.asarray(scale, np.float32)


def main() -> int:
    arw, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    variants = sys.argv[sys.argv.index("--variants") + 1].split(",") if "--variants" in sys.argv else ["fixA", "fixAB"]
    exif = read_exiftool_metadata(arw)
    iso = int(exif.get("ISO") or 100)
    lens = parse_lens_corr(sony_lens_corrections(exif))
    orig_norm = D._pack_normalise
    orig_calib = dict(M.CALIB_7CM2)
    for v in variants:
        D._pack_normalise = _pack_normalise_noclip if v.startswith("fixA") else orig_norm
        M.CALIB_7CM2.clear()
        M.CALIB_7CM2.update(orig_calib)
        if v.endswith("B"):
            M.CALIB_7CM2.update(MARBLE_CTX_25600)
        marble = None if v.endswith("nomarble") else (iso, 5)
        img, cp = R.render(arw, "sony", marble)
        print(f"  {v}: 渲染 {img.shape} marble={marble} calib p38={M.CALIB_7CM2['p38']} normalise={'noclip' if v.startswith('fixA') else 'clip'}", flush=True)
        if lens:
            # 逐通道做,省下 apply_distortion 的 float64 整幅临时量(提交额紧张时会 OOM)
            chans = []
            for c in range(3):
                d, s = apply_distortion(np.ascontiguousarray(img[..., c:c + 1]), lens["distortion"])
                chans.append(d.astype(np.float16)[..., 0])
                del d
                gc.collect()
            del img
            img = np.stack(chans, -1)
            del chans
        np.save(out / f"llr_{v}.npy", np.clip(img, 0, 1).astype(np.float16))
        print(f"      -> {out / f'llr_{v}.npy'}", flush=True)
        del img
        gc.collect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
