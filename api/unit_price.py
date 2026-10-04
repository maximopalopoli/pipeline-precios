"""Price per kilogram or litre, so different sizes can be compared.

It is computed from the canonical package size (``contenido`` in g or ml,
migration 038), not read from SEPA's reference price: on the local AMBA day
that field matches the computed price per kg or l in about 97% of the rows
of most chains, but several chains (Farmacity among them) copy the list
price into it, and one mixes prices per kg and per 100 g.  Products sold by
the unit have no unit price.
"""

from __future__ import annotations

from decimal import Decimal

REFERENCE_UNITS = {"g": "kg", "ml": "l"}
CENTS = Decimal("0.01")


def unit_price(
    price: Decimal | None, contenido: Decimal | None, contenido_unidad: str | None
) -> dict | None:
    """``{"precio", "unidad"}`` per kg or l, or ``None`` when it does not apply."""
    unidad = REFERENCE_UNITS.get(contenido_unidad or "")
    if price is None or unidad is None or not contenido or contenido <= 0:
        return None
    precio = (Decimal(price) * 1000 / Decimal(contenido)).quantize(CENTS)
    return {"precio": precio, "unidad": unidad}


def with_unit_price(row) -> dict:
    """A store offer with the unit price of its store price.

    The row carries the product's ``contenido`` and ``contenido_unidad``,
    which are dropped from the result.
    """
    offer = dict(row)
    contenido = offer.pop("contenido")
    contenido_unidad = offer.pop("contenido_unidad")
    offer["precio_unitario"] = unit_price(offer["mejor_precio"], contenido, contenido_unidad)
    return offer
