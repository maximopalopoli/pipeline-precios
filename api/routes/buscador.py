"""Buscador endpoints — find cheapest store for a product."""

from fastapi import APIRouter, Query

from api.db import fetch

router = APIRouter()

BEST_PRICE = (
    "COALESCE(LEAST(NULLIF(f.precio_promo1, 0), "
    "NULLIF(f.precio_promo2, 0)), f.precio_lista)"
)


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
    return [dict(r) for r in rows]
