-- 037_classify_promotion_conditions.sql
-- Separate unconditional from conditional promotions.  SEPA only provides a
-- free-text legend per promotion, so its requirement and validity are read
-- from that text.  A promotion that needs several units, a payment method, a
-- loyalty programme or a time of day is conditional: it is shown with its
-- legend but never used as the store's price.  A promotion whose declared
-- validity does not cover the snapshot day applies to nobody.
--
-- The functions below are the rule.  Reading a legend costs several regular
-- expressions, and a day has hundreds of thousands of promotional rows but
-- only hundreds of distinct legends, so publication stores each legend's
-- reading once in promo_leyendas.  A legend missing from that table is read
-- on the fly with the same functions.

-- Requirement of a promotion legend, or NULL when it states none.
CREATE OR REPLACE FUNCTION pipeline_promo_condition(leyenda TEXT)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT CASE
        -- 3X2, 2X1, 2X$2500, 2DO AL 70%, 2DA UNIDAD, Llevando 2 ...
        -- (a pack size such as 6X330ML is not matched).
        WHEN leyenda ~* '\m\d{1,2}\s*X\s*\$?\s*\d+(\.\d+)?\M'
          OR leyenda ~* '\m\d{1,2}\s*(DO|DA)\M'
          OR leyenda ~* '\msegund[oa]\s+unidad|\mllevando\M|\mcomprando\s+\d'
            THEN 'cantidad'
        WHEN leyenda ~* '\mtarjeta|\mbanco\M|\mbancari|\mvisa\M|mastercard|american\s+express|\mamex\M|\mcabal\M|\mnaranja\s*x\M|mercado\s*pago|\mmodo\M|cuenta\s+dni|billetera|\md[eé]bito\M|\mcr[eé]dito\M'
            THEN 'medio_pago'
        WHEN leyenda ~* '\mclub\M|\msocios?\M|\mcomunidad\M|\mpuntos\M|\mapp\M|\maplicaci[oó]n\M|\mexclusivo\s+(para\s+)?clientes'
            THEN 'programa'
        WHEN leyenda ~* '\m(x|por\s+la)\s+(tarde|mañana|noche)\M|\mhorario\M|happy\s+hour'
            THEN 'horario'
    END
$$;

-- Day written as dd/mm/yyyy or yyyy-mm-dd, or NULL.  An impossible day such
-- as 31/04 rolls over instead of failing the query.
CREATE OR REPLACE FUNCTION pipeline_legend_date(value TEXT)
RETURNS DATE
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT CASE
        WHEN value ~ '^\d{1,2}/\d{1,2}/\d{4}$' THEN make_date(
            split_part(value, '/', 3)::int, split_part(value, '/', 2)::int, 1
        ) + (split_part(value, '/', 1)::int - 1)
        WHEN value ~ '^\d{4}-\d{2}-\d{2}$' THEN make_date(
            split_part(value, '-', 1)::int, split_part(value, '-', 2)::int, 1
        ) + (split_part(value, '-', 3)::int - 1)
    END
$$;

-- First date after "desde"/"del": the start of the validity period.
CREATE OR REPLACE FUNCTION pipeline_promo_valid_from(leyenda TEXT)
RETURNS DATE
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT pipeline_legend_date(substring(
        leyenda FROM '(?i)\m(?:desde|del)\s+(?:el\s+)?((?:0?[1-9]|[12]\d|3[01])/(?:0?[1-9]|1[0-2])/20\d\d|20\d\d-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01]))'
    ))
$$;

-- Last date after "hasta"/"al": the end of the validity period.
CREATE OR REPLACE FUNCTION pipeline_promo_valid_until(leyenda TEXT)
RETURNS DATE
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT pipeline_legend_date(substring(
        leyenda FROM '(?i)^.*\m(?:hasta|al)\s+(?:el\s+)?((?:0?[1-9]|[12]\d|3[01])/(?:0?[1-9]|1[0-2])/20\d\d|20\d\d-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01]))'
    ))
$$;

