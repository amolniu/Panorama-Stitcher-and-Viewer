"""Tests for the print plate: coordinate formatting, paper sizing, and what the caption claims.

The formatting tests matter more than they look. A plate whose whole purpose is to state
where a photograph was taken is worse than useless if it rounds a coordinate wrongly,
mislabels which altitude it is showing, or prints a heading the camera never recorded.
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from panolib.capture import (Capture, decimal_degrees, dms, format_altitude,
                             format_datetime, format_heading, format_latlon)
from panolib.paper import (ACCEPTABLE_DPI, GOOD_DPI, assess_fit, fit_box, max_print_size,
                           parse_size)
from panolib.plate import NO_POSITION, build_rows


def _cap(**kw) -> Capture:
    base = dict(path="x.jpg", width=4000, height=2250)
    base.update(kw)
    return Capture(**base)


# ------------------------------------------------------------------ coordinates

def test_dms_basic():
    assert dms(51.47780, "N", "S") == "51°28′40.1″N"
    assert dms(-0.00147, "E", "W") == "0°00′05.3″W"
    assert dms(0.0, "N", "S") == "0°00′00.0″N"


def test_dms_seconds_are_zero_padded_to_two_digits():
    """3.0 seconds must read 03.0, not 3.0 and certainly not 003.0."""
    out = dms(12.5841666, "N", "S")
    assert out == "12°35′03.0″N", out


def test_dms_rounding_carries():
    """59.97 seconds must roll into the next minute rather than printing 60.0."""
    assert dms(23.9999999, "N", "S") == "24°00′00.0″N"
    # and a minute carry must roll into the degree
    assert dms(8.999999, "N", "S") == "9°00′00.0″N"


def test_hemisphere_from_sign():
    assert dms(-0.5, "N", "S").endswith("S")
    assert dms(0.5, "N", "S").endswith("N")
    assert decimal_degrees(-1.25, "E", "W") == "1.25000°W"


def test_precision_is_not_overstated():
    """Five decimals is ~1.1 m, already finer than the fix; never print more."""
    s = decimal_degrees(51.477801234567, "N", "S")
    assert s == "51.47780°N", s

    # One decimal place on the arc-seconds is about 3.1 m, which is the right order for
    # consumer GNSS. Pull the seconds field out and count its decimals.
    out = dms(51.47780123, "N", "S")                      # 51°28′40.1″N
    seconds = out.split("′")[1].rstrip("″NSEW")  # "40.1"
    assert seconds.count(".") == 1, out
    assert len(seconds.split(".")[1]) == 1, f"{out} has too many decimals on the seconds"


# ------------------------------------------------------------------ altitude & heading

def test_plate_altitude_is_height_above_launch_only():
    """The sea-level figure is barometric and drifts with the weather (the same spot read
    39 m apart on consecutive days; some files record a drone below sea level), so the
    plate prints only height above launch -- labelled, never a bare "altitude"."""
    rows = build_rows(_cap(lat=51.5, lon=-0.1, alt_msl=206.58, alt_agl=19.4))
    alt = next(r for r in rows if r.label == "Altitude")
    assert alt.value == "19 m above launch", alt.value
    assert not any("sea level" in r.value or "207" in r.value for r in rows)
    # a negative sea-level reading never reaches the face
    rows = build_rows(_cap(lat=57.2, lon=-5.9, alt_msl=-108.8, alt_agl=3.2))
    assert next(r for r in rows if r.label == "Altitude").value == "3 m above launch"


def test_sea_level_alone_is_not_printed():
    rows = build_rows(_cap(lat=1.0, lon=2.0, alt_msl=200.0, alt_agl=None))
    assert not any(r.label == "Altitude" for r in rows)


def test_altitude_rounds_to_metres():
    assert format_altitude(206.58) == "207 m"
    assert format_altitude(19.4) == "19 m"
    assert format_altitude(19.4, "ft") == "64 ft"
    assert format_altitude(2.5) == "3 m"          # half away from zero, not banker's
    assert format_altitude(-0.2) == "0 m"         # never "-0 m"
    assert format_altitude(-0.6) == "-1 m"


def test_flying_below_the_take_off_point_reads_as_below():
    """Panoramas flown down into a gorge record a negative height above launch; the
    plate says "below launch" rather than printing a minus sign or a "-0"."""
    def alt(v):
        return next(r for r in build_rows(_cap(lat=1.0, lon=2.0, alt_agl=v)) if r.label == "Altitude").value
    assert alt(-25.4) == "25 m below launch"
    assert alt(-0.4) == "0 m above launch"
    assert alt(-0.6) == "1 m below launch"
    assert alt(42.0) == "42 m above launch"


def test_missing_altitude_is_omitted_not_invented():
    rows = build_rows(_cap(lat=1.0, lon=2.0, alt_msl=None, alt_agl=None))
    assert not any(r.label == "Altitude" for r in rows)


def test_heading_compass_points():
    assert format_heading(0) == "0° N"
    assert format_heading(90) == "90° E"
    assert format_heading(308) == "308° NW"
    assert format_heading(-51.8) == "308° NW"   # wraps to a positive bearing


def test_panorama_shows_no_heading():
    """A full sphere faces every direction, so a single bearing would be meaningless."""
    rows = build_rows(_cap(lat=1.0, lon=2.0, captured="2026:08:03 19:17:56",
                           camera_heading=120.0, is_panorama=True))
    rec = next(r for r in rows if r.label == "Recorded")
    assert "facing" not in rec.value


def test_photo_shows_heading():
    rows = build_rows(_cap(lat=1.0, lon=2.0, captured="2026:08:03 19:17:56",
                           camera_heading=308.0, is_panorama=False))
    rec = next(r for r in rows if r.label == "Recorded")
    assert "308° NW" in rec.value


# ------------------------------------------------------------------ honesty

def test_no_position_says_so():
    rows = build_rows(_cap(lat=None, lon=None))
    assert not any(r.label == "Position" for r in rows)
    assert "No position recorded" in NO_POSITION


def test_caption_never_claims_verification():
    """No rendered string may assert verification.

    The ban is on ASSERTIONS. The long footnote legitimately contains "verification" and
    "certificate" while denying them, so a claim only counts when no negator precedes it
    in the same clause -- otherwise the lint would push the wording to be vaguer, which is
    the one direction it must not move.
    """
    from panolib.plate import (BANNED_CLAIMS, FOOTNOTE, FOOTNOTE_FULL, NO_POSITION,
                               PANORAMA_NOTE)
    negators = ("not", "never", "nothing", "no ", "cannot")

    for text in (FOOTNOTE, FOOTNOTE_FULL, NO_POSITION, PANORAMA_NOTE):
        for clause in text.replace(";", ".").replace(",", ".").split("."):
            low = clause.lower()
            if any(n in low for n in negators):
                continue
            for word in BANNED_CLAIMS:
                assert word not in low, (
                    f"unqualified claim {word!r} in: {clause.strip()!r}")


def test_short_footnote_states_the_caveat():
    """WHEN shown, the short note must still say the data is editable and unverified."""
    from panolib.plate import FOOTNOTE
    low = FOOTNOTE.lower()
    assert "as recorded" in low
    assert "edited" in low, "the default footnote must say the metadata can be edited"
    assert "not a verification" in low


def test_panorama_discloses_its_processing():
    """A stitched sphere is many frames with a synthesised cap; the plate must say so."""
    from panolib.plate import footnote_text
    note = footnote_text(_cap(lat=1.0, lon=2.0, is_panorama=True))
    assert "multiple frames" in note
    assert "synthesised" in note
    plain = footnote_text(_cap(lat=1.0, lon=2.0, is_panorama=False))
    assert "synthesised" not in plain


def test_footnote_is_optional():
    """The note is the owner's choice; printing without it must not raise."""
    from panolib.plate import require_footnote
    rows = build_rows(_cap(lat=1.0, lon=2.0, alt_msl=200.0))
    require_footnote(False, rows)      # no longer compulsory
    require_footnote(True, rows)


