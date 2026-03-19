"""
Integration tests for the v_variacion_precios view.

Three query patterns exercised:
  1. Top products with highest price variation %
  2. National average (avg across provinces) for a specific product
  3. Province-level comparison for a product on a given day
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

DATE_D1 = date(2026, 3, 14)
DATE_D2 = date(2026, 3, 15)
FECHA_D1 = 20260314
FECHA_D2 = 20260315


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def seed_metadata(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(_SEEDS.read_text())
        cur.execute(
            "INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)"
            " VALUES ('t.zip','http://t','x1',1,'2026-03-14') RETURNING id"
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


def _producto(id_producto="7790001234567", id_sucursal=10, precio=Decimal("100.00")) -> ProductoRow:
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
        productos_precio_unitario_promo1=None,
        productos_leyenda_promo1=None,
        productos_precio_unitario_promo2=None,
        productos_leyenda_promo2=None,
        tiene_promo=False,
    )


def _load_and_agg(pg_conn, run_id, productos, sucursales, run_date, fecha_id):
    comercios = [_comercio()]
    fact_df = build_fact_df(
        pg_conn, productos=productos, sucursales=sucursales, comercios=comercios,
        run_date=run_date, run_id=run_id,
    ).df
    warehouse_load(pg_conn, comercios=comercios, sucursales=sucursales,
                   productos=productos, fact_df=fact_df, run_id=run_id)
    aggregate(pg_conn, fecha_id)
    pg_conn.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestVVariacionPrecios:
    """
    All tests seed two days of data so LAG() has a predecessor to compute
    variacion_pct against.
    """

    def _seed_two_days(self, pg_conn, run_id,
                       precio_d1=Decimal("100.00"),
                       precio_d2=Decimal("120.00"),
                       sucursales=None,
                       id_producto="7790001234567"):
        suc = sucursales or [_sucursal(id_sucursal=10, provincia="AR-C")]
        _load_and_agg(
            pg_conn, run_id,
            [_producto(id_producto=id_producto, id_sucursal=suc[0].id_sucursal, precio=precio_d1)],
            suc, DATE_D1, FECHA_D1,
        )
        _load_and_agg(
            pg_conn, run_id,
            [_producto(id_producto=id_producto, id_sucursal=suc[0].id_sucursal, precio=precio_d2)],
            suc, DATE_D2, FECHA_D2,
        )

    # --- Query 1: top products by variation % ---

    def test_top_products_by_variation(self, pg_conn, run_id):
        """
        Product A: +20% (100→120), Product B: +50% (100→150).
        Top-1 by variacion_pct should be Product B.
        """
        suc_a = [_sucursal(id_sucursal=10, provincia="AR-C")]
        suc_b = [_sucursal(id_sucursal=20, provincia="AR-C")]

        _load_and_agg(
            pg_conn, run_id,
            [
                _producto("PROD_A", id_sucursal=10, precio=Decimal("100.00")),
                _producto("PROD_B", id_sucursal=20, precio=Decimal("100.00")),
            ],
            suc_a + suc_b, DATE_D1, FECHA_D1,
        )
        _load_and_agg(
            pg_conn, run_id,
            [
                _producto("PROD_A", id_sucursal=10, precio=Decimal("120.00")),
                _producto("PROD_B", id_sucursal=20, precio=Decimal("150.00")),
            ],
            suc_a + suc_b, DATE_D2, FECHA_D2,
        )

        with pg_conn.cursor() as cur:
            cur.execute("""
                SELECT id_producto, variacion_pct
                FROM v_variacion_precios
                WHERE fecha_id = %s
                  AND variacion_pct IS NOT NULL
                ORDER BY variacion_pct DESC
                LIMIT 1
            """, (FECHA_D2,))
            row = cur.fetchone()

        assert row is not None
        assert row[0] == "PROD_B"
        assert float(row[1]) == pytest.approx(50.0)

    def test_variacion_pct_calculated_correctly(self, pg_conn, run_id):
        """variacion_pct = (new - old) / old * 100."""
        self._seed_two_days(pg_conn, run_id,
                            precio_d1=Decimal("200.00"),
                            precio_d2=Decimal("250.00"))

        with pg_conn.cursor() as cur:
            cur.execute("""
                SELECT variacion_pct
                FROM v_variacion_precios
                WHERE fecha_id = %s AND variacion_pct IS NOT NULL
            """, (FECHA_D2,))
            row = cur.fetchone()

        assert row is not None
        assert float(row[0]) == pytest.approx(25.0)

    def test_first_day_has_null_variacion(self, pg_conn, run_id):
        """On the first day there is no prior row → variacion_pct must be NULL."""
        self._seed_two_days(pg_conn, run_id)

        with pg_conn.cursor() as cur:
            cur.execute("""
                SELECT variacion_pct
                FROM v_variacion_precios
                WHERE fecha_id = %s
            """, (FECHA_D1,))
            row = cur.fetchone()

        assert row is not None
        assert row[0] is None

    # --- Query 2: national average for a specific product ---

    def test_national_average_across_provinces(self, pg_conn, run_id):
        """
        Product in AR-C @ 100 and AR-B @ 200.
        National average (AVG across provinces) = 150.
        """
        suc_c = _sucursal(id_sucursal=10, provincia="AR-C")
        suc_b = _sucursal(id_sucursal=20, provincia="AR-B")

        _load_and_agg(
            pg_conn, run_id,
            [
                _producto("LECHE", id_sucursal=10, precio=Decimal("100.00")),
                _producto("LECHE", id_sucursal=20, precio=Decimal("200.00")),
            ],
            [suc_c, suc_b], DATE_D2, FECHA_D2,
        )

        with pg_conn.cursor() as cur:
            cur.execute("""
                SELECT ROUND(AVG(precio_lista_avg), 2) AS promedio_nacional
                FROM v_variacion_precios
                WHERE id_producto = 'LECHE'
                  AND fecha_id    = %s
            """, (FECHA_D2,))
            row = cur.fetchone()

        assert row is not None
        assert float(row[0]) == pytest.approx(150.0)

    # --- Query 3: province-level comparison ---

    def test_province_comparison(self, pg_conn, run_id):
        """
        Same product, two provinces on the same day → two rows with different
        precio_lista_avg values.
        """
        suc_c = _sucursal(id_sucursal=10, provincia="AR-C")
        suc_b = _sucursal(id_sucursal=20, provincia="AR-B")

        _load_and_agg(
            pg_conn, run_id,
            [
                _producto("LECHE", id_sucursal=10, precio=Decimal("90.00")),
                _producto("LECHE", id_sucursal=20, precio=Decimal("110.00")),
            ],
            [suc_c, suc_b], DATE_D2, FECHA_D2,
        )

        with pg_conn.cursor() as cur:
            cur.execute("""
                SELECT provincia, precio_lista_avg
                FROM v_variacion_precios
                WHERE id_producto = 'LECHE'
                  AND fecha_id    = %s
                ORDER BY precio_lista_avg
            """, (FECHA_D2,))
            rows = cur.fetchall()

        assert len(rows) == 2
        cheapest_prov, cheapest_price = rows[0]
        priciest_prov, priciest_price = rows[1]
        assert float(cheapest_price) == pytest.approx(90.0)
        assert float(priciest_price) == pytest.approx(110.0)
        # provinces must differ
        assert cheapest_prov != priciest_prov
