"""Tests for the geometric refinement pass.

The important one is :func:`test_injected_yaw_error_is_recovered`. Refinement is a chain
of sign conventions -- phase-correlation direction, the cross product in the Jacobian,
the direction a correction is applied -- and getting any one of them backwards produces
code that runs, converges, and makes the panorama worse. Injecting a KNOWN error and
demanding the exact negative back is the only way to be sure.

A flipped correlation sign returns +1.0 instead of -1.0; a doubled application returns
-2.0. Both fail loudly here.
"""

from __future__ import annotations

import math
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from panolib import refine as R
from panolib.exif import TileMeta
from panolib.geometry import Camera, camera_basis, equirect_angles

TILE_W, TILE_H = 480, 360

# a subset of the real sphere grid -- enough pairs to constrain the solve, few enough
# to keep the test quick
ANGLES = [
    (-56.4, 14.85), (-56.4, -19.87), (-56.4, -54.91),
    (-101.4, 14.86), (-101.4, -20.08), (-101.4, -55.01),
    (-146.4, 14.99), (-146.4, -19.92), (-146.4, -54.85),
    (-11.4, 14.86), (-11.4, -20.12), (-11.4, -54.99),
]


def textured_reference(width: int = 2048) -> np.ndarray:
    """An equirect full of fine, non-repeating detail.

    Phase correlation needs texture with a broad spectrum. A smooth gradient would give
    an ambiguous peak, and a regular checker would alias to multiple equal peaks, so the
    pattern is built from several incommensurate frequencies plus noise.
    """
    h = width // 2
    theta, phi = equirect_angles(width, h)
    TH, PH = np.meshgrid(theta, phi)
    rng = np.random.default_rng(12345)
    img = (0.5
           + 0.16 * np.sin(TH * 37.0) * np.cos(PH * 23.0)
           + 0.12 * np.sin(TH * 11.0 + 1.7) * np.sin(PH * 41.0)
           + 0.09 * np.cos(TH * 67.0 + 0.3) * np.cos(PH * 13.0 + 2.1)
           + 0.07 * np.sin(TH * 97.0) * np.sin(PH * 89.0))
    img = img + rng.normal(0.0, 0.03, img.shape)
    return np.clip(img, 0.02, 0.98).astype(np.float32)


def render_gray_tile(ref: np.ndarray, yaw: float, pitch: float, cam: Camera) -> np.ndarray:
    """Sample the reference through the camera model -> one uint8 grayscale tile."""
    h, w = ref.shape
    right, down, fwd = camera_basis(yaw, pitch, 0.0)
    xs = (np.arange(cam.width) + 0.5) - cam.width / 2.0
    ys = (np.arange(cam.height) + 0.5) - cam.height / 2.0
    X, Y = np.meshgrid(xs, ys)
    d = (right[None, None, :] * X[..., None]
         + down[None, None, :] * Y[..., None]
         + fwd[None, None, :] * cam.f_px)
    d /= np.linalg.norm(d, axis=-1, keepdims=True)
    lon = np.arctan2(d[..., 0], d[..., 2])
    lat = np.arcsin(np.clip(d[..., 1], -1, 1))
    u = np.clip(((lon + math.pi) / (2 * math.pi)) * w, 0, w - 1).astype(np.int32)
    v = np.clip(((math.pi / 2 - lat) / math.pi) * h, 0, h - 1).astype(np.int32)
    return (ref[v, u] * 255).astype(np.uint8)


def build_scene(angles=ANGLES):
    """Grayscale tiles rendered at the TRUE angles, plus matching TileMeta records."""
    cam = Camera(TILE_W, TILE_H)
    ref = textured_reference()
    grays = [render_gray_tile(ref, y, p, cam) for (y, p) in angles]
    tiles = [TileMeta(path=f"<synthetic {k}>", filename=f"DJI_{k:04d}.JPG",
                      yaw=y, pitch=p, roll=0.0, width=TILE_W, height=TILE_H,
                      exposure_time=0.001, iso=100.0, f_number=2.8)
             for k, (y, p) in enumerate(angles)]
    return cam, grays, tiles


# --------------------------------------------------------------------------- tests

def test_phase_correlation_sign():
    """A patch shifted by a known amount must report that exact shift.

    ``phase_correlate(a, b)`` is defined to return ``t`` with ``b(p) = a(p + t)``.
    """
    rng = np.random.default_rng(7)
    n = 128
    base = rng.normal(0, 1, (n + 40, n + 40)).astype(np.float32)
    from scipy.ndimage import gaussian_filter
    base = gaussian_filter(base, 1.2)

    for (sx, sy) in ((5, 0), (0, 7), (-4, 3), (6, -8)):
        a = base[20:20 + n, 20:20 + n]
        # b(p) = a(p + t) with t = (sx, sy)  ->  sample a further along by (sx, sy)
        b = base[20 + sy:20 + sy + n, 20 + sx:20 + sx + n]
        win = R.hann2d(n)
        du, dv, psr = R.phase_correlate(a * win, b * win)
        assert abs(du - sx) < 0.6, f"du {du:.2f} should be {sx} (shift {sx},{sy})"
        assert abs(dv - sy) < 0.6, f"dv {dv:.2f} should be {sy} (shift {sx},{sy})"
        assert psr > 4.0, f"peak too weak, psr {psr:.1f}"


