"""The location filter measures distance before limits and grows to reach chains."""

import asyncio
import re
from datetime import date

import pytest

from api.location import KM_PER_DEGREE_LAT, KM_PER_DEGREE_LON, Location
from api.routes import buscador, locales
from etl.load.warehouse_loader import _ensure_partition

FECHA_ID = 20260317
OBELISCO = (-34.6037, -58.3816)
FARMACITY = 24


class Day:
    """A published day with one product, priced in the stores added to it."""

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
                VALUES ('p.zip', 'https://example.test/p.zip', 'location', 1, '2026-03-17')
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
            cur.execute(
                """
                INSERT INTO dim_producto (producto_identity, id_producto)
                VALUES ('ean:7790000000011', '7790000000011') RETURNING producto_sk
                """
            )
            self.producto_sk = cur.fetchone()[0]
        _ensure_partition(conn, FECHA_ID)

    def store(
        self, comercio: int, km_north: float, price: float | None = 100, km_east: float = 0
    ) -> int:
        """A store that far from the Obelisco; ``price`` None leaves it unpriced."""
        self.stores += 1
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO dim_comercio (id_comercio, id_bandera, comercio_bandera_nombre)
                VALUES (%s, 1, %s)
                ON CONFLICT (id_comercio, id_bandera) DO UPDATE SET id_comercio = EXCLUDED.id_comercio
                RETURNING comercio_sk
                """,
                (comercio, f"Cadena {comercio}"),
            )
            comercio_sk = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO dim_sucursal (
                    id_comercio, id_bandera, id_sucursal, comercio_sk, provincia_sk,
                    sucursales_latitud, sucursales_longitud
                ) VALUES (%s, 1, %s, %s, %s, %s, %s) RETURNING sucursal_sk
                """,
                (
                    comercio, self.stores, comercio_sk, self.provincia_sk,
                    round(OBELISCO[0] + km_north / KM_PER_DEGREE_LAT, 6),
                    round(OBELISCO[1] + km_east / KM_PER_DEGREE_LON, 6),
                ),
            )
            sucursal_sk = cur.fetchone()[0]
            if price is not None:
                cur.execute(
                    """
                    INSERT INTO fact_precios (
                        producto_sk, sucursal_sk, fecha_id, run_id, precio_lista
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (self.producto_sk, sucursal_sk, FECHA_ID, self.run_id, price),
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

    monkeypatch.setattr(buscador, "fetch", fetch)
    monkeypatch.setattr(locales, "fetch", fetch)
    return Day(pg_conn)


def _radius(radius: float) -> dict:
    return asyncio.run(locales.get_radius(Location(*OBELISCO, radius)))


def test_a_nearby_store_is_listed_behind_many_cheaper_far_ones(day):
    # More stores than a product's store list holds, all cheaper, in the
    # corners of a 1 km square: 1.27 km away.
    for _ in range(205):
        day.store(10, 0.9, price=50, km_east=0.9)
    day.store(12, 0.5, price=900)
    nearby_id = day.stores

    offers = asyncio.run(
        buscador.get_buscador(
            producto_sk=day.producto_sk, fecha_id=FECHA_ID,
            provincia=None, localidad=None, cadenas=None,
            location=Location(*OBELISCO, 1),
        )
    )

    assert [o["id_sucursal"] for o in offers] == [nearby_id]


def test_distance_is_exact_not_a_square(day):
    # 0.9 km north and east is inside a 1 km square but 1.27 km away.
    day.store(12, 0.9, km_east=0.9)
    inside = day.store(15, 0.9)

    stores = asyncio.run(locales.get_locales(Location(*OBELISCO, 1), cadenas=None))

    assert [s["sucursal_sk"] for s in stores] == [inside]


def test_nearby_stores_come_nearest_first(day):
    far, near = day.store(10, 2), day.store(12, 0.5)

    stores = asyncio.run(locales.get_locales(Location(*OBELISCO, 5), cadenas=None))

    assert [(s["sucursal_sk"], float(s["distancia_km"])) for s in stores] == [
        (near, 0.5), (far, 2.0),
    ]


def test_the_radius_grows_until_three_chain_companies_are_near(day):
    day.store(10, 0.5)
    day.store(10, 0.8)  # A second Carrefour is not a second company.
    day.store(12, 1.5)
    day.store(15, 4)

    assert _radius(1) == {"radio_km": 5, "cadenas": 3, "ampliado": True, "suficiente": True}
    # A larger requested radius is kept, never shrunk.
    assert _radius(8) == {"radio_km": 8, "cadenas": 3, "ampliado": False, "suficiente": True}


def test_pharmacies_and_unpriced_stores_do_not_count_as_chains(day):
    day.store(FARMACITY, 0.2)
    day.store(9, 0.3, price=None)
    day.store(10, 0.5)
    day.store(12, 0.7)
    day.store(15, 1.8)

    assert _radius(1) == {"radio_km": 2, "cadenas": 3, "ampliado": True, "suficiente": True}


def test_a_sparse_area_reports_the_chains_the_largest_radius_reaches(day):
    day.store(10, 3)
    day.store(12, 60)

    assert _radius(1) == {"radio_km": 50, "cadenas": 1, "ampliado": True, "suficiente": False}
