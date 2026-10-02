"""Reading DJI tile metadata with exiftool.

Two things here are load-bearing and easy to get wrong:

1. exiftool returns the ``drone-dji`` gimbal tags as STRINGS even with ``-n``,
   because the values carry an explicit ``+``/``-`` sign prefix (``"+14.85"``).
   Every angle must go through :func:`as_float` or it silently becomes a
   ``TypeError`` -- or worse, a string comparison that quietly does the wrong thing.

2. Starting exiftool once per file is ruinously slow on Windows (process startup
   dominates). One recursive invocation per set, or a single ``-stay_open`` session
   for a whole scan, is the difference between seconds and many minutes.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Iterable, Sequence

#: Tags pulled for every tile. Keep this list tight -- exiftool cost scales with it.
TAGS: tuple[str, ...] = (
    "FileName", "Directory",
    "GimbalYawDegree", "GimbalPitchDegree", "GimbalRollDegree",
    "FlightYawDegree", "FlightPitchDegree", "FlightRollDegree",
    "ExposureTime", "ISO", "FNumber", "ExposureCompensation",
    "ImageWidth", "ImageHeight",
    "DateTimeOriginal", "CreateDate",
    "GPSLatitude", "GPSLongitude", "GPSAltitude",
    "AbsoluteAltitude", "RelativeAltitude",
    "Model", "Make", "SerialNumber", "XPComment",
)

_DEFAULT_EXIFTOOL_PATHS = (
    r"C:\Users\rzram\AppData\Local\Programs\ExifTool\ExifTool.exe",
    "exiftool",
)


class ExifToolMissing(RuntimeError):
    """Raised when no exiftool binary can be located."""


def find_exiftool(explicit: str | None = None) -> str:
    """Locate the exiftool binary, preferring an explicit path then PATH."""
    candidates = ([explicit] if explicit else []) + list(_DEFAULT_EXIFTOOL_PATHS)
    for cand in candidates:
        if not cand:
            continue
        if os.path.isfile(cand):
            return cand
        found = shutil.which(cand)
        if found:
            return found
    raise ExifToolMissing(
        "exiftool not found. Install it from https://exiftool.org/ and either put it "
        "on PATH or pass --exiftool <path>."
    )


def as_float(value, default: float = 0.0) -> float:
    """Coerce an exiftool value to float, tolerating ``"+14.85"`` style strings.

    Returns ``default`` for ``None`` and for anything unparseable, so a single odd
    tag cannot abort a whole batch.
    """
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip().lstrip("+"))
    except (ValueError, TypeError):
        return default


def as_opt_float(value) -> float | None:
    """Like :func:`as_float` but distinguishes 'absent' from 'zero'.

    Needed for GPS, where 0.0 is a real coordinate and must not be confused with a
    missing fix.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip().lstrip("+"))
    except (ValueError, TypeError):
        return None


@dataclass
class TileMeta:
    """One panorama tile, with the angles already coerced to floats."""

    path: str
    filename: str
    yaw: float
    pitch: float
    roll: float
    width: int
    height: int
    exposure_time: float
    iso: float
    f_number: float
    datetime: str | None = None
    lat: float | None = None
    lon: float | None = None
    altitude: float | None = None
    rel_altitude: float | None = None
    model: str | None = None
    serial: str | None = None
    has_gimbal: bool = True
    #: optional world-frame axis-angle correction (radians) from the refinement pass
    delta_omega: tuple[float, float, float] | None = None

    @property
    def exposure_gain(self) -> float:
        """Relative sensor gain, proportional to exposure_time * ISO / f^2.

        Used to bring independently auto-exposed tiles onto a common exposure before
        blending. Guarded against zero so a corrupt tag cannot divide by zero.
        """
        n = self.f_number if self.f_number else 2.8
        g = (self.exposure_time * self.iso) / (n * n)
        return g if g > 1e-12 else 1e-12


def _run_exiftool(exiftool: str, args: Sequence[str]) -> list[dict]:
    """Invoke exiftool once and parse its JSON, returning [] on failure."""
    cmd = [exiftool, "-j", "-n", "-charset", "filename=utf8"]
    cmd += [f"-{t}" for t in TAGS]
    cmd += list(args)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        return []
    out = (proc.stdout or "").strip()
    if not out:
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else [data]


