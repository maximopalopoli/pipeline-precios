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


# A product's stores are listed in pages; AMBA has a few thousand stores.
MAX_STORES_PER_PAGE = 200
MAX_STORE_OFFSET = 10000
SUMMARY_KEYS = ("total", "precio_min", "precio_max", "precio_promedio")


@router.get("/buscador")
async def get_buscador(
    producto_sk: int = Query(...),
    fecha_id: int = Query(...),
    provincia: str | None = Query(None),
    localidad: str | None = Query(None),
    cadenas: list[str] | None = Query(None),
    location: Location | None = Depends(optional_location),
    limit: int = Query(50, ge=1, le=MAX_STORES_PER_PAGE),
    offset: int = Query(0, ge=0, le=MAX_STORE_OFFSET),
):
    """One page of a product's store prices, cheapest first.

    Returns ``{"sucursales": [...], "total", "hay_mas", "precio_min",
    "precio_max", "precio_promedio"}``.  The count and the price summary
    cover every store the filters choose, not only the page.  Ties are
    broken by store, so pages neither repeat nor skip one.
    """
    conditions = ["f.producto_sk = $1", "f.fecha_id = $2"]
    params: list = [producto_sk, fecha_id]
    conditions += store_conditions(params, provincia, localidad, cadenas, location)
    where = " AND ".join(conditions)
    params += [limit, offset]
    limit_ref, offset_ref = f"${len(params) - 1}", f"${len(params)}"

    rows = await fetch(
        f"""
        WITH ofertas AS (
            SELECT
                s.sucursal_sk,
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
        ),
        resumen AS (
            SELECT COUNT(*)                         AS total,
                   MIN(mejor_precio)                AS precio_min,
                   MAX(mejor_precio)                AS precio_max,
                   ROUND(AVG(mejor_precio), 2)      AS precio_promedio
            FROM ofertas
        )
        -- The summary row is kept when the page is empty.
        SELECT resumen.*, pagina.*
        FROM resumen
        LEFT JOIN LATERAL (
            SELECT * FROM ofertas
            ORDER BY mejor_precio NULLS LAST, sucursal_sk
            LIMIT {limit_ref} OFFSET {offset_ref}
        ) pagina ON TRUE
        """,
        *params,
    )
    summary = {key: rows[0][key] for key in SUMMARY_KEYS}
    page = [
        with_unit_price({k: v for k, v in r.items() if k not in SUMMARY_KEYS})
        for r in rows
        if r["sucursal_sk"] is not None
    ]
    return {
        "sucursales": page,
        **summary,
        "hay_mas": offset + len(page) < summary["total"],
    }
