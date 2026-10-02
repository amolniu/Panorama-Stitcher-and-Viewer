"""The small HTTP API behind the viewer's Print panel.

This turns the static file server into something that writes files and runs a
subprocess (exiftool) on request, so it is built defensively:

* It binds to 127.0.0.1 only (``serve`` enforces this) and refuses any request whose
  ``Host`` header is not loopback, which closes DNS rebinding -- a page on
  ``attacker.example`` whose DNS answer flips to 127.0.0.1 would otherwise be
  same-origin with us and could read the library (every flight's GPS) and the token.
* Every mutating request needs a per-session token injected into the served
  ``index.html``. A page on another origin cannot read our HTML, so it cannot obtain the
  token -- and its cross-origin POST also fails the Origin check. Both guards are kept:
  either alone would be a single point of failure.
* It accepts library IDS only, never paths, and confirms the id's file really lives
  under the library directory even if ``library.json`` has been tampered with.
* Sizes, dpi, pixel count and text are validated and bounded BEFORE a job is accepted,
  so a bad request is a 400 rather than a job that fails later, and nobody can ask for
  a render that needs more memory than the machine has.
* Renders run one at a time in a worker thread; the client polls a job. Finished files
  are written to a temporary name and renamed into place, so a reload never serves a
  half-written JPEG, and every distinct request gets a distinct filename.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
import re
import secrets
import socket
import threading
import time
import unicodedata
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import numpy as np
from PIL import Image

from .capture import enrich_from_library, file_digest, read_capture
from .geonames import nearest_place
from .paper import NAMED_SIZES, Paper, assess_fit, parse_size
from .plate import STYLES, render, save_print, view_box
from .project import (VIEW_MODES, View, heading_of_view, native_view_width, render_view,
                      TWO_PI)

Image.MAX_IMAGE_PIXELS = None

MAX_SIDE_PX = 12000
MAX_PIXELS = 120_000_000        # 24x36 at 300 dpi is 78 MP and must still work
DPI_MIN, DPI_MAX = 72, 400
MAX_BODY = 64 * 1024
MAX_JOBS = 50                   # finished records kept for polling
MAX_LIVE_JOBS = 4               # queued + running before we answer 429
PROOF_DPI = 72
TITLE_MAX = 120
REQUEST_TIMEOUT_S = 30


class PrintContext:
    """Everything the handler needs, shared across requests."""

    def __init__(self, out_dir: str, port: int, exiftool: str | None = None):
        self.out_dir = os.path.realpath(out_dir)
        self.port = port
        self.exiftool = exiftool
        self.token = secrets.token_urlsafe(24)
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.render_slot = threading.BoundedSemaphore(1)   # one render at a time
        self._lib: dict | None = None
        self._lib_mtime = 0.0
        self.allowed_origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}

    # -- library ------------------------------------------------------------
    def library(self) -> dict:
        """The library, re-read when its mtime changes; the last good copy on a bad read.

        ``build`` rewrites the file; a read that lands mid-write must not drop the
        connection, so a failed load keeps serving the previous library.
        """
        path = os.path.join(self.out_dir, "library.json")
        try:
            m = os.path.getmtime(path)
        except OSError:
            return self._lib or {"panoramas": []}
        if self._lib is None or m != self._lib_mtime:
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    lib = json.load(fh)
            except (OSError, ValueError):
                return self._lib or {"panoramas": []}
            if isinstance(lib, dict):
                self._lib, self._lib_mtime = lib, m
        return self._lib or {"panoramas": []}

    def entry(self, pid: str) -> dict | None:
        for e in self.library().get("panoramas", []):
            if (isinstance(e, dict) and e.get("id") == pid and e.get("status") == "ok"
                    and isinstance(e.get("equirect"), str)):
                return e
        return None

    def confined(self, rel: str) -> str:
        """Resolve a library-relative path and refuse anything outside the library.

        Defence in depth: the id lookup already limits us to entries the build wrote,
        but a tampered library.json must not be able to point exiftool, PIL and the
        digest at an arbitrary file and publish the result.
        """
        full = os.path.realpath(os.path.join(self.out_dir, rel.replace("/", os.sep)))
        if os.path.commonpath([full, self.out_dir]) != self.out_dir:
            raise BadRequest("library entry points outside the library")
        return full

    # -- jobs ---------------------------------------------------------------
    def new_job(self, req: dict) -> str | None:
        """Register a job and start it. Returns None when too many are already live."""
        jid = uuid.uuid4().hex[:12]
        with self.lock:
            live = sum(1 for j in self.jobs.values() if j["state"] in ("queued", "running"))
            if live >= MAX_LIVE_JOBS:
                return None
            # evict only FINISHED records; a live one must stay pollable
            finished = sorted((k for k, j in self.jobs.items() if j["state"] in ("done", "error")),
                              key=lambda k: self.jobs[k]["created"])
            while len(self.jobs) >= MAX_JOBS and finished:
                del self.jobs[finished.pop(0)]
            self.jobs[jid] = {"state": "queued", "progress": 0.0, "created": time.time(),
                              "result": None, "error": None}
        threading.Thread(target=self._run, args=(jid, req), daemon=True).start()
        return jid

    def snapshot(self, jid: str) -> dict | None:
        with self.lock:
            j = self.jobs.get(jid)
            return None if j is None else {k: j[k] for k in ("state", "progress", "result", "error")}

    def _set(self, jid: str, **kw) -> None:
        with self.lock:
            if jid in self.jobs:
                self.jobs[jid].update(kw)

    def _run(self, jid: str, req: dict) -> None:
        with self.render_slot:                       # 'queued' is a real state now
            try:
                self._set(jid, state="running", progress=0.05)
                result = run_print(self, req, lambda p: self._set(jid, progress=p))
                self._set(jid, state="done", progress=1.0, result=result)
            except Exception as exc:  # the job must report, never take the server down
                # our own messages are written for the panel; anything else is a bug
                # report and keeps its class name so it can be found in the code
                ours = isinstance(exc, (RuntimeError, BadRequest))
                self._set(jid, state="error",
                          error=str(exc) if ours else f"{exc.__class__.__name__}: {exc}")


# --------------------------------------------------------------------------- validation

class BadRequest(ValueError):
    pass


def _num(v, lo: float, hi: float, name: str) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError, OverflowError):
        raise BadRequest(f"{name} must be a number")
    if not math.isfinite(f):
        raise BadRequest(f"{name} must be finite")
    return min(hi, max(lo, f))


def _choice(v, allowed, name: str, default):
    if v is None:
        return default
    if not isinstance(v, str) or v not in allowed:
        raise BadRequest(f"{name} must be one of {', '.join(sorted(allowed))}")
    return v


#: Unicode categories that never belong in a title: control, format (bidi/zero-width),
#: surrogates (a lone one cannot be encoded to UTF-8 and would fail the sidecar write
#: after the print was already made), private use, unassigned, line/paragraph separators.
TITLE_DROP = ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp")


def clean_title(title) -> str | None:
    """Strip characters that cannot be typeset or stored (see TITLE_DROP)."""
    if title is None:
        return None
    if not isinstance(title, str):
        raise BadRequest("title must be text")
    kept = "".join(c for c in title if unicodedata.category(c) not in TITLE_DROP)
    return kept.strip()[:TITLE_MAX] or None


def check_paper(paper: Paper) -> None:
    """Reject sizes that are absurd or that would need more memory than is reasonable."""
    if min(paper.width_in, paper.height_in) < 1.0:
        raise BadRequest("paper must be at least 1 inch a side")
    if not 0.1 <= paper.aspect <= 10.0:
        raise BadRequest("paper must be no more than 10:1")
    if paper.width_px > MAX_SIDE_PX or paper.height_px > MAX_SIDE_PX:
        raise BadRequest(f"that size at {paper.dpi} dpi is {paper.width_px}x{paper.height_px} px; "
                         f"the limit is {MAX_SIDE_PX} px a side -- lower the dpi")
    px = paper.width_px * paper.height_px
    if px > MAX_PIXELS:
        raise BadRequest(f"that size at {paper.dpi} dpi is {px / 1e6:.0f} megapixels; "
                         f"the limit is {MAX_PIXELS // 10**6} MP -- lower the dpi")


def validate(body) -> dict:
    """Turn an untrusted request body into a clean, bounded one -- or raise BadRequest."""
    if not isinstance(body, dict):
        raise BadRequest("body must be an object")
    pid = body.get("id")
    if not isinstance(pid, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", pid):
        raise BadRequest("id is required and must be a library id")

    source = _choice(body.get("source"), ("view", "equirect"), "source", "view")
    style = _choice(body.get("style"), STYLES, "style", "gallery")
    fit = _choice(body.get("fit"), ("contain", "cover"), "fit", "contain")

    size = body.get("size", "a3")
    if not isinstance(size, str) or not 1 <= len(size) <= 24:
        raise BadRequest("size is not valid")
    dpi = int(_num(body.get("dpi", 300), DPI_MIN, DPI_MAX, "dpi"))
    proof = bool(body.get("proof", False))
    # The side and area limits do not depend on orientation, so the paper can be
    # checked here, as a 400, rather than inside the job as a mysterious failure.
    try:
        check_paper(parse_size(size, PROOF_DPI if proof else dpi, "landscape"))
    except ValueError as exc:
        raise BadRequest(str(exc))

    title = clean_title(body.get("title"))

    view = None
    screen_aspect = None
    if source == "view":
        v = body.get("view") or {}
        if not isinstance(v, dict):
            raise BadRequest("view must be an object")
        mode = _choice(v.get("mode"), VIEW_MODES, "mode", "immersive")
        yaw = _num(v.get("yaw", 0.0), -1e9, 1e9, "yaw")
        view = View(
            mode=mode,
            yaw=math.remainder(yaw, TWO_PI),      # the viewer lets yaw grow unbounded
            pitch=_num(v.get("pitch", 0.0), -math.pi, math.pi, "pitch"),
            roll=_num(v.get("roll", 0.0), -math.pi, math.pi, "roll"),
            fov=_num(v.get("fov", 75.0), 10.0, 330.0, "fov"),
            morph=_num(v.get("morph", 0.0), 0.0, 1.0, "morph"),
            exposure=_num(v.get("exposure", 1.0), 0.25, 3.0, "exposure"),
        )
        screen_aspect = _num(v.get("aspect", 1.5), 0.2, 5.0, "aspect")

    return {"id": pid, "source": source, "style": style, "fit": fit, "size": size,
            "dpi": dpi, "title": title, "view": view, "screen_aspect": screen_aspect,
            "proof": proof, "record": bool(body.get("record", True))}


# --------------------------------------------------------------------------- rendering

def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", s).strip("-")[:60] or "print"


def _request_key(req: dict, title: str | None, paper: Paper) -> str:
    """Six hex digits that differ whenever the print would differ.

    Two prints of the same panorama at the same paper and style but a different dpi,
    title, fit, exposure or sub-degree yaw must not share a filename, or the second
    silently replaces the first while the panel says "Saved" for both. The *resolved*
    paper goes in, not the request's size name: ``5x7`` is the same name in landscape
    and portrait, and a view print's orientation follows the browser window.
    """
    view = dataclasses.asdict(req["view"]) if req["view"] else None
    key = json.dumps({"id": req["id"], "paper": [paper.width_in, paper.height_in, paper.dpi],
                      "fit": req["fit"], "title": title, "style": req["style"],
                      "source": req["source"], "view": view}, sort_keys=True)
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:6]


def _orientation(paper: Paper) -> str:
    if abs(paper.width_in - paper.height_in) < 1e-6:
        return "square"
    return "landscape" if paper.width_in > paper.height_in else "portrait"


REPLACE_TRIES = 4
REPLACE_SIBLINGS = 9


def _try_replace(src: str, dst: str, tries: int) -> bool:
    """``os.replace`` with retries; False when ``dst`` (or ``src``) stays locked.

    On Windows a rename fails with PermissionError while either file is open in another
    program. Anything else (a vanished source, a bad path) is a real error and raises.
    """
    for attempt in range(tries):
        try:
            os.replace(src, dst)
            return True
        except PermissionError:
            time.sleep(0.15 * (attempt + 1))
    return False


def _publish_pair(part: str, side_part: str | None, jpg: str) -> tuple[str, str | None]:
    """Rename the finished image -- and its record, if any -- into place together.

    A stem is taken only if BOTH its names can be taken; otherwise the next numbered
    sibling (``X-2``, ``X-3`` ...) is tried for both, so the record always carries the
    image's exact name. An earlier record under the chosen stem is set aside before
    ours replaces it and restored if the image cannot follow, so a re-print never
    leaves a record without its image or an image without its record. If the earlier
    record cannot be put back, that is an error, never a success with a broken pair.
    When no stem can be taken, the temporaries are removed and the error says which
    file is in the way. Returns the final paths.
    """
    stem0, ext = os.path.splitext(jpg)
    stems = [stem0] + [f"{stem0}-{n}" for n in range(2, REPLACE_SIBLINGS + 1)]
    record_at = None            # where our record currently sits, if placed
    prev_of = None              # (prev_path, record_path, (atime_ns, mtime_ns)) while an
                                # earlier record is set aside
    try:
        for i, stem in enumerate(stems):
            tries = REPLACE_TRIES if i == 0 else 1
            image = stem + ext
            if side_part is None:
                if _try_replace(part, image, tries):
                    return image, None
                continue
            record = stem + ".json"
            prev = record + ".prev"
            if os.path.exists(record):
                try:
                    st = os.stat(record)
                    stamps = (st.st_atime_ns, st.st_mtime_ns)
                except OSError:
                    stamps = None
                if not _try_replace(record, prev, tries):
                    continue                               # the earlier record is locked
                prev_of = (prev, record, stamps)
                # the set-aside copy keeps the earlier record's mtime; stamp it now so a
                # concurrent sweep's age rule protects it like any other in-flight file
                try:
                    os.utime(prev, None)
                except OSError:
                    pass
            if not _try_replace(side_part, record, tries):
                if prev_of and not _put_back(prev_of):
                    raise _restore_failed(prev_of)
                prev_of = None
                continue
            record_at = record
            if _try_replace(part, image, tries):
                if prev_of:
                    _remove_with_backoff(prev_of[0])
                return image, record
            # the image is blocked under this stem: put everything back, try the next
            if not _try_replace(record, side_part, REPLACE_TRIES):
                raise RuntimeError("could not save the print: its record is locked by "
                                   "another program moments after it was written -- "
                                   "print again in a moment")
            record_at = None
            if prev_of and not _put_back(prev_of):
                raise _restore_failed(prev_of)
            prev_of = None
    except BaseException as exc:
        # Undo whatever this stem had done, best effort, then clear our temporaries.
        # The undo itself must not raise: if the file it would move has vanished too,
        # the temporaries still have to go and the message still has to be ours.
        if record_at and side_part and _undo(record_at, side_part):
            record_at = None
        if prev_of and _put_back(prev_of, raising=False):
            prev_of = None
        _remove_with_backoff(part)
        if side_part:
            _remove_with_backoff(side_part)
        if isinstance(exc, FileNotFoundError):
            # one of our own files disappeared mid-publish: a quarantine, a deletion
            # from Explorer, another viewer's clean-up
            raise RuntimeError("could not save the print: a temporary file vanished while "
                               "it was being saved (another viewer's clean-up, a virus "
                               "scanner, or it was deleted) -- print again") from exc
        raise
    # Every stem failed. Either the destinations are all open (unlikely) or a temporary
    # file itself is locked -- a virus scanner or indexer reading a fresh JPEG does that
    # on Windows -- and the message must say which, or it sends the user to close a
    # file that is not open. Both removals are attempted whatever the first one says.
    part_gone = _remove_with_backoff(part)
    side_gone = side_part is None or _remove_with_backoff(side_part)
    if part_gone and side_gone:
        raise RuntimeError("could not save the print: a file with this name is open in "
                           "another program; close it and print again")
    raise RuntimeError("could not save the print: its temporary file is locked, probably "
                       "by a virus scanner or indexer; it is cleared the next time the "
                       "viewer starts -- print again in a moment")


def _put_back(prev_of: tuple, raising: bool = True) -> bool:
    """Return an earlier record from its .prev name, with its own timestamps.

    The set-aside copy was stamped with the current time (so the sweep treats it as in
    flight); the record must not keep that stamp once it is a record again, or it would
    carry a date its image does not. ``raising=False`` is the failure-path variant that
    never raises.
    """
    prev, record, stamps = prev_of
    ok = _undo(prev, record) if not raising else _try_replace(prev, record, REPLACE_TRIES)
    if ok and stamps:
        try:
            os.utime(record, ns=stamps)
        except OSError:
            pass
    return ok


def _undo(src: str, dst: str) -> bool:
    """A best-effort move for the failure path: False on any OS error, never raises."""
    try:
        return _try_replace(src, dst, REPLACE_TRIES)
    except OSError:
        return False


def _rename_no_clobber(src: str, dst: str) -> bool:
    """Move ``src`` to ``dst`` only if ``dst`` does not exist; False if it does.

    ``os.replace`` would overwrite, which is wrong for the sweep's restore of an orphaned
    record: a publish running in another viewer may have just placed a new ``dst``.
    A hard link is atomic no-clobber on both platforms; where links are unsupported
    the fallback is a plain rename guarded by an existence check.
    """
    try:
        os.link(src, dst)
        os.unlink(src)
        return True
    except FileExistsError:
        return False
    except OSError:
        if os.path.exists(dst):
            return False
        os.rename(src, dst)
        return True


def _restore_failed(prev_of: tuple) -> RuntimeError:
    return RuntimeError(f"could not save the print, and the earlier record could not be put "
                        f"back: it is at {prev_of[0]} -- rename it to {os.path.basename(prev_of[1])} "
                        f"once the program holding it is closed")


def _remove_with_backoff(path: str) -> bool:
    """Delete ``path``; True when it is gone (or never was), False when it stays locked."""
    for attempt in range(REPLACE_TRIES):
        try:
            os.remove(path)
            return True
        except FileNotFoundError:
            return True
        except PermissionError:
            time.sleep(0.15 * (attempt + 1))
        except OSError:
            return False
    return not os.path.exists(path)


TEMP_SUFFIXES = (".jpg.part", ".json.part", ".json.prev")
TEMP_MIN_AGE_S = 15 * 60
TEMP_PREV_GRACE_S = 10          # a .prev younger than this may belong to a running publish


def sweep_temp_files(out_dir: str) -> int:
    """Remove temporary files left by an interrupted run.

    Only the two directories this API writes, only the names it creates, only files
    older than TEMP_MIN_AGE_S, and never through a link or junction: a user who links a
    folder into the prints directory must not lose other programs' ``*.part``
    downloads, and a second viewer started on another port against the same library
    must not sweep the first one's files mid-write. Called once the port is bound.
    """
    n = 0
    cutoff = time.time() - TEMP_MIN_AGE_S
    root = os.path.join(out_dir, "prints", "from-viewer")
    for d in (root, os.path.join(root, ".proofs")):
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            try:
                linked = e.is_symlink() or getattr(e, "is_junction", lambda: False)()
                if linked or not e.is_file(follow_symlinks=False):
                    continue
            except OSError:
                continue
            if not e.name.endswith(TEMP_SUFFIXES):
                continue
            try:
                mtime = e.stat(follow_symlinks=False).st_mtime
                if e.name.endswith(".json.prev") and not os.path.exists(e.path[:-len(".prev")]):
                    # an earlier record set aside by a publish that never finished: it is
                    # the only copy, so it goes back where it was, whatever its age --
                    # unless it is seconds old, when a publish may still be running
                    if mtime > time.time() - TEMP_PREV_GRACE_S:
                        continue
                    _rename_no_clobber(e.path, e.path[:-len(".prev")])
                    continue
                if mtime > cutoff:
                    continue
                os.remove(e.path)
                n += 1
            except OSError:
                pass
    return n


def run_print(ctx: PrintContext, req: dict, progress) -> dict:
    """Produce the print described by a validated request. Returns the result record."""
    entry = ctx.entry(req["id"])
    if entry is None:
        raise BadRequest("no such panorama in the library")
    eq_path = ctx.confined(entry["equirect"])
    if not os.path.isfile(eq_path):
        raise BadRequest("the panorama's image file is missing; rebuild the library")

    cap = enrich_from_library(read_capture(eq_path, ctx.exiftool), ctx.out_dir)
    progress(0.15)

    dpi = PROOF_DPI if req["proof"] else req["dpi"]
    style = req["style"]

    title = req["title"]
    place = None
    if title is None and cap.has_position:
        place = nearest_place(cap.lat, cap.lon)
        title = place.label() if place else None

    with Image.open(eq_path) as im:
        im.load()
        eq_w, eq_h = im.size

        if req["source"] == "view":
            view: View = req["view"]
            # The bearing is part of the caption, and the caption's row count sets the
            # image box -- so it must be known BEFORE the box is measured, or the view
            # is rendered one line taller than the box and 'cover' crops it.
            if view.mode in ("immersive", "fisheye", "pannini"):
                cap.view_heading = heading_of_view(view)

            paper = parse_size(req["size"], dpi, "auto", req["screen_aspect"])
            check_paper(paper)
            box_w, box_h = view_box(cap, paper, style, title, footnote=False)

            cov = entry.get("coverage") or {}
            lon0 = math.radians(cov.get("lon_min", -180.0))
            lon1 = math.radians(cov.get("lon_max", 180.0))
            if lon1 <= lon0:                    # tolerate a range written the other way
                lon1 += TWO_PI
            lat_r = (math.radians(cov.get("lat_min", -90.0)), math.radians(cov.get("lat_max", 90.0)))

            pano = np.asarray(im.convert("RGB"), dtype=np.float32) / 255.0
            progress(0.3)
            img = render_view(pano, view, box_w, box_h, (lon0, lon1), lat_r,
                              background=(14 / 255, 17 / 255, 22 / 255),
                              exposure=view.exposure,
                              progress=lambda f: progress(0.3 + 0.45 * f))
            del pano
            source_img = Image.fromarray(img)
            del img

            plate, fit_report = render(cap, source_img, paper, style=style, title=title,
                                       fit="cover", footnote=False)
            if fit_report.cropped_fraction > 1e-6 or abs(fit_report.scale - 1.0) > 1e-3:
                raise RuntimeError("internal: view box and plate box disagree")
            native_w = native_view_width(eq_w, view, aspect=box_w / max(box_h, 1),
                                         lon_span=lon1 - lon0)
            native_h = max(1, int(round(native_w * box_h / max(box_w, 1))))
            report = assess_fit(native_w, native_h, box_w, box_h,
                                (0, 0, native_w, native_h), paper)
            hdg = round(heading_of_view(view)) % 360
            tag = f"view{hdg:03d}-{math.degrees(view.pitch):+03.0f}-f{view.fov:.0f}"
        else:
            paper = parse_size(req["size"], dpi, "auto", eq_w / max(eq_h, 1))
            check_paper(paper)
            progress(0.3)
            plate, report = render(cap, im, paper, style=style, title=title,
                                   fit=req["fit"], footnote=False)
            tag = "equirect"

    progress(0.85)
    sub = ".proofs" if req["proof"] else ""
    dest_dir = os.path.join(ctx.out_dir, "prints", "from-viewer", sub)
    os.makedirs(dest_dir, exist_ok=True)
    # The id, not the folder name: DJI reuses 100_0858-style names across cards, and
    # the library has 20 such duplicates. The id carries the folder name plus a hash.
    stem = (f"{_slug(req['id'])}-{tag}-{paper.name}-{_orientation(paper)}-"
            f"{paper.dpi}dpi-{style}-{_request_key(req, title, paper)}")
    jpg = os.path.join(dest_dir, stem + ".jpg")
    part = jpg + ".part"
    try:
        save_print(plate, part, paper, jpeg_quality=80 if req["proof"] else 95)
    except BaseException:
        _remove_with_backoff(part)
        raise

    side_part = None
    if not req["proof"] and req["record"]:
        from ._print_cmd import write_sidecar
        side_part = os.path.join(dest_dir, stem + ".json.part")
        extra = {
            "paper": paper.name,
            "paper_inches": [round(paper.width_in, 3), round(paper.height_in, 3)],
            "dpi": paper.dpi, "pixels": [plate.width, plate.height], "style": style,
            "effective_dpi": round(report.effective_dpi, 1),
            "enlargement": round(report.scale, 3),
            "title": title, "title_source": "explicit" if req["title"] else ("derived" if place else None),
            "source_sha256_short": file_digest(eq_path),
            "made_from": "viewer",
        }
        if req["source"] == "view":
            v = req["view"]
            extra["view"] = {"mode": v.mode, "yaw_deg": round(math.degrees(v.yaw) % 360.0, 2),
                             "pitch_deg": round(math.degrees(v.pitch), 2),
                             "roll_deg": round(math.degrees(v.roll), 2),
                             "fov_deg": round(v.fov, 1), "morph": round(v.morph, 3),
                             "exposure": round(v.exposure, 3),
                             "note": "A rendered framing of the panorama, not a camera frame. "
                                     "'view facing' on the plate is this framing's bearing."}
        try:
            write_sidecar(side_part, cap, extra)
        except BaseException:
            # publish nothing: a print without its record is not what was asked for
            _remove_with_backoff(part)
            _remove_with_backoff(side_part)
            raise

    # Both files are complete under temporary names; only now do they become visible,
    # together, under one stem (see _publish_pair).
    dest, sidecar = _publish_pair(part, side_part, jpg)

    rel = os.path.relpath(dest, ctx.out_dir).replace(os.sep, "/")
    return {
        # `path` is absolute on purpose: this is a loopback tool for the machine's own
        # user, and the point of the result is to tell them where their file went.
        "url": "/" + rel, "path": dest,
        "sidecar": ("/" + os.path.relpath(sidecar, ctx.out_dir).replace(os.sep, "/")) if sidecar else None,
        "width": plate.width, "height": plate.height,
        "paper": paper.describe(), "effective_dpi": round(report.effective_dpi),
        "warning": report.warning, "title": title, "proof": req["proof"],
    }


# --------------------------------------------------------------------------- handler

def make_handler(ctx: PrintContext):
    class Handler(SimpleHTTPRequestHandler):
        timeout = REQUEST_TIMEOUT_S

        def __init__(self, *a, **kw):
            super().__init__(*a, directory=ctx.out_dir, **kw)

        def log_message(self, fmt, *a):  # keep the console for the user's own output
            pass

        def list_directory(self, path):   # the library is browsed through the viewer
            self.send_error(403)
            return None

        # -- helpers
        def _json(self, code: int, obj) -> None:
            data = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _host_ok(self) -> bool:
            """Only loopback names may address this server (defeats DNS rebinding)."""
            host = self.headers.get("Host", "")
            name, _, port = host.partition(":")
            return name in ("127.0.0.1", "localhost") and (port == "" or port == str(ctx.port))

        def _authorised(self) -> bool:
            origin = self.headers.get("Origin")
            if origin and origin not in ctx.allowed_origins:
                return False
            tok = self.headers.get("X-Print-Token", "")
            return secrets.compare_digest(tok.encode("latin-1", "replace"), ctx.token.encode("ascii"))

        # -- GET / HEAD
        def do_HEAD(self):
            if not self._host_ok():
                return self.send_error(421)
            return super().do_HEAD()

        def do_GET(self):
            if not self._host_ok():
                return self.send_error(421)
            path = urlparse(self.path).path
            if path.startswith("/api/jobs/"):
                snap = ctx.snapshot(path.rsplit("/", 1)[-1])
                return self._json(404, {"error": "no such job"}) if snap is None else self._json(200, snap)
            if path in ("/", "/index.html"):
                return self._serve_index()
            if path.startswith("/api/"):
                return self._json(404, {"error": "unknown endpoint"})
            return super().do_GET()

        def _serve_index(self):
            p = os.path.join(ctx.out_dir, "index.html")
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    html = fh.read()
            except OSError:
                return self.send_error(404)
            data = html.replace("__PRINT_TOKEN__", ctx.token).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        # -- POST
        def do_POST(self):
            if not self._host_ok():
                return self.send_error(421)
            path = urlparse(self.path).path
            if path != "/api/print":
                return self._json(404, {"error": "unknown endpoint"})
            if not self._authorised():
                return self._json(403, {"error": "not authorised"})
            try:
                n = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self._json(400, {"error": "bad length"})
            if n <= 0:
                return self._json(400, {"error": "empty body"})
            if n > MAX_BODY:
                return self._json(413, {"error": "body too large"})
            try:
                body = json.loads(self.rfile.read(n).decode("utf-8"))
                req = validate(body)
                if ctx.entry(req["id"]) is None:
                    return self._json(404, {"error": "no such panorama"})
            except BadRequest as exc:
                return self._json(400, {"error": str(exc)})
            except (ValueError, TypeError, OverflowError, RecursionError, UnicodeDecodeError) as exc:
                return self._json(400, {"error": f"malformed request: {exc.__class__.__name__}"})
            except Exception as exc:
                return self._json(500, {"error": exc.__class__.__name__})
            jid = ctx.new_job(req)
            if jid is None:
                return self._json(429, {"error": "too many prints in progress; try again shortly"})
            return self._json(202, {"job": jid})

    return Handler


class _Server(ThreadingHTTPServer):
    """Loopback server that refuses to share its port.

    ``allow_reuse_address`` is off: on Windows SO_REUSEADDR would let another process
    bind the same port and intercept the viewer; SO_EXCLUSIVEADDRUSE is the opposite.
    """
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def serve(out_dir: str, port: int, exiftool: str | None = None) -> ThreadingHTTPServer:
    """Build the server. Bound to loopback only: this API writes files."""
    ctx = PrintContext(out_dir, port, exiftool)
    httpd = _Server(("127.0.0.1", port), make_handler(ctx))
    # Only once the port is ours. A second launch against a busy port must die here
    # without touching the running instance's temporary files.
    sweep_temp_files(ctx.out_dir)
    # port 0 means "any free port"; the Host and Origin checks must use the one bound
    real_port = httpd.server_address[1]
    ctx.port = real_port
    ctx.allowed_origins = {f"http://127.0.0.1:{real_port}", f"http://localhost:{real_port}"}
    httpd.print_context = ctx  # type: ignore[attr-defined]
    return httpd


def size_names() -> list[str]:
    return sorted(NAMED_SIZES)
