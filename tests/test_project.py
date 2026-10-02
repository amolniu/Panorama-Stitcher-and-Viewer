"""Tests for the print-resolution view renderer.

The renderer reimplements the viewer shader in numpy, and the two must agree or
"print this view" prints something other than what was on screen. These tests pin the
orientation with coloured markers at known directions: a mirrored or rotated frame
cannot pass them by accident.
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from panolib.project import (View, heading_of_view, minification, native_view_width,
                             render_view, supersample_factor, view_directions)

W, H = 720, 360


def _equirect_quadrants() -> np.ndarray:
    """North half red, south half blue; above the horizon brighter than below."""
    img = np.zeros((H, W, 3), dtype=np.float32)
    lon = (np.arange(W) + 0.5) / W * 2 * math.pi - math.pi      # -pi..pi, 0 = north
    lat = math.pi / 2 - (np.arange(H) + 0.5) / H * math.pi
    LON, LAT = np.meshgrid(lon, lat)
    east = np.sin(LON) > 0                   # x > 0
    img[..., 0] = np.where(east, 0.2, 0.9)   # west is red
    img[..., 2] = np.where(east, 0.9, 0.2)   # east is blue
    img[..., 1] = np.where(LAT > 0, 0.9, 0.1)  # sky is green-bright
    return img


def _marker_equirect(lon_deg: float, lat_deg: float, radius_deg: float = 4.0) -> np.ndarray:
    """Black everywhere except a white disc around one direction."""
    img = np.zeros((H, W, 3), dtype=np.float32)
    lon = (np.arange(W) + 0.5) / W * 2 * math.pi - math.pi
    lat = math.pi / 2 - (np.arange(H) + 0.5) / H * math.pi
    LON, LAT = np.meshgrid(lon, lat)
    d = np.stack([np.cos(LAT) * np.sin(LON), np.sin(LAT), np.cos(LAT) * np.cos(LON)], -1)
    c = np.array([math.cos(math.radians(lat_deg)) * math.sin(math.radians(lon_deg)),
                  math.sin(math.radians(lat_deg)),
                  math.cos(math.radians(lat_deg)) * math.cos(math.radians(lon_deg))])
    ang = np.degrees(np.arccos(np.clip(d @ c, -1, 1)))
    img[ang < radius_deg] = 1.0
    return img


def _f(out: np.ndarray) -> np.ndarray:
    """render_view returns uint8; compare in 0..1."""
    return out.astype(np.float32) / 255.0


def test_centre_pixel_looks_where_the_camera_points():
    """A view yawed to 90 degrees must put the EAST marker at its centre."""
    pano = _marker_equirect(90.0, 0.0)
    out = _f(render_view(pano, View(yaw=math.radians(90), fov=60.0), 200, 100))
    assert out[50, 100].mean() > 0.9, "east marker should be at the centre of an east-facing view"
    out_n = _f(render_view(pano, View(yaw=0.0, fov=60.0), 200, 100))
    assert out_n[50, 100].mean() < 0.1, "a north-facing view must not see the east marker"


def test_yaw_then_pitch_order():
    """Facing east and tilted up 30 deg: the (east, +30) marker sits at centre and a
    marker on the horizon 40 deg to the left (bearing 50) lies left-and-below it.
    Composing pitch before yaw, or yawing the wrong way, puts it elsewhere."""
    pano = _marker_equirect(90.0, 30.0) + _marker_equirect(50.0, 0.0)
    out = _f(render_view(pano, View(yaw=math.radians(90), pitch=math.radians(30), fov=110.0), 240, 120))
    assert out[60, 120].mean() > 0.9, "the (east,+30) marker should be centred"
    ys, xs = np.nonzero(out.mean(axis=2) > 0.5)
    other = [(y, x) for y, x in zip(ys, xs) if abs(x - 120) > 20 or abs(y - 60) > 20]
    assert other, "the bearing-50 marker should be in frame at 110 deg"
    oy = np.mean([y for y, _ in other]); ox = np.mean([x for _, x in other])
    assert ox < 120 and oy > 60, f"bearing-50 marker should be left-and-below centre, got x={ox:.0f} y={oy:.0f}"


def test_roll_is_right_handed_about_forward():
    """Facing north with +90 deg roll, the marker that was straight UP appears on the RIGHT."""
    pano = _marker_equirect(0.0, 30.0)
    out = _f(render_view(pano, View(yaw=0.0, roll=math.radians(90), fov=90.0), 200, 200))
    ys, xs = np.nonzero(out.mean(axis=2) > 0.5)
    assert xs.size, "marker should be in frame"
    assert xs.mean() > 130 and abs(ys.mean() - 100) < 25, (
        f"with +90 roll the overhead marker should move to the right: x={xs.mean():.0f} y={ys.mean():.0f}")


def test_exposure_scales_the_image():
    pano = np.full((H, W, 3), 0.25, dtype=np.float32)
    dim = _f(render_view(pano, View(fov=60.0), 50, 50, exposure=1.0))
    bright = _f(render_view(pano, View(fov=60.0), 50, 50, exposure=2.0))
    assert abs(dim.mean() - 0.25) < 0.02 and abs(bright.mean() - 0.5) < 0.02


def test_large_render_is_strip_rendered_identically():
    """Strips must not leave seams: a strip boundary and the rows beside it agree."""
    import panolib.project as P
    pano = _equirect_quadrants()
    old = P.STRIP_PIXELS
    try:
        P.STRIP_PIXELS = 50 * 100           # force several strips on a 100-wide render
        strips = _f(render_view(pano, View(yaw=0.3, pitch=0.2, fov=80.0), 100, 100))
    finally:
        P.STRIP_PIXELS = old
    whole = _f(render_view(pano, View(yaw=0.3, pitch=0.2, fov=80.0), 100, 100))
    assert np.abs(strips - whole).max() < 1e-6, "strip rendering must equal whole rendering"
    assert minification(pano.shape, View(yaw=0.3, pitch=0.2, fov=80.0), 100, 100) > 1.0, \
        "this case should run the supersampled (ss=2) path"

    # and the production path -- big enlargements run with ss=1 -- in several strips
    small = _equirect_quadrants()[::2, ::2]                      # 360 x 180
    v = View(yaw=0.3, pitch=0.2, fov=80.0)
    assert minification(small.shape, v, 100, 100) <= 1.0, "this case must be ss=1"
    try:
        P.STRIP_PIXELS = 30 * 100                                # 4 strips of 30 rows
        strips1 = _f(render_view(small, v, 100, 100))
    finally:
        P.STRIP_PIXELS = old
    whole1 = _f(render_view(small, v, 100, 100))
    assert np.abs(strips1 - whole1).max() < 1e-6


def test_render_view_acts_on_the_supersampling_decision():
    """render_view must USE the measure, not just agree with it: forcing the measure to
    'not minified' on a minified case changes the pixels, forcing it to 2.0 does not."""
    import panolib.project as P
    pano = _equirect_quadrants()
    v = View(yaw=0.3, pitch=0.2, fov=80.0)
    assert supersample_factor(pano.shape, v, 100, 100) == 2
    assert supersample_factor(pano.shape[:2], v, 400, 400) == 1
    real = P.minification
    base = _f(render_view(pano, v, 100, 100))
    try:
        P.minification = lambda *a, **k: 0.5
        off = _f(render_view(pano, v, 100, 100))
        P.minification = lambda *a, **k: 2.0
        on = _f(render_view(pano, v, 100, 100))
    finally:
        P.minification = real
    assert np.abs(base - on).max() < 1e-6, "forcing ss=2 must reproduce the real render"
    assert np.abs(base - off).max() > 0.05, "forcing ss=1 must change a minified render"


def test_up_is_up_and_west_is_left():
    """Facing north: sky at the top of the frame, west on the left -- no mirror, no flip."""
    pano = _equirect_quadrants()
    out = _f(render_view(pano, View(yaw=0.0, fov=90.0), 200, 100))
    top = out[10, 100]
    bottom = out[90, 100]
    assert top[1] > 0.8 and bottom[1] < 0.2, f"sky should be on top: top={top}, bottom={bottom}"
    left = out[50, 10]
    right = out[50, 190]
    assert left[0] > 0.8 and left[2] < 0.3, f"west (red) should be on the LEFT when facing north: {left}"
    assert right[2] > 0.8 and right[0] < 0.3, f"east (blue) should be on the RIGHT: {right}"


def test_pitch_moves_the_marker_vertically():
    pano = _marker_equirect(0.0, 30.0)                       # marker 30 degrees up, north
    level = _f(render_view(pano, View(yaw=0.0, pitch=0.0, fov=90.0), 200, 100))
    tilted = _f(render_view(pano, View(yaw=0.0, pitch=math.radians(30), fov=90.0), 200, 100))
    assert tilted[50, 100].mean() > 0.9, "tilting up 30 degrees should centre the elevated marker"
    assert level[50, 100].mean() < 0.1
    # in the level view the marker sits in the upper half
    ys, xs = np.nonzero(level.mean(axis=2) > 0.5)
    assert ys.size and ys.mean() < 50, "an elevated marker must appear above centre"


def test_little_planet_puts_nadir_at_centre():
    pano = _marker_equirect(0.0, -90.0, radius_deg=6.0)
    out = _f(render_view(pano, View(mode="planet", pitch=math.radians(-90), fov=205.0), 200, 200))
    assert out[100, 100].mean() > 0.9, "a nadir-centred stereographic view should show the nadir at centre"


def test_partial_panorama_outside_coverage_is_background():
    """A sweep covering -60..60 degrees must render background when looking east.

    ``fov`` is the vertical field; on a 2:1 frame a 40-degree view spans about 72
    degrees horizontally, so use a square frame here to keep the test's geometry plain.
    """
    pano = np.ones((H, W, 3), dtype=np.float32)              # all white
    lon_r = (math.radians(-60), math.radians(60))
    out = _f(render_view(pano, View(yaw=math.radians(90), fov=40.0), 100, 100, lon_range=lon_r,
                         background=(0.0, 0.0, 0.0)))
    assert out.mean() < 0.02, f"looking 90 deg away from a +/-60 deg sweep must be background, got {out.mean():.3f}"
    out_in = _f(render_view(pano, View(yaw=0.0, fov=40.0), 100, 100, lon_range=lon_r,
                            background=(0.0, 0.0, 0.0)))
    assert out_in.mean() > 0.98
    # and a view straddling the coverage edge is partly covered, partly background
    edge = _f(render_view(pano, View(yaw=math.radians(60), fov=40.0), 100, 100, lon_range=lon_r,
                          background=(0.0, 0.0, 0.0)))
    assert 0.3 < edge.mean() < 0.7, edge.mean()


def test_fov_is_vertical_like_the_shader():
    """Screen x is scaled by aspect, so a wider frame sees more horizontally at the same fov."""
    d_sq, _ = view_directions(View(yaw=0.0, fov=60.0), 100, 100)
    d_wide, _ = view_directions(View(yaw=0.0, fov=60.0), 200, 100)
    lon_sq = np.degrees(np.arctan2(d_sq[..., 0], d_sq[..., 2]))
    lon_wide = np.degrees(np.arctan2(d_wide[..., 0], d_wide[..., 2]))
    assert abs(lon_sq.max() - 30.0) < 1.0, lon_sq.max()          # square: half-angle = fov/2
    assert lon_wide.max() > 45.0                                    # 2:1: atan(2 tan 30) = 49 deg
    lat_sq = np.degrees(np.arcsin(d_sq[..., 1]))
    lat_wide = np.degrees(np.arcsin(d_wide[..., 1]))
    assert abs(lat_sq.max() - lat_wide.max()) < 0.5                  # vertical extent unchanged


def test_directions_are_unit_vectors():
    d, valid = view_directions(View(yaw=1.0, pitch=0.3, fov=80.0), 64, 32)
    n = np.linalg.norm(d, axis=-1)
    assert np.allclose(n, 1.0, atol=1e-6)
    assert valid.all()


def test_native_width_is_honest():
    """An 8192-px equirect yields ~1304 px/rad. A 75-degree (vertical) view on a square
    frame spans 2*1304*tan(37.5) ~ 2000 native px; a 2:1 frame sees atan(2*tan 37.5) =
    56.9 deg half-angle, so ~4000 px. Narrower fields give fewer; nothing exceeds the
    equirect itself."""
    w_sq = native_view_width(8192, View(fov=75.0), aspect=1.0)
    assert 1950 <= w_sq <= 2050, w_sq
    w_wide = native_view_width(8192, View(fov=75.0), aspect=2.0)
    assert 3950 <= w_wide <= 4050, w_wide
    assert native_view_width(8192, View(fov=20.0), aspect=1.0) < w_sq
    assert native_view_width(8192, View(mode="planet", fov=250.0), aspect=1.0) <= 8192
    # a sweep is stored cropped to its coverage: 3850 px over 246 degrees is 1.46x denser
    # than the same width over 360, and the honest figure must say so
    full = native_view_width(3850, View(fov=75.0), aspect=1.3)
    sweep = native_view_width(3850, View(fov=75.0), aspect=1.3, lon_span=math.radians(246.09))
    assert abs(sweep / full - 360.0 / 246.09) < 0.02, (full, sweep)


def test_minification_is_measured_not_estimated():
    """The supersampling decision must see the real centre density, including for wide
    fields where the capped warning estimate is deliberately low."""
    shape = (4096, 8192, 3)
    # square 60-degree view on a 720-px equirect: centre texel footprint is
    # (720/2pi) * (2 tan 30 / N) source px per output px = 1.32 at N=100, 0.66 at N=200
    assert abs(minification((360, 720, 3), View(fov=60.0), 100, 100) - 1.32) < 0.05
    assert abs(minification((360, 720, 3), View(fov=60.0), 200, 200) - 0.66) < 0.03
    # fov 140, aspect 1.5: the capped estimate says 6775 px is enough; the centre is
    # really 1.59x minified there (reviewer's probe), so ss must fire
    est = native_view_width(8192, View(fov=140.0), aspect=1.5)
    assert 6700 <= est <= 6850, est
    m = minification(shape, View(fov=140.0), est, round(est / 1.5))
    assert 1.5 < m < 1.7, m
    # and at ~10750 px wide it is no longer minified at the centre
    m2 = minification(shape, View(fov=140.0), 10800, 7200)
    assert m2 <= 1.02, m2
    # a little planet: the nadir's RADIAL density is real detail (texel rows per radian),
    # and at 400 px from a 720-px source the centre is 1.43x minified -- stereographic
    # magnification is lowest there, 2*tan(fov/4) rad per screen unit. Longitudes
    # converging at the nadir must NOT count, though: at 800 px the lon term would be
    # enormous in raw texels, yet the measure says "not minified".
    planet = View(mode="planet", pitch=math.radians(-90), fov=205.0)
    assert abs(minification((360, 720, 3), planet, 400, 400) - 1.43) < 0.03
    assert minification((360, 720, 3), planet, 800, 800) < 0.75
    # roll rotates the pixel step off the lon/lat axes; the footprint is a length and
    # must not change: 2048 px / 2pi * 2 tan(37.5) / 400 = 1.251 at any roll
    base = minification((1024, 2048, 3), View(fov=75.0), 400, 400)
    assert abs(base - 1.251) < 0.01, base
    for roll in (20.0, 45.0, 60.0):        # the old max-of-components measure is off by >0.07 here
        m = minification((1024, 2048, 3), View(fov=75.0, roll=math.radians(roll)), 400, 400)
        assert abs(m - base) < 1e-3, (roll, m, base)


def test_heading_of_view_is_compass():
    assert heading_of_view(View(yaw=0.0)) == 0.0
    assert abs(heading_of_view(View(yaw=math.radians(90))) - 90.0) < 1e-9
    assert abs(heading_of_view(View(yaw=math.radians(-51.8))) - 308.2) < 1e-6


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
