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
