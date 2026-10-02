"""Working out what kind of panorama a folder of tiles actually is.

Classification is driven by the DISTRIBUTION OF GIMBAL ANGLES, not by counting files.
Tile counts are a tempting shortcut -- in a clean archive 26 means sphere, 9 means a
3x3 grid, 7 means a horizontal sweep -- but real folders contain interrupted captures,
sets missing a tile, and one-off single frames. Counting would mislabel all of them,
and a mislabelled set gets the wrong canvas and the wrong viewer modes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .exif import TileMeta

# Human-facing names. Note the deliberate absence of "180 degree": DJI calls the
# 7-tile mode that, but the tiles actually span about 246 degrees once the lens FOV is
# added to the 179.5 degree sweep of their centres, so the label would be wrong.
MODE_LABELS = {
    "sphere": "Full sphere (360°)",
    "sweep": "Wide sweep",
    "grid": "Wide grid",
    "vertical": "Vertical strip",
    "partial": "Partial capture",
    "single": "Single frame",
}


@dataclass
class Classification:
    mode: str
    label: str
    yaw_clusters: int
    pitch_clusters: int
    yaw_span: float          # degrees spanned by tile CENTRES (not including FOV)
    pitch_span: float
    has_nadir: bool
    has_zenith: bool
    complete: bool           # False when the capture looks interrupted
    notes: list[str]


def _cluster(values: list[float], tol: float, circular: bool = False) -> list[float]:
    """Group angles that are within ``tol`` degrees, returning cluster centres."""
    if not values:
        return []
    if circular:
        # work on the unit circle so -179 and +179 land in the same cluster
        pts = sorted((v % 360.0) for v in values)
        clusters: list[list[float]] = [[pts[0]]]
        for v in pts[1:]:
            if v - clusters[-1][-1] <= tol:
                clusters[-1].append(v)
            else:
                clusters.append([v])
        if len(clusters) > 1 and (pts[0] + 360.0) - clusters[-1][-1] <= tol:
            clusters[0] = clusters[-1] + clusters[0]
            clusters.pop()
        return [sum(c) / len(c) for c in clusters]

    pts = sorted(values)
    clusters = [[pts[0]]]
    for v in pts[1:]:
        if v - clusters[-1][-1] <= tol:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    return [sum(c) / len(c) for c in clusters]


def _circular_span(values: list[float]) -> float:
    """Angular span of yaw centres, accounting for the wrap at +/-180."""
    if len(values) < 2:
        return 0.0
    pts = sorted(v % 360.0 for v in values)
    gaps = [pts[(i + 1) % len(pts)] - pts[i] for i in range(len(pts) - 1)]
    gaps.append(pts[0] + 360.0 - pts[-1])
    return 360.0 - max(gaps)


def classify(tiles: list[TileMeta]) -> Classification:
    """Infer capture mode from the gimbal angles of ``tiles``."""
    usable = [t for t in tiles if t.has_gimbal]
    notes: list[str] = []
    if len(tiles) != len(usable):
        notes.append(f"{len(tiles) - len(usable)} tile(s) have no gimbal metadata")

    if not usable:
        return Classification("single", MODE_LABELS["single"], 0, 0, 0.0, 0.0,
                              False, False, False,
                              notes + ["no usable orientation metadata"])

    yaws = [t.yaw for t in usable]
    pitches = [t.pitch for t in usable]

    yaw_c = _cluster(yaws, tol=12.0, circular=True)
    pitch_c = _cluster(pitches, tol=10.0)
    yaw_span = _circular_span(yaws)
    pitch_span = max(pitches) - min(pitches)
    has_nadir = min(pitches) < -75.0
    has_zenith = max(pitches) > 75.0

    if len(usable) == 1:
        mode = "single"
    elif yaw_span >= 300.0 and len(pitch_c) >= 2:
        mode = "sphere"
    elif len(pitch_c) == 1 and len(yaw_c) >= 3:
        mode = "sweep"
    elif len(yaw_c) == 1 and len(pitch_c) >= 2:
        mode = "vertical"
    elif len(yaw_c) >= 2 and len(pitch_c) >= 2:
        mode = "grid"
    else:
        mode = "partial"

    # A sphere capture walks 8 yaw columns; far fewer means it was cut short.
    complete = True
    if mode == "sphere":
        if len(yaw_c) < 7:
            complete = False
            notes.append(f"only {len(yaw_c)} yaw columns for a sphere — capture looks interrupted")
        if not has_nadir:
            notes.append("no nadir tile — the downward pole will be empty")
    elif mode in ("vertical", "partial") and len(usable) >= 3:
        complete = False
        notes.append("looks like an interrupted capture")

    if not has_zenith:
        # expected for every DJI sphere; worth stating once rather than alarming
        notes.append("zenith not captured (normal for this drone) — the cap is filled in")

    return Classification(
        mode=mode, label=MODE_LABELS[mode],
        yaw_clusters=len(yaw_c), pitch_clusters=len(pitch_c),
        yaw_span=yaw_span, pitch_span=pitch_span,
        has_nadir=has_nadir, has_zenith=has_zenith,
        complete=complete, notes=notes,
    )


def missing_tiles(tiles: list[TileMeta]) -> list[str]:
    """Gaps in the DJI_NNNN numbering, which mean a tile was lost from the backup."""
    nums = []
    for t in tiles:
        stem = t.filename.rsplit(".", 1)[0]
        digits = "".join(ch for ch in stem if ch.isdigit())
        if digits:
            nums.append(int(digits))
    if len(nums) < 2:
        return []
    nums.sort()
    return [f"DJI_{n:04d}.JPG" for n in range(nums[0], nums[-1] + 1) if n not in set(nums)]


def recommended_canvas(cls: Classification, native_width: int) -> int:
    """Canvas width for a set, scaled down when it does not span the full circle.

    A 7-tile sweep painted onto a full 360 degree canvas wastes about two thirds of
    the pixels on empty space, and the cropped result ends up softer than the tiles
    deserve. Sizing the canvas to the actual sweep keeps the output at native
    angular resolution.
    """
    if cls.mode == "sphere":
        return native_width
    span = max(cls.yaw_span, 30.0) + 70.0          # centres plus roughly one lens FOV
    frac = min(1.0, span / 360.0)
    w = int(native_width * frac)
    return max(1024, (w // 64) * 64)
