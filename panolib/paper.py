"""Paper sizes, fitting and the honest-resolution question.

The one judgement call in here is what to do when the source cannot fill the requested
print. Silently upscaling is the common choice and the wrong one: it produces a file that
looks right on screen and soft on paper, and the user only finds out after paying for it.
This warns, states the size the file can actually carry, and upscales only within a
bounded factor.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

MM_PER_INCH = 25.4

#: Named sizes, in inches, long edge second. Metric sizes are converted from millimetres.
NAMED_SIZES: dict[str, tuple[float, float]] = {
    "5x7": (5.0, 7.0),
    "6x4": (4.0, 6.0),
    "8x10": (8.0, 10.0),
    "11x14": (11.0, 14.0),
    "12x18": (12.0, 18.0),
    "16x20": (16.0, 20.0),
    "16x24": (16.0, 24.0),
    "20x30": (20.0, 30.0),
    "24x36": (24.0, 36.0),
    "a5": (148 / MM_PER_INCH, 210 / MM_PER_INCH),
    "a4": (210 / MM_PER_INCH, 297 / MM_PER_INCH),
    "a3": (297 / MM_PER_INCH, 420 / MM_PER_INCH),
    "a2": (420 / MM_PER_INCH, 594 / MM_PER_INCH),
    "a1": (594 / MM_PER_INCH, 841 / MM_PER_INCH),
    # panoramic ratios, sized to common roll widths
    "pano-2to1": (12.0, 24.0),
    "pano-3to1": (10.0, 30.0),
    "square": (12.0, 12.0),
}

_SIZE_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*(in|inch|inches|mm|cm)?\s*$",
    re.IGNORECASE)


def _px(inches: float, dpi: int) -> int:
    """Inches to pixels, rounding half UP.

    Not ``round()``: Python rounds halves to even, so ``round(82.5)`` is 82 and
    ``round(3507.5)`` is 3508 -- the same fractional part lands differently depending on
    the integer beside it. For paper arithmetic that produces sheets a pixel short in a
    way that is maddening to track down.
    """
    return math.floor(inches * dpi + 0.5)


@dataclass(frozen=True)
class Paper:
    """A print surface in inches, plus the pixel grid it implies at a given DPI."""

    width_in: float
    height_in: float
    dpi: int
    name: str = "custom"

    @property
    def width_px(self) -> int:
        return _px(self.width_in, self.dpi)

    @property
    def height_px(self) -> int:
        return _px(self.height_in, self.dpi)

    @property
    def aspect(self) -> float:
        return self.width_in / self.height_in

    @property
    def short_edge_px(self) -> int:
        return min(self.width_px, self.height_px)

    def describe(self) -> str:
        mm_w = self.width_in * MM_PER_INCH
        mm_h = self.height_in * MM_PER_INCH
        return (f'{self.width_in:g}″ × {self.height_in:g}″ '
                f'({mm_w:.0f} × {mm_h:.0f} mm) at {self.dpi} dpi '
                f'= {self.width_px} × {self.height_px} px')


def parse_size(spec: str, dpi: int = 300, orientation: str = "auto",
               source_aspect: float | None = None) -> Paper:
    """Parse a size: a name like ``16x24`` or ``a3``, or a free form like ``300x400mm``.

    ``orientation`` of ``auto`` turns the paper to match the image, which is almost always
    what is wanted and avoids a landscape photograph being letterboxed onto portrait paper.
    """
    key = spec.strip().lower().replace(" ", "")
    if key in NAMED_SIZES:
        w, h = NAMED_SIZES[key]
        name = key
    else:
        m = _SIZE_RE.match(spec)
        if not m:
            raise ValueError(
                f"cannot read paper size {spec!r}. Use a name "
                f"({', '.join(sorted(NAMED_SIZES)[:6])} ...) or a form like "
                f"'16x24in', '300x400mm', '30x40cm'.")
        a, b, unit = float(m.group(1)), float(m.group(2)), (m.group(3) or "in").lower()
        if unit.startswith("mm"):
            a, b = a / MM_PER_INCH, b / MM_PER_INCH
        elif unit.startswith("cm"):
            a, b = a * 10 / MM_PER_INCH, b * 10 / MM_PER_INCH
        w, h = a, b
        name = "custom"

    if orientation == "auto":
        want_landscape = (source_aspect or 1.0) >= 1.0
        is_landscape = w > h
        if want_landscape != is_landscape:
            w, h = h, w
    elif orientation == "landscape" and w < h:
        w, h = h, w
    elif orientation == "portrait" and w > h:
        w, h = h, w

    if dpi <= 0:
        raise ValueError("dpi must be positive")
    return Paper(width_in=w, height_in=h, dpi=dpi, name=name)


@dataclass
class FitReport:
    """What fitting the source into a box implies for print quality."""

    target_w: int
    target_h: int
    source_w: int
    source_h: int
    scale: float                 # >1 means upscaling
    effective_dpi: float         # the real dpi the source delivers at this print size
    cropped_fraction: float      # how much of the source the crop discards
    warning: str | None = None


#: Below this the print visibly softens; 300 is the usual lab target.
GOOD_DPI = 300.0
ACCEPTABLE_DPI = 180.0
#: Beyond this, upscaling is inventing detail rather than resampling it.
MAX_UPSCALE = 1.6


def fit_box(source_w: int, source_h: int, box_w: int, box_h: int,
            mode: str = "contain") -> tuple[int, int, tuple[int, int, int, int]]:
    """Return the drawn size and the source crop box for a fit mode.

    ``contain`` fits the whole image inside the box, so nothing is lost and the box may
    not be filled. ``cover`` fills the box and crops the overflow, centred.
    """
    if source_w <= 0 or source_h <= 0:
        raise ValueError("source has no pixels")
    sa = source_w / source_h
    ba = box_w / box_h

    if mode == "cover":
        if sa > ba:
            # source is wider: crop the sides
            new_w = int(round(source_h * ba))
            x0 = (source_w - new_w) // 2
            crop = (x0, 0, x0 + new_w, source_h)
        else:
            new_h = int(round(source_w / ba))
            y0 = (source_h - new_h) // 2
            crop = (0, y0, source_w, y0 + new_h)
        return box_w, box_h, crop

    # contain
    crop = (0, 0, source_w, source_h)
    if sa > ba:
        return box_w, max(1, int(round(box_w / sa))), crop
    return max(1, int(round(box_h * sa))), box_h, crop


def assess_fit(source_w: int, source_h: int, draw_w: int, draw_h: int,
               crop: tuple[int, int, int, int], paper: Paper) -> FitReport:
    """Judge whether this print is honestly achievable from this source."""
    crop_w = crop[2] - crop[0]
    crop_h = crop[3] - crop[1]
    scale = draw_w / max(crop_w, 1)
    effective_dpi = paper.dpi / max(scale, 1e-9)
    cropped = 1.0 - (crop_w * crop_h) / max(source_w * source_h, 1)

    warning = None
    if scale > MAX_UPSCALE:
        warning = (
            f"the source would be enlarged {scale:.1f}x for this size, which invents "
            f"detail rather than resampling it. At {GOOD_DPI:.0f} dpi this file supports "
            f"about {crop_w / GOOD_DPI:.1f}″ × {crop_h / GOOD_DPI:.1f}″; "
            f"at {ACCEPTABLE_DPI:.0f} dpi, {crop_w / ACCEPTABLE_DPI:.1f}″ × "
            f"{crop_h / ACCEPTABLE_DPI:.1f}″.")
    elif effective_dpi < ACCEPTABLE_DPI:
        warning = (f"effective resolution is {effective_dpi:.0f} dpi, below the "
                   f"{ACCEPTABLE_DPI:.0f} dpi that still looks crisp on paper.")
    return FitReport(draw_w, draw_h, source_w, source_h, scale, effective_dpi,
                     cropped, warning)


def max_print_size(source_w: int, source_h: int, dpi: float = GOOD_DPI) -> tuple[float, float]:
    """The largest print, in inches, this source fills at ``dpi`` without enlargement."""
    return source_w / dpi, source_h / dpi
