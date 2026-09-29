"""Geographic scope of a published ingestion.

A scope keeps only the stores of one region, and the product rows of those
stores, while staging.  Unlike the development bounds in
``etl.development_ingestion``, a scoped ingestion is a production run: it
publishes, and every row left out is counted in its own bucket.

``SEPA_REGIONAL_SCOPE`` selects a named scope; unset or blank keeps every
store.  Changing the scope is a configuration change followed by
re-ingestion.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from etl.quality.checks import _ARG_LAT_MAX, _ARG_LAT_MIN, _ARG_LON_MAX, _ARG_LON_MIN

_SCOPE_ENV = "SEPA_REGIONAL_SCOPE"
_EARTH_RADIUS_KM = 6371.0088


class StoreScope(str, Enum):
    """Where a store falls relative to a regional scope."""

    IN_SCOPE = "in_scope"
    OUT_OF_SCOPE = "out_of_scope"
    # In a scoped province, but without plausible coordinates to place it
    UNLOCATED = "unlocated"


@dataclass(frozen=True)
class RegionalScope:
    """Stores within ``radius_km`` of a center, in one of ``provinces``.

    A store in ``provinces_inside_radius`` lies inside the radius wherever it
    is, so it stays in scope even without coordinates.  Any other store
    without plausible coordinates is unlocated: counted, never guessed.
    """

    name: str
    center_lat: float
    center_lon: float
    radius_km: float
    provinces: frozenset[str]
    provinces_inside_radius: frozenset[str] = frozenset()

    def classify(self, latitude: str, longitude: str, provincia: str) -> StoreScope:
        """Classify one store from its raw staging values."""
        provincia = provincia.strip()
        if provincia not in self.provinces:
            return StoreScope.OUT_OF_SCOPE
        coordinates = _plausible_coordinates(latitude, longitude)
        if coordinates is None:
            if provincia in self.provinces_inside_radius:
                return StoreScope.IN_SCOPE
            return StoreScope.UNLOCATED
        distance = _distance_km(self.center_lat, self.center_lon, *coordinates)
        if distance <= self.radius_km:
            return StoreScope.IN_SCOPE
        return StoreScope.OUT_OF_SCOPE


# CABA plus Buenos Aires province within 60 km of the Obelisco.  All of CABA
# is within about 20 km of the Obelisco.
AMBA = RegionalScope(
    name="amba",
    center_lat=-34.603722,
    center_lon=-58.381592,
    radius_km=60.0,
    provinces=frozenset({"AR-C", "AR-B"}),
    provinces_inside_radius=frozenset({"AR-C"}),
)

SCOPES: dict[str, RegionalScope] = {AMBA.name: AMBA}


def scope_from_environment(
    env: Mapping[str, str] | None = None,
) -> RegionalScope | None:
    """Return the configured scope, or ``None`` to keep every store.

    An unknown name fails fast rather than silently publishing the whole
    country or nothing.
    """
    values = os.environ if env is None else env
    name = values.get(_SCOPE_ENV, "").strip().lower()
    if not name:
        return None
    try:
        return SCOPES[name]
    except KeyError:
        known = ", ".join(sorted(SCOPES))
        raise ValueError(f"{_SCOPE_ENV} must be one of: {known}") from None


def _plausible_coordinates(latitude: str, longitude: str) -> tuple[float, float] | None:
    """Parse coordinates inside Argentina's bounding box, else ``None``."""
    try:
        lat = float(latitude)
        lon = float(longitude)
    except ValueError:
        return None
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return None
    if not (_ARG_LAT_MIN <= lat <= _ARG_LAT_MAX and _ARG_LON_MIN <= lon <= _ARG_LON_MAX):
        return None
    return lat, lon


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle (haversine) distance in kilometers."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))
