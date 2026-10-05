-- 036_unify_effective_price.sql
-- One shared price rule.  A promotion counts only when it is positive and
-- lower than a positive list price; a zero promotion, one at or above the
-- list price, or a row without a valid list price never supplies a price.
-- Migration 016 took the lowest positive value of all three prices, which
-- could choose a promotion when the list price was missing.

CREATE OR REPLACE FUNCTION pipeline_discount_promo_price(
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
        CASE WHEN precio_promo1 > 0 AND precio_promo1 < precio_lista
             THEN precio_promo1 END,
        CASE WHEN precio_promo2 > 0 AND precio_promo2 < precio_lista
             THEN precio_promo2 END
    )
$$;

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
    SELECT CASE
        WHEN precio_lista > 0 THEN LEAST(
            precio_lista,
            pipeline_discount_promo_price(precio_lista, precio_promo1, precio_promo2)
        )
    END
$$;
