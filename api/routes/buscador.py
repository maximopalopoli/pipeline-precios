"""Buscador endpoints — find cheapest store for a product."""

from fastapi import APIRouter, Query

from api.db import fetch
from api.pricing import effective_price_sql

router = APIRouter()

BEST_PRICE = effective_price_sql()


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    d_lat = (lat2 - lat1) * 111.0
    d_lon = (lon2 - lon1) * 85.0
    return (d_lat * d_lat + d_lon * d_lon) ** 0.5


@router.get("/buscador/dates")
async def get_product_dates(producto_sk: int = Query(...)):
    rows = await fetch(
        """
        SELECT DISTINCT a.fecha_id, fe.fecha
        FROM agg_precios_diarios a
        JOIN dim_fecha fe ON a.fecha_id = fe.fecha_id
        WHERE a.producto_sk = $1
        ORDER BY fe.fecha DESC
        """,
        producto_sk,
    )
    return [
        {"fecha_id": r["fecha_id"], "fecha": r["fecha"].isoformat()}
        for r in rows
    ]


@router.get("/buscador")
async def get_buscador(
    producto_sk: int = Query(...),
    fecha_id: int = Query(...),
    provincia: str | None = Query(None),
    localidad: str | None = Query(None),
    cadenas: list[str] | None = Query(None),
    lat: float | None = Query(None),
    lon: float | None = Query(None),
    radius: float = Query(1, ge=0.5, le=50),
):
    conditions = ["f.producto_sk = $1", "f.fecha_id = $2"]
    params: list = [producto_sk, fecha_id]
    idx = 3

    if provincia:
        conditions.append(f"pr.nombre = ${idx}")
        params.append(provincia)
        idx += 1

    if localidad:
        conditions.append(f"s.sucursales_localidad = ${idx}")
        params.append(localidad)
        idx += 1

    if cadenas:
        placeholders = ", ".join(f"${idx + i}" for i in range(len(cadenas)))
        conditions.append(f"c.comercio_bandera_nombre IN ({placeholders})")
        params.extend(cadenas)
        idx += len(cadenas)

    has_location = lat is not None and lon is not None
    if has_location:
        lat_range = radius / 111.0
        lon_range = radius / 85.0
        conditions.append(f"s.sucursales_latitud BETWEEN ${idx} AND ${idx + 1}")
        params.extend([lat - lat_range, lat + lat_range])
        idx += 2
        conditions.append(f"s.sucursales_longitud BETWEEN ${idx} AND ${idx + 1}")
        params.extend([lon - lon_range, lon + lon_range])
        idx += 2

    where = " AND ".join(conditions)

    rows = await fetch(
        f"""
        SELECT
            c.comercio_bandera_nombre AS cadena,
            s.sucursales_tipo         AS tipo,
            s.sucursales_localidad    AS localidad,
            pr.nombre                 AS provincia,
            f.precio_lista,
            {BEST_PRICE}              AS mejor_precio,
            f.precio_lista - {BEST_PRICE} AS ahorro,
            s.sucursales_latitud      AS lat,
            s.sucursales_longitud     AS lon,
            s.sucursales_nombre       AS sucursal
        FROM fact_precios f
        JOIN dim_sucursal  s  ON f.sucursal_sk  = s.sucursal_sk
        JOIN dim_comercio  c  ON s.comercio_sk  = c.comercio_sk
        JOIN dim_provincia pr ON s.provincia_sk  = pr.provincia_sk
        WHERE {where}
        ORDER BY mejor_precio ASC
        LIMIT 200
        """,
        *params,
    )
    results = [dict(r) for r in rows]
    if has_location:
        results = [
            r for r in results
            if r["lat"] is not None and r["lon"] is not None
            and _distance_km(lat, lon, float(r["lat"]), float(r["lon"])) <= radius
        ]
    return results


# ---------------------------------------------------------------------------
# Generic search — search by text across all matching products
# ---------------------------------------------------------------------------


@router.get("/buscador/generic/dates")
async def get_generic_dates(q: str = Query(..., min_length=2)):
    rows = await fetch(
        """
        SELECT DISTINCT f.fecha_id, fe.fecha
        FROM fact_precios f
        JOIN dim_producto p ON f.producto_sk = p.producto_sk
        JOIN dim_fecha fe   ON f.fecha_id    = fe.fecha_id
        WHERE p.productos_descripcion ILIKE $1
        ORDER BY fe.fecha DESC
        LIMIT 30
        """,
        f"%{q}%",
    )
    return [
        {"fecha_id": r["fecha_id"], "fecha": r["fecha"].isoformat()}
        for r in rows
    ]


@router.get("/buscador/generic")
async def get_buscador_generic(
    q: str = Query(..., min_length=2),
    fecha_id: int = Query(...),
    provincia: str | None = Query(None),
    localidad: str | None = Query(None),
    cadenas: list[str] | None = Query(None),
    lat: float | None = Query(None),
    lon: float | None = Query(None),
    radius: float = Query(1, ge=0.5, le=50),
):
    conditions = ["p.productos_descripcion ILIKE $1", "f.fecha_id = $2"]
    params: list = [f"%{q}%", fecha_id]
    idx = 3

    if provincia:
        conditions.append(f"pr.nombre = ${idx}")
        params.append(provincia)
        idx += 1

    if localidad:
        conditions.append(f"s.sucursales_localidad = ${idx}")
        params.append(localidad)
        idx += 1

    if cadenas:
        placeholders = ", ".join(f"${idx + i}" for i in range(len(cadenas)))
        conditions.append(f"c.comercio_bandera_nombre IN ({placeholders})")
        params.extend(cadenas)
        idx += len(cadenas)

    has_location = lat is not None and lon is not None
    if has_location:
        lat_range = radius / 111.0
        lon_range = radius / 85.0
        conditions.append(f"s.sucursales_latitud BETWEEN ${idx} AND ${idx + 1}")
        params.extend([lat - lat_range, lat + lat_range])
        idx += 2
        conditions.append(f"s.sucursales_longitud BETWEEN ${idx} AND ${idx + 1}")
        params.extend([lon - lon_range, lon + lon_range])
        idx += 2

    where = " AND ".join(conditions)

    rows = await fetch(
        f"""
        SELECT
            c.comercio_bandera_nombre AS cadena,
            p.productos_descripcion   AS producto,
            p.productos_marca         AS marca,
            s.sucursales_nombre       AS sucursal,
            s.sucursales_tipo         AS tipo,
            s.sucursales_localidad    AS localidad,
            pr.nombre                 AS provincia,
            f.precio_lista,
            {BEST_PRICE}              AS mejor_precio,
            s.sucursales_latitud      AS lat,
            s.sucursales_longitud     AS lon
        FROM fact_precios f
        JOIN dim_producto  p  ON f.producto_sk = p.producto_sk
        JOIN dim_sucursal  s  ON f.sucursal_sk = s.sucursal_sk
        JOIN dim_comercio  c  ON s.comercio_sk = c.comercio_sk
        JOIN dim_provincia pr ON s.provincia_sk = pr.provincia_sk
        WHERE {where}
        ORDER BY c.comercio_bandera_nombre, mejor_precio ASC
        LIMIT 1000
        """,
        *params,
    )
    results = [dict(r) for r in rows]
    if has_location:
        results = [
            r for r in results
            if r["lat"] is not None and r["lon"] is not None
            and _distance_km(lat, lon, float(r["lat"]), float(r["lon"])) <= radius
        ]
    return results
