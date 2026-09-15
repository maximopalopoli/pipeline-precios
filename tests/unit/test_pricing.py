from decimal import Decimal

from api.pricing import effective_price, effective_price_sql


def test_effective_price_uses_the_list_price_without_promotions():
    assert effective_price(Decimal("100"), None, None) == Decimal("100")


def test_effective_price_uses_the_lowest_positive_price():
    assert effective_price(Decimal("100"), Decimal("80"), Decimal("90")) == Decimal("80")


def test_effective_price_ignores_zero_and_promotion_higher_than_list_price():
    assert effective_price(Decimal("100"), Decimal("0"), Decimal("200")) == Decimal("100")


def test_effective_price_returns_none_when_no_positive_price_exists():
    assert effective_price(None, Decimal("0"), None) is None


def test_effective_price_sql_uses_the_same_three_fact_columns():
    assert effective_price_sql("price") == (
        "pipeline_effective_price(price.precio_lista, "
        "price.precio_promo1, price.precio_promo2)"
    )