def test_empty_field_set_means_no_fields():
    """An empty include set must not fall back to the defaults (falsy-set trap)."""
    assert build_rows(_cap(lat=1.0, lon=2.0, alt_msl=5.0), include=set()) == []


def test_paper_pixels_round_half_up():
    """Python's round() is banker's rounding; paper arithmetic must not use it."""
    from panolib.paper import _px
    assert _px(0.275, 300) == 83          # 82.5 -> 83, not round()'s 82
    assert _px(2.5 / 300, 300) == 3       # 2.5 -> 3


def test_rows_degrade_in_priority_order():
    """Fields present appear; fields absent simply do not."""
    rows = build_rows(_cap(lat=1.0, lon=2.0))
    assert [r.label for r in rows] == ["Position"]


def test_datetime_has_no_invented_timezone():
    s = format_datetime("2026:07:26 12:10:03")
    assert s == "26 July 2026, 12:10"
    for z in ("UTC", "GMT", "+00", "Z"):
        assert z not in s


# ------------------------------------------------------------------ paper & fitting

def test_named_sizes_and_units():
    p = parse_size("a4", 300, "landscape")
    assert (p.width_px, p.height_px) == (3508, 2480), (p.width_px, p.height_px)
    p2 = parse_size("300x400mm", 300, "portrait")
    assert abs(p2.width_in - 300 / 25.4) < 1e-6
    p3 = parse_size("30x40cm", 300, "portrait")
    assert abs(p3.width_px - p2.width_px) <= 1


