"""目视检查 chromanr:硬色边有没有渗色。

数字已经对上 Edit(§2.8),但「引擎在色度阶跃上怎么做」是唯一没测过的性质,
而尺度分割必然软化阶跃。所以要挑**饱和度高、色边硬**的区域看,不是挑平坦区。
三张一组:llr 现状 / llr + chromanr / Edit 成品,同一裁块。
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
from PIL import Image

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")
sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")
import e2e_pipeline as E  # noqa: E402
from engine_final_check import align, to_grid  # noqa: E402
from llr_worker.sony.chromanr import apply_chroma_nr  # noqa: E402

SRC = Path("/mnt/e/10960725")
TMP = Path("/home/jannchie/llr/tmp")
OUT = TMP / "nr_out"
FULL = 16383


def main():
    stem = sys.argv[1] if len(sys.argv) > 1 else "DSC02995"
    OUT.mkdir(parents=True, exist_ok=True)
    z = np.load(TMP / f"final_{stem}.npz")
    step, W, H = (int(v) for v in z["step"])
    eng = z["ZcTaskSIMDMarble_out"][:H // step, :W // step]

    ours, _ = E.render(SRC / f"{stem}.ARW", "sony", half_size=False,
                       denoise_model="wavelet")
    ours = to_grid(align(ours, eng, eng.shape[:2])[0], eng.shape[:2])
    clean = apply_chroma_nr(ours.astype(np.float32))

    # 挑色度**方差最大**的 256 见方裁块 —— 那里才有硬色边可看
    e = eng.astype(np.float32) / FULL
    cb = e[..., 2] - e[..., 1]
    t = 64
    h, w = cb.shape[0] // t * t, cb.shape[1] // t * t
    v = cb[:h, :w].reshape(h // t, t, w // t, t).std(axis=(1, 3))
    j, i = np.unravel_index(int(np.argmax(v)), v.shape)
    y0 = min(max(j * t - 96, 0), eng.shape[0] - 256)
    x0 = min(max(i * t - 96, 0), eng.shape[1] - 256)
    print(f"{stem}  裁块 ({y0}, {x0}) 256x256  色度 std {v[j, i]:.4f}")

    crop = (slice(y0, y0 + 256), slice(x0, x0 + 256))
    imgs = {
        "llr": ours[crop],
        "llr_chromanr": clean[crop],
        "edit": e[crop],
    }
    for tag, img in imgs.items():
        a = np.clip(img, 0, 1)
        # 放大 3 倍(最近邻)才看得清单像素级的渗色
        a = np.repeat(np.repeat(a, 3, axis=0), 3, axis=1)
        Image.fromarray((a * 255).astype(np.uint8)).save(OUT / f"chroma_{tag}.png")
        d = img[..., 2] - img[..., 1]
        print(f"  {tag:<14} (B-G) std {d.std():.4f}  幅度 "
              f"{np.percentile(np.abs(d), 99):.4f}")
    print(f"\n写到 {OUT}/chroma_*.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
