"""Composing a print-ready plate: the photograph, mounted, with a caption of record.

What the caption claims matters as much as how it looks. It states what the file records,
attributed to the camera that recorded it. It does not say the photograph is verified,
because a printed coordinate cannot establish that -- EXIF is editable with free tools.
What it genuinely does is put the claim in the open where a reader can check it against a
map, which makes a quiet alteration both more effortful and more conspicuous than leaving
the data buried in metadata nobody reads.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from fractions import Fraction

import numpy as np
from PIL import ExifTags, Image, ImageDraw
from PIL.TiffImagePlugin import IFDRational

from .capture import (Capture, format_altitude, format_datetime, format_exposure,
                      format_heading, format_latlon)
from .paper import FitReport, Paper, assess_fit, fit_box
from .typeset import (TypeScale, draw_small_caps, draw_tracked, load_font, text_width)

Image.MAX_IMAGE_PIXELS = None

#: The caption's data block when no fields are named. The print file's metadata follows
#: the same set (see print_exif), so it is defined once.
DEFAULT_FIELDS = frozenset({"position", "altitude", "recorded"})


@dataclass
class PlateStyle:
    """A complete look. Colours are RGB tuples; roles name font stacks in typeset.py."""

    name: str
    ground: tuple[int, int, int]
    ink: tuple[int, int, int]
    ink_soft: tuple[int, int, int]
    rule: tuple[int, int, int]
    title_role: str = "serif"
    label_role: str = "sans_semibold"
    value_role: str = "mono"
    foot_role: str = "serif"
    base_pt: float = 9.0
    label_tracking: float = 0.16     # as a fraction of the label pixel size
    show_rule: bool = True
    labels: bool = True              # label column, or values alone
    image_border: tuple[int, int, int] | None = None


STYLES: dict[str, PlateStyle] = {
    # Quiet, warm, bottom-weighted: a gallery label under a mounted print.
    "gallery": PlateStyle(
        name="gallery",
        ground=(250, 248, 244), ink=(26, 26, 28), ink_soft=(122, 118, 112),
        rule=(206, 200, 190),
        title_role="serif", value_role="mono", foot_role="serif",
        base_pt=9.0, show_rule=True, labels=True,
        image_border=(222, 216, 206),
    ),
    # The same information presented as instrument data.
    "survey": PlateStyle(
        name="survey",
        ground=(18, 20, 24), ink=(232, 230, 225), ink_soft=(140, 146, 156),
        rule=(62, 68, 78),
        title_role="sans_semibold", value_role="mono", foot_role="sans",
        base_pt=8.6, show_rule=True, labels=True,
        image_border=(52, 58, 68),
    ),
    # No mat: the caption sits on the image itself, over a gradient scrim.
    "overlay": PlateStyle(
        name="overlay",
        ground=(0, 0, 0), ink=(255, 255, 255), ink_soft=(205, 205, 205),
        rule=(255, 255, 255),
        title_role="serif", value_role="mono", foot_role="sans",
        base_pt=8.4, show_rule=False, labels=False,
        image_border=None,
    ),
}


@dataclass
class PlateRow:
    label: str
    value: str


# Wording is deliberate. "Recorded position" and "as recorded by the camera" describe what
# the file says; "verified" or "authenticated" would assert something a caption cannot.
FOOTNOTE = ("Position, altitude and time as recorded by the camera at capture. "
            "Metadata of this kind can be edited; this is a transcription, not a verification.")

FOOTNOTE_FULL = (
    "Every value above is transcribed from metadata inside the source file. Coordinates are "
    "WGS 84 from the aircraft's satellite receiver, good to a few metres. Height is "
    "barometric, measured from the take-off point. Camera "
    "direction is the aircraft's compass, not corrected for magnetic declination. The clock "
    "is the camera's own and records no time zone. Metadata of this kind can be edited with "
    "free tools: this plate reports what the file records. It is not a verification, a "
    "certificate, or proof that the picture was taken where, when or how the file says.")

NO_POSITION = "No position recorded in this file."

#: Appended for a stitched panorama. A plate that attests provenance while implying a
#: single untouched exposure undercuts its own point: a sphere is dozens of frames, merged
#: and tone-mapped, with the cap above about +41 degrees elevation synthesised because the
#: aircraft never photographs it.
PANORAMA_NOTE = ("Assembled from multiple frames and tone-mapped; "
                 "the sky near the zenith is synthesised.")

#: Words that would turn a transcription into a certificate. Enforced by a test.
BANNED_CLAIMS = (
    "verified", "certified", "authenticated", "authentic", "validated",
    "guaranteed", "proof of", "tamper-proof", "tamper-evident",
    "notarised", "notarized", "unaltered", "genuine",
)


def footnote_text(cap: Capture, variant: str = "short",
                  digest: str | None = None) -> str:
    """The honesty note, in the requested length, with any processing disclosed."""
    if not cap.has_position:
        base = NO_POSITION
    else:
        base = FOOTNOTE_FULL if variant == "full" else FOOTNOTE
    if cap.is_panorama:
        base = base + " " + PANORAMA_NOTE
    if digest and variant == "full":
        base = base + f" Source file sha256 {digest}…"
    return base


def require_footnote(show_footnote: bool, rows: list) -> None:
    """Kept as a no-op hook.

    This used to refuse to print recorded values without the caveat beneath them. That is
    the right default for a plate shown to strangers, but it is the owner's call for their
    own prints, so the footnote is now simply off by default and available via
    ``--footnote short`` or ``--footnote full``.
    """
    return None


def build_rows(cap: Capture, *, units: str = "m", include: set[str] | None = None) -> list[PlateRow]:
    """The caption's data block, in priority order, skipping whatever is absent.

    Nothing here is ever invented: a field with no value simply does not appear.
    """
    # `include or {...}` would be wrong: an empty set is falsy, so asking for NO fields
    # would silently get the default three back.
    inc = DEFAULT_FIELDS if include is None else include
    rows: list[PlateRow] = []

    if "position" in inc and cap.has_position:
        lat_s, lon_s = format_latlon(cap.lat, cap.lon, "dms")
        rows.append(PlateRow("Position", f"{lat_s}   {lon_s}"))

    if "altitude" in inc and cap.alt_agl is not None:
        # Height above the take-off point only. The aircraft's "above sea level" figure is
        # barometric and never corrected for the day's air pressure: two flights from the
        # same spot on Skye put the ground 39 m apart on consecutive days, and nine
        # panoramas in the archive record a drone BELOW sea level. A plate states what a
        # reader can check, so that figure stays in the file's metadata and the sidecar.
        # Flying down from a take-off point -- into a gorge, off a cliff -- gives a
        # negative reading, which reads as "below launch", not "-25 m above launch".
        below = format_altitude(cap.alt_agl, units).startswith("-")
        rows.append(PlateRow("Altitude", f"{format_altitude(abs(cap.alt_agl), units)} "
                                         f"{'below' if below else 'above'} launch"))

    if "recorded" in inc:
        when = format_datetime(cap.captured)
        bits = [when] if when else []
        # A 360 panorama faces every direction at once, so a heading would be meaningless
        # -- unless this is a print of one chosen framing, in which case the bearing of
        # that framing is real and is worded as the VIEW's, not the camera's.
        if cap.view_heading is not None:
            bits.append(f"view facing {format_heading(cap.view_heading)}")
        elif cap.camera_heading is not None and not cap.is_panorama:
            bits.append(f"camera facing {format_heading(cap.camera_heading)}")
        if bits:
            rows.append(PlateRow("Recorded", "   ·   ".join(bits)))

    if "camera" in inc and cap.model:
        maker = (cap.make or "").strip()
        label = f"{maker} {cap.model}".strip()
        exp = format_exposure(cap)
        rows.append(PlateRow("Camera", f"{label}   ·   {exp}" if exp else label))

    return rows


def layout_box(paper: Paper, ts: TypeScale, rows: list, title: str | None,
               footnote: bool) -> tuple[int, int, int, int]:
    """Mat geometry: (margin, caption height, image box width, image box height).

    Bottom-weighted: equal side and top margins, a deeper bottom to carry the caption.
    The eye reads a print as centred when the lower margin is the largest, which is why
    picture framers have cut mats this way for a century. Kept as one function so the
    viewer's "print this view" can render straight into the exact box the mat will have.
    """
    W, H = paper.width_px, paper.height_px
    S = paper.short_edge_px
    margin = int(round(S * 0.060))
    caption_h = int(round(ts.line_px * (len(rows) + 0.95)
                          + (ts.title_px * 1.35 if title else 0)
                          + (ts.footnote_px * 4.2 if footnote else 0)))
    bottom = margin + caption_h
    return margin, caption_h, W - 2 * margin, H - margin - bottom


def view_box(cap: Capture, paper: Paper, style: str | PlateStyle, title: str | None,
             footnote: bool = False, include: set[str] | None = None,
             units: str = "m") -> tuple[int, int]:
    """The pixel size a view must be rendered at to fill this plate's image area exactly."""
    st = STYLES[style] if isinstance(style, str) else style
    if st.name == "overlay":
        return paper.width_px, paper.height_px
    ts = TypeScale(dpi=paper.dpi, base_pt=caption_point_size(st.base_pt, paper))
    rows = build_rows(cap, units=units, include=include)
    _, _, w, h = layout_box(paper, ts, rows, title, footnote)
    return max(16, w), max(16, h)