def test_orientation_follows_the_image():
    land = parse_size("a3", 300, "auto", source_aspect=1.778)
    port = parse_size("a3", 300, "auto", source_aspect=0.56)
    assert land.width_in > land.height_in
    assert port.height_in > port.width_in


def test_bad_size_is_rejected_clearly():
    try:
        parse_size("enormous", 300)
    except ValueError as exc:
        assert "cannot read paper size" in str(exc)
    else:
        raise AssertionError("a nonsense size should raise")


def test_contain_keeps_everything_cover_crops():
    dw, dh, crop = fit_box(4000, 2250, 1000, 1000, "contain")
    assert crop == (0, 0, 4000, 2250)
    assert dw == 1000 and dh == 562
    dw2, dh2, crop2 = fit_box(4000, 2250, 1000, 1000, "cover")
    assert (dw2, dh2) == (1000, 1000)
    assert crop2[2] - crop2[0] < 4000    # sides were cropped


def test_upscaling_is_flagged():
    """A print that needs the source enlarged must say so rather than quietly softening."""
    paper = parse_size("24x36", 300, "auto", 1.778)
    dw, dh, crop = fit_box(4000, 2250, paper.width_px, paper.height_px, "cover")
    rep = assess_fit(4000, 2250, dw, dh, crop, paper)
    assert rep.scale > 2.0
    assert rep.warning is not None and "enlarged" in rep.warning


def test_ample_resolution_is_not_flagged():
    paper = parse_size("a4", 300, "auto", 2.0)
    dw, dh, crop = fit_box(8192, 4096, paper.width_px, paper.height_px, "contain")
    rep = assess_fit(8192, 4096, dw, dh, crop, paper)
    assert rep.warning is None
    assert rep.effective_dpi > GOOD_DPI


def test_max_print_size_matches_the_archive():
    w, h = max_print_size(4000, 2250, GOOD_DPI)
    assert abs(w - 13.333) < 0.01 and abs(h - 7.5) < 0.01
    w2, _ = max_print_size(8192, 4096, GOOD_DPI)
    assert abs(w2 - 27.3) < 0.1


# ------------------------------------------------------------------ target resolution

