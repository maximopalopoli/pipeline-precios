"""Nearby stores and per-store promotions endpoints."""

from functools import lru_cache

from fastapi import APIRouter, Query
from geopy.exc import GeocoderTimedOut, GeocoderUnavailable
from geopy.geocoders import Nominatim

from api.db import fetch, fetchrow
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


@router.get("/locales")
async def get_locales(
    lat: float = Query(...),
    lon: float = Query(...),
    radius: float = Query(5, ge=1, le=50),
    cadenas: list[str] | None = Query(None),
):
    lat_range = radius / 111.0
    lon_range = radius / 85.0

    conditions = [
        STORE_HAS_PUBLISHED_PRICE,
        "s.sucursales_latitud IS NOT NULL",
        "s.sucursales_longitud IS NOT NULL",
        "s.sucursales_latitud BETWEEN $1 AND $2",
        "s.sucursales_longitud BETWEEN $3 AND $4",
    ]
    params: list = [
        lat - lat_range,
        lat + lat_range,
        lon - lon_range,
        lon + lon_range,
    ]
    idx = 5

    if cadenas:
        placeholders = ", ".join(f"${idx + i}" for i in range(len(cadenas)))
        conditions.append(f"c.comercio_bandera_nombre IN ({placeholders})")
        params.extend(cadenas)
        idx += len(cadenas)

    where = " AND ".join(conditions)

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
            ROUND(
                SQRT(
                    POW((s.sucursales_latitud - {lat}) * 111.0, 2) +
                    POW((s.sucursales_longitud - {lon}) * 85.0, 2)
                )::numeric, 1
            ) AS distancia_km
        FROM dim_sucursal s
        JOIN dim_comercio  c  ON s.comercio_sk  = c.comercio_sk
        JOIN dim_provincia pr ON s.provincia_sk  = pr.provincia_sk
        WHERE {where}
        ORDER BY distancia_km ASC
        LIMIT 100
        """,
        *params,
    )

    # Post-filter by actual distance
    results = [dict(r) for r in rows if float(r["distancia_km"]) <= radius]
    return results


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
        JOIN dim_producto p ON f.producto_sk = p.producto_sk
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
