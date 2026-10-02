"""Typesetting helpers for the print plate.

Pillow is a drawing library, not a typesetting engine, and this build has no raqm, so
there is no text shaping, no letterspacing and no small-caps. Each of those has to be
synthesised a glyph at a time. That is fine -- a caption is a few dozen characters -- but
it has to be done explicitly rather than assumed.

Everything is measured in pixels at a given DPI. Sizes are specified in points so the
design holds its proportions from a 5x7 to a 24x36 print: ``px = pt * dpi / 72``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from PIL import ImageDraw, ImageFont

FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")

#: Fallback chains by role. First readable file wins, so this degrades on a machine with a
#: different font set rather than crashing.
FONT_STACKS: dict[str, tuple[str, ...]] = {
    # Tabular figures matter for the coordinate block: the digits must sit in a column.
    # Consolas, Palatino and Lucida Bright all have them; Georgia does NOT -- its
    # old-style figures hang below the baseline and vary in width, which is beautiful in
    # running text and wrong in a data table.
    "mono": ("consola.ttf", "cour.ttf", "DejaVuSansMono.ttf"),
    "mono_bold": ("consolab.ttf", "courbd.ttf", "DejaVuSansMono-Bold.ttf"),
    "serif": ("constan.ttf", "georgia.ttf", "times.ttf", "DejaVuSerif.ttf"),
    "serif_bold": ("constanb.ttf", "georgiab.ttf", "timesbd.ttf", "DejaVuSerif-Bold.ttf"),
    "serif_tab": ("LBRITE.TTF", "pala.ttf", "constan.ttf"),
    "sans": ("segoeui.ttf", "calibri.ttf", "arial.ttf", "DejaVuSans.ttf"),
    "sans_semibold": ("seguisb.ttf", "calibrib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"),
}

_CACHE: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


class FontUnavailable(RuntimeError):
    pass


def pt_to_px(points: float, dpi: int) -> int:
    """Points to pixels. 1 pt = 1/72 inch by definition."""
    return max(1, int(round(points * dpi / 72.0)))


def load_font(role: str, px: int) -> ImageFont.FreeTypeFont:
    """Load a font for a role at a pixel size, trying each candidate in turn."""
    key = (role, px)
    if key in _CACHE:
        return _CACHE[key]
    for name in FONT_STACKS.get(role, ()):
        path = name if os.path.isabs(name) else os.path.join(FONT_DIR, name)
        if not os.path.exists(path):
            continue
        try:
            font = ImageFont.truetype(path, px)
        except OSError:
            continue
        _CACHE[key] = font
        return font
    raise FontUnavailable(
        f"no font found for role {role!r}; tried {', '.join(FONT_STACKS.get(role, ()))} "
        f"in {FONT_DIR}")


def has_glyphs(font: ImageFont.FreeTypeFont, text: str) -> bool:
    """Whether every non-space character in ``text`` actually renders.

    Worth checking before using typographic niceties: the thin space U+2009 is missing
    from Consolas and Segoe UI on this machine, and a missing glyph draws as a blank or a
    box rather than failing loudly.
    """
    for ch in text:
        if ch.isspace():
            continue
        try:
            if font.getmask(ch).getbbox() is None:
                return False
        except Exception:
            return False
    return True


def text_width(draw: ImageDraw.ImageDraw, text: str,
               font: ImageFont.FreeTypeFont, tracking: float = 0.0) -> float:
    """Width of ``text``, including synthesised letterspacing."""
    if not text:
        return 0.0
    if tracking == 0.0:
        return float(draw.textlength(text, font=font))
    return float(sum(draw.textlength(c, font=font) for c in text)
                 + tracking * (len(text) - 1))


def draw_tracked(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str,
                 font: ImageFont.FreeTypeFont, fill, tracking: float = 0.0,
                 anchor: str = "ls") -> float:
    """Draw ``text`` with letterspacing, returning the width drawn.

    Pillow has no tracking, so with a non-zero value each glyph is placed individually.
    Kerning pairs are lost when doing that, which is an acceptable trade for the small
    letterspaced labels this is used on -- and tracked capitals barely kern anyway.
    ``anchor`` is applied to the run as a whole, not to each glyph.
    """
    x, y = xy
    width = text_width(draw, text, font, tracking)

    if anchor[0] == "m":
        x -= width / 2.0
    elif anchor[0] == "r":
        x -= width
    vert = anchor[1] if len(anchor) > 1 else "s"

    if tracking == 0.0:
        draw.text((x, y), text, font=font, fill=fill, anchor="l" + vert)
        return width

    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill, anchor="l" + vert)
        x += draw.textlength(ch, font=font) + tracking
    return width


def draw_small_caps(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str,
                    font_full: ImageFont.FreeTypeFont,
                    font_small: ImageFont.FreeTypeFont, fill,
                    tracking: float = 0.0, anchor: str = "ls") -> float:
    """Synthesised small caps: capitals at full size, lower case as smaller capitals.

    Real small caps are a separate set of glyphs designed to match the capitals' weight.
    Synthesising them by shrinking capitals makes slightly light letters, which is why the
    small size is 0.78 of the full rather than something smaller -- it keeps the colour of
    the line close enough to pass at caption size.
    """
    runs = [(ch.upper(), font_full if ch.isupper() or not ch.isalpha() else font_small)
            for ch in text]
    width = sum(draw.textlength(c, font=f) for c, f in runs) + tracking * max(len(runs) - 1, 0)

    x, y = xy
    if anchor[0] == "m":
        x -= width / 2.0
    elif anchor[0] == "r":
        x -= width
    vert = anchor[1] if len(anchor) > 1 else "s"

    for ch, f in runs:
        draw.text((x, y), ch, font=f, fill=fill, anchor="l" + vert)
        x += draw.textlength(ch, font=f) + tracking
    return width


@dataclass
class TypeScale:
    """Pixel sizes for every role on the plate, derived from one base size.

    Keeping them proportional to a single number is what lets the same design hold from a
    small print to a large one without re-tuning by hand.
    """

    dpi: int
    base_pt: float = 9.0

    @property
    def value_px(self) -> int:
        return pt_to_px(self.base_pt, self.dpi)

    @property
    def label_px(self) -> int:
        return pt_to_px(self.base_pt * 0.72, self.dpi)

    @property
    def title_px(self) -> int:
        return pt_to_px(self.base_pt * 1.55, self.dpi)

    @property
    def footnote_px(self) -> int:
        return pt_to_px(self.base_pt * 0.62, self.dpi)

    @property
    def smallcap_px(self) -> int:
        return pt_to_px(self.base_pt * 0.72 * 0.78, self.dpi)

    @property
    def line_px(self) -> int:
        """Leading for the data block: 1.55x looks airy enough to read as a label list."""
        return int(round(self.value_px * 1.55))