def _fake_library(tmp: str) -> str:
    """A library with a name shared by two trips, as the real archive has."""
    import json
    os.makedirs(os.path.join(tmp, "panoramas", "a"), exist_ok=True)
    os.makedirs(os.path.join(tmp, "panoramas", "b"), exist_ok=True)
    for sub in ("a", "b"):
        with open(os.path.join(tmp, "panoramas", sub, "eq.jpg"), "wb") as fh:
            fh.write(b"not a real jpeg")
    lib = {"panoramas": [
        {"id": "100_0858-aaaa", "name": "100_0858", "trip": "Scotland", "status": "ok",
         "captured": "2022:07:09 16:17:22", "source": "E:/Scotland/...",
         "equirect": "panoramas/a/eq.jpg"},
        {"id": "100_0858-bbbb", "name": "100_0858", "trip": "India-2023-Media", "status": "ok",
         "captured": "2023:11:19 06:49:38", "source": "E:/India/...",
         "equirect": "panoramas/b/eq.jpg"},
    ]}
    with open(os.path.join(tmp, "library.json"), "w", encoding="utf-8") as fh:
        json.dump(lib, fh)
    return tmp


def test_ambiguous_panorama_name_is_refused():
    """Folder names repeat across SD cards, so a bare name can mean several photographs.

    Resolving it to whichever came first silently prints the wrong one -- which is exactly
    what happened: a request for Scotland's 100_0858 produced a panorama from Uttarakhand.
    """
    import tempfile
    from panolib._print_cmd import AmbiguousTarget, resolve_targets
    tmp = _fake_library(tempfile.mkdtemp(prefix="pano_lib_"))
    try:
        resolve_targets("100_0858", tmp)
    except AmbiguousTarget as exc:
        msg = str(exc)
        assert "100_0858-aaaa" in msg and "100_0858-bbbb" in msg, msg
        assert "Scotland" in msg and "India" in msg
    else:
        raise AssertionError("an ambiguous name must be refused, not guessed")


def test_trip_narrows_an_ambiguous_name():
    import tempfile
    from panolib._print_cmd import resolve_targets
    tmp = _fake_library(tempfile.mkdtemp(prefix="pano_lib_"))
    hits = resolve_targets("100_0858", tmp, trip="Scotland")
    assert len(hits) == 1
    assert hits[0][0].replace("\\", "/").endswith("panoramas/a/eq.jpg"), hits


def test_unique_id_always_resolves():
    import tempfile
    from panolib._print_cmd import resolve_targets
    tmp = _fake_library(tempfile.mkdtemp(prefix="pano_lib_"))
    hits = resolve_targets("100_0858-bbbb", tmp)
    assert len(hits) == 1
    assert hits[0][0].replace("\\", "/").endswith("panoramas/b/eq.jpg")


def test_trip_name_resolves_to_its_panoramas():
    import tempfile
    from panolib._print_cmd import resolve_targets
    tmp = _fake_library(tempfile.mkdtemp(prefix="pano_lib_"))
    hits = resolve_targets("Scotland", tmp)
    assert len(hits) == 1 and hits[0][1] == "100_0858"


def test_same_named_panoramas_print_to_different_files():
    """In the archive 20 DJI folder names are used by more than one panorama (43 of the
    135 folders). A trip batch that holds two of them must not write both to one
    filename, so the print's stem is the id (name + 8 hex of the source path), while
    the console still shows the name."""
    import tempfile
    from panolib._print_cmd import resolve_targets
    tmp = _fake_library(tempfile.mkdtemp(prefix="pano_lib_"))
    a = resolve_targets("100_0858-aaaa", tmp)[0]
    b = resolve_targets("100_0858-bbbb", tmp)[0]
    assert a[1] == b[1] == "100_0858"                                # friendly, for the console
    assert (a[2], b[2]) == ("100_0858-aaaa", "100_0858-bbbb")        # stems, for filenames
    assert resolve_targets("Scotland", tmp)[0][2] == "100_0858-aaaa"
    assert resolve_targets("100_0858", tmp, trip="Scotland")[0][2] == "100_0858-aaaa"


