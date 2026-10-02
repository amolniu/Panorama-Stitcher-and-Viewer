"""Camera geometry for DJI panorama tiles.

Coordinate conventions (verified empirically against the real archive -- see
docs/GROUND_TRUTH.md sections 1-3; do not change these without re-running the
calibration, a sign flip here silently mirrors every panorama):

    World frame:  X = East,  Y = Up,  Z = North.
    Gimbal yaw:   0 = North, positive = clockwise toward East.
    Gimbal pitch: 0 = level, -90 = straight down (nadir).
    Gimbal roll:  as reported by DJI, but MUST BE NEGATED here.

The camera basis is built directly as vectors rather than by composing Euler
rotation matrices -- fewer chances to get a composition order wrong, and it
reads the same way as the convention above.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# --- Calibrated camera constants -------------------------------------------------
# HFOV is the MEASURED value, not the 73.74 deg implied by EXIF
# FocalLengthIn35mmFormat=24mm. DJI crops / distortion-corrects the panorama tiles,
# so the EXIF figure is wrong for them. Measured across 5 sets spanning 4 countries
# and 5 years: 66.5, 67.0, 66.5, 66.5, 67.0 -> median 66.5, mean 66.7.
DEFAULT_HFOV_DEG = 66.7

#: DJI reports gimbal roll with the opposite sign to this module's convention.
ROLL_SIGN = -1.0


@dataclass(frozen=True)
class Camera:
    """Pinhole model for one tile size and field of view."""

    width: int
    height: int
    hfov_deg: float = DEFAULT_HFOV_DEG

    @property
    def f_px(self) -> float:
        """Focal length in pixels. 1520.9 px for a 2000 px wide tile at 66.7 deg."""
        return (self.width / 2.0) / math.tan(math.radians(self.hfov_deg) / 2.0)

    @property
    def vfov_deg(self) -> float:
        """Vertical FOV implied by the tile aspect ratio. 52.5 deg for 2000x1500."""
        return 2.0 * math.degrees(math.atan((self.height / 2.0) / self.f_px))

    @property
    def half_diagonal_rad(self) -> float:
        """Angular radius of the cone enclosing the tile -- used for bounding boxes."""
        return math.atan(math.hypot(
            math.tan(math.radians(self.hfov_deg) / 2.0),
            math.tan(math.radians(self.vfov_deg) / 2.0),
        ))

    @property
    def K(self) -> np.ndarray:
        f = self.f_px
        return np.array([[f, 0.0, self.width / 2.0],
                         [0.0, f, self.height / 2.0],
                         [0.0, 0.0, 1.0]], dtype=np.float64)


def camera_basis(yaw_deg: float, pitch_deg: float, roll_deg: float,
                 roll_sign: float = ROLL_SIGN) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (right, down, forward) unit vectors in the world frame.

    ``v_world = R @ v_cam`` where ``R`` has these as its columns, and the camera
    frame is X=right, Y=down, Z=forward (the usual computer-vision convention).
    """
    y = math.radians(yaw_deg)
    p = math.radians(pitch_deg)
    r = math.radians(roll_deg) * roll_sign

    fwd = np.array([math.sin(y) * math.cos(p),
                    math.sin(p),
                    math.cos(y) * math.cos(p)], dtype=np.float64)
    right = np.array([math.cos(y), 0.0, -math.sin(y)], dtype=np.float64)
    down = np.cross(right, fwd)

    if r:  # rotate right/down about the optical axis (Rodrigues)
        c, s = math.cos(r), math.sin(r)
        right = right * c + np.cross(fwd, right) * s + fwd * (fwd @ right) * (1.0 - c)
        down = down * c + np.cross(fwd, down) * s + fwd * (fwd @ down) * (1.0 - c)

    return right, down, fwd


def rotation_matrix(yaw_deg: float, pitch_deg: float, roll_deg: float,
                    roll_sign: float = ROLL_SIGN) -> np.ndarray:
    """Camera-to-world rotation, columns [right, down, forward]."""
    right, down, fwd = camera_basis(yaw_deg, pitch_deg, roll_deg, roll_sign)
    return np.stack([right, down, fwd], axis=1)


def rotate_vector(v: np.ndarray, omega: np.ndarray) -> np.ndarray:
    """Rotate ``v`` by the axis-angle vector ``omega`` (Rodrigues, exact)."""
    theta = float(np.linalg.norm(omega))
    if theta < 1e-15:
        return v
    k = omega / theta
    c, s = math.cos(theta), math.sin(theta)
    return v * c + np.cross(k, v) * s + k * float(k @ v) * (1.0 - c)


