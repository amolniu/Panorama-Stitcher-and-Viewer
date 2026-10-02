"""Grouping panoramas by where they were taken.

Every tile carries GPS, so an archive shot over several years across several countries
already knows its own geography. Two things fall out of that for free: panoramas taken
at the same spot can be linked so you can hop between viewpoints, and repeat visits to
one place can be put side by side across dates.
"""

from __future__ import annotations

import math
from collections import defaultdict

EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial compass bearing from point 1 to point 2, 0 = north."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def link_nearby(entries: list[dict], radius_m: float = 400.0,
                max_links: int = 6) -> None:
    """Annotate each entry with the panoramas taken near it, nearest first.

    Modifies ``entries`` in place, adding a ``nearby`` list of
    ``{id, name, distance_m, bearing, captured}``. This is what lets the viewer offer
    "another viewpoint from here" and turns a flight's worth of panoramas into
    something you can walk through rather than a flat list.

    Comparison is O(n^2), which is nothing at 135 entries and stays fine into the
    thousands; if this archive ever grows past that, grid-bucket by rounded latitude
    and longitude first.
    """
    located = [e for e in entries
               if e.get("lat") is not None and e.get("lon") is not None
               and e.get("status") == "ok"]

    for e in entries:
        e["nearby"] = []

    for i, a in enumerate(located):
        found = []
        for j, b in enumerate(located):
            if i == j:
                continue
            d = haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
            if d <= radius_m:
                found.append({
                    "id": b["id"],
                    "name": b.get("name"),
                    "distance_m": round(d, 1),
                    "bearing": round(bearing_deg(a["lat"], a["lon"], b["lat"], b["lon"]), 1),
                    "captured": b.get("captured"),
                })
        found.sort(key=lambda x: x["distance_m"])
        a["nearby"] = found[:max_links]


def cluster_places(entries: list[dict], radius_m: float = 600.0) -> list[dict]:
    """Group panoramas into named places by simple single-link clustering.

    Gives the gallery a geographic level between "trip" and "individual panorama",
    which is what you actually want when one trip produced thirty panoramas at six
    different locations.
    """
    located = [e for e in entries
               if e.get("lat") is not None and e.get("lon") is not None
               and e.get("status") == "ok"]
    if not located:
        return []

    parent = list(range(len(located)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    for i in range(len(located)):
        for j in range(i + 1, len(located)):
            if haversine_m(located[i]["lat"], located[i]["lon"],
                           located[j]["lat"], located[j]["lon"]) <= radius_m:
                union(i, j)

    groups: dict[int, list[dict]] = defaultdict(list)
    for idx, e in enumerate(located):
        groups[find(idx)].append(e)

    places = []
    for members in groups.values():
        lats = [m["lat"] for m in members]
        lons = [m["lon"] for m in members]
        dates = sorted({(m.get("captured") or "")[:10] for m in members if m.get("captured")})
        trips = sorted({m.get("trip") for m in members if m.get("trip")})
        place_id = f"place-{len(places):03d}"
        place = {
            "id": place_id,
            "trip": trips[0] if trips else None,
            "lat": round(sum(lats) / len(lats), 6),
            "lon": round(sum(lons) / len(lons), 6),
            "count": len(members),
            "dates": dates,
            "panoramas": [m["id"] for m in members],
        }
        for m in members:
            m["place"] = place_id
        places.append(place)

    places.sort(key=lambda p: (-p["count"], p.get("trip") or ""))
    return places
