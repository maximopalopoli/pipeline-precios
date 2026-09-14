-- Separate retailer-local product codes while retaining validated EANs as
-- global identities.  Existing retailer-local facts are reassigned before
-- aggregates are rebuilt from their corrected product keys.

ALTER TABLE dim_producto
    ADD COLUMN producto_identity TEXT,
    ADD COLUMN source_id_comercio INT,
    ADD COLUMN source_id_bandera INT;

ALTER TABLE dim_producto
    DROP CONSTRAINT dim_producto_id_producto_key;

CREATE FUNCTION pipeline_is_valid_ean(value TEXT)
RETURNS BOOLEAN
LANGUAGE plpgsql
IMMUTABLE
AS $$
DECLARE
    code TEXT := BTRIM(value);
    digit_sum INT := 0;
    position INT;
BEGIN
    IF code !~ '^[0-9]{8}$' AND code !~ '^[0-9]{13}$' THEN
        RETURN FALSE;
    END IF;

    FOR position IN REVERSE LENGTH(code) - 1 .. 1 LOOP
        digit_sum := digit_sum + SUBSTRING(code FROM position FOR 1)::INT
            * CASE WHEN (LENGTH(code) - position) % 2 = 1 THEN 3 ELSE 1 END;
    END LOOP;

    RETURN (10 - digit_sum % 10) % 10 = RIGHT(code, 1)::INT;
END;
$$;

UPDATE dim_producto
SET producto_identity = CASE
        WHEN es_ean AND pipeline_is_valid_ean(id_producto) THEN 'ean:' || id_producto
        ELSE 'legacy:' || producto_sk
    END;

CREATE TEMP TABLE product_retailer_identity ON COMMIT DROP AS
SELECT DISTINCT
    p.producto_sk AS old_producto_sk,
    c.id_comercio,
    c.id_bandera,
    'chain:' || c.id_comercio || ':' || c.id_bandera || ':' || p.id_producto
        AS producto_identity
FROM dim_producto p
JOIN fact_precios f ON f.producto_sk = p.producto_sk
JOIN dim_sucursal s ON s.sucursal_sk = f.sucursal_sk
JOIN dim_comercio c ON c.comercio_sk = s.comercio_sk
WHERE NOT (p.es_ean AND pipeline_is_valid_ean(p.id_producto));

INSERT INTO dim_producto (
    producto_identity,
    source_id_comercio,
    source_id_bandera,
    id_producto,
    es_ean,
    productos_descripcion,
    productos_marca,
    productos_cantidad_presentacion,
    productos_unidad_medida_presentacion,
    productos_cantidad_referencia,
    productos_unidad_medida_referencia,
    updated_at
)
SELECT
    r.producto_identity,
    r.id_comercio,
    r.id_bandera,
    p.id_producto,
    p.es_ean,
    p.productos_descripcion,
    p.productos_marca,
    p.productos_cantidad_presentacion,
    p.productos_unidad_medida_presentacion,
    p.productos_cantidad_referencia,
    p.productos_unidad_medida_referencia,
    p.updated_at
FROM product_retailer_identity r
JOIN dim_producto p ON p.producto_sk = r.old_producto_sk;

UPDATE fact_precios f
SET producto_sk = replacement.producto_sk
FROM dim_sucursal s
JOIN dim_comercio c ON c.comercio_sk = s.comercio_sk
JOIN product_retailer_identity r
    ON r.id_comercio = c.id_comercio AND r.id_bandera = c.id_bandera
JOIN dim_producto replacement ON replacement.producto_identity = r.producto_identity
WHERE f.sucursal_sk = s.sucursal_sk
  AND f.producto_sk = r.old_producto_sk;

UPDATE pending_fact_precios f
SET producto_sk = replacement.producto_sk
FROM dim_sucursal s
JOIN dim_comercio c ON c.comercio_sk = s.comercio_sk
JOIN product_retailer_identity r
    ON r.id_comercio = c.id_comercio AND r.id_bandera = c.id_bandera
JOIN dim_producto replacement ON replacement.producto_identity = r.producto_identity
WHERE f.sucursal_sk = s.sucursal_sk
  AND f.producto_sk = r.old_producto_sk;

DELETE FROM agg_precios_diarios;

INSERT INTO agg_precios_diarios (
    producto_sk,
    fecha_id,
    provincia_sk,
    precio_lista_avg,
    precio_lista_min,
    precio_lista_max,
    precio_lista_median,
    precio_promo_avg,
    cantidad_obs,
    cantidad_con_promo,
    updated_at
)
SELECT
    f.producto_sk,
    f.fecha_id,
    s.provincia_sk,
    ROUND(AVG(f.precio_lista), 2),
    MIN(f.precio_lista),
    MAX(f.precio_lista),
    ROUND(percentile_cont(0.5) WITHIN GROUP (ORDER BY f.precio_lista)::NUMERIC, 2),
    ROUND(AVG(LEAST(
        COALESCE(f.precio_promo1, f.precio_lista),
        COALESCE(f.precio_promo2, f.precio_lista)
    )), 2),
    COUNT(*),
    COUNT(*) FILTER (WHERE f.tiene_promo),
    NOW()
FROM fact_precios f
JOIN dim_sucursal s ON s.sucursal_sk = f.sucursal_sk
WHERE s.provincia_sk IS NOT NULL
GROUP BY f.producto_sk, f.fecha_id, s.provincia_sk;

ALTER TABLE dim_producto
    ALTER COLUMN producto_identity SET NOT NULL;

CREATE UNIQUE INDEX idx_dim_producto_identity
    ON dim_producto (producto_identity);
