"""GET /api/kpis — home page KPI metrics."""

from fastapi import APIRouter

from api.db import fetch, fetchrow
from api.kpi_metrics import WEEKLY_VARIATION_SQL

router = APIRouter()

WEEKLY_VARIATION_METHODOLOGY = (
    "Promedio simple de la variación de precio por producto y provincia entre "
    "dos extremos separados por siete días; excluye series sin ambos extremos."
)


def _weekly_variation_response(row: dict | None) -> dict:
    """Format the weekly indicator and its coverage for the API contract."""
    value = row["variacion_semanal"] if row else None
    start = row["fecha_inicio"] if row else None
    end = row["fecha_fin"] if row else None
    return {
        "valor": float(value) if value is not None else None,
        "fecha_inicio": start.isoformat() if start is not None else None,
        "fecha_fin": end.isoformat() if end is not None else None,
        "series_comparadas": int(row["series_comparadas"]) if row else 0,
        "metodologia": WEEKLY_VARIATION_METHODOLOGY,
    }


@router.get("/kpis")
async def get_kpis():
    # All queries run concurrently via gather would be ideal,
    # but sequential is fine for now given they're all fast aggregations.

    # 1. Average change between the exact weekly endpoints.
    weekly_variation = await fetchrow(WEEKLY_VARIATION_SQL)

    # 2. Total products
    products = await fetchrow("SELECT COUNT(*) AS n FROM dim_producto")

    # 3. Total stores
    stores = await fetchrow("SELECT COUNT(*) AS n FROM dim_sucursal")

    # 4. Promo coverage today
    promo = await fetchrow("""
        SELECT
            ROUND(100.0 * SUM(CASE WHEN f.tiene_promo THEN 1 ELSE 0 END)
                  / NULLIF(COUNT(*), 0), 1) AS pct_promo
        FROM fact_precios f
        JOIN dim_fecha fe ON f.fecha_id = fe.fecha_id
        WHERE fe.fecha = (
            SELECT MAX(fecha) FROM dim_fecha
            JOIN agg_precios_diarios USING (fecha_id)
        )
    """)

    # 5. Top increases
    top_subas = await fetch("""
        SELECT productos_descripcion AS producto, provincia,
               ROUND(variacion_pct::numeric, 1) AS variacion
        FROM v_variacion_precios
        WHERE variacion_pct IS NOT NULL
          AND fecha = (SELECT MAX(fecha) FROM v_variacion_precios)
        ORDER BY variacion_pct DESC
        LIMIT 5
    """)

    # 6. Top decreases
    top_bajas = await fetch("""
        SELECT productos_descripcion AS producto, provincia,
               ROUND(variacion_pct::numeric, 1) AS variacion
        FROM v_variacion_precios
        WHERE variacion_pct IS NOT NULL
          AND fecha = (SELECT MAX(fecha) FROM v_variacion_precios)
        ORDER BY variacion_pct ASC
        LIMIT 5
    """)

    # 7. Best promo chain
    best_chain = await fetchrow("""
        SELECT
            c.comercio_bandera_nombre AS cadena,
            ROUND(100.0 * SUM(CASE WHEN f.tiene_promo THEN 1 ELSE 0 END)
                  / NULLIF(COUNT(*), 0), 1) AS pct_promo
        FROM fact_precios f
        JOIN dim_fecha    fe ON f.fecha_id    = fe.fecha_id
        JOIN dim_sucursal s  ON f.sucursal_sk = s.sucursal_sk
        JOIN dim_comercio c  ON s.comercio_sk = c.comercio_sk
        WHERE fe.fecha = (
            SELECT MAX(fecha) FROM dim_fecha
            JOIN agg_precios_diarios USING (fecha_id)
        )
        GROUP BY c.comercio_bandera_nombre
        ORDER BY pct_promo DESC
        LIMIT 1
    """)

    weekly_indicator = _weekly_variation_response(weekly_variation)
    promo_percentage = promo["pct_promo"] if promo else None
    return {
        # Kept during the client migration; this is not official inflation.
        "inflacion_semanal": weekly_indicator["valor"],
        "variacion_semanal": weekly_indicator,
        "total_productos": products["n"] if products else 0,
        "total_sucursales": stores["n"] if stores else 0,
        "pct_promo": float(promo_percentage) if promo_percentage is not None else None,
        "top_subas": [dict(r) for r in top_subas],
        "top_bajas": [dict(r) for r in top_bajas],
        "best_chain": dict(best_chain) if best_chain else None,
    }