CREATE TABLE IF NOT EXISTS promo_leyendas (
    leyenda        TEXT PRIMARY KEY,
    condicion      TEXT,
    vigencia_desde DATE,
    vigencia_hasta DATE
);

COMMENT ON TABLE promo_leyendas IS
    'Reading of each published promotion legend; filled at publication.';

INSERT INTO promo_leyendas (leyenda, condicion, vigencia_desde, vigencia_hasta)
SELECT
    leyenda,
    pipeline_promo_condition(leyenda),
    pipeline_promo_valid_from(leyenda),
    pipeline_promo_valid_until(leyenda)
FROM (
    SELECT leyenda_promo1 FROM fact_precios WHERE tiene_promo
    UNION
    SELECT leyenda_promo2 FROM fact_precios WHERE tiene_promo
) AS legends(leyenda)
WHERE leyenda IS NOT NULL
ON CONFLICT (leyenda) DO NOTHING;

-- Terms of a legend: its stored reading, or a reading made now.
CREATE OR REPLACE FUNCTION pipeline_promo_terms(leyenda TEXT, stored promo_leyendas)
RETURNS promo_leyendas
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT CASE
        WHEN (stored).leyenda IS NOT NULL OR leyenda IS NULL THEN stored
        ELSE ROW(
            leyenda,
            pipeline_promo_condition(leyenda),
            pipeline_promo_valid_from(leyenda),
            pipeline_promo_valid_until(leyenda)
        )::promo_leyendas
    END
$$;

-- Whether a promotion lowers a positive list price on a day.  conditional
-- selects conditional (TRUE) or unconditional (FALSE) promotions, or both
-- (NULL).  The price rule matches migration 036.
CREATE OR REPLACE FUNCTION pipeline_promo_applies(
    precio_lista NUMERIC,
    precio_promo NUMERIC,
    terms promo_leyendas,
    on_date DATE,
    conditional BOOLEAN
)
RETURNS BOOLEAN
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT COALESCE(
        precio_promo > 0
        AND precio_promo < precio_lista
        AND (conditional IS NULL OR ((terms).condicion IS NOT NULL) = conditional)
        AND COALESCE((terms).vigencia_desde <= on_date, TRUE)
        AND COALESCE((terms).vigencia_hasta >= on_date, TRUE),
        FALSE
    )
$$;

-- Which promotion (1 or 2) is the lowest that applies; ties keep the source
-- order.  NULL when neither applies.
CREATE OR REPLACE FUNCTION pipeline_promo_slot(
    precio_lista NUMERIC,
    precio_promo1 NUMERIC,
    terms1 promo_leyendas,
    precio_promo2 NUMERIC,
    terms2 promo_leyendas,
    on_date DATE,
    conditional BOOLEAN
)
RETURNS SMALLINT
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT CASE
        WHEN a1 AND (NOT a2 OR precio_promo1 <= precio_promo2) THEN 1::smallint
        WHEN a2 THEN 2::smallint
    END
    FROM (
        SELECT
            pipeline_promo_applies(precio_lista, precio_promo1, terms1, on_date, conditional) AS a1,
            pipeline_promo_applies(precio_lista, precio_promo2, terms2, on_date, conditional) AS a2
    ) AS applies
$$;

-- Price a shopper pays without meeting any condition: the list price, or a
-- lower unconditional promotion in force.  NULL without a valid list price.
CREATE OR REPLACE FUNCTION pipeline_store_price(
    precio_lista NUMERIC,
    precio_promo1 NUMERIC,
    terms1 promo_leyendas,
    precio_promo2 NUMERIC,
    terms2 promo_leyendas,
    on_date DATE
)
RETURNS NUMERIC
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT CASE
        WHEN precio_lista > 0 THEN CASE pipeline_promo_slot(
            precio_lista, precio_promo1, terms1, precio_promo2, terms2, on_date, FALSE
        )
            WHEN 1 THEN precio_promo1
            WHEN 2 THEN precio_promo2
            ELSE precio_lista
        END
    END
$$;
