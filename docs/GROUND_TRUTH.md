# Ground truth: DJI Mavic Air 2 panorama source data

Verified by inspecting the user's actual backup drive (E:) with exiftool on 2026-09-29.
Do NOT re-discover this. Treat as authoritative input.

## Inventory
135 panorama sets across 14 root PANORAMA folders on E:.
Roots (each contains subfolders, one per pano set, named like `100_0679`):
- E:\Drone 2021\Drone\PANORAMA (1)
- E:\Scotland\Drone\2. 07.07\PANORAMA (1)
- E:\Scotland\Drone\3. 07.08-07.09\DCIM\PANORAMA (9)
- E:\Scotland\Drone\1. 07.05 - 07.07\DCIM\PANORAMA (6)
- E:\Drone 2023\PANORAMA (13)
- E:\India-2023-Media\Drone-SD-1..4 and -India-and-SD \DCIM\PANORAMA (13,18,19,12,19)
- E:\Drone-Review\DCIM\PANORAMA (10)
- E:\South Africa\2\PANORAMA (7)
- E:\Drone - BU Unknown 08.31.25\DCIM\PANORAMA (3)
- E:\2026 - Drone - Mix BU - Aug\DCIM\PANORAMA (4)

Tile-count distribution: 26 tiles x107 sets, 9 x10, 7 x9, 4 x2, 2 x2, 1 x5.
Tiles are always named DJI_0001.JPG ... DJI_00NN.JPG inside each set folder.
There are NO DJI-stitched output files anywhere - DJI Fly stitches in-app, so only raw tiles exist on the SD backup.

## Camera
Model `FC7303` (Mavic Air 2). Tiles are 2000x1500 (3 MP, 4:3) for ALL pano modes.
FocalLength 4.5mm, FocalLengthIn35mmFormat 24mm, f/2.8.
exiftool reports Composite FOV 73.7 deg (this is the DIAGONAL-ish value exiftool derives; derive
HFOV/VFOV yourself from the 35mm-equivalent focal length and the 4:3 aspect, and sanity-check
against the observed tile overlap implied by the angle grids below).

## Per-tile orientation metadata (THE KEY ASSET)
Every tile carries XMP-drone-dji tags, readable via exiftool:
  GimbalYawDegree, GimbalPitchDegree, GimbalRollDegree   <- camera orientation, USE THESE
  FlightYawDegree, FlightPitchDegree, FlightRollDegree   <- airframe attitude, do NOT use for projection
  AbsoluteAltitude, RelativeAltitude
Also EXIF GPSLatitude/GPSLongitude/GPSAltitude, DateTimeOriginal, ExposureTime, ISO, FNumber.
IFD0 XPComment is `Type=P, Mode=P, DE=None` for panorama tiles.

This means stitching can be METADATA-DRIVEN (geometric reprojection from known angles),
not dependent on fragile feature matching. Roll is non-zero in some modes, so a full
3-axis rotation matrix per tile is REQUIRED - do not assume roll==0.

## Measured capture patterns

### Sphere / 360 (26 tiles) - e.g. E:\Drone 2023\PANORAMA\100_0679
8 yaw columns spaced 45 deg apart. 3 pitch rows: approx +14.9, -19.9, -54.9.
Column 1 additionally has a horizon shot (pitch -0.1) and a NADIR shot (pitch -89.9).
So 7 columns x 3 + 1 column x 5 = 26. Roll ~0 throughout.
Actual measured (yaw, pitch) per file:
0001 -56.40 -0.10 | 0002 -56.40 +14.85 | 0003 -56.41 -19.87 | 0004 -56.41 -54.91 | 0005 -56.41 -89.89
0006 -101.41 -55.01 | 0007 -101.41 -20.08 | 0008 -101.40 +14.86
0009 -146.22 +14.99 | 0010 -146.39 -19.92 | 0011 -146.39 -54.85
0012 +168.74 -54.99 | 0013 +168.61 -20.12 | 0014 +168.61 +14.87
0015 +123.69 +14.99 | 0016 +123.59 -19.86 | 0017 +123.59 -54.70
0018 +78.77 -55.00 | 0019 +78.61 -20.17 | 0020 +78.61 +14.78
0021 +33.83 +14.99 | 0022 +33.61 -19.89 | 0023 +33.61 -54.81
0024 -11.30 -54.99 | 0025 -11.40 -20.12 | 0026 -11.40 +14.86

