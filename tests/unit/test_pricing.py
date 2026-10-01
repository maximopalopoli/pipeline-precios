import pytest

from api.pricing import (
    CONDITIONAL_SLOT,
    OFFER_PRICE_COLUMNS,
    PROMO_JOINS,
    STORE_PRICE,
    UNCONDITIONAL_SLOT,
    discount_promo_price,
    effective_price,
    promo_condition_sql,
    promo_label_sql,
    promo_price_sql,
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


def test_promotion_columns_follow_the_chosen_slot():
    assert promo_price_sql("ps.x") == (
        "CASE ps.x WHEN 1 THEN f.precio_promo1 WHEN 2 THEN f.precio_promo2 END"
    )
    assert promo_label_sql("ps.x") == (
        "CASE ps.x WHEN 1 THEN f.leyenda_promo1 WHEN 2 THEN f.leyenda_promo2 END"
    )
    assert promo_condition_sql("ps.x") == (
        "CASE ps.x WHEN 1 THEN (pt.terms1).condicion "
        "WHEN 2 THEN (pt.terms2).condicion END"
    )


def test_store_price_never_uses_a_conditional_promotion():
    assert STORE_PRICE.startswith("pipeline_store_price(")
    assert f"{promo_price_sql(UNCONDITIONAL_SLOT)} AS precio_promo" in OFFER_PRICE_COLUMNS
    assert f"{promo_price_sql(CONDITIONAL_SLOT)} AS precio_condicional" in OFFER_PRICE_COLUMNS
    assert f"< {STORE_PRICE}" in CONDITIONAL_SLOT


def test_promotion_joins_read_stored_legend_terms():
    assert "LEFT JOIN promo_leyendas pl1 ON pl1.leyenda = f.leyenda_promo1" in PROMO_JOINS
    assert "LEFT JOIN promo_leyendas pl2 ON pl2.leyenda = f.leyenda_promo2" in PROMO_JOINS