def camera_basis_corrected(yaw_deg: float, pitch_deg: float, roll_deg: float,
                           delta_omega=None, roll_sign: float = ROLL_SIGN):
    """Camera basis with an optional small world-frame rotation correction applied.

    The correction is an axis-angle vector rather than a yaw/pitch pair on purpose. A
    tile pointing at the nadir has almost no image response to a yaw change -- yaw and
    roll become degenerate there -- so solving for yaw corrections makes the pole tile
    ill-conditioned. An axis-angle correction has no such singularity.
    """
    right, down, fwd = camera_basis(yaw_deg, pitch_deg, roll_deg, roll_sign)
    if delta_omega is None:
        return right, down, fwd
    w = np.asarray(delta_omega, dtype=np.float64)
    if float(w @ w) < 1e-30:
        return right, down, fwd
    return (rotate_vector(right, w), rotate_vector(down, w), rotate_vector(fwd, w))


def equirect_direction(theta: np.ndarray, phi: np.ndarray) -> np.ndarray:
    """World directions for longitude ``theta`` and latitude ``phi``, both radians."""
    cp = np.cos(phi)
    return np.stack([cp * np.sin(theta), np.sin(phi), cp * np.cos(theta)], axis=-1)


def equirect_angles(width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
    """Pixel-centre longitude and latitude arrays for a ``width`` x ``height`` canvas.

    Longitude runs -pi..pi with 0 at North; latitude runs +pi/2 at the top row to
    -pi/2 at the bottom. A level panorama therefore puts the horizon on row
    ``height // 2`` exactly, which is the cheapest available correctness check.
    """
    theta = ((np.arange(width) + 0.5) / width) * 2.0 * math.pi - math.pi
    phi = math.pi / 2.0 - ((np.arange(height) + 0.5) / height) * math.pi
    return theta, phi


def tile_bbox(forward: np.ndarray, cam: Camera, width: int, height: int,
              margin_px: int = 8) -> tuple[int, int, list[tuple[int, int]]]:
    """Rows and column ranges of the equirect canvas a tile can possibly touch.

    Restricting each tile to its own footprint is the single biggest speedup in the
    stitcher: a tile covers roughly 1/15 of the sphere, so evaluating it over the
    whole canvas wastes most of the work (see docs/GROUND_TRUTH.md section 12).

    Returns ``(row_start, row_end, [(col_start, col_end), ...])``. Two column ranges
    are returned when the tile straddles the +/-180 seam; the caller must handle
    both. A tile whose cap reaches a pole gets the full column range, because every
    longitude is then in view.
    """
    hd = cam.half_diagonal_rad
    elev = math.asin(max(-1.0, min(1.0, float(forward[1]))))
    azim = math.atan2(float(forward[0]), float(forward[2]))

    elev_max = min(math.pi / 2.0, elev + hd)
    elev_min = max(-math.pi / 2.0, elev - hd)
    r0 = max(0, int(math.floor((math.pi / 2.0 - elev_max) / math.pi * height)) - margin_px)
    r1 = min(height, int(math.ceil((math.pi / 2.0 - elev_min) / math.pi * height)) + margin_px)

    cos_e = math.cos(elev)
    if cos_e <= math.sin(hd) + 1e-9:
        return r0, r1, [(0, width)]  # cap covers a pole -> all longitudes

    dlon = math.asin(min(1.0, math.sin(hd) / cos_e))
    c0 = int(math.floor((azim - dlon + math.pi) / (2.0 * math.pi) * width)) - margin_px
    c1 = int(math.ceil((azim + dlon + math.pi) / (2.0 * math.pi) * width)) + margin_px

    if c1 - c0 >= width:
        return r0, r1, [(0, width)]
    if c0 < 0:
        return r0, r1, [(c0 % width, width), (0, c1)]
    if c1 > width:
        return r0, r1, [(c0, width), (0, c1 - width)]
    return r0, r1, [(c0, c1)]


def recommended_width(cam: Camera, cap: int = 8192) -> int:
    """Equirect width that matches the tiles' native angular resolution.

    At ``f_px`` pixels per radian a full 360 deg turn spans ``2*pi*f_px`` pixels --
    9556 px for this camera. Rounded down to a power of two that GPUs will accept as
    a texture, that is 8192. Picking a width without this justification either throws
    away resolution or invents detail that is not there.
    """
    native = int(round(2.0 * math.pi * cam.f_px))
    w = 1
    while w * 2 <= min(native, cap):
        w *= 2
    return w