def read_tiles(folder: str, exiftool: str | None = None) -> list[TileMeta]:
    """Read every JPEG in ``folder`` (non-recursive) as tiles, sorted by filename.

    Tiles whose gimbal tags are absent are still returned, flagged with
    ``has_gimbal=False``, so the caller can report them rather than silently
    stitching garbage at yaw=pitch=0.
    """
    exe = find_exiftool(exiftool)
    records = _run_exiftool(exe, [folder])

    tiles: list[TileMeta] = []
    for rec in records:
        name = rec.get("FileName", "")
        if not name.lower().endswith((".jpg", ".jpeg")):
            continue
        directory = rec.get("Directory") or folder
        has_gimbal = rec.get("GimbalYawDegree") is not None
        tiles.append(TileMeta(
            path=os.path.join(directory.replace("/", os.sep), name),
            filename=name,
            yaw=as_float(rec.get("GimbalYawDegree")),
            pitch=as_float(rec.get("GimbalPitchDegree")),
            roll=as_float(rec.get("GimbalRollDegree")),
            width=int(as_float(rec.get("ImageWidth"), 2000)),
            height=int(as_float(rec.get("ImageHeight"), 1500)),
            exposure_time=as_float(rec.get("ExposureTime"), 1.0) or 1.0,
            iso=as_float(rec.get("ISO"), 100.0) or 100.0,
            f_number=as_float(rec.get("FNumber"), 2.8) or 2.8,
            datetime=rec.get("DateTimeOriginal") or rec.get("CreateDate"),
            lat=as_opt_float(rec.get("GPSLatitude")),
            lon=as_opt_float(rec.get("GPSLongitude")),
            altitude=as_opt_float(rec.get("GPSAltitude")),
            rel_altitude=as_opt_float(rec.get("RelativeAltitude")),
            model=rec.get("Model"),
            serial=rec.get("SerialNumber"),
            has_gimbal=has_gimbal,
        ))

    tiles.sort(key=lambda t: t.filename)
    return tiles


def read_many(folders: Iterable[str], exiftool: str | None = None) -> dict[str, list[TileMeta]]:
    """Read several set folders in one exiftool invocation.

    Far faster than one call per folder when scanning a whole drive, because
    exiftool process startup -- not the parsing -- is the bottleneck on Windows.
    """
    folders = list(folders)
    if not folders:
        return {}
    exe = find_exiftool(exiftool)
    records = _run_exiftool(exe, folders)

    by_dir: dict[str, list[TileMeta]] = {f: [] for f in folders}
    norm = {os.path.normcase(os.path.abspath(f)): f for f in folders}
    for rec in records:
        name = rec.get("FileName", "")
        if not name.lower().endswith((".jpg", ".jpeg")):
            continue
        directory = (rec.get("Directory") or "").replace("/", os.sep)
        key = norm.get(os.path.normcase(os.path.abspath(directory)))
        if key is None:
            continue
        by_dir[key].append(TileMeta(
            path=os.path.join(directory, name),
            filename=name,
            yaw=as_float(rec.get("GimbalYawDegree")),
            pitch=as_float(rec.get("GimbalPitchDegree")),
            roll=as_float(rec.get("GimbalRollDegree")),
            width=int(as_float(rec.get("ImageWidth"), 2000)),
            height=int(as_float(rec.get("ImageHeight"), 1500)),
            exposure_time=as_float(rec.get("ExposureTime"), 1.0) or 1.0,
            iso=as_float(rec.get("ISO"), 100.0) or 100.0,
            f_number=as_float(rec.get("FNumber"), 2.8) or 2.8,
            datetime=rec.get("DateTimeOriginal") or rec.get("CreateDate"),
            lat=as_opt_float(rec.get("GPSLatitude")),
            lon=as_opt_float(rec.get("GPSLongitude")),
            altitude=as_opt_float(rec.get("GPSAltitude")),
            rel_altitude=as_opt_float(rec.get("RelativeAltitude")),
            model=rec.get("Model"),
            serial=rec.get("SerialNumber"),
            has_gimbal=rec.get("GimbalYawDegree") is not None,
        ))

    for tiles in by_dir.values():
        tiles.sort(key=lambda t: t.filename)
    return by_dir
