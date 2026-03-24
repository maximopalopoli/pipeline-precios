-- 004_create_fact_precios.sql
-- Price fact table, partitioned by monthly range of fecha_id (YYYYMMDD)

CREATE TABLE IF NOT EXISTS fact_precios (
    -- surrogate keys
    producto_sk         INT         NOT NULL REFERENCES dim_producto  (producto_sk),
    sucursal_sk         INT         NOT NULL REFERENCES dim_sucursal  (sucursal_sk),
    fecha_id            INT         NOT NULL REFERENCES dim_fecha     (fecha_id),
    run_id              INT         NOT NULL REFERENCES ingestion_runs (id),

    -- prices
    precio_lista        NUMERIC(12, 2),
    precio_referencia   NUMERIC(12, 2),
    precio_promo1       NUMERIC(12, 2),
    precio_promo2       NUMERIC(12, 2),

    -- promo labels
    leyenda_promo1      TEXT,
    leyenda_promo2      TEXT,

    -- computed flag
    tiene_promo         BOOLEAN     NOT NULL DEFAULT FALSE,

    -- uniqueness per grain
    UNIQUE (producto_sk, sucursal_sk, fecha_id)

) PARTITION BY RANGE (fecha_id);

-- Indexes on the parent table (propagated to each partition)
CREATE INDEX IF NOT EXISTS idx_fact_precios_sucursal ON fact_precios (sucursal_sk);
CREATE INDEX IF NOT EXISTS idx_fact_precios_producto ON fact_precios (producto_sk);
CREATE INDEX IF NOT EXISTS idx_fact_precios_run      ON fact_precios (run_id);

-- ------------------------------------------------------------
-- Monthly partitions
-- Range: [first day of month as YYYYMMDD, first day of next month)
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS fact_precios_202601
    PARTITION OF fact_precios FOR VALUES FROM (20260101) TO (20260201);

CREATE TABLE IF NOT EXISTS fact_precios_202602
    PARTITION OF fact_precios FOR VALUES FROM (20260201) TO (20260301);

CREATE TABLE IF NOT EXISTS fact_precios_202603
    PARTITION OF fact_precios FOR VALUES FROM (20260301) TO (20260401);

CREATE TABLE IF NOT EXISTS fact_precios_202604
    PARTITION OF fact_precios FOR VALUES FROM (20260401) TO (20260501);

CREATE TABLE IF NOT EXISTS fact_precios_202605
    PARTITION OF fact_precios FOR VALUES FROM (20260501) TO (20260601);

CREATE TABLE IF NOT EXISTS fact_precios_202606
    PARTITION OF fact_precios FOR VALUES FROM (20260601) TO (20260701);

-- Default partition: captures data outside declared ranges
CREATE TABLE IF NOT EXISTS fact_precios_default
    PARTITION OF fact_precios DEFAULT;
