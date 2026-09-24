"""
Integration tests for etl/load/warehouse_loader.py.

Requires a live PostgreSQL instance via the pg_conn fixture (pytest-postgresql).
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from etl.transform.cleaner import ComercioRow, ProductoRow, SucursalRow
from etl.transform.fact_builder import _FACT_COLUMNS, build_fact_df
from etl.load.warehouse_loader import (
    LoadResult,
    load,
    load_dimensions,
    load_dim_produto_from_staging,
    load_fact,
    load_pending_fact,
    register_rows_loaded,
)

_SEEDS = Path(__file__).parent.parent.parent / "db" / "seeds" / "seed_provincias.sql"

RUN_DATE = date(2026, 3, 15)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def seed_data(pg_conn):
    """Seed provinces and create a minimal ingestion_run row for FK integrity."""
    with pg_conn.cursor() as cur:
        cur.execute(_SEEDS.read_text())
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES ('test.zip', 'http://test', 'abc123', 1024, '2026-03-15')
            RETURNING id
            """
        )
        raw_file_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO ingestion_runs (raw_file_id, status)
            VALUES (%s, 'running')
            """,
            (raw_file_id,),
        )
    pg_conn.commit()


@pytest.fixture()
def run_id(pg_conn) -> int:
    with pg_conn.cursor() as cur:
        cur.execute("SELECT id FROM ingestion_runs LIMIT 1")
        return cur.fetchone()[0]


def _comercio(id_comercio=1, id_bandera=2) -> ComercioRow:
    return ComercioRow(
        id_comercio=id_comercio, id_bandera=id_bandera,
        comercio_cuit="20123456789",
        comercio_razon_social="Supermercado SA",
        comercio_bandera_nombre="SuperBrand",
        comercio_bandera_url="http://example.com",
        comercio_ultima_actualizacion="2026-01-15",
        comercio_version_sepa="1.0",
    )


def _sucursal(id_comercio=1, id_bandera=2, id_sucursal=10) -> SucursalRow:
    return SucursalRow(
        id_comercio=id_comercio, id_bandera=id_bandera, id_sucursal=id_sucursal,
        sucursales_nombre="Sucursal Centro",
        sucursales_tipo="Supermercado",
        sucursales_calle="Av. Corrientes", sucursales_numero="1234",
        sucursales_latitud=Decimal("-34.603722"),
        sucursales_longitud=Decimal("-58.381592"),
        sucursales_observaciones=None, sucursales_barrio="San Nicolás",
        sucursales_codigo_postal="1043", sucursales_localidad="Buenos Aires",
        sucursales_provincia="AR-C",
        sucursales_lunes_horario_atencion=None,
        sucursales_martes_horario_atencion=None,
        sucursales_miercoles_horario_atencion=None,
        sucursales_jueves_horario_atencion=None,
        sucursales_viernes_horario_atencion=None,
        sucursales_sabado_horario_atencion=None,
        sucursales_domingo_horario_atencion=None,
    )


def _producto(id_producto="7790001234567", id_sucursal=10, **kwargs) -> ProductoRow:
    defaults = dict(
        id_comercio=1, id_bandera=2, id_sucursal=id_sucursal,
        id_producto=id_producto, es_ean=True,
        productos_descripcion="Leche entera 1L",
        productos_cantidad_presentacion="1",
        productos_unidad_medida_presentacion="L",
        productos_marca="La Serenísima",
        productos_precio_lista=Decimal("250.50"),
        productos_precio_referencia=Decimal("240.00"),
        productos_cantidad_referencia="1",
        productos_unidad_medida_referencia="L",
        productos_precio_unitario_promo1=None,
        productos_leyenda_promo1=None,
        productos_precio_unitario_promo2=None,
        productos_leyenda_promo2=None,
        tiene_promo=False,
    )
    defaults.update(kwargs)
    return ProductoRow(**defaults)


def _load_dims_and_build_fact(pg_conn, run_id, run_date=RUN_DATE, p=None, s=None, c=None):
    """Helper: load dimensions, build and return fact DataFrame."""
    p = p or _producto()
    s = s or _sucursal()
    c = c or _comercio()
    load_dimensions(pg_conn, [c], [s], [p])
    pg_conn.commit()
    return build_fact_df(
        pg_conn,
        productos=[p],
        sucursales=[s],
        comercios=[c],
        run_date=run_date,
        run_id=run_id,
    ).df


# ---------------------------------------------------------------------------
# load_dimensions
# ---------------------------------------------------------------------------


class TestLoadDimensions:
    def test_inserts_comercio(self, pg_conn):
        rc, rs, rp = load_dimensions(pg_conn, [_comercio()], [], [])
        assert rc == 1
        with pg_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM dim_comercio")
            assert cur.fetchone()[0] == 1

    def test_inserts_producto(self, pg_conn):
        rc, rs, rp = load_dimensions(pg_conn, [], [], [_producto()])
        assert rp == 1
        with pg_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM dim_producto")
            assert cur.fetchone()[0] == 1

    def test_inserts_sucursal_with_provincia_sk(self, pg_conn):
        load_dimensions(pg_conn, [_comercio()], [_sucursal()], [])
        with pg_conn.cursor() as cur:
            cur.execute("SELECT provincia_sk FROM dim_sucursal")
            row = cur.fetchone()
        assert row is not None
        assert row[0] is not None  # provincia_sk resolved via JOIN dim_provincia

    def test_deduplicates_comercios(self, pg_conn):
        """Same natural key twice → one row in dim_comercio."""
        rc, _, _ = load_dimensions(pg_conn, [_comercio(), _comercio()], [], [])
        assert rc == 1

    def test_deduplicates_productos(self, pg_conn):
        same = [_producto("7790001111111"), _producto("7790001111111")]
        _, _, rp = load_dimensions(pg_conn, [], [], same)
        assert rp == 1

    def test_scopes_internal_product_codes_to_the_retailer(self, pg_conn):
        code = "internal-milk-1l"
        products = [
            _producto(code, es_ean=False, id_comercio=1, id_bandera=1),
            _producto(code, es_ean=False, id_comercio=2, id_bandera=1),
        ]

        _, _, rows = load_dimensions(pg_conn, [], [], products)

        assert rows == 2
        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT producto_identity FROM dim_producto ORDER BY producto_identity"
            )
            assert [row[0] for row in cur.fetchall()] == [
                "chain:1:1:internal-milk-1l",
                "chain:2:1:internal-milk-1l",
            ]

    def test_upsert_updates_comercio_attributes(self, pg_conn):
        """Second load with updated razon_social overwrites (SCD Type 1)."""
        load_dimensions(pg_conn, [_comercio()], [], [])
        pg_conn.commit()

        updated = _comercio()
        updated.comercio_razon_social = "Nuevo Nombre SA"
        load_dimensions(pg_conn, [updated], [], [])
        pg_conn.commit()

        with pg_conn.cursor() as cur:
            cur.execute("SELECT comercio_razon_social FROM dim_comercio")
            assert cur.fetchone()[0] == "Nuevo Nombre SA"

    def test_upsert_updates_producto_attributes(self, pg_conn):
        load_dimensions(pg_conn, [], [], [_producto()])
        pg_conn.commit()

        updated = _producto()
        updated.productos_descripcion = "Leche descremada 1L"
        load_dimensions(pg_conn, [], [], [updated])
        pg_conn.commit()

        with pg_conn.cursor() as cur:
            cur.execute("SELECT productos_descripcion FROM dim_producto")
            assert cur.fetchone()[0] == "Leche descremada 1L"

    def test_sucursal_unknown_provincia_gets_null_sk(self, pg_conn):
        """provincia ISO code not in dim_provincia → provincia_sk = NULL."""
        load_dimensions(pg_conn, [_comercio()], [], [])
        pg_conn.commit()

        s = _sucursal()
        s.sucursales_provincia = "AR-ZZ"  # not seeded
        load_dimensions(pg_conn, [], [s], [])
        with pg_conn.cursor() as cur:
            cur.execute("SELECT provincia_sk FROM dim_sucursal")
            assert cur.fetchone()[0] is None

    def test_empty_inputs_are_noop(self, pg_conn):
        rc, rs, rp = load_dimensions(pg_conn, [], [], [])
        assert (rc, rs, rp) == (0, 0, 0)


class TestLoadDimensionProductoFromStaging:
    def test_scopes_internal_codes_and_shares_valid_eans(self, pg_conn, run_id):
        with pg_conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO stg_productos (
                    run_id, id_comercio, id_bandera, id_sucursal, id_producto,
                    productos_ean, productos_descripcion
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (run_id, "1", "1", "10", "internal-milk-1l", "0", "Leche A"),
                    (run_id, "2", "1", "20", "internal-milk-1l", "0", "Leche B"),
                    (run_id, "1", "1", "10", "7791234567898", "1", "Leche EAN"),
                    (run_id, "2", "1", "20", "7791234567898", "1", "Leche EAN"),
                    (run_id, "invalid", "1", "30", "ignored-product", "0", "Inválido"),
                ],
            )

        rows = load_dim_produto_from_staging(pg_conn, run_id)

        assert rows == 3
        with pg_conn.cursor() as cur:
            cur.execute(
                """
                SELECT producto_identity, source_id_comercio, source_id_bandera
                FROM dim_producto
                ORDER BY producto_identity
                """
            )
            assert cur.fetchall() == [
                ("chain:1:1:internal-milk-1l", 1, 1),
                ("chain:2:1:internal-milk-1l", 2, 1),
                ("ean:7791234567898", None, None),
            ]


# ---------------------------------------------------------------------------
# load_fact
# ---------------------------------------------------------------------------


class TestLoadFact:
    def test_loads_rows_into_fact_precios(self, pg_conn, run_id):
        df = _load_dims_and_build_fact(pg_conn, run_id)
        count = load_fact(pg_conn, df, run_id)
        pg_conn.commit()

        assert count == 1
        with pg_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM fact_precios")
            assert cur.fetchone()[0] == 1

    def test_prices_persisted_correctly(self, pg_conn, run_id):
        df = _load_dims_and_build_fact(pg_conn, run_id)
        load_fact(pg_conn, df, run_id)
        pg_conn.commit()

        with pg_conn.cursor() as cur:
            cur.execute("SELECT precio_lista, precio_referencia FROM fact_precios")
            row = cur.fetchone()
        assert float(row[0]) == pytest.approx(250.50)
        assert float(row[1]) == pytest.approx(240.00)

    def test_upsert_updates_existing_row(self, pg_conn, run_id):
        """Same grain on second load → price updated, no duplicate row."""
        df = _load_dims_and_build_fact(pg_conn, run_id)
        load_fact(pg_conn, df, run_id)
        pg_conn.commit()

        df2 = df.copy()
        df2["precio_lista"] = 999.00
        load_fact(pg_conn, df2, run_id)
        pg_conn.commit()

        with pg_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*), MAX(precio_lista) FROM fact_precios")
            count, max_price = cur.fetchone()
        assert count == 1
        assert float(max_price) == pytest.approx(999.00)

    def test_empty_dataframe_returns_zero(self, pg_conn, run_id):
        empty = pd.DataFrame(columns=_FACT_COLUMNS)
        assert load_fact(pg_conn, empty, run_id) == 0

    def test_auto_creates_partition_for_new_month(self, pg_conn, run_id):
        """A fecha_id outside the pre-seeded 202601–202606 range triggers partition creation."""
        future_date = date(2027, 1, 15)  # no partition for 202701 pre-seeded
        p = _producto()
        s = _sucursal()
        c = _comercio()
        df = _load_dims_and_build_fact(pg_conn, run_id, run_date=future_date, p=p, s=s, c=c)
        count = load_fact(pg_conn, df, run_id)
        pg_conn.commit()

        assert count == 1
        with pg_conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relname = 'fact_precios_20270115' AND n.nspname = 'public'
                """
            )
            assert cur.fetchone() is not None


