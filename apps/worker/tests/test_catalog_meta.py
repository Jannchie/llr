"""catalog_meta: exiftool record → the catalog's per-photo fields."""

from llr_worker.catalog_meta import catalog_meta_from_exif, parse_exif_datetime


def test_exif_datetime_becomes_iso_with_the_camera_zone():
    assert parse_exif_datetime("2024:05:01 12:34:56", "+09:00") == "2024-05-01T12:34:56+09:00"


def test_naive_stamp_stays_naive():
    assert parse_exif_datetime("2024:05:01 12:34:56") == "2024-05-01T12:34:56"
    assert parse_exif_datetime("2024:05:01 12:34:56", "garbage") == "2024-05-01T12:34:56"


def test_unset_clock_and_junk_are_none():
    assert parse_exif_datetime("0000:00:00 00:00:00") is None
    assert parse_exif_datetime("yesterday") is None
    assert parse_exif_datetime(None) is None


def test_record_maps_numeric_tags():
    meta = catalog_meta_from_exif(
        {
            "Make": "SONY",
            "Model": " ILCE-7CM2 ",
            "LensModel": "FE 35mm F1.8",
            "Orientation": 8,
            "ISO": "100",
            "ExposureTime": 0.004,
            "FNumber": 1.8,
            "FocalLength": 35,
            "DateTimeOriginal": "2024:05:01 12:34:56",
        }
    )
    assert meta == {
        "orientation": 8,
        "capturedAt": "2024-05-01T12:34:56",
        "make": "SONY",
        "model": "ILCE-7CM2",
        "lens": "FE 35mm F1.8",
        "iso": 100,
        "exposure": 0.004,
        "fnumber": 1.8,
        "focal": 35.0,
    }


def test_empty_record_is_all_none():
    assert all(v is None for v in catalog_meta_from_exif({}).values())