def test_standalone_files_get_a_path_keyed_stem():
    """Camera-roll names restart on every card: DJI_0001.JPG exists in 100MEDIA and in
    101MEDIA. Printed into one folder they must not collide, and the stem must be the
    same every time the same file is printed, so a re-run replaces its own output."""
    import tempfile
    from panolib._print_cmd import resolve_targets
    tmp = tempfile.mkdtemp(prefix="pano_files_")
    paths = []
    for sub in ("100MEDIA", "101MEDIA"):
        os.makedirs(os.path.join(tmp, sub))
        p = os.path.join(tmp, sub, "DJI_0001.JPG")
        with open(p, "wb") as fh:
            fh.write(b"x")
        paths.append(p)
    stems = [resolve_targets(p, tmp)[0][2] for p in paths]
    assert all(s.startswith("DJI_0001-") and len(s) == len("DJI_0001-") + 8 for s in stems), stems
    assert stems[0] != stems[1]
    assert resolve_targets(paths[0], tmp)[0][2] == stems[0], "the stem must be deterministic"
    assert resolve_targets(paths[0], tmp)[0][1] == "DJI_0001"
    by_glob = resolve_targets(os.path.join(tmp, "*", "DJI_0001.JPG"), tmp)
    assert sorted(h[2] for h in by_glob) == sorted(stems)
    by_dir = resolve_targets(os.path.join(tmp, "100MEDIA"), tmp)
    assert [h[2] for h in by_dir] == [stems[0]]


# ------------------------------------------------------------------ place names

def _place(dist_km, name="Garelochhead", admin1="Scotland", bearing=0.0):
    from panolib.geonames import Place
    return Place(name=name, admin1=admin1, country_code="GB",
                 lat=56.0, lon=-4.8, distance_km=dist_km, bearing_deg=bearing)


def test_place_label_near_is_a_plain_name():
    """Inside the locality the town is just a label, not a distance."""
    assert _place(0.3, "Fatehpur Sikri", "Uttar Pradesh").label() == "Fatehpur Sikri, Uttar Pradesh"
    assert _place(4.5, "Chicago", "Illinois").label() == "Chicago, Illinois"


def test_place_label_far_gives_bearing_and_distance():
    """Beyond the locality radius, claiming the town as the location would be false.

    These are real positions from the archive: 37 km from Garelochhead is open Highland,
    not Garelochhead.
    """
    assert _place(37.0, bearing=0.0).label() == "37 km N of Garelochhead, Scotland"
    assert _place(59.4, "Jozini", "KwaZulu-Natal", bearing=90.0).label() ==         "59 km E of Jozini, KwaZulu-Natal"


def test_place_label_uses_whole_kilometres():
    """Past the locality radius the distance is to a town CENTROID, so decimals would be
    spurious precision."""
    assert _place(37.4, bearing=0.0).label() == "37 km N of Garelochhead, Scotland"
    assert _place(27.95, "Portree", "Scotland", bearing=135.0).label() ==         "28 km SE of Portree, Scotland"


def test_mid_distance_is_still_a_locality_label():
    """A town 7 km away is the locality; the archive's Lake Wisconsin sets sit like this."""
    assert _place(6.9, "Lake Wisconsin", "Wisconsin", bearing=45.0).label() ==         "Lake Wisconsin, Wisconsin"


def test_place_radius_is_generous_because_centroids_are_not_city_edges():
    """A tight radius would print a distance for a photo taken inside a large city."""
    from panolib.geonames import AT_PLACE_KM
    assert AT_PLACE_KM >= 15.0, "Chicago's centroid is 4.5 km from its own lakefront"
    # and not so generous that genuinely remote Highland positions get mislabelled
    assert AT_PLACE_KM < 31.0, "31 km from Beauly is not Beauly"


def test_place_compass_points():
    for deg, want in ((0, "N"), (45, "NE"), (90, "E"), (180, "S"), (315, "NW")):
        assert _place(50.0, bearing=deg).compass == want


