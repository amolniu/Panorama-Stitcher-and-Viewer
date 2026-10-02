# Drone Panorama Viewer

Turns the folders of raw tiles your DJI Mavic Air 2 leaves on the SD card into finished
panoramas, and gives you the DJI-album experience on your laptop — plus a set of views
the app does not offer.

DJI stitches panoramas inside the Fly app, so the SD card only ever holds the source
tiles: `PANORAMA/100_0679/DJI_0001.JPG … DJI_0026.JPG`. On a computer that is 26 loose
photos. This rebuilds the panorama from them.

## Status

Built against the real archive on the E: drive: **135 panorama sets, all 135 stitched,
none failed** — 107 full spheres, 12 wide grids, 7 sweeps, 4 vertical strips and 5 single
frames, across eight trips in Wisconsin, Scotland, India, South Africa and Chicago.
Quality: 116 good, 16 ok, 3 poor; median score 0.88.

Angle refinement was applied to **123 of the 130 sets it was attempted on**, raising
overlap agreement by a median of **3.9×**. The seven it declined were degenerate cases —
two-tile sets, a four-tile partial, and sets where too few pairs correlated — and it left
those exactly as the metadata placed them.

38 minutes of wall clock on ten workers, 0.7 GB of output. Roughly half of that is the
refinement pass; `--no-refine` builds in about 25 minutes. A re-run takes 26 seconds
because everything is cached.

## Quick start

```bash
python -m panolib build "E:/" --workers 10
```

```bash
python -m panolib view
```

The first command finds every panorama set under the folder you point at, stitches each
one, and writes everything to `out/`. The second serves `out/` and opens the viewer.

To look before committing to a long run:

```bash
python -m panolib scan "E:/"
```

## Viewing modes

Press the mode buttons, or use the keyboard: `i` immersive, `g` globe, `p` little planet,
`t` tunnel, `space` auto-spin, `h` hide the interface, `f` fullscreen.

| Mode | What you see |
| --- | --- |
| **Immersive 360** | Inside the sphere looking out — the DJI album view. This is the "inverted globe": the picture is on the inside surface and you are at the centre. |
| **Globe** | The same sphere from the *outside*, as a ball you orbit. The literal inverse of immersive. |
| **Little Planet** | Stereographic from below; the ground curls into a tiny world. |
| **Tunnel** | The same projection centred on the zenith — you appear to look up a shaft. |
| **Fisheye** | Very wide but still natural-looking. Good for one big landmark. |
| **Pannini** | Wide angle that keeps vertical lines vertical. Best for towns, cliffs and canyons. |
| **Flat** | The raw 2:1 equirectangular map, pan and zoom. |
| **Mercator** | Straight verticals across a wide strip. |
| **Mirror Ball** | The whole sphere reflected in a chrome bead. |
| **Cube Cross** | Six faces unfolded. No pole stretching — the best view for checking a stitch. |

The **Little-planet pull** slider in Immersive mode morphs continuously between a normal
view and a little planet, so the transition is one camera move rather than a hard cut.
Modes that need a full sphere are disabled for partial panoramas instead of showing
something broken.

### Grouped by where, not by folder

The gallery groups panoramas by **where and when they were actually taken**, derived from
their GPS and timestamps at build time — not by the backup folder they landed in. A trip
is a region over a contiguous span of time, so the same place visited two years apart is
two trips, and one folder holding several countries is several.

That matters because the folders on this archive are wrong often enough to mislead:
`India-2023-Media` holds five Indian states *and* four panoramas from Chula Vista,
California; `Drone 2023` mixes Wisconsin with Chicago; and a folder named
`Drone - BU Unknown 08.31.25` is Pelican Bay, Florida. Grouped by place the archive reads
as a coherent history — Malibu 2021, Scotland 2022, Wisconsin and Chicago 2023, India
Nov–Dec 2023, Wisconsin 2024, South Africa 2024, Florida 2024, Chicago 2026.

A **By folder** toggle in the gallery restores the old view. Each entry keeps its folder as
`source_folder`, and `library.json → misfiled` lists the panoramas whose folder names a
place they were not taken. Nothing on the source drive is moved or renamed.