#: The size the type is tuned for -- A3's short edge, 11.69 inches.
REFERENCE_SHORT_IN = 11.69


def caption_point_size(base_pt: float, paper: Paper) -> float:
    """Caption size in points, grown gently with the print.

    Type cannot simply stay at a fixed point size: 9 pt is right on an A3 held at arm's
    length and looks like a footnote lost in the margin of a 24-inch print, which is read
    from across a room. Nor should it scale linearly, which would make a large print
    shout. The 0.35 exponent splits the difference -- roughly how apparent size tracks
    viewing distance -- giving about 6.6 pt on a 5x7 and 11.5 pt on a 24x36.
    """
    short_in = min(paper.width_in, paper.height_in)
    scale = (short_in / REFERENCE_SHORT_IN) ** 0.35
    return max(5.5, base_pt * scale)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: float) -> list[str]:
    """Greedy word wrap to a pixel width. Pillow does no line breaking of its own."""
    words = text.split()
    if not words:
        return []
    lines, cur = [], words[0]
    for w in words[1:]:
        trial = cur + " " + w
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def _luminance(img: Image.Image, box: tuple[int, int, int, int]) -> float:
    """Mean luminance of a region, 0..1, used to choose light or dark text."""
    region = img.crop(box).convert("L").resize((48, 48), Image.BILINEAR)
    return float(np.asarray(region, dtype=np.float32).mean() / 255.0)


