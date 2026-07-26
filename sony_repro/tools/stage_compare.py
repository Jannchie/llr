r"""同一块内存的前后两份,把中间整段单独拎出来验证。

`stage_pair.py` 发现 MainGamma 与 SSCS 有若干共用的缓冲区 —— 那些 tile 是原地变换。
于是同一个指针上:

    MainGamma 入口 = 矩阵之后、曲线之前
    SSCS 入口      = 曲线 + RGB2YCC + ChromaSuppres + YGamma + YCC2RGB + ITP 之后

我们能复刻其中的曲线、RGB2YCC 与 YCC2RGB,所以差额就是 YGamma 与 ITP 的净效果。
不需要跟整幅对齐,也不受 demosaic / 白平衡 / 矩阵的影响。

用法: python stage_compare.py <ARW> <stage_pair.npz> [style]
"""
import ast
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llr_worker.sony.sr2 import look_calibrations  # noqa: E402
from llr_worker.sony.tone import LOOK_ORDER, tone_curve  # noqa: E402
from sony_repro.ycc import FULL  # noqa: E402

TONE_WHITE = 8192


def load(npz):
    z = np.load(npz, allow_pickle=True)
    meta = [ast.literal_eval(s) for s in z["meta"]]
    out = {}
    for kind, entries in zip(("gamma", "sscs"), meta, strict=True):
        for slot, (i, planes) in enumerate(entries):
            key = f"{kind}_{slot}"
            if key in z:
                out.setdefault(planes[0]["data"], {})[kind] = (z[key], planes[0], i)
    return out


def as_rows(flat, plane):
    per = plane["stride"] // 2
    rows = flat.size // per
    return flat.reshape(rows, per)[:, :plane["w"]].astype(np.float64)


def main():
    arw = Path(sys.argv[1])
    shared = load(sys.argv[2])
    style = sys.argv[3] if len(sys.argv) > 3 else "VV2"

    cal = look_calibrations(arw)[LOOK_ORDER.index(style)]
    lut = tone_curve(cal, style)
    lut_x = np.linspace(0.0, TONE_WHITE, lut.size)

    pairs = [(k, v) for k, v in shared.items() if {"gamma", "sscs"} <= set(v)]
    print(f"共用缓冲区 {len(pairs)} 个\n")

    for key, v in pairs[:6]:
        before, pb, _ = v["gamma"]
        after, pa, _ = v["sscs"]
        b = as_rows(before, pb)
        a = as_rows(after, pa)
        n = min(b.shape[0], a.shape[0])
        b, a = b[:n], a[:n]
        print(f"{key}  {pb['w']}x{n}行   曲线前 max={b.max():.0f} 中位={np.median(b):.0f}"
              f"   ITP 后 max={a.max():.0f} 中位={np.median(a):.0f}")

        # 三个平面在这里是交错的还是分开的不确定,先只拿第 0 个平面做单通道对照:
        # 曲线本身是逐通道的,单通道就能看出量程与形状
        toned = np.interp(np.clip(b, 0, TONE_WHITE), lut_x, lut) * FULL
        print(f"    我们过一遍曲线: max={toned.max():.0f} 中位={np.median(toned):.0f}"
              f"   与 ITP 后之比 中位={np.median(toned) / max(np.median(a), 1):.3f}")

    if not pairs:
        print("没有共用缓冲区,说明这一版数据里 tile 不是原地变换")
        return

    # 中位数之比在各块之间是否稳定 —— 稳定就说明 ITP 是一条固定曲线
    ratios = []
    for _, v in pairs:
        b = as_rows(v["gamma"][0], v["gamma"][1])
        a = as_rows(v["sscs"][0], v["sscs"][1])
        n = min(b.shape[0], a.shape[0])
        toned = np.interp(np.clip(b[:n], 0, TONE_WHITE), lut_x, lut) * FULL
        m = np.median(a[:n])
        if m > 1:
            ratios.append(np.median(toned) / m)
    print(f"\n各块的比值: {[round(r, 3) for r in ratios]}")


if __name__ == "__main__":
    main()
