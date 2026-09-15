-- Migration 016 changes the effective-price rule used for future aggregates.
-- Recalculate the existing materialized values so historical promo trends and
-- API responses do not remain based on the old rule until a date is reingested.

WITH recalculated AS (
    SELECT
        f.producto_sk,
        f.fecha_id,
        s.provincia_sk,
        ROUND(AVG(pipeline_effective_price(
            f.precio_lista, f.precio_promo1, f.precio_promo2
        )), 2) AS precio_promo_avg
    FROM fact_precios f
    JOIN dim_sucursal s ON s.sucursal_sk = f.sucursal_sk
    WHERE s.provincia_sk IS NOT NULL
    GROUP BY f.producto_sk, f.fecha_id, s.provincia_sk
)
UPDATE agg_precios_diarios daily_aggregate
SET precio_promo_avg = recalculated.precio_promo_avg,
    updated_at = NOW()
FROM recalculated
WHERE daily_aggregate.producto_sk = recalculated.producto_sk
  AND daily_aggregate.fecha_id = recalculated.fecha_id
  AND daily_aggregate.provincia_sk = recalculated.provincia_sk
  AND daily_aggregate.precio_promo_avg IS DISTINCT FROM recalculated.precio_promo_avg;
