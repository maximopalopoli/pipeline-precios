-- 009_create_pending_fact_precios.sql
-- Facts loaded for an ingestion run before they are atomically published.

CREATE TABLE IF NOT EXISTS pending_fact_precios (
    run_id              INT           NOT NULL REFERENCES ingestion_runs (id),
    producto_sk         INT           NOT NULL REFERENCES dim_producto  (producto_sk),
    sucursal_sk         INT           NOT NULL REFERENCES dim_sucursal  (sucursal_sk),
    fecha_id            INT           NOT NULL REFERENCES dim_fecha     (fecha_id),

    precio_lista        NUMERIC(12, 2),
    precio_referencia   NUMERIC(12, 2),
    precio_promo1       NUMERIC(12, 2),
    precio_promo2       NUMERIC(12, 2),
    leyenda_promo1      TEXT,
    leyenda_promo2      TEXT,
    tiene_promo         BOOLEAN       NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ   NOT NULL DEFAULT NOW(),

    PRIMARY KEY (run_id, producto_sk, sucursal_sk, fecha_id)
);

CREATE INDEX IF NOT EXISTS idx_pending_fact_precios_run_fecha
    ON pending_fact_precios (run_id, fecha_id);
