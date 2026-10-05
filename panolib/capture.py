"""Reading and formatting where a photograph was taken.

This works on any image, not only the panoramas this tool builds: the user's archive holds
roughly 3,900 ordinary drone frames alongside the 135 panorama sets, and every one sampled
carried GPS, both altitudes, a timestamp and a camera heading.

Formatting lives here too, because how a coordinate is written is part of what it claims.
Two rules shape all of it:

* **Never print more precision than the instrument has.** Consumer GNSS on this aircraft is
  good to a few metres. Six decimal places of latitude is 0.11 m -- writing it implies a
  survey-grade fix that does not exist.
* **Never conflate the two altitudes.** ``GPSAltitude`` is height above sea level;
  ``RelativeAltitude`` is height above the point the drone took off from. They answer
  different questions and differ by hundreds of metres inland. Labelling either one simply
  "altitude" would be wrong. On this aircraft the sea-level figure is barometric and never
  corrected for the day's air pressure -- tens of metres out, sometimes negative -- so
  plates print only height above launch; the sea-level value is kept as recorded.
"""

from __future__ import annotations

import math
import os
import subprocess
from dataclasses import dataclass

from .exif import TAGS, as_float, as_opt_float, find_exiftool

# Extra tags beyond what the panorama pipeline reads.
PHOTO_TAGS = TAGS + (
    "FocalLengthIn35mmFormat", "ExposureCompensation", "ShutterSpeed",
    "GPSDateStamp", "GPSTimeStamp", "Orientation", "ImageDescription",
)


@dataclass
class Capture:
    """What a file records about where and how it was taken."""

    path: str
    width: int
    height: int
    lat: float | None = None
    lon: float | None = None
    alt_msl: float | None = None        # metres above sea level
    alt_agl: float | None = None        # metres above the takeoff point
    captured: str | None = None         # "YYYY:MM:DD HH:MM:SS", no timezone
    camera_heading: float | None = None  # degrees, 0 = north, + clockwise
    aircraft_heading: float | None = None
    #: where camera_heading came from: "gimbal" or "airframe"
    heading_source: str | None = None
    #: for a print of a chosen framing of a panorama: the bearing that VIEW faces.
    #: Distinct from camera_heading, which is what the file records; this is a fact
    #: about the print, and the plate words it differently ("view facing").
    view_heading: float | None = None
    model: str | None = None
    make: str | None = None
    serial: str | None = None
    f_number: float | None = None
    exposure_time: float | None = None
    iso: float | None = None
    focal_35: float | None = None
    is_panorama: bool = False

    @property
    def has_position(self) -> bool:
        return self.lat is not None and self.lon is not None

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height else 1.0


def read_capture(path: str, exiftool: str | None = None) -> Capture:
    """Read one image's capture metadata. Missing fields stay ``None``, never invented."""
    exe = find_exiftool(exiftool)
    cmd = [exe, "-j", "-n", "-charset", "filename=utf8"]
    cmd += [f"-{t}" for t in PHOTO_TAGS]
    cmd.append(path)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120)
        import json
        recs = json.loads(proc.stdout) if proc.stdout.strip() else []
    except (OSError, subprocess.TimeoutExpired, ValueError):
        recs = []
    rec = recs[0] if recs else {}

    # Fall back to the pixels themselves when EXIF has no dimensions.
    w = int(as_float(rec.get("ImageWidth"), 0)) or 0
    h = int(as_float(rec.get("ImageHeight"), 0)) or 0
    if not (w and h):
        try:
            from PIL import Image
            with Image.open(path) as im:
                w, h = im.size
        except Exception:
            w = h = 0

    xp = (rec.get("XPComment") or "")

    # Which field actually holds the direction the camera was pointing.
    #
    # Measured over 1,843 ordinary frames from three different cards: GimbalYawDegree is
    # EXACTLY 0.00 on every single one, while FlightYawDegree spans the full circle. The
    # Mavic Air 2's gimbal has no independent yaw axis -- it only pitches, and the aircraft
    # turns to aim the camera -- so on a normal photo the gimbal yaw is a relative value
    # pinned at zero and the airframe heading IS the camera heading. Spot-checked against
    # a Chicago lakefront frame: FlightYaw -51.8 deg (NW) puts the skyline right of frame
    # and the setting sun to the left, which matches the picture.
    #
    # Panorama TILES are the exception: there DJI writes a real absolute bearing into
    # GimbalYawDegree for each tile, which is what the stitcher relies on.
    gimbal_yaw = as_opt_float(rec.get("GimbalYawDegree"))
    flight_yaw = as_opt_float(rec.get("FlightYawDegree"))
    if gimbal_yaw is not None and abs(gimbal_yaw) > 0.005:
        heading, heading_source = gimbal_yaw, "gimbal"
    elif flight_yaw is not None:
        heading, heading_source = flight_yaw, "airframe"
    else:
        heading, heading_source = gimbal_yaw, ("gimbal" if gimbal_yaw is not None else None)

    return Capture(
        path=path, width=w, height=h,
        lat=as_opt_float(rec.get("GPSLatitude")),
        lon=as_opt_float(rec.get("GPSLongitude")),
        alt_msl=as_opt_float(rec.get("AbsoluteAltitude")) if rec.get("AbsoluteAltitude") is not None
        else as_opt_float(rec.get("GPSAltitude")),
        alt_agl=as_opt_float(rec.get("RelativeAltitude")),
        captured=rec.get("DateTimeOriginal") or rec.get("CreateDate"),
        camera_heading=heading, heading_source=heading_source,
        aircraft_heading=flight_yaw,
        model=rec.get("Model"), make=rec.get("Make"),
        serial=rec.get("SerialNumber"),
        f_number=as_opt_float(rec.get("FNumber")),
        exposure_time=as_opt_float(rec.get("ExposureTime")),
        iso=as_opt_float(rec.get("ISO")),
        focal_35=as_opt_float(rec.get("FocalLengthIn35mmFormat")),
        is_panorama=is_composite(xp, w, h),
    )


