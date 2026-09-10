r"""从导出整幅的 RGB2YCC 入口/出口最小二乘反解八个色度参数,与 SR2 外观表里的比。

    bash run_py.sh ycc_fit.py <frames.npz> [ARW] [style]
"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np  # noqa: E402


def main():
    z = np.load(sys.argv[1])
    a = z["ZcTaskRGB2YCC_in"][::3, ::3].astype(np.float64)
    b = z["ZcTaskRGB2YCC_out"][::3, ::3].astype(np.float64)
    r, g, bl = a[..., 0], a[..., 1], a[..., 2]
    cr = b[..., 1] - 32768.0
    cb = b[..., 2] - 32768.0
    u = r - g
    v = bl - g
    sel = (np.abs(u) > 40) & (np.abs(v) > 40) & (np.abs(cr) < 8000) & (np.abs(cb) < 8000)
    print("引擎 8 参的用法:v2 = cross[u>=0?1:3]*u + v; u2 = cross[v>=0?0:2]*v + u; cr = gain[u2>=0?1:3]*u2; cb = gain[v2>=0?0:2]*v2")
    print("(cross = (short>>2)/256, gain = ((short>>3)&0xff)/128;单位与 14 位平面一致时 cr,cb 还要乘 16383/... 这里只看比例)")
    for qu in (1, -1):
        for qv in (1, -1):
            m = sel & (np.sign(u) == qu) & (np.sign(v) == qv)
            if m.sum() < 500:
                continue
            A = np.vstack([u[m], v[m]]).T
            kc = np.linalg.lstsq(A, cr[m], rcond=None)[0]
            kb = np.linalg.lstsq(A, cb[m], rcond=None)[0]
            rc = np.median(np.abs(A @ kc - cr[m]))
            rb = np.median(np.abs(A @ kb - cb[m]))
            su = "+" if qu > 0 else "-"
            sv = "+" if qv > 0 else "-"
            print(f"u{su} v{sv} n={m.sum():7d}   cr = {kc[0]:+.4f}u {kc[1]:+.4f}v (res {rc:.1f})   cb = {kb[0]:+.4f}u {kb[1]:+.4f}v (res {rb:.1f})")
    st = [-51, -211, -259, -204, 986, 824, 1010, 1126]
    cross = [(x >> 2) / 256 for x in st[:4]]
    gain = [((x >> 3) & 0xff) / 128 for x in st[4:]]
    print("ST 表换算 cross", [round(c, 4) for c in cross], "gain", [round(g_, 4) for g_ in gain])
    print("  预测 u+v+: cr = gain1*(u + cross0*v) =", round(gain[1], 4), "u", round(gain[1] * cross[0], 4), "v ; cb = gain0*(v + cross1*u) =", round(gain[0] * cross[1], 4), "u", round(gain[0], 4), "v")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