def axis_error_deg(tile, true_yaw, true_pitch):
    """Angle between a tile's corrected optical axis and where it should point."""
    from panolib.geometry import camera_basis, camera_basis_corrected
    got = camera_basis_corrected(tile.yaw, tile.pitch, tile.roll,
                                 getattr(tile, "delta_omega", None))[2]
    want = camera_basis(true_yaw, true_pitch, 0.0)[2]
    return math.degrees(math.acos(float(np.clip(got @ want, -1, 1))))


def test_clean_scene_needs_no_correction():
    """Tiles already at their true angles must not be nudged around."""
    cam, grays, tiles = build_scene()
    pairs = R.candidate_pairs(tiles)
    obs = R.measure(tiles, grays, cam, pairs)
    assert len(obs) >= 8, f"expected usable pairs, got {len(obs)}/{len(pairs)}"
    omega, _ = R.solve_corrections(tiles, obs)
    worst = float(R.axis_shift_deg(tiles, omega).max())
    assert worst < 0.15, f"spurious correction of {worst:.3f} deg on a clean scene"


def test_injected_yaw_error_is_recovered():
    """THE sign test. Tell the solver a tile is at yaw+1.0 and it must answer -1.0.

    +1.0 back would mean the correlation or Jacobian sign is flipped; -2.0 would mean
    the error is being counted twice.
    """
    cam, grays, tiles = build_scene()
    target = 4
    injected = 1.0

    perturbed = []
    import copy
    for k, t in enumerate(tiles):
        c = copy.copy(t)
        if k == target:
            c.yaw = t.yaw + injected      # the ESTIMATE is wrong; the pixels are not
        perturbed.append(c)

    pairs = R.candidate_pairs(perturbed)
    obs = R.measure(perturbed, grays, cam, pairs)
    assert len(obs) >= 8, f"not enough pairs survived: {len(obs)}/{len(pairs)}"
    omega, _ = R.solve_corrections(perturbed, obs)
    fixed = R.apply_corrections(perturbed, omega)

    # before correcting, the target tile is off by the injected amount
    before = axis_error_deg(perturbed[target], *ANGLES[target])
    after = axis_error_deg(fixed[target], *ANGLES[target])
    assert before > 0.8, f"setup wrong: injected error reads as {before:.3f} deg"
    assert after < 0.2, (
        f"after refinement the axis is still {after:.3f} deg off (was {before:.3f}). "
        f"A flipped correlation sign would roughly double it; a missed solve leaves it.")

    others = [axis_error_deg(fixed[k], *ANGLES[k])
              for k in range(len(tiles)) if k != target]
    assert max(others) < 0.3, f"error leaked onto other tiles: max {max(others):.3f} deg"


def test_injected_pitch_error_is_recovered():
    """Same test on the pitch axis, which uses each tile's own right vector."""
    cam, grays, tiles = build_scene()
    target = 7
    injected = -0.8

    import copy
    perturbed = []
    for k, t in enumerate(tiles):
        c = copy.copy(t)
        if k == target:
            c.pitch = t.pitch + injected
        perturbed.append(c)

    pairs = R.candidate_pairs(perturbed)
    obs = R.measure(perturbed, grays, cam, pairs)
    omega, _ = R.solve_corrections(perturbed, obs)
    fixed = R.apply_corrections(perturbed, omega)
    before = axis_error_deg(perturbed[target], *ANGLES[target])
    after = axis_error_deg(fixed[target], *ANGLES[target])
    assert before > 0.6, f"setup wrong: injected error reads as {before:.3f} deg"
    assert after < 0.2, f"pitch error still {after:.3f} deg after refinement (was {before:.3f})"


def test_refine_applies_and_improves():
    """End to end: a perturbed scene must be accepted and score better afterwards."""
    cam, grays, tiles = build_scene()
    import copy
    perturbed = []
    rng = np.random.default_rng(3)
    for t in tiles:
        c = copy.copy(t)
        c.yaw = t.yaw + float(rng.normal(0, 0.6))
        c.pitch = t.pitch + float(rng.normal(0, 0.4))
        perturbed.append(c)

    out, res = R.refine(perturbed, cam=cam, grays=grays)
    assert res.applied, f"refinement was rejected: {res.reason}"
    assert res.refined_score > res.baseline_score, (
        f"score did not improve: {res.baseline_score} -> {res.refined_score}")

    # the corrected axes must be closer to the truth than the perturbed ones were
    def total_err(ts):
        return sum(axis_error_deg(t, *ANGLES[k]) for k, t in enumerate(ts))
    before = total_err(perturbed)
    after = total_err(out)
    assert after < before * 0.6, (
        f"axis error only went {before:.2f} -> {after:.2f} deg")


def test_guard_rejects_when_no_gain():
    """A clean scene gives no real gain, so the guard must keep the original tiles."""
    cam, grays, tiles = build_scene()
    out, res = R.refine(tiles, cam=cam, grays=grays)
    if res.applied:
        # accepted is tolerable only if the corrections are genuinely negligible
        assert res.max_correction_deg < 0.2, (
            f"accepted a {res.max_correction_deg:.2f} deg change on an already-correct scene")
    else:
        assert out is tiles


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