def is_composite(xp_comment: str | None, width: int, height: int) -> bool:
    """Is this image several frames merged into one?

    DJI writes ``Type=P`` into every frame it shoots for a panorama, so a single source
    frame -- 4:3 like any one exposure -- carries the marker too. Printed on its own it is
    one exposure with one real bearing, not a panorama. Only a ``Type=P`` image that is
    not 4:3 (a panorama DJI Fly stitched itself) or this tool's own 2:1 output is a
    composite.
    """
    if not (width and height):
        return False
    if width >= 4000 and abs(width / height - 2.0) < 0.02:
        return True
    return "Type=P" in (xp_comment or "") and abs(width / height - 4 / 3) > 0.02


# --------------------------------------------------------------------- formatting

def dms(value: float, positive: str, negative: str, seconds_dp: int = 1) -> str:
    """Degrees-minutes-seconds, e.g. ``51°28'40.1"N``.

    One decimal on the seconds is 3.1 m of latitude, which sits right at the limit of what
    the receiver can actually resolve. More would be invented precision.
    """
    hemi = positive if value >= 0 else negative
    v = abs(value)
    d = int(v)
    m_full = (v - d) * 60.0
    m = int(m_full)
    s = (m_full - m) * 60.0
    # rounding the seconds can carry into the minutes, and the minutes into the degrees
    s_r = round(s, seconds_dp)
    if s_r >= 60.0:
        s_r -= 60.0
        m += 1
    if m >= 60:
        m -= 60
        d += 1
    # width = 2 integer digits + the decimal point + seconds_dp, so 3.0 pads to "03.0"
    return f"{d}°{m:02d}′{s_r:0{3 + seconds_dp}.{seconds_dp}f}″{hemi}"


def decimal_degrees(value: float, positive: str, negative: str, dp: int = 5) -> str:
    """Decimal degrees with a hemisphere letter, e.g. ``51.47780°N``.

    Five places is about 1.1 m -- already finer than the fix. It is the most this should
    ever print.
    """
    hemi = positive if value >= 0 else negative
    return f"{abs(value):.{dp}f}°{hemi}"


def format_latlon(lat: float, lon: float, style: str = "dms") -> tuple[str, str]:
    """Return (latitude, longitude) strings in the requested style."""
    if style == "decimal":
        return decimal_degrees(lat, "N", "S"), decimal_degrees(lon, "E", "W")
    return dms(lat, "N", "S"), dms(lon, "E", "W")


