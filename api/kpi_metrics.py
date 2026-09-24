"""Queries and definitions for aggregate KPI calculations."""

WEEKLY_VARIATION_DAYS = 7

# This is a price-change indicator, not an official inflation measurement.
# It compares the same product × province series at the two exact endpoints of
# the latest available seven-day window.  Series missing either endpoint are
# excluded, rather than averaging unrelated daily changes or shorter windows.
WEEKLY_VARIATION_SQL = """
    WITH latest AS (
        SELECT calendar.fecha_id, calendar.fecha
        FROM dim_fecha AS calendar
        WHERE calendar.fecha_id = (SELECT MAX(fecha_id) FROM agg_precios_diarios)
    ), endpoint_prices AS (
        SELECT
            final.precio_lista_avg AS precio_final,
            initial.precio_lista_avg AS precio_inicial
        FROM latest
        JOIN dim_fecha AS initial_calendar
          ON initial_calendar.fecha = latest.fecha - 7
        JOIN agg_precios_diarios AS final
          ON final.fecha_id = latest.fecha_id
        JOIN agg_precios_diarios AS initial
          ON initial.producto_sk = final.producto_sk
         AND initial.provincia_sk = final.provincia_sk
         AND initial.fecha_id = initial_calendar.fecha_id
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
