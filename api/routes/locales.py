"""Nearby stores and per-store promotions endpoints."""

from functools import lru_cache

from fastapi import APIRouter, Depends, Query
from geopy.exc import GeocoderTimedOut, GeocoderUnavailable
from geopy.geocoders import Nominatim

from api.db import fetch, fetchrow
from api.location import (
    Location,
    adaptive_radius,
    bind,
    distance_km_sql,
    nearest_companies_sql,
    required_location,
    within_sql,
)
from api.price_date import PRICE_DATE_SQL
from api.published_stores import LATEST_PUBLISHED_FECHA_ID, STORE_HAS_PUBLISHED_PRICE
from api.pricing import (
    ANY_SLOT,
    PROMO_JOINS,
    promo_condition_sql,
    promo_label_sql,
    promo_price_sql,
)

router = APIRouter()

# Every promotion in force, conditional or not, labelled with its requirement.
PROMO_PRICE = promo_price_sql(ANY_SLOT)
PROMO_LABEL = promo_label_sql(ANY_SLOT)
PROMO_CONDITION = promo_condition_sql(ANY_SLOT)


@lru_cache(maxsize=256)
def _geocode(address: str) -> tuple[float, float] | None:
    geolocator = Nominatim(user_agent="pipeline-precios-api")
    try:
        location = geolocator.geocode(address, country_codes="ar", timeout=10)
    except (GeocoderTimedOut, GeocoderUnavailable):
        return None
    if location is None:
        return None
    return (location.latitude, location.longitude)


@router.get("/geocode")
async def geocode(address: str = Query(...), city: str = Query("Buenos Aires")):
    full = f"{address}, {city}, Argentina"
    coords = _geocode(full)
    if coords is None:
        return {"lat": None, "lon": None}
    return {"lat": coords[0], "lon": coords[1]}


@router.get("/location/radius")
async def get_radius(location: Location = Depends(required_location)):
    """The radius to search around a point: the requested one, grown if needed.

    It grows through 1, 2, 5, 10, 20 and 50 km until grocery stores of at
    least three chain companies with a price in the latest published day are
    within it.  ``cadenas`` counts those companies; ``suficiente`` is false
    when even 50 km reaches fewer.
    """
    params: list = []
    rows = await fetch(nearest_companies_sql(location, params), *params)
    return adaptive_radius(location.radius, [float(r["km"]) for r in rows])


@router.get("/locales")
async def get_locales(
    location: Location = Depends(required_location),
    cadenas: list[str] | None = Query(None),
):
    """The nearest stores with a price in the latest published day."""
    params: list = []
    # Bound in the order the statement reads them.
    distance = distance_km_sql(location, params)
    conditions = [STORE_HAS_PUBLISHED_PRICE, within_sql(location, params)]
    if cadenas:
        conditions.append(f"c.comercio_bandera_nombre = ANY({bind(params, cadenas)}::text[])")

    rows = await fetch(
        f"""
        SELECT
            s.sucursal_sk,
            s.sucursales_nombre       AS nombre,
            c.comercio_bandera_nombre AS cadena,
            s.sucursales_tipo         AS tipo,
            s.sucursales_calle        AS calle,
            s.sucursales_numero       AS numero,
            s.sucursales_localidad    AS localidad,
            s.sucursales_latitud      AS lat,
            s.sucursales_longitud     AS lon,
            pr.nombre                 AS provincia,
            ROUND({distance}::numeric, 1) AS distancia_km
        FROM dim_sucursal s
        JOIN dim_comercio  c  ON s.comercio_sk  = c.comercio_sk
        JOIN dim_provincia pr ON s.provincia_sk  = pr.provincia_sk
        WHERE {" AND ".join(conditions)}
        ORDER BY distancia_km, s.sucursal_sk
        LIMIT 100
        """,
        *params,
    )
    return [dict(r) for r in rows]


@router.get("/locales/{sucursal_sk}/promos")
async def get_store_promos(sucursal_sk: int):
    fecha = await fetchrow(f"SELECT {LATEST_PUBLISHED_FECHA_ID} AS fecha_id")
    if not fecha or fecha["fecha_id"] is None:
        return []

    rows = await fetch(
        f"""
        SELECT
            p.productos_descripcion AS producto,
            p.productos_marca       AS marca,
            f.precio_lista,
            {PRICE_DATE_SQL} AS fecha_precio,
            {PROMO_PRICE}  AS precio_promo,
            {PROMO_LABEL}  AS leyenda_promo,
            {PROMO_CONDITION} AS condicion,
            ROUND(
                (f.precio_lista - {PROMO_PRICE})
                / NULLIF(f.precio_lista, 0) * 100, 1
            ) AS descuento_pct
        FROM fact_precios f
        JOIN v_producto p ON f.producto_sk = p.producto_sk
        {PROMO_JOINS}
        WHERE f.sucursal_sk = $1
          AND f.fecha_id = $2
          AND f.tiene_promo = TRUE
          AND {ANY_SLOT} IS NOT NULL
        ORDER BY descuento_pct DESC
        LIMIT 10
        """,
        sucursal_sk,
        fecha["fecha_id"],
    )
    return [dict(r) for r in rows]