def format_altitude(metres: float, unit: str = "m") -> str:
    """Altitudes to the nearest metre. The barometer drifts; decimals would be theatre.

    Rounded half away from zero (format's banker's rounding would make 2.5 m "2 m"), and
    never "-0": a reading of -0.2 m is 0 m.
    """
    v = metres * 3.280839895 if unit == "ft" else metres
    r = int(math.floor(abs(v) + 0.5))
    return f"{-r if v < 0 else r:,} {'ft' if unit == 'ft' else 'm'}"


_COMPASS = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")


def format_heading(deg: float) -> str:
    """Compass bearing with its point, e.g. ``118° ESE``.

    Rounded BEFORE wrapping, so 359.6 prints as ``0° N`` rather than ``360° N``.
    """
    d = deg % 360.0
    point = _COMPASS[int((d + 11.25) // 22.5) % 16]
    return f"{round(d) % 360}° {point}"


def format_datetime(captured: str | None) -> str | None:
    """``2026:07:26 12:10:03`` -> ``26 July 2026, 12:10``.

    No timezone is shown. EXIF ``DateTimeOriginal`` carries none, and this archive spans
    four countries -- printing a zone would mean guessing one.
    """
    if not captured:
        return None
    try:
        date_part, _, time_part = captured.partition(" ")
        y, m, d = (int(x) for x in date_part.split(":")[:3])
        hh, mm = (int(x) for x in time_part.split(":")[:2])
    except (ValueError, IndexError):
        return captured
    months = ("January", "February", "March", "April", "May", "June", "July",
              "August", "September", "October", "November", "December")
    if not 1 <= m <= 12:
        return captured
    return f"{d} {months[m - 1]} {y}, {hh:02d}:{mm:02d}"


def format_exposure(cap: Capture) -> str | None:
    """``f/2.8 · 1/500 s · ISO 100``, omitting whatever is missing."""
    bits = []
    if cap.f_number:
        bits.append(f"f/{cap.f_number:g}")
    if cap.exposure_time:
        if cap.exposure_time >= 1:
            bits.append(f"{cap.exposure_time:g} s")
        else:
            bits.append(f"1/{round(1 / cap.exposure_time):g} s")
    if cap.iso:
        bits.append(f"ISO {cap.iso:g}")
    return "  ·  ".join(bits) if bits else None


def file_digest(path: str, short: int = 16) -> str | None:
    """SHA-256 of the source file, truncated for printing.

    This is the one provenance aid that does something a coordinate cannot. It proves
    nothing about where the picture was taken -- a doctored file has a digest too. What it
    does is bind THIS print to ONE exact file: keep the original off the card, and you can
    show later that the file you hold is the one the print was made from. Sixteen hex
    digits is 64 bits, far beyond accidental collision, and short enough to set in type.
    """
    import hashlib
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()[:short] if short else h.hexdigest()


def map_url(lat: float, lon: float) -> str:
    """A link a viewer can type in to check the position for themselves."""
    return f"https://www.openstreetmap.org/?mlat={lat:.5f}&mlon={lon:.5f}#map=15/{lat:.5f}/{lon:.5f}"


def enrich_from_library(cap: Capture, out_dir: str) -> Capture:
    """Fill gaps from the panorama library when the image is one of our own outputs.

    A stitched panorama inherits its GPS and timestamp from the first tile, but older
    builds did not carry the above-takeoff altitude into the JPEG. The manifest always
    has it, along with the trip and place names, so prefer that when it matches.
    """
    import json
    lib_path = os.path.join(out_dir, "library.json")
    if not os.path.exists(lib_path):
        return cap
    try:
        with open(lib_path, "r", encoding="utf-8") as fh:
            lib = json.load(fh)
    except (OSError, ValueError):
        return cap

    target = os.path.normcase(os.path.abspath(cap.path))
    for e in lib.get("panoramas", []):
        for key in ("equirect", "preview", "thumb"):
            rel = e.get(key)
            if not rel:
                continue
            full = os.path.normcase(os.path.abspath(
                os.path.join(out_dir, rel.replace("/", os.sep))))
            if full == target:
                if cap.alt_agl is None and e.get("altitude_rel") is not None:
                    cap.alt_agl = float(e["altitude_rel"])
                if cap.alt_msl is None and e.get("altitude") is not None:
                    cap.alt_msl = float(e["altitude"])
                if not cap.captured and e.get("captured"):
                    cap.captured = e["captured"]
                if cap.lat is None and e.get("lat") is not None:
                    cap.lat = float(e["lat"])
                    cap.lon = float(e["lon"])
                cap.is_panorama = True
                return cap
    return cap
