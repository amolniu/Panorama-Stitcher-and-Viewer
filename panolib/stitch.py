"""Metadata-driven equirectangular stitching.

Tiles are placed from their recorded gimbal angles rather than by feature matching.
DJI records the camera orientation for every tile, which makes the geometry a direct
computation instead of an estimation problem -- no failure mode where a sky-heavy or
low-texture panorama refuses to align.

Blending happens in LINEAR light after each tile is brought onto a common exposure,
because the tiles are independently auto-exposed and a DJI set is effectively an
exposure bracket across the scene.

Performance notes (all measured, see docs/GROUND_TRUTH.md section 12 -- the naive
version took 186 s per 8k sphere, this one takes 47 s):
  * each tile is evaluated only inside its own angular bounding box;
  * valid pixels are selected with a boolean mask BEFORE gathering, so the expensive
    bilinear taps only touch pixels that exist;
  * the four taps use ``np.take`` on a flattened image;
  * accumulation uses a plain fancy-index ``+=`` on a flattened canvas rather than
    ``np.add.at``. That is ~5x faster and is only correct because a single tile never
    writes the same output pixel twice. Do not copy this to a loop where indices can
    repeat -- there it would silently drop contributions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from .exif import TileMeta
from .geometry import Camera, camera_basis_corrected, tile_bbox

Image.MAX_IMAGE_PIXELS = None  # these are our own files; the decompression guard only gets in the way


def srgb_to_linear_lut(gain: float = 1.0) -> np.ndarray:
    """256-entry sRGB->linear table with an exposure gain folded in.

    One uint8 lookup replaces a power function over millions of pixels.
    """
    v = np.arange(256, dtype=np.float32) / 255.0
    lin = np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)
    return (lin * np.float32(gain)).astype(np.float32)


def linear_to_srgb(x: np.ndarray) -> np.ndarray:
    """Inverse transfer curve, clamped to the display range."""
    x = np.clip(x, 0.0, 1.0)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1.0 / 2.4) - 0.055)


@dataclass
class StitchResult:
    """A merged panorama in linear light, plus what we learned while merging."""

    image: np.ndarray                 # (H, W, 3) float32, LINEAR (not display-ready)
    coverage: np.ndarray              # (H, W) bool -- True where any tile contributed
    width: int
    height: int
    tiles_used: int
    tiles_skipped: int
    overlap_disagreement: float       # mean |difference| between overlapping tiles
    seam_closure_error: float         # disagreement across the +/-180 seam
    horizon_row: int                  # where a level horizon should land
    warnings: list[str] = field(default_factory=list)

    @property
    def coverage_fraction(self) -> float:
        """Fraction of equirect PIXELS covered (not solid angle -- they differ a lot)."""
        return float(self.coverage.mean())

    @property
    def coverage_solid_angle(self) -> float:
        """Fraction of the SPHERE covered, weighting each row by cos(latitude)."""
        phi = math.pi / 2.0 - ((np.arange(self.height) + 0.5) / self.height) * math.pi
        w = np.cos(phi)
        per_row = self.coverage.mean(axis=1)
        return float((per_row * w).sum() / w.sum())


def _angular_extent(coverage: np.ndarray) -> tuple[float, float]:
    """Actual angular coverage in degrees, seam-aware for longitude."""
    h, w = coverage.shape
    rows = np.nonzero(coverage.any(axis=1))[0]
    cols_any = coverage.any(axis=0)
    if rows.size == 0 or not cols_any.any():
        return 0.0, 0.0
    vert = (rows[-1] - rows[0] + 1) / h * 180.0
    if cols_any.all():
        return 360.0, vert
    # longest contiguous covered arc on the circle of columns
    gaps = np.nonzero(~cols_any)[0]
    start = (gaps[-1] + 1) % w
    order = (np.arange(w) + start) % w
    run = cols_any[order]
    first = int(np.argmax(run))
    last = len(run) - int(np.argmax(run[::-1]))
    return (last - first) / w * 360.0, vert


def stitch(tiles: list[TileMeta], width: int, hfov_deg: float | None = None,
           feather_power: float = 1.0, track_quality: bool = True) -> StitchResult:
    """Merge ``tiles`` into an equirectangular canvas ``width`` x ``width//2``.

    Returns LINEAR light. Apply tone mapping before display -- these scenes routinely
    carry 12 stops of dynamic range and a naive gamma will either clip the sky or
    crush the ground.
    """
    if not tiles:
        raise ValueError("stitch() needs at least one tile")

    height = width // 2
    acc = np.zeros((height * width, 3), dtype=np.float32)
    wacc = np.zeros(height * width, dtype=np.float32)

    # quality bookkeeping: remember the first tile's value per pixel so later tiles
    # can be compared against it in the overlap
    first_val = np.full((height * width, 3), np.nan, dtype=np.float32) if track_quality else None
    dis_sum = 0.0
    dis_n = 0

    warnings: list[str] = []
    ref_gain: float | None = None
    used = skipped = 0

    for tile in tiles:
        if not tile.has_gimbal:
            warnings.append(f"{tile.filename}: no gimbal metadata, skipped")
            skipped += 1
            continue

        cam = Camera(tile.width, tile.height, hfov_deg or Camera(tile.width, tile.height).hfov_deg)
        right, down, fwd = camera_basis_corrected(
            tile.yaw, tile.pitch, tile.roll, getattr(tile, 'delta_omega', None))
        r0, r1, col_ranges = tile_bbox(fwd, cam, width, height)
        if r1 <= r0:
            skipped += 1
            continue

        try:
            with Image.open(tile.path) as im:
                raw = np.asarray(im.convert("RGB"))
        except (OSError, ValueError) as exc:
            warnings.append(f"{tile.filename}: unreadable ({exc.__class__.__name__}), skipped")
            skipped += 1
            continue

        if raw.shape[0] != tile.height or raw.shape[1] != tile.width:
            cam = Camera(raw.shape[1], raw.shape[0], cam.hfov_deg)

        if ref_gain is None:
            ref_gain = tile.exposure_gain
        lut = srgb_to_linear_lut(ref_gain / tile.exposure_gain)
        flat_img = lut[raw].reshape(-1, 3)

        # A clipped pixel records only "at least this bright". Scaling it to the
        # common exposure turns that floor into a confident value, so a tile whose
        # sky blew out would drag the blend toward its own clipping point wherever it
        # overlaps a correctly exposed neighbour. Down-weight clipped samples so the
        # neighbour wins, but keep a small floor -- where every tile is clipped the
        # pixel must still be covered rather than punched out.
        clipped = (raw.max(axis=2).astype(np.float32) - 250.0) / 5.0
        np.clip(clipped, 0.0, 1.0, out=clipped)
        clip_weight = (1.0 - 0.95 * clipped).reshape(-1)
        ih, iw = raw.shape[0], raw.shape[1]
        f_px = cam.f_px
        cx, cy = iw / 2.0, ih / 2.0

        phi = (math.pi / 2.0 - ((np.arange(r0, r1) + 0.5) / height) * math.pi)
        sin_p = np.sin(phi).astype(np.float32)[:, None]
        cos_p = np.cos(phi).astype(np.float32)[:, None]

        for c0, c1 in col_ranges:
            if c1 <= c0:
                continue
            theta = ((((np.arange(c0, c1) + 0.5) / width) * 2.0 * math.pi - math.pi)
                     .astype(np.float32)[None, :])
            dx = cos_p * np.sin(theta)
            dz = cos_p * np.cos(theta)

            zc = dx * fwd[0] + sin_p * fwd[1] + dz * fwd[2]
            in_front = zc > 1e-6
            if not in_front.any():
                continue
            xc = dx * right[0] + sin_p * right[1] + dz * right[2]
            yc = dx * down[0] + sin_p * down[1] + dz * down[2]

            safe = np.where(in_front, zc, np.float32(1.0))
            u = f_px * xc / safe + cx
            v = f_px * yc / safe + cy
            valid = in_front & (u >= 0) & (u <= iw - 1) & (v >= 0) & (v <= ih - 1)
            if not valid.any():
                continue

            uu = u[valid]
            vv = v[valid]
            x0 = uu.astype(np.int32)
            y0 = vv.astype(np.int32)
            x1 = np.minimum(x0 + 1, iw - 1)
            y1 = np.minimum(y0 + 1, ih - 1)
            fx = (uu - x0)[:, None]
            fy = (vv - y0)[:, None]

            base_y0 = y0.astype(np.int64) * iw
            base_y1 = y1.astype(np.int64) * iw
            sample = (np.take(flat_img, base_y0 + x0, axis=0) * ((1 - fx) * (1 - fy))
                      + np.take(flat_img, base_y0 + x1, axis=0) * (fx * (1 - fy))
                      + np.take(flat_img, base_y1 + x0, axis=0) * ((1 - fx) * fy)
                      + np.take(flat_img, base_y1 + x1, axis=0) * (fx * fy))

            # feather toward the tile edges so overlaps cross-fade instead of stepping
            wx = np.minimum(uu, iw - 1 - uu) * (2.0 / iw)
            wy = np.minimum(vv, ih - 1 - vv) * (2.0 / ih)
            weight = np.minimum(wx, wy).astype(np.float32)
            if feather_power != 1.0:
                weight = np.power(weight, np.float32(feather_power))
            weight *= np.take(clip_weight, base_y0 + x0)
            weight += np.float32(1e-6)

            rows, cols = np.nonzero(valid)
            flat_idx = (rows + r0).astype(np.int64) * width + (cols + c0)

            if first_val is not None:
                prev = first_val[flat_idx]
                seen = ~np.isnan(prev[:, 0])
                if seen.any():
                    dis_sum += float(np.abs(prev[seen] - sample[seen]).mean()) * int(seen.sum())
                    dis_n += int(seen.sum())
                    fresh = ~seen
                    if fresh.any():
                        first_val[flat_idx[fresh]] = sample[fresh]
                else:
                    first_val[flat_idx] = sample

            # safe because a tile writes each output pixel at most once
            acc[flat_idx] += sample * weight[:, None]
            wacc[flat_idx] += weight

        used += 1

    covered = wacc > 0
    out = np.zeros_like(acc)
    out[covered] = acc[covered] / wacc[covered][:, None]
    image = out.reshape(height, width, 3)
    coverage = covered.reshape(height, width)

    seam_err = 0.0
    if coverage[:, 0].any() and coverage[:, -1].any():
        both = coverage[:, 0] & coverage[:, -1]
        if both.any():
            seam_err = float(np.abs(image[both, 0] - image[both, -1]).mean())

    if used == 0:
        warnings.append("no tiles could be placed")

    return StitchResult(
        image=image,
        coverage=coverage,
        width=width,
        height=height,
        tiles_used=used,
        tiles_skipped=skipped,
        overlap_disagreement=(dis_sum / dis_n) if dis_n else 0.0,
        seam_closure_error=seam_err,
        horizon_row=height // 2,
        warnings=warnings,
    )


def _circular_blur(row: np.ndarray, radius: int) -> np.ndarray:
    """Box blur along a row that wraps around the +/-180 seam."""
    if radius < 1:
        return row
    radius = min(radius, len(row) // 2)
    k = 2 * radius + 1
    padded = np.concatenate([row[-radius:], row, row[:radius]], axis=0)
    kernel = np.ones(k, dtype=np.float32) / k
    return np.stack([np.convolve(padded[:, c], kernel, mode="valid") for c in range(3)], axis=-1)


def _circular_median(values: np.ndarray, radius: int) -> np.ndarray:
    """Median filter along a wrapping row -- used to reject local outliers."""
    if radius < 1:
        return values
    n = len(values)
    radius = min(radius, n // 2)
    padded = np.concatenate([values[-radius:], values, values[:radius]], axis=0)
    idx = np.arange(n)[:, None] + np.arange(2 * radius + 1)[None, :]
    return np.median(padded[idx], axis=1).astype(np.float32)


def fill_zenith(image: np.ndarray, coverage: np.ndarray,
                trust_margin_frac: float = 0.012) -> tuple[np.ndarray, np.ndarray]:
    """Fill the uncaptured cap above the top tile row so the sky does not gape open.

    A DJI sphere's highest pitch row is +14.9 deg, so with a 52.5 deg vertical FOV
    nothing above about +41 deg is ever photographed -- about 17% of the sphere by
    solid angle. It has to be invented, and it should look deliberate.

    Two measured properties of this boundary drive the design:

    * The topmost covered rows are NOT trustworthy. Measured on a real sphere, the
      boundary row averages 0.155 brighter than 20 rows below it, with p95 at 0.971
      -- essentially clipped. Seeding the fill from it produces a bright scalloped
      fringe. So a ``trust_margin`` of rows below the boundary is discarded and
      overwritten too, and the seed comes from a clean band below that.
    * Local outliers exist: on the same sphere a contiguous run of ~260 columns sat
      below 60% of the median brightness. Seeding per column alone would smear that
      blob up to the pole, so the per-column seed is passed through a circular
      median filter first.

    The boundary is scalloped (it is the tops of eight tiles), so every step works
    per column against that column's own boundary rather than a single global row.
    """
    h, w, _ = image.shape
    out = image.copy()
    cov = coverage.copy()
    if cov.all():
        return out, cov

    first_row = np.full(w, -1, dtype=np.int32)
    for x in range(w):
        idx = np.nonzero(cov[:, x])[0]
        if idx.size:
            first_row[x] = idx[0]
    if not (first_row >= 0).any():
        return out, cov

    trust = max(4, int(h * trust_margin_frac))
    band = max(4, h // 200)
    fallback = int(np.median(first_row[first_row >= 0]))

    # The top tile row often has the drone's own propellers or gimbal shroud
    # intruding along its upper edge -- a near-black, out-of-focus band with no
    # overlapping tile above it to outvote it. It cannot be told from genuinely dark
    # scene content by brightness or texture alone (both were measured and neither
    # separates), but in the merged canvas it always shows as an abrupt dark band at
    # the very top of coverage, with the real sky plateau just below. So each column
    # walks down from its own boundary until luminance reaches a fraction of that
    # plateau, and everything above is treated as unusable. The search is capped so a
    # genuinely dark sky can never eat an unbounded amount of the image.
    lum = image @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    ref_off = max(band + 2, int(h * 0.06))
    max_trust = max(trust, int(h * 0.09))
    start = np.empty(w, dtype=np.int32)
    for x in range(w):
        f = int(first_row[x]) if first_row[x] >= 0 else fallback
        lo = min(h - 1, f + ref_off)
        hi = min(h, lo + band)
        col = lum[lo:hi, x][cov[lo:hi, x]] if hi > lo else np.empty(0, dtype=np.float32)
        ref = float(np.median(col)) if col.size else 0.0
        j = f
        if ref > 1e-4:
            limit = min(h - 1, f + max_trust)
            while j < limit and cov[j, x] and lum[j, x] < 0.6 * ref:
                j += 1
        start[x] = max(j, f + trust)
    start = np.clip(start, 0, h - band - 1)

    # per-column median over a clean band below the untrusted fringe
    seed = np.zeros((w, 3), dtype=np.float32)
    for x in range(w):
        lo = int(start[x])
        hi = min(h, lo + band)
        col_cov = cov[lo:hi, x]
        col = out[lo:hi, x][col_cov]
        seed[x] = np.median(col, axis=0) if col.size else out[lo:hi, x].mean(axis=0)

    # suppress local outliers (dark blobs, flare) before they can reach the pole
    seed = _circular_median(seed, max(3, w // 64))

    mean_color = seed.mean(axis=0)
    max_start = int(start.max())
    fill_above = np.arange(h)[:, None] < start[None, :]   # (h, w) rows to synthesise

    current = seed.copy()
    for j in range(max_start - 1, -1, -1):
        t = (max_start - j) / max(max_start, 1)
        radius = int(1 + t * t * (w * 0.25))
        blended = _circular_blur(current, radius) * (1.0 - t * 0.6) + mean_color * (t * 0.6)
        mask = fill_above[j]
        if mask.any():
            row = out[j].copy()
            row[mask] = blended[mask]
            out[j] = row
        # columns not yet reached keep the seed so they start clean
        current = np.where(mask[:, None], blended, seed)

    # feather the last few real rows into the synthesised sky so there is no hard line
    feather = max(2, trust // 2)
    for x in range(w):
        s = int(start[x])
        for i in range(feather):
            j = s + i
            if j >= h or not cov[j, x]:
                continue
            a = (i + 1) / (feather + 1)
            out[j, x] = out[j, x] * a + seed[x] * (1.0 - a)

    cov[:max_start] = True
    return out, cov


def crop_to_coverage(image: np.ndarray, coverage: np.ndarray,
                     inscribed: bool = False) -> tuple[np.ndarray, tuple[float, float]]:
    """Crop to the covered region, returning the image and its angular size in degrees.

    Handles the +/-180 seam: a set straddling it has covered columns at both ends of
    the canvas, so a naive min/max over column indices would return the whole width.

    ``inscribed=True`` returns the largest fully-covered axis-aligned rectangle
    instead of the bounding box. Partial panoramas have scalloped edges where the
    tile corners are, and a bounding-box crop leaves them visibly ragged.
    """
    h, w, _ = image.shape
    col_any = coverage.any(axis=0)
    row_any = coverage.any(axis=1)
    if not col_any.any() or not row_any.any():
        return image, (0.0, 0.0)

    if col_any.all():
        cols = np.arange(w)
    else:
        gaps = np.nonzero(~col_any)[0]
        start = (gaps[-1] + 1) % w
        order = (np.arange(w) + start) % w
        run = col_any[order]
        first = int(np.argmax(run))
        last = len(run) - int(np.argmax(run[::-1]))
        cols = order[first:last]

    rows = np.nonzero(row_any)[0]
    r0, r1 = int(rows[0]), int(rows[-1]) + 1
    sub = image[r0:r1][:, cols]
    sub_cov = coverage[r0:r1][:, cols]

    if inscribed and not sub_cov.all():
        # shrink rows inward until every remaining row is fully covered
        keep = sub_cov.all(axis=1)
        if keep.any():
            idx = np.nonzero(keep)[0]
            sub = sub[idx[0]:idx[-1] + 1]
            sub_cov = sub_cov[idx[0]:idx[-1] + 1]
        colkeep = sub_cov.all(axis=0)
        if colkeep.any():
            cidx = np.nonzero(colkeep)[0]
            sub = sub[:, cidx[0]:cidx[-1] + 1]

    deg_h = sub.shape[1] / w * 360.0
    deg_v = sub.shape[0] / h * 180.0
    return sub, (deg_h, deg_v)
