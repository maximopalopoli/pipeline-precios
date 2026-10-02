-- Curated product attribute overrides.
--
-- The chain vote (migration 038) is wrong for some products: the chains
-- disagree, or all of them abbreviate.  A curated override corrects the
-- description, brand or size of one product identity.  Overrides are applied
-- when reading, through v_producto, so an edit is visible at once and removing
-- it restores the voted value.  db/seeds/seed_producto_overrides.sql is the
-- versioned source of truth and replaces this table on every migration run.

CREATE TABLE producto_overrides (
    producto_identity TEXT        PRIMARY KEY,
    descripcion       TEXT        CHECK (BTRIM(descripcion) <> ''),
    marca             TEXT        CHECK (BTRIM(marca) <> ''),
    contenido         NUMERIC     CHECK (contenido > 0),
    contenido_unidad  TEXT        CHECK (contenido_unidad IN ('g', 'ml', 'u')),
    -- Why the vote is wrong, and what was checked.
    nota              TEXT        NOT NULL CHECK (BTRIM(nota) <> ''),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK ((contenido IS NULL) = (contenido_unidad IS NULL)),
    CHECK (descripcion IS NOT NULL OR marca IS NOT NULL OR contenido IS NOT NULL)
);

-- dim_producto with the curated overrides applied.  The API reads products
-- through this view.
CREATE VIEW v_producto AS
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
    p.updated_at
FROM dim_producto p
LEFT JOIN producto_overrides o ON o.producto_identity = p.producto_identity;

-- Chain disagreements that no override has settled yet.  Review the products
-- with most chains first (ORDER BY cadenas_fuente DESC).
CREATE VIEW v_producto_conflictos_pendientes AS
SELECT
    c.producto_identity,
    p.productos_descripcion AS descripcion_votada,
    c.atributo,
    c.valores,
    p.cadenas_fuente,
    c.run_id,
    c.detected_at
FROM producto_conflictos c
JOIN dim_producto p ON p.producto_identity = c.producto_identity
LEFT JOIN producto_overrides o ON o.producto_identity = c.producto_identity
WHERE NOT (c.atributo = 'marca' AND o.marca IS NOT NULL)
  AND NOT (c.atributo = 'contenido' AND o.contenido IS NOT NULL);