IMPORTANT CONSEQUENCE: the highest row is +14.9 deg. With the tile VFOV, coverage stops
around +43 deg elevation. The ZENITH CAP (roughly +43..+90 deg) is NEVER CAPTURED.
Any sphere output must fill that hole deliberately (e.g. feathered extrapolation of the
top scanline / sky-tone fill), or the viewer will show a gaping hole overhead.
Nadir is a single tile so the down pole is covered but low-resolution and often shows the drone.

### 180 deg (7 tiles) - e.g. E:\Drone 2023\PANORAMA\100_0690
Single row, all pitch ~0. Yaw spaced 30 deg: -145.3, -115.4, -85.4, -55.6, -25.7, +4.2, +34.2.
Spans ~180 deg horizontally, ~1 tile VFOV vertically. Roll ~0.

### Wide 3x3 (9 tiles) - e.g. E:\Drone 2023\PANORAMA\100_0691
3 yaw columns (approx -32.8, -55.0, -77.3) x 3 pitch rows (approx -1, -26, -51).
ROLL IS NON-ZERO AND PER-COLUMN: -11.5 for the -32.8 column, ~0 for the -55 column,
+11.5 for the -77.3 column. Full rotation matrix mandatory.

### Partial / degenerate sets
`E:\Drone 2021\Drone\PANORAMA\100_0056` has 4 tiles: one yaw column (-151.1) at pitches
-0.0, +15.0, -19.9, -54.8. An aborted sphere. 5 sets have a single tile, 2 have two tiles.
Classification must be tolerant: infer mode from the actual angle distribution, not just
the tile count, and degrade gracefully (a partial set should still produce a usable
partial panorama rather than an error).

## Photometry
Each tile is auto-exposed independently. In set 100_0679 ExposureTime varies between
1/1600 (0.000625 s) and 1/1000 (0.001 s) - a 2/3 stop difference - at constant ISO 100 and f/2.8.
Because the exact ExposureTime/ISO/FNumber are known per tile, tiles can be linearised and
brought to a common exposure ANALYTICALLY before blending, instead of estimating gains blindly.
JPEGs are sRGB, so undo the sRGB transfer curve before any photometric scaling or blending,
and re-apply it on output.

## Available tooling on this machine
exiftool 13.59  C:\Users\rzram\AppData\Local\Programs\ExifTool\ExifTool.exe
python 3.13.14  (`python`, NO `py` launcher)  - pip installs are allowed
node v22.15.0, npm
ffmpeg 9.0.1 (full build)
git
NOT installed: ImageMagick (`magick`), Hugin, PTGui.
Platform: Windows 11. Primary shell PowerShell; a bash (Git Bash) is also available.
Working directory / project root: C:\Users\rzram\Claude-DroneImages-Pano

---

# CALIBRATION RESULTS (measured empirically, 2026-09-29) - AUTHORITATIVE

These supersede any FOV value derived from EXIF. Established by prototype experiments on the
user's real data, not assumed.

## 1. True field of view: HFOV = 66.7 deg, NOT the 73.7 deg EXIF implies
The EXIF FocalLengthIn35mmFormat of 24mm implies HFOV 73.74 deg. That is WRONG for these
panorama tiles - DJI crops / distortion-corrects the 2000x1500 pano tiles relative to the full frame.

Measured by estimating focal length from adjacent tile pairs using the exact pure-rotation
homography H = K (R2^T R1) K^-1 and maximising contrast-normalised cross-correlation (NCC) over a
FIXED region of interest. A fixed ROI is essential: a naive mean-absolute-difference metric over
"whatever overlaps" is biased toward small FOV, because shrinking the FOV shrinks the overlap toward
tile centres where alignment is intrinsically easier. That naive metric decreases monotonically and
is NOT a valid estimator - do not use it.

Result, 5 sets spanning 4 countries and 5 years, each curve unimodal with an interior peak:
  wisconsin-0679  66.50      scotland-0496   67.00      safrica-0177  66.50
  india-0153      66.50      aug2026-0368    67.00
  median 66.50, mean 66.70, spread 66.50-67.00

