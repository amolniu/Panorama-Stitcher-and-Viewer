"""Tests for the metadata the build writes into a stitched panorama.

A stitched panorama is a new file, so whatever the tiles recorded reaches it only through
write_gpano -- and every plate and print is read from it, not from the tiles. These tests
pin the two ways that went wrong: the GPS altitude lost the sign of a below-sea-level
reading, and a metadata fix never reached panoramas already in the build cache.

Synthetic values throughout. Needs exiftool; each test passes vacuously without it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from panolib.build import META_VERSION, retag_if_stale, write_gpano
from panolib.capture import read_capture
from panolib.exif import TileMeta


def _exiftool():
    try:
        from panolib.exif import find_exiftool
        return find_exiftool(None)
    except Exception:
        return None


def _tile(altitude: float, rel: float = 3.2) -> TileMeta:
    return TileMeta(path="DJI_0001.JPG", filename="DJI_0001.JPG", yaw=0.0, pitch=0.0,
                    roll=0.0, width=4000, height=3000, exposure_time=0.001, iso=100.0,
                    f_number=2.8, datetime="2022:07:07 07:34:29", lat=12.3456789,
                    lon=-45.6789012, altitude=altitude, rel_altitude=rel, model="FC7303")


def _equirect() -> str:
    path = os.path.join(tempfile.mkdtemp(prefix="pano_build_"), "equirect.jpg")
    rng = np.random.default_rng(7)
    Image.fromarray(rng.integers(0, 255, (100, 200, 3), dtype=np.uint8)).save(path, quality=90)
    return path


def _tags(exe: str, path: str) -> dict:
    out = subprocess.run([exe, "-j", "-n", "-G1", "-GPS:GPSAltitude", "-GPS:GPSAltitudeRef",
                          "-XMP-drone-dji:all", "-XMP-GPano:ProjectionType", path],
                         capture_output=True, text=True, encoding="utf-8")
    return json.loads(out.stdout)[0]


def _write(exe, path, tile):
    return write_gpano(path, exe, img_w=200, img_h=100, lon_span=360.0, lat_max=90.0,
                       tile_count=26, tile=tile)


def test_below_sea_level_keeps_its_sign_in_the_stitched_file():
    """The camera recorded 108.776 m BELOW sea level; the stitched file must say so --
    in the GPS block and in DJI's own signed field, which the plate reader prefers."""
    exe = _exiftool()
    if not exe:
        return
    path = _equirect()
    assert _write(exe, path, _tile(-108.776))
    t = _tags(exe, path)
    assert t.get("GPS:GPSAltitudeRef") == 1, t                   # 1 = below sea level
    assert abs(t["GPS:GPSAltitude"] - 108.776) < 1e-6, t
    assert abs(float(t["XMP-drone-dji:AbsoluteAltitude"]) + 108.78) < 1e-6, t
    cap = read_capture(path, exe)
    assert abs(cap.alt_msl + 108.78) < 0.01, cap.alt_msl
    assert abs(cap.alt_agl - 3.2) < 0.01, cap.alt_agl


def test_above_sea_level_is_marked_as_such():
    exe = _exiftool()
    if not exe:
        return
    path = _equirect()
    assert _write(exe, path, _tile(209.48, rel=42.0))
    t = _tags(exe, path)
    assert t.get("GPS:GPSAltitudeRef") == 0 and abs(t["GPS:GPSAltitude"] - 209.48) < 1e-6, t
    assert abs(read_capture(path, exe).alt_msl - 209.48) < 0.01


def test_a_stale_cached_panorama_is_retagged_in_place_once():
    """A panorama stitched before a metadata fix must get the fix on the next build --
    without re-stitching and without touching the pixels -- and only once."""
    exe = _exiftool()
    if not exe:
        return
    path = _equirect()
    # what the old build left behind: the magnitude, no flag, no DJI field
    subprocess.run([exe, "-q", "-q", "-overwrite_original", "-GPS:GPSAltitude#=108.776",
                    "-XMP-GPano:ProjectionType=equirectangular", path], check=True)
    assert read_capture(path, exe).alt_msl > 0, "fixture should reproduce the old bug"
    with Image.open(path) as im:
        before = np.asarray(im).copy()
    meta = os.path.join(os.path.dirname(path), "meta-x.json")
    cached = {"id": "x", "width": 200, "height": 100,
              "coverage": {"lon_span": 360.0, "lat_max": 90.0}}
    with open(meta, "w", encoding="utf-8") as fh:
        json.dump(cached, fh)

    assert retag_if_stale(cached, path, meta, [_tile(-108.776)], exe) is True
    assert abs(read_capture(path, exe).alt_msl + 108.78) < 0.01
    with Image.open(path) as im:
        assert np.array_equal(np.asarray(im), before), "re-tagging must not touch the image"
    assert _tags(exe, path).get("XMP-GPano:ProjectionType") == "equirectangular"
    with open(meta, encoding="utf-8") as fh:
        assert json.load(fh)["meta_version"] == META_VERSION
    assert cached["meta_version"] == META_VERSION
    # second build: nothing to do
    assert retag_if_stale(cached, path, meta, [_tile(-108.776)], exe) is False


def test_retag_is_skipped_without_exiftool_or_tiles():
    cached = {"width": 200, "height": 100}
    assert retag_if_stale(cached, "x.jpg", "meta.json", [_tile(1.0)], None) is False
    assert retag_if_stale(cached, "x.jpg", "meta.json", [], "exiftool") is False
    assert "meta_version" not in cached


def _main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {fn.__name__}: {exc}")
        except Exception as exc:
            failed += 1
            print(f"  ERROR {fn.__name__}: {exc.__class__.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
