"""Price per kg or l comes from the canonical package size."""

from decimal import Decimal as D

import pytest

from api.unit_price import unit_price, with_unit_price


@pytest.mark.parametrize(
    "price, contenido, unidad, expected",
    [
        (D("1500"), D("1500"), "ml", {"precio": D("1000.00"), "unidad": "l"}),
        (D("2000"), D("900"), "ml", {"precio": D("2222.22"), "unidad": "l"}),
        (D("850"), D("400"), "g", {"precio": D("2125.00"), "unidad": "kg"}),
        (D("3000"), D("50"), "u", None),
        (D("100"), None, None, None),
        (D("100"), D("0"), "g", None),
        (None, D("400"), "g", None),
    ],
)
def test_a_unit_price_applies_to_weight_and_volume(price, contenido, unidad, expected):
    assert unit_price(price, contenido, unidad) == expected


def test_an_offer_trades_its_size_for_the_unit_price_of_its_store_price():
    offer = with_unit_price(
        {"mejor_precio": D("900"), "precio_lista": D("1000"),
         "contenido": D("500"), "contenido_unidad": "g"}
    )

    assert offer == {
        "mejor_precio": D("900"), "precio_lista": D("1000"),
        "precio_unitario": {"precio": D("1800.00"), "unidad": "kg"},
    }
