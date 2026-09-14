"""
Integration tests for etl/load/aggregator.py.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from etl.transform.cleaner import ComercioRow, ProductoRow, SucursalRow
from etl.transform.fact_builder import build_fact_df
from etl.load.warehouse_loader import load as warehouse_load
from etl.load.aggregator import AggResult, aggregate

_SEEDS = Path(__file__).parent.parent.parent / "db" / "seeds" / "seed_provincias.sql"

RUN_DATE = date(2026, 3, 15)
FECHA_ID = 20260315


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def seed_data(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(_SEEDS.read_text())
        cur.execute(
            "INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)"
            " VALUES ('t.zip','http://t','x1',1,'2026-03-15') RETURNING id"
        )
        raw_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO ingestion_runs (raw_file_id, status) VALUES (%s,'running')",
            (raw_id,),
        )
    pg_conn.commit()


@pytest.fixture()
def run_id(pg_conn) -> int:
    with pg_conn.cursor() as cur:
        cur.execute("SELECT id FROM ingestion_runs LIMIT 1")
        return cur.fetchone()[0]


def _comercio() -> ComercioRow:
    return ComercioRow(
        id_comercio=1, id_bandera=2,
        comercio_cuit="20123456789",
        comercio_razon_social="Super SA",
        comercio_bandera_nombre="Brand",
        comercio_bandera_url="http://x.com",
        comercio_ultima_actualizacion=None,
        comercio_version_sepa="1.0",
    )


def _sucursal(id_sucursal=10, provincia="AR-C") -> SucursalRow:
    return SucursalRow(
        id_comercio=1, id_bandera=2, id_sucursal=id_sucursal,
        sucursales_nombre="Centro", sucursales_tipo="Supermercado",
        sucursales_calle=None, sucursales_numero=None,
        sucursales_latitud=Decimal("-34.6"), sucursales_longitud=Decimal("-58.4"),
        sucursales_observaciones=None, sucursales_barrio=None,
        sucursales_codigo_postal=None, sucursales_localidad="CABA",
        sucursales_provincia=provincia,
        sucursales_lunes_horario_atencion=None,
        sucursales_martes_horario_atencion=None,
        sucursales_miercoles_horario_atencion=None,
        sucursales_jueves_horario_atencion=None,
        sucursales_viernes_horario_atencion=None,
        sucursales_sabado_horario_atencion=None,
        sucursales_domingo_horario_atencion=None,
    )


def _producto(id_producto="7790001234567", id_sucursal=10, precio=Decimal("100.00"), promo=None) -> ProductoRow:
    return ProductoRow(
        id_comercio=1, id_bandera=2, id_sucursal=id_sucursal,
        id_producto=id_producto, es_ean=True,
        productos_descripcion="Leche 1L", productos_marca="Marca",
        productos_cantidad_presentacion="1",
        productos_unidad_medida_presentacion="L",
        productos_precio_lista=precio,
        productos_precio_referencia=precio,
        productos_cantidad_referencia="1",
        productos_unidad_medida_referencia="L",
        productos_precio_unitario_promo1=promo,
        productos_leyenda_promo1="2x1" if promo else None,
        productos_precio_unitario_promo2=None,
        productos_leyenda_promo2=None,
        tiene_promo=promo is not None,
    )


def _load_all(pg_conn, run_id, productos, sucursales=None, comercios=None):
    c = comercios or [_comercio()]
    s = sucursales or [_sucursal()]
    fact_df = build_fact_df(
        pg_conn, productos=productos, sucursales=s, comercios=c,
        run_date=RUN_DATE, run_id=run_id,
    ).df
    warehouse_load(pg_conn, comercios=c, sucursales=s, productos=productos,
                   fact_df=fact_df, run_id=run_id)
    pg_conn.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAggregate:
    def test_returns_agg_result(self, pg_conn, run_id):
        _load_all(pg_conn, run_id, [_producto()])
        result = aggregate(pg_conn, FECHA_ID)
        assert isinstance(result, AggResult)
        assert result.fecha_id == FECHA_ID

    def test_inserts_one_row_per_product_province(self, pg_conn, run_id):
        _load_all(pg_conn, run_id, [_produto := _producto()])
        aggregate(pg_conn, FECHA_ID)
        pg_conn.commit()

        with pg_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM agg_precios_diarios WHERE fecha_id = %s", (FECHA_ID,))
            assert cur.fetchone()[0] == 1

    def test_avg_min_max_correct(self, pg_conn, run_id):
        """Three branches in same province → avg/min/max computed correctly."""
        prods = [
            _producto(id_producto="AAA", id_sucursal=10, precio=Decimal("100.00")),
            _producto(id_producto="AAA", id_sucursal=20, precio=Decimal("200.00")),
            _producto(id_producto="AAA", id_sucursal=30, precio=Decimal("300.00")),
        ]
        suc = [_sucursal(id_sucursal=10), _sucursal(id_sucursal=20), _sucursal(id_sucursal=30)]
        _load_all(pg_conn, run_id, prods, sucursales=suc)
        aggregate(pg_conn, FECHA_ID)
        pg_conn.commit()

        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT precio_lista_avg, precio_lista_min, precio_lista_max, cantidad_obs"
                " FROM agg_precios_diarios WHERE fecha_id = %s",
                (FECHA_ID,),
            )
            row = cur.fetchone()
        assert float(row[0]) == pytest.approx(200.0)
        assert float(row[1]) == pytest.approx(100.0)
        assert float(row[2]) == pytest.approx(300.0)
        assert row[3] == 3

    def test_median_correct(self, pg_conn, run_id):
        prods = [
            _producto(id_producto="AAA", id_sucursal=10, precio=Decimal("100.00")),
            _producto(id_producto="AAA", id_sucursal=20, precio=Decimal("200.00")),
            _producto(id_producto="AAA", id_sucursal=30, precio=Decimal("300.00")),
        ]
        suc = [_sucursal(id_sucursal=10), _sucursal(id_sucursal=20), _sucursal(id_sucursal=30)]
        _load_all(pg_conn, run_id, prods, sucursales=suc)
        aggregate(pg_conn, FECHA_ID)
        pg_conn.commit()

        with pg_conn.cursor() as cur:
            cur.execute("SELECT precio_lista_median FROM agg_precios_diarios WHERE fecha_id = %s", (FECHA_ID,))
            assert float(cur.fetchone()[0]) == pytest.approx(200.0)

    def test_promo_count_and_avg(self, pg_conn, run_id):
        prods = [
            _producto(id_producto="AAA", id_sucursal=10, precio=Decimal("100.00"), promo=Decimal("80.00")),
            _producto(id_producto="AAA", id_sucursal=20, precio=Decimal("100.00")),
        ]
        suc = [_sucursal(id_sucursal=10), _sucursal(id_sucursal=20)]
        _load_all(pg_conn, run_id, prods, sucursales=suc)
        aggregate(pg_conn, FECHA_ID)
        pg_conn.commit()

        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT cantidad_con_promo, precio_promo_avg FROM agg_precios_diarios WHERE fecha_id = %s",
                (FECHA_ID,),
            )
            row = cur.fetchone()
        assert row[0] == 1
        # promo_avg = LEAST(80, 100) + LEAST(100, 100) / 2 = (80 + 100) / 2 = 90
        assert float(row[1]) == pytest.approx(90.0)

    def test_two_provinces_produce_two_rows(self, pg_conn, run_id):
        prods = [
            _producto(id_producto="AAA", id_sucursal=10),
            _producto(id_producto="AAA", id_sucursal=20),
        ]
        suc = [_sucursal(id_sucursal=10, provincia="AR-C"), _sucursal(id_sucursal=20, provincia="AR-B")]
        _load_all(pg_conn, run_id, prods, sucursales=suc)
        result = aggregate(pg_conn, FECHA_ID)
        pg_conn.commit()

        assert result.rows_upserted == 2
        with pg_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM agg_precios_diarios WHERE fecha_id = %s", (FECHA_ID,))
            assert cur.fetchone()[0] == 2

    def test_idempotent_replacement(self, pg_conn, run_id):
        """Running aggregate twice → same rows, updated values."""
        _load_all(pg_conn, run_id, [_producto(precio=Decimal("100.00"))])
        aggregate(pg_conn, FECHA_ID)
        pg_conn.commit()

        # Simulate re-run: reload fact with new price, re-aggregate
        _load_all(pg_conn, run_id, [_producto(precio=Decimal("200.00"))])
        aggregate(pg_conn, FECHA_ID)
        pg_conn.commit()

        with pg_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*), MAX(precio_lista_avg) FROM agg_precios_diarios WHERE fecha_id = %s", (FECHA_ID,))
            count, avg = cur.fetchone()
        assert count == 1
        assert float(avg) == pytest.approx(200.0)

    def test_removes_aggregate_groups_absent_from_a_rectification(self, pg_conn, run_id):
        productos = [
            _producto(id_producto="AAA", id_sucursal=10, precio=Decimal("100.00")),
            _producto(id_producto="BBB", id_sucursal=20, precio=Decimal("200.00")),
        ]
        sucursales = [_sucursal(id_sucursal=10), _sucursal(id_sucursal=20)]
        _load_all(pg_conn, run_id, productos, sucursales=sucursales)
        aggregate(pg_conn, FECHA_ID)
        pg_conn.commit()

        with pg_conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM fact_precios
                WHERE fecha_id = %s
                  AND producto_sk = (
                      SELECT producto_sk FROM dim_producto WHERE id_producto = 'BBB'
                  )
                """,
                (FECHA_ID,),
            )
        aggregate(pg_conn, FECHA_ID)
        pg_conn.commit()

        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM agg_precios_diarios WHERE fecha_id = %s",
                (FECHA_ID,),
            )
            assert cur.fetchone()[0] == 1
            cur.execute(
                """
                SELECT p.id_producto
                FROM agg_precios_diarios a
                JOIN dim_producto p ON p.producto_sk = a.producto_sk
                WHERE a.fecha_id = %s
                """,
                (FECHA_ID,),
            )
            assert cur.fetchone()[0] == "AAA"

    def test_no_rows_for_other_fecha(self, pg_conn, run_id):
        _load_all(pg_conn, run_id, [_producto()])
        aggregate(pg_conn, FECHA_ID)
        pg_conn.commit()

        with pg_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM agg_precios_diarios WHERE fecha_id = 20260316")
            assert cur.fetchone()[0] == 0

    def test_sucursal_without_provincia_excluded(self, pg_conn, run_id):
        """Sucursales con provincia_sk NULL no deben aparecer en los agregados."""
        from etl.load.warehouse_loader import load_dimensions
        c = _comercio()
        s = _sucursal()
        p = _producto()

        # Load sucursal with invalid provincia so provincia_sk = NULL
        s.sucursales_provincia = "AR-ZZ"
        load_dimensions(pg_conn, [c], [s], [p])
        pg_conn.commit()

        fact_df = build_fact_df(
            pg_conn, productos=[p], sucursales=[s], comercios=[c],
            run_date=RUN_DATE, run_id=run_id,
        ).df
        from etl.load.warehouse_loader import load_fact
        load_fact(pg_conn, fact_df, run_id)
        pg_conn.commit()

        result = aggregate(pg_conn, FECHA_ID)
        pg_conn.commit()

        assert result.rows_upserted == 0