ADOPT: HFOV = 66.7 deg  ->  f_px = (2000/2)/tan(66.7/2 deg) = 1519.46 px
  VFOV = 2*atan((1500/2)/1520.9) = 52.5 deg
  K = [[1519.46, 0, 1000], [0, 1519.46, 750], [0, 0, 1]]
  (an earlier draft of this note said 1520.9 -- arithmetic slip, corrected; the code
   derives f_px from HFOV so it was never wrong there)
The tiny 0.5 deg spread suggests a fixed camera constant; still, allow an optional per-set refinement.

## 2. GimbalRollDegree MUST BE NEGATED
In the convention below, roll must be multiplied by -1. Established by sweeping a roll scale factor
on the 9-tile set (roll = +/-11.5 deg): a clean unimodal bowl with its minimum at exactly -1.00
(-1.25 -> 0.0692, -1.00 -> 0.0651, -0.75 -> 0.0682, 0.0 -> 0.0842, +1.00 -> 0.1163).
Control: the same sweep on the 26-tile sphere (roll ~ 0) is perfectly flat, confirming the test is
actually measuring roll and nothing else.

## 3. Verified coordinate convention (reproduces the measured grids correctly)
World frame: X = East, Y = Up, Z = North.
Gimbal yaw: 0 = North, positive = clockwise toward East. Pitch: 0 = level, -90 = straight down.
Build the camera basis DIRECTLY as vectors rather than composing Euler matrices - less error-prone:
    f  = [sin(yaw)cos(pitch),  sin(pitch),  cos(yaw)cos(pitch)]   # forward / optical axis
    rt = [cos(yaw),            0,          -sin(yaw)]             # right (horizontal)
    dn = cross(rt, f)                                             # down
    then rotate rt and dn about f by (-roll) via Rodrigues
    R = columns [rt, dn, f]  so that  v_world = R @ v_cam
Projection (camera frame X right, Y down, Z forward):
    u = f_px * Xc/Zc + cx ,  v = f_px * Yc/Zc + cy ,  valid only where Zc > 0
Equirect ray for output pixel (i,j) of a WxH canvas (W = 2H):
    theta = ((i+0.5)/W)*2pi - pi        # longitude, 0 = North, +ve toward East
    phi   = pi/2 - ((j+0.5)/H)*pi       # latitude, +pi/2 at top row
    d = [cos(phi)sin(theta), sin(phi), cos(phi)cos(theta)]
Verified: with this convention the stitched horizon of a level sphere lands exactly on row H/2.
Use INVERSE mapping (output pixel -> ray -> each tile) as shown; it is what the prototype validated.

## 4. Consequences of the corrected FOV
Zenith hole: top pitch row is +14.9, plus VFOV/2 = 26.25  ->  coverage stops at about +41.2 deg
elevation. The cap above that is roughly 17% of the sphere and is NEVER captured. Must be filled.
Overlap: yaw steps 45 deg vs HFOV 66.7  -> ~22 deg overlap. Pitch steps 35 deg vs VFOV 52.5 -> ~17 deg.
Native angular resolution: 1519.5 px/radian  ->  a full 360 deg equirect is 2*pi*1519.5 = 9547 px wide.
So 8192x4096 is a sound default (just under native, power-of-two, within GPU max texture size);
offer ~9556-12288 as a max-quality option. Do NOT pick a width without this justification.

## 5. Implementation gotcha: exiftool -n still returns STRINGS
With -j -n, exiftool returns the drone-dji gimbal tags as strings like "-56.40" / "+14.85"
(the sign prefix defeats numeric coercion). float() them explicitly, stripping a leading '+',
or every angle silently becomes a TypeError or, worse, a string comparison. Same for ExposureTime.

## 6. Photometric normalisation that worked
Convert sRGB -> linear, then scale each tile by ref_gain/tile_gain where gain = (ExposureTime*ISO)/FNumber^2,
blend in linear light, then linear -> sRGB on output. Feather weight per tile from normalised distance
to the nearest tile edge, weights normalised across overlapping tiles.

## 7. PHOTOMETRY: these scenes are genuinely HDR - a global tone curve is NOT enough
Measured on the Wisconsin sunset sphere (100_0679) after linear exposure normalisation:
  p1 = 0.00023, p50 = 0.00163, p99 = 0.684, max = 1.149  ->  dynamic range about 5078:1 (~12 stops).
Because each tile is independently auto-exposed, a DJI pano set is effectively an exposure bracket
across the scene, so the merged linear result carries far more range than 8-bit sRGB can show.

