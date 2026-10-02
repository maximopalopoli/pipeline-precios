-- Product display name and indexed catalog search.
--
-- A shopper should read "Leche La Serenisima parcialmente descremada 1 l",
-- not "LECHE PARC.DESC.MULTID. 1% SACHET", and a search for "leche" should
-- list milk before dulce de leche or cookies.  The display name is built
-- from the canonical attributes (type, brand, variant, size); a product
-- without a type keeps its description, since nothing else is guessed.  The
-- original description stays available next to it.
--
-- Search matches every query word anywhere in the normalized description,
-- brand, type and variant.  Normalizing all products on every query took
-- about 850 ms on 99 k products, so the text of the voted attributes is
-- indexed with trigrams; the few products with an override are searched
-- apart (see api/routes/products.py).  Trigrams need three letters, so a
-- query of shorter words only ("te") finds the products of the type it names,
-- through an index on the type.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- A size for reading: 500 g, 1,5 l, x 6.  One unit says nothing and is left
-- out.
CREATE FUNCTION pipeline_format_size(contenido NUMERIC, unidad TEXT) RETURNS TEXT
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE
        WHEN contenido IS NULL OR unidad IS NULL THEN NULL
        WHEN unidad = 'u' AND contenido = 1 THEN NULL
        WHEN unidad = 'u' THEN 'x ' || TRIM_SCALE(contenido)::TEXT
        WHEN contenido >= 1000 THEN
            REPLACE(TRIM_SCALE(contenido / 1000)::TEXT, '.', ',')
            || CASE unidad WHEN 'g' THEN ' kg' ELSE ' l' END
        ELSE REPLACE(TRIM_SCALE(contenido)::TEXT, '.', ',') || ' ' || unidad
    END
$$;

CREATE FUNCTION pipeline_product_display_name(
    tipo TEXT, marca TEXT, variante TEXT, contenido NUMERIC, unidad TEXT, descripcion TEXT
) RETURNS TEXT
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE
        WHEN tipo IS NULL THEN descripcion
        ELSE CONCAT_WS(
            ' ',
            UPPER(LEFT(tipo, 1)) || SUBSTRING(tipo FROM 2),
            INITCAP(NULLIF(BTRIM(marca), '')),
            variante,
            pipeline_format_size(contenido, unidad)
        )
    END
$$;

-- The normalized text a query word must appear in.  It joins with || rather
-- than CONCAT_WS, which is not immutable and so cannot be indexed, and names
-- the schema because PostgreSQL 17 and later maintain indexes with a search
-- path of pg_catalog only.
CREATE FUNCTION pipeline_product_search_text(
    descripcion TEXT, marca TEXT, tipo TEXT, variante TEXT
) RETURNS TEXT
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT public.pipeline_normalize_text(
        COALESCE(descripcion, '') || ' ' || COALESCE(marca, '') || ' '
        || COALESCE(tipo, '') || ' ' || COALESCE(variante, '')
    )
$$;

CREATE INDEX idx_dim_producto_search_text ON dim_producto USING gin (
    pipeline_product_search_text(productos_descripcion, productos_marca, tipo, variante)
    gin_trgm_ops
);

CREATE INDEX idx_dim_producto_tipo ON dim_producto (tipo);

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
    COALESCE(o.variante, p.variante) AS variante,
    pipeline_product_display_name(
        COALESCE(o.tipo, p.tipo),
        COALESCE(o.marca, p.productos_marca),
        COALESCE(o.variante, p.variante),
        COALESCE(o.contenido, p.contenido),
        COALESCE(o.contenido_unidad, p.contenido_unidad),
        COALESCE(o.descripcion, p.productos_descripcion)
    ) AS nombre,
    pipeline_product_search_text(
        COALESCE(o.descripcion, p.productos_descripcion),
        COALESCE(o.marca, p.productos_marca),
        COALESCE(o.tipo, p.tipo),
        COALESCE(o.variante, p.variante)
    ) AS texto_busqueda
FROM dim_producto p
LEFT JOIN producto_overrides o ON o.producto_identity = p.producto_identity;
