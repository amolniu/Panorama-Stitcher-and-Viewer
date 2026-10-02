"""Tone mapping for merged panoramas.

Why this module exists at all: because each tile is auto-exposed on its own, a DJI
panorama set is effectively an exposure bracket, and the merged linear result carries
far more dynamic range than 8-bit sRGB can show. A measured example from the archive
(the Wisconsin sunset sphere) spans about 5000:1 -- roughly 12 stops -- with the
ground sitting some 9 stops below the sky.

Global operators were tried on that scene and all failed visibly: no tone mapping and
"normalise the median to 0.18" blow the sky to white, while Reinhard, extended
Reinhard, ACES and a log-lift curve each either clip the sky or crush the ground.
There is no single curve that fits 12 stops into 8 bits.

So the default is a LOCAL operator (Durand-style): split log luminance into a
low-frequency base and a high-frequency detail layer, compress only the base, and put
the detail back. A guided filter provides the base because a plain Gaussian halos
badly along the horizon, which is exactly where these images have their strongest
luminance edge.
"""

from __future__ import annotations

import numpy as np
from scipy.ndimage import uniform_filter, zoom as ndzoom

LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


def luminance(rgb: np.ndarray) -> np.ndarray:
    return rgb @ LUMA


def dynamic_range(image: np.ndarray, coverage: np.ndarray | None = None) -> float:
    """Ratio of the 99.9th to the 1st luminance percentile over covered pixels."""
    lum = luminance(image)
    lum = lum[coverage] if coverage is not None else lum.ravel()
    lum = lum[lum > 1e-6]
    if lum.size == 0:
        return 1.0
    lo = max(float(np.percentile(lum, 1)), 1e-9)
    return float(np.percentile(lum, 99.9) / lo)


def guided_filter(p: np.ndarray, guide: np.ndarray, radius: int, eps: float) -> np.ndarray:
    """Edge-aware smoothing (He et al.), used to build a halo-free base layer."""
    mean_g = uniform_filter(guide, radius)
    mean_p = uniform_filter(p, radius)
    corr_gg = uniform_filter(guide * guide, radius)
    corr_gp = uniform_filter(guide * p, radius)
    var_g = corr_gg - mean_g * mean_g
    cov_gp = corr_gp - mean_g * mean_p
    a = cov_gp / (var_g + eps)
    b = mean_p - a * mean_g
    return uniform_filter(a, radius) * guide + uniform_filter(b, radius)


def auto_compression(dr: float) -> float:
    """Pick a base-layer compression strength from the scene's dynamic range.

    A flat overcast scene needs almost none; a 12-stop sunset needs a lot. Baking one
    value in would either flatten the easy scenes or fail the hard ones, and this
    archive contains both.
    """
    if dr <= 60:            # gentle, low-contrast light
        return 0.85
    if dr <= 400:
        return 0.65
    if dr <= 2000:
        return 0.50
    return 0.42             # measured sweet spot for the ~5000:1 sunset case


def local_tonemap(image: np.ndarray, coverage: np.ndarray | None = None, *,
                  compression: float | None = None, detail_gain: float = 1.12,
                  radius_frac: float = 0.035, saturation: float = 0.92,
                  base_downscale: int = 4, highlight_knee: float = 0.8,
                  white_percentile: float = 99.5) -> np.ndarray:
    """Tone map a LINEAR panorama to a display-referred LINEAR image.

    The caller still applies the sRGB transfer curve afterwards.

    ``base_downscale`` computes the guided filter at reduced resolution and upsamples
    the result. That is valid because the base layer is low-frequency by construction,
    and it removes most of the cost -- the filter dominated tone-mapping time at 8k.
    """
    img = image.astype(np.float32, copy=False)
    if coverage is None:
        coverage = np.ones(img.shape[:2], dtype=bool)

    lum = np.maximum(luminance(img), 1e-6)
    log_lum = np.log(lum)

    # uncovered pixels would drag the filter toward -inf; neutralise them first
    if not coverage.all():
        fill = float(log_lum[coverage].mean()) if coverage.any() else 0.0
        log_lum = np.where(coverage, log_lum, fill)

    if compression is None:
        compression = auto_compression(dynamic_range(img, coverage))

    lo, hi = float(log_lum.min()), float(log_lum.max())
    span = max(hi - lo, 1e-6)
    norm = (log_lum - lo) / span

    ds = max(1, int(base_downscale))
    radius = max(4, int(img.shape[1] * radius_frac / ds))
    if ds > 1:
        small = norm[::ds, ::ds]
        base_small = guided_filter(small, small, radius, 1e-3)
        zoom_factors = (norm.shape[0] / base_small.shape[0], norm.shape[1] / base_small.shape[1])
        base = ndzoom(base_small, zoom_factors, order=1)
        if base.shape != norm.shape:  # rounding can leave it a pixel short
            base = np.resize(base, norm.shape)
    else:
        base = guided_filter(norm, norm, radius, 1e-3)

    base = base * span + lo
    detail = log_lum - base
    new_log = base * compression + detail * detail_gain

    scale = np.exp(new_log - log_lum).astype(np.float32)
    out = img * scale[..., None]

    ref = float(np.percentile(luminance(out)[coverage], white_percentile)) if coverage.any() else 1.0
    out = out / max(ref, 1e-6) * 0.92

    # soft shoulder so specular highlights roll off instead of clipping flat
    out = out / (1.0 + np.maximum(out - highlight_knee, 0.0))

    if saturation != 1.0:
        grey = luminance(out)[..., None]
        out = grey + (out - grey) * saturation

    return np.clip(out, 0.0, 1.0)


def global_tonemap(image: np.ndarray, coverage: np.ndarray | None = None,
                   exposure: float = 1.0) -> np.ndarray:
    """Plain exposure plus an ACES-style shoulder.

    Kept for flat, low-dynamic-range scenes and for a fast preview -- it is much
    cheaper than the local operator but will clip a high-contrast sky.
    """
    img = image.astype(np.float32, copy=False) * np.float32(exposure)
    a, b, c, d, e = 2.51, 0.03, 2.43, 0.59, 0.14
    return np.clip((img * (a * img + b)) / (img * (c * img + d) + e), 0.0, 1.0)
