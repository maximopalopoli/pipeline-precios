"""Daily offers endpoint."""

from fastapi import APIRouter, Query

from api.db import fetch, fetchrow
from api.price_date import PRICE_DATE_SQL
from api.pricing import (
    ANY_SLOT,
    PROMO_JOINS,
    promo_condition_sql,
    promo_label_sql,
    promo_price_sql,
)
from api.published_stores import LATEST_PUBLISHED_FECHA_ID

router = APIRouter()

# Every promotion in force, conditional or not, labelled with its requirement.
PROMO_PRICE = promo_price_sql(ANY_SLOT)
PROMO_LABEL = promo_label_sql(ANY_SLOT)
PROMO_CONDITION = promo_condition_sql(ANY_SLOT)


@router.get("/ofertas")
async def get_ofertas(
    provincia: str | None = Query(None),
    localidad: str | None = Query(None),
    cadenas: list[str] | None = Query(None),
):
    latest = await fetchrow(f"""
        SELECT fe.fecha AS max_fecha
        FROM dim_fecha fe
        WHERE fe.fecha_id = {LATEST_PUBLISHED_FECHA_ID}
    """)
    if not latest or latest["max_fecha"] is None:
        return {"fecha": None, "items": []}

    fecha = latest["max_fecha"]
    conditions = ["fe.fecha = $1", "f.tiene_promo = TRUE", f"{ANY_SLOT} IS NOT NULL"]
    params: list = [fecha]
    idx = 2

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

    where = " AND ".join(conditions)

    rows = await fetch(
        f"""
        SELECT
            p.productos_descripcion AS producto,
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
            )::numeric, 1) AS descuento_pct
        FROM fact_precios f
        JOIN v_producto  p  ON f.producto_sk  = p.producto_sk
        JOIN dim_fecha     fe ON f.fecha_id     = fe.fecha_id
        JOIN dim_sucursal  s  ON f.sucursal_sk  = s.sucursal_sk
        JOIN dim_comercio  c  ON s.comercio_sk  = c.comercio_sk
        JOIN dim_provincia pr ON s.provincia_sk  = pr.provincia_sk
        {PROMO_JOINS}
        WHERE {where}
        GROUP BY p.productos_descripcion, p.productos_marca,
                 c.comercio_bandera_nombre, {PROMO_LABEL}, {PROMO_CONDITION},
                 {PRICE_DATE_SQL}
        ORDER BY descuento_pct DESC
        LIMIT 50
        """,
        *params,
    )

    return {
        "fecha": fecha.isoformat(),
        "items": [dict(r) for r in rows],
    }