### Walking between viewpoints

Every tile carries GPS, so the archive knows its own geography. Panoramas taken within
400 m of each other are linked, and an **also here** row appears under the view showing
each neighbour's compass direction and distance — click to fly there. Panoramas are also
clustered into *places*, so one trip that produced thirty panoramas at six locations
browses as six locations.

## Cinematic clips

A 360 panorama is awkward to share — an equirect JPEG shows people a distorted map
rather than the place. A short clip that moves through the sphere reads immediately and
plays anywhere.

```bash
python -m panolib render 100_0679 --move dolly
```

| Move | What it does |
| --- | --- |
| `planet-spin` | A little planet turning a full revolution. |
| `dolly` | Pulls back from a normal view into a little planet while turning. |
| `orbit` | A slow level pan right around the horizon. |
| `globe` | Orbits the panorama as a solid ball seen from outside. |
| `tunnel-rise` | Rises from the horizon to look straight up the zenith. |

Takes a panorama name or id from the library, or a path to any equirect JPEG. Output is
H.264 in `yuv420p` with `faststart`, so it plays in browsers, on phones and in messaging
apps. `--size 1280x720 --seconds 8 --fps 30` control the rest.

## Prints with a caption of record

Prepares any image — a stitched panorama or an ordinary frame — as a print-ready file with
a typographic caption of where and how high it was taken.

```bash
python -m panolib print DJI_0383.JPG --size a3 --title "Chicago Lakefront"
```

Takes a file, a folder, a glob, or a panorama name from the library. Paper by name
(`a3`, `16x24`, `12x18`, `pano-2to1`) or free-form (`300x400mm`, `30x40cm`); orientation
follows the image. The DPI is written into the file so a lab prints it at the intended
size.

Output files are named `<source>-<8 hex>-<paper>-<style>.jpg`, e.g.
`100_0368-f602714f-a3-gallery.jpg`: the panorama's library id, or for a standalone photo
its basename plus a hash of its path. The eight digits are there because names alone
collide — in this archive 20 DJI folder names are used by more than one panorama (43 of
the 135 folders), and camera-roll names (`DJI_0001.JPG`) restart on every card — and a
print must never be overwritten by a different photograph that happens to share its name.
Re-printing the *same* source replaces its own file. Three styles:

- **`gallery`** (default) — light mat, caption below the image. The photograph is never
  covered, and the print has a border to trim to.
- **`survey`** — the same layout on a dark ground.
- **`overlay`** — caption set on the picture itself, lower left, over a gradient scrim.
  Handsome when the lower corner is dark; the scrim strength and a drop shadow are derived
  from the luminance actually under the text, so it stays legible over bright ground too.

> **Caveat — `overlay` crops by default; the mat styles never do.** Overlay fills the sheet
> and trims the overflow (`--fit cover`), because a caption sitting on the picture wants the
> picture to reach the paper edge. `gallery` and `survey` fit the whole frame inside the
> margins (`--fit contain`), so nothing is lost.
>
> On matched proportions the two are identical — a 2:1 panorama onto `pano-2to1` keeps 100%.
> On mismatched ones the loss is substantial, and it is the paper that decides it:
>
> | 16:9 frame onto | kept | lost |
> | --- | --- | --- |
> | `pano-2to1` | 88.9% | 11.1% |
> | `16x24`, `12x18` | 84.4% | 15.6% |
> | `a3` | 79.5% | 20.5% |
> | `8x10` | 70.3% | 29.7% |
>
> Pass `--fit contain` to keep the whole frame in overlay too; the image is then centred and
> the ground shows as bands where it does not reach the edge. Either way the crop is centred
> and cannot be nudged yet.

### Printing from the viewer

The viewer has a **Print…** button (also `Ctrl+P`). It can print either the whole
panorama or **the view you are looking at** — the heading, tilt and zoom you composed on
screen, rendered at print resolution with the same caption. That second option is the
reason the panel exists: the equirect of a 360° sphere is a strange 2:1 strip with a
stretched sky, but a chosen framing is simply a photograph of the place. The plate labels
its bearing as *view facing*, not *camera facing*, because it is a fact about the print
rather than something the camera recorded.

