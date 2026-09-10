r"""「高级色彩复制」的整幅 A/B:Edit(高级 − 标准) 对 llr(lut3d − off),同一张、同一降噪档(关)。

    bash -lc "cd ~/llr/apps/worker && uv run --with tifffile python ../../sony_repro/tools/lut3d_ab.py <ARW> <Edit标准.TIF> <Edit高级.TIF>"

按 Edit 标准档的 Y 分桶:两边「开关带来的 ΔY 中位」和「色度比(开/关)」应当一致;再列 llr(开) 对 Edit(高级) 的绝对差。
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np  # noqa: E402

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")
sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")
import colour_check as C  # noqa: E402
import e2e_pipeline as E  # noqa: E402
from lens_apply import apply_distortion, parse_lens_corr  # noqa: E402

from llr_worker.cli import prepare_linear  # noqa: E402


def main():
    arw, tif_std, tif_adv = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    e_std, e_adv = C.load_edit_tiff(tif_std), C.load_edit_tiff(tif_adv)
    meta = prepare_linear(arw, {"profileId": "sony"}, E.ROOT, None, False, half_size=False).metadata
    lens = parse_lens_corr(getattr(meta, "lens_corr", None))
    outs = {}
    for name, flag in (("off", False), ("on", True)):
        img, _ = E.render(arw, "sony", with_chroma=True, half_size=False, denoise_model=None, lut3d=flag)
        img = np.asarray(img, np.float32)
        outs[name] = apply_distortion(img, lens["distortion"])[0].astype(np.float32) if lens else img
    h = min(e_std.shape[0], outs["off"].shape[0]); w = min(e_std.shape[1], outs["off"].shape[1])
    cy, cx = h // 2, w // 2
    win = (slice(cy - 1500, cy + 1500), slice(cx - 1500, cx + 1500))
    ys = C.ycc(e_std[win]); ya = C.ycc(e_adv[win]); lo = C.ycc(outs["off"][:h, :w][win]); ln = C.ycc(outs["on"][:h, :w][win])
    sat = np.hypot(ys[..., 1], ys[..., 2]) > 0.03
    print("Edit 标准 Y 分桶 | Edit(高级−标准) ΔY中位, 色度比 | llr(开−关) ΔY中位, 色度比 | llr(开)−Edit(高级) ΔY中位, 色度比")
    for lo_, hi_ in [(i / 10, (i + 1) / 10) for i in range(10)] + [(0.93, 1.01)]:
        m = (ys[..., 0] >= lo_) & (ys[..., 0] < hi_)
        ms = m & sat
        if m.sum() < 1000:
            continue

        def cr(a, b):
            return np.hypot(a[..., 1], a[..., 2])[ms].mean() / max(np.hypot(b[..., 1], b[..., 2])[ms].mean(), 1e-6)
        print(f"  [{lo_:.2f},{hi_:.2f}) n={m.sum():8d} | {np.median((ya[..., 0] - ys[..., 0])[m]):+.4f} {cr(ya, ys):.3f} | "
              f"{np.median((ln[..., 0] - lo[..., 0])[m]):+.4f} {cr(ln, lo):.3f} | {np.median((ln[..., 0] - ya[..., 0])[m]):+.4f} {cr(ln, ya):.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
