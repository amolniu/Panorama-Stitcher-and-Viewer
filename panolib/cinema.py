"""Rendering cinematic moves from a finished panorama, straight to video.

A 360 panorama is hard to share: sending someone a JPEG of an equirect shows them a
distorted map, not the place. A short clip that moves through the sphere reads
immediately and plays anywhere.

The projections here mirror the viewer's shader exactly, so what you export is what you
saw on screen.
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
from dataclasses import dataclass

import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None


def find_ffmpeg(explicit: str | None = None) -> str | None:
    for cand in ([explicit] if explicit else []) + ["ffmpeg"]:
        if cand and os.path.isfile(cand):
            return cand
        found = shutil.which(cand) if cand else None
        if found:
            return found
    return None


def camera_matrix(yaw: float, pitch: float, roll: float = 0.0) -> np.ndarray:
    """View-to-world, columns [right, up, forward] -- matches the viewer's shader."""
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    fwd = np.array([sy * cp, sp, cy * cp])
    right = np.array([cy, 0.0, -sy])
    up = np.cross(fwd, right)
    if roll:
        c, s = math.cos(roll), math.sin(roll)
        right = right * c + np.cross(fwd, right) * s + fwd * (fwd @ right) * (1 - c)
        up = up * c + np.cross(fwd, up) * s + fwd * (fwd @ up) * (1 - c)
    return np.stack([right, up, fwd], axis=1)


