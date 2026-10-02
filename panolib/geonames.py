"""Turning coordinates into a human-readable place name.

A printed coordinate tells you where a photograph was taken only if you already know the
place. "51°28′40.1″N 0°00′05.3″W" and "Greenwich, England" carry the same information, but
only one of them is legible across a room.

Two deliberate choices:

* **Offline.** The lookup uses a gazetteer bundled with ``reverse_geocoder`` (about 150,000
  populated places, 8 MB) rather than a web geocoding service. A web service would give
  finer detail -- street and neighbourhood rather than nearest town -- at the cost of
  sending the position of every photograph to a third party, and of only working with a
  connection. For a personal archive that is a bad trade.
* **The distance is part of the answer.** The gazetteer returns the nearest populated
  place, which on this archive ranges from 0.3 km (Fatehpur Sikri) to 13.4 km (New Delhi).
  Printing "New Delhi" for a position 13 km outside it would be wrong, so anything beyond a
  few kilometres is phrased as a bearing and distance from the town instead of a label for
  it.

This is DERIVED, not recorded. It belongs in the title, where a human label is expected,
and never in the data rows, which carry only what the file itself says.
"""

from __future__ import annotations

import io
import contextlib
from dataclasses import dataclass

from .places import bearing_deg, haversine_m

#: Within this, the nearest town is used as a plain locality label; beyond it, the label
#: becomes a bearing and distance from that town.
#:
#: 25 km looks generous for a "nearest town" radius, and it is chosen because the gazetteer
#: returns a city's CENTROID with no population attached. Chicago's centroid is 4.5 km from
#: Burnham Harbour, but the harbour is unambiguously in Chicago -- the city is 30 km across
#: -- so a tighter rule prints "4.5 km SE of Chicago" for a photograph taken inside it.
#: Checked against this archive the radius behaves at both extremes: Chicago (4.5 km),
#: Fort William (14 km) and New Delhi (13 km) read as locality labels, while Beauly
#: (31 km), Garelochhead (39 km) and Jozini (59 km) correctly become distances, because
#: those really are remote Highland and Drakensberg positions far from any town.
AT_PLACE_KM = 25.0

_COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")

_searcher = None
_unavailable = False


@dataclass
class Place:
    """The nearest populated place to a position."""

    name: str
    admin1: str
    country_code: str
    lat: float
    lon: float
    distance_km: float
    bearing_deg: float

    @property
    def compass(self) -> str:
        return _COMPASS[int(((self.bearing_deg % 360) + 22.5) // 45) % 8]

    def label(self, long_form: bool = False) -> str:
        """A line for the plate's title.

        Near the town it is simply its name; further out it says how far and in which
        direction, which is both more honest and more useful for finding the spot again.
        """
        where = f"{self.name}, {self.admin1}" if self.admin1 else self.name
        if self.distance_km <= AT_PLACE_KM:
            return where
        # Whole kilometres only: anything reaching this branch is already more than
        # AT_PLACE_KM away, where a decimal place would be spurious precision on a
        # distance measured to a town's centroid.
        return f"{self.distance_km:.0f} km {self.compass} of {where}"


def _get_searcher():
    """Load the gazetteer once. Returns None when the package is not installed."""
    global _searcher, _unavailable
    if _searcher is not None or _unavailable:
        return _searcher
    try:
        import reverse_geocoder
        # the first load prints a progress line to stdout, which would corrupt the
        # command's own output in a batch run
        with contextlib.redirect_stdout(io.StringIO()):
            _searcher = reverse_geocoder.RGeocoder(mode=1, verbose=False)
    except Exception:
        _unavailable = True
        _searcher = None
    return _searcher


def available() -> bool:
    return _get_searcher() is not None


def nearest_place(lat: float, lon: float) -> Place | None:
    """Nearest populated place, or None when the gazetteer is unavailable."""
    rg = _get_searcher()
    if rg is None:
        return None
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            hit = rg.query([(float(lat), float(lon))])[0]
    except Exception:
        return None
    try:
        plat, plon = float(hit["lat"]), float(hit["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    return Place(
        name=hit.get("name", "").strip(),
        admin1=hit.get("admin1", "").strip(),
        country_code=hit.get("cc", "").strip(),
        lat=plat, lon=plon,
        distance_km=haversine_m(lat, lon, plat, plon) / 1000.0,
        bearing_deg=bearing_deg(plat, plon, lat, lon),
    )


def place_label(lat: float | None, lon: float | None) -> str | None:
    """Convenience: the title line for a position, or None if it cannot be derived."""
    if lat is None or lon is None:
        return None
    p = nearest_place(lat, lon)
    return p.label() if p else None
