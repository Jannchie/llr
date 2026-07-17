"""DefaultCrop rect math, including the camera-flip transforms."""

from types import SimpleNamespace

import numpy as np
import pytest

from llr_worker.cli import apply_camera_crop, camera_crop_rect

VW, VH = 20, 12  # visible sensor frame in sensor orientation


def make_raw(flip: int = 0) -> SimpleNamespace:
    return SimpleNamespace(sizes=SimpleNamespace(width=VW, height=VH, flip=flip))


def exif(x: int, y: int, w: int, h: int) -> dict:
    return {"DefaultCropOrigin": f"{x} {y}", "DefaultCropSize": [w, h]}


def test_missing_or_degenerate_tags_return_none() -> None:
    assert camera_crop_rect(make_raw(), {}) is None
    assert camera_crop_rect(make_raw(), exif(0, 0, VW, VH)) is None  # full frame
    assert camera_crop_rect(make_raw(), exif(2, 2, 0, 5)) is None  # zero size
    assert camera_crop_rect(make_raw(), exif(18, 0, 4, 4)) is None  # out of bounds
    assert camera_crop_rect(make_raw(), {"DefaultCropOrigin": "bad", "DefaultCropSize": "1 2"}) is None


def test_unflipped_rect_passes_through() -> None:
    assert camera_crop_rect(make_raw(flip=0), exif(3, 2, 10, 8)) == ((3, 2, 10, 8), (VW, VH))


@pytest.mark.parametrize(
    ("flip", "k"),
    [
        (3, 2),  # 180 degrees
        (5, 1),  # 90 degrees counter-clockwise (np.rot90 direction)
        (6, -1),  # 90 degrees clockwise
    ],
)
def test_flipped_rect_selects_the_same_pixels(flip: int, k: int) -> None:
    x, y, w, h = 3, 2, 10, 7
    result = camera_crop_rect(make_raw(flip=flip), exif(x, y, w, h))
    assert result is not None
    (rx, ry, rw, rh), (fw, fh) = result

    # Mark the crop in the sensor frame, rotate the frame like postprocess does,
    # and check the transformed rect is exactly the marks' bounding box.
    sensor = np.zeros((VH, VW), dtype=np.uint8)
    sensor[y : y + h, x : x + w] = 1
    rotated = np.rot90(sensor, k)
    assert (fh, fw) == rotated.shape
    rows, cols = np.nonzero(rotated)
    assert (rx, ry) == (cols.min(), rows.min())
    assert (rw, rh) == (cols.max() - cols.min() + 1, rows.max() - rows.min() + 1)


def test_apply_camera_crop_scales_for_half_size_decodes() -> None:
    full = np.arange(VH * VW * 3, dtype=np.float32).reshape(VH, VW, 3)
    crop = ((4, 2, 10, 8), (VW, VH))
    assert apply_camera_crop(full, crop).shape == (8, 10, 3)

    half = full[::2, ::2]  # decode at half resolution
    cropped = apply_camera_crop(half, crop)
    assert cropped.shape == (4, 5, 3)
    assert np.array_equal(cropped, half[1:5, 2:7])


def test_apply_camera_crop_noop_paths_return_input() -> None:
    arr = np.zeros((VH, VW, 3), dtype=np.float32)
    assert apply_camera_crop(arr, None) is arr
    assert apply_camera_crop(arr, ((0, 0, VW, VH), (VW, VH))) is arr
