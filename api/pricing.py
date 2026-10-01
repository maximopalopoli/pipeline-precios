"""Shared effective-price rule for API price comparisons.

A promotion counts only when it is positive and lower than a positive list
price.  SEPA can mark a row as promotional even when its promotional price is
zero or higher than the list price; those values are kept for auditability
but never chosen.  The SQL functions of migration 036 implement the same rule.
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


def _price_args(alias: str) -> str:
    return f"{alias}.precio_lista, {alias}.precio_promo1, {alias}.precio_promo2"


def effective_price_sql(alias: str = "f") -> str:
    """Return the SQL expression matching :func:`effective_price` for facts."""
    return f"pipeline_effective_price({_price_args(alias)})"


def discount_promo_price_sql(alias: str = "f") -> str:
    """Return the SQL expression matching :func:`discount_promo_price`."""
    return f"pipeline_discount_promo_price({_price_args(alias)})"


def discount_promo_label_sql(alias: str = "f") -> str:
    """Return the label of the promotion chosen by :func:`discount_promo_price_sql`.

    Ties prefer the first promotion, matching its source order.  A missing
    label remains ``NULL``: the source does not provide enough information to
    invent a condition.
    """
    discount = discount_promo_price_sql(alias)
    return (
        "CASE "
        f"WHEN {alias}.precio_promo1 = {discount} THEN {alias}.leyenda_promo1 "
        f"WHEN {alias}.precio_promo2 = {discount} THEN {alias}.leyenda_promo2 "
        "END"
    )
