"""Command line entry point.

    python -m panolib scan  <folder>            list the panorama sets found
    python -m panolib build <folder> [options]  stitch them and write the viewer library
    python -m panolib view                      serve the viewer in a browser

Designed so the whole job is one command: point it at a folder, wait, look at the
panoramas.
"""

from __future__ import annotations

import argparse
import dataclasses
import concurrent.futures as futures
import json
import os
import sys
import time
import webbrowser

from .build import BuildOptions, build_one
from .cinema import MOVES, render_video
from .exif import ExifToolMissing, find_exiftool
from .places import cluster_places, link_nearby
from .regions import assign_location_trips, misfiled_report
from .scan import scan

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
VIEWER_DIR = os.path.join(PROJECT, "viewer")
DEFAULT_OUT = os.path.join(PROJECT, "out")


def _fmt_secs(s: float) -> str:
    if s < 90:
        return f"{s:.0f}s"
    return f"{int(s // 60)}m {int(s % 60):02d}s"


def cmd_scan(args: argparse.Namespace) -> int:
    sets = scan(args.folders, max_depth=args.depth)
    if not sets:
        print("No panorama sets found.")
        print("Point this at a folder containing PANORAMA/<set> directories, "
              "or directly at one set folder.")
        return 1
    by_trip: dict[str, list] = {}
    for s in sets:
        by_trip.setdefault(s.trip, []).append(s)
    print(f"{len(sets)} panorama set(s) in {len(by_trip)} group(s)\n")
    for trip, items in sorted(by_trip.items()):
        print(f"  {trip}  ({len(items)})")
        for s in items[:args.list_limit]:
            print(f"      {s.name:14s} {s.tile_count:4d} tiles   {s.path}")
        if len(items) > args.list_limit:
            print(f"      … {len(items) - args.list_limit} more")
        print()
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    try:
        exe = find_exiftool(args.exiftool)
    except ExifToolMissing as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"exiftool: {exe}")

    sets = scan(args.folders, max_depth=args.depth)
    if not sets:
        print("No panorama sets found.", file=sys.stderr)
        return 1
    if args.limit:
        sets = sets[:args.limit]

    out_dir = os.path.abspath(args.out or DEFAULT_OUT)
    os.makedirs(out_dir, exist_ok=True)
    opts = BuildOptions(
        out_dir=out_dir, width=args.width, tonemap=args.tonemap,
        compression=args.compression, fill_cap=not args.no_fill,
        make_planet=not args.no_planet, force=args.force, refine=not args.no_refine,
        preview_width=args.preview_width, quality_jpeg=args.jpeg_quality,
    )

    print(f"building {len(sets)} panorama(s) -> {out_dir}")
    print(f"workers: {args.workers}   tone map: {opts.tonemap}\n")

    started = time.time()
    entries: list[dict] = []
    done = 0

    def report(e: dict) -> None:
        nonlocal done
        done += 1
        tag = {"ok": "ok  ", "error": "FAIL", "skipped": "skip"}.get(e.get("status"), "?   ")
        extra = ""
        if e.get("status") == "ok":
            q = e.get("quality")
            extra = f"q={q:.2f} {e.get('mode','')}"
            if e.get("cached"):
                extra += " (cached)"
        else:
            extra = e.get("error", "")[:60]
        print(f"  [{done}/{len(sets)}] {tag} {e['name']:14s} {extra}")

    if args.workers > 1:
        with futures.ProcessPoolExecutor(max_workers=args.workers) as pool:
            futs = {pool.submit(build_one, s, opts, exe): s for s in sets}
            for fut in futures.as_completed(futs):
                try:
                    e = fut.result()
                except Exception as exc:  # worker died outright
                    s = futs[fut]
                    e = {"id": s.id, "name": s.name, "source": s.path, "trip": s.trip,
                         "status": "error", "error": f"worker crashed: {exc}"}
                entries.append(e)
                report(e)
    else:
        for s in sets:
            e = build_one(s, opts, exe)
            entries.append(e)
            report(e)

    entries.sort(key=lambda e: (e.get("trip", ""), e.get("captured") or "", e.get("name", "")))
    ok = [e for e in entries if e.get("status") == "ok"]
    bad = [e for e in entries if e.get("status") == "error"]
    skipped = [e for e in entries if e.get("status") == "skipped"]

    # GPS is in every tile, so the archive can describe its own geography: link
    # panoramas taken near one another so the viewer can offer another viewpoint from
    # the same spot, and cluster them into places for browsing.
    link_nearby(entries)
    places = cluster_places(entries)

    # Group by where each panorama was actually taken, not which backup folder it landed
    # in -- the folders are wrong often enough that one holds five Indian states and a
    # corner of California. The folder name is kept as source_folder.
    location_trips = assign_location_trips(entries)
    misfiled = misfiled_report(entries)

    library = {
        "version": 1,
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "roots": [os.path.abspath(f) for f in args.folders],
        "counts": {"total": len(entries), "ok": len(ok),
                   "failed": len(bad), "skipped": len(skipped),
                   "places": len(places), "location_trips": len(location_trips),
                   "misfiled": len(misfiled)},
        "places": places,
        "location_trips": [dataclasses.asdict(t) for t in location_trips],
        "misfiled": misfiled,
        "panoramas": entries,
    }
    # Written to a temporary name and renamed: the viewer's server re-reads this file
    # whenever its mtime changes, and must never catch it half-written.
    lib_path = os.path.join(out_dir, "library.json")
    with open(lib_path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(library, fh, indent=2)
    os.replace(lib_path + ".tmp", lib_path)

    # the viewer is served from out/, so link the static files in next to the data
    _install_viewer(out_dir)

    elapsed = time.time() - started
    print(f"\ndone in {_fmt_secs(elapsed)} — {len(ok)} built, "
          f"{len(skipped)} skipped, {len(bad)} failed")
    if ok:
        poor = [e for e in ok if e.get("quality_grade") == "poor"]
        if poor:
            print(f"  {len(poor)} panorama(s) scored poorly:")
            for e in poor[:8]:
                issues = e.get("quality_detail", {}).get("issues", [])
                print(f"    {e['name']}: {issues[0] if issues else 'low score'}")
    for e in bad[:8]:
        print(f"  FAILED {e['name']}: {e.get('error')}")

    if location_trips:
        print(f"\n  grouped into {len(location_trips)} trips by actual location:")
        for t in location_trips:
            print(f"    {t.label:34s} {t.count:3d}   {', '.join(t.towns)}")
    if misfiled:
        folders = sorted({m['folder'] for m in misfiled})
        print(f"\n  {len(misfiled)} panorama(s) sit in folders that do not describe where "
              f"they were taken ({', '.join(folders)}); see library.json -> misfiled")
    print(f"\nnow run:  python -m panolib view")
    return 0 if not bad else 0


def _install_viewer(out_dir: str) -> None:
    """Copy the viewer next to the generated data so one folder is self-contained."""
    import shutil
    for name in ("index.html", "app.js", "shaders.js"):
        src = os.path.join(VIEWER_DIR, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(out_dir, name))
    vendor_src = os.path.join(VIEWER_DIR, "vendor")
    vendor_dst = os.path.join(out_dir, "vendor")
    if os.path.isdir(vendor_src):
        shutil.copytree(vendor_src, vendor_dst, dirs_exist_ok=True)


def cmd_render(args: argparse.Namespace) -> int:
    from ._render_cmd import cmd_render as _impl
    return _impl(args, DEFAULT_OUT)


def cmd_print(args: argparse.Namespace) -> int:
    from ._print_cmd import cmd_print as _impl
    return _impl(args, DEFAULT_OUT)


def cmd_view(args: argparse.Namespace) -> int:
    import http.server
    import socketserver

    out_dir = os.path.abspath(args.out or DEFAULT_OUT)
    if not os.path.exists(os.path.join(out_dir, "library.json")):
        print(f"No library.json in {out_dir}. Run:  python -m panolib build <folder>",
              file=sys.stderr)
        return 1
    _install_viewer(out_dir)

    # Loopback only, on purpose: the Print panel's API writes files and runs exiftool,
    # so it must never be reachable from another machine. See panolib/printapi.py for
    # the token and Origin checks that guard it from other pages in the same browser.
    from .printapi import serve
    httpd = serve(out_dir, args.port, getattr(args, "exiftool", None))
    port = httpd.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    print(f"serving {out_dir}\n  {url}\n  Print panel enabled (this session only)\nCtrl+C to stop")
    if not args.no_open:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="panolib",
        description="Stitch DJI drone panorama tile folders and view them.")
    sub = p.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("folders", nargs="+", help="folder(s) to search")
    common.add_argument("--depth", type=int, default=8, help="how deep to search")

    s = sub.add_parser("scan", parents=[common], help="list panorama sets, build nothing")
    s.add_argument("--list-limit", type=int, default=6)
    s.set_defaults(func=cmd_scan)

    b = sub.add_parser("build", parents=[common], help="stitch panoramas and write the library")
    b.add_argument("--out", help=f"output folder (default {DEFAULT_OUT})")
    b.add_argument("--width", type=int, default=0,
                   help="equirect width; 0 picks the camera's native resolution")
    b.add_argument("--preview-width", type=int, default=2048)
    b.add_argument("--jpeg-quality", type=int, default=90)
    b.add_argument("--tonemap", choices=["local", "global", "none"], default="local")
    b.add_argument("--compression", type=float, default=None,
                   help="tone-map strength 0.3-0.9; omit to choose per scene")
    b.add_argument("--no-refine", action="store_true",
                   help="skip the angle-refinement pass (faster, but leaves the "
                        "ghosting that DJI's angle error causes)")
    b.add_argument("--no-fill", action="store_true", help="leave the zenith cap empty")
    b.add_argument("--no-planet", action="store_true", help="skip little-planet stills")
    b.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    b.add_argument("--limit", type=int, default=0, help="only the first N sets")
    b.add_argument("--force", action="store_true", help="rebuild even if cached")
    b.add_argument("--exiftool", help="path to exiftool")
    b.set_defaults(func=cmd_build)

    r = sub.add_parser("render", help="render a cinematic clip from a panorama")
    r.add_argument("target", help="panorama id or name from the library, or a path to an equirect JPEG")
    r.add_argument("--move", default="planet-spin", choices=sorted(MOVES),
                   help="camera move; " + "; ".join(f"{k}: {v.describe}" for k, v in MOVES.items()))
    r.add_argument("--out", help="output .mp4 (default out/videos/<name>-<move>.mp4)")
    r.add_argument("--size", default="1920x1080", help="frame size, e.g. 1280x720")
    r.add_argument("--fps", type=int, default=30)
    r.add_argument("--seconds", type=float, default=None)
    r.add_argument("--ffmpeg", help="path to ffmpeg")
    r.set_defaults(func=cmd_render)

    pr = sub.add_parser("print", help="prepare an image for printing with a caption of record")
    pr.add_argument("target",
                    help="an image file, a folder, a glob, a panorama name, or a trip name")
    pr.add_argument("--trip", default=None,
                    help="narrow an ambiguous panorama name to one trip")
    pr.add_argument("--size", default="a3",
                    help="paper size: a name (a3, 16x24, 12x18, pano-2to1 ...) or a form "
                         "like 300x400mm (default: a3)")
    pr.add_argument("--dpi", type=int, default=300)
    pr.add_argument("--style", default="gallery",
                    help="gallery (light mat), survey (dark mat), overlay (on the image)")
    pr.add_argument("--title", default=None,
                    help="a title line above the data block. Omit it and the nearest "
                         "named place is derived from the coordinates offline.")
    pr.add_argument("--no-place", action="store_true",
                    help="do not derive a place name when no --title is given")
    pr.add_argument("--fit", choices=["contain", "cover"], default="contain",
                    help="contain keeps the whole frame; cover fills the paper and crops")
    pr.add_argument("--orientation", choices=["auto", "landscape", "portrait"], default="auto")
    pr.add_argument("--units", choices=["m", "ft"], default="m")
    pr.add_argument("--fields", default=None,
                    help="comma list of position,altitude,recorded,camera")
    pr.add_argument("--footnote", choices=["short", "full", "none"], default="none",
                    help="add a note under the data block: 'short' one line, "
                         "'full' the complete caveat with datum and accuracy. "
                         "Default none.")
    pr.add_argument("--format", choices=["jpeg", "tiff", "png"], default="jpeg")
    pr.add_argument("--jpeg-quality", type=int, default=95)
    pr.add_argument("--digest", action="store_true",
                    help="print a short SHA-256 of the source file in the footnote, "
                         "binding this print to that exact file (needs --footnote full)")
    pr.add_argument("--sidecar", action="store_true",
                    help="also write a .json of everything the file records")
    pr.add_argument("--allow-upscale", action="store_true",
                    help="print even when the source must be enlarged past the point "
                         "where it is resampling rather than inventing detail")
    pr.add_argument("--allow-no-position", action="store_true",
                    help="print even when the file has no GPS")
    pr.add_argument("--out", default=None, help="output folder (default out/prints)")
    pr.add_argument("--out-dir", default=None, help="library folder (default out)")
    pr.add_argument("--limit", type=int, default=0)
    pr.add_argument("--exiftool", default=None)
    pr.set_defaults(func=cmd_print)

    v = sub.add_parser("view", help="serve the viewer locally")
    v.add_argument("--out", help=f"folder to serve (default {DEFAULT_OUT})")
    v.add_argument("--port", type=int, default=8777)
    v.add_argument("--no-open", action="store_true")
    v.set_defaults(func=cmd_view)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