**Proof** renders a quick low-resolution preview in the panel; **Print** writes the full
file to `out/prints/from-viewer/` with its sidecar, and links to it. Both use exactly the
code the command line uses, so the output is identical. "The view you are looking at"
includes the exposure slider: the print is what was on screen, and the sidecar records
the exposure along with the heading, tilt and zoom. Each distinct request gets its own
filename -- the same framing at a different size, orientation, dpi or title never
overwrites an earlier print -- and files are written under a temporary name and renamed
into place, so a print is either complete or absent, never half-written. The image and
its record are published together under one name: if either file a re-print would
replace is open in another program (Windows refuses the rename), the whole pair goes to a
numbered sibling and the earlier pair is left as it was.

Two honest limits. A view *magnifies* the panorama: an 8192-px equirect holds about 1300
pixels per radian, so a 75° view on A3 is already being enlarged, and the panel says so
with the same warning the command line gives. And the panel only works while
`python -m panolib view` is running — a copy of `out/` opened some other way shows the
viewer but no Print button, because there is nothing behind it.

The API behind the button writes files and runs exiftool, so it is deliberately hard to
misuse: it listens on 127.0.0.1 only; every request needs a per-session token that is
injected into the page (another website in your browser cannot read it) and must come
from the page's own origin; it refuses requests addressed to any host name but loopback
(so a hostile site cannot reach it through DNS rebinding); it accepts library ids, never
paths; sizes, dpi, pixel count and text are bounded and checked *before* a job is
accepted; and renders run one at a time, in strips, so a 24x36-inch print at 300 dpi
(78 megapixels) needs a few hundred megabytes rather than many gigabytes.
`tests/test_printapi.py` checks each of those refusals against the real server.

### Place names

With no `--title`, the plate derives one from the coordinates:

```
Chicago, Illinois
37 km N of Garelochhead, Scotland
```

The lookup is **offline** — a gazetteer of ~150,000 populated places bundled with the
package, not a web geocoder. A web service would give street-level detail, but at the cost
of sending the position of every photograph you print to a third party, and of needing a
connection. For a personal archive that is the wrong trade.

The distance is part of the answer. The gazetteer returns a town's *centroid*, which on this
archive ranges from 0.3 km (Fatehpur Sikri) to 59 km (a Drakensberg position east of
Jozini). Inside roughly 25 km the town is used as a plain locality label; beyond it the
label becomes a bearing and distance, because calling a spot 37 km into the Highlands
"Garelochhead" would be false. The radius is deliberately generous: Chicago's centroid is
4.5 km from its own lakefront, so a tight rule would print a distance for a photograph taken
inside the city.

This name is **derived, not recorded**, so it appears only as the title — where a human
label is expected — and never among the data rows. The sidecar records which it was, the
exact distance and bearing, and the dataset used. `--no-place` turns it off.

This works on far more than the panoramas — the archive holds about 3,900 ordinary drone
frames, and every one sampled carried GPS, both altitudes, a timestamp and a heading.

### What the plate does and does not prove

The plate prints **what the image file records**. It is a transcription, not a
verification. Metadata of this kind can be edited with free tools, so a printed coordinate
is not evidence on its own — and you will not find the words *verified*, *certified* or
*authenticated* on a plate this tool produces. There is a test that fails the build if any
of them appear as a claim. Whether to spell that caveat out on the print itself is the
owner's choice (`--footnote`, off by default).

What it does do is make the claim public and checkable. Buried in EXIF, *51°28′40.1″N
0°00′05.3″W, 37 m above launch, facing 308° NW* is a claim nobody reads. Printed under the
picture it is one anyone can test — against a map, against the skyline in the frame,
against where the shadows fall at that hour and latitude. That raises the cost of a false
claim from editing one field to making a whole photograph agree with it.

