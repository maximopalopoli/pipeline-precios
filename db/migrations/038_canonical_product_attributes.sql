-- Canonical product attributes.
--
-- A valid EAN is shared by every chain that sells it, and each chain spells
-- its attributes differently ("20 gr", "0.02 KG", "20.00 GR.").  The loader
-- now chooses each attribute by a vote of chains instead of keeping an
-- arbitrary chain's row, normalizes the package size to grams, millilitres or
-- units, and records EANs whose chains disagree on brand or measured size.

-- Base unit of a SEPA unit of measure: 'g', 'ml', 'u', or NULL when unknown
-- (metres, pairs, packs, ...).  Unknown units are never guessed.
CREATE FUNCTION pipeline_size_unit(unidad TEXT)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT CASE UPPER(BTRIM(unidad))
        WHEN 'G' THEN 'g' WHEN 'GR' THEN 'g' WHEN 'GR.' THEN 'g'
        WHEN 'GRM' THEN 'g' WHEN 'GRS' THEN 'g'
        WHEN 'KG' THEN 'g' WHEN 'KG.' THEN 'g' WHEN 'KGM' THEN 'g' WHEN 'KGR' THEN 'g'
        WHEN 'ML' THEN 'ml' WHEN 'ML.' THEN 'ml' WHEN 'CC' THEN 'ml' WHEN 'CC.' THEN 'ml'
        WHEN 'CM3' THEN 'ml' WHEN 'CMQ' THEN 'ml' WHEN 'CL' THEN 'ml'
        WHEN 'L' THEN 'ml' WHEN 'LT' THEN 'ml' WHEN 'LT.' THEN 'ml' WHEN 'LTR' THEN 'ml'
        WHEN 'DM3' THEN 'ml' WHEN 'DMQ' THEN 'ml'
        WHEN 'U' THEN 'u' WHEN 'UN' THEN 'u' WHEN 'UN.' THEN 'u' WHEN 'UNI' THEN 'u'
        WHEN 'UD' THEN 'u' WHEN 'EA' THEN 'u' WHEN 'UNIDAD' THEN 'u'
    END
$$;

-- Package size in the base unit, or NULL when the quantity is not a positive
-- number or the unit is unknown.
CREATE FUNCTION pipeline_size_amount(cantidad TEXT, unidad TEXT)
RETURNS NUMERIC
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT CASE
        WHEN BTRIM(cantidad) !~ '^[0-9]*\.?[0-9]+$' OR BTRIM(cantidad)::NUMERIC <= 0
            THEN NULL
        ELSE TRIM_SCALE(BTRIM(cantidad)::NUMERIC * CASE UPPER(BTRIM(unidad))
            WHEN 'KG' THEN 1000 WHEN 'KG.' THEN 1000 WHEN 'KGM' THEN 1000 WHEN 'KGR' THEN 1000
            WHEN 'L' THEN 1000 WHEN 'LT' THEN 1000 WHEN 'LT.' THEN 1000 WHEN 'LTR' THEN 1000
            WHEN 'DM3' THEN 1000 WHEN 'DMQ' THEN 1000
            WHEN 'CL' THEN 10
            ELSE CASE WHEN pipeline_size_unit(unidad) IS NULL THEN NULL ELSE 1 END
        END)
    END
$$;

ALTER TABLE dim_producto
    ADD COLUMN contenido NUMERIC,
    ADD COLUMN contenido_unidad TEXT
        CHECK (contenido_unidad IN ('g', 'ml', 'u')),
    ADD COLUMN cadenas_fuente INT;

COMMENT ON COLUMN dim_producto.contenido IS
    'Package size in contenido_unidad (g, ml or u); NULL when unknown.';
COMMENT ON COLUMN dim_producto.cadenas_fuente IS
    'Chains that reported the product in the last full ingestion that loaded it.';

UPDATE dim_producto
SET contenido = pipeline_size_amount(
        productos_cantidad_presentacion, productos_unidad_medida_presentacion
    ),
    contenido_unidad = CASE
        WHEN pipeline_size_amount(
            productos_cantidad_presentacion, productos_unidad_medida_presentacion
        ) IS NOT NULL
            THEN pipeline_size_unit(productos_unidad_medida_presentacion)
    END;

-- Attributes on which the chains selling one product disagree, for review.
-- Each full ingestion replaces the rows of the products it loaded.
CREATE TABLE producto_conflictos (
    producto_identity TEXT        NOT NULL,
    atributo          TEXT        NOT NULL CHECK (atributo IN ('marca', 'contenido')),
    -- [{"valor": ..., "cadenas": n}, ...], most chains first
    valores           JSONB       NOT NULL,
    run_id            INT         NOT NULL REFERENCES ingestion_runs (id),
    detected_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (producto_identity, atributo)
);
