"""重新生成 apps/web/scripts/chroma-fixture.json 里的参考值。

这个 fixture 是 GLSL 与 Python 参考实现之间唯一的**真实数据**对照:合成图只能证明
没接反,真照片才同时带着噪声、硬色边、饱和块和亮度纹理。头一版是临时脚本产的,脚本
没留下来 —— fixture 于是不可复现,改一次标定就没法跟着更新。这个文件补上那一环。

**只重算 ref,不重新渲染 src。** src 是 DSC02995 在 (160,224) 处的 128x128 裁切,
已经在 json 里;重跑一遍整条渲染管线会把管线自身的改动混进来,而这个 fixture 要盯的
是色度降噪这一步。src 换掉的时候再单独说。

产两档:
  ref         amount=1.0,滤波器本身,跟 shader 的 run(px, 1) 对
  ref_shipped amount=DEFAULT_AMOUNT,上线配置,跟 run(px, CHROMA_AMOUNT) 对
两档都对,才能同时抓住「滤波器错了」和「混合系数两边不一致」。
"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")
from llr_worker.sony.chromanr import DEFAULT_AMOUNT, apply_chroma_nr  # noqa: E402

FIX = Path("/home/jannchie/llr/apps/web/scripts/chroma-fixture.json")


def main():
    d = json.loads(FIX.read_text(encoding="utf-8"))
    n = int(d["n"])
    src = np.asarray(d["src"], np.float32).reshape(n, n, 3)

    out = {"note": d["note"], "n": n, "src": d["src"]}
    for key, amt in (("ref", 1.0), ("ref_shipped", float(DEFAULT_AMOUNT))):
        r = apply_chroma_nr(src, subsample=8, amount=amt)
        out[key] = [round(float(v), 6) for v in r.reshape(-1)]
        moved = float(np.abs(r - src).max())
        print(f"  {key:<12} amount={amt:.2f}   算子自身最大位移 {moved:.4f}")
    out["amount_shipped"] = float(DEFAULT_AMOUNT)
    out["note"] = (f"DSC02995 crop at (160,224), {n}x{n}; ref = chromanr fast s8 "
                   f"at amount 1.0, ref_shipped at {DEFAULT_AMOUNT:.2f}. "
                   f"Regenerate with sony_repro/tools/make_chroma_fixture.py")

    FIX.write_text(json.dumps(out), encoding="utf-8")
    print(f"写回 {FIX}  ({FIX.stat().st_size / 1024:.0f} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
