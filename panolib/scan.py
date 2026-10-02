"""Finding panorama sets under a folder the user points at.

The user may point at a single set, at a ``PANORAMA`` folder, at an SD-card backup,
or at a whole external drive, so discovery has to work at every level without being
told which one it is.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field

SET_DIR_RE = re.compile(r"^\d{3}_\d{4}$")
MEDIA_DIR_RE = re.compile(r"^\d{3}MEDIA$", re.I)
JPEG_EXT = (".jpg", ".jpeg")

#: Directories that never contain drone panoramas and can be very large.
SKIP_DIRS = {
    "node_modules", ".git", ".svn", "__pycache__", "$RECYCLE.BIN",
    "System Volume Information", ".venv", "venv", "AppData", "Windows",
    "Program Files", "Program Files (x86)", ".cache", "dist", "build",
}


@dataclass
class PanoSet:
    """A candidate panorama: one folder of tiles."""

    id: str
    name: str
    path: str
    files: list[str]
    trip: str
    reasons: list[str] = field(default_factory=list)

    @property
    def tile_count(self) -> int:
        return len(self.files)


def _jpegs(path: str) -> list[str]:
    try:
        with os.scandir(path) as it:
            return sorted(e.name for e in it
                          if e.is_file() and e.name.lower().endswith(JPEG_EXT))
    except OSError:
        return []


def make_id(path: str, name: str) -> str:
    """Stable id that cannot collide across roots.

    Set folder names repeat constantly -- ``100_0679`` exists under several different
    SD-card backups in this archive -- so the folder name alone would silently merge
    unrelated panoramas and overwrite their outputs. The full path is hashed in.
    """
    digest = hashlib.sha1(os.path.normcase(os.path.abspath(path)).encode("utf-8", "replace"))
    return f"{name}-{digest.hexdigest()[:8]}"


def _trip_name(path: str, roots: list[str]) -> str:
    """A human label for the grouping, taken from the folder structure.

    For ``E:\\Scotland\\Drone\\2. 07.07\\PANORAMA\\100_0496`` this yields "Scotland":
    the first path component below the drive that is not a generic container.
    """
    generic = {"dcim", "panorama", "drone", "media", "photos", "pictures", "images", "100media"}
    abspath = os.path.abspath(path)
    drive, rest = os.path.splitdrive(abspath)
    parts = [p for p in rest.split(os.sep) if p]
    parts = parts[:-1]  # drop the set folder itself
    for p in parts:
        if p.lower() not in generic and not SET_DIR_RE.match(p):
            return p
    return parts[0] if parts else (drive or "Panoramas")


def looks_like_set(path: str, files: list[str]) -> tuple[bool, list[str]]:
    """Decide whether a directory of JPEGs is a panorama set, and say why.

    This is the cheap filesystem-only pass; the build step confirms each candidate
    from its EXIF afterwards. It has to be *specific*, not merely permissive: an
    ordinary DCIM camera-roll folder is also full of sequentially named DJI_NNNN
    JPEGs, and on this archive treating that naming as sufficient pulled in 19 camera
    rolls -- one of them with 963 photos in it.

    So membership requires a structural signal (living under ``PANORAMA``, or a
    ``NNN_NNNN`` set-folder name); DJI tile naming only corroborates.
    """
    if not files:
        return False, []
    name = os.path.basename(path.rstrip(os.sep))
    parent = os.path.basename(os.path.dirname(path.rstrip(os.sep)))

    if MEDIA_DIR_RE.match(name):
        return False, []          # 100MEDIA / 101MEDIA etc. are camera rolls

    reasons = []
    if parent.upper() == "PANORAMA":
        reasons.append("inside a PANORAMA folder")
    if SET_DIR_RE.match(name):
        reasons.append("folder named like a DJI set")
    if not reasons:
        return False, []

    if len(files) >= 3 and all(f.upper().startswith("DJI_") for f in files):
        reasons.append("DJI_NNNN tile naming")
    return True, reasons


def scan(roots: list[str], max_depth: int = 8, follow_links: bool = False) -> list[PanoSet]:
    """Walk ``roots`` and return every panorama set found, sorted by path.

    Accepts a set folder, a ``PANORAMA`` folder, or an entire drive.
    """
    found: list[PanoSet] = []
    seen: set[str] = set()

    for root in roots:
        root = os.path.abspath(root)
        if not os.path.isdir(root):
            continue

        # pointed straight at one set
        files = _jpegs(root)
        ok, reasons = looks_like_set(root, files)
        if ok:
            key = os.path.normcase(root)
            if key not in seen:
                seen.add(key)
                name = os.path.basename(root.rstrip(os.sep))
                found.append(PanoSet(make_id(root, name), name, root, files,
                                     _trip_name(root, roots), reasons))
            continue

        base_depth = root.rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, filenames in os.walk(root, followlinks=follow_links):
            if dirpath.rstrip(os.sep).count(os.sep) - base_depth >= max_depth:
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames
                           if d not in SKIP_DIRS and not d.startswith("$")]

            jpegs = sorted(f for f in filenames if f.lower().endswith(JPEG_EXT))
            if not jpegs:
                continue
            ok, reasons = looks_like_set(dirpath, jpegs)
            if not ok:
                continue
            key = os.path.normcase(dirpath)
            if key in seen:
                continue
            seen.add(key)
            name = os.path.basename(dirpath.rstrip(os.sep))
            found.append(PanoSet(make_id(dirpath, name), name, dirpath, jpegs,
                                 _trip_name(dirpath, roots), reasons))

    found.sort(key=lambda s: os.path.normcase(s.path))
    return found
