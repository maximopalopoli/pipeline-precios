"""Daily offers endpoint."""

from fastapi import APIRouter, Query

from api.db import fetch, fetchrow

router = APIRouter()

BEST_PRICE = (
    "COALESCE(LEAST(NULLIF(f.precio_promo1, 0), "
    "NULLIF(f.precio_promo2, 0)), f.precio_lista)"
)


@router.get("/ofertas")
async def get_ofertas(
    provincia: str | None = Query(None),
    localidad: str | None = Query(None),
    cadenas: list[str] | None = Query(None),
):
    # Get latest date
    latest = await fetchrow("""
        SELECT MAX(fecha) AS max_fecha
        FROM dim_fecha
        JOIN agg_precios_diarios USING (fecha_id)
    """)
    if not latest or latest["max_fecha"] is None:
        return {"fecha": None, "items": []}

    fecha = latest["max_fecha"]
    conditions = [f"fe.fecha = $1", "f.tiene_promo = TRUE"]
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
            ROUND(AVG(f.precio_lista)::numeric, 2) AS precio_lista,
            ROUND(AVG({BEST_PRICE})::numeric, 2) AS precio_promo,
            ROUND(AVG(
                (f.precio_lista - {BEST_PRICE})
                / NULLIF(f.precio_lista, 0) * 100
            )::numeric, 1) AS descuento_pct
        FROM fact_precios f
        JOIN dim_producto  p  ON f.producto_sk  = p.producto_sk
        JOIN dim_fecha     fe ON f.fecha_id     = fe.fecha_id
        JOIN dim_sucursal  s  ON f.sucursal_sk  = s.sucursal_sk
        JOIN dim_comercio  c  ON s.comercio_sk  = c.comercio_sk
        JOIN dim_provincia pr ON s.provincia_sk  = pr.provincia_sk
        WHERE {where}
        GROUP BY p.productos_descripcion, p.productos_marca,
                 c.comercio_bandera_nombre
        HAVING AVG(f.precio_lista - {BEST_PRICE}) > 0
        ORDER BY descuento_pct DESC
        LIMIT 50
        """,
        *params,
    )

    return {
        "fecha": fecha.isoformat(),
        "items": [dict(r) for r in rows],
    }
