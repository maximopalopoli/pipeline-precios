"""Shared price rules for API price comparisons.

A promotion counts only when it is positive and lower than a positive list
price.  SEPA can mark a row as promotional even when its promotional price is
zero or higher than the list price; those values are kept for auditability
but never chosen.  The SQL functions of migration 036 implement the same rule.

A promotion whose legend states a requirement (several units, a payment
method, a loyalty programme or a time of day) is conditional and never sets
the store price; neither does one outside its declared validity (migration
037).
"""

from __future__ import annotations

from decimal import Decimal


def discount_promo_price(
    precio_lista: Decimal | None,
    precio_promo1: Decimal | None,
    precio_promo2: Decimal | None,
) -> Decimal | None:
    """Return the lowest promotion that is a real discount, if any."""
    if precio_lista is None:
        return None
    discounts = [
        promo
        for promo in (precio_promo1, precio_promo2)
        if promo is not None and 0 < promo < precio_lista
    ]
    return min(discounts) if discounts else None


def effective_price(
    precio_lista: Decimal | None,
    precio_promo1: Decimal | None,
    precio_promo2: Decimal | None,
) -> Decimal | None:
    """Return the price a shopper pays, or ``None`` without a valid list price."""
    if precio_lista is None or precio_lista <= 0:
        return None
    discount = discount_promo_price(precio_lista, precio_promo1, precio_promo2)
    return precio_lista if discount is None else discount


# Joins that read the terms of both promotions of a fact aliased ``f`` and
# choose, once per row, the promotion that applies in each sense.  Columns:
# ``ps.incondicional`` (no requirement), ``ps.condicional`` (a requirement) and
# ``ps.cualquiera`` (either), each 1, 2 or NULL.  Migration 037 defines the rule.
_ON_DATE = "TO_DATE(f.fecha_id::text, 'YYYYMMDD')"
_SLOT_ARGS = "f.precio_lista, f.precio_promo1, pt.terms1, f.precio_promo2, pt.terms2"

PROMO_JOINS = f"""
        LEFT JOIN promo_leyendas pl1 ON pl1.leyenda = f.leyenda_promo1
        LEFT JOIN promo_leyendas pl2 ON pl2.leyenda = f.leyenda_promo2
        CROSS JOIN LATERAL (
            SELECT
                pipeline_promo_terms(f.leyenda_promo1, pl1) AS terms1,
                pipeline_promo_terms(f.leyenda_promo2, pl2) AS terms2
            OFFSET 0
        ) pt
        CROSS JOIN LATERAL (
            SELECT
                pipeline_promo_slot({_SLOT_ARGS}, {_ON_DATE}, FALSE) AS incondicional,
                pipeline_promo_slot({_SLOT_ARGS}, {_ON_DATE}, TRUE) AS condicional,
                pipeline_promo_slot({_SLOT_ARGS}, {_ON_DATE}, NULL) AS cualquiera
            OFFSET 0
        ) ps"""

UNCONDITIONAL_SLOT = "ps.incondicional"
ANY_SLOT = "ps.cualquiera"

# Price paid without meeting any condition; NULL without a valid list price.
STORE_PRICE = f"pipeline_store_price({_SLOT_ARGS}, {_ON_DATE})"


def promo_price_sql(slot: str) -> str:
    """Return the price of the promotion chosen by ``slot``, or NULL."""
    return f"CASE {slot} WHEN 1 THEN f.precio_promo1 WHEN 2 THEN f.precio_promo2 END"


def promo_label_sql(slot: str) -> str:
    """Return the source legend of the promotion chosen by ``slot``, or NULL."""
    return f"CASE {slot} WHEN 1 THEN f.leyenda_promo1 WHEN 2 THEN f.leyenda_promo2 END"


def promo_condition_sql(slot: str) -> str:
    """Return the requirement of the promotion chosen by ``slot``, or NULL."""
    return f"CASE {slot} WHEN 1 THEN (pt.terms1).condicion WHEN 2 THEN (pt.terms2).condicion END"


# A conditional promotion is only worth showing when it beats the store price.
CONDITIONAL_SLOT = (
    f"CASE WHEN {promo_price_sql('ps.condicional')} < {STORE_PRICE} "
    "THEN ps.condicional END"
)

# Price columns of a store offer: the store price, the unconditional
# promotion behind it, and the best conditional promotion shown apart.
OFFER_PRICE_COLUMNS = f"""
            {STORE_PRICE} AS mejor_precio,
            {promo_price_sql(UNCONDITIONAL_SLOT)} AS precio_promo,
            {promo_label_sql(UNCONDITIONAL_SLOT)} AS leyenda_promo,
            ({UNCONDITIONAL_SLOT} IS NOT NULL) AS tiene_descuento,
            {promo_price_sql(CONDITIONAL_SLOT)} AS precio_condicional,
            {promo_label_sql(CONDITIONAL_SLOT)} AS leyenda_condicional,
            {promo_condition_sql(CONDITIONAL_SLOT)} AS condicion"""