Empirically tested and RULED OUT for scenes like this:
  - No tone mapping, or normalising the median to 0.18: sky blows to pure white (verified).
  - Global Reinhard / extended Reinhard / ACES / log-lift: all either crush the ground to black or
    clip the sky, because the ground sits ~9 stops below the sky. Verified visually on real output.

ADOPTED: LOCAL tone mapping, Durand-style, which works well (verified visually - sky keeps cloud
structure and sunset colour while forest, lake, houses, roads and boats are all clearly visible):
  1. L = luminance of the linear merge; logL = log(max(L,1e-6)); fill uncovered pixels with the
     covered mean first, otherwise the filter is dragged toward -inf.
  2. base = guided filter (He et al.) of normalised logL, guide = itself, radius ~3.5% of image
     width, eps = 1e-3. Guided is used instead of Gaussian to limit halos at the horizon.
  3. detail = logL - base
  4. newlog = base*compression + detail*detail_gain, with compression ~0.42, detail_gain ~1.12
  5. rescale linear by exp(newlog)/exp(logL); normalise so p99.5 -> 0.92
  6. soft highlight rolloff  out/(1+max(out-0.8,0)); saturation trim ~0.92; then linear -> sRGB
Measured output for compression 0.42: mean 0.384, p1 0.155, p99 0.910 - full range in use, no clipping.
compression 0.32 is flatter/brighter, 0.55 more contrasty; expose this as a user setting.
Not every set is this extreme (a flat daylight scene needs little compression), so choose the
compression adaptively from the measured dynamic range, and keep the merged LINEAR data available so
the viewer can offer a live exposure slider instead of baking one look in.

## 8. Zenith fill - working approach and the bug to avoid
Filling by extending the topmost covered scanline upward with a progressively larger circular blur,
blending toward the mean colour at the pole, produces a natural-looking sky (verified).
BUG OBSERVED: seeding from a single scanline smears any dark artifact in that row (a tile corner,
flare, or the drone body) into an obvious dark streak across the zenith - clearly visible in the
first test render. FIX: seed from a BAND of rows just below the coverage boundary and take a
per-column robust statistic (median), rejecting dark outliers, before extending upward.
Note the coverage boundary is scalloped (the 8 tile tops), not a straight line - handle per column.

## 9. Coverage numbers confirmed
Raw coverage of a 26-tile sphere = 72.5% of equirect PIXELS. This is exactly the zenith cap and
nothing else: the cap begins at about +40.5 deg elevation, and (90-40.5)/180 = 27.5% of rows.
As SOLID ANGLE the cap is only (1-sin 40.5)/2 = 17.5% of the sphere. Quote whichever you mean.
Verified from the coverage mask: below the cap there are NO holes, gaps or slivers anywhere.

## 10. PERFORMANCE - the naive implementation is far too slow, and why
Measured on this machine with the prototype (float64, every tile evaluated over the ENTIRE canvas):
  8192x4096 sphere, 26 tiles : stitch 186.5 s + local tone map 37.7 s = ~224 s (3.7 min) per panorama
  At that rate the 107 spheres alone would take about 6.6 HOURS single-threaded. Not acceptable.

Root cause: the prototype computes the full HxW ray grid and then, for every tile, does dot products
and validity tests across ALL 33.5 M output pixels, even though a single tile covers only ~1/15 of
the sphere. That is ~26x33.5 M = 872 M wasted vector ops per panorama.

REQUIRED OPTIMISATIONS for the real implementation:
  1. Per-tile BOUNDING BOX. Compute each tile's angular extent from its centre direction plus the
     half-diagonal FOV, convert to a row/column range in the equirect (widening the longitude range
     with latitude, and falling back to full width when the tile touches a pole), and evaluate that
     tile ONLY inside its own box. Expect roughly a 10-15x speedup. This is the single biggest win.
  2. float32 throughout, not float64 - halves memory traffic. Accumulators can stay float32.
  3. Process SETS in parallel with multiprocessing (one set per worker, workers = cores-2). Do not
     try to thread within a single stitch; numpy is already partly vectorised and per-set
     parallelism scales better and is simpler.
  4. Tone mapping cost (37.7 s at 8k) is dominated by the guided filter's uniform_filter passes;
     run the base-layer estimation at reduced resolution (e.g. 1/4) and upsample the base, which is
     valid because the base layer is low-frequency by construction.