def _fit_text(draw: ImageDraw.ImageDraw, text: str, role: str, px: int,
              max_w: float, tracking: float = 0.0) -> tuple[object, int]:
    """Shrink a font until the string fits the available width.

    Long DMS coordinate pairs on a small print are the case that needs this; without it
    the value column would simply run off the mat.
    """
    size = px
    while size > 6:
        font = load_font(role, size)
        if text_width(draw, text, font, tracking * size / max(px, 1)) <= max_w:
            return font, size
        size -= 1
    return load_font(role, 6), 6


def render(cap: Capture, source: Image.Image, paper: Paper, *,
           style: str | PlateStyle = "gallery", title: str | None = None,
           fit: str = "contain", units: str = "m",
           include: set[str] | None = None,
           footnote: bool = True, footnote_variant: str = "short",
           digest: str | None = None) -> tuple[Image.Image, FitReport]:
    """Compose the plate. Returns the finished image and a resolution report."""
    st = STYLES[style] if isinstance(style, str) else style
    W, H = paper.width_px, paper.height_px
    S = paper.short_edge_px
    ts = TypeScale(dpi=paper.dpi, base_pt=caption_point_size(st.base_pt, paper))

    if st.name == "overlay":
        # The caption sits on the picture, so it has to read against the whole frame, not
        # against the paper's short edge. On a 2:1 panorama that edge is half the long one
        # and the text came out visually tiny.
        ref_in = (paper.width_in + paper.height_in) / 2.0
        ov_pt = max(st.base_pt, caption_point_size(st.base_pt, paper)
                    * (ref_in / max(min(paper.width_in, paper.height_in), 1e-6)) ** 0.5)
        ts = TypeScale(dpi=paper.dpi, base_pt=ov_pt)
        return _render_overlay(cap, source, paper, st, ts, title, units, include,
                               footnote, footnote_variant, digest, fit)

    rows = build_rows(cap, units=units, include=include)
    require_footnote(footnote, rows)
    margin, caption_h, box_w, box_h = layout_box(paper, ts, rows, title, footnote)

    plate = Image.new("RGB", (W, H), st.ground)
    draw = ImageDraw.Draw(plate)
    if box_w < 16 or box_h < 16:
        raise ValueError("paper too small for this layout")

    draw_w, draw_h, crop = fit_box(source.width, source.height, box_w, box_h, fit)
    report = assess_fit(source.width, source.height, draw_w, draw_h, crop, paper)

    img = source.crop(crop) if crop != (0, 0, source.width, source.height) else source
    img = img.convert("RGB").resize((draw_w, draw_h), Image.LANCZOS)

    # Distribute whatever vertical space the image did not use, rather than centring it
    # in its box and leaving a dead strip under the caption. Framers cut mats with the
    # lower margin deeper than the upper one -- an optically centred print sits slightly
    # high -- so the leftover is split 42/58 in favour of the bottom.
    img_x = margin + (box_w - draw_w) // 2
    content_h = draw_h + caption_h
    leftover = max(0, H - 2 * margin - content_h)
    img_y = margin + int(round(leftover * 0.42))
    plate.paste(img, (img_x, img_y))

    if st.image_border:
        draw.rectangle([img_x - 1, img_y - 1, img_x + draw_w, img_y + draw_h],
                       outline=st.image_border, width=1)

    _draw_caption(draw, st, ts, rows, title, cap, footnote,
                  left=img_x, right=img_x + draw_w,
                  top=img_y + draw_h, bottom_limit=H - margin,
                  footnote_variant=footnote_variant, digest=digest)
    return plate, report