def test_place_label_without_admin1():
    p = _place(1.0, "Somewhere", "")
    assert p.label() == "Somewhere"


def test_derived_place_is_not_a_recorded_field():
    """The place name must never appear as a data row; it is an inference."""
    rows = build_rows(_cap(lat=51.48, lon=-0.01, alt_msl=265.0, alt_agl=126.0))
    for r in rows:
        assert "Greenwich" not in r.value
        assert r.label in ("Position", "Altitude", "Recorded", "Camera")


# ------------------------------------------------------------------ metadata in the print file
# Synthetic values throughout -- an invented position and a fake serial -- so these tests
# carry no real flight data.

FAKE_SERIAL = "TESTSERIAL0001"


def _exif_cap(**kw) -> Capture:
    base = dict(lat=12.3456789, lon=-45.6789012, alt_msl=224.779, alt_agl=86.5,
                captured="2026:08:28 19:30:08", make="DJI", model="FC7303",
                serial=FAKE_SERIAL)
    base.update(kw)
    return _cap(**base)


def _save_with_exif(cap, include=None, ext=".jpg", dpi=100):
    import tempfile
    from PIL import Image
    from panolib.plate import print_exif, save_print
    path = os.path.join(tempfile.mkdtemp(prefix="pano_exif_"), "print" + ext)
    ok = save_print(Image.new("RGB", (120, 90), (90, 120, 150)), path,
                    parse_size("5x7", dpi, "landscape"), exif=print_exif(cap, include))
    return path, ok


def _read_exif(path):
    """(IFD0, Exif IFD, GPS IFD, info) read back with Pillow."""
    from PIL import ExifTags, Image
    with Image.open(path) as im:
        ex = im.getexif()
        return (dict(ex), dict(ex.get_ifd(ExifTags.IFD.Exif)),
                dict(ex.get_ifd(ExifTags.IFD.GPSInfo)), dict(im.info),
                dict(ex.get_ifd(ExifTags.IFD.IFD1)))


def _deg(t, ref) -> float:
    d, m, s = (float(x) for x in t)
    v = d + m / 60 + s / 3600
    return -v if ref in ("S", "W") else v


def _byte(v) -> int:
    return v[0] if isinstance(v, (bytes, tuple)) else int(v)


def test_print_file_carries_the_recorded_position_time_and_camera():
    """A print is a new file; without this, photo apps see a picture with no place or
    date. The values must be the camera's own, not the plate's rounded ones."""
    from PIL import ExifTags
    G, B = ExifTags.GPS, ExifTags.Base
    path, ok = _save_with_exif(_exif_cap())
    assert ok
    ifd0, sub, gps, info, _ = _read_exif(path)
    assert gps[G.GPSLatitudeRef] == "N" and gps[G.GPSLongitudeRef] == "W"
    assert abs(_deg(gps[G.GPSLatitude], "N") - 12.3456789) < 1e-9
    assert abs(_deg(gps[G.GPSLongitude], "W") - (-45.6789012)) < 1e-9
    assert _byte(gps[G.GPSAltitudeRef]) == 0 and abs(float(gps[G.GPSAltitude]) - 224.779) < 1e-9
    assert sub[B.DateTimeOriginal] == "2026:08:28 19:30:08"
    assert ifd0[B.Make] == "DJI" and ifd0[B.Model] == "FC7303" and ifd0[B.Software] == "panolib"
    # the print size is stated the same way in both headers
    assert float(ifd0[B.XResolution]) == 100 and ifd0[B.ResolutionUnit] == 2
    assert tuple(round(v) for v in info["dpi"]) == (100, 100)
    # and exiftool -- what every other tool here trusts -- reads the same values back
    try:
        from panolib.exif import find_exiftool
        exe = find_exiftool(None)
    except Exception:
        return
    from panolib.capture import read_capture
    back = read_capture(path, exe)
    assert abs(back.lat - 12.3456789) < 1e-9 and abs(back.lon + 45.6789012) < 1e-9, (back.lat, back.lon)
    assert abs(back.alt_msl - 224.779) < 1e-6 and back.captured == "2026:08:28 19:30:08"
    assert back.make == "DJI" and back.model == "FC7303" and back.serial is None