Target after optimisation: roughly 15-20 s per 8k sphere per core, so the full 135-set archive in
well under 10 minutes on 8 workers. VERIFY this rather than assuming it.

Memory: an 8192x4096 float32 RGB accumulator is 402 MB, plus weights 134 MB, plus the ray grid
(3 channels) 402 MB. Build the ray grid per bounding box rather than once for the whole canvas, or
peak RSS per worker will exceed 1 GB and 8 workers will thrash. Budget and enforce a per-worker cap.

## 11. Non-sphere modes - measured actual coverage (crop, do not stretch to a full sphere)
  7-tile  "180"  : actual coverage 246.2 x 52.6 deg  (yaw centres span 179.5 deg, PLUS the 66.7 deg
                   HFOV). So it is NOT a 180 degree image - do not label it 180 in the UI.
  9-tile  "wide" : actual coverage 172.9 x 107.1 deg. Wider than the 44.5 deg yaw-centre span
                   suggests, because the strongly pitched-down tiles cover much more longitude.
  4-tile partial : actual coverage 150.5 x 122.3 deg (single yaw column, pitches 0 to -55).
Compute coverage from the actual reprojected mask, never from the tile count or nominal mode.
CROPPING: a plain bounding-box crop leaves visibly RAGGED, scalloped top and bottom edges (confirmed
visually). Offer both a bounding-box crop and an INSCRIBED-RECTANGLE crop (largest fully-covered
axis-aligned rectangle), and default presentation views to the inscribed one.
Yaw-seam safe cropping: find the largest contiguous covered arc on the circle of columns, which may
wrap past +/-180 - a naive min/max over column indices is wrong for sets that straddle the seam.

## 12. MEASURED optimisation results (do not re-derive; these were profiled, not guessed)
Profile of the naive 8192x4096 26-tile stitch, by stage:
    scatter (np.add.at) 41.2 s  45.0%
    gather  (bilinear)  32.3 s  35.3%
    projection          14.9 s  16.2%
    JPEG decode          1.8 s   2.0%
    sRGB->linear         1.4 s   1.5%
So the cost is scatter + gather, NOT decode or the transfer curve. Optimise those two.

What actually worked, measured end to end on the same set at 8192x4096:
    naive, full-canvas per tile, float64, np.add.at ............ 186.5 s   (baseline)
    + per-tile bounding box only .............................. . 81.0 s   2.3x
    + dense window instead of scatter ......................... 137.0 s   SLOWER - do not do this
    + flat-index fancy `+=` and np.take gather ................. 46.8 s   4.0x   <- ADOPT
Correctness of the 4.0x version verified against the baseline output: 99.9992% of the 33.5 M pixels
identical, 265 pixels (0.0008%) differ by >2/255, and exactly ONE pixel differs by more than 32 -
a float tie-break at a tile boundary where two blend weights are nearly equal. Benign.

KEY INSIGHT, and the counter-intuitive part: replacing the scatter with a dense contiguous window
accumulation made it SLOWER (137 s), because it forces bilinear gathers over every pixel in the
window including invalid ones. The win comes instead from:
  a) restricting each tile to its own angular bounding box;
  b) selecting valid pixels with a boolean mask FIRST, so gather and scatter touch only real pixels;
  c) using np.take on a FLATTENED (iw*ih, 3) image for the 4 bilinear taps;
  d) replacing np.add.at with a plain fancy-index `+=` on a FLATTENED accumulator. This is safe and
     roughly 5x faster ONLY because the target indices are unique within a single tile - a tile
     never writes the same output pixel twice. Do not copy this trick to a context where indices
     can repeat; there np.add.at (or np.bincount) is required for correctness.
  e) a 256-entry LUT folding sRGB->linear AND the per-tile exposure gain into one uint8 lookup.

Projected batch cost with 8 worker processes: 107 spheres x 46.8 s / 8 = about 10.4 minutes, plus
tone mapping. Tone mapping measured 37.7 s at 8k; compute the guided-filter BASE layer at 1/4
resolution and upsample (valid - the base is low-frequency by construction) to cut most of that.

