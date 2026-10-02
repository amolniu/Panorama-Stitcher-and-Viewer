"""Reading DJI's per-second flight telemetry out of a video.

A still grabbed from a video through a player carries no metadata at all -- the position,
height and time are gone. But DJI writes a telemetry subtitle track into the MP4 itself,
one record per second, and that survives. So a video frame's provenance is recoverable
after the fact, as long as you know which video it came from and roughly when.

The track is a ``mov_text`` subtitle stream holding lines like::

    F/2.8, SS 1769.41, ISO 110, EV 0, DZOOM 1.000, GPS (-0.0015, 51.4778, 17),
    D 8.46m, H 0.00m, H.S 0.00m/s, V.S -0.00m/s

Two things in that are easy to get wrong:

* **GPS is (longitude, latitude, satellites)** -- longitude FIRST. Reading it in the usual
  lat/lon order silently relocates a Chicago photograph to the Indian Ocean off Somalia.
* **H is height above the take-off point**, not above sea level. It is the same quantity as
  ``RelativeAltitude`` in the stills, and the video carries no sea-level figure at all.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass

from .cinema import find_ffmpeg

#: One telemetry line. Fields absent from a given firmware's output stay None.
_FIELD_RE = {
    "f_number": re.compile(r"\bF/([0-9.]+)"),
    "shutter": re.compile(r"\bSS\s+([0-9.]+)"),
    "iso": re.compile(r"\bISO\s+([0-9]+)"),
    "ev": re.compile(r"\bEV\s+([-+]?[0-9.]+)"),
    "zoom": re.compile(r"\bDZOOM\s+([0-9.]+)"),
    "distance_m": re.compile(r"\bD\s+([-+]?[0-9.]+)m"),
    "height_m": re.compile(r"\bH\s+([-+]?[0-9.]+)m"),
    "h_speed": re.compile(r"\bH\.S\s+([-+]?[0-9.]+)m/s"),
    "v_speed": re.compile(r"\bV\.S\s+([-+]?[0-9.]+)m/s"),
}
# longitude, latitude, satellite count -- in that order
_GPS_RE = re.compile(r"GPS\s*\(\s*([-+]?[0-9.]+)\s*,\s*([-+]?[0-9.]+)\s*,\s*([0-9]+)\s*\)")
_TIME_RE = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")


@dataclass
class TelemetryRecord:
    """One second of flight."""

    start_s: float
    end_s: float
    lat: float | None = None
    lon: float | None = None
    satellites: int | None = None
    height_m: float | None = None       # above the take-off point
    distance_m: float | None = None
    f_number: float | None = None
    shutter: float | None = None        # DJI writes the denominator, e.g. 1769.41 = 1/1769
    iso: float | None = None
    ev: float | None = None
    h_speed: float | None = None
    v_speed: float | None = None

    @property
    def has_fix(self) -> bool:
        return self.lat is not None and self.lon is not None


def _parse_block(text: str, start_s: float, end_s: float) -> TelemetryRecord:
    rec = TelemetryRecord(start_s=start_s, end_s=end_s)
    m = _GPS_RE.search(text)
    if m:
        lon, lat, sats = float(m.group(1)), float(m.group(2)), int(m.group(3))
        # A zero fix means the receiver had not locked yet; it is not the Gulf of Guinea.
        if abs(lat) > 1e-6 or abs(lon) > 1e-6:
            rec.lat, rec.lon, rec.satellites = lat, lon, sats
    for name, rx in _FIELD_RE.items():
        hit = rx.search(text)
        if hit:
            try:
                setattr(rec, name, float(hit.group(1)))
            except ValueError:
                pass
    return rec


def read_telemetry(video: str, ffmpeg: str | None = None) -> list[TelemetryRecord]:
    """Extract and parse the telemetry track. Returns [] when the video has none.

    The track is ``mov_text``, so it must be transcoded to SRT rather than stream-copied;
    ``-c copy`` fails with "Unsupported subtitles codec".
    """
    exe = find_ffmpeg(ffmpeg)
    if not exe or not os.path.exists(video):
        return []
    cmd = [exe, "-v", "error", "-i", video, "-map", "0:s:0", "-c:s", "srt", "-f", "srt", "pipe:1"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        return []
    text = proc.stdout.decode("utf-8", "replace")
    if not text.strip():
        return []

    out: list[TelemetryRecord] = []
    for block in re.split(r"\n\s*\n", text):
        tm = _TIME_RE.search(block)
        if not tm:
            continue
        a = (int(tm.group(1)) * 3600 + int(tm.group(2)) * 60
             + int(tm.group(3)) + int(tm.group(4)) / 1000.0)
        b = (int(tm.group(5)) * 3600 + int(tm.group(6)) * 60
             + int(tm.group(7)) + int(tm.group(8)) / 1000.0)
        body = block[tm.end():]
        out.append(_parse_block(body, a, b))
    return out


def at_time(records: list[TelemetryRecord], seconds: float) -> TelemetryRecord | None:
    """The record covering ``seconds``, or the nearest one if it falls in a gap."""
    if not records:
        return None
    for r in records:
        if r.start_s <= seconds < r.end_s:
            return r
    return min(records, key=lambda r: min(abs(r.start_s - seconds), abs(r.end_s - seconds)))


def video_start_time(video: str, exiftool: str | None = None) -> str | None:
    """The video's CreateDate, so a frame's wall-clock time can be derived from its offset.

    QuickTime ``CreateDate`` is written in UTC by this camera, which is why the evening's
    files carry the following day's date. Converting it needs the capture's time zone,
    which nothing in the file records, so this returns the raw value and leaves the
    interpretation to the caller.
    """
    from .exif import find_exiftool
    try:
        exe = find_exiftool(exiftool)
    except Exception:
        return None
    try:
        proc = subprocess.run([exe, "-s3", "-CreateDate", video],
                              capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    val = (proc.stdout or "").strip()
    return val or None


def capture_from_frame(video: str, seconds: float, frame_path: str,
                       ffmpeg: str | None = None):
    """Build a :class:`~panolib.capture.Capture` for a still taken from a video.

    The height is above the take-off point; the video records no sea-level figure, so
    ``alt_msl`` stays None rather than being invented. Heading is likewise absent: the
    telemetry carries speeds and distance but no compass bearing.
    """
    from .capture import Capture
    recs = read_telemetry(video, ffmpeg)
    rec = at_time(recs, seconds)
    width = height = 0
    try:
        from PIL import Image
        with Image.open(frame_path) as im:
            width, height = im.size
    except Exception:
        pass

    cap = Capture(path=frame_path, width=width, height=height)
    if rec and rec.has_fix:
        cap.lat, cap.lon = rec.lat, rec.lon
    if rec:
        cap.alt_agl = rec.height_m
        cap.f_number = rec.f_number
        cap.iso = rec.iso
        if rec.shutter:
            cap.exposure_time = 1.0 / rec.shutter if rec.shutter > 1 else rec.shutter
    cap.captured = video_start_time(video)
    cap.model = "FC7303"
    cap.make = "DJI"
    return cap, rec
