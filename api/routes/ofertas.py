"""Daily offers endpoint."""

from fastapi import APIRouter, Depends, Query

from api.db import fetch, fetchrow
from api.location import Location, optional_location
from api.price_date import PRICE_DATE_SQL
from api.pricing import (
    ANY_SLOT,
    PROMO_JOINS,
    promo_condition_sql,
    promo_label_sql,
    promo_price_sql,
)
from api.published_stores import LATEST_PUBLISHED_FECHA_ID
from api.store_filters import store_conditions

router = APIRouter()

# Every promotion in force, conditional or not, labelled with its requirement.
PROMO_PRICE = promo_price_sql(ANY_SLOT)
PROMO_LABEL = promo_label_sql(ANY_SLOT)
PROMO_CONDITION = promo_condition_sql(ANY_SLOT)


# Offers are listed in pages; the summary covers them all.
MAX_OFFERS_PER_PAGE = 200
MAX_OFFER_OFFSET = 10000
SUMMARY_KEYS = ("total", "descuento_promedio", "descuento_maximo")


@router.get("/ofertas")
async def get_ofertas(
    provincia: str | None = Query(None),
    localidad: str | None = Query(None),
    cadenas: list[str] | None = Query(None),
    location: Location | None = Depends(optional_location),
    limit: int = Query(50, ge=1, le=MAX_OFFERS_PER_PAGE),
    offset: int = Query(0, ge=0, le=MAX_OFFER_OFFSET),
):
    """One page of the latest published day's promotions, largest discount first.

    An offer is a product's promotion in one chain: the stores of that chain
    with the same legend, requirement and price date are averaged and
    counted in ``sucursales``.  ``total``, ``descuento_promedio`` and
    ``descuento_maximo`` cover every offer the filters choose, not only the
    page, and ``hay_mas`` says whether another page follows.  Ties are
    broken by product, chain and legend, so pages neither repeat nor skip an
    offer.
    """
    empty = {
        "fecha": None, "items": [], "total": 0, "hay_mas": False,
        "descuento_promedio": None, "descuento_maximo": None,
    }
    latest = await fetchrow(f"""
        SELECT fe.fecha_id, fe.fecha
        FROM dim_fecha fe
        WHERE fe.fecha_id = {LATEST_PUBLISHED_FECHA_ID}
    """)
    if not latest or latest["fecha"] is None:
        return empty

    params: list = [latest["fecha_id"]]
    conditions = ["f.fecha_id = $1", "f.tiene_promo = TRUE", f"{ANY_SLOT} IS NOT NULL"]
    conditions += store_conditions(params, provincia, localidad, cadenas, location)
    where = " AND ".join(conditions)
    params += [limit, offset]
    limit_ref, offset_ref = f"${len(params) - 1}", f"${len(params)}"

    rows = await fetch(
        f"""
        WITH ofertas AS (
            SELECT
                p.producto_sk,
                p.nombre                AS producto,
                p.productos_descripcion AS descripcion,
                p.productos_marca       AS marca,
                c.comercio_bandera_nombre AS cadena,
                {PROMO_LABEL}  AS leyenda_promo,
                {PROMO_CONDITION} AS condicion,
                ROUND(AVG(f.precio_lista)::numeric, 2) AS precio_lista,
                {PRICE_DATE_SQL} AS fecha_precio,
                ROUND(AVG({PROMO_PRICE})::numeric, 2) AS precio_promo,
                ROUND(AVG(
                    (f.precio_lista - {PROMO_PRICE})
                    / NULLIF(f.precio_lista, 0) * 100
                )::numeric, 1) AS descuento_pct,
                COUNT(*) AS sucursales
            FROM fact_precios f
            JOIN v_producto    p  ON f.producto_sk  = p.producto_sk
            JOIN dim_sucursal  s  ON f.sucursal_sk  = s.sucursal_sk
            JOIN dim_comercio  c  ON s.comercio_sk  = c.comercio_sk
            JOIN dim_provincia pr ON s.provincia_sk  = pr.provincia_sk
            {PROMO_JOINS}
            WHERE {where}
            GROUP BY p.producto_sk, p.nombre, p.productos_descripcion, p.productos_marca,
                     c.comercio_bandera_nombre, {PROMO_LABEL}, {PROMO_CONDITION},
                     {PRICE_DATE_SQL}
        ),
        resumen AS (
            SELECT COUNT(*)                       AS total,
                   ROUND(AVG(descuento_pct), 1)   AS descuento_promedio,
                   MAX(descuento_pct)             AS descuento_maximo
            FROM ofertas
        )
        -- The summary row is kept when the page is empty.
        SELECT resumen.*, pagina.*
        FROM resumen
        LEFT JOIN LATERAL (
            SELECT * FROM ofertas
            ORDER BY descuento_pct DESC NULLS LAST, producto_sk, cadena,
                     leyenda_promo, condicion, fecha_precio
            LIMIT {limit_ref} OFFSET {offset_ref}
        ) pagina ON TRUE
        """,
        *params,
    )
    summary = {key: rows[0][key] for key in SUMMARY_KEYS}
    items = [
        {k: v for k, v in r.items() if k not in SUMMARY_KEYS}
        for r in rows
        if r["producto_sk"] is not None
    ]
    return {
        "fecha": latest["fecha"].isoformat(),
        "items": items,
        **summary,
        "hay_mas": offset + len(items) < summary["total"],
    }
