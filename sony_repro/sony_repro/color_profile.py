"""color_profile: 从成对样本拟合并应用 Sony 的颜色/色调变换。

模型:target ≈ trilinear_lut( src @ M.T )
    - M 为 3x3(或 affine 时 3x4)色彩矩阵,捕捉线性部分;
    - 残差 3D LUT 捕捉逐像素非线性(基础/creative LUT + 色调曲线 + 色相饱和)。

纯 numpy 实现,不依赖 rawpy / scipy。
"""

from __future__ import annotations

import numpy as np

__all__ = [
    "fit_matrix",
    "ColorTransform",
    "fit",
    "delta_e_stats",
]


def fit_matrix(src: np.ndarray, tgt: np.ndarray, affine: bool = False) -> np.ndarray:
    """最小二乘拟合矩阵 M,使 src @ M.T ≈ tgt。

    affine=False -> 返回 3x3;affine=True -> 返回 3x4(最后一列为偏置)。
    """
    src = np.asarray(src, dtype=np.float64)
    tgt = np.asarray(tgt, dtype=np.float64)
    if src.ndim != 2 or src.shape[1] != 3:
        raise ValueError("src 必须是 Nx3")
    if tgt.shape != src.shape:
        raise ValueError("tgt 必须与 src 同形")

    if affine:
        a = np.concatenate([src, np.ones((src.shape[0], 1))], axis=1)  # Nx4
    else:
        a = src  # Nx3
    # 解 a @ X = tgt  ->  X: (4or3)x3;M = X.T
    x, *_ = np.linalg.lstsq(a, tgt, rcond=None)
    return x.T.copy()


