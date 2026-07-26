"""YCC 段的解码与不变量。参数取自实机抓到的 VV2 一组,以及 ARW 里各外观的值。"""
import numpy as np
import pytest

from sony_repro.ycc import (
    FULL, LUMA_WEIGHTS, apply_ycc, chroma_params, rgb_to_ycc, unpack_chroma, ycc_to_rgb,
)

# DSC03015 的 VV2,与实机 hook 到的插值结果逐位相同
VV2 = np.array([-267, -226, -235, -172, 1110, 654, 952, 1126])
BW = np.zeros(8, dtype=np.int64)


def test_the_gain_and_cross_fields_decode_the_way_the_engine_reads_them():
    cross, gain = unpack_chroma(VV2)
    # -267 >> 2 = -67,再 /256
    assert cross[0] == pytest.approx(-67 / 256)
    # 1110 >> 3 = 138,再 /128
    assert gain[0] == pytest.approx(138 / 128)


def test_a_neutral_pixel_survives_untouched():
    """每个外观都得让灰保持灰 —— 色差为零,增益乘什么都还是零。"""
    grey = np.full((4, 1, 3), 5000.0)
    out = apply_ycc(grey, VV2)
    assert np.abs(out - 5000).max() <= 1


def test_black_and_white_falls_out_of_the_parameters_themselves():
    """BW 的八个参数全零,增益随之为零,Cb 与 Cr 只能是零。"""
    rgb = np.array([[[12000.0, 4000.0, 800.0]]])
    _, cb, cr = rgb_to_ycc(rgb, BW)
    assert cb.max() == 0 and cr.max() == 0
    out = apply_ycc(rgb, BW)
    assert out[..., 0] == out[..., 1] == out[..., 2]


def test_the_luma_weights_sum_to_one():
    assert sum(LUMA_WEIGHTS) == 8192


def test_the_transform_is_not_its_own_inverse():
    """这一对变换不互逆 —— 差额正是各外观的饱和度风格,不是实现误差。"""
    rgb = np.array([[[9000.0, 5000.0, 3000.0]]])
    out = apply_ycc(rgb, VV2)
    assert np.abs(out - rgb).max() > 100


def test_the_gains_push_saturation_past_the_identity_point():
    """恒等所需的增益是 0.75/1.402;VV2 给的比它大,所以是在加饱和。"""
    _, gain = unpack_chroma(VV2)
    assert gain[1] > 0.75 / 1.402


def test_ycc_to_rgb_stays_inside_the_14_bit_range():
    y = np.array([16000.0, 0.0])
    cb = np.array([8000.0, -8000.0])
    cr = np.array([8000.0, -8000.0])
    out = ycc_to_rgb(y, cb, cr)
    assert out.min() >= 0 and out.max() <= FULL


def test_illuminant_weights_interpolate_off_the_base():
    ifd = {0x7842: VV2, 0x7843: np.zeros(8, dtype=np.int64),
           0x7844: np.ones(8, dtype=np.int64),
           0x7845: np.zeros(8, dtype=np.int64), 0x7846: np.zeros(8, dtype=np.int64)}
    # 权重 1024 就是 1.0,所以第二组的每项 1 原样加了上去
    assert list(chroma_params(ifd, (0, 1024, 0, 0))) == list(VV2 + 1)
    # 半权重,增量被砍掉一半 —— 1 >> 1 归零,基准原样留下
    assert list(chroma_params(ifd, (0, 512, 0, 0))) == list(VV2)
    # 默认权重全压在第一组,而第一组是零
    assert list(chroma_params(ifd)) == list(VV2)