## 13. GEOMETRIC REFINEMENT - measured results and the two traps
Implemented in panolib/refine.py. Treats the recorded angles as a prior and solves a small
per-tile rotation from the overlaps. Measured over 14 sets spanning all eight trips and
every capture mode: applied to 14 of 14, ghosting (overlap disagreement relative to scene
contrast) down 9.1% mean, 7.5% median, best 19.1%, worst 2.3% - every set improved.
Corrections landed between 0.99 and 2.40 deg. Cost roughly 15-25 s per panorama.

TRAP 1 - do NOT parameterise the correction as yaw/pitch.
Yaw about world-up is nearly unobservable for the nadir tile: a tile pointing straight
down barely moves when yawed, so the solver can assign it an arbitrary value at almost no
cost. On the real archive this handed DJI_0005 (pitch -89.85) a 3.09 deg yaw correction
while it appeared in only 7 of 91 measured pairs, tripping the safety clamp and causing
good refinements to be rejected. Parameterise instead as a rotation about each tile's OWN
right and up axes (an axis-angle correction); both tilt the optical axis and are strongly
observable wherever the tile points. The roll DOF about the optical axis is excluded
deliberately - it does not translate the patch centre, so a centre-shift measurement
cannot see it.

TRAP 2 - remove the global rotation gauge.
Every measurement is a DIFFERENCE between two tiles, so rotating the entire panorama is
invisible to all of them: a genuine 3-D null space. Whatever the solver puts there is
arbitrary, and leaving it in inflated every correction by a common amount - on the real
archive it appeared as a systematic +2 to +3 deg of pitch across most tiles, which looks
exactly like a runaway solve. Subtract the mean of the per-tile axis-angle vectors after
solving. Spinning the finished panorama does not reduce ghosting.

CLAMP: originally 2.0 deg on the theory that DJI's error is 0.5-2 deg. Measured too tight.
100_0708 and 100_0820 both needed up to 2.8 deg, and applying those corrections raised
pair agreement by ~330% while cutting the ghosting metric 10-20%. On 100_0708 the first
three tiles shifted ~2.5 deg together - the aircraft drifting during that column, a real
error. Raised to 4.0 deg. The clamp is a sanity veto only; the real protection is the
score guard (refined must beat baseline by 1.005x over a FROZEN pair set).

SIGN: phase_correlate returns the peak with NO negation. If b(p) = a(p-s) then
ifft(F_a * conj(F_b)) peaks at n = -s; wanting t with b(p) = a(p+t) means s = -t, so the
peak sits at +t. An earlier derivation negated this and the injected-error test caught it
immediately - recovered +0.915 deg where -1.000 was required. Do not "fix" the sign by
inspection; run tests/test_refine.py, which injects a known error and demands the exact
negative back.

## 14. ALTITUDE - only height above launch is trustworthy (measured 2026-10-03)

Each DJI file records two heights: GPSAltitude with GPSAltitudeRef (and the same value as
XMP drone-dji:AbsoluteAltitude), and XMP drone-dji:RelativeAltitude, height above the
take-off point.

- The sea-level figure is barometric and is NOT corrected for the day's air pressure, so it
  drifts with the weather. Panoramas 100_0220 and 100_0333 were flown from the same spot on
  Skye (11 m apart) on 6 and 7 July 2022; subtracting each one's height above launch puts
  that ground at -73 m and -112 m. Within a single day at one spot the implied ground height
  is steady (median spread 0 m, max 15 m over 16 cases), which is why above-launch is good.
- 9 of 142 panoramas record a drone BELOW sea level (8 in Scotland). Physically impossible
  there; it is the barometric offset.
- Consequence: plates print only "N m above launch". The sea-level value is kept, as
  recorded and with its sign, in print EXIF and in the sidecar (with a note).
- Write trap: GPS altitude is a magnitude plus a separate above/below flag. Writing
  -GPSAltitude=-108.8 with exiftool stores 108.8 and no flag, which reads as ABOVE sea level.
  Write both: -GPS:GPSAltitude#=108.8 -GPS:GPSAltitudeRef#=1. Until 2026-10-03 build.py wrote
  only the magnitude, so the stitched files of the 9 negatives (and 16 Scotland plates)
  showed the wrong sign.
- Cache trap: build.py reuses already-stitched panoramas, so a change to write_gpano never
  reached them (the XMP drone-dji fields added on 2026-09-30 were missing from all 135).
  META_VERSION in build.py now re-tags stale cached panoramas in place on the next build,
  without re-stitching.