def test_print_file_never_carries_the_serial_projection_orientation_or_thumbnail():
    from PIL import ExifTags
    path, _ = _save_with_exif(_exif_cap())
    with open(path, "rb") as fh:
        data = fh.read()
    assert FAKE_SERIAL.encode() not in data, "the aircraft serial must never be written"
    assert b"GPano" not in data and b"equirectangular" not in data
    ifd0, _, _, info, ifd1 = _read_exif(path)
    assert not info.get("xmp")
    assert ExifTags.Base.Orientation not in ifd0
    assert not ifd1, "no thumbnail: the source's would show a different picture"


def test_print_metadata_southern_eastern_and_below_sea_level():
    """Signs live in the reference tags. Some DJI files really do record a negative
    'above sea level'; the file keeps saying what the camera said."""
    from PIL import ExifTags
    G = ExifTags.GPS
    _, _, gps, _, _ = _read_exif(_save_with_exif(_exif_cap(lat=-33.5, lon=151.25, alt_msl=-16.79))[0])
    assert gps[G.GPSLatitudeRef] == "S" and gps[G.GPSLongitudeRef] == "E"
    assert abs(_deg(gps[G.GPSLatitude], "S") + 33.5) < 1e-9
    assert abs(_deg(gps[G.GPSLongitude], "E") - 151.25) < 1e-9
    assert _byte(gps[G.GPSAltitudeRef]) == 1 and abs(float(gps[G.GPSAltitude]) - 16.79) < 1e-9


def test_print_metadata_follows_the_plates_fields():
    """The file never discloses more than the face of the print: leave position off the
    plate and the GPS position stays out of the file too."""
    from PIL import ExifTags
    G, B = ExifTags.GPS, ExifTags.Base
    _, sub, gps, _, _ = _read_exif(_save_with_exif(_exif_cap(), include={"altitude", "recorded"})[0])
    assert G.GPSLatitude not in gps and G.GPSLongitude not in gps
    assert G.GPSAltitude in gps and B.DateTimeOriginal in sub
    ifd0, sub, gps, _, _ = _read_exif(_save_with_exif(_exif_cap(), include=set())[0])
    assert not gps and B.DateTimeOriginal not in sub
    assert ifd0[B.Model] == "FC7303"                     # the camera is not location data
    _, _, gps, _, _ = _read_exif(_save_with_exif(_exif_cap(lat=None, lon=None))[0])
    assert G.GPSLatitude not in gps                      # nothing recorded, nothing written


def test_print_metadata_carries_rounded_seconds_and_skips_a_malformed_time():
    from PIL import ExifTags
    path, _ = _save_with_exif(_exif_cap(lat=10.9999999999999, captured="28 Aug 2026 19:30"))
    _, sub, gps, _, _ = _read_exif(path)
    d, m, s = (float(x) for x in gps[ExifTags.GPS.GPSLatitude])
    assert s < 60 and m < 60, (d, m, s)
    assert abs(_deg(gps[ExifTags.GPS.GPSLatitude], "N") - 11.0) < 1e-9
    assert ExifTags.Base.DateTimeOriginal not in sub, "a malformed time must not be written"


