"""Queries and definitions for aggregate KPI calculations."""

WEEKLY_VARIATION_DAYS = 7

# This is a price-change indicator, not an official inflation measurement.
# It compares the same product × province series at the two exact endpoints of
# the latest available seven-day window.  Series missing either endpoint are
# excluded, rather than averaging unrelated daily changes or shorter windows.
WEEKLY_VARIATION_SQL = """
    WITH latest AS (
        SELECT MAX(fecha) AS fecha
        FROM agg_precios_diarios
        JOIN dim_fecha USING (fecha_id)
    ), endpoint_prices AS (
        SELECT
            aggregate.producto_sk,
            aggregate.provincia_sk,
            MAX(aggregate.precio_lista_avg) FILTER (
                WHERE calendar.fecha = latest.fecha - 7
            ) AS precio_inicial,
            MAX(aggregate.precio_lista_avg) FILTER (
                WHERE calendar.fecha = latest.fecha
            ) AS precio_final
        FROM agg_precios_diarios AS aggregate
        JOIN dim_fecha AS calendar USING (fecha_id)
        CROSS JOIN latest
        WHERE calendar.fecha IN (latest.fecha - 7, latest.fecha)
        GROUP BY aggregate.producto_sk, aggregate.provincia_sk
        HAVING COUNT(*) FILTER (WHERE calendar.fecha = latest.fecha - 7) = 1
           AND COUNT(*) FILTER (WHERE calendar.fecha = latest.fecha) = 1
    ), weekly_variation AS (
        SELECT
            ROUND(
                AVG(100.0 * (precio_final - precio_inicial) / NULLIF(precio_inicial, 0)),
                2
            ) AS variacion_semanal,
            COUNT(*) AS series_comparadas
        FROM endpoint_prices
    )
    SELECT
        weekly_variation.variacion_semanal,
        weekly_variation.series_comparadas,
        latest.fecha - 7 AS fecha_inicio,
        latest.fecha AS fecha_fin
    FROM weekly_variation
    CROSS JOIN latest
"""
