-- Product variant from a curated term dictionary.
--
-- Two products of the same type, brand and size still differ by flavor, fat
-- content or recipe: "YOG FIRME VAI", "LECHE PARC DESC", "AGUA S/GAS POMELO".
-- A curated dictionary (db/seeds/seed_producto_variantes.sql, replaced on
-- every migration run) maps words and short phrases to a variant value.  A
-- description's variant is the sorted set of the values it names, at any
-- word, without the product's own type ("DULCE DE LECHE" is the type of a
-- dulce de leche, a variant of an alfajor).  The chains vote, as for the
-- other attributes (migrations 038 and 040).  A description with no known
-- term leaves the variant empty: it is never guessed.  A dictionary change
-- takes effect for the products of the next full ingestion.

CREATE TABLE producto_variante_terminos (
    -- One to three normalized words: "VAI", "PARC DESC", "S G".
    termino  TEXT PRIMARY KEY CHECK (
        termino <> ''
        AND termino = pipeline_normalize_text(termino)
        AND CARDINALITY(STRING_TO_ARRAY(termino, ' ')) <= 3
    ),
    variante TEXT NOT NULL CHECK (variante ~ '^[a-záéíóúñü]+( [a-záéíóúñü]+)*$')
);

-- The variant values a description names.  A term inside a longer matched
-- term does not count: "LIMA LIMON" is lima limón, not also limón.
CREATE FUNCTION pipeline_product_variant_matches(descripcion TEXT)
RETURNS TABLE (variante TEXT)
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    WITH coincidencias AS (
        SELECT t.variante, inicio, inicio + largo - 1 AS fin
        FROM (
            -- OFFSET 0 normalizes once per description (see migration 040).
            SELECT STRING_TO_ARRAY(pipeline_normalize_text(descripcion), ' ') AS palabras
            OFFSET 0
        ) AS texto
        CROSS JOIN GENERATE_SERIES(1, CARDINALITY(texto.palabras)) AS inicio
        CROSS JOIN GENERATE_SERIES(1, 3) AS largo
        JOIN producto_variante_terminos t
          ON t.termino = ARRAY_TO_STRING(texto.palabras[inicio:inicio + largo - 1], ' ')
        WHERE inicio + largo - 1 <= CARDINALITY(texto.palabras)
    )
    SELECT DISTINCT c.variante
    FROM coincidencias c
    WHERE NOT EXISTS (
        SELECT 1
        FROM coincidencias mayor
        WHERE mayor.inicio <= c.inicio
          AND mayor.fin >= c.fin
          AND mayor.fin - mayor.inicio > c.fin - c.inicio
    )
$$;

-- A description's variant for a product of the given type: its values in
-- alphabetical order, joined with ", ", or NULL when it names none.
CREATE FUNCTION pipeline_product_variant(descripcion TEXT, tipo TEXT) RETURNS TEXT
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    SELECT NULLIF(STRING_AGG(variante, ', ' ORDER BY variante), '')
    FROM pipeline_product_variant_matches(descripcion)
    WHERE variante IS DISTINCT FROM tipo
$$;

ALTER TABLE dim_producto ADD COLUMN variante TEXT;

ALTER TABLE producto_overrides
    ADD COLUMN variante TEXT CHECK (BTRIM(variante) <> ''),
    DROP CONSTRAINT producto_overrides_any_attribute,
    ADD CONSTRAINT producto_overrides_any_attribute CHECK (
        descripcion IS NOT NULL OR marca IS NOT NULL OR contenido IS NOT NULL
        OR tipo IS NOT NULL OR variante IS NOT NULL
    );

CREATE OR REPLACE VIEW v_producto AS
SELECT
    p.producto_sk,
    p.producto_identity,
    p.source_id_comercio,
    p.source_id_bandera,
    p.id_producto,
    p.es_ean,
    COALESCE(o.descripcion, p.productos_descripcion) AS productos_descripcion,
    COALESCE(o.marca, p.productos_marca) AS productos_marca,
    CASE
        WHEN o.contenido IS NOT NULL THEN TRIM_SCALE(o.contenido)::TEXT
        ELSE p.productos_cantidad_presentacion
    END AS productos_cantidad_presentacion,
    CASE
        WHEN o.contenido IS NOT NULL THEN o.contenido_unidad
        ELSE p.productos_unidad_medida_presentacion
    END AS productos_unidad_medida_presentacion,
    p.productos_cantidad_referencia,
    p.productos_unidad_medida_referencia,
    COALESCE(o.contenido, p.contenido) AS contenido,
    COALESCE(o.contenido_unidad, p.contenido_unidad) AS contenido_unidad,
    p.cadenas_fuente,
    o.producto_identity IS NOT NULL AS corregido,
    p.updated_at,
    COALESCE(o.tipo, p.tipo) AS tipo,
    COALESCE(o.variante, p.variante) AS variante
FROM dim_producto p
LEFT JOIN producto_overrides o ON o.producto_identity = p.producto_identity;
