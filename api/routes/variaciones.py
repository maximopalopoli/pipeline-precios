"""Price variations endpoint — top 20 products with biggest price changes."""

from fastapi import APIRouter, Query

from api.db import fetch, fetchrow
from api.pricing import effective_price_sql

router = APIRouter()
BEST_PRICE = effective_price_sql()


@router.get("/variaciones")
async def get_variaciones(
    mode: str = Query("lista", regex="^(lista|promo)$"),
    provincia: str | None = Query(None),
    cadenas: list[str] | None = Query(None),
):
    # Resolve date range
    date_range = await fetchrow("""
        SELECT MAX(f.fecha) AS max_fecha, MAX(f.fecha) - INTERVAL '7 days' AS min_fecha
        FROM dim_fecha f
        JOIN agg_precios_diarios a ON a.fecha_id = f.fecha_id
    """)
    if not date_range or date_range["max_fecha"] is None:
        return {"min_fecha": None, "max_fecha": None, "items": []}

    if mode == "lista":
        variation_col = "variacion_pct"
        price_col = "precio_lista_avg"
        prev_col = "precio_lista_avg_anterior"
    else:
        variation_col = "variacion_promo_pct"
        price_col = "precio_promo_avg"
        prev_col = "precio_promo_avg_anterior"

    if cadenas:
        # Chain-filtered query using fact_precios directly
        conditions = []
        params: list = []
        idx = 1

        if provincia:
            conditions.append(f"pr.nombre = ${idx}")
            params.append(provincia)
            idx += 1

        chain_placeholders = ", ".join(f"${idx + i}" for i in range(len(cadenas)))
        conditions.append(f"c.comercio_bandera_nombre IN ({chain_placeholders})")
        params.extend(cadenas)
        idx += len(cadenas)

        extra_where = (" AND " + " AND ".join(conditions)) if conditions else ""

        rows = await fetch(
            f"""
            WITH daily AS (
                SELECT
                    p.producto_sk,
                    p.id_producto,
                    p.productos_descripcion,
                    p.productos_marca,
                    fe.fecha_id,
                    fe.fecha,
                    pr.nombre AS provincia,
                    ROUND(AVG(f.precio_lista)::numeric, 2) AS precio_lista_avg,
                    ROUND(AVG({BEST_PRICE})::numeric, 2) AS precio_promo_avg
                FROM fact_precios f
                JOIN dim_producto  p  ON f.producto_sk  = p.producto_sk
                JOIN dim_fecha     fe ON f.fecha_id     = fe.fecha_id
                JOIN dim_sucursal  s  ON f.sucursal_sk  = s.sucursal_sk
                JOIN dim_comercio  c  ON s.comercio_sk  = c.comercio_sk
                JOIN dim_provincia pr ON s.provincia_sk  = pr.provincia_sk
                WHERE fe.fecha >= (
                        SELECT MAX(fecha) - INTERVAL '7 days'
                        FROM dim_fecha
                        JOIN agg_precios_diarios USING (fecha_id)
                      )
                  {extra_where}
                GROUP BY p.producto_sk, p.id_producto, p.productos_descripcion,
                         p.productos_marca, fe.fecha_id, fe.fecha, pr.nombre
            ),
            with_lag AS (
                SELECT *,
                    LAG(precio_lista_avg) OVER w AS precio_lista_avg_anterior,
                    LAG(precio_promo_avg) OVER w AS precio_promo_avg_anterior,
                    ROUND(
                        (precio_lista_avg - LAG(precio_lista_avg) OVER w)
                        / NULLIF(LAG(precio_lista_avg) OVER w, 0) * 100, 2
                    ) AS variacion_pct,
                    ROUND(
                        (precio_promo_avg - LAG(precio_promo_avg) OVER w)
                        / NULLIF(LAG(precio_promo_avg) OVER w, 0) * 100, 2
                    ) AS variacion_promo_pct
                FROM daily
                WINDOW w AS (PARTITION BY producto_sk, provincia ORDER BY fecha_id)
            )
            SELECT *
            FROM with_lag
            WHERE {variation_col} IS NOT NULL
            ORDER BY ABS({variation_col}) DESC
            LIMIT 20
            """,
            *params,
        )
    else:
        # Global query using the view
        params = []
        idx = 1
        prov_clause = ""

        if provincia:
            prov_clause = f"AND provincia = ${idx}"
            params.append(provincia)
            idx += 1

        if mode == "promo":
            rows = await fetch(
                f"""
                WITH base AS (
                    SELECT
                        producto_sk, id_producto, productos_descripcion,
                        productos_marca, fecha_id, fecha, provincia,
                        precio_promo_avg,
                        LAG(precio_promo_avg) OVER (
                            PARTITION BY producto_sk, provincia ORDER BY fecha_id
                        ) AS precio_promo_avg_anterior,
                        ROUND(
                            (precio_promo_avg - LAG(precio_promo_avg) OVER (
                                PARTITION BY producto_sk, provincia ORDER BY fecha_id
                            ))
                            / NULLIF(LAG(precio_promo_avg) OVER (
                                PARTITION BY producto_sk, provincia ORDER BY fecha_id
                            ), 0) * 100, 2
                        ) AS variacion_promo_pct
                    FROM v_variacion_precios
                    WHERE fecha >= (
                        SELECT MAX(fecha) FROM v_variacion_precios
                    ) - INTERVAL '7 days'
                    {prov_clause}
                )
                SELECT * FROM base
                WHERE variacion_promo_pct IS NOT NULL
                ORDER BY ABS(variacion_promo_pct) DESC
                LIMIT 20
                """,
                *params,
            )
        else:
            rows = await fetch(
                f"""
                SELECT *
                FROM v_variacion_precios
                WHERE variacion_pct IS NOT NULL
                  AND fecha >= (
                      SELECT MAX(fecha) FROM v_variacion_precios
                  ) - INTERVAL '7 days'
                  {prov_clause}
                ORDER BY ABS(variacion_pct) DESC
                LIMIT 20
                """,
                *params,
            )

    return {
        "min_fecha": date_range["min_fecha"].isoformat(),
        "max_fecha": date_range["max_fecha"].isoformat(),
        "items": [
            {
                "productos_descripcion": r.get("productos_descripcion"),
                "productos_marca": r.get("productos_marca"),
                "provincia": r.get("provincia"),
                "precio_anterior": float(r[prev_col]) if r.get(prev_col) else None,
                "precio_actual": float(r[price_col]) if r.get(price_col) else None,
                "variacion": float(r[variation_col]) if r.get(variation_col) else None,
            }
            for r in rows
        ],
    }