class TestLoadPendingFact:
    def test_loads_rows_without_publishing_them(self, pg_conn, run_id):
        df = _load_dims_and_build_fact(pg_conn, run_id)

        count = load_pending_fact(pg_conn, df, run_id)
        pg_conn.commit()

        assert count == 1
        with pg_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM pending_fact_precios WHERE run_id = %s", (run_id,))
            assert cur.fetchone()[0] == 1
            cur.execute("SELECT COUNT(*) FROM fact_precios")
            assert cur.fetchone()[0] == 0

    def test_upsert_is_scoped_to_the_ingestion_run(self, pg_conn, run_id):
        df = _load_dims_and_build_fact(pg_conn, run_id)
        load_pending_fact(pg_conn, df, run_id)
        pg_conn.commit()

        updated = df.copy()
        updated["precio_lista"] = 999.00
        load_pending_fact(pg_conn, updated, run_id)
        pg_conn.commit()

        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*), MAX(precio_lista) FROM pending_fact_precios WHERE run_id = %s",
                (run_id,),
            )
            count, price = cur.fetchone()
            cur.execute(
                "SELECT COUNT(*), MAX(precio_lista) FROM tmp_pending_fact_precios"
            )
            temp_count, temp_price = cur.fetchone()
        assert count == 1
        assert float(price) == pytest.approx(999.0)
        assert temp_count == 1
        assert float(temp_price) == pytest.approx(999.0)


