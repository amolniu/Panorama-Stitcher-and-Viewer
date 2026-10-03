"""Turning a scanned panorama set into viewable outputs and a manifest entry."""

from __future__ import annotations

import json
import math
import os
import time
import traceback
from dataclasses import dataclass

import numpy as np
from PIL import Image

from .classify import classify, missing_tiles, recommended_canvas
from .exif import TileMeta, read_tiles
from .geometry import Camera, recommended_width
from .quality import score as score_quality
from .refine import refine as refine_angles
from .scan import PanoSet
from .stitch import (StitchResult, crop_to_coverage, fill_zenith, linear_to_srgb,
                     stitch)
from .tonemap import dynamic_range, local_tonemap

CACHE_VERSION = 5  # bump when output would change, to invalidate cached results
#: Bump when what write_gpano writes changes. Unlike CACHE_VERSION this re-stitches
#: nothing: a cached panorama whose tags are older is re-tagged in place on the next
#: build. Without it a metadata fix silently never reaches panoramas already stitched --
#: which is how the signed DJI altitude added in version 2 went missing from all 135.
META_VERSION = 2


@dataclass
class BuildOptions:
    out_dir: str
    width: int = 0                 # 0 = choose from the camera's native resolution
    preview_width: int = 2048
    thumb_width: int = 512
    quality_jpeg: int = 90
    tonemap: str = "local"         # local | global | none
    compression: float | None = None
    fill_cap: bool = True
    refine: bool = True
    make_planet: bool = True
    force: bool = False


def cache_key(pano: PanoSet, tiles: list[TileMeta], opts: BuildOptions) -> str:
    """Identity of the inputs AND the settings, so stale outputs are never reused."""
    import hashlib
    h = hashlib.sha1()
    h.update(f"v{CACHE_VERSION}|{opts.width}|{opts.tonemap}|{opts.compression}|"
             f"{opts.fill_cap}|{opts.preview_width}|{opts.quality_jpeg}|"
             f"refine={opts.refine}".encode())
    for t in sorted(tiles, key=lambda x: x.filename):
        try:
            st = os.stat(t.path)
            stamp = f"{t.filename}:{st.st_size}:{int(st.st_mtime)}"
        except OSError:
            stamp = f"{t.filename}:missing"
        h.update(stamp.encode("utf-8", "replace"))
        h.update(f":{t.yaw:.2f},{t.pitch:.2f},{t.roll:.2f}".encode())
    return h.hexdigest()[:16]


