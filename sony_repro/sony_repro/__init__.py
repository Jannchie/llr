"""sony_repro — 从 Sony Imaging Edge 逆向出的、可复刻的三块处理。

- tone_lut:      色调滑块 = 1D LUT + 线性插值(MainGamma 语义,可 bit-exact)
- linear_matrix: 原厂线性色彩矩阵(LinearMatrix16,标定表已逆向,bit-exact)
- sr2:           从 ARW 的加密 SR2SubIFD 里读出原厂标定数据(无需跑 Edit.exe)
- color_profile: 颜色/色调 profile = 矩阵 + 残差 3D LUT 拟合(可 bit-exact)
- dro:           动态范围优化 = AreaComp 空间局部色调(结构性重建,近似)

每个模块互相独立,纯 numpy,可单独测试。
"""

__all__ = ["tone_lut", "linear_matrix", "sr2", "color_profile", "dro"]