def screen_grid(width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
    aspect = width / height
    x = ((np.arange(width) + 0.5) / width * 2.0 - 1.0) * aspect
    y = 1.0 - (np.arange(height) + 0.5) / height * 2.0
    return np.meshgrid(x, y)


def dirs_rectilinear(X: np.ndarray, Y: np.ndarray, fov: float) -> np.ndarray:
    t = math.tan(fov / 2.0)
    d = np.stack([X * t, Y * t, np.ones_like(X)], axis=-1)
    return d / np.linalg.norm(d, axis=-1, keepdims=True)


def dirs_stereographic(X: np.ndarray, Y: np.ndarray, fov: float) -> np.ndarray:
    r = np.hypot(X, Y)
    theta = 2.0 * np.arctan(r * math.tan(fov / 4.0))
    safe = np.where(r < 1e-9, 1.0, r)
    st = np.sin(theta)
    return np.stack([st * X / safe, st * Y / safe, np.cos(theta)], axis=-1)


def sample_equirect(pano: np.ndarray, dirs: np.ndarray) -> np.ndarray:
    """Nearest-neighbour equirect lookup. The source is far denser than the output
    frame, so the sampling error stays below a pixel and this is much faster."""
    h, w, _ = pano.shape
    lon = np.arctan2(dirs[..., 0], dirs[..., 2])
    lat = np.arcsin(np.clip(dirs[..., 1], -1.0, 1.0))
    u = ((lon + math.pi) / (2 * math.pi) * w).astype(np.int32) % w
    v = np.clip(((math.pi / 2 - lat) / math.pi * h).astype(np.int32), 0, h - 1)
    return pano[v, u]


def ease(t: float) -> float:
    """Smoothstep, so moves start and stop gently instead of jerking."""
    return t * t * (3.0 - 2.0 * t)


@dataclass
class Move:
    name: str
    seconds: float
    describe: str


MOVES = {
    "planet-spin": Move("planet-spin", 12.0,
                        "A little planet turning a full revolution."),
    "orbit": Move("orbit", 16.0,
                  "A slow level pan right around the horizon."),
    "dolly": Move("dolly", 14.0,
                  "Pulls back from a normal view into a little planet while turning."),
    "globe": Move("globe", 12.0,
                  "Orbits the panorama as a solid ball seen from outside."),
    "tunnel-rise": Move("tunnel-rise", 10.0,
                        "Rises from the horizon to look straight up the zenith."),
}


def render_frames(pano: np.ndarray, move: str, width: int, height: int,
                  fps: int, seconds: float):
    """Yield uint8 RGB frames for the named move."""
    X, Y = screen_grid(width, height)
    n = max(2, int(round(fps * seconds)))

    for i in range(n):
        t = i / n                       # 0..1, not inclusive, so a loop is seamless
        te = ease(min(1.0, i / max(n - 1, 1)))

        if move == "planet-spin":
            R = camera_matrix(t * 2 * math.pi, math.radians(-90))
            d = dirs_stereographic(X, Y, math.radians(205))
        elif move == "orbit":
            R = camera_matrix(t * 2 * math.pi, math.radians(-8))
            d = dirs_rectilinear(X, Y, math.radians(80))
        elif move == "dolly":
            fov = math.radians(75 + te * 165)
            pitch = math.radians(-8 - te * 82)
            R = camera_matrix(t * 2 * math.pi, pitch)
            a = dirs_rectilinear(X, Y, fov)
            b = dirs_stereographic(X, Y, fov)
            d = a * (1 - te) + b * te
            d /= np.linalg.norm(d, axis=-1, keepdims=True)
        elif move == "globe":
            R = camera_matrix(t * 2 * math.pi, math.radians(12))
            rd = dirs_rectilinear(X, Y, math.radians(45))
            dist = 2.7
            ro = np.array([0.0, 0.0, -dist])
            b_ = rd @ ro
            c_ = float(ro @ ro) - 1.0
            disc = b_ * b_ - c_
            hit = disc >= 0.0
            tt = -b_ - np.sqrt(np.maximum(disc, 0.0))
            pt = ro[None, None, :] + rd * tt[..., None]
            nrm = np.linalg.norm(pt, axis=-1, keepdims=True)
            d = np.where(hit[..., None], pt / np.maximum(nrm, 1e-9), np.array([0.0, 0.0, 1.0]))
            world = d @ R.T
            frame = sample_equirect(pano, world)
            frame = np.where(hit[..., None], frame, 0.055)
            yield (np.clip(frame, 0, 1) * 255).astype(np.uint8)
            continue
        elif move == "tunnel-rise":
            pitch = math.radians(-5 + te * 95)
            R = camera_matrix(t * math.pi, pitch)
            d = dirs_stereographic(X, Y, math.radians(120 + te * 95))
        else:
            raise ValueError(f"unknown move {move!r}; choose from {', '.join(MOVES)}")

        world = d @ R.T
        yield (np.clip(sample_equirect(pano, world), 0, 1) * 255).astype(np.uint8)


def render_video(pano_path: str, out_path: str, move: str = "planet-spin",
                 width: int = 1920, height: int = 1080, fps: int = 30,
                 seconds: float | None = None, ffmpeg: str | None = None,
                 crf: int = 18, progress=None) -> str:
    """Render ``move`` from the panorama at ``pano_path`` into an MP4."""
    exe = find_ffmpeg(ffmpeg)
    if not exe:
        raise RuntimeError("ffmpeg not found — install it or pass --ffmpeg <path>")
    if move not in MOVES:
        raise ValueError(f"unknown move {move!r}; choose from {', '.join(MOVES)}")
    seconds = seconds or MOVES[move].seconds

    with Image.open(pano_path) as im:
        pano = np.asarray(im.convert("RGB"), dtype=np.float32) / 255.0

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    cmd = [
        exe, "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{width}x{height}", "-r", str(fps), "-i", "pipe:0",
        "-an", "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
        # yuv420p so the file plays in browsers, phones and messaging apps
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        out_path,
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    total = max(2, int(round(fps * seconds)))
    try:
        for i, frame in enumerate(render_frames(pano, move, width, height, fps, seconds)):
            proc.stdin.write(frame.tobytes())
            if progress and (i % 15 == 0 or i == total - 1):
                progress(i + 1, total)
    finally:
        if proc.stdin:
            proc.stdin.close()
        err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"ffmpeg failed ({rc}): {err[:400]}")
    return out_path