Some limits are worth knowing. The coordinates come from the aircraft's own satellite
receiver and are good to a few metres, so the plate never prints more than one decimal of
arc-second (about 3 m) — printing six decimal places would imply a survey-grade fix that
does not exist. Both heights are barometric, and the two are never conflated: one is
measured from sea level, the other from wherever the aircraft took off, and they differ by
hundreds of metres inland. The clock is the camera's own and carries no time zone, so the
time is printed as recorded rather than converted. A stitched panorama says so, because a
plate attesting provenance while implying one untouched exposure would undercut itself.

`--digest` prints a short SHA-256 of the source file. That proves nothing about where the
picture was taken — a doctored file has a digest too — but it binds this print to one exact
file, so you can show later that a file you hold is the one the print came from.
`--sidecar` writes the full record as JSON alongside.

By default the plate carries the data lines alone. `--footnote short` adds a one-line note
that the values are transcribed and editable; `--footnote full` adds the complete caveat
with datum, accuracy and the barometric/declination limits. When shown, the wording is
still held to the rule above.

### Honest resolution

A print is only as good as the pixels behind it, so the tool says what each file can
actually carry rather than silently enlarging it:

| Source | Fills at 300 dpi | Still crisp at 180 dpi |
| --- | --- | --- |
| standalone photo, 4000×2250 | 13.3″ × 7.5″ | 22″ × 12.5″ |
| stitched sphere, 8192×4096 | 27.3″ × 13.7″ | 45″ × 22.7″ |

Asking for more warns and names the size the file supports.

## How it works

Every tile carries the camera orientation DJI recorded when it was taken
(`GimbalYawDegree`, `GimbalPitchDegree`, `GimbalRollDegree` in XMP). Tiles are therefore
placed by **direct geometric reprojection**, not by feature matching. That matters for
aerial panoramas: half of every frame is featureless sky, which is exactly where
feature-based stitchers fail.

For each output pixel the code computes a viewing ray, rotates it into each tile's
frame, projects it through a pinhole model and samples. Tiles are merged in linear
light after being normalised to a common exposure from their EXIF, then tone mapped.

Details, and the measurements behind every constant, are in
[docs/GROUND_TRUTH.md](docs/GROUND_TRUTH.md).

### Things worth knowing about this camera

- **The field of view is 66.7°, not the 73.7° the EXIF implies.** DJI crops and
  distortion-corrects panorama tiles, so the `FocalLengthIn35mmFormat` figure is wrong
  for them. The real value was measured from tile overlaps across five sets spanning
  four countries and five years, which agreed to within 0.5°. Using the EXIF figure
  gives visibly misaligned stitches.
- **`GimbalRollDegree` has to be negated** to match the coordinate convention used here.
- **The zenith is never photographed.** The highest pitch row is +14.9°, so with a 52.5°
  vertical field nothing above about +41° is captured — roughly 17% of the sphere. The
  cap is synthesised from the sky just below it, and the viewer's Globe and Little Planet
  modes would otherwise show a hole straight through the top.
- **These scenes are genuinely HDR.** Each tile is auto-exposed on its own, so a set is
  effectively an exposure bracket; a measured sunset spanned about 5000:1. A single
  global tone curve cannot hold both sky and ground, so a local (Durand-style) operator
  is used by default.
- **The "180°" mode is not 180°.** Its seven tiles span about 246° once the lens field is
  added to the sweep of their centres.

## Output

```
out/
  library.json                 index the viewer reads
  index.html app.js …          the viewer itself (copied in, works offline)
  panoramas/<id>/
    equirect-<key>.jpg         full resolution, tagged as a Google photo sphere
    preview-<key>.jpg          2048 px, loads first so the view appears instantly
    thumb-<key>.jpg            gallery thumbnail
    planet-<key>.jpg           little-planet still
    meta-<key>.json            everything known about this panorama
```

The equirects carry **GPano** tags plus the original GPS, timestamp and camera model, so
they are recognised as 360 photos by Google Photos, Facebook, Pannellum, Marzipano and
most VR viewers — not only by this tool.

Set ids include a hash of the source path. Folder names repeat constantly across SD-card
backups (`100_0679` exists under several of them), and without the hash those panoramas
would silently overwrite each other.

## Quality scores

Every panorama is scored automatically so a large archive can be triaged without opening
each one. The score combines how well overlapping tiles agree, how cleanly the 360° seam
closes, and how much of the expected field was captured.

