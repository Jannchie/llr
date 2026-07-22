"""Decoding rendered images (JPEG/PNG/TIFF) into linear ProPhoto D50."""

import numpy as np
import pytest
from PIL import Image, ImageCms

from llr_worker.dcp import srgb_decode_float
from llr_worker.imported import (
    SRGB_TO_XYZ_D50,
    _match_transfer,
    decode_image_linear,
)


def write_png(path, rgb, size=(8, 8), icc=None):
    array = np.zeros((size[1], size[0], 3), dtype=np.uint8)
    array[:] = rgb
    kwargs = {"icc_profile": icc} if icc else {}
    Image.fromarray(array).save(path, **kwargs)
    return path


def srgb_profile_bytes():
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def test_untagged_image_is_treated_as_srgb(tmp_path):
    linear, info = decode_image_linear(write_png(tmp_path / "grey.png", (128, 128, 128)))
    assert info["kind"] == "rendered-image"
    assert info["workingSpace"] == "linear-prophoto-d50"
    # sRGB 128/255 decodes to ~0.2159 linear; ProPhoto is grey-preserving, so a
    # neutral input must stay exactly neutral after the primaries rotation.
    assert linear[0, 0] == pytest.approx([0.2159, 0.2159, 0.2159], abs=2e-3)


def test_neutral_stays_neutral_across_the_tonal_range(tmp_path):
    for value in (0, 32, 128, 200, 255):
        linear, _ = decode_image_linear(write_png(tmp_path / f"g{value}.png", (value,) * 3))
        r, g, b = linear[0, 0]
        assert r == pytest.approx(g, abs=1e-5)
        assert g == pytest.approx(b, abs=1e-5)


def test_srgb_primaries_land_on_known_prophoto_values(tmp_path):
    linear, _ = decode_image_linear(write_png(tmp_path / "red.png", (255, 0, 0)))
    assert linear[0, 0] == pytest.approx([0.5295, 0.0985, 0.0168], abs=2e-3)


def test_white_maps_to_one(tmp_path):
    linear, _ = decode_image_linear(write_png(tmp_path / "white.png", (255, 255, 255)))
    assert linear[0, 0] == pytest.approx([1.0, 1.0, 1.0], abs=2e-3)


def test_embedded_srgb_icc_matches_the_untagged_path(tmp_path):
    plain, _ = decode_image_linear(write_png(tmp_path / "plain.png", (200, 90, 40)))
    tagged, info = decode_image_linear(
        write_png(tmp_path / "tagged.png", (200, 90, 40), icc=srgb_profile_bytes())
    )
    # The ICC colorants are read numerically rather than matched by name, so they
    # must reproduce the built-in sRGB matrix rather than merely resemble it.
    assert info["sourceProfile"] != "sRGB (untagged)"
    assert tagged[0, 0] == pytest.approx(plain[0, 0], abs=1e-3)


def test_unreadable_icc_falls_back_to_srgb(tmp_path):
    linear, info = decode_image_linear(
        write_png(tmp_path / "bad.png", (128, 128, 128), icc=b"not an icc profile")
    )
    assert "sRGB" in info["sourceProfile"]
    assert linear[0, 0] == pytest.approx([0.2159, 0.2159, 0.2159], abs=2e-3)


def test_grayscale_and_alpha_inputs_become_three_opaque_channels(tmp_path):
    gray = tmp_path / "gray.png"
    Image.fromarray(np.full((4, 4), 128, dtype=np.uint8), mode="L").save(gray)
    assert decode_image_linear(gray)[0].shape == (4, 4, 3)

    rgba = tmp_path / "rgba.png"
    array = np.zeros((4, 4, 4), dtype=np.uint8)
    array[:] = [200, 90, 40, 128]
    Image.fromarray(array, mode="RGBA").save(rgba)
    linear, _ = decode_image_linear(rgba)
    assert linear.shape == (4, 4, 3)
    # Alpha is dropped, not premultiplied: the colour must match the opaque file.
    opaque, _ = decode_image_linear(write_png(tmp_path / "opaque.png", (200, 90, 40)))
    assert linear[0, 0] == pytest.approx(opaque[0, 0], abs=1e-4)


def test_exif_orientation_is_applied(tmp_path):
    array = np.zeros((4, 8, 3), dtype=np.uint8)  # 8 wide, 4 tall
    array[:, :4] = [255, 0, 0]
    path = tmp_path / "rot.jpg"
    exif = Image.Exif()
    exif[274] = 6  # rotate 90° CW
    Image.fromarray(array).save(path, exif=exif)
    linear, _ = decode_image_linear(path)
    assert linear.shape[:2] == (8, 4)


def test_output_is_contiguous_float32(tmp_path):
    linear, _ = decode_image_linear(write_png(tmp_path / "c.png", (10, 20, 30)))
    assert linear.dtype == np.float32
    assert linear.flags["C_CONTIGUOUS"]


def test_no_negative_values_after_the_gamut_rotation(tmp_path):
    # Saturated sRGB primaries are inside ProPhoto, but rounding can push a
    # channel just below zero; the renderer treats negatives as garbage.
    for rgb in [(255, 0, 0), (0, 255, 0), (0, 0, 255), (0, 255, 255), (255, 0, 255)]:
        linear, _ = decode_image_linear(write_png(tmp_path / "s.png", rgb))
        assert (linear >= 0).all()


@pytest.mark.parametrize(
    "description,expected",
    [
        ("sRGB IEC61966-2.1", "srgb"),
        ("Display P3", "display p3"),
        ("Adobe RGB (1998)", "adobe rgb"),
        ("ProPhoto RGB", "prophoto"),
        ("Rec. 2020", "rec. 2020"),
        ("Some Vendor Custom Space", "srgb (assumed)"),
    ],
)
def test_transfer_function_matching(description, expected):
    assert _match_transfer(description)[0] == expected


def test_srgb_transfer_is_the_piecewise_curve_not_a_plain_power():
    # The toe is where a naive 2.2 power visibly diverges; guarding it keeps a
    # future "simplification" from crushing shadows.
    toe = np.array([0.02], dtype=np.float32)
    assert srgb_decode_float(toe)[0] == pytest.approx(0.02 / 12.92, abs=1e-6)
    assert srgb_decode_float(np.array([1.0]))[0] == pytest.approx(1.0, abs=1e-6)


def test_srgb_matrix_rows_sum_to_the_d50_white_point():
    assert SRGB_TO_XYZ_D50.sum(axis=1) == pytest.approx([0.9642, 1.0, 0.8249], abs=1e-3)
