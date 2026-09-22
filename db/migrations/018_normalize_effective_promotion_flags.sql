-- ``tiene_promo`` represents an actionable discount, not merely a populated
-- source promotion field.  Preserve the raw source prices and labels while
-- making KPI and aggregate coverage match the customer-facing rule.

UPDATE fact_precios
SET tiene_promo = (
    (precio_promo1 > 0 AND precio_promo1 < precio_lista)
    OR (precio_promo2 > 0 AND precio_promo2 < precio_lista)
)
WHERE tiene_promo IS DISTINCT FROM (
    (precio_promo1 > 0 AND precio_promo1 < precio_lista)
    OR (precio_promo2 > 0 AND precio_promo2 < precio_lista)
);

WITH promotion_counts AS (
    SELECT
        f.producto_sk,
        f.fecha_id,
        s.provincia_sk,
        COUNT(*) FILTER (WHERE f.tiene_promo) AS cantidad_con_promo
    FROM fact_precios f
    JOIN dim_sucursal s ON s.sucursal_sk = f.sucursal_sk
    WHERE s.provincia_sk IS NOT NULL
    GROUP BY f.producto_sk, f.fecha_id, s.provincia_sk
)
UPDATE agg_precios_diarios daily_aggregate
SET cantidad_con_promo = promotion_counts.cantidad_con_promo,
    updated_at = NOW()
FROM promotion_counts
WHERE daily_aggregate.producto_sk = promotion_counts.producto_sk
  AND daily_aggregate.fecha_id = promotion_counts.fecha_id
  AND daily_aggregate.provincia_sk = promotion_counts.provincia_sk
  AND daily_aggregate.cantidad_con_promo IS DISTINCT FROM promotion_counts.cantidad_con_promo;
