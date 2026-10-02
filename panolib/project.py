"""Rendering a chosen view of a panorama at print resolution.

The viewer's shader turns each screen pixel into a world direction and samples the
equirect. This module does the same arithmetic in numpy so that "print this view"
produces the framing the user was actually looking at. The two MUST agree -- every
formula here mirrors ``viewer/shaders.js`` line for line, and ``tests/test_project.py``
pins the orientation by rendering a view aimed at a known marker.

Resolution is the honest limit of this feature. An 8192-px equirect holds about 1304
pixels per radian; a 75-degree rectilinear view therefore has only ~2000 native pixels
across however large you print it. :func:`native_view_width` reports that figure so the
usual enlargement warning can be given truthfully instead of quietly upscaling.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .cinema import camera_matrix

TWO_PI = 2.0 * math.pi

#: Viewer modes that are photographic framings and can be printed as a view.
#: (flat, mercator, mirror and cube are diagnostic layouts; print the equirect instead.)
VIEW_MODES = {"immersive", "planet", "tunnel", "fisheye", "pannini"}


@dataclass
class View:
    """A camera framing, in the viewer's own units."""

    mode: str = "immersive"
    yaw: float = 0.0        # radians, 0 = north, + clockwise
    pitch: float = 0.0      # radians, + up
    roll: float = 0.0       # radians
    fov: float = 75.0       # degrees, VERTICAL -- the shader scales x by aspect, so the
                            # horizontal field is 2*atan(aspect*tan(fov/2)). Keep it this
                            # way: changing the convention here would break parity.
    morph: float = 0.0      # 0 rectilinear .. 1 stereographic (immersive mode only)
    pannini_d: float = 1.0
    exposure: float = 1.0   # the viewer's exposure slider; "print what I see" honours it

    @property
    def shader_mode(self) -> int:
        return {"immersive": 0, "planet": 1, "tunnel": 1, "fisheye": 1, "pannini": 3}.get(self.mode, 0)