# ---------------------------------------------------------------------------
# register_rows_loaded
# ---------------------------------------------------------------------------


class TestRegisterRowsLoaded:
    def test_updates_ingestion_runs(self, pg_conn, run_id):
        register_rows_loaded(pg_conn, run_id, 42)
        pg_conn.commit()

        with pg_conn.cursor() as cur:
            cur.execute("SELECT rows_loaded FROM ingestion_runs WHERE id = %s", (run_id,))
            assert cur.fetchone()[0] == 42

    def test_overwrite_on_second_call(self, pg_conn, run_id):
        register_rows_loaded(pg_conn, run_id, 10)
        pg_conn.commit()
        register_rows_loaded(pg_conn, run_id, 99)
        pg_conn.commit()

        with pg_conn.cursor() as cur:
            cur.execute("SELECT rows_loaded FROM ingestion_runs WHERE id = %s", (run_id,))
            assert cur.fetchone()[0] == 99


# ---------------------------------------------------------------------------
# load (high-level entry point)
# ---------------------------------------------------------------------------


class TestLoad:
    def test_returns_load_result(self, pg_conn, run_id):
        p = _producto()
        s = _sucursal()
        c = _comercio()
        fact_df = build_fact_df(
            pg_conn,
            productos=[p], sucursales=[s], comercios=[c],
            run_date=RUN_DATE, run_id=run_id,
        ).df

        result = load(pg_conn, comercios=[c], sucursales=[s], productos=[p],
                      fact_df=fact_df, run_id=run_id)
        pg_conn.commit()

        assert isinstance(result, LoadResult)
        assert result.rows_comercios == 1
        assert result.rows_sucursales == 1
        assert result.rows_productos == 1
        assert result.rows_fact == 1

    def test_rows_loaded_persisted_after_load(self, pg_conn, run_id):
        p = _producto()
        s = _sucursal()
        c = _comercio()
        fact_df = build_fact_df(
            pg_conn,
            productos=[p], sucursales=[s], comercios=[c],
            run_date=RUN_DATE, run_id=run_id,
        ).df

        load(pg_conn, comercios=[c], sucursales=[s], productos=[p],
             fact_df=fact_df, run_id=run_id)
        pg_conn.commit()

        with pg_conn.cursor() as cur:
            cur.execute("SELECT rows_loaded FROM ingestion_runs WHERE id = %s", (run_id,))
            assert cur.fetchone()[0] == 1
