-- 008: Indexes to support consumer dashboard queries
-- Buscador: product + date + sucursal covering index
CREATE INDEX IF NOT EXISTS idx_fact_precios_producto_fecha_sucursal
ON fact_precios (producto_sk, fecha_id, sucursal_sk);

-- Ofertas: promo rows on a given date (partial index)
CREATE INDEX IF NOT EXISTS idx_fact_precios_fecha_promo
ON fact_precios (fecha_id, tiene_promo) WHERE tiene_promo = TRUE;

-- Locality-based filtering
CREATE INDEX IF NOT EXISTS idx_dim_sucursal_provincia_localidad
ON dim_sucursal (provincia_sk, sucursales_localidad);
