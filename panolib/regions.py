"""Grouping panoramas by where and when they were actually taken.

The library's ``trip`` field comes from the folder a set was backed up into, and folders
lie. On this archive ``India-2023-Media`` holds five Indian states and four panoramas from
Chula Vista, California; ``Drone 2023`` mixes Wisconsin with Chicago; and a folder called
``Drone - BU Unknown`` turns out to be Pelican Bay, Florida. Every tile carries GPS and a
timestamp, so the archive can describe its own geography instead.

A trip is a REGION over a contiguous span of TIME. Geography alone is not enough: the
California panoramas are Malibu in October 2021 and Chula Vista in October 2023, two years
apart, and nobody would call those one trip. So sets in the same region are split wherever
the gap between consecutive captures exceeds ``TRIP_GAP_DAYS``.

Nothing here touches the source drive. It only changes how the library labels things;
the folder-derived ``trip`` is kept alongside as ``source_folder`` so nothing is lost.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass, field
from datetime import datetime

from .geonames import nearest_place

#: Consecutive captures in the same region further apart than this are separate trips.
TRIP_GAP_DAYS = 60

#: Friendlier names for a few administrative labels that read oddly on a gallery header.
REGION_ALIASES = {
    "NCT": "Delhi",
    "Andaman and Nicobar Islands": "Andaman Islands",
}

COUNTRY_NAMES = {
    "US": "United States", "GB": "United Kingdom", "IN": "India", "ZA": "South Africa",
    "CA": "Canada", "AU": "Australia", "FR": "France", "DE": "Germany", "ES": "Spain",
    "IT": "Italy", "MX": "Mexico", "JP": "Japan", "NZ": "New Zealand", "IE": "Ireland",
}

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


@dataclass
class LocationTrip:
    id: str
    label: str
    region: str
    country: str
    country_code: str
    start: str
    end: str
    count: int
    towns: list[str]
    source_folders: list[str]
    panoramas: list[str] = field(default_factory=list)


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y:%m:%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19] if " " in s else s[:10], fmt)
        except ValueError:
            continue
    return None


def _date_range_label(a: datetime, b: datetime) -> str:
    """``Jul 2022``, ``Sep–Oct 2023``, or ``Nov 2023 – Jan 2024``."""
    if (a.year, a.month) == (b.year, b.month):
        return f"{_MONTHS[a.month - 1]} {a.year}"
    if a.year == b.year:
        return f"{_MONTHS[a.month - 1]}–{_MONTHS[b.month - 1]} {a.year}"
    return f"{_MONTHS[a.month - 1]} {a.year} – {_MONTHS[b.month - 1]} {b.year}"


def locate(entry: dict) -> dict | None:
    """Where one panorama is, from its coordinates. None when it has no position."""
    lat, lon = entry.get("lat"), entry.get("lon")
    if lat is None or lon is None:
        return None
    p = nearest_place(lat, lon)
    if p is None:
        return None
    region = REGION_ALIASES.get(p.admin1, p.admin1) or p.name
    return {
        "country_code": p.country_code,
        "country": COUNTRY_NAMES.get(p.country_code, p.country_code),
        "admin1": p.admin1,
        "region": region,
        "town": p.name,
        "town_distance_km": round(p.distance_km, 1),
    }


def assign_location_trips(entries: list[dict]) -> list[LocationTrip]:
    """Annotate each entry with ``location``, ``location_trip`` and ``misfiled``.

    Returns the trip list. Entries without a position keep their folder-derived trip as
    the location trip, flagged ``location: None``, so nothing silently disappears from
    the gallery.
    """
    # 1. locate every panorama
    for e in entries:
        e["source_folder"] = e.get("source_folder") or e.get("trip")
        e["location"] = locate(e) if e.get("status") == "ok" else None

    # 2. bucket by region, then split each bucket on time gaps
    by_region: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for e in entries:
        loc = e.get("location")
        if loc:
            by_region[(loc["country_code"], loc["region"])].append(e)

    trips: list[LocationTrip] = []
    for (cc, region), members in by_region.items():
        dated = sorted(members, key=lambda x: _parse_dt(x.get("captured")) or datetime.min)
        runs: list[list[dict]] = []
        for e in dated:
            dt = _parse_dt(e.get("captured"))
            if runs and dt is not None:
                prev = _parse_dt(runs[-1][-1].get("captured"))
                if prev is not None and (dt - prev).days > TRIP_GAP_DAYS:
                    runs.append([e])
                    continue
            if runs and dt is None:
                runs[-1].append(e)
                continue
            if not runs:
                runs.append([e])
            else:
                runs[-1].append(e)

        for run in runs:
            dts = [d for d in (_parse_dt(x.get("captured")) for x in run) if d]
            start = min(dts) if dts else None
            end = max(dts) if dts else None
            when = _date_range_label(start, end) if start and end else "undated"
            towns = [t for t, _ in collections.Counter(
                x["location"]["town"] for x in run).most_common(3)]
            folders = sorted({x.get("source_folder") or "" for x in run})
            country = COUNTRY_NAMES.get(cc, cc)
            label = f"{region} · {when}"
            tid = f"{cc}-{region}-{start.strftime('%Y%m') if start else 'undated'}".lower()
            tid = tid.replace(" ", "-")
            trip = LocationTrip(
                id=tid, label=label, region=region, country=country, country_code=cc,
                start=start.strftime("%Y-%m-%d") if start else "",
                end=end.strftime("%Y-%m-%d") if end else "",
                count=len(run), towns=towns, source_folders=folders,
                panoramas=[x["id"] for x in run],
            )
            trips.append(trip)
            for x in run:
                x["location_trip"] = label
                x["location_trip_id"] = tid
                # misfiled = the folder's name does not describe where this was taken.
                # Compared loosely: a folder called "Scotland" or "South Africa" matches
                # its region/country; "India-2023-Media" does not match California.
                folder = (x.get("source_folder") or "").lower()
                words = {region.lower(), country.lower(), cc.lower()}
                if cc == "US":
                    words.add("drone")  # the US folders are named "Drone 2023" etc.
                x["misfiled"] = not any(w and w in folder for w in words)

    # 3. anything unlocated falls back to its folder
    for e in entries:
        if "location_trip" not in e:
            e["location_trip"] = e.get("source_folder") or "Unlocated"
            e["location_trip_id"] = "folder-" + (e.get("source_folder") or "unlocated").lower()
            e["misfiled"] = False

    trips.sort(key=lambda t: (t.start or "9999", t.label))
    return trips


def misfiled_report(entries: list[dict]) -> list[dict]:
    """The panoramas whose folder name does not describe where they were taken."""
    out = []
    for e in entries:
        if e.get("misfiled") and e.get("location"):
            out.append({
                "id": e["id"], "name": e["name"],
                "folder": e.get("source_folder"),
                "actually": f"{e['location']['town']}, {e['location']['region']}, "
                            f"{e['location']['country']}",
                "captured": (e.get("captured") or "")[:10],
                "source": e.get("source"),
            })
    return out
