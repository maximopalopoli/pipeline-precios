"""
Integration tests for etl/transform/fact_builder.py.

Requires a live PostgreSQL instance via the pg_conn fixture (pytest-postgresql).
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from etl.transform.cleaner import ComercioRow, ProductoRow, SucursalRow
from etl.transform.fact_builder import _FACT_COLUMNS, build_fact_df

_SEEDS = Path(__file__).parent.parent.parent / "db" / "seeds" / "seed_provincias.sql"

RUN_DATE = date(2026, 3, 15)
RUN_ID = 1


@pytest.fixture(autouse=True)
def seed_provincias(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(_SEEDS.read_text())
    pg_conn.commit()


# ── Fixtures ───────────────────────────────────────────────────────────────────


def _comercio(id_comercio=1, id_bandera=2) -> ComercioRow:
    return ComercioRow(
        id_comercio=id_comercio, id_bandera=id_bandera,
        comercio_cuit="20123456789",
        comercio_razon_social="Supermercado SA",
        comercio_bandera_nombre="SuperBrand",
        comercio_bandera_url="http://example.com",
        comercio_ultima_actualizacion="2024-01-15",
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


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestBuildFactDf:
    def test_returns_correct_columns(self, pg_conn):
        result = build_fact_df(
            pg_conn,
            productos=[_producto()],
            sucursales=[_sucursal()],
            comercios=[_comercio()],
            run_date=RUN_DATE,
            run_id=RUN_ID,
        )
        assert list(result.df.columns) == _FACT_COLUMNS

    def test_single_row_happy_path(self, pg_conn):
        result = build_fact_df(
            pg_conn,
            productos=[_producto()],
            sucursales=[_sucursal()],
            comercios=[_comercio()],
            run_date=RUN_DATE,
            run_id=RUN_ID,
        )
        assert len(result.df) == 1
        assert len(result.rejected) == 0
        row = result.df.iloc[0]
        assert row["fecha_id"] == 20260315
        assert row["run_id"] == RUN_ID
        assert float(row["precio_lista"]) == pytest.approx(250.50)
        assert row["tiene_promo"] == False  # noqa: E712  (np.bool_ doesn't support `is`)

    def test_promo_prices_propagated(self, pg_conn):
        p = _producto(
            productos_precio_unitario_promo1=Decimal("200.00"),
            productos_leyenda_promo1="2x1",
            tiene_promo=True,
        )
        result = build_fact_df(
            pg_conn,
            productos=[p],
            sucursales=[_sucursal()],
            comercios=[_comercio()],
            run_date=RUN_DATE,
            run_id=RUN_ID,
        )
        row = result.df.iloc[0]
        assert float(row["precio_promo1"]) == pytest.approx(200.00)
        assert row["leyenda_promo1"] == "2x1"
        assert row["tiene_promo"] == True  # noqa: E712

    def test_null_promo_stored_as_none(self, pg_conn):
        result = build_fact_df(
            pg_conn,
            productos=[_producto()],
            sucursales=[_sucursal()],
            comercios=[_comercio()],
            run_date=RUN_DATE,
            run_id=RUN_ID,
        )
        row = result.df.iloc[0]
        assert row["precio_promo1"] is None
        assert row["precio_promo2"] is None

    def test_multiple_products_same_branch(self, pg_conn):
        products = [
            _producto(id_producto="7790001111111"),
            _producto(id_producto="7790002222222"),
            _producto(id_producto="7790003333333"),
        ]
        result = build_fact_df(
            pg_conn,
            productos=products,
            sucursales=[_sucursal()],
            comercios=[_comercio()],
            run_date=RUN_DATE,
            run_id=RUN_ID,
        )
        assert len(result.df) == 3
        # All rows share the same sucursal_sk and fecha_id
        assert result.df["sucursal_sk"].nunique() == 1
        assert result.df["fecha_id"].nunique() == 1

    def test_same_product_different_branches(self, pg_conn):
        result = build_fact_df(
            pg_conn,
            productos=[
                _producto(id_sucursal=10),
                _producto(id_sucursal=20),
            ],
            sucursales=[_sucursal(id_sucursal=10), _sucursal(id_sucursal=20)],
            comercios=[_comercio()],
            run_date=RUN_DATE,
            run_id=RUN_ID,
        )
        assert len(result.df) == 2
        assert result.df["sucursal_sk"].nunique() == 2

    def test_producto_deduplication_for_dim(self, pg_conn):
        """Same id_producto in two branches → one dim_producto row, two fact rows."""
        result = build_fact_df(
            pg_conn,
            productos=[
                _produto := _producto(id_producto="7790001234567", id_sucursal=10),
                _producto(id_producto="7790001234567", id_sucursal=20),
            ],
            sucursales=[_sucursal(id_sucursal=10), _sucursal(id_sucursal=20)],
            comercios=[_comercio()],
            run_date=RUN_DATE,
            run_id=RUN_ID,
        )
        assert len(result.df) == 2
        assert result.df["producto_sk"].nunique() == 1  # same product → same SK

    def test_missing_sucursal_produces_rejected_row(self, pg_conn):
        """A product whose sucursal wasn't loaded → ends up in rejected, not fact."""
        result = build_fact_df(
            pg_conn,
            productos=[_producto(id_sucursal=99)],  # 99 not in sucursales list
            sucursales=[_sucursal(id_sucursal=10)],
            comercios=[_comercio()],
            run_date=RUN_DATE,
            run_id=RUN_ID,
        )
        assert len(result.df) == 0
        assert len(result.rejected) == 1
        assert "sucursal_sk not found" in result.rejected[0]["reason"]

    def test_empty_inputs_returns_empty_df(self, pg_conn):
        result = build_fact_df(
            pg_conn,
            productos=[],
            sucursales=[],
            comercios=[],
            run_date=RUN_DATE,
            run_id=RUN_ID,
        )
        assert result.df.empty
        assert list(result.df.columns) == _FACT_COLUMNS

    def test_surrogate_keys_are_integers(self, pg_conn):
        result = build_fact_df(
            pg_conn,
            productos=[_producto()],
            sucursales=[_sucursal()],
            comercios=[_comercio()],
            run_date=RUN_DATE,
            run_id=RUN_ID,
        )
        row = result.df.iloc[0]
        import numbers
        assert isinstance(row["producto_sk"], numbers.Integral)
        assert isinstance(row["sucursal_sk"], numbers.Integral)
        assert isinstance(row["fecha_id"], numbers.Integral)
