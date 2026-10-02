"""The ``render`` subcommand, kept in its own module for clarity."""

from __future__ import annotations

import argparse
import json
import os
import sys

from .cinema import render_video


def cmd_render(args: argparse.Namespace, default_out: str) -> int:
    """Render a cinematic clip from one panorama."""
    out_root = os.path.abspath(default_out)
    src = args.target
    name = os.path.splitext(os.path.basename(src))[0]

    if not os.path.isfile(src):
        lib_path = os.path.join(out_root, "library.json")
        if not os.path.exists(lib_path):
            print("No library.json in " + out_root
                  + ", and " + repr(src) + " is not a file.", file=sys.stderr)
            return 1
        with open(lib_path, "r", encoding="utf-8") as fh:
            lib = json.load(fh)
        match = next((e for e in lib["panoramas"]
                      if e.get("id") == src or e.get("name") == src), None)
        if not match or not match.get("equirect"):
            print("No panorama called " + repr(src) + " in the library.", file=sys.stderr)
            return 1
        src = os.path.join(out_root, match["equirect"].replace("/", os.sep))
        name = match.get("name", name)

    try:
        w, h = (int(x) for x in args.size.lower().split("x"))
    except ValueError:
        print("--size should look like 1920x1080, got " + repr(args.size), file=sys.stderr)
        return 1

    out = args.out or os.path.join(out_root, "videos", name + "-" + args.move + ".mp4")
    print(name + "  ->  " + out)

    def progress(i, n):
        pct = 100.0 * i / n
        print("  rendering {}: {}/{} frames ({:.0f}%)".format(args.move, i, n, pct))

    try:
        render_video(src, out, move=args.move, width=w, height=h, fps=args.fps,
                     seconds=args.seconds, ffmpeg=args.ffmpeg, progress=progress)
    except (RuntimeError, ValueError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2

    size_mb = os.path.getsize(out) / 1e6
    print("  done - {:.1f} MB".format(size_mb))
    return 0
