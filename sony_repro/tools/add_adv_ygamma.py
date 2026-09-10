r"""把高级色彩复制档下 dump 的两张 YGamma LUT 加进 worker 的 ygamma_luts.npz(一次性工具,已跑过)。"""
import os

import numpy as np

P = "/home/jannchie/llr/apps/worker/src/llr_worker/sony/data/ygamma_luts.npz"
T = "/home/jannchie/llr/sony_repro/tools/"
z = dict(np.load(P))
adv_s = np.load(T + "cs_export_DSC03036-adv-cs.npz")["lut_ygam"].astype(np.uint16)
adv_f = np.load(T + "cs_export_cs_DSC02919-adv-cs.npz")["lut_ygam"].astype(np.uint16)
z["adv_c4096_d14848"] = adv_s
z["adv_c1024_d16384"] = adv_f
z["source"] = np.concatenate([z["source"], np.array([
    "adv_*: the same calib+0x318fc dumped with Edit 色彩复制 set to 高级 (advanced colour reproduction), 2026-09-10",
    "adv_c4096_d14848: Standard family, DSC03036 ILCE-7CM2 (6 export tiles bit-exact with contrast 17280/16384)",
    "adv_c1024_d16384: FL family, cs_DSC02919 ILCE-7CM2 (6 export tiles bit-exact with contrast 17280/16384; FL own 0x780e is 16384, 高级 uses 17280 regardless)",
])])
np.savez_compressed(P, **z)
print("saved", os.path.getsize(P), sorted(z))
