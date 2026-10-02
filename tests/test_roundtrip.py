"""Synthetic round-trip test of the stitching geometry.

The idea: take a known equirectangular image, resample it into fake tiles at the exact
gimbal angles a real DJI sphere uses, stitch those tiles back, and require the result to
match what we started with. If any sign, axis or composition order is wrong, the
reconstruction mirrors or rotates and the comparison fails -- which is precisely the
class of bug that is invisible when you only look at a single pretty panorama.

Run with:  python -m pytest tests/ -v
       or: python tests/test_roundtrip.py
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from panolib.exif import TileMeta
from panolib.geometry import (Camera, camera_basis, equirect_angles, recommended_width,
                              tile_bbox)
from panolib.stitch import stitch

# The real capture grid, copied from a measured set (E:\Drone 2023\PANORAMA\100_0679).
SPHERE_ANGLES = [
    (-56.40, -0.10), (-56.40, 14.85), (-56.41, -19.87), (-56.41, -54.91), (-56.41, -89.89),
    (-101.41, -55.01), (-101.41, -20.08), (-101.40, 14.86),
    (-146.22, 14.99), (-146.39, -19.92), (-146.39, -54.85),
    (168.74, -54.99), (168.61, -20.12), (168.61, 14.87),
    (123.69, 14.99), (123.59, -19.86), (123.59, -54.70),
    (78.77, -55.00), (78.61, -20.17), (78.61, 14.78),
    (33.83, 14.99), (33.61, -19.89), (33.61, -54.81),
    (-11.30, -54.99), (-11.40, -20.12), (-11.40, 14.86),
]

TILE_W, TILE_H = 400, 300     # small tiles keep the test quick; geometry is scale-free


def make_reference(width: int = 1024) -> np.ndarray:
    """A synthetic equirect with strong, unambiguous directional structure.

    Deliberately asymmetric in every axis: a horizontal luminance ramp with longitude, a
    vertical one with latitude, and a checker whose phase differs per hemisphere. A
    mirrored or rotated reconstruction cannot coincidentally match this.
    """
    h = width // 2
    theta, phi = equirect_angles(width, h)
    TH, PH = np.meshgrid(theta, phi)

    lon01 = (TH + math.pi) / (2 * math.pi)
    lat01 = (PH + math.pi / 2) / math.pi
    checker = (((TH * 6 // 1).astype(int) + (PH * 6 // 1).astype(int)) % 2).astype(np.float32)

    img = np.zeros((h, width, 3), dtype=np.float32)
    img[..., 0] = 0.25 + 0.7 * lon01                 # red rises toward +longitude
    img[..., 1] = 0.25 + 0.7 * lat01                 # green rises toward the zenith
    img[..., 2] = 0.3 + 0.5 * checker                # blue carries local structure
    return np.clip(img, 0, 1)


def render_tile(ref: np.ndarray, yaw: float, pitch: float, roll: float,
                cam: Camera) -> np.ndarray:
    """Sample ``ref`` through the camera model to produce one synthetic tile."""
    h, w, _ = ref.shape
    right, down, fwd = camera_basis(yaw, pitch, roll)
    f_px = cam.f_px

    xs = (np.arange(cam.width) + 0.5) - cam.width / 2.0
    ys = (np.arange(cam.height) + 0.5) - cam.height / 2.0
    X, Y = np.meshgrid(xs, ys)

    d = (right[None, None, :] * X[..., None]
         + down[None, None, :] * Y[..., None]
         + fwd[None, None, :] * f_px)
    d /= np.linalg.norm(d, axis=-1, keepdims=True)

    lon = np.arctan2(d[..., 0], d[..., 2])
    lat = np.arcsin(np.clip(d[..., 1], -1, 1))
    u = np.clip(((lon + math.pi) / (2 * math.pi)) * w, 0, w - 1).astype(np.int32)
    v = np.clip(((math.pi / 2 - lat) / math.pi) * h, 0, h - 1).astype(np.int32)
    return ref[v, u]


def build_synthetic_set(ref: np.ndarray, tmpdir: str,
                        angles=SPHERE_ANGLES) -> list[TileMeta]:
    """Write synthetic tiles to disk and return their TileMeta records."""
    from PIL import Image
    cam = Camera(TILE_W, TILE_H)
    os.makedirs(tmpdir, exist_ok=True)
    tiles = []
    for i, (yaw, pitch) in enumerate(angles, start=1):
        arr = render_tile(ref, yaw, pitch, 0.0, cam)
        # tiles are written as sRGB-encoded 8-bit, exactly like the real ones
        srgb = np.where(arr <= 0.0031308, arr * 12.92, 1.055 * arr ** (1 / 2.4) - 0.055)
        path = os.path.join(tmpdir, f"DJI_{i:04d}.JPG")
        Image.fromarray((np.clip(srgb, 0, 1) * 255 + 0.5).astype(np.uint8)).save(
            path, quality=97)
        tiles.append(TileMeta(
            path=path, filename=os.path.basename(path),
            yaw=yaw, pitch=pitch, roll=0.0,
            width=TILE_W, height=TILE_H,
            exposure_time=0.001, iso=100.0, f_number=2.8,
        ))
    return tiles


# --------------------------------------------------------------------------- tests

def test_camera_basis_orientation():
    """Yaw 0 must look north, +90 east, and pitch -90 straight down."""
    right, down, fwd = camera_basis(0, 0, 0)
    assert np.allclose(fwd, [0, 0, 1], atol=1e-9), f"yaw 0 should face north, got {fwd}"
    assert np.allclose(right, [1, 0, 0], atol=1e-9), f"right should be east, got {right}"
    assert np.allclose(down, [0, -1, 0], atol=1e-9), f"down should be -Y, got {down}"

    _, _, fwd_e = camera_basis(90, 0, 0)
    assert np.allclose(fwd_e, [1, 0, 0], atol=1e-9), f"yaw 90 should face east, got {fwd_e}"

    _, _, fwd_n = camera_basis(0, -90, 0)
    assert np.allclose(fwd_n, [0, -1, 0], atol=1e-9), f"pitch -90 should face down, got {fwd_n}"


def test_basis_is_orthonormal_with_roll():
    """Roll must rotate the frame without distorting it."""
    for roll in (-11.5, 0.0, 11.5, 45.0):
        r, d, f = camera_basis(-55.0, -25.0, roll)
        for v in (r, d, f):
            assert abs(np.linalg.norm(v) - 1.0) < 1e-9
        assert abs(r @ d) < 1e-9 and abs(r @ f) < 1e-9 and abs(d @ f) < 1e-9
        # `down` is DEFINED as right x forward, so that is the identity to hold.
        # (Consequently right x down == -forward; the world axes are labelled
        # (East, Up, North), an odd permutation of ENU. That is only a labelling
        # quirk -- the projection uses atan2(x, z) built from the same basis, so
        # image-right maps to increasing compass bearing and nothing is mirrored.)
        assert np.allclose(np.cross(r, f), d, atol=1e-9)
        assert np.allclose(np.cross(r, d), -f, atol=1e-9)


def test_camera_constants():
    """The calibrated optics must produce the documented numbers."""
    cam = Camera(2000, 1500)
    assert abs(cam.f_px - 1519.5) < 1.0, f"f_px should be ~1519.5, got {cam.f_px:.1f}"
    assert abs(cam.vfov_deg - 52.5) < 0.2, f"VFOV should be ~52.5, got {cam.vfov_deg:.2f}"
    assert recommended_width(cam) == 8192


def test_tile_bbox_covers_projection():
    """Every pixel a tile actually reaches must lie inside its bounding box.

    The bounding box is the core optimisation; if it is ever too small the stitch
    silently loses slivers of image.
    """
    width, height = 512, 256
    cam = Camera(TILE_W, TILE_H)
    theta, phi = equirect_angles(width, height)
    TH, PH = np.meshgrid(theta, phi)
    D = np.stack([np.cos(PH) * np.sin(TH), np.sin(PH), np.cos(PH) * np.cos(TH)], axis=-1)

    for yaw, pitch in SPHERE_ANGLES:
        right, down, fwd = camera_basis(yaw, pitch, 0.0)
        zc = D @ fwd
        xc, yc = D @ right, D @ down
        with np.errstate(divide="ignore", invalid="ignore"):
            u = cam.f_px * xc / zc + cam.width / 2
            v = cam.f_px * yc / zc + cam.height / 2
        hit = (zc > 1e-6) & (u >= 0) & (u <= cam.width - 1) & (v >= 0) & (v <= cam.height - 1)

        r0, r1, col_ranges = tile_bbox(fwd, cam, width, height)
        inside = np.zeros_like(hit)
        for c0, c1 in col_ranges:
            inside[r0:r1, c0:c1] = True
        missed = hit & ~inside
        assert not missed.any(), (
            f"bbox missed {missed.sum()} px for yaw={yaw} pitch={pitch}")


def test_sphere_roundtrip(tmp_path=None):
    """The headline test: reference -> tiles -> stitch must reproduce the reference."""
    import tempfile
    tmpdir = str(tmp_path) if tmp_path else tempfile.mkdtemp(prefix="pano_rt_")
    ref = make_reference(1024)
    tiles = build_synthetic_set(ref, tmpdir)

    result = stitch(tiles, 1024)
    assert result.tiles_used == len(SPHERE_ANGLES)

    # compare only where tiles actually landed, and away from the poles where
    # equirect sampling is extremely anisotropic
    h, w, _ = result.image.shape
    band = slice(int(h * 0.30), int(h * 0.70))
    mask = result.coverage[band]
    assert mask.mean() > 0.99, f"horizon band should be fully covered, got {mask.mean():.3f}"

    got = result.image[band][mask]
    want = ref[band][mask]
    err = np.abs(got - want).mean()
    assert err < 0.035, f"round-trip error {err:.4f} too high — check signs/axes"

    # a mirrored reconstruction would still be smooth, so test the ramps directly:
    # red must increase with longitude, green with latitude
    mid = h // 2
    row = result.image[mid]
    covered = result.coverage[mid]
    if covered.sum() > w * 0.9:
        left = row[: w // 4, 0].mean()
        right = row[3 * w // 4 :, 0].mean()
        assert right > left, "red ramp reversed — longitude axis is mirrored"

    col_top = result.image[int(h * 0.32)][result.coverage[int(h * 0.32)]][:, 1].mean()
    col_bot = result.image[int(h * 0.68)][result.coverage[int(h * 0.68)]][:, 1].mean()
    assert col_top > col_bot, "green ramp reversed — latitude axis is flipped"


def test_horizon_lands_on_centre_row():
    """A level capture must put the horizon exactly halfway down the canvas."""
    import tempfile
    tmpdir = tempfile.mkdtemp(prefix="pano_hz_")
    width = 512
    h = width // 2
    theta, phi = equirect_angles(width, h)
    TH, PH = np.meshgrid(theta, phi)
    # sky above the horizon, ground below -- a single hard step
    ref = np.zeros((h, width, 3), dtype=np.float32)
    ref[PH >= 0] = np.array([0.30, 0.45, 0.85], dtype=np.float32)
    ref[PH < 0] = np.array([0.18, 0.30, 0.12], dtype=np.float32)

    tiles = build_synthetic_set(ref, tmpdir, angles=[(y, 0.0) for y in range(-180, 180, 45)])
    result = stitch(tiles, width)

    lum = result.image @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    grad = np.abs(np.diff(lum, axis=0))
    grad[~(result.coverage[1:] & result.coverage[:-1])] = 0.0
    rows = [int(np.argmax(grad[:, x])) for x in range(0, width, 8) if grad[:, x].max() > 1e-4]
    assert rows, "no horizon edge detected"
    median_row = float(np.median(rows))
    assert abs(median_row - (h // 2 - 1)) <= 2.0, (
        f"horizon at row {median_row}, expected ~{h // 2}")


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
