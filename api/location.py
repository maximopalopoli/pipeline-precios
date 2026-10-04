"""Location filter shared by the shopper routes.

A location is a point and a radius in kilometres.  Distance is measured in
SQL on every store before any limit, so a nearby store is never dropped
because farther ones filled the page.  It uses a flat approximation of a
degree in AMBA, accurate enough for a few kilometres; PostGIS is not needed
at these sizes.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Query

from api.published_stores import STORE_HAS_PUBLISHED_PRICE

KM_PER_DEGREE_LAT = 111.0
KM_PER_DEGREE_LON = 85.0

# Argentina's bounding box, as the ETL quality checks use it.
LAT_MIN, LAT_MAX = -55.1, -21.8
LON_MIN, LON_MAX = -73.6, -53.6

MIN_RADIUS_KM = 0.5
MAX_RADIUS_KM = 50.0
# Radii tried in turn until enough grocery chain companies are near.
RADIUS_STEPS_KM = (1.0, 2.0, 5.0, 10.0, 20.0, MAX_RADIUS_KM)
# Phase 0: 1 km reaches 4-7 companies in CABA but none in parts of GBA.
MIN_CHAIN_COMPANIES = 3
# SEPA companies that are not grocery stores: Estacion Lima, FULL and Axion
# (gas-station shops) and Farmacity.  They are listed in single-product
# search, but never make a neighbourhood well covered and take no part in
# shopping list totals.
NON_GROCERY_COMERCIOS = (4, 19, 23, 24)


@dataclass(frozen=True)
class Location:
    lat: float
    lon: float
    radius: float


def _location(lat: float | None, lon: float | None, radius: float) -> Location | None:
    if (lat is None) != (lon is None):
        raise HTTPException(status_code=422, detail="lat y lon van juntas")
    return None if lat is None else Location(lat, lon, radius)


def optional_location(
    lat: float | None = Query(None, ge=LAT_MIN, le=LAT_MAX),
    lon: float | None = Query(None, ge=LON_MIN, le=LON_MAX),
    radius: float = Query(1, ge=MIN_RADIUS_KM, le=MAX_RADIUS_KM),
) -> Location | None:
    """A point in Argentina and a radius, or ``None`` without coordinates."""
    return _location(lat, lon, radius)


def required_location(
    lat: float = Query(..., ge=LAT_MIN, le=LAT_MAX),
    lon: float = Query(..., ge=LON_MIN, le=LON_MAX),
    radius: float = Query(1, ge=MIN_RADIUS_KM, le=MAX_RADIUS_KM),
) -> Location:
    """A point in Argentina and a radius."""
    return Location(lat, lon, radius)


def bind(params: list, value: object) -> str:
    """Append ``value`` to ``params`` and return its placeholder."""
    params.append(value)
    return f"${len(params)}"


def distance_km_sql(location: Location, params: list, store: str = "s") -> str:
    """Distance in km from ``location`` to the store aliased ``store``."""
    lat = f"({store}.sucursales_latitud::float8 - {bind(params, location.lat)}::float8)"
    lon = f"({store}.sucursales_longitud::float8 - {bind(params, location.lon)}::float8)"
    return f"SQRT(POW({lat} * {KM_PER_DEGREE_LAT}, 2) + POW({lon} * {KM_PER_DEGREE_LON}, 2))"


def within_sql(location: Location, params: list, store: str = "s") -> str:
    """Condition that the store is within the radius; unlocated stores are not."""
    distance = distance_km_sql(location, params, store)
    return f"{distance} <= {bind(params, location.radius)}::float8"


def nearest_companies_sql(location: Location, params: list) -> str:
    """Each grocery company's nearest store with a price, up to the largest radius."""
    distance = distance_km_sql(location, params)
    return f"""
        SELECT s.id_comercio, MIN({distance}) AS km
        FROM dim_sucursal s
        WHERE {STORE_HAS_PUBLISHED_PRICE}
          AND s.id_comercio <> ALL({bind(params, list(NON_GROCERY_COMERCIOS))}::int[])
          AND {distance} <= {bind(params, MAX_RADIUS_KM)}::float8
        GROUP BY s.id_comercio
        ORDER BY km"""


def adaptive_radius(requested: float, nearest_km: list[float]) -> dict:
    """The smallest radius from ``requested`` on that reaches enough companies.

    ``nearest_km`` holds each company's nearest store distance.  Radii grow
    through ``RADIUS_STEPS_KM``; when even the largest falls short it is
    returned with the companies it reaches, so the shopper sees the gap.
    """
    steps = [requested, *(step for step in RADIUS_STEPS_KM if step > requested)]
    for radius in steps:
        companies = sum(1 for km in nearest_km if km <= radius)
        if companies >= MIN_CHAIN_COMPANIES:
            break
    return {
        "radio_km": radius,
        "cadenas": companies,
        "ampliado": radius > requested,
        "suficiente": companies >= MIN_CHAIN_COMPANIES,
    }
