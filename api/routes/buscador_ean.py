"""EAN-only product search — compare the exact same product across chains."""

from fastapi import APIRouter, Depends, Query

from api.db import fetch
from api.location import Location, optional_location
from api.price_date import PRICE_DATE_SQL
from api.pricing import OFFER_PRICE_COLUMNS, PROMO_JOINS
from api.store_filters import store_conditions

router = APIRouter()


@router.get("/buscador/ean/dates")
async def get_ean_dates(q: str = Query(..., min_length=2)):
    rows = await fetch(
        """
        SELECT DISTINCT f.fecha_id, fe.fecha
        FROM fact_precios f
        JOIN v_producto p ON f.producto_sk = p.producto_sk
        JOIN dim_fecha fe   ON f.fecha_id    = fe.fecha_id
        WHERE p.es_ean = true
          AND p.productos_descripcion ILIKE $1
        ORDER BY fe.fecha DESC
        LIMIT 30
        """,
        f"%{q}%",
    )
    return [
        {"fecha_id": r["fecha_id"], "fecha": r["fecha"].isoformat()}
        for r in rows
    ]


@router.get("/buscador/ean")
async def get_buscador_ean(
    q: str = Query(..., min_length=2),
    fecha_id: int = Query(...),
    provincia: str | None = Query(None),
    localidad: str | None = Query(None),
    cadenas: list[str] | None = Query(None),
    location: Location | None = Depends(optional_location),
):
    conditions = [
        "p.es_ean = true",
        "p.productos_descripcion ILIKE $1",
        "f.fecha_id = $2",
    ]
    params: list = [f"%{q}%", fecha_id]
    conditions += store_conditions(params, provincia, localidad, cadenas, location)
    where = " AND ".join(conditions)

    rows = await fetch(
        f"""
        SELECT
            p.producto_sk,
            p.id_producto,
            p.productos_descripcion   AS producto,
            p.productos_marca         AS marca,
            p.productos_cantidad_presentacion  AS cantidad,
            p.productos_unidad_medida_presentacion AS unidad,
            c.comercio_bandera_nombre AS cadena,
            s.sucursal_sk,
            s.sucursales_nombre       AS sucursal,
            s.sucursales_tipo         AS tipo,
            s.sucursales_calle        AS calle,
            s.sucursales_numero       AS numero,
            s.sucursales_localidad    AS localidad,
            pr.nombre                 AS provincia,
            f.precio_lista,
            {PRICE_DATE_SQL}          AS fecha_precio,
            {OFFER_PRICE_COLUMNS},
            s.sucursales_latitud      AS lat,
            s.sucursales_longitud     AS lon
        FROM fact_precios f
        JOIN v_producto  p  ON f.producto_sk = p.producto_sk
        JOIN dim_sucursal  s  ON f.sucursal_sk = s.sucursal_sk
        JOIN dim_comercio  c  ON s.comercio_sk = c.comercio_sk
        JOIN dim_provincia pr ON s.provincia_sk = pr.provincia_sk
        {PROMO_JOINS}
        WHERE {where}
        ORDER BY p.productos_descripcion, mejor_precio ASC
        LIMIT 1000
        """,
        *params,
    )
    return [dict(r) for r in rows]
