-- 005_create_aggregations.sql
-- Daily aggregation table and price variation view

CREATE TABLE IF NOT EXISTS agg_precios_diarios (
    producto_sk             INT         NOT NULL REFERENCES dim_producto  (producto_sk),
    fecha_id                INT         NOT NULL REFERENCES dim_fecha     (fecha_id),
    provincia_sk            INT         NOT NULL REFERENCES dim_provincia (provincia_sk),

    -- list price
    precio_lista_avg        NUMERIC(12, 2),
    precio_lista_min        NUMERIC(12, 2),
    precio_lista_max        NUMERIC(12, 2),
    precio_lista_median     NUMERIC(12, 2),

    -- promo price (average of best available promo per store)
    precio_promo_avg        NUMERIC(12, 2),

    -- counts
    cantidad_obs            INT         NOT NULL DEFAULT 0,
    cantidad_con_promo      INT         NOT NULL DEFAULT 0,

    -- uniqueness per grain
    PRIMARY KEY (producto_sk, fecha_id, provincia_sk),

    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agg_fecha     ON agg_precios_diarios (fecha_id);
CREATE INDEX IF NOT EXISTS idx_agg_provincia ON agg_precios_diarios (provincia_sk);

-- ------------------------------------------------------------
-- View: list price variation compared to the previous day
-- per product × province
-- ------------------------------------------------------------

CREATE OR REPLACE VIEW v_variacion_precios AS
SELECT
    a.producto_sk,
    p.id_producto,
    p.productos_descripcion,
    p.productos_marca,
    a.fecha_id,
    f.fecha,
    a.provincia_sk,
    pr.nombre                                       AS provincia,
    a.precio_lista_avg,
    LAG(a.precio_lista_avg) OVER w                  AS precio_lista_avg_anterior,
    ROUND(
        (a.precio_lista_avg - LAG(a.precio_lista_avg) OVER w)
        / NULLIF(LAG(a.precio_lista_avg) OVER w, 0) * 100,
        2
    )                                               AS variacion_pct,
    a.precio_lista_min,
    a.precio_lista_max,
    a.precio_promo_avg,
    a.cantidad_obs,
    a.cantidad_con_promo
FROM agg_precios_diarios a
JOIN dim_producto  p  ON p.producto_sk  = a.producto_sk
JOIN dim_fecha     f  ON f.fecha_id     = a.fecha_id
JOIN dim_provincia pr ON pr.provincia_sk = a.provincia_sk
WINDOW w AS (
    PARTITION BY a.producto_sk, a.provincia_sk
    ORDER BY a.fecha_id
);
