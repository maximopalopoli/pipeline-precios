-- Canonical price-comparison rule used by API queries and ETL aggregations.
-- A zero or negative value is not an actionable price.  Among valid values,
-- the customer-facing effective price is always the lowest one available.

CREATE OR REPLACE FUNCTION pipeline_effective_price(
    precio_lista NUMERIC,
    precio_promo1 NUMERIC,
    precio_promo2 NUMERIC
)
RETURNS NUMERIC
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT LEAST(
        CASE WHEN precio_lista > 0 THEN precio_lista END,
        CASE WHEN precio_promo1 > 0 THEN precio_promo1 END,
        CASE WHEN precio_promo2 > 0 THEN precio_promo2 END
    )
$$;
