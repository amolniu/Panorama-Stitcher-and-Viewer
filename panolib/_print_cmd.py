"""The ``print`` subcommand: prepare an image for printing with a caption of record."""

from __future__ import annotations

import argparse
import glob as globlib
import json
import os
import sys

from PIL import Image

from .capture import enrich_from_library, file_digest, map_url, read_capture
from .geonames import nearest_place
from .paper import MAX_UPSCALE, assess_fit, fit_box, max_print_size, parse_size
from .plate import STYLES, build_rows, render, save_print
from .scan import make_id

Image.MAX_IMAGE_PIXELS = None

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".tif", ".tiff")


class AmbiguousTarget(ValueError):
    """A name that matches more than one panorama."""


def resolve_targets(target: str, out_dir: str,
                    trip: str | None = None) -> list[tuple[str, str, str]]:
    """Turn a path, glob, folder or panorama name into (file, friendly-name, stem) triples.

    The friendly name is what the console shows (``100_0368``); the stem is what the
    print's filename starts with. The two differ because names are not unique: in this
    archive 20 DJI folder names are used by more than one panorama (43 of the 135
    folders), and camera-roll names (``DJI_0001.JPG``) restart on every card. A print named only by
    its friendly name would be overwritten by the next print of a different photograph
    with the same name. So the stem is the library id for a panorama -- the name plus
    eight hex digits of its source path -- and the same construction for a standalone
    file. (A panorama's equirect is stored under a cache-keyed filename, which is why
    the output is not simply named after the source file.)
    """
    def named(paths):
        return [(p, os.path.splitext(os.path.basename(p))[0],
                 make_id(p, os.path.splitext(os.path.basename(p))[0])) for p in paths]

    if os.path.isfile(target):
        return named([target])

    if any(ch in target for ch in "*?["):
        hits = [p for p in sorted(globlib.glob(target)) if p.lower().endswith(IMAGE_EXT)]
        if hits:
            return named(hits)

    if os.path.isdir(target):
        hits = [os.path.join(target, f) for f in sorted(os.listdir(target))
                if f.lower().endswith(IMAGE_EXT)]
        if hits:
            return named(hits)

    # a panorama name or id from the library
    lib_path = os.path.join(out_dir, "library.json")
    if os.path.exists(lib_path):
        try:
            with open(lib_path, "r", encoding="utf-8") as fh:
                lib = json.load(fh)
        except (OSError, ValueError):
            lib = {"panoramas": []}
        # Ids are unique by construction (they carry a hash of the source path); names
        # are NOT. 20 of this archive's 112 panorama names are shared between folders --
        # 100_0858 exists three times, in Scotland and twice in India. Resolving a name to
        # whichever entry happens to come first silently prints the wrong photograph, so an
        # ambiguous name is refused and the caller is shown the candidates.
        for e in lib.get("panoramas", []):
            if e.get("id") == target and e.get("equirect"):
                return [(os.path.join(out_dir, e["equirect"].replace("/", os.sep)),
                         e.get("name") or target, e["id"])]

        named = [e for e in lib.get("panoramas", [])
                 if e.get("name") == target and e.get("equirect")]
        if trip:
            named = [e for e in named
                     if (e.get("trip") or "").strip().lower() == trip.strip().lower()]
        if len(named) == 1:
            e = named[0]
            return [(os.path.join(out_dir, e["equirect"].replace("/", os.sep)),
                     e.get("name") or target, e["id"])]
        if len(named) > 1:
            lines = [f"{len(named)} panoramas are called {target!r}. "
                     f"Narrow it with --trip, or use the id:"]
            for e in named:
                lines.append(f"    {e['id']}   trip={e.get('trip')}   "
                             f"{(e.get('captured') or '?')[:10]}   {e.get('source')}")
            raise AmbiguousTarget(chr(10).join(lines))

        # a whole trip, e.g. "Scotland" -- the grouping the library already knows
        want = (trip or target).strip().lower()
        trip_hits = []
        for e in lib.get("panoramas", []):
            if (e.get("trip") or "").strip().lower() != want:
                continue
            if e.get("status") != "ok" or not e.get("equirect"):
                continue
            trip_hits.append((os.path.join(out_dir, e["equirect"].replace("/", os.sep)),
                              e.get("name") or e.get("id"), e["id"]))
        if trip_hits:
            return sorted(trip_hits, key=lambda t: (t[1], t[2]))
    return []


def list_trips(out_dir: str) -> list[str]:
    """Trip names the library knows, for an error message that actually helps."""
    lib_path = os.path.join(out_dir, "library.json")
    if not os.path.exists(lib_path):
        return []
    try:
        with open(lib_path, "r", encoding="utf-8") as fh:
            lib = json.load(fh)
    except (OSError, ValueError):
        return []
    return sorted({e.get("trip") for e in lib.get("panoramas", []) if e.get("trip")})


