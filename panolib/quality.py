"""Automatic quality scoring, so 135 panoramas can be triaged without opening each one.

Every check here is computed from the stitch itself rather than from eyeballing the
result, because the point is to surface the handful of panoramas that need attention
out of a large archive.

The strongest signal is geometric self-consistency: if the recorded gimbal angles are
right, overlapping tiles must agree where they overlap, and going all the way around
the horizon must come back to where it started.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict

import numpy as np

from .classify import Classification
from .stitch import StitchResult


@dataclass
class QualityReport:
    score: float                  # 0..1, higher is better
    grade: str                    # good | ok | poor
    overlap_agreement: float      # 0..1
    seam_agreement: float         # 0..1
    coverage: float               # fraction of the sphere, by solid angle
    completeness: float           # 0..1 vs what this capture mode should cover
    tiles_used: int
    tiles_skipped: int
    scene_contrast: float
    overlap_ratio: float          # disagreement relative to scene contrast
    issues: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def _expected_solid_angle(cls: Classification) -> float:
    """How much of the sphere this capture mode can physically cover.

    A DJI sphere never photographs the zenith cap above about +41 degrees elevation,
    which is roughly 17.5% of the sphere, so a perfect sphere set tops out near 0.825.
    Scoring against 1.0 would mark every good panorama down.
    """
    if cls.mode == "sphere":
        return 0.825
    span_h = min(360.0, cls.yaw_span + 67.0)
    span_v = min(180.0, cls.pitch_span + 52.5)
    lat_half = math.radians(span_v / 2.0)
    return (span_h / 360.0) * math.sin(lat_half)


def scene_contrast(result: StitchResult) -> float:
    """Luminance spread over the covered area, in linear light.

    Overlap disagreement has to be judged against this. A low pass over grass and
    trees is full of high spatial frequency, so a sub-pixel misalignment there shows
    up as a much larger pixel difference than the same misalignment over calm water
    or open sky. Comparing raw differences across such scenes measures the landscape,
    not the stitch.
    """
    lum = result.image @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    vals = lum[result.coverage]
    return float(vals.std()) if vals.size else 0.0


# Thresholds below are CALIBRATED from the archive, not chosen by eye: 22 sets
# sampled across all eight trips and every capture mode were stitched and measured.
#   raw disagreement   p10 0.0105  median 0.0318  p75 0.0491  p90 0.0635  max 0.0857
#   contrast-relative  p10 0.0429  median 0.0977  p75 0.1387  p90 0.1853  max 0.3851
# The raw figure is useless as a grade -- the MEDIAN panorama sits at 0.032, so an
# absolute cutoff anywhere near it marks most of a perfectly good archive as broken.
# The contrast-relative ratio is well behaved and is what the score uses.
RATIO_EXCELLENT = 0.045     # ~p10
RATIO_POOR = 0.30           # beyond ~p95
SEAM_RATIO_GOOD = 0.015
SEAM_RATIO_POOR = 0.14


def score(result: StitchResult, cls: Classification,
          missing: list[str] | None = None) -> QualityReport:
    """Combine the geometric and completeness checks into one 0..1 score."""
    issues: list[str] = []
    contrast = scene_contrast(result)

    ratio = result.overlap_disagreement / max(contrast, 1e-6)
    overlap = float(np.clip(1.0 - (ratio - RATIO_EXCELLENT) / (RATIO_POOR - RATIO_EXCELLENT),
                            0.0, 1.0))
    if ratio > 0.22:
        issues.append(f"tiles disagree noticeably where they overlap "
                      f"(relative {ratio:.2f}) — expect ghosting from movement in the "
                      f"scene or drifted gimbal angles")

    seam_ratio = result.seam_closure_error / max(contrast, 1e-6)
    seam = float(np.clip(1.0 - (seam_ratio - SEAM_RATIO_GOOD) / (SEAM_RATIO_POOR - SEAM_RATIO_GOOD),
                         0.0, 1.0))
    if seam_ratio > 0.12:
        issues.append(f"the 360° seam does not close cleanly (relative {seam_ratio:.2f})")

    cov = result.coverage_solid_angle
    expected = _expected_solid_angle(cls)
    completeness = float(np.clip(cov / max(expected, 1e-3), 0.0, 1.0))
    if completeness < 0.9:
        issues.append(f"only {100*completeness:.0f}% of the expected field was captured")

    if result.tiles_skipped:
        issues.append(f"{result.tiles_skipped} tile(s) could not be used")
    if missing:
        issues.append(f"{len(missing)} tile(s) missing from the numbering: {', '.join(missing[:4])}"
                      + (" …" if len(missing) > 4 else ""))
    for note in cls.notes:
        if "zenith not captured" not in note:
            issues.append(note)

    total = 0.45 * overlap + 0.20 * seam + 0.35 * completeness
    if result.tiles_skipped:
        total *= max(0.4, 1.0 - 0.12 * result.tiles_skipped)
    total = float(np.clip(total, 0.0, 1.0))

    grade = "good" if total >= 0.72 else ("ok" if total >= 0.45 else "poor")
    return QualityReport(
        score=round(total, 4), grade=grade,
        overlap_agreement=round(overlap, 4), seam_agreement=round(seam, 4),
        coverage=round(cov, 4), completeness=round(completeness, 4),
        tiles_used=result.tiles_used, tiles_skipped=result.tiles_skipped,
        scene_contrast=round(contrast, 5), overlap_ratio=round(ratio, 4),
        issues=issues,
    )