def _save_jpeg(path: str, arr: np.ndarray, quality: int) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img = Image.fromarray((np.clip(arr, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8))
    img.save(path, quality=quality, optimize=True, progressive=True)


def write_gpano(path: str, exiftool: str, *, img_w: int, img_h: int,
                lon_span: float, lat_max: float, tile_count: int,
                tile: TileMeta | None = None) -> bool:
    """Tag a JPEG with Google Photo Sphere (GPano) metadata.

    Without these tags the output is just a wide picture. With them, Google Photos,
    Facebook, Pannellum, Marzipano and most VR viewers recognise it as a sphere and
    offer their own 360 navigation -- so the panoramas are usable well beyond this
    tool, which matters for something meant to preserve a personal archive.

    The tags describe where this image sits inside the VIRTUAL full sphere, which is
    how a partial panorama (a sweep, or a sphere with the cap left unfilled) gets
    letterboxed correctly instead of being stretched to 360x180.
    """
    px_per_deg = img_w / max(lon_span, 1e-6)
    full_w = int(round(360.0 * px_per_deg))
    full_h = int(round(full_w / 2))
    left = int(round(((360.0 - lon_span) / 2.0) * px_per_deg)) if lon_span < 359.5 else 0
    top = max(0, int(round((90.0 - lat_max) * (full_h / 180.0))))

    args = [
        exiftool, "-overwrite_original", "-q", "-q",
        "-XMP-GPano:UsePanoramaViewer=True",
        "-XMP-GPano:ProjectionType=equirectangular",
        f"-XMP-GPano:FullPanoWidthPixels={full_w}",
        f"-XMP-GPano:FullPanoHeightPixels={full_h}",
        f"-XMP-GPano:CroppedAreaImageWidthPixels={img_w}",
        f"-XMP-GPano:CroppedAreaImageHeightPixels={img_h}",
        f"-XMP-GPano:CroppedAreaLeftPixels={left}",
        f"-XMP-GPano:CroppedAreaTopPixels={top}",
        f"-XMP-GPano:SourcePhotosCount={tile_count}",
        "-XMP-GPano:StitchingSoftware=panolib",
        "-XMP-GPano:PoseHeadingDegrees=0",
    ]
    if tile is not None:
        if tile.lat is not None and tile.lon is not None:
            args += [f"-GPSLatitude={tile.lat}", f"-GPSLongitude={tile.lon}",
                     f"-GPSLatitudeRef={'N' if tile.lat >= 0 else 'S'}",
                     f"-GPSLongitudeRef={'E' if tile.lon >= 0 else 'W'}"]
        if tile.altitude is not None:
            # EXIF stores GPS altitude as a magnitude plus a separate above/below sea
            # level flag. Writing the number alone dropped the sign: a tile that recorded
            # 108.8 m BELOW sea level came out 108.8 m above. Write both halves.
            args += [f"-GPS:GPSAltitude#={abs(tile.altitude)}",
                     f"-GPS:GPSAltitudeRef#={1 if tile.altitude < 0 else 0}"]
        # Height above the launch point is the number people mean by "how high was it",
        # and it is not derivable from GPSAltitude. Carry it through so the finished
        # panorama describes itself without needing the library manifest alongside.
        if tile.rel_altitude is not None:
            args.append(f"-XMP-drone-dji:RelativeAltitude={tile.rel_altitude:+.2f}")
        if tile.altitude is not None:
            args.append(f"-XMP-drone-dji:AbsoluteAltitude={tile.altitude:+.2f}")
        if tile.datetime:
            args.append(f"-DateTimeOriginal={tile.datetime}")
        if tile.model:
            args += [f"-Model={tile.model}", "-Make=DJI"]
    args.append(path)

    import subprocess
    try:
        proc = subprocess.run(args, capture_output=True, timeout=120)
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def retag_if_stale(cached: dict, eq_path: str, meta_path: str,
                   tiles: list[TileMeta], exiftool: str | None) -> bool:
    """Bring a cached panorama's tags up to META_VERSION in place. True if it re-tagged.

    Only the metadata is rewritten (exiftool does not touch the image data), from the
    same tiles and the same geometry the build recorded, so the result is what a fresh
    build would write. The meta record is updated so this happens once.
    """
    if not exiftool or not tiles or cached.get("meta_version", 1) >= META_VERSION:
        return False
    cov = cached.get("coverage") or {}
    if not (cached.get("width") and cached.get("height")):
        return False
    ok = write_gpano(eq_path, exiftool, img_w=int(cached["width"]), img_h=int(cached["height"]),
                     lon_span=float(cov.get("lon_span", 360.0)),
                     lat_max=float(cov.get("lat_max", 90.0)),
                     tile_count=len(tiles), tile=tiles[0])
    if ok:
        cached["meta_version"] = META_VERSION
        with open(meta_path + ".tmp", "w", encoding="utf-8") as fh:
            json.dump({k: v for k, v in cached.items() if k != "cached"}, fh, indent=2)
        os.replace(meta_path + ".tmp", meta_path)
    return ok


def _resize(arr: np.ndarray, width: int) -> np.ndarray:
    h, w, _ = arr.shape
    if w <= width:
        return arr
    height = max(1, int(round(h * width / w)))
    img = Image.fromarray((np.clip(arr, 0, 1) * 255.0 + 0.5).astype(np.uint8))
    return np.asarray(img.resize((width, height), Image.LANCZOS), dtype=np.float32) / 255.0


def little_planet(equirect: np.ndarray, size: int = 1400, zenith: bool = False) -> np.ndarray:
    """Render a stereographic 'tiny planet' still from an equirect panorama."""
    h, w, _ = equirect.shape
    y, x = np.mgrid[0:size, 0:size].astype(np.float32)
    nx = (x - size / 2) / (size / 2)
    ny = (y - size / 2) / (size / 2)
    r = np.hypot(nx, ny)
    colat = 2.0 * np.arctan(r)
    lon = np.arctan2(nx, -ny)
    lat = (math.pi / 2 - colat) if zenith else (-math.pi / 2 + colat)
    u = np.clip(((lon + math.pi) / (2 * math.pi)) * w, 0, w - 1).astype(np.int32)
    v = np.clip(((math.pi / 2 - lat) / math.pi) * h, 0, h - 1).astype(np.int32)
    return equirect[v, u]


def build_one(pano: PanoSet, opts: BuildOptions,
              exiftool: str | None = None) -> dict:
    """Stitch one set and write its outputs. Never raises -- failures are reported.

    A single unreadable folder must not take down a 135-set batch, so everything is
    caught and turned into a manifest entry with ``status: "error"``.
    """
    started = time.time()
    entry: dict = {
        "id": pano.id,
        "name": pano.name,
        "source": pano.path,
        "trip": pano.trip,
        "tile_count": pano.tile_count,
        "status": "ok",
    }

    try:
        tiles = read_tiles(pano.path, exiftool)
        if not tiles:
            entry.update(status="error", error="no readable tiles")
            return entry

        cls = classify(tiles)
        entry["mode"] = cls.mode
        entry["mode_label"] = cls.label
        entry["complete"] = cls.complete

        # a camera roll that slipped through the filesystem filter: real panorama
        # sets move the gimbal between frames, ordinary photo folders do not
        if cls.mode == "single" and len(tiles) > 3:
            entry.update(status="skipped", error="not a panorama (tiles share one orientation)")
            return entry

        first = tiles[0]
        cam = Camera(first.width, first.height)
        native = opts.width or recommended_width(cam)
        width = recommended_canvas(cls, native)

        key = cache_key(pano, tiles, opts)
        entry["cache_key"] = key
        rel = os.path.join("panoramas", pano.id)
        eq_path = os.path.join(opts.out_dir, rel, f"equirect-{key}.jpg")
        pv_path = os.path.join(opts.out_dir, rel, f"preview-{key}.jpg")
        th_path = os.path.join(opts.out_dir, rel, f"thumb-{key}.jpg")
        pl_path = os.path.join(opts.out_dir, rel, f"planet-{key}.jpg")

        if not opts.force and os.path.exists(eq_path) and os.path.exists(th_path):
            meta_path = os.path.join(opts.out_dir, rel, f"meta-{key}.json")
            if os.path.exists(meta_path):
                with open(meta_path, "r", encoding="utf-8") as fh:
                    cached = json.load(fh)
                retag_if_stale(cached, eq_path, meta_path, tiles, exiftool)
                cached["cached"] = True
                return cached

        # Refine the recorded angles from the overlaps before stitching. This is
        # guarded: it returns the original tiles unless it can show the alignment
        # actually improved, so turning it on can reduce ghosting but cannot make a
        # panorama worse.
        if opts.refine and cls.mode != "single":
            tiles, refinement = refine_angles(tiles, cam=cam)
            entry["refinement"] = refinement.to_dict()
        else:
            entry["refinement"] = None

        result: StitchResult = stitch(tiles, width)
        if result.tiles_used == 0:
            entry.update(status="error", error="no tiles could be placed")
            return entry

        if opts.tonemap == "local":
            display = local_tonemap(result.image, result.coverage,
                                    compression=opts.compression)
        elif opts.tonemap == "global":
            from .tonemap import global_tonemap
            display = global_tonemap(result.image, result.coverage)
        else:
            display = result.image

        srgb = linear_to_srgb(display)
        coverage = result.coverage
        if opts.fill_cap and cls.mode == "sphere":
            srgb, coverage = fill_zenith(srgb, result.coverage)

        cropped, (deg_h, deg_v) = crop_to_coverage(srgb, coverage, inscribed=False)

        # angular bounds the viewer needs to place the crop back on the sphere
        h_img = srgb.shape[0]
        _rows = np.nonzero(coverage.any(axis=1))[0]
        lat_max_deg = 90.0 - (_rows[0] / h_img) * 180.0
        lat_min_deg = 90.0 - ((_rows[-1] + 1) / h_img) * 180.0

        _save_jpeg(eq_path, cropped, opts.quality_jpeg)
        if exiftool and write_gpano(eq_path, exiftool, img_w=cropped.shape[1],
                                    img_h=cropped.shape[0], lon_span=deg_h,
                                    lat_max=lat_max_deg, tile_count=len(tiles), tile=tiles[0]):
            entry["meta_version"] = META_VERSION
        _save_jpeg(pv_path, _resize(cropped, opts.preview_width), 84)
        _save_jpeg(th_path, _resize(cropped, opts.thumb_width), 78)
        if opts.make_planet and cls.mode == "sphere":
            _save_jpeg(pl_path, little_planet(srgb), 88)

        report = score_quality(result, cls, missing_tiles(tiles))

        with_gps = [t for t in tiles if t.lat is not None and t.lon is not None]
        entry.update({
            "status": "ok",
            "equirect": f"{rel}/equirect-{key}.jpg".replace(os.sep, "/"),
            "preview": f"{rel}/preview-{key}.jpg".replace(os.sep, "/"),
            "thumb": f"{rel}/thumb-{key}.jpg".replace(os.sep, "/"),
            "planet": (f"{rel}/planet-{key}.jpg".replace(os.sep, "/")
                       if (opts.make_planet and cls.mode == "sphere") else None),
            "width": int(cropped.shape[1]),
            "height": int(cropped.shape[0]),
            "coverage": {
                "lon_span": round(deg_h, 2), "lat_span": round(deg_v, 2),
                "lon_min": -180.0 if deg_h >= 359.0 else round(-deg_h / 2, 2),
                "lon_max": 180.0 if deg_h >= 359.0 else round(deg_h / 2, 2),
                "lat_min": round(lat_min_deg, 2), "lat_max": round(lat_max_deg, 2),
                "solid_angle": round(result.coverage_solid_angle, 4),
            },
            "captured": tiles[0].datetime,
            "lat": with_gps[0].lat if with_gps else None,
            "lon": with_gps[0].lon if with_gps else None,
            "altitude": with_gps[0].altitude if with_gps else None,
            "altitude_rel": tiles[0].rel_altitude,
            "camera": tiles[0].model,
            "dynamic_range": round(dynamic_range(result.image, result.coverage), 1),
            "quality": report.score,
            "quality_grade": report.grade,
            "quality_detail": report.to_dict(),
            "warnings": result.warnings + cls.notes,
            "build_seconds": round(time.time() - started, 2),
        })

        meta_path = os.path.join(opts.out_dir, rel, f"meta-{key}.json")
        os.makedirs(os.path.dirname(meta_path), exist_ok=True)
        with open(meta_path, "w", encoding="utf-8") as fh:
            json.dump(entry, fh, indent=2)

    except Exception as exc:  # one bad set must never abort the batch
        entry.update(status="error", error=f"{exc.__class__.__name__}: {exc}",
                     traceback=traceback.format_exc(limit=4))

    return entry
