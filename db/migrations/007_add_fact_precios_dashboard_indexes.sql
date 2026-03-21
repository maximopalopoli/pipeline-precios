-- Index to support the dashboard's most common filter pattern: product + date.
CREATE INDEX IF NOT EXISTS idx_fact_precios_producto_fecha
ON fact_precios (producto_sk, fecha_id);
