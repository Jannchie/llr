r"""整批跑主管线对机内 JPEG,给出每张的误差,用来**定位**还差在哪。

注意这是分诊工具,不是判决工具:拿整幅复刻去比机内 JPEG 会把上下游所有阶段的
误差揉成一个数(PIPELINE.md §11 记过这个教训)。但要回答「哪些图不一致」,
按误差排序恰恰是对的第一步 —— 挑出离群的那几张,再用 stage_frame.py 逐段拆。

用法:
    python survey_batch.py              # 全部
    python survey_batch.py DSC02961 ... # 指定
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

sys.path.insert(0, str(Path(__file__).parent))
from e2e_pipeline import SRC, render  # noqa: E402

M = np.array([[0.299, 0.587, 0.114], [-0.168736, -0.331264, 0.5],
              [0.5, -0.418688, -0.081312]], np.float32)
EXIFTOOL = "/home/jannchie/llr/vendor/exiftool/exiftool"
# 想看看误差跟哪个拍摄参数走。DRO/WB/色温是最可能的嫌疑。
EXIF_KEYS = ("WhiteBalance", "ColorTemperature", "DynamicRangeOptimizer", "ISO",
             "LightSource", "WB_RGGBLevels")


def exif(path: Path) -> dict:
    try:
        out = subprocess.run([EXIFTOOL, "-j", "-n", *[f"-{k}" for k in EXIF_KEYS], str(path)],
                             capture_output=True, text=True, timeout=60)
        return json.loads(out.stdout)[0] if out.stdout.strip() else {}
    except Exception:
        return {}


def measure(ours, jpg):
    """按 **JPEG 的** 色度筛像素 —— 换配置时点集才不会跟着变。"""
    sel = ((jpg.max(-1) - jpg.min(-1) > 0.10) & (jpg.max(-1) > 0.15) & (jpg.max(-1) < 0.95))
    if sel.sum() < 500:
        return None
    a, b = ours[sel], jpg[sel]
    ra, rb = np.hypot(a @ M[1], a @ M[2]), np.hypot(b @ M[1], b @ M[2])
    dh = np.degrees(np.arctan2(b @ M[2], b @ M[1]) - np.arctan2(a @ M[2], a @ M[1]))
    return {
        "n": int(sel.sum()),
        "chroma": float(np.median(rb / np.maximum(ra, 1e-6))),
        "hue": float(np.median((dh + 180) % 360 - 180)),
        "luma": float(np.median((b @ M[0]) - (a @ M[0]))),
        "rmse": float(np.sqrt(((ours - jpg) ** 2).mean()) * 255),
    }


def main():
    stems = sys.argv[1:] or sorted(p.stem for p in SRC.glob("*.ARW"))
    rows = []
    for stem in stems:
        jpg_path = SRC / f"{stem}.JPG"
        if not jpg_path.exists():
            continue
        try:
            ours, cp = render(SRC / f"{stem}.ARW", "sony")
        except Exception as e:                       # 回落到 DCP 的、非索尼的,都跳过
            print(f"{stem}: skip ({type(e).__name__}: {e})", flush=True)
            continue
        h, w = ours.shape[:2]
        # rawpy 按 EXIF 把画面转正,PIL 不会。竖幅照片直接 resize 成横的会把
        # RMSE 顶到 80/255,看着像颜色崩了,其实一个像素都没对上。
        img = ImageOps.exif_transpose(Image.open(jpg_path))
        if (img.width > img.height) != (w > h):
            print(f"{stem}: 画幅方向对不上 {img.size} vs {(w, h)},跳过", flush=True)
            continue
        jpg = np.asarray(img.resize((w, h), Image.BILINEAR), np.float32) / 255
        m = measure(ours, jpg)
        if m is None:
            continue
        m.update(stem=stem, look=cp.get("creativeLook") or "?", kind=cp.get("kind"), **exif(jpg_path))
        rows.append(m)
        print("%-10s %-4s 色度 %.3f  色相 %+6.2f  亮度 %+.4f  RMSE %5.1f  n=%d"
              % (stem, m["look"], m["chroma"], m["hue"], m["luma"], m["rmse"], m["n"]),
              flush=True)

    out = Path("/home/jannchie/llr/tmp/survey.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    print(f"\n{len(rows)} 张,写入 {out}")
    if not rows:
        return
    for key, unit in (("chroma", ""), ("hue", " 度"), ("luma", ""), ("rmse", "/255")):
        v = np.array([r[key] for r in rows])
        print("  %-7s 中位 %+.4f%s   p90 %+.4f   最差 %s"
              % (key, np.median(v), unit, np.percentile(np.abs(v - np.median(v)), 90),
                 rows[int(np.argmax(np.abs(v - np.median(v))))]["stem"]))


if __name__ == "__main__":
    main()
