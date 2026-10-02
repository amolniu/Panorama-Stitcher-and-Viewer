"""Tests for grouping panoramas by actual location and time.

The gazetteer is an optional dependency, so these tests substitute a fake lookup and
exercise the grouping, time-splitting, labelling and misfiled logic on their own.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from panolib import regions as R
from panolib.geonames import Place


# A tiny fake world: coordinates near these points resolve to these places.
_WORLD = [
    (10.00, 10.00, Place("Chicago", "Illinois", "US", 10.00, 10.00, 0, 0)),
    (12.00, 10.00, Place("Slinger", "Wisconsin", "US", 12.00, 10.00, 0, 0)),
    (20.00, 30.00, Place("Malibu", "California", "US", 20.00, 30.00, 0, 0)),
    (22.00, 30.00, Place("Chula Vista", "California", "US", 22.00, 30.00, 0, 0)),
    (30.00, 50.00, Place("Portree", "Scotland", "GB", 30.00, 50.00, 0, 0)),
    (40.00, 70.00, Place("Bhowali", "Uttarakhand", "IN", 40.00, 70.00, 0, 0)),
    (42.00, 70.00, Place("New Delhi", "NCT", "IN", 42.00, 70.00, 0, 0)),
]


def _fake_nearest(lat, lon):
    best = min(_WORLD, key=lambda w: (w[0] - lat) ** 2 + (w[1] - lon) ** 2)
    return best[2]


def _entry(name, lat, lon, captured, folder, status="ok"):
    return {"id": f"{name}-x", "name": name, "lat": lat, "lon": lon,
            "captured": captured, "trip": folder, "status": status}


def _with_fake_world(fn):
    real = R.nearest_place
    R.nearest_place = _fake_nearest
    try:
        return fn()
    finally:
        R.nearest_place = real


# ------------------------------------------------------------------ labels

def test_date_range_labels():
    from datetime import datetime as D
    assert R._date_range_label(D(2022, 7, 5), D(2022, 7, 10)) == "Jul 2022"
    assert R._date_range_label(D(2023, 9, 1), D(2023, 10, 20)) == "Sep–Oct 2023"
    assert R._date_range_label(D(2023, 11, 1), D(2024, 1, 5)) == "Nov 2023 – Jan 2024"


def test_region_aliases_make_headers_readable():
    def run():
        e = [_entry("a", 42.00, 70.00, "2023:11:15 10:00:00", "India-2023-Media")]
        R.assign_location_trips(e)
        return e[0]
    e = _with_fake_world(run)
    assert e["location"]["region"] == "Delhi", e["location"]
    assert e["location_trip"] == "Delhi · Nov 2023"


# ------------------------------------------------------------------ grouping

def test_same_region_different_years_are_separate_trips():
    """Malibu 2021 and Chula Vista 2023 are both California; they are not one trip."""
    def run():
        e = [_entry("m", 20.00, 30.00, "2021:10:12 12:00:00", "Drone 2021"),
             _entry("c1", 22.00, 30.00, "2023:10:26 12:00:00", "India-2023-Media"),
             _entry("c2", 22.01, 30.01, "2023:10:27 12:00:00", "India-2023-Media")]
        trips = R.assign_location_trips(e)
        return e, trips
    e, trips = _with_fake_world(run)
    cal = [t for t in trips if t.region == "California"]
    assert len(cal) == 2, [t.label for t in cal]
    assert {t.count for t in cal} == {1, 2}
    assert e[0]["location_trip"] != e[1]["location_trip"]
    assert e[1]["location_trip"] == e[2]["location_trip"]


def test_gap_threshold_splits_trips():
    def run():
        e = [_entry("a", 10.00, 10.00, "2023:09:01 12:00:00", "Drone 2023"),
             _entry("b", 10.01, 10.01, "2023:10:15 12:00:00", "Drone 2023"),   # 44 days: same
             _entry("c", 10.00, 10.00, "2026:08:03 12:00:00", "2026 - Drone")] # years: new
        return R.assign_location_trips(e), e
    trips, e = _with_fake_world(run)
    ill = [t for t in trips if t.region == "Illinois"]
    assert len(ill) == 2
    assert e[0]["location_trip"] == e[1]["location_trip"] == "Illinois · Sep–Oct 2023"
    assert e[2]["location_trip"] == "Illinois · Aug 2026"


def test_mixed_folder_is_split_by_location():
    """One backup folder, two countries -> two trips, and the folder is recorded on both."""
    def run():
        e = [_entry("u1", 40.00, 70.00, "2023:11:18 09:00:00", "India-2023-Media"),
             _entry("u2", 40.01, 70.01, "2023:11:19 09:00:00", "India-2023-Media"),
             _entry("cv", 22.00, 30.00, "2023:10:26 09:00:00", "India-2023-Media")]
        return R.assign_location_trips(e), e
    trips, e = _with_fake_world(run)
    labels = {t.label for t in trips}
    assert "Uttarakhand · Nov 2023" in labels
    assert "California · Oct 2023" in labels
    for t in trips:
        assert t.source_folders == ["India-2023-Media"]


def test_trips_are_chronological():
    def run():
        e = [_entry("a", 30.00, 50.00, "2022:07:06 12:00:00", "Scotland"),
             _entry("b", 10.00, 10.00, "2026:08:03 12:00:00", "2026 - Drone"),
             _entry("c", 20.00, 30.00, "2021:10:12 12:00:00", "Drone 2021")]
        return R.assign_location_trips(e)
    trips = _with_fake_world(run)
    assert [t.region for t in trips] == ["California", "Scotland", "Illinois"]


# ------------------------------------------------------------------ misfiled

def test_misfiled_detection():
    def run():
        e = [_entry("ok1", 30.00, 50.00, "2022:07:06 12:00:00", "Scotland"),
             _entry("ok2", 12.00, 10.00, "2023:07:22 12:00:00", "Drone 2023"),
             _entry("bad1", 22.00, 30.00, "2023:10:26 12:00:00", "India-2023-Media"),
             _entry("bad2", 40.00, 70.00, "2023:11:18 12:00:00", "Drone - BU Unknown")]
        R.assign_location_trips(e)
        return e, R.misfiled_report(e)
    e, report = _with_fake_world(run)
    flags = {x["name"]: x["misfiled"] for x in e}
    assert flags == {"ok1": False, "ok2": False, "bad1": True, "bad2": True}, flags
    names = {r["name"] for r in report}
    assert names == {"bad1", "bad2"}
    cv = next(r for r in report if r["name"] == "bad1")
    assert "Chula Vista" in cv["actually"] and "California" in cv["actually"]


def test_entries_without_position_keep_their_folder():
    """Nothing disappears from the gallery just because it has no GPS."""
    def run():
        e = [_entry("nogps", None, None, "2022:07:06 12:00:00", "Scotland"),
             _entry("err", 30.00, 50.00, "2022:07:06 12:00:00", "Scotland", status="error")]
        R.assign_location_trips(e)
        return e
    e = _with_fake_world(run)
    assert e[0]["location"] is None
    assert e[0]["location_trip"] == "Scotland"
    assert e[0]["misfiled"] is False
    assert e[1]["location"] is None          # not located when the stitch failed


def test_source_folder_is_preserved():
    def run():
        e = [_entry("a", 22.00, 30.00, "2023:10:26 12:00:00", "India-2023-Media")]
        R.assign_location_trips(e)
        return e[0]
    e = _with_fake_world(run)
    assert e["source_folder"] == "India-2023-Media"
    assert e["trip"] == "India-2023-Media"       # untouched, for anything still reading it


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
