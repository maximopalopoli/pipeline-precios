"""Daily offers are paged by discount and summarized over every offer."""

import asyncio
import re
from decimal import Decimal as D

import pytest

from api.location import Location
from api.routes import ofertas
from tests.integration.test_list_totals import OBELISCO, Day


@pytest.fixture()
def day(pg_conn, monkeypatch):
    async def fetch(sql: str, *params):
        assert {int(n) for n in re.findall(r"\$(\d+)", sql)} == set(range(1, len(params) + 1))
        with pg_conn.cursor() as cur:
            cur.execute(
                re.sub(r"\$(\d+)", r"%(p\1)s", sql),
                {f"p{i}": value for i, value in enumerate(params, start=1)},
            )
            columns = [column.name for column in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]

    async def fetchrow(sql: str, *params):
        rows = await fetch(sql, *params)
        return rows[0] if rows else None

    monkeypatch.setattr(ofertas, "fetch", fetch)
    monkeypatch.setattr(ofertas, "fetchrow", fetchrow)
    return Day(pg_conn)


def _promoted(day, products: dict[int, str]) -> None:
    """Describe ``products`` and flag every promoted fact as the loader does."""
    with day.conn.cursor() as cur:
        for producto_sk, description in products.items():
            cur.execute(
                "UPDATE dim_producto SET productos_descripcion = %s WHERE producto_sk = %s",
                (description, producto_sk),
            )
        cur.execute("UPDATE fact_precios SET tiene_promo = precio_promo1 IS NOT NULL")


def _offers(location=None, limit=50, offset=0) -> dict:
    return asyncio.run(
        ofertas.get_ofertas(
            provincia=None, localidad=None, cadenas=None,
            location=location, limit=limit, offset=offset,
        )
    )


@pytest.fixture()
def offers(day):
    entera, otra_entera, arroz = day.products(3)
    near = day.store(0.5, {
        entera: (100, 50, "50% OFF"), otra_entera: (100, 80, "20% OFF"),
        arroz: (100, 90, "10% OFF"),
    })
    far = day.store(3, {arroz: (100, 10, "90% OFF")})
    day.store(0.7, {entera: 100})
    # Two products may share a description; they are still two offers.
    _promoted(day, {entera: "LECHE ENTERA 1L", otra_entera: "LECHE ENTERA 1L", arroz: "ARROZ"})
    return {"entera": entera, "otra_entera": otra_entera, "arroz": arroz,
            "near": near, "far": far}


def test_offers_come_largest_discount_first_one_per_product(offers):
    response = _offers()

    assert response["fecha"] == "2026-03-17"
    assert [(o["producto_sk"], o["descuento_pct"]) for o in response["items"]] == [
        (offers["arroz"], D("90.0")),
        (offers["entera"], D("50.0")),
        (offers["otra_entera"], D("20.0")),
        (offers["arroz"], D("10.0")),
    ]
    assert [o["sucursales"] for o in response["items"]] == [1, 1, 1, 1]
    assert (response["total"], response["hay_mas"]) == (4, False)
    assert (response["descuento_promedio"], response["descuento_maximo"]) == (
        D("42.5"), D("90.0"),
    )


def test_pages_neither_repeat_nor_skip_and_keep_the_full_summary(offers):
    pages = [_offers(limit=3, offset=offset) for offset in (0, 3, 6)]

    assert [len(page["items"]) for page in pages] == [3, 1, 0]
    assert [page["hay_mas"] for page in pages] == [True, False, False]
    assert [o["descuento_pct"] for page in pages for o in page["items"]] == [
        D("90.0"), D("50.0"), D("20.0"), D("10.0"),
    ]
    for page in pages:
        assert (page["total"], page["descuento_maximo"]) == (4, D("90.0"))


def test_a_location_keeps_only_nearby_offers(offers):
    response = _offers(location=Location(*OBELISCO, 1))

    assert [o["descuento_pct"] for o in response["items"]] == [D("50.0"), D("20.0"), D("10.0")]
    assert (response["total"], response["descuento_maximo"]) == (3, D("50.0"))


def test_no_published_day_means_no_offers(pg_conn, monkeypatch):
    async def fetchrow(sql: str, *params):
        return None

    monkeypatch.setattr(ofertas, "fetchrow", fetchrow)

    assert _offers()["items"] == []
