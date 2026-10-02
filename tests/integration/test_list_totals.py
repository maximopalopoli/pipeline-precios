"""A shopping list is totalled per nearby store, ranked by coverage first."""

import asyncio
import re
from datetime import date
from decimal import Decimal as D

import pytest

from api.location import KM_PER_DEGREE_LAT, Location
from api.routes import lista
from etl.load.warehouse_loader import _ensure_partition

FECHA_ID = 20260317
OBELISCO = (-34.6037, -58.3816)


class Day:
    """A published day; products and stores are priced as the test adds them."""

    def __init__(self, conn):
        self.conn = conn
        self.stores = 0
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO dim_fecha (
                    fecha_id, fecha, anio, mes, trimestre, semana_iso, dia_semana, es_fin_semana
                ) VALUES (%s, %s, 2026, 3, 1, 12, 2, FALSE)
                """,
                (FECHA_ID, date(2026, 3, 17)),
            )
            cur.execute(
                """
                INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
                VALUES ('l.zip', 'https://example.test/l.zip', 'lista', 1, '2026-03-17')
                RETURNING id
                """
            )
            cur.execute(
                """
                INSERT INTO ingestion_runs (raw_file_id, status, rows_fact_published, finished_at)
                VALUES (%s, 'completed', 1, NOW()) RETURNING id
                """,
                (cur.fetchone()[0],),
            )
            self.run_id = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO dim_provincia (codigo_iso, nombre, region)
                VALUES ('AR-C', 'CABA', 'AMBA') RETURNING provincia_sk
                """
            )
            self.provincia_sk = cur.fetchone()[0]
        _ensure_partition(conn, FECHA_ID)

    def products(self, count: int) -> list[int]:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO dim_producto (producto_identity, id_producto)
                SELECT 'test:' || n, n::text FROM generate_series(1, %s) n
                RETURNING producto_sk
                """,
                (count,),
            )
            return [row[0] for row in cur.fetchall()]

    def store(self, km_north: float, prices: dict[int, object]) -> int:
        """A store that far north of the Obelisco with ``prices`` per product.

        A price is the list price, or ``(list price, promotion, legend)``.
        """
        self.stores += 1
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO dim_comercio (id_comercio, id_bandera, comercio_bandera_nombre)
                VALUES (%s, 1, %s) RETURNING comercio_sk
                """,
                (self.stores, f"Cadena {self.stores}"),
            )
            cur.execute(
                """
                INSERT INTO dim_sucursal (
                    id_comercio, id_bandera, id_sucursal, comercio_sk, provincia_sk,
                    sucursales_latitud, sucursales_longitud
                ) VALUES (%s, 1, 1, %s, %s, %s, %s) RETURNING sucursal_sk
                """,
                (
                    self.stores, cur.fetchone()[0], self.provincia_sk,
                    round(OBELISCO[0] + km_north / KM_PER_DEGREE_LAT, 6), OBELISCO[1],
                ),
            )
            sucursal_sk = cur.fetchone()[0]
            for producto_sk, price in prices.items():
                lista_price, promo, legend = price if isinstance(price, tuple) else (
                    price, None, None
                )
                cur.execute(
                    """
                    INSERT INTO fact_precios (
                        producto_sk, sucursal_sk, fecha_id, run_id, precio_lista,
                        precio_promo1, leyenda_promo1
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (producto_sk, sucursal_sk, FECHA_ID, self.run_id, lista_price, promo, legend),
                )
        return sucursal_sk


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

    monkeypatch.setattr(lista, "fetch", fetch)
    monkeypatch.setattr(lista, "fetchrow", fetchrow)
    return Day(pg_conn)


def _totals(products: list[int], radius: float = 2) -> list[dict]:
    return asyncio.run(lista.get_list_totals(products, Location(*OBELISCO, radius)))


def test_a_complete_basket_ranks_above_a_cheaper_incomplete_one(day):
    leche, pan, arroz = day.products(3)
    complete = day.store(0.5, {leche: 500, pan: 800, arroz: 900})
    cheaper_without_rice = day.store(0.3, {leche: 100, pan: 100})

    totals = _totals([leche, pan, arroz])

    assert [(t["sucursal_sk"], t["cubiertos"], t["productos"], t["faltantes"], t["total"])
            for t in totals] == [
        (complete, 3, 3, [], D("2200")),
        (cheaper_without_rice, 2, 3, [arroz], D("200")),
    ]


def test_stores_with_the_same_coverage_rank_by_their_unconditional_total(day):
    leche, pan = day.products(2)
    dear = day.store(0.2, {leche: 500, pan: 800})
    # The 20% promotion has no condition, so it is the price paid.
    promoted = day.store(0.9, {leche: (500, 300, "20% de descuento"), pan: 800})

    totals = _totals([leche, pan])

    assert [(t["sucursal_sk"], t["total"]) for t in totals] == [
        (promoted, D("1100")), (dear, D("1300")),
    ]
    assert [item["mejor_precio"] for item in totals[0]["items"]] == [D("300"), D("800")]


def test_the_conditional_total_is_kept_apart(day):
    leche, pan = day.products(2)
    conditional = day.store(0.5, {leche: (600, 400, "3X2 JUGOS TUTTI"), pan: 800})
    plain = day.store(0.6, {leche: 600, pan: 900})

    totals = {t["sucursal_sk"]: t for t in _totals([leche, pan])}

    assert totals[conditional]["total"] == D("1400")
    assert totals[conditional]["total_condicional"] == D("1200")
    assert totals[conditional]["condicionales"] == 1
    assert totals[plain]["total_condicional"] is None
    assert totals[plain]["condicionales"] == 0


def test_far_stores_invalid_prices_and_repeated_items_do_not_count(day):
    leche, pan = day.products(2)
    day.store(3, {leche: 100, pan: 100})
    zero_priced_bread = day.store(0.5, {leche: 500, pan: 0})
    day.store(0.7, {})

    totals = _totals([leche, pan, leche])

    assert [(t["sucursal_sk"], t["cubiertos"], t["productos"], t["faltantes"], t["total"])
            for t in totals] == [(zero_priced_bread, 1, 2, [pan], D("500"))]


def test_each_item_keeps_its_price_date(day):
    (leche,) = day.products(1)
    day.store(0.5, {leche: 500})

    (store,) = _totals([leche])

    assert store["items"][0]["fecha_precio"] == "2026-03-17"
    assert float(store["distancia_km"]) == 0.5