def _apply_matrix(x: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """x: (...,3);matrix: 3x3 或 3x4。"""
    matrix = np.asarray(matrix, dtype=np.float64)
    if matrix.shape == (3, 3):
        return x @ matrix.T
    if matrix.shape == (3, 4):
        return x @ matrix[:, :3].T + matrix[:, 3]
    raise ValueError("matrix 必须是 3x3 或 3x4")


def _as_endpoint(x):
    """domain 端点归一化:标量 -> float,数组 -> float64 ndarray。"""
    return float(np.asarray(x).item()) if np.ndim(x) == 0 else np.asarray(x, dtype=np.float64)


def _lut_coords(pts: np.ndarray, lut_domain, s: int):
    """把输入点归一化到 LUT 索引空间,返回 (i0, i1, frac)。

    输入按 domain 归一化到 [0, S-1] 并 clamp;i0 为下界整数格(clamp 到 [0,S-2]
    以保证 i0+1 合法),i1=i0+1,frac∈[0,1]。索引轴顺序为 (R,G,B)。
    三线性采样与残差 LUT 的 splat/精修共用这套坐标分解。
    """
    lo = np.asarray(lut_domain[0], dtype=np.float64)
    hi = np.asarray(lut_domain[1], dtype=np.float64)
    coord = (pts - lo) / (hi - lo) * (s - 1)
    coord = np.clip(coord, 0.0, s - 1)
    i0 = np.floor(coord).astype(np.int64)
    i0 = np.clip(i0, 0, s - 2) if s > 1 else np.zeros_like(i0)
    i1 = np.minimum(i0 + 1, s - 1)
    frac = coord - i0
    return i0, i1, frac


def _corner_weights(i0: np.ndarray, i1: np.ndarray, frac: np.ndarray, s: int):
    """8 个立方体角点的 (flat 索引, 三线性权重),各为 (8, N)。索引轴顺序 (R,G,B)。"""
    fr, fg, fb = frac[:, 0], frac[:, 1], frac[:, 2]
    r0, g0, b0 = i0[:, 0], i0[:, 1], i0[:, 2]
    r1, g1, b1 = i1[:, 0], i1[:, 1], i1[:, 2]
    corners = [
        (r0, g0, b0, (1 - fr) * (1 - fg) * (1 - fb)),
        (r1, g0, b0, fr * (1 - fg) * (1 - fb)),
        (r0, g1, b0, (1 - fr) * fg * (1 - fb)),
        (r1, g1, b0, fr * fg * (1 - fb)),
        (r0, g0, b1, (1 - fr) * (1 - fg) * fb),
        (r1, g0, b1, fr * (1 - fg) * fb),
        (r0, g1, b1, (1 - fr) * fg * fb),
        (r1, g1, b1, fr * fg * fb),
    ]
    flat_idx = np.stack([(ri * s + gi) * s + bi for ri, gi, bi, _ in corners])  # (8,N)
    weights = np.stack([w for *_, w in corners])  # (8,N)
    return flat_idx, weights


def _trilinear_sample(lut: np.ndarray, pts: np.ndarray, lut_domain) -> np.ndarray:
    """在 3D LUT 上做三线性查表。

    lut: (S,S,S,3)。pts: (N,3)。lut_domain: (lo, hi) 定义输入定义域。
    8 角点按三线性权重加权求和,等价于逐维线性插值。
    """
    s = lut.shape[0]
    i0, i1, frac = _lut_coords(pts, lut_domain, s)
    flat_idx, weights = _corner_weights(i0, i1, frac, s)  # (8,N)
    flat_lut = lut.reshape(s * s * s, 3)
    return (weights[:, :, None] * flat_lut[flat_idx]).sum(axis=0)


def _identity_grid(s: int, lut_domain) -> np.ndarray:
    """返回 (S,S,S,3) 的恒等 LUT:每个节点的值等于其输入坐标。"""
    lo, hi = lut_domain
    lo = np.asarray(lo, dtype=np.float64)
    hi = np.asarray(hi, dtype=np.float64)
    axis = np.linspace(0.0, 1.0, s)
    rr, gg, bb = np.meshgrid(axis, axis, axis, indexing="ij")
    grid = np.stack([rr, gg, bb], axis=-1)  # (S,S,S,3),每维在 [0,1]
    return lo + grid * (hi - lo)


class ColorTransform:
    """持有色彩矩阵与可选残差 3D LUT,并可应用/保存/导出。"""

    def __init__(self, matrix: np.ndarray, lut3d: np.ndarray | None = None,
                 lut_domain=(0.0, 1.0)):
        self.matrix = np.asarray(matrix, dtype=np.float64)
        if self.matrix.shape not in ((3, 3), (3, 4)):
            raise ValueError("matrix 必须是 3x3 或 3x4")
        self.lut3d = None if lut3d is None else np.asarray(lut3d, dtype=np.float64)
        if self.lut3d is not None:
            s = self.lut3d.shape[0]
            if self.lut3d.shape != (s, s, s, 3):
                raise ValueError("lut3d 必须是 (S,S,S,3)")
        self.lut_domain = (_as_endpoint(lut_domain[0]), _as_endpoint(lut_domain[1]))

    # -- 应用 --
    def apply(self, img: np.ndarray) -> np.ndarray:
        """先过矩阵,再(若有)过三线性 3D LUT。支持任意前导维度。"""
        img = np.asarray(img, dtype=np.float64)
        if img.shape[-1] != 3:
            raise ValueError("最后一维必须是 3")
        lead = img.shape[:-1]
        flat = img.reshape(-1, 3)
        out = _apply_matrix(flat, self.matrix)
        if self.lut3d is not None:
            out = _trilinear_sample(self.lut3d, out, self.lut_domain)
        return out.reshape(*lead, 3)

    # -- 序列化 --
    def save(self, path) -> None:
        lo, hi = self.lut_domain
        kw = {
            "matrix": self.matrix,
            "lut_domain_lo": np.asarray(lo, dtype=np.float64),
            "lut_domain_hi": np.asarray(hi, dtype=np.float64),
            "has_lut": np.asarray(self.lut3d is not None),
        }
        if self.lut3d is not None:
            kw["lut3d"] = self.lut3d
        np.savez(path, **kw)

    @classmethod
    def load(cls, path) -> "ColorTransform":
        data = np.load(path, allow_pickle=False)
        matrix = data["matrix"]
        has_lut = bool(data["has_lut"])
        lut3d = data["lut3d"] if has_lut else None
        lo = _as_endpoint(data["lut_domain_lo"])
        hi = _as_endpoint(data["lut_domain_hi"])
        return cls(matrix, lut3d, (lo, hi))

    # -- 导出 .cube --
    def bake_lut(self, size: int | None = None) -> np.ndarray:
        """把矩阵 + lut3d 一起 bake 成一个 (size,size,size,3) 的 3D LUT。

        节点坐标取自 [0,1] 网格,值 = apply(节点坐标)。这样单独用该 LUT 即等价于本变换。
        """
        if size is None:
            size = self.lut3d.shape[0] if self.lut3d is not None else 17
        return self.apply(_identity_grid(size, (0.0, 1.0)))

    def to_cube(self, path, size: int | None = None, title: str = "sony_repro") -> None:
        """导出标准 .cube 3D LUT。矩阵被 bake 进去,cube 单独用与 apply 等价。

        .cube 规范:R 变化最快(最内层循环),故写出顺序对应索引 (b,g,r) 的三重循环、r 在最内。
        """
        baked = self.bake_lut(size)  # (S,S,S,3),索引 (r,g,b)
        s = baked.shape[0]
        lines = [f"TITLE \"{title}\"", f"LUT_3D_SIZE {s}", "DOMAIN_MIN 0.0 0.0 0.0",
                 "DOMAIN_MAX 1.0 1.0 1.0", ""]
        # R 最快:遍历顺序 b(外) g(中) r(内)
        for b in range(s):
            for g in range(s):
                for r in range(s):
                    v = baked[r, g, b]
                    lines.append(f"{v[0]:.6f} {v[1]:.6f} {v[2]:.6f}")
        with open(path, "w", encoding="ascii") as f:
            f.write("\n".join(lines) + "\n")


def _build_residual_lut(pred: np.ndarray, tgt: np.ndarray, lut_size: int,
                        lut_domain, refine_iters: int = 40) -> np.ndarray:
    """用 (pred, tgt) 样本构建 lut_size^3 的 3D LUT。

    两步:
    1) splat 初始化:每个样本按三线性权重 splat 到其 cube 的 8 个节点(三线性插值的伴随),
       节点值 = 加权平均;采样不足的节点回退到恒等,避免外插爆掉。
    2) 最小二乘精修:以 splat 结果为初值,做若干次 Jacobi 梯度迭代,直接最小化
       Σ_i ||trilinear(LUT, pred_i) - tgt_i||^2,让 apply 后残差进一步显著下降。
       只更新被样本覆盖到的节点,未覆盖节点保持恒等。
    """
    s = lut_size
    n_nodes = s * s * s

    # 每样本落入某个 cube 的 8 个角点(flat 索引 + 三线性权重),各为 (8, N)
    i0, i1, frac = _lut_coords(pred, lut_domain, s)
    flat_idx, weights = _corner_weights(i0, i1, frac, s)
    all_idx = flat_idx.reshape(-1)   # (8N,)
    all_w = weights.reshape(-1)      # (8N,)

    def scatter3(vals8: np.ndarray) -> np.ndarray:
        """把 (8N,3) 的值按 all_idx 累加回节点,返回 (n_nodes, 3)。"""
        return np.stack(
            [np.bincount(all_idx, weights=all_w * vals8[:, k], minlength=n_nodes) for k in range(3)],
            axis=1,
        )

    # splat 初始化:节点值 = 三线性权重加权平均;采样不足的节点回退到恒等。
    wsum = np.bincount(all_idx, weights=all_w, minlength=n_nodes)
    acc = scatter3(np.tile(tgt, (8, 1)))
    lut = _identity_grid(s, lut_domain).reshape(n_nodes, 3)
    covered = wsum > 1e-8
    lut[covered] = acc[covered] / wsum[covered][:, None]

    # 精修:预条件用节点权重质量 wsum_j = Σ_i w_ij。
    # A^TA 的行和恰等于 wsum(因为每样本 8 权重和为 1),故以 wsum 作预条件的
    # 阻尼 Richardson 迭代谱半径 <2、lr<=1 时稳定收敛(用 sum w^2 会因非对角占优而发散)。
    if refine_iters > 0:
        inv_pre = np.zeros(n_nodes, dtype=np.float64)
        inv_pre[covered] = 1.0 / wsum[covered]
        lr = 0.9
        for _ in range(refine_iters):
            # 当前每样本预测 = Σ_c w_ic * lut[idx_ic]
            pred_i = (weights[:, :, None] * lut[flat_idx]).sum(axis=0)  # (N,3)
            resid = tgt - pred_i
            grad = scatter3(np.tile(resid, (8, 1)))
            lut[covered] += lr * inv_pre[covered][:, None] * grad[covered]

    return lut.reshape(s, s, s, 3)


def fit(src: np.ndarray, tgt: np.ndarray, lut_size: int = 17,
        fit_matrix_first: bool = True) -> ColorTransform:
    """拟合 ColorTransform = 矩阵 + 残差 3D LUT。"""
    src = np.asarray(src, dtype=np.float64)
    tgt = np.asarray(tgt, dtype=np.float64)

    if fit_matrix_first:
        m = fit_matrix(src, tgt, affine=False)
    else:
        m = np.eye(3)
    pred = _apply_matrix(src, m)

    lut = _build_residual_lut(pred, tgt, lut_size, (0.0, 1.0))
    return ColorTransform(m, lut, (0.0, 1.0))


def delta_e_stats(a: np.ndarray, b: np.ndarray) -> dict:
    """逐像素欧氏误差统计(在 RGB 上直接算)。"""
    a = np.asarray(a, dtype=np.float64).reshape(-1, 3)
    b = np.asarray(b, dtype=np.float64).reshape(-1, 3)
    d = np.linalg.norm(a - b, axis=1)
    p50, p95 = np.percentile(d, [50, 95])
    return {
        "mean": float(np.mean(d)),
        "p50": float(p50),
        "p95": float(p95),
        "max": float(np.max(d)),
    }
