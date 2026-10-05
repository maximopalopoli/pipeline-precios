-- Product type from a curated term dictionary.
--
-- SEPA has no category column, but descriptions name the product first,
-- often abbreviated: "YOG BEBIBLE", "SH SEDAL", "GALL CHOC".  A curated
-- dictionary (db/seeds/seed_producto_tipos.sql, replaced on every migration
-- run) maps words and short phrases to a type.  The loader classifies each
-- chain's description and the chains vote, as for the other attributes
-- (migration 038).  A description with no known term at its start leaves
-- the type empty: it is never guessed.  A dictionary change takes
-- effect for the products of the next full ingestion.

-- Upper case, no accents, Ñ as N (chains write both PAÑAL and PANAL), and
-- every run of other characters as one space.
CREATE FUNCTION pipeline_normalize_text(texto TEXT) RETURNS TEXT
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT BTRIM(REGEXP_REPLACE(
        TRANSLATE(UPPER(texto), 'ÁÉÍÓÚÀÈÌÒÙÄËÏÖÜÂÊÎÔÛÑ', 'AEIOUAEIOUAEIOUAEIOUN'),
        '[^A-Z0-9]+', ' ', 'g'
    ))
$$;

CREATE TABLE producto_tipo_terminos (
    -- One to three normalized words: "YOG", "DULCE DE LECHE".
    termino    TEXT    PRIMARY KEY CHECK (
        termino <> ''
        AND termino = pipeline_normalize_text(termino)
        AND CARDINALITY(STRING_TO_ARRAY(termino, ' ')) <= 3
    ),
    tipo       TEXT    NOT NULL CHECK (tipo ~ '^[a-záéíóúñü]+( [a-záéíóúñü]+)*$'),
    -- A term matches at the first word.  A term that never names an
    -- ingredient or flavor ("SH", "DEO", "YOG") also matches at the second
    -- or third word, behind a brand: "SEDAL SH", "REXONA MEN DEO".
    tras_marca BOOLEAN NOT NULL DEFAULT FALSE
);

-- The dictionary terms found in a description, with the word they start at
-- and their length.  The type is the match that starts earliest, the longest
-- one when several start there ("DULCE DE LECHE" over "DULCE").  Other words
-- after the first are flavors, ingredients or uses ("ALFAJOR ... DULCE DE
-- LECHE", "PISTOLA DE AGUA"), so only tras_marca terms match there.
CREATE FUNCTION pipeline_product_type_matches(descripcion TEXT)
RETURNS TABLE (tipo TEXT, inicio INT, largo INT)
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    SELECT t.tipo, inicio, largo
    FROM (
        -- OFFSET 0 normalizes once per description instead of once per
        -- candidate term (10x faster over a full day's descriptions).
        SELECT STRING_TO_ARRAY(pipeline_normalize_text(descripcion), ' ') AS palabras
        OFFSET 0
    ) AS texto
    CROSS JOIN GENERATE_SERIES(1, LEAST(CARDINALITY(texto.palabras), 3)) AS inicio
    CROSS JOIN GENERATE_SERIES(1, 3) AS largo
    JOIN producto_tipo_terminos t
      ON t.termino = ARRAY_TO_STRING(texto.palabras[inicio:inicio + largo - 1], ' ')
    WHERE inicio + largo - 1 <= CARDINALITY(texto.palabras)
      AND (inicio = 1 OR t.tras_marca)
$$;

CREATE FUNCTION pipeline_product_type(descripcion TEXT) RETURNS TEXT
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    SELECT tipo
    FROM pipeline_product_type_matches(descripcion)
    ORDER BY inicio, largo DESC
    LIMIT 1
$$;

ALTER TABLE dim_producto ADD COLUMN tipo TEXT;

ALTER TABLE producto_overrides
    ADD COLUMN tipo TEXT CHECK (tipo ~ '^[a-záéíóúñü]+( [a-záéíóúñü]+)*$'),
    DROP CONSTRAINT producto_overrides_check1,
    ADD CONSTRAINT producto_overrides_any_attribute CHECK (
        descripcion IS NOT NULL OR marca IS NOT NULL
        OR contenido IS NOT NULL OR tipo IS NOT NULL
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
    COALESCE(o.tipo, p.tipo) AS tipo
FROM dim_producto p
LEFT JOIN producto_overrides o ON o.producto_identity = p.producto_identity;
