r"""llr **真实整幅路径**(rawpy 马赛克 → denoise_raw_inplace → sony.itp.demosaic)对引擎导出 tile 逐位核对。

    cd apps/worker && uv run python ../../sony_repro/tools/highiso_frame_check.py <ARW> q50c

tile 级的「逐位」用的是引擎自己的入口平面;这里改用 llr 自己的输入,分三段:
  1. 引擎 RawNR 入口 tile 在 rawpy 马赛克里能否原样找到(位置、逐位);
  2. llr 整幅 RawNR(prepare_linear 同一调用)后,同位置是否等于引擎 RawNR 出口;
  3. llr 整幅 ITP 后,同位置是否等于引擎 ITP 出口(RGB 三平面,14 位)。
哪一段先不相等,暗部色偏就落在哪一段。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np  # noqa: E402
import rawpy  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from llr_worker.denoise import denoise_raw_inplace, get_denoiser  # noqa: E402
from llr_worker.sony import itp  # noqa: E402
from llr_worker.sony.rawnr import detail_restore, noise_model  # noqa: E402


def locate(tile, frame, pos, halo_guess, span=48):
    """在 frame 里找 tile 的位置(整像素,保持 Bayer 相位:只搜偶数位移)。返回 (y0, x0, 逐位率)。"""
    th, tw = tile.shape
    cy, cx = th // 2, tw // 2
    patch = tile[cy - 64:cy + 64, cx - 64:cx + 64].astype(np.int32)
    gy, gx = pos[1] - halo_guess + cy - 64, pos[0] - halo_guess + cx - 64
    best = None
    for dy in range(-span, span + 1, 2):
        for dx in range(-span, span + 1, 2):
            y, x = gy + dy, gx + dx
            if y < 0 or x < 0 or y + 128 > frame.shape[0] or x + 128 > frame.shape[1]:
                continue
            eq = float(np.mean(frame[y:y + 128, x:x + 128].astype(np.int32) == patch))
            if best is None or eq > best[0]:
                best = (eq, dy, dx)
    eq, dy, dx = best
    return pos[1] - halo_guess + dy, pos[0] - halo_guess + dx, eq


def main():
    arw, suf = sys.argv[1], sys.argv[2]
    from highiso_probe_analyze import probe_path
    z = np.load(probe_path(suf))
    raw = rawpy.imread(arw)
    vis = raw.raw_image_visible
    print(f"rawpy visible {vis.shape}, black {raw.black_level_per_channel}, white {raw.white_level}, sizes top/left {raw.sizes.top_margin}/{raw.sizes.left_margin}")
    before = vis.copy()
    # 1. 入口
    locs = {}
    for i in range(3):
        if f"t{i}_in" not in z:
            continue
        ti = z[f"t{i}_in"][..., 0]
        m = z[f"t{i}_meta"]
        pos = [int(v) for v in m[18:22]]
        halo = (ti.shape[1] - (pos[2] - pos[0])) // 2
        y0, x0, eq = locate(ti, before, pos, halo)
        th, tw = ti.shape
        sub = before[y0:y0 + th, x0:x0 + tw]
        full_eq = float(np.mean(sub == ti)) if sub.shape == ti.shape else float("nan")
        locs[i] = (y0, x0)
        print(f"1. RawNR 入口 t{i} pos {pos} halo {halo}: 在 rawpy 马赛克 ({y0},{x0}) 处,中央块逐位 {eq * 100:.2f}%,整块逐位 {full_eq * 100:.3f}%")
    # 2. llr 整幅 RawNR(与 prepare_linear 同一调用)
    curve, restore = noise_model(arw), detail_restore(arw)
    stats = denoise_raw_inplace(raw, get_denoiser("sony"), noise=curve,
                                detail=(restore.fraction, restore.limit_in_thresholds(curve)),
                                chroma_scale=1.0, strength=1.0)
    print(f"   llr denoise_raw_inplace: {stats.model} {stats.width}x{stats.height} black {stats.black_levels} white {stats.white_level}")
    after = raw.raw_image_visible
    for i, (y0, x0) in locs.items():
        to = z[f"t{i}_out"][..., 0]
        th, tw = to.shape
        sub = after[y0:y0 + th, x0:x0 + tw].astype(np.int64)
        t = 32
        d = np.abs(sub[t:-t, t:-t] - to[t:-t, t:-t].astype(np.int64))
        print(f"2. RawNR 出口 t{i}: llr 整幅 vs 引擎 逐位 {np.mean(d == 0) * 100:.4f}%  max {d.max()}  均值差 {(sub[t:-t, t:-t] - to[t:-t, t:-t]).mean():+.4f}")
    # 3. ITP 整幅
    sizes = raw.sizes
    top, left = int(sizes.top_margin), int(sizes.left_margin)
    h, w = int(sizes.height), int(sizes.width)
    mosaic = raw.raw_image[top:top + h, left:left + w]
    cwb = [float(v) for v in raw.camera_whitebalance]
    g2 = cwb[3] if cwb[3] > 0 else cwb[1]
    black = float(np.mean([float(v) for v in raw.black_level_per_channel]))
    rgb = itp.demosaic(np.ascontiguousarray(mosaic), (cwb[0], cwb[1], g2, cwb[2]), black)
    rgb14 = np.clip(np.rint(rgb * itp.ENGINE_WHITE), 0, 65535).astype(np.int64)
    for i in range(8):
        if f"SIMDITP_t{i}_out" not in z:
            continue
        ti = z[f"SIMDITP_t{i}_in"][..., 0]
        to = z[f"SIMDITP_t{i}_out"]
        m = z[f"SIMDITP_t{i}_meta"]
        pos = [int(v) for v in m[18:22]]
        rect = [int(v) for v in m[12:16]]
        halo = (ti.shape[1] - (pos[2] - pos[0])) // 2
        y0, x0, eq = locate(ti, after, pos, halo)
        x0r, y0r, x1r, y1r = rect
        t = 12
        sub = rgb14[y0 + y0r + t:y0 + y1r - t, x0 + x0r + t:x0 + x1r - t]
        row = f"3. ITP t{i} pos {pos}: 入口 tile 对 llr 降噪后马赛克 逐位 {eq * 100:.2f}% @({y0},{x0});"
        for c in range(3):
            want = to[y0r + t:y1r - t, x0r + t:x1r - t, c].astype(np.int64)
            d = np.abs(sub[..., c] - want)
            row += f"  out{c} 逐位 {np.mean(d == 0) * 100:.3f}% max {d.max()} 均值差 {(sub[..., c] - want).mean():+.3f}"
        print(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
