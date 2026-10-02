# Brief: print-ready plates with a location/altitude caption

## The request (from the user, verbatim in spirit)
"Prepare an image for printing -- the image doesn't necessarily need to be a panorama,
these could be standalone images too. Overlay the coordinates and altitude of where the
image was taken in nice text, which can help highlight the authenticity of the image."

So: a print-ready output, for ANY image (panorama or ordinary photo), carrying a
typographic caption of where and how high it was taken.

## Verified facts about the source material (measured 2026-09-30, do not re-derive)

### Standalone photos exist in quantity and carry everything we need
`E:\2026 - Drone - Mix BU - Aug\DCIM\100MEDIA` holds 470 .JPG plus 61 .MP4. There are
several such `NNNMEDIA` camera-roll folders across the archive (they were deliberately
EXCLUDED from panorama scanning; see docs/GROUND_TRUTH.md).

A representative standalone frame:
  Model FC7303 (DJI Mavic Air 2), 4000x2250 (16:9), full resolution
  DateTimeOriginal   2026:07:26 12:10:03
  GPSLatitude        51 deg 28' 40.08" N
  GPSLongitude       0 deg 0' 5.29" W
  GPSAltitude        206.5 m Above Sea Level
  AbsoluteAltitude   +206.58      (XMP-drone-dji, same thing, more precision)
  RelativeAltitude   +19.40       (metres ABOVE TAKEOFF POINT -- the "how high was the
                                   drone" number people actually mean)
  GimbalYawDegree    +0.00        (where the CAMERA was pointing; 0 = north, + = clockwise)
  FlightYawDegree    +47.60       (airframe heading -- NOT the camera direction)
  FNumber 2.8, ExposureTime 1/500, ISO 100, FocalLength 4.5mm
  SerialNumber       <redacted>
  XPComment          Type=N, Mode=P, DE=None     <- Type=N means a NORMAL photo
                                                    (panorama tiles are Type=P)

Panorama outputs from this tool already carry GPS, altitude, timestamp and GPano tags
(written with exiftool), plus a `meta-<key>.json` holding everything known about the set.

### Fonts available on this machine (C:\Windows\Fonts, 346 installed)
georgia.ttf/georgiab.ttf, times.ttf/timesbd.ttf, consola.ttf/consolab.ttf,
arial.ttf/arialbd.ttf, calibri.ttf/calibrib.ttf, cambria.ttc, segoeui.ttf/segoeuib.ttf,
seguisb.ttf (semibold), constan.ttf/constanb.ttf (Constantia), pala.ttf/palab.ttf
(Palatino), LBRITE.TTF (Lucida Bright), ebrima.ttf.
Pillow needs an explicit TTF path; there is no automatic system font lookup.

### Tooling
Python 3.13 with numpy, Pillow 12.3, scipy, in a venv at .venv.
exiftool 13.59 available. ffmpeg available. NO ImageMagick.
Existing modules to build on: panolib/exif.py (exiftool batch reader, with the
string-coercion gotcha already handled), panolib/geometry.py, panolib/build.py.

## An honesty constraint that must shape the design
EXIF is editable with free tools. A caption showing coordinates does NOT prove an image
is authentic, and the output must not imply otherwise. What it genuinely does is:
  - present the provenance the camera recorded, legibly and in a form a viewer can check
    against a map;
  - make tampering more effortful and more obvious, because the claim is now visible and
    falsifiable rather than buried in metadata nobody reads.
Design and word the plate so it ATTESTS WHAT THE FILE RECORDS rather than asserting proof.
Avoid words like "verified", "certified" or "authenticated" unless something real backs
them. If a stronger guarantee is proposed, it must be stated plainly what it does and does
not prove.
