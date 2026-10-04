"""Buscador endpoints — find cheapest store for a product."""

from fastapi import APIRouter, Depends, Query

from api.db import fetch
from api.location import Location, optional_location
from api.price_date import PRICE_DATE_SQL
from api.pricing import OFFER_PRICE_COLUMNS, PROMO_JOINS, STORE_PRICE
from api.store_filters import store_conditions
from api.unit_price import with_unit_price

router = APIRouter()


@router.get("/buscador/dates")
async def get_product_dates(producto_sk: int = Query(...)):
    rows = await fetch(
        """
        SELECT DISTINCT f.fecha_id, fe.fecha
        FROM fact_precios f
        JOIN dim_fecha fe ON f.fecha_id = fe.fecha_id
        WHERE f.producto_sk = $1
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
    location: Location | None = Depends(optional_location),
):
    conditions = ["f.producto_sk = $1", "f.fecha_id = $2"]
    params: list = [producto_sk, fecha_id]
    conditions += store_conditions(params, provincia, localidad, cadenas, location)
    where = " AND ".join(conditions)

    rows = await fetch(
        f"""
        SELECT
            c.comercio_bandera_nombre AS cadena,
            s.sucursales_tipo         AS tipo,
            s.sucursales_localidad    AS localidad,
            pr.nombre                 AS provincia,
            f.precio_lista,
            {PRICE_DATE_SQL}          AS fecha_precio,
            f.precio_lista - {STORE_PRICE} AS ahorro,
            {OFFER_PRICE_COLUMNS},
            s.sucursales_latitud      AS lat,
            s.sucursales_longitud     AS lon,
            s.sucursales_nombre       AS sucursal,
            NULLIF(CONCAT_WS(' ', s.sucursales_calle, s.sucursales_numero), '') AS direccion,
            s.id_comercio,
            s.id_bandera,
            s.id_sucursal,
            p.contenido,
            p.contenido_unidad
        FROM fact_precios f
        JOIN v_producto    p  ON f.producto_sk  = p.producto_sk
        JOIN dim_sucursal  s  ON f.sucursal_sk  = s.sucursal_sk
        JOIN dim_comercio  c  ON s.comercio_sk  = c.comercio_sk
        JOIN dim_provincia pr ON s.provincia_sk  = pr.provincia_sk
        {PROMO_JOINS}
        WHERE {where}
        ORDER BY mejor_precio ASC
        LIMIT 200
        """,
        *params,
    )
    return [with_unit_price(r) for r in rows]


# ---------------------------------------------------------------------------
# Generic search — search by text across all matching products
# ---------------------------------------------------------------------------


@router.get("/buscador/generic/dates")
async def get_generic_dates(q: str = Query(..., min_length=2)):
    rows = await fetch(
        """
        SELECT DISTINCT f.fecha_id, fe.fecha
        FROM fact_precios f
        JOIN v_producto p ON f.producto_sk = p.producto_sk
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
    location: Location | None = Depends(optional_location),
):
    conditions = ["p.productos_descripcion ILIKE $1", "f.fecha_id = $2"]
    params: list = [f"%{q}%", fecha_id]
    conditions += store_conditions(params, provincia, localidad, cadenas, location)
    where = " AND ".join(conditions)

    rows = await fetch(
        f"""
        SELECT
            c.comercio_bandera_nombre AS cadena,
            p.producto_sk,
            p.productos_descripcion   AS producto,
            p.productos_marca         AS marca,
            s.sucursales_nombre       AS sucursal,
            s.sucursales_tipo         AS tipo,
            s.sucursales_localidad    AS localidad,
            pr.nombre                 AS provincia,
            f.precio_lista,
            {PRICE_DATE_SQL}          AS fecha_precio,
            {OFFER_PRICE_COLUMNS},
            s.sucursales_latitud      AS lat,
            s.sucursales_longitud     AS lon,
            p.contenido,
            p.contenido_unidad
        FROM fact_precios f
        JOIN v_producto  p  ON f.producto_sk = p.producto_sk
        JOIN dim_sucursal  s  ON f.sucursal_sk = s.sucursal_sk
        JOIN dim_comercio  c  ON s.comercio_sk = c.comercio_sk
        JOIN dim_provincia pr ON s.provincia_sk = pr.provincia_sk
        {PROMO_JOINS}
        WHERE {where}
        ORDER BY c.comercio_bandera_nombre, mejor_precio ASC
        LIMIT 1000
        """,
        *params,
    )
    return [with_unit_price(r) for r in rows]