def screen_grid(width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
    """Pixel-centre coordinates in the shader's frame: x scaled by aspect, +y up."""
    aspect = width / height
    x = ((np.arange(width, dtype=np.float64) + 0.5) / width * 2.0 - 1.0) * aspect
    y = 1.0 - (np.arange(height, dtype=np.float64) + 0.5) / height * 2.0
    return np.meshgrid(x, y)


def dirs_rectilinear(X, Y, fov_rad: float) -> np.ndarray:
    t = math.tan(min(max(fov_rad, 0.01), 3.0) * 0.5)
    d = np.stack([X * t, Y * t, np.ones_like(X)], axis=-1)
    return d / np.linalg.norm(d, axis=-1, keepdims=True)


def dirs_stereographic(X, Y, fov_rad: float) -> np.ndarray:
    r = np.hypot(X, Y)
    theta = 2.0 * np.arctan(r * math.tan(min(max(fov_rad, 0.01), 6.0) * 0.25))
    safe = np.where(r < 1e-9, 1.0, r)
    s = np.sin(theta)
    return np.stack([s * X / safe, s * Y / safe, np.cos(theta)], axis=-1)


def dirs_pannini(X, Y, fov_rad: float, d: float) -> tuple[np.ndarray, np.ndarray]:
    """Returns (dirs, valid); points outside the projection's domain are invalid."""
    s = math.tan(min(max(fov_rad, 0.01), 3.0) * 0.5)
    x = X * s * (d + 1.0)
    y = Y * s * (d + 1.0)
    u = x / (d + 1.0)
    arg = u * d / np.sqrt(1.0 + u * u)
    valid = np.abs(arg) <= 1.0
    phi = np.arctan(u) + np.arcsin(np.clip(arg, -1.0, 1.0))
    S = (d + 1.0) / (d + np.cos(phi))
    theta = np.arctan(y / np.maximum(S, 1e-4))
    dirs = np.stack([np.sin(phi) * np.cos(theta), np.sin(theta), np.cos(phi) * np.cos(theta)], axis=-1)
    return dirs, valid


def _directions_for_grid(view: View, X: np.ndarray, Y: np.ndarray,
                         R: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """World directions for an arbitrary grid of screen coordinates."""
    fov = math.radians(view.fov)
    valid = np.ones(X.shape, dtype=bool)
    if view.shader_mode == 0:
        d = dirs_rectilinear(X, Y, fov)
        if view.morph > 0.001:
            b = dirs_stereographic(X, Y, fov)
            d = d * (1.0 - view.morph) + b * view.morph
            d /= np.linalg.norm(d, axis=-1, keepdims=True)
    elif view.shader_mode == 1:
        d = dirs_stereographic(X, Y, fov)
    elif view.shader_mode == 3:
        d, valid = dirs_pannini(X, Y, fov, view.pannini_d)
    else:
        raise ValueError(f"mode {view.mode!r} cannot be printed as a view")
    return d @ R.T, valid


def view_directions(view: View, width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
    """World-space unit directions for every pixel of a ``width`` x ``height`` render."""
    X, Y = screen_grid(width, height)
    R = camera_matrix(view.yaw, view.pitch, view.roll)
    return _directions_for_grid(view, X, Y, R)


def sample_equirect_bilinear(pano: np.ndarray, dirs: np.ndarray,
                             lon_range=(-math.pi, math.pi),
                             lat_range=(-math.pi / 2, math.pi / 2)) -> tuple[np.ndarray, np.ndarray]:
    """Bilinear lookup with horizontal wrap, honouring a partial panorama's coverage.

    Same mapping as the shader's ``samplePano``: longitude fraction wraps around the
    full circle and is then scaled by the covered span, so a 246-degree sweep lands
    on its own pixels and anything outside is reported invalid rather than smeared.
    """
    h, w, _ = pano.shape
    lon = np.arctan2(dirs[..., 0], dirs[..., 2])
    lat = np.arcsin(np.clip(dirs[..., 1], -1.0, 1.0))

    span = max(lon_range[1] - lon_range[0], 1e-4)
    t = np.mod((lon - lon_range[0]) / TWO_PI, 1.0)
    u = t * (TWO_PI / span)
    vspan = max(lat_range[1] - lat_range[0], 1e-4)
    v = (lat_range[1] - lat) / vspan
    inside = (u >= 0.0) & (u <= 1.0) & (v >= 0.0) & (v <= 1.0)

    full_circle = span >= TWO_PI - 1e-6
    fx = u * w - 0.5
    fy = np.clip(v * h - 0.5, 0.0, h - 1.0)
    x0 = np.floor(fx).astype(np.int64)
    y0 = np.floor(fy).astype(np.int64)
    wx = (fx - x0).astype(np.float32)[..., None]
    wy = (fy - y0).astype(np.float32)[..., None]
    if full_circle:
        x0m = np.mod(x0, w)
        x1m = np.mod(x0 + 1, w)
    else:
        x0m = np.clip(x0, 0, w - 1)
        x1m = np.clip(x0 + 1, 0, w - 1)
    y1 = np.clip(y0 + 1, 0, h - 1)
    y0 = np.clip(y0, 0, h - 1)

    p = pano.astype(np.float32, copy=False)
    out = (p[y0, x0m] * (1 - wx) * (1 - wy) + p[y0, x1m] * wx * (1 - wy)
           + p[y1, x0m] * (1 - wx) * wy + p[y1, x1m] * wx * wy)
    return out, inside


#: Pixels per strip. Direction arrays are float64 x 3 plus several intermediates --
#: about 170 bytes per pixel in flight -- so this keeps a strip near 350 MB regardless
#: of how large the finished print is.
STRIP_PIXELS = 2_000_000


def render_view(pano: np.ndarray, view: View, width: int, height: int,
                lon_range=(-math.pi, math.pi), lat_range=(-math.pi / 2, math.pi / 2),
                background=(0.05, 0.05, 0.06), exposure: float = 1.0,
                progress=None) -> np.ndarray:
    """Render ``view`` of ``pano`` (float RGB in 0..1) at ``width`` x ``height``, as uint8.

    Rendered in horizontal strips so peak memory is bounded by STRIP_PIXELS rather than
    by the output size: a 24x36 print at 300 dpi is 78 million pixels, which rendered
    in one piece would need more than 10 GB.

    When the view is *minified* -- the equirect has more pixels across the view than the
    output does -- it is rendered at 2x and box-filtered down, standing in for the GPU's
    mipmapped sampling in the viewer. Prints are usually enlargements, so this rarely
    fires, but without it a reduced view would alias where the screen did not.
    """
    pano32 = pano.astype(np.float32, copy=False)
    aspect = width / height
    # The honest-resolution estimate caps wide fields at 120 degrees, which is the right
    # direction for a warning but the wrong one for anti-aliasing (the centre of a 140
    # degree view is finer than the capped figure). So the supersampling decision
    # measures the real worst-case source-texels-per-output-pixel instead.
    ss = supersample_factor(pano32.shape, view, width, height, lon_range, lat_range)
    W2, H2 = width * ss, height * ss

    out = np.empty((height, width, 3), dtype=np.uint8)
    bg = np.array(background, dtype=np.float32)
    xs = ((np.arange(W2, dtype=np.float64) + 0.5) / W2 * 2.0 - 1.0) * aspect
    R = camera_matrix(view.yaw, view.pitch, view.roll)

    rows_per_strip = max(ss, (max(1, STRIP_PIXELS // W2) // ss) * ss)
    for y0 in range(0, H2, rows_per_strip):
        y1 = min(H2, y0 + rows_per_strip)
        ys = 1.0 - (np.arange(y0, y1, dtype=np.float64) + 0.5) / H2 * 2.0
        X, Y = np.meshgrid(xs, ys)
        dirs, valid = _directions_for_grid(view, X, Y, R)
        img, inside = sample_equirect_bilinear(pano32, dirs, lon_range, lat_range)
        ok = valid & inside
        img = np.where(ok[..., None], img * np.float32(exposure), bg)
        if ss == 2:
            img = img.reshape((y1 - y0) // 2, 2, width, 2, 3).mean(axis=(1, 3))
        out[y0 // ss:y1 // ss] = np.clip(img * 255.0 + 0.5, 0.0, 255.0).astype(np.uint8)
        if progress:
            progress(y1 / H2)
    return out


def minification(pano_shape, view: View, width: int, height: int,
                 lon_range=(-math.pi, math.pi), lat_range=(-math.pi / 2, math.pi / 2),
                 grid: int = 17) -> float:
    """Worst-case source texels per output pixel over the frame (1.0 = no minification).

    Measured, not estimated: directions are evaluated on a coarse grid of pixel centres
    and one pixel to the right and below, converted to equirect texel coordinates, and
    the larger step taken. Longitude steps are weighted by ``cos(lat)`` because the
    equirect's rows near the poles hold many texels per unit of real detail; counting
    them raw would call every little planet minified.
    """
    h, w = pano_shape[0], pano_shape[1]
    span = max(lon_range[1] - lon_range[0], 1e-4)
    vspan = max(lat_range[1] - lat_range[0], 1e-4)
    aspect = width / height
    R = camera_matrix(view.yaw, view.pitch, view.roll)
    gx = np.linspace(0.5, width - 0.5, min(grid, width))
    gy = np.linspace(0.5, height - 0.5, min(grid, height))

    def lonlat(px, py):
        X, Y = np.meshgrid((px / width * 2.0 - 1.0) * aspect, 1.0 - py / height * 2.0)
        d, valid = _directions_for_grid(view, X, Y, R)
        return (np.arctan2(d[..., 0], d[..., 2]), np.arcsin(np.clip(d[..., 1], -1, 1)), valid)

    lon0, lat0, v0 = lonlat(gx, gy)
    lon1, lat1, v1 = lonlat(gx + 1.0, gy)
    lon2, lat2, v2 = lonlat(gx, gy + 1.0)
    ok = v0 & v1 & v2
    if not ok.any():
        return 0.0
    # Each step is a vector in texel space; its LENGTH is the footprint. Taking the
    # larger component instead would under-count a step that runs diagonally in the
    # lon/lat frame -- by 1/sqrt(2) at 45 degrees of roll -- and skip supersampling.
    def step(lon_b, lat_b):
        dlon = np.remainder(lon_b - lon0 + math.pi, TWO_PI) - math.pi
        return np.hypot(dlon * np.cos(lat0) * (w / span), (lat_b - lat0) * (h / vspan))

    return float(np.max(np.maximum(step(lon1, lat1), step(lon2, lat2))[ok]))


def supersample_factor(pano_shape, view: View, width: int, height: int,
                       lon_range=(-math.pi, math.pi), lat_range=(-math.pi / 2, math.pi / 2)) -> int:
    """2 when the render would minify the source anywhere in the frame, else 1."""
    return 2 if minification(pano_shape, view, width, height, lon_range, lat_range) > 1.0 else 1


def native_view_width(equirect_width: int, view: View, aspect: float = 1.5,
                      lon_span: float = TWO_PI) -> int:
    """How many source pixels a view really spans across -- the honest print limit.

    ``view.fov`` is the VERTICAL field, exactly as in the shader (screen x is scaled by
    the aspect ratio, so the horizontal field follows from it). The horizontal half-angle
    is therefore ``atan(aspect * tan(fov/2))``.

    The equirect carries ``W / lon_span`` pixels per radian at the horizon -- ``lon_span``
    is 2pi for a full sphere, but a sweep is stored cropped to its coverage and packs the
    same width into fewer radians. A rectilinear view has its finest source-to-output
    ratio at its centre, where output pixels per radian are ``(box_w/2) / tan(h_half)``;
    no enlargement at the centre means ``box_w <= 2 * tan(h_half) * W / span``, which is
    the figure returned. Wide stereographic framings compress their edges and have no
    single number; the formula is applied with the field capped as a conservative
    estimate, never above the equirect's own width.
    """
    px_per_rad = equirect_width / max(lon_span, 1e-4)
    fov_v = math.radians(max(1.0, min(view.fov, 170.0)))
    if view.shader_mode == 1 or view.fov > 120:
        fov_v = math.radians(120.0)
    h_half = math.atan(max(aspect, 0.05) * math.tan(fov_v / 2.0))
    return int(min(equirect_width, round(2.0 * px_per_rad * math.tan(h_half))))


def heading_of_view(view: View) -> float:
    """Compass bearing the view faces, in degrees, 0 = north, clockwise."""
    return (math.degrees(view.yaw)) % 360.0