def _draw_caption(draw, st: PlateStyle, ts: TypeScale, rows: list[PlateRow],
                  title: str | None, cap: Capture, footnote: bool,
                  left: int, right: int, top: int, bottom_limit: int,
                  footnote_variant: str = "short",
                  digest: str | None = None) -> None:
    """Lay out the caption block below the image, aligned to the image's own edges."""
    width = right - left
    y = top + int(round(ts.line_px * 0.95))

    if title:
        # a long title shrinks to the image width instead of running off the mat
        font, _ = _fit_text(draw, title, st.title_role, ts.title_px, max(width, 1))
        draw.text((left, y), title, font=font, fill=st.ink, anchor="ls")
        y += int(round(ts.title_px * 1.35))

    if st.show_rule and rows:
        ry = y - int(round(ts.line_px * 0.42))
        draw.line([(left, ry), (right, ry)], fill=st.rule, width=max(1, ts.dpi // 300))
        y += int(round(ts.line_px * 0.30))

    label_font = load_font(st.label_role, ts.label_px)
    tracking = ts.label_px * st.label_tracking
    label_w = 0.0
    if st.labels and rows:
        label_w = max(text_width(draw, r.label.upper(), label_font, tracking) for r in rows)
        gutter = ts.value_px * 1.05
    else:
        gutter = 0.0

    for row in rows:
        if st.labels:
            draw_tracked(draw, (left, y), row.label.upper(), label_font, st.ink_soft,
                         tracking=tracking, anchor="ls")
        vx = left + label_w + gutter
        font, _ = _fit_text(draw, row.value, st.value_role, ts.value_px,
                            max(width - (vx - left), 1))
        draw.text((vx, y), row.value, font=font, fill=st.ink, anchor="ls")
        y += ts.line_px

    if footnote:
        y += int(round(ts.footnote_px * 0.75))
        f = load_font(st.foot_role, ts.footnote_px)
        lead = int(round(ts.footnote_px * 1.35))
        for line in _wrap(draw, footnote_text(cap, footnote_variant, digest), f, width):
            if y > bottom_limit:
                break
            draw.text((left, y), line, font=f, fill=st.ink_soft, anchor="ls")
            y += lead


def _render_overlay(cap: Capture, source: Image.Image, paper: Paper, st: PlateStyle,
                    ts: TypeScale, title: str | None, units: str,
                    include: set[str] | None, footnote: bool,
                    footnote_variant: str = "short", digest: str | None = None,
                    fit: str = "cover"):
    """Caption on the image itself, over a gradient scrim.

    Defaults to ``cover`` because a caption sitting on the picture wants the picture to
    reach the paper edge, but honours ``contain`` so the whole frame can be kept when the
    proportions do not match -- on a 16:9 frame onto A3, ``cover`` discards 20.5% of it.
    With ``contain`` the image is centred and the surrounding ground shows through.
    """
    W, H = paper.width_px, paper.height_px
    draw_w, draw_h, crop = fit_box(source.width, source.height, W, H, fit)
    report = assess_fit(source.width, source.height, draw_w, draw_h, crop, paper)
    scaled = source.crop(crop).convert("RGB").resize((draw_w, draw_h), Image.LANCZOS)
    if (draw_w, draw_h) == (W, H):
        img = scaled
    else:
        img = Image.new("RGB", (W, H), st.ground)
        img.paste(scaled, ((W - draw_w) // 2, (H - draw_h) // 2))

    rows = build_rows(cap, units=units, include=include)
    band_h = int(round(ts.line_px * (len(rows) + 1.6)
                       + (ts.title_px * 1.4 if title else 0)))
    band_h = min(band_h, H // 2)

    # A gradient scrim keeps the text legible without flattening the picture into a
    # letterbox. Its strength follows the brightness underneath, so a bright sky gets a
    # heavier veil than a dark forest already provides.
    #
    # Measured on real plates: a lakefront caption sitting over dark water reads at a
    # region luminance of 0.285, while the same treatment over sunlit grass at 0.387 was
    # nearly illegible. The earlier curve (0.30 + 0.45*lum) was far too weak across that
    # whole range, so it now starts higher and climbs faster -- and because the veil is a
    # gradient that reaches full strength only at the very bottom edge, a heavier one
    # costs the picture almost nothing.
    #
    # Only the region the text actually occupies is measured. Averaging the full band
    # width lets bright sky on the far side of a 2:1 panorama decide the veil over a dark
    # corner, or the reverse.
    lum = _luminance(img, (0, H - band_h, int(W * 0.55), H))
    alpha_max = int(round(255 * min(0.90, 0.46 + 0.62 * lum)))
    scrim = Image.new("L", (1, band_h))
    for i in range(band_h):
        t = i / max(band_h - 1, 1)
        scrim.putpixel((0, i), int(alpha_max * (t ** 1.45)))
    scrim = scrim.resize((W, band_h))
    dark = Image.new("RGB", (W, band_h), (0, 0, 0))
    region = img.crop((0, H - band_h, W, H))
    img.paste(Image.composite(dark, region, scrim), (0, H - band_h))

    draw = ImageDraw.Draw(img)
    margin = int(round(min(W, H) * 0.045))
    # Drawn twice: a dark offset pass first, then the type on top. The scrim handles the
    # average case, the shadow handles the bright detail that happens to fall under a
    # glyph -- a white gull or a sunlit rock will otherwise erase a character.
    shadow = PlateStyle(**{**st.__dict__, "ink": (0, 0, 0), "ink_soft": (0, 0, 0)})
    off = max(1, int(round(ts.value_px * 0.055)))
    _draw_caption(draw, shadow, ts, rows, title, cap, footnote,
                  left=margin + off, right=W - margin + off,
                  top=H - band_h + int(ts.line_px * 0.1) + off,
                  bottom_limit=H - margin // 2 + off,
                  footnote_variant=footnote_variant, digest=digest)
    _draw_caption(draw, st, ts, rows, title, cap, footnote,
                  left=margin, right=W - margin,
                  top=H - band_h + int(ts.line_px * 0.1), bottom_limit=H - margin // 2,
                  footnote_variant=footnote_variant, digest=digest)
    return img, report


SOFTWARE = "panolib"
_EXIF_DATETIME = re.compile(r"\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}")


def _dms_rationals(value: float) -> tuple[IFDRational, IFDRational, IFDRational]:
    """|value| as EXIF degree/minute/second rationals, the seconds to a micro-arcsecond.

    That carries the camera's own figure through unchanged (DJI records the seconds to
    four decimals). The plate rounds for a reader; the file does not round at all.
    """
    v = abs(value)
    d = int(v)
    m_full = (v - d) * 60.0
    m = int(m_full)
    s = Fraction((m_full - m) * 60.0).limit_denominator(1_000_000)
    # rounding can carry into the minutes, and the minutes into the degrees
    if s >= 60:
        s -= 60
        m += 1
    if m >= 60:
        m -= 60
        d += 1
    return IFDRational(d, 1), IFDRational(m, 1), IFDRational(s.numerator, s.denominator)


def print_exif(cap: Capture, include: set[str] | None = None) -> Image.Exif:
    """The metadata a print file carries: what the source records, as far as the plate shows it.

    A print is a new file, so nothing comes across unless it is written here -- and what
    is written is chosen, never a wholesale copy of the source's tags:

    * Position and capture time when the plate shows them, and the altitude the aircraft
      recorded above sea level when the plate shows altitude. They follow the same
      ``include`` fields as the caption, so the file never discloses more than the face of
      the print: leave position off the plate and the GPS stays out of the file too. These
      are what put the print on the map and in the timeline of a photo library.
    * The camera make and model, and ``Software=panolib`` so the file does not pass for
      a camera original.
    * Never the aircraft serial number: it identifies the owner and would link every
      print to every other. Never the panorama projection tags: a matted print is not an
      equirectangular image, and photo apps would wrap it round a sphere. Never the
      source's orientation or thumbnail, which describe a different picture.

    The plate prints height above launch, which has no standard EXIF tag; it is on the
    plate and in the sidecar. The sea-level figure is left off the plate (it is barometric
    and drifts with the weather) but goes into the file as recorded, sign included: it is
    the camera's own record, and the file keeps saying what the camera said.
    """
    inc = DEFAULT_FIELDS if include is None else include
    exif = Image.Exif()
    if cap.make and cap.make.strip():
        exif[ExifTags.Base.Make] = cap.make.strip()
    if cap.model and cap.model.strip():
        exif[ExifTags.Base.Model] = cap.model.strip()
    exif[ExifTags.Base.Software] = SOFTWARE

    when = (cap.captured or "").strip()
    if "recorded" in inc and _EXIF_DATETIME.fullmatch(when):
        sub = exif.get_ifd(ExifTags.IFD.Exif)
        sub[ExifTags.Base.ExifVersion] = b"0232"
        sub[ExifTags.Base.DateTimeOriginal] = when

    gps: dict = {}
    if ("position" in inc and cap.has_position
            and math.isfinite(cap.lat) and math.isfinite(cap.lon)
            and abs(cap.lat) <= 90.0 and abs(cap.lon) <= 180.0):
        gps[ExifTags.GPS.GPSLatitudeRef] = "N" if cap.lat >= 0 else "S"
        gps[ExifTags.GPS.GPSLatitude] = _dms_rationals(cap.lat)
        gps[ExifTags.GPS.GPSLongitudeRef] = "E" if cap.lon >= 0 else "W"
        gps[ExifTags.GPS.GPSLongitude] = _dms_rationals(cap.lon)
    if "altitude" in inc and cap.alt_msl is not None and math.isfinite(cap.alt_msl):
        alt = Fraction(abs(cap.alt_msl)).limit_denominator(1000)       # millimetres
        gps[ExifTags.GPS.GPSAltitudeRef] = 0 if cap.alt_msl >= 0 else 1  # 1 = below sea level
        gps[ExifTags.GPS.GPSAltitude] = IFDRational(alt.numerator, alt.denominator)
    if gps:
        exif.get_ifd(ExifTags.IFD.GPSInfo).update(
            {ExifTags.GPS.GPSVersionID: b"\x02\x03\x00\x00", **gps})
    return exif


def _embed_exif_with_exiftool(path: str, exif: Image.Exif, exiftool: str | None) -> bool:
    """TIFF only: write ``exif`` into an existing file. True on success.

    Pillow's compressed-TIFF writer cannot produce the EXIF and GPS sub-directories, so
    the block goes through exiftool instead, copied from a one-pixel carrier JPEG that
    holds exactly what print_exif built. The image data and its compression are not
    touched.
    """
    import subprocess
    import tempfile
    from .exif import ExifToolMissing, find_exiftool
    try:
        exe = find_exiftool(exiftool)
    except ExifToolMissing:
        return False
    fd, carrier = tempfile.mkstemp(prefix="panolib-exif-", suffix=".jpg")
    os.close(fd)
    try:
        Image.new("RGB", (1, 1)).save(carrier, format="JPEG", exif=exif)
        proc = subprocess.run([exe, "-q", "-q", "-charset", "filename=utf8",
                               "-overwrite_original", "-tagsFromFile", carrier,
                               "-EXIF:all", path],
                              capture_output=True, timeout=120)
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
    finally:
        try:
            os.remove(carrier)
        except OSError:
            pass


def save_print(image: Image.Image, path: str, paper: Paper, *,
               jpeg_quality: int = 95, exif: Image.Exif | None = None,
               exiftool: str | None = None) -> bool:
    """Write the plate with the DPI recorded, so a lab prints it at the intended size.

    Without the dpi tag the file is just a pixel grid and the print size is whatever the
    lab assumes. ``exif`` (built by print_exif) goes into the same file, in the same
    write for JPEG and PNG; the resolution is added to it so the EXIF and JFIF headers
    agree on the print size.

    Returns False only when metadata was asked for and could not be embedded, which can
    happen for TIFF alone (it needs exiftool). The print itself is written either way.
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    # a temporary name such as ``plate.jpg.part`` keeps the format of the real name
    final = path[:-len(".part")] if path.endswith(".part") else path
    ext = os.path.splitext(final)[1].lower()
    params: dict = {"dpi": (paper.dpi, paper.dpi)}
    if exif is not None:
        exif[ExifTags.Base.XResolution] = IFDRational(paper.dpi, 1)
        exif[ExifTags.Base.YResolution] = IFDRational(paper.dpi, 1)
        exif[ExifTags.Base.ResolutionUnit] = 2                         # inches
    if ext in (".tif", ".tiff"):
        image.save(path, format="TIFF", compression="tiff_lzw", **params)
        return exif is None or _embed_exif_with_exiftool(path, exif, exiftool)
    if exif is not None:
        params["exif"] = exif
    if ext == ".png":
        image.save(path, format="PNG", **params)
    else:
        image.save(path, format="JPEG", quality=jpeg_quality, subsampling=0, optimize=True, **params)
    return True
