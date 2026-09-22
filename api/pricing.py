"""Shared effective-price rule for API price comparisons."""

from __future__ import annotations

from decimal import Decimal


def effective_price(*prices: Decimal | None) -> Decimal | None:
    """Return the lowest positive published price, or ``None`` when absent."""
    valid_prices = [price for price in prices if price is not None and price > 0]
    return min(valid_prices) if valid_prices else None


def effective_price_sql(alias: str = "f") -> str:
    """Return the SQL expression matching :func:`effective_price` for facts."""
    return (
        "pipeline_effective_price("
        f"{alias}.precio_lista, "
        f"{alias}.precio_promo1, "
        f"{alias}.precio_promo2"
        ")"
    )


def discount_promo_price_sql(alias: str = "f") -> str:
    """Return the lowest promotion price that is actually a discount.

    SEPA can mark a row as promotional even when its published promotional
    price is zero or higher than the list price.  Those values must not be
    presented as an actionable promotion to clients.
    """
    return (
        "LEAST("
        f"CASE WHEN {alias}.precio_promo1 > 0 "
        f"AND {alias}.precio_promo1 < {alias}.precio_lista "
        f"THEN {alias}.precio_promo1 END, "
        f"CASE WHEN {alias}.precio_promo2 > 0 "
        f"AND {alias}.precio_promo2 < {alias}.precio_lista "
        f"THEN {alias}.precio_promo2 END"
        ")"
    )


def discount_promo_label_sql(alias: str = "f") -> str:
    """Return the label associated with :func:`discount_promo_price_sql`.

    Ties deliberately prefer the first promotion, matching its source order.
    A missing label remains ``NULL``: the source does not provide enough
    information to invent a condition.
    """
    promo1_is_discount = (
        f"{alias}.precio_promo1 > 0 "
        f"AND {alias}.precio_promo1 < {alias}.precio_lista"
    )
    promo2_is_discount = (
        f"{alias}.precio_promo2 > 0 "
        f"AND {alias}.precio_promo2 < {alias}.precio_lista"
    )
    return (
        "CASE "
        f"WHEN {promo1_is_discount} AND (NOT ({promo2_is_discount}) "
        f"OR {alias}.precio_promo1 <= {alias}.precio_promo2) "
        f"THEN {alias}.leyenda_promo1 "
        f"WHEN {promo2_is_discount} THEN {alias}.leyenda_promo2 "
        "END"
    )