Overlap disagreement is measured **relative to the scene's own contrast**. A low pass over
grass and trees is full of fine detail, so the same sub-pixel misalignment produces a much
larger raw difference there than over calm water — comparing raw differences would grade
the landscape rather than the stitch. The thresholds come from measuring 22 sets across
every trip and capture mode in the archive, not from taste.

## Options

```
python -m panolib build <folder> [options]

  --width N            equirect width; 0 (default) uses the camera's native resolution
  --tonemap MODE       local (default) | global | none
  --compression F      tone-map strength 0.3–0.9; omit to choose per scene
  --workers N          parallel sets (default: cores − 2)
  --limit N            only the first N sets, for a quick trial
  --force              rebuild even when a cached result exists
  --no-fill            leave the zenith cap empty instead of synthesising it
  --no-planet          skip little-planet stills
```

Rebuilds are cached on the tiles' size, timestamp and angles together with the build
settings, so re-running only redoes what changed.

## Requirements

- Python 3.11+ with `numpy`, `pillow`, `scipy` (`pip install -r requirements.txt`)
- [ExifTool](https://exiftool.org/) on `PATH` (or `--exiftool <path>`)
- Optional: `reverse_geocoder` for place names on prints. It bundles an 8 MB gazetteer and
  runs entirely offline; without it prints simply have no derived title.

No internet connection is needed — three.js is vendored into `viewer/vendor/`.

## The refinement pass

DJI's recorded angles carry roughly 0.5–2° of error — sometimes more when the aircraft
drifts mid-capture. At 1519 px per radian that is 13–50 px of misregistration, which is
exactly the doubling you see on ridgelines, rooftops and shorelines.

So the metadata is treated as a *prior*, not as truth. Adjacent tiles are rendered into a
shared tangent plane, phase correlation measures how far apart they actually are, and one
least-squares solve finds the small rotation for each tile that best explains every
measurement at once. It runs by default; `--no-refine` turns it off.

Measured over fourteen sets spanning all eight trips and every capture mode: **applied to
14 of 14, ghosting down 9.1% on average** (median 7.5%, best 19.1%, worst 2.3% — every
one improved). Across the full archive it was applied to 123 of 130, lifting overlap
agreement by a median of 3.9× with corrections of 1.79° (median) up to 3.93°.

Three design points are what make it safe rather than merely clever:

- **It can only help.** Every run scores agreement across a frozen set of pairs before and
  after, and keeps the metadata-only placement unless the score genuinely improves. A
  refinement that cannot prove itself does not ship, and the reason is recorded.
- **Corrections are per-tile rotations, not yaw/pitch.** Yaw is nearly unobservable for the
  nadir tile — a tile pointing straight down barely responds to it — so solving in yaw
  handed that tile 3.09° of nonsense and tripped the safety clamp. An axis-angle
  correction about each tile's own axes has no such singularity.
- **The global rotation is removed.** Every measurement is a *difference* between two
  tiles, so rotating the whole panorama is invisible to all of them. Left in, that free
  parameter inflated every correction by a common 2–3°. Spinning the finished panorama
  does not reduce ghosting, so the gauge is subtracted.

The signs are pinned by test, not by argument: `tests/test_refine.py` injects a known
+1.0° error into one tile and requires the solver to return −1.0°. A flipped correlation
sign or a transposed Jacobian row fails loudly there. It caught a real sign error during
development.

## Known limitations

- **Propeller intrusions.** On some sets the drone's own propeller clips the top edge of
  the upward-pitched tiles. There is no tile above that row to outvote it, so the
  affected columns are detected and discarded; a faint soft patch can remain in the
  synthesised sky.
- **Moving subjects ghost.** Water, foliage and vehicles move between tiles, several
  seconds apart. Feathered blending softens this rather than removing it, and no angle
  correction can help — the scene itself changed.
- **Parallax at low altitude.** A panorama assumes the camera rotates about a single
  point. A few metres above textured ground that is not true, and the resulting
  misregistration is not a rotation error, so refinement reduces it but cannot remove it.
  These are the sets that score lowest.
