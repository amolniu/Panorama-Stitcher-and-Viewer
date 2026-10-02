"""Geometric refinement: solving small per-tile angle corrections from the overlaps.

DJI's recorded gimbal angles carry roughly 0.5-2 degrees of error. At 1519 px per
radian that is 13-53 px of misregistration, which shows up as doubled edges -- the
ghosting you see on rooftops, shorelines and tree lines.

The fix is to treat the metadata as a PRIOR rather than as truth: measure how far
adjacent tiles actually disagree in their overlaps, and solve for the small rotation of
each tile that best explains all those measurements at once.

## The measurement model

For a pair of overlapping tiles, both are rendered into a common gnomonic tangent plane
centred on their shared direction ``c``, with an orthonormal basis ``(e1, e2)``
perpendicular to ``c``. Phase correlation then gives the residual shift between them.

If tile ``k``'s true camera-to-world rotation is ``exp([w_k]x) R_k`` but we render with
``R_k``, the rendered content is displaced in the tangent plane by::

    m_k = f * [ (w_k x c) . e1 ,  (w_k x c) . e2 ]

so the shift between the two rendered patches is::

    t_ij = m_j - m_i = f * P[ (w_j - w_i) x c ]

which is LINEAR in the corrections. Each tile's correction is written in ITS OWN frame,
as a rotation about its right axis and one about its up axis::

    w_k = alpha_k * r_hat_k  +  beta_k * u_hat_k

giving two unknowns per tile and two equations per pair. A sphere yields far more pairs
than unknowns, so the system is well over-determined.

Two things about that parameterisation are load-bearing, and both were learned the hard
way on the real archive (see docs/GROUND_TRUTH.md section 13):

* **Not yaw and pitch.** Yaw about world up is nearly unobservable for the nadir tile --
  a camera pointing straight down barely moves when it yaws -- so the solver can hand it
  an arbitrary value for free. It did exactly that, assigning 3.09 degrees to a tile that
  appeared in 7 of 91 pairs, which tripped the safety clamp and threw away good work.
  Rotations about a tile's own axes always tilt its optical axis, so they are always
  observable.
* **The global rotation must be removed.** Every measurement is a difference between two
  tiles, so rotating the whole panorama is invisible to all of them -- a real
  three-dimensional null space. Left in, it inflated every correction by a common 2-3
  degrees. Spinning the finished panorama does not reduce ghosting, so it is subtracted.

Sign conventions here are derived, then PINNED BY TEST: ``tests/test_refine.py`` injects a
known +1.0 degree error into one tile and requires the solver to put the optical axis back
within 0.2 degrees of truth. A flipped correlation sign roughly doubles the error instead
of removing it. That test caught a real sign inversion during development, and it is the
reason to trust this module.

## The guard

Refinement is only allowed to help. Every run scores agreement across a FROZEN set of
pairs before and after, and keeps the metadata-only placement unless the score genuinely
improves. A refinement that cannot prove it helped does not ship, and the reason is
recorded in the manifest.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field, asdict

import numpy as np
from PIL import Image

from .exif import TileMeta
from .geometry import Camera, camera_basis_corrected

Image.MAX_IMAGE_PIXELS = None

# --- measurement parameters (see docs/BUILD_SPEC.md section 9) --------------------
PATCH_PX = 384                 # tangent-plane patch size
PATCH_SCALE_PX_PER_DEG = 20.0  # patch resolution; 384 px covers 19.2 deg
MAX_SHIFT_DEG = 3.0            # reject correlations further out than this
MIN_VALID_FRAC = 0.15          # both patches must cover this much of the window
MIN_TEXTURE = 0.010            # std(high-passed)/mean(patch); rejects blank sky
MIN_PSR = 4.5                  # peak-to-sidelobe ratio gate
PAIR_MIN_SEP_DEG = 12.0
PAIR_MAX_SEP_DEG = 58.0

# --- guard thresholds ------------------------------------------------------------
# The real protection is MIN_SCORE_GAIN: a correction ships only if it demonstrably
# improves agreement over a frozen set of pairs. MAX_CORRECTION_DEG is a second-line
# sanity veto against an absurd solve, not the primary defence.
#
# It was originally 2.0 deg, on the reasoning that DJI's angles are good to 0.5-2 deg.
# Measured on this archive that turned out to be too tight and rejected genuinely good
# refinements: 100_0708 and 100_0820 both needed up to 2.8 deg, and applying it raised
# their pair agreement by ~330% while cutting the ghosting metric by 10-20%. On
# 100_0708 the first three tiles all shifted ~2.5 deg together, which is the aircraft
# drifting during that column of the capture -- a real error, not a bad solve. Raised
# to 4.0 deg so honest corrections of that size survive.
MAX_CORRECTION_DEG = 4.0
MIN_SCORE_GAIN = 1.005         # refined must beat baseline by 0.5%
MIN_PAIR_RETENTION = 0.60      # enough evidence to trust the solve

DEG = math.pi / 180.0


@dataclass
class PairObs:
    """One measured overlap between two tiles."""

    i: int
    j: int
    c: np.ndarray                 # tangent-plane centre (unit world direction)
    e1: np.ndarray
    e2: np.ndarray
    du: float                     # measured shift, patch pixels
    dv: float
    psr: float                    # correlation confidence
    n: int                        # overlapping pixels, used as a weight
    ncc0: float                   # baseline agreement, free from the same render
    sep_deg: float


@dataclass
class RefinementResult:
    applied: bool
    reason: str
    baseline_score: float = 0.0
    refined_score: float = 0.0
    axis_shift_deg: list[float] = field(default_factory=list)
    pairs_measured: int = 0
    pairs_possible: int = 0
    max_correction_deg: float = 0.0
    rms_correction_deg: float = 0.0
    iterations: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["axis_shift_deg"] = [round(x, 4) for x in self.axis_shift_deg]
        return d


def tile_basis(t: TileMeta):
    """Current basis of a tile, including any correction it already carries."""
    return camera_basis_corrected(t.yaw, t.pitch, t.roll,
                                  getattr(t, "delta_omega", None))


# ---------------------------------------------------------------- image helpers

def load_gray(tiles: list[TileMeta]) -> list[np.ndarray | None]:
    """Grayscale tiles as uint8. Kept 8-bit deliberately: 26 full-size float32 tiles
    would be ~300 MB per worker, and phase correlation gains nothing from the extra
    precision."""
    out: list[np.ndarray | None] = []
    for t in tiles:
        try:
            with Image.open(t.path) as im:
                out.append(np.asarray(im.convert("L"), dtype=np.uint8))
        except (OSError, ValueError):
            out.append(None)
    return out


def tangent_basis(c: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """An orthonormal basis perpendicular to ``c``, stable near the poles."""
    up = np.array([0.0, 1.0, 0.0])
    if abs(float(c @ up)) > 0.99:
        up = np.array([0.0, 0.0, 1.0])
    e1 = np.cross(up, c)
    e1 /= max(np.linalg.norm(e1), 1e-12)
    e2 = np.cross(c, e1)
    e2 /= max(np.linalg.norm(e2), 1e-12)
    return e1, e2


def render_patch(gray: np.ndarray, R_cols: tuple[np.ndarray, np.ndarray, np.ndarray],
                 cam: Camera, c: np.ndarray, e1: np.ndarray, e2: np.ndarray,
                 patch_px: int = PATCH_PX,
                 scale: float = PATCH_SCALE_PX_PER_DEG) -> tuple[np.ndarray, np.ndarray]:
    """Render one tile into the tangent plane at ``c``. Returns (patch, valid mask)."""
    right, down, fwd = R_cols
    f_patch = scale / DEG                       # patch pixels per radian
    half = patch_px / 2.0
    ax = (np.arange(patch_px) + 0.5 - half) / f_patch
    AX, AY = np.meshgrid(ax, ax)

    d = (c[None, None, :]
         + AX[..., None] * e1[None, None, :]
         + AY[..., None] * e2[None, None, :])
    d /= np.linalg.norm(d, axis=-1, keepdims=True)

    zc = d @ fwd
    ok = zc > 1e-6
    safe = np.where(ok, zc, 1.0)
    u = cam.f_px * (d @ right) / safe + gray.shape[1] / 2.0
    v = cam.f_px * (d @ down) / safe + gray.shape[0] / 2.0

    ih, iw = gray.shape
    valid = ok & (u >= 0) & (u <= iw - 1) & (v >= 0) & (v <= ih - 1)
    patch = np.zeros((patch_px, patch_px), dtype=np.float32)
    if not valid.any():
        return patch, valid

    uu = u[valid]
    vv = v[valid]
    x0 = uu.astype(np.int32)
    y0 = vv.astype(np.int32)
    x1 = np.minimum(x0 + 1, iw - 1)
    y1 = np.minimum(y0 + 1, ih - 1)
    fx = (uu - x0).astype(np.float32)
    fy = (vv - y0).astype(np.float32)
    g = gray.astype(np.float32, copy=False)
    patch[valid] = (g[y0, x0] * (1 - fx) * (1 - fy) + g[y0, x1] * fx * (1 - fy)
                    + g[y1, x0] * (1 - fx) * fy + g[y1, x1] * fx * fy) / 255.0
    return patch, valid


_HANN_CACHE: dict[int, np.ndarray] = {}


def hann2d(n: int) -> np.ndarray:
    if n not in _HANN_CACHE:
        w = np.hanning(n).astype(np.float32)
        _HANN_CACHE[n] = np.outer(w, w)
    return _HANN_CACHE[n]


def prepare(patch: np.ndarray, valid: np.ndarray) -> tuple[np.ndarray, float]:
    """High-pass and window a patch, returning it and its texture measure.

    High-passing removes the exposure and vignetting differences between tiles, so the
    correlation responds to structure rather than brightness. The mean is taken only
    over valid pixels so the invalid border does not drag it down.
    """
    from scipy.ndimage import uniform_filter
    mean_val = float(patch[valid].mean()) if valid.any() else 0.0
    filled = np.where(valid, patch, mean_val)
    low = uniform_filter(filled, size=max(3, patch.shape[0] // 12))
    high = (filled - low) * valid
    texture = float(high[valid].std()) / max(mean_val, 1e-6) if valid.any() else 0.0
    return high * hann2d(patch.shape[0]), texture


def phase_correlate(a: np.ndarray, b: np.ndarray) -> tuple[float, float, float]:
    """Return (du, dv, psr) such that ``b(p) ~ a(p + (du, dv))``.

    Sign derivation, which is worth writing out because getting it backwards produces
    code that runs and converges while making the panorama worse:

        b(p) = a(p - s)                      definition of a shift by s
        F_b(k) = F_a(k) * exp(-2i*pi*k*s/N)
        cross  = F_a * conj(F_b) = |F_a|^2 * exp(+2i*pi*k*s/N)
        ifft(exp(+2i*pi*k*s/N))[n] = delta(n + s)    -> peak sits at n = -s

    We want ``t`` with ``b(p) = a(p + t)``, i.e. ``s = -t``, so the peak sits at
    ``-s = +t``: the peak IS the answer, with no negation.

    ``tests/test_refine.py::test_phase_correlation_sign`` pins this against a synthetic
    shift, and the injected-error tests pin the rest of the chain.
    """
    n = a.shape[0]
    Fa = np.fft.rfft2(a)
    Fb = np.fft.rfft2(b)
    cross = Fa * np.conj(Fb)
    mag = np.abs(cross)
    cross /= np.where(mag > 1e-12, mag, 1.0)
    corr = np.fft.irfft2(cross, s=a.shape)

    peak = int(np.argmax(corr))
    py, px = divmod(peak, n)
    peak_val = float(corr[py, px])

    # peak-to-sidelobe ratio, excluding a small box around the peak
    masked = corr.copy()
    r = max(2, n // 64)
    ys = [(py + dy) % n for dy in range(-r, r + 1)]
    xs = [(px + dx) % n for dx in range(-r, r + 1)]
    masked[np.ix_(ys, xs)] = np.nan
    side = masked[~np.isnan(masked)]
    psr = (peak_val - float(side.mean())) / max(float(side.std()), 1e-9)

    # sub-pixel refinement by parabolic fit on each axis
    def parabolic(prev: float, cur: float, nxt: float) -> float:
        denom = prev - 2.0 * cur + nxt
        return 0.0 if abs(denom) < 1e-12 else 0.5 * (prev - nxt) / denom

    sy = parabolic(float(corr[(py - 1) % n, px]), peak_val, float(corr[(py + 1) % n, px]))
    sx = parabolic(float(corr[py, (px - 1) % n]), peak_val, float(corr[py, (px + 1) % n]))

    # unwrap the circular shift into a signed offset
    dx = px + sx
    dy = py + sy
    if dx > n / 2:
        dx -= n
    if dy > n / 2:
        dy -= n
    return float(dx), float(dy), float(psr)


def zero_lag_ncc(a: np.ndarray, b: np.ndarray, valid: np.ndarray) -> float:
    """Contrast-normalised agreement between two prepared patches, no shift allowed.

    This is the guard's currency: it rises only when the two tiles actually line up.
    """
    if valid.sum() < 64:
        return 0.0
    av = a[valid]
    bv = b[valid]
    av = av - av.mean()
    bv = bv - bv.mean()
    na = math.sqrt(float((av * av).sum()))
    nb = math.sqrt(float((bv * bv).sum()))
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return float((av * bv).sum() / (na * nb))


# ---------------------------------------------------------------- measurement

def candidate_pairs(tiles: list[TileMeta]) -> list[tuple[int, int, float]]:
    """Tile pairs close enough to overlap but not so close as to be redundant.

    Tiles with no gimbal metadata are excluded. They default to yaw = pitch = 0, so
    they would look like a tile pointing due north and pair up with whatever genuinely
    points that way -- feeding a meaningless measurement into the solve.
    """
    fwds = []
    for t in tiles:
        _, _, f = tile_basis(t)
        fwds.append(f)
    out = []
    for i in range(len(tiles)):
        if not tiles[i].has_gimbal:
            continue
        for j in range(i + 1, len(tiles)):
            if not tiles[j].has_gimbal:
                continue
            cos = float(np.clip(fwds[i] @ fwds[j], -1.0, 1.0))
            sep = math.degrees(math.acos(cos))
            if PAIR_MIN_SEP_DEG <= sep <= PAIR_MAX_SEP_DEG:
                out.append((i, j, sep))
    return out


def measure(tiles: list[TileMeta], grays: list[np.ndarray | None], cam: Camera,
            pairs: list[tuple[int, int, float]]) -> list[PairObs]:
    """Measure the residual shift for every viable pair."""
    bases = [tile_basis(t) for t in tiles]
    fwds = [b[2] for b in bases]
    obs: list[PairObs] = []
    max_shift_px = MAX_SHIFT_DEG * PATCH_SCALE_PX_PER_DEG

    for i, j, sep in pairs:
        gi, gj = grays[i], grays[j]
        if gi is None or gj is None:
            continue
        c = fwds[i] + fwds[j]
        norm = np.linalg.norm(c)
        if norm < 1e-9:
            continue
        c = c / norm
        e1, e2 = tangent_basis(c)

        pi, vi = render_patch(gi, bases[i], cam, c, e1, e2)
        pj, vj = render_patch(gj, bases[j], cam, c, e1, e2)
        both = vi & vj
        n = int(both.sum())
        if n < MIN_VALID_FRAC * PATCH_PX * PATCH_PX:
            continue

        ai, ti = prepare(pi, both)
        aj, tj = prepare(pj, both)
        if min(ti, tj) < MIN_TEXTURE:
            continue                       # featureless sky: nothing to align, and
                                           # nothing will look wrong either

        du, dv, psr = phase_correlate(ai, aj)
        if psr < MIN_PSR:
            continue
        if abs(du) > max_shift_px or abs(dv) > max_shift_px:
            continue

        obs.append(PairObs(i=i, j=j, c=c, e1=e1, e2=e2, du=du, dv=dv,
                           psr=psr, n=n, ncc0=zero_lag_ncc(ai, aj, both),
                           sep_deg=sep))
    return obs


# ---------------------------------------------------------------- solve

def build_system(tiles: list[TileMeta], obs: list[PairObs]) -> tuple[np.ndarray, np.ndarray]:
    """Assemble ``A x = t`` over per-tile corrections in each tile's OWN frame.

    Each tile gets two unknowns: a rotation about its right axis and one about its up
    axis. Both tilt the optical axis, which is what translates the image, so both are
    strongly observable no matter where the tile points.

    Correcting yaw about world-up instead looks natural but is singular at the poles:
    the nadir tile's image barely responds to yaw, so the solver can hand it an
    arbitrarily large value at almost no cost. That is exactly what happened on the
    real archive -- the nadir tile was handed 3.09 degrees and tripped the clamp.

    The rotation about each tile's own optical axis (roll) is deliberately left out. It
    does not translate the patch centre, so a centre-shift measurement cannot see it.
    """
    n_tiles = len(tiles)
    f_patch = PATCH_SCALE_PX_PER_DEG / DEG
    bases = [tile_basis(t) for t in tiles]

    A = np.zeros((2 * len(obs), 2 * n_tiles), dtype=np.float64)
    b = np.zeros(2 * len(obs), dtype=np.float64)

    for k, o in enumerate(obs):
        ri, di, _ = bases[o.i]
        rj, dj, _ = bases[o.j]
        ui, uj = -di, -dj                       # up = -down
        # d(shift)/d(w) = f * P[w x c]
        gri, gui = np.cross(ri, o.c), np.cross(ui, o.c)
        grj, guj = np.cross(rj, o.c), np.cross(uj, o.c)

        for row, e in ((2 * k, o.e1), (2 * k + 1, o.e2)):
            A[row, o.j] += f_patch * float(grj @ e)                # alpha_j
            A[row, o.i] -= f_patch * float(gri @ e)                # alpha_i
            A[row, n_tiles + o.j] += f_patch * float(guj @ e)      # beta_j
            A[row, n_tiles + o.i] -= f_patch * float(gui @ e)      # beta_i
        b[2 * k] = o.du
        b[2 * k + 1] = o.dv
    return A, b


def solve_corrections(tiles: list[TileMeta], obs: list[PairObs],
                      lam: float = 1e-6, iters: int = 4) -> tuple[np.ndarray, np.ndarray, int]:
    """Solve for per-tile yaw/pitch corrections, robustly.

    Tikhonov regularisation does double duty: it fixes the gauge (a global yaw shift is
    unobservable from relative measurements alone, so the system is rank-deficient
    without it) and it keeps the solution near the metadata prior, which is what we
    want -- the metadata is mostly right.

    Iteratively reweighted least squares with a Huber weight handles pairs spoiled by
    moving water, foliage or vehicles, which would otherwise pull the whole solve.
    """
    n_tiles = len(tiles)
    A, b = build_system(tiles, obs)
    if A.shape[0] < 4:
        return np.zeros(n_tiles), np.zeros(n_tiles), 0

    base_w = np.array([math.sqrt(max(o.n, 1)) * min(o.psr / MIN_PSR, 3.0) for o in obs])
    w = np.repeat(base_w, 2)
    x = np.zeros(2 * n_tiles)
    used = 0
    for it in range(iters):
        Aw = A * w[:, None]
        bw = b * w
        normal = Aw.T @ Aw
        # Every measurement is a DIFFERENCE of corrections, so adding the same yaw to
        # every tile changes nothing: that direction is exactly in the null space and
        # an unregularised solve is singular. Scaling the ridge term to the system's
        # own magnitude kills the null direction without biasing the observable part
        # (a fixed lambda would be either negligible or dominant depending on how many
        # pixels happened to overlap).
        ridge = lam * float(np.trace(normal)) / max(2 * n_tiles, 1)
        lhs = normal + ridge * np.eye(2 * n_tiles)
        rhs = Aw.T @ bw
        try:
            x = np.linalg.solve(lhs, rhs)
        except np.linalg.LinAlgError:
            return np.zeros(n_tiles), np.zeros(n_tiles), it
        used = it + 1
        resid = A @ x - b
        scale = 1.4826 * float(np.median(np.abs(resid - np.median(resid)))) + 1e-6
        huber = np.clip(1.5 * scale / np.maximum(np.abs(resid), 1e-9), 0.0, 1.0)
        w = np.repeat(base_w, 2) * huber

    # Assemble the per-tile world-frame corrections, then remove the global rotation.
    # Every measurement is a difference between two tiles, so rotating the whole
    # panorama is invisible to all of them -- it is a genuine three-dimensional null
    # space, and whatever the solver puts there is arbitrary. Leaving it in inflates
    # every correction by a common amount and makes honest results look like runaway
    # ones (on the real archive it showed up as a systematic +2 to +3 degrees of pitch
    # across most tiles). Spinning the finished panorama does not reduce ghosting, so
    # the gauge is simply subtracted.
    bases = [tile_basis(t) for t in tiles]
    omega = np.zeros((n_tiles, 3), dtype=np.float64)
    for k in range(n_tiles):
        right, down, _ = bases[k]
        omega[k] = x[k] * right + x[n_tiles + k] * (-down)
    omega -= omega.mean(axis=0, keepdims=True)
    return omega, used


# ---------------------------------------------------------------- scoring / guard

def score_pairs(tiles: list[TileMeta], grays: list[np.ndarray | None], cam: Camera,
                obs: list[PairObs]) -> float:
    """Weighted zero-lag NCC over a FROZEN pair set.

    Both baseline and refined are scored over exactly the same pairs and the same
    tangent frames, so the comparison measures alignment and nothing else.
    """
    bases = [tile_basis(t) for t in tiles]
    total = 0.0
    weight = 0.0
    for o in obs:
        gi, gj = grays[o.i], grays[o.j]
        if gi is None or gj is None:
            continue
        pi, vi = render_patch(gi, bases[o.i], cam, o.c, o.e1, o.e2)
        pj, vj = render_patch(gj, bases[o.j], cam, o.c, o.e1, o.e2)
        both = vi & vj
        if both.sum() < 64:
            continue
        ai, _ = prepare(pi, both)
        aj, _ = prepare(pj, both)
        ncc = zero_lag_ncc(ai, aj, both)
        total += ncc * both.sum()
        weight += both.sum()
    return total / weight if weight else 0.0


def axis_shift_deg(tiles: list[TileMeta], omega: np.ndarray) -> np.ndarray:
    """How far each tile's optical axis actually moves, in degrees.

    This is the quantity worth clamping and reporting. A rotation component *along* the
    optical axis is a roll and shifts nothing on screen, so judging a correction by the
    raw magnitude of its axis-angle vector would overstate it.
    """
    out = np.zeros(len(tiles))
    for k, t in enumerate(tiles):
        fwd = tile_basis(t)[2]
        out[k] = float(np.linalg.norm(np.cross(omega[k], fwd))) / DEG
    return out


def apply_corrections(tiles: list[TileMeta], omega: np.ndarray) -> list[TileMeta]:
    """Return copies of ``tiles`` carrying the accumulated rotation correction."""
    import copy
    out = []
    for k, t in enumerate(tiles):
        c = copy.copy(t)
        prev = np.asarray(getattr(t, "delta_omega", None) or (0.0, 0.0, 0.0), dtype=float)
        total = prev + omega[k]          # corrections are small, so they simply add
        c.delta_omega = (float(total[0]), float(total[1]), float(total[2]))
        out.append(c)
    return out


def refine(tiles: list[TileMeta], cam: Camera | None = None,
           grays: list[np.ndarray | None] | None = None,
           verbose: bool = False) -> tuple[list[TileMeta], RefinementResult]:
    """Refine tile angles from their overlaps, or return the originals unchanged.

    Never returns something worse than the metadata-only placement: the corrections are
    accepted only if they measurably improve agreement across a frozen set of pairs and
    stay within a couple of degrees.
    """
    usable = [t for t in tiles if t.has_gimbal]
    if len(usable) < 4:
        return tiles, RefinementResult(False, "too few tiles with orientation metadata")

    if cam is None:
        cam = Camera(tiles[0].width, tiles[0].height)
    if grays is None:
        grays = load_gray(tiles)

    pairs = candidate_pairs(tiles)
    if not pairs:
        return tiles, RefinementResult(False, "no overlapping pairs")

    obs = measure(tiles, grays, cam, pairs)
    retention = len(obs) / max(len(pairs), 1)
    if retention < MIN_PAIR_RETENTION:
        return tiles, RefinementResult(
            False, f"only {len(obs)}/{len(pairs)} pairs gave a usable correlation "
                   f"({100*retention:.0f}% < {100*MIN_PAIR_RETENTION:.0f}%)",
            pairs_measured=len(obs), pairs_possible=len(pairs))

    # The baseline agreement was already computed while measuring, from exactly the
    # patches that produced the measurements -- re-rendering them here would cost a
    # third of the pass for nothing.
    weight = sum(o.n for o in obs)
    baseline = sum(o.ncc0 * o.n for o in obs) / weight if weight else 0.0

    omega, iters = solve_corrections(tiles, obs)

    shifts = axis_shift_deg(tiles, omega)
    max_corr = float(shifts.max()) if shifts.size else 0.0
    rms_corr = float(np.sqrt(np.mean(shifts ** 2))) if shifts.size else 0.0
    if max_corr > MAX_CORRECTION_DEG:
        return tiles, RefinementResult(
            False, f"correction of {max_corr:.2f} deg exceeds the {MAX_CORRECTION_DEG} deg limit",
            baseline_score=baseline, pairs_measured=len(obs), pairs_possible=len(pairs),
            max_correction_deg=max_corr, rms_correction_deg=rms_corr, iterations=iters)

    corrected = apply_corrections(tiles, omega)
    refined = score_pairs(corrected, grays, cam, obs)

    result = RefinementResult(
        applied=False, reason="", baseline_score=round(baseline, 5),
        refined_score=round(refined, 5),
        axis_shift_deg=[round(float(v), 4) for v in shifts],
        pairs_measured=len(obs), pairs_possible=len(pairs),
        max_correction_deg=round(max_corr, 4), rms_correction_deg=round(rms_corr, 4),
        iterations=iters)

    if refined > baseline * MIN_SCORE_GAIN:
        result.applied = True
        result.reason = (f"agreement {baseline:.4f} -> {refined:.4f} "
                         f"(+{100*(refined/max(baseline,1e-9)-1):.1f}%), "
                         f"max correction {max_corr:.2f} deg")
        if verbose:
            print(f"    refine: {result.reason}")
        return corrected, result

    result.reason = (f"no improvement ({baseline:.4f} -> {refined:.4f}); "
                     f"kept metadata-only placement")
    if verbose:
        print(f"    refine: {result.reason}")
    return tiles, result
