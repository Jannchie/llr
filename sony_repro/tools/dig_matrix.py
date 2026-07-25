"""离线在 dump 的参数块里搜索真实的色彩矩阵系数。

1) 比对两次抓到的 tone LUT 是否一致
2) 打印已拟合 profile 的矩阵作为"指纹"
3) 扫描 param_block.bin 中所有 float32 / int16 的 9 元组,找像色彩矩阵的候选

用法: python tools/dig_matrix.py
"""
import numpy as np

BLK = "data/param_block.bin"


def compare_tone():
    a = np.load("data/sony_tone_lut_live.npz")
    b = np.load("data/tone_spawn.npz")
    ka, kb = list(a.keys())[0], list(b.keys())[0]
    ta = a[ka].astype(np.int64)
    tb = b[kb].astype(np.int64)
    print(f"[tone] live key={ka} shape={ta.shape} max={ta.max()}")
    print(f"[tone] spawn key={kb} shape={tb.shape} max={tb.max()}")
    if ta.shape != tb.shape:
        print("[tone] 形状不同,跳过逐点比对")
        return
    d = tb - ta
    nz = np.nonzero(d)[0]
    print(f"[tone] identical={nz.size == 0} ndiff={nz.size} maxabs={int(np.abs(d).max())}")
    if nz.size:
        i = nz[:8]
        print(f"[tone] first idx={i.tolist()}")
        print(f"[tone]   live ={ta[i].tolist()}")
        print(f"[tone]   spawn={tb[i].tolist()}")


def fingerprint():
    p = np.load("data/sony_profile.npz")
    print("[profile] keys:", list(p.keys()))
    for k in p.keys():
        v = p[k]
        if v.size == 9:
            print(f"[profile] {k} =\n{v.reshape(3, 3)}")
        else:
            print(f"[profile] {k} shape={v.shape} dtype={v.dtype}")
    return p


def plausible(m):
    """m: (...,3,3) -> bool mask,像 RGB->RGB 色彩矩阵的判据"""
    finite = np.isfinite(m).all(axis=(-2, -1))
    rng = (np.abs(m) < 4.0).all(axis=(-2, -1))
    diag = np.array([m[..., 0, 0], m[..., 1, 1], m[..., 2, 2]])
    diagpos = (diag > 0.3).all(axis=0) & (diag < 3.0).all(axis=0)
    rowsum = m.sum(axis=-1)
    neutral = (np.abs(rowsum - 1.0) < 0.12).all(axis=-1)
    nontrivial = (np.abs(m - np.eye(3)).max(axis=(-2, -1)) > 0.02)
    return finite & rng & diagpos & neutral & nontrivial


def scan_f32(buf):
    print("\n=== float32 9-元组扫描 ===")
    hits = []
    for align in (0, 1, 2, 3):
        n = (len(buf) - align) // 4
        f = np.frombuffer(buf, dtype="<f4", count=n, offset=align)
        if f.size < 9:
            continue
        w = np.lib.stride_tricks.sliding_window_view(f, 9).reshape(-1, 3, 3)
        mask = plausible(w)
        for idx in np.nonzero(mask)[0]:
            hits.append((align + idx * 4, w[idx].copy()))
    print(f"候选数: {len(hits)}")
    for off, m in hits[:40]:
        print(f"  @0x{off:06x} rowsum={m.sum(axis=1).round(4).tolist()}\n{m.round(5)}")
    return hits


def scan_i16(buf, scales=(1024.0, 4096.0, 8192.0, 16384.0, 10000.0)):
    print("\n=== int16 9-元组扫描 ===")
    hits = []
    for align in (0, 1):
        n = (len(buf) - align) // 2
        v = np.frombuffer(buf, dtype="<i2", count=n, offset=align).astype(np.float64)
        if v.size < 9:
            continue
        w = np.lib.stride_tricks.sliding_window_view(v, 9).reshape(-1, 3, 3)
        for s in scales:
            m = w / s
            mask = plausible(m)
            for idx in np.nonzero(mask)[0]:
                hits.append((align + idx * 2, s, m[idx].copy()))
    print(f"候选数: {len(hits)}")
    seen = set()
    shown = 0
    for off, s, m in hits:
        key = (off // 2)
        if key in seen:
            continue
        seen.add(key)
        print(f"  @0x{off:06x} /{s:.0f} rowsum={m.sum(axis=1).round(4).tolist()}\n{m.round(5)}")
        shown += 1
        if shown >= 40:
            break
    return hits


def main():
    compare_tone()
    print()
    fingerprint()
    buf = open(BLK, "rb").read()
    print(f"\n[block] {len(buf)} bytes")
    scan_f32(buf)
    scan_i16(buf)


if __name__ == "__main__":
    main()