def write_sidecar(path: str, cap, extra: dict) -> None:
    """Write the full recorded metadata next to the print.

    The plate shows a handful of fields chosen to read well. The sidecar keeps everything
    the file actually recorded, so the print can be checked against its source later
    without re-deriving anything. It is a record, not a guarantee.
    """
    data = {
        "source_file": os.path.abspath(cap.path),
        "source_pixels": [cap.width, cap.height],
        "position": None if not cap.has_position else {
            "latitude_deg": cap.lat, "longitude_deg": cap.lon,
            "map_url": map_url(cap.lat, cap.lon),
        },
        "altitude_m": {"above_sea_level": cap.alt_msl, "above_launch": cap.alt_agl},
        "captured_local": cap.captured,
        "timezone": "not recorded by the camera",
        "heading_deg": cap.camera_heading,
        "heading_source": cap.heading_source,
        "camera": {"make": cap.make, "model": cap.model, "serial": cap.serial},
        "exposure": {"f_number": cap.f_number, "exposure_time_s": cap.exposure_time,
                     "iso": cap.iso, "focal_length_35mm": cap.focal_35},
        "print": extra,
        "note": ("These values are what the image file records. They are not independently "
                 "verified; EXIF metadata can be edited."),
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


def cmd_print(args: argparse.Namespace, default_out: str) -> int:
    out_root = os.path.abspath(args.out_dir or default_out)
    try:
        targets = resolve_targets(args.target, out_root, getattr(args, 'trip', None))
    except AmbiguousTarget as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not targets:
        print("Nothing to print for " + repr(args.target) + ".", file=sys.stderr)
        print("Give an image file, a folder, a glob, a panorama name, or a trip name.",
              file=sys.stderr)
        trips = list_trips(out_root)
        if trips:
            print("Trips in the library: " + ", ".join(trips), file=sys.stderr)
        return 1
    if args.limit:
        targets = targets[:args.limit]

    dest_dir = args.out or os.path.join(out_root, "prints")
    os.makedirs(dest_dir, exist_ok=True)

    style = args.style
    if style not in STYLES:
        print("Unknown style " + repr(style) + ". Choose from: "
              + ", ".join(sorted(STYLES)), file=sys.stderr)
        return 1

    failures = 0
    for src_path, friendly, stem in targets:
        try:
            cap = read_capture(src_path, args.exiftool)
            cap = enrich_from_library(cap, out_root)

            if not cap.has_position and not args.allow_no_position:
                print("  skip  " + os.path.basename(src_path)
                      + "  (no position recorded; pass --allow-no-position to print anyway)")
                continue

            digest = file_digest(src_path) if (args.digest or args.sidecar) else None

            # A derived place name is an inference, not something the file records, so it
            # goes in the TITLE -- where a human label is expected -- and never into the
            # data rows. An explicit --title always wins.
            title = args.title
            place = None
            if title is None and cap.has_position and not args.no_place:
                place = nearest_place(cap.lat, cap.lon)
                if place:
                    title = place.label()

            with Image.open(src_path) as im:
                im.load()
                paper = parse_size(args.size, args.dpi, args.orientation, cap.aspect)
                if args.fields and args.fields.strip().lower() == "none":
                    fields = set()
                elif args.fields:
                    fields = set(f.strip() for f in args.fields.split(",") if f.strip())
                else:
                    fields = None
                probe_w, probe_h, probe_crop = fit_box(
                    im.width, im.height, paper.width_px, paper.height_px, args.fit)
                probe = assess_fit(im.width, im.height, probe_w, probe_h, probe_crop, paper)
                if probe.scale > MAX_UPSCALE and not args.allow_upscale:
                    iw, ih = max_print_size(im.width, im.height, 300.0)
                    print("  skip  " + os.path.basename(src_path)
                          + "  ({}x{} px would need {:.1f}x enlargement for this size; "
                            "it fills {:.1f}x{:.1f} inches at 300 dpi. "
                            "Use --allow-upscale or a smaller --size.)".format(
                                im.width, im.height, probe.scale, iw, ih))
                    continue

                plate, report = render(
                    cap, im, paper, style=style, title=title, fit=args.fit,
                    units=args.units, include=fields,
                    footnote=(args.footnote != "none"),
                    footnote_variant=args.footnote,
                    digest=digest if args.digest else None)

            # stem is unique per source (see resolve_targets); friendly is for the console
            ext = ".tif" if args.format == "tiff" else (".png" if args.format == "png" else ".jpg")
            dest = os.path.join(dest_dir, stem + "-" + paper.name + "-" + style + ext)
            save_print(plate, dest, paper, jpeg_quality=args.jpeg_quality)

            mb = os.path.getsize(dest) / 1e6
            print("  ok    " + os.path.basename(dest)
                  + "  {}x{}px  {:.0f} dpi effective  {:.1f} MB".format(
                      plate.width, plate.height, report.effective_dpi, mb))
            if report.warning:
                print("        ! " + report.warning)

            if args.sidecar:
                write_sidecar(os.path.splitext(dest)[0] + ".json", cap, {
                    "source_sha256_short": digest,
                    "paper": paper.name,
                    "paper_inches": [round(paper.width_in, 3), round(paper.height_in, 3)],
                    "dpi": paper.dpi,
                    "pixels": [plate.width, plate.height],
                    "style": style,
                    "effective_dpi": round(report.effective_dpi, 1),
                    "enlargement": round(report.scale, 3),
                    "title": title,
                    "title_source": ("explicit" if args.title else
                                     ("derived" if place else None)),
                    "nearest_place": None if not place else {
                        "name": place.name,
                        "admin1": place.admin1,
                        "country_code": place.country_code,
                        "distance_km": round(place.distance_km, 2),
                        "bearing_deg": round(place.bearing_deg, 1),
                        "source": "GeoNames cities1000 via reverse_geocoder, offline",
                        "note": ("Derived from the recorded coordinates, not recorded in "
                                 "the file. Names the nearest populated place."),
                    },
                })
        except Exception as exc:
            failures += 1
            print("  FAIL  " + os.path.basename(src_path) + "  "
                  + exc.__class__.__name__ + ": " + str(exc), file=sys.stderr)

    if len(targets) == 1 and not failures:
        cap = read_capture(targets[0][0], args.exiftool)
        if cap.width:
            iw, ih = max_print_size(cap.width, cap.height, 300.0)
            print("\n  this source fills {:.1f} x {:.1f} inches at 300 dpi "
                  "without enlargement".format(iw, ih))
    print("\n  prints in " + dest_dir)
    return 1 if failures else 0
