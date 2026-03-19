"""
Integration test: second pipeline run for the same day (post-rectification).

Scenario
--------
The SEPA source occasionally publishes a corrected file for a date that was
already processed.  When the pipeline re-runs for that same fecha_id it must:

1. Upsert fact_precios — existing rows are overwritten with the rectified
   prices (ON CONFLICT DO UPDATE).
2. Re-compute agg_precios_diarios for that fecha_id — the stale aggregates
   are replaced by fresh values derived from the rectified facts.

This test exercises warehouse_load + aggregate called twice for the same
fecha_id with different price data and asserts that the final state of the
agg table reflects the second (rectified) run, not the first.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from etl.transform.cleaner import ComercioRow, ProductoRow, SucursalRow
from etl.transform.fact_builder import build_fact_df
from etl.load.warehouse_loader import load as warehouse_load
from etl.load.aggregator import aggregate

_SEEDS = Path(__file__).parent.parent.parent / "db" / "seeds" / "seed_provincias.sql"

RUN_DATE = date(2026, 3, 15)
FECHA_ID = 20260315


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def seed_metadata(pg_conn):
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


def _producto(id_producto="AAA", id_sucursal=10, precio=Decimal("100.00"),
              promo=None) -> ProductoRow:
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


def _run_once(pg_conn, run_id, productos, sucursales=None):
    """Simulate one pipeline execution: warehouse load + aggregation."""
    c = [_comercio()]
    s = sucursales or [_sucursal()]
    fact_df = build_fact_df(
        pg_conn, productos=productos, sucursales=s, comercios=c,
        run_date=RUN_DATE, run_id=run_id,
    ).df
    warehouse_load(pg_conn, comercios=c, sucursales=s, productos=productos,
                   fact_df=fact_df, run_id=run_id)
    agg = aggregate(pg_conn, FECHA_ID)
    pg_conn.commit()
    return agg


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPipelineRerun:

    def test_second_run_overwrites_aggregates(self, pg_conn, run_id):
        """
        First run at price=100, second run (rectification) at price=200.
        After the second run, agg must show avg=200, not the stale 100.
        """
        _run_once(pg_conn, run_id, [_produto := _producto(precio=Decimal("100.00"))])

        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT precio_lista_avg FROM agg_precios_diarios WHERE fecha_id=%s",
                (FECHA_ID,),
            )
            assert float(cur.fetchone()[0]) == pytest.approx(100.0)

        # Second run with rectified price
        _run_once(pg_conn, run_id, [_producto(precio=Decimal("200.00"))])

        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT precio_lista_avg FROM agg_precios_diarios WHERE fecha_id=%s",
                (FECHA_ID,),
            )
            assert float(cur.fetchone()[0]) == pytest.approx(200.0)

    def test_second_run_no_duplicate_agg_rows(self, pg_conn, run_id):
        """Re-running must not duplicate rows in agg_precios_diarios."""
        _run_once(pg_conn, run_id, [_producto(precio=Decimal("100.00"))])
        _run_once(pg_conn, run_id, [_producto(precio=Decimal("150.00"))])

        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM agg_precios_diarios WHERE fecha_id=%s",
                (FECHA_ID,),
            )
            assert cur.fetchone()[0] == 1

    def test_second_run_updates_promo_stats(self, pg_conn, run_id):
        """
        First run: no promo.  Second run: promo added (rectification).
        cantidad_con_promo must reflect the second run.
        """
        suc = [_sucursal(id_sucursal=10)]

        _run_once(pg_conn, run_id,
                  [_producto(id_sucursal=10, precio=Decimal("100.00"), promo=None)],
                  sucursales=suc)

        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT cantidad_con_promo FROM agg_precios_diarios WHERE fecha_id=%s",
                (FECHA_ID,),
            )
            assert cur.fetchone()[0] == 0

        # Second run adds a promo
        _run_once(pg_conn, run_id,
                  [_producto(id_sucursal=10, precio=Decimal("100.00"), promo=Decimal("80.00"))],
                  sucursales=suc)

        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT cantidad_con_promo, precio_promo_avg"
                " FROM agg_precios_diarios WHERE fecha_id=%s",
                (FECHA_ID,),
            )
            row = cur.fetchone()
        assert row[0] == 1
        assert float(row[1]) == pytest.approx(80.0)

    def test_second_run_with_extra_sucursal(self, pg_conn, run_id):
        """
        Rectified file adds a new branch.  Aggregates must reflect the
        combined data, and the row count in agg must stay at 1 (same grain).
        """
        suc10 = _sucursal(id_sucursal=10, provincia="AR-C")
        suc20 = _sucursal(id_sucursal=20, provincia="AR-C")

        # First run: only branch 10
        _run_once(pg_conn, run_id,
                  [_producto(id_sucursal=10, precio=Decimal("100.00"))],
                  sucursales=[suc10])

        # Second run: branch 10 rectified + new branch 20
        _run_once(pg_conn, run_id,
                  [
                      _producto(id_sucursal=10, precio=Decimal("120.00")),
                      _producto(id_sucursal=20, precio=Decimal("180.00")),
                  ],
                  sucursales=[suc10, suc20])

        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT cantidad_obs, precio_lista_avg, precio_lista_min, precio_lista_max"
                " FROM agg_precios_diarios WHERE fecha_id=%s",
                (FECHA_ID,),
            )
            row = cur.fetchone()

        assert row[0] == 2                           # two observations
        assert float(row[1]) == pytest.approx(150.0) # avg(120, 180)
        assert float(row[2]) == pytest.approx(120.0) # min
        assert float(row[3]) == pytest.approx(180.0) # max