def test_png_and_tiff_prints_carry_the_metadata_too():
    """PNG goes through Pillow like JPEG. Pillow's compressed-TIFF writer cannot write
    the GPS block, so TIFF goes through exiftool -- and says so when it cannot."""
    from PIL import ExifTags
    path, ok = _save_with_exif(_exif_cap(), ext=".png")
    _, _, gps, _, _ = _read_exif(path)
    assert ok and abs(_deg(gps[ExifTags.GPS.GPSLatitude], "N") - 12.3456789) < 1e-9
    path, ok = _save_with_exif(_exif_cap(), ext=".tif")
    try:
        from panolib.exif import find_exiftool
        exe = find_exiftool(None)
    except Exception:
        assert ok is False, "without exiftool a TIFF must report that GPS was not embedded"
        return
    assert ok
    from PIL import Image
    from panolib.capture import read_capture
    back = read_capture(path, exe)
    assert abs(back.lat - 12.3456789) < 1e-7 and abs(back.lon + 45.6789012) < 1e-7
    assert back.captured == "2026:08:28 19:30:08" and back.model == "FC7303"
    assert back.serial is None
    with Image.open(path) as im:                          # the image itself is untouched
        assert im.size == (120, 90) and im.info.get("compression") == "tiff_lzw"
        assert tuple(round(v) for v in im.info["dpi"]) == (100, 100)


def test_cli_print_embeds_the_metadata_unless_position_is_left_off():
    """End to end through `python -m panolib print`, from a photo that records a
    position, a time, a camera and a serial."""
    import glob
    import subprocess
    import tempfile
    from PIL import ExifTags, Image
    from PIL.TiffImagePlugin import IFDRational
    try:
        from panolib.exif import find_exiftool
        find_exiftool(None)
    except Exception:
        return                                            # the CLI reads sources with exiftool
    G, B = ExifTags.GPS, ExifTags.Base
    tmp = tempfile.mkdtemp(prefix="pano_cli_exif_")
    src = os.path.join(tmp, "DJI_9999.JPG")
    ex = Image.Exif()
    ex[B.Make], ex[B.Model] = "DJI", "FC7303"
    sub = ex.get_ifd(ExifTags.IFD.Exif)
    sub[B.DateTimeOriginal] = "2026:08:28 19:30:08"
    sub[0xA431] = FAKE_SERIAL
    ex.get_ifd(ExifTags.IFD.GPSInfo).update({
        G.GPSVersionID: b"\x02\x03\x00\x00",
        G.GPSLatitudeRef: "N", G.GPSLatitude: (IFDRational(12, 1), IFDRational(20, 1), IFDRational(444, 10)),
        G.GPSLongitudeRef: "W", G.GPSLongitude: (IFDRational(45, 1), IFDRational(40, 1), IFDRational(444, 10)),
        G.GPSAltitudeRef: 0, G.GPSAltitude: IFDRational(224779, 1000)})
    Image.new("RGB", (600, 400), (70, 110, 90)).save(src, format="JPEG", exif=ex)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, PYTHONIOENCODING="utf-8")

    def run(out, *extra):
        proc = subprocess.run([sys.executable, "-m", "panolib", "print", src, "--size", "5x7",
                               "--dpi", "72", "--no-place", "--out", out, "--out-dir", tmp, *extra],
                              cwd=root, env=env, capture_output=True, text=True, timeout=300)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        hits = glob.glob(os.path.join(out, "*.jpg"))
        assert len(hits) == 1, (hits, proc.stdout)
        return hits[0]

    full = run(os.path.join(tmp, "full"))
    ifd0, sub, gps, _, _ = _read_exif(full)
    assert abs(_deg(gps[G.GPSLatitude], "N") - (12 + 20 / 60 + 44.4 / 3600)) < 1e-9
    assert abs(_deg(gps[G.GPSLongitude], "W") + (45 + 40 / 60 + 44.4 / 3600)) < 1e-9
    assert sub[B.DateTimeOriginal] == "2026:08:28 19:30:08" and ifd0[B.Model] == "FC7303"
    with open(full, "rb") as fh:
        assert FAKE_SERIAL.encode() not in fh.read()
    hidden = run(os.path.join(tmp, "no-position"), "--fields", "altitude,recorded")
    _, sub, gps, _, _ = _read_exif(hidden)
    assert G.GPSLatitude not in gps and G.GPSLongitude not in gps
    assert sub[B.DateTimeOriginal] == "2026:08:28 19:30:08"


def _main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {fn.__name__}: {exc}")
        except Exception as exc:
            failed += 1
            print(f"  ERROR {fn.__name__}: {exc.__class__.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
