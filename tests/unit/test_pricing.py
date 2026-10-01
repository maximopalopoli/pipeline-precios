import pytest

from api.pricing import (
    discount_promo_label_sql,
    discount_promo_price,
    discount_promo_price_sql,
    effective_price,
    effective_price_sql,
)
from tests.price_cases import PRICE_CASES

CASES = [pytest.param(*case[1:], id=case[0]) for case in PRICE_CASES]


@pytest.mark.parametrize("lista, promo1, promo2, discount, effective", CASES)
def test_discount_promo_price_follows_the_shared_rule(
    lista, promo1, promo2, discount, effective
):
    assert discount_promo_price(lista, promo1, promo2) == discount


@pytest.mark.parametrize("lista, promo1, promo2, discount, effective", CASES)
def test_effective_price_follows_the_shared_rule(
    lista, promo1, promo2, discount, effective
):
    assert effective_price(lista, promo1, promo2) == effective


def test_effective_price_sql_calls_the_shared_function():
    assert effective_price_sql("price") == (
        "pipeline_effective_price(price.precio_lista, "
        "price.precio_promo1, price.precio_promo2)"
    )


def test_discount_promotion_sql_calls_the_shared_function():
    assert discount_promo_price_sql("price") == (
        "pipeline_discount_promo_price(price.precio_lista, "
        "price.precio_promo1, price.precio_promo2)"
    )


def test_discount_promotion_label_sql_compares_with_the_selected_price():
    expression = discount_promo_label_sql("price")
    discount = discount_promo_price_sql("price")
    assert f"WHEN price.precio_promo1 = {discount} THEN price.leyenda_promo1" in expression
    assert f"WHEN price.precio_promo2 = {discount} THEN price.leyenda_promo2" in expression
