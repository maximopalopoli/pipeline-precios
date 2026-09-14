"""
Integration tests for etl/transform/dimension_resolver.py.

Requires a live PostgreSQL instance via the pg_conn fixture (pytest-postgresql).
All migrations are applied; dim_provincia is seeded before each test class.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from etl.transform.cleaner import ComercioRow, ProductoRow, SucursalRow
from etl.transform.dimension_resolver import (
    resolve_comercio,
    resolve_fecha,
    resolve_producto,
    resolve_provincia,
    resolve_sucursal,
)

_SEEDS = Path(__file__).parent.parent.parent / "db" / "seeds" / "seed_provincias.sql"


@pytest.fixture(autouse=True)
def seed_provincias(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(_SEEDS.read_text())
    pg_conn.commit()


# ── Helpers ───────────────────────────────────────────────────────────────────


def _comercio_row(**kwargs) -> ComercioRow:
    defaults = dict(
        id_comercio=1, id_bandera=2,
        comercio_cuit="20123456789",
        comercio_razon_social="Supermercado SA",
        comercio_bandera_nombre="SuperBrand",
        comercio_bandera_url="http://example.com",
        comercio_ultima_actualizacion="2024-01-15",
        comercio_version_sepa="1.0",
    )
    defaults.update(kwargs)
    return ComercioRow(**defaults)


def _sucursal_row(**kwargs) -> SucursalRow:
    defaults = dict(
        id_comercio=1, id_bandera=2, id_sucursal=10,
        sucursales_nombre="Sucursal Centro",
        sucursales_tipo="Supermercado",
        sucursales_calle="Av. Corrientes",
        sucursales_numero="1234",
        sucursales_latitud=Decimal("-34.603722"),
        sucursales_longitud=Decimal("-58.381592"),
        sucursales_observaciones=None,
        sucursales_barrio="San Nicolás",
        sucursales_codigo_postal="1043",
        sucursales_localidad="Buenos Aires",
        sucursales_provincia="AR-C",
        sucursales_lunes_horario_atencion=None,
        sucursales_martes_horario_atencion=None,
        sucursales_miercoles_horario_atencion=None,
        sucursales_jueves_horario_atencion=None,
        sucursales_viernes_horario_atencion=None,
        sucursales_sabado_horario_atencion=None,
        sucursales_domingo_horario_atencion=None,
    )
    defaults.update(kwargs)
    return SucursalRow(**defaults)


def _producto_row(**kwargs) -> ProductoRow:
    defaults = dict(
        id_comercio=1, id_bandera=2, id_sucursal=10,
        id_producto="7790001234567",
        es_ean=True,
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


# ── resolve_comercio ──────────────────────────────────────────────────────────


class TestResolveComercio:
    def test_insert_returns_sk(self, pg_conn):
        sk = resolve_comercio(pg_conn, _comercio_row())
        assert isinstance(sk, int)
        assert sk > 0

    def test_idempotent_same_sk(self, pg_conn):
        row = _comercio_row()
        sk1 = resolve_comercio(pg_conn, row)
        sk2 = resolve_comercio(pg_conn, row)
        assert sk1 == sk2

    def test_scd1_updates_attributes(self, pg_conn):
        row = _comercio_row()
        sk = resolve_comercio(pg_conn, row)

        updated = _comercio_row(comercio_razon_social="Nuevo Nombre SA")
        sk2 = resolve_comercio(pg_conn, updated)
        assert sk == sk2

        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT comercio_razon_social FROM dim_comercio WHERE comercio_sk = %s",
                (sk,),
            )
            (name,) = cur.fetchone()
        assert name == "Nuevo Nombre SA"

    def test_different_natural_key_different_sk(self, pg_conn):
        sk1 = resolve_comercio(pg_conn, _comercio_row(id_comercio=1, id_bandera=1))
        sk2 = resolve_comercio(pg_conn, _comercio_row(id_comercio=1, id_bandera=2))
        assert sk1 != sk2


# ── resolve_provincia ─────────────────────────────────────────────────────────


class TestResolveProvncia:
    def test_known_code_returns_sk(self, pg_conn):
        sk = resolve_provincia(pg_conn, "AR-C")
        assert isinstance(sk, int)
        assert sk > 0

    def test_unknown_code_returns_none(self, pg_conn):
        assert resolve_provincia(pg_conn, "AR-ZZ") is None

    def test_consistent_across_calls(self, pg_conn):
        sk1 = resolve_provincia(pg_conn, "AR-B")
        sk2 = resolve_provincia(pg_conn, "AR-B")
        assert sk1 == sk2

    def test_different_codes_different_sk(self, pg_conn):
        sk_b = resolve_provincia(pg_conn, "AR-B")
        sk_c = resolve_provincia(pg_conn, "AR-C")
        assert sk_b != sk_c


# ── resolve_sucursal ──────────────────────────────────────────────────────────


class TestResolveSucursal:
    def _setup_comercio(self, pg_conn):
        return resolve_comercio(pg_conn, _comercio_row())

    def test_insert_returns_sk(self, pg_conn):
        comercio_sk = self._setup_comercio(pg_conn)
        provincia_sk = resolve_provincia(pg_conn, "AR-C")
        sk = resolve_sucursal(pg_conn, _sucursal_row(), comercio_sk, provincia_sk)
        assert isinstance(sk, int)
        assert sk > 0

    def test_idempotent_same_sk(self, pg_conn):
        comercio_sk = self._setup_comercio(pg_conn)
        provincia_sk = resolve_provincia(pg_conn, "AR-C")
        row = _sucursal_row()
        sk1 = resolve_sucursal(pg_conn, row, comercio_sk, provincia_sk)
        sk2 = resolve_sucursal(pg_conn, row, comercio_sk, provincia_sk)
        assert sk1 == sk2

    def test_scd1_updates_lat_lon(self, pg_conn):
        comercio_sk = self._setup_comercio(pg_conn)
        provincia_sk = resolve_provincia(pg_conn, "AR-C")
        sk = resolve_sucursal(pg_conn, _sucursal_row(), comercio_sk, provincia_sk)

        new_lat = Decimal("-34.700000")
        updated = _sucursal_row(sucursales_latitud=new_lat)
        sk2 = resolve_sucursal(pg_conn, updated, comercio_sk, provincia_sk)
        assert sk == sk2

        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT sucursales_latitud FROM dim_sucursal WHERE sucursal_sk = %s",
                (sk,),
            )
            (lat,) = cur.fetchone()
        assert float(lat) == pytest.approx(float(new_lat))

    def test_null_provincia_accepted(self, pg_conn):
        comercio_sk = self._setup_comercio(pg_conn)
        sk = resolve_sucursal(pg_conn, _sucursal_row(), comercio_sk, None)
        assert sk > 0

    def test_different_id_sucursal_different_sk(self, pg_conn):
        comercio_sk = self._setup_comercio(pg_conn)
        sk1 = resolve_sucursal(pg_conn, _sucursal_row(id_sucursal=10), comercio_sk, None)
        sk2 = resolve_sucursal(pg_conn, _sucursal_row(id_sucursal=20), comercio_sk, None)
        assert sk1 != sk2


# ── resolve_producto ──────────────────────────────────────────────────────────


class TestResolveProducto:
    def test_insert_returns_sk(self, pg_conn):
        sk = resolve_producto(pg_conn, _producto_row())
        assert isinstance(sk, int)
        assert sk > 0

    def test_idempotent_same_sk(self, pg_conn):
        row = _producto_row()
        sk1 = resolve_producto(pg_conn, row)
        sk2 = resolve_producto(pg_conn, row)
        assert sk1 == sk2

    def test_scd1_updates_description(self, pg_conn):
        sk = resolve_producto(pg_conn, _producto_row())
        updated = _producto_row(productos_descripcion="Leche descremada 1L")
        sk2 = resolve_producto(pg_conn, updated)
        assert sk == sk2

        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT productos_descripcion FROM dim_producto WHERE producto_sk = %s",
                (sk,),
            )
            (desc,) = cur.fetchone()
        assert desc == "Leche descremada 1L"

    def test_different_id_producto_different_sk(self, pg_conn):
        sk1 = resolve_producto(pg_conn, _producto_row(id_producto="7790001234567"))
        sk2 = resolve_producto(pg_conn, _producto_row(id_producto="7790009999999"))
        assert sk1 != sk2

    def test_es_ean_stored(self, pg_conn):
        resolve_producto(pg_conn, _producto_row(es_ean=False))
        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT es_ean FROM dim_producto WHERE id_producto = %s",
                ("7790001234567",),
            )
            (es_ean,) = cur.fetchone()
        assert es_ean is False

    def test_valid_ean_is_shared_across_retailers(self, pg_conn):
        ean = "7791234567898"
        sk1 = resolve_producto(
            pg_conn,
            _producto_row(id_producto=ean, es_ean=True, id_comercio=1, id_bandera=1),
        )
        sk2 = resolve_producto(
            pg_conn,
            _producto_row(id_producto=ean, es_ean=True, id_comercio=2, id_bandera=1),
        )

        assert sk1 == sk2

    def test_internal_code_is_not_shared_across_retailers(self, pg_conn):
        code = "internal-milk-1l"
        sk1 = resolve_producto(
            pg_conn,
            _producto_row(id_producto=code, es_ean=False, id_comercio=1, id_bandera=1),
        )
        sk2 = resolve_producto(
            pg_conn,
            _producto_row(id_producto=code, es_ean=False, id_comercio=2, id_bandera=1),
        )

        assert sk1 != sk2

    def test_internal_code_is_shared_within_a_retailer(self, pg_conn):
        code = "internal-milk-1l"
        sk1 = resolve_producto(
            pg_conn,
            _producto_row(
                id_producto=code,
                es_ean=False,
                id_comercio=1,
                id_bandera=1,
                id_sucursal=10,
            ),
        )
        sk2 = resolve_producto(
            pg_conn,
            _producto_row(
                id_producto=code,
                es_ean=False,
                id_comercio=1,
                id_bandera=1,
                id_sucursal=20,
            ),
        )

        assert sk1 == sk2

    def test_invalid_ean_is_scoped_to_its_retailer(self, pg_conn):
        invalid_ean = "7791234567897"
        sk1 = resolve_producto(
            pg_conn,
            _producto_row(id_producto=invalid_ean, es_ean=True, id_comercio=1, id_bandera=1),
        )
        sk2 = resolve_producto(
            pg_conn,
            _producto_row(id_producto=invalid_ean, es_ean=True, id_comercio=2, id_bandera=1),
        )

        assert sk1 != sk2


# ── resolve_fecha ─────────────────────────────────────────────────────────────


class TestResolveFecha:
    def test_returns_yyyymmdd_int(self, pg_conn):
        fid = resolve_fecha(pg_conn, date(2024, 3, 15))
        assert fid == 20240315

    def test_idempotent(self, pg_conn):
        d = date(2024, 6, 1)
        fid1 = resolve_fecha(pg_conn, d)
        fid2 = resolve_fecha(pg_conn, d)
        assert fid1 == fid2

    def test_row_stored_in_dim_fecha(self, pg_conn):
        d = date(2024, 12, 25)
        resolve_fecha(pg_conn, d)
        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT anio, mes, dia_semana, es_fin_semana FROM dim_fecha WHERE fecha_id = %s",
                (20241225,),
            )
            row = cur.fetchone()
        assert row is not None
        anio, mes, dia_semana, es_fin_semana = row
        assert anio == 2024
        assert mes == 12
        assert dia_semana == 3   # 2024-12-25 is Wednesday
        assert es_fin_semana is False

    def test_weekend_flag(self, pg_conn):
        d = date(2024, 12, 28)  # Saturday
        resolve_fecha(pg_conn, d)
        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT es_fin_semana FROM dim_fecha WHERE fecha_id = %s",
                (20241228,),
            )
            (es_fin_semana,) = cur.fetchone()
        assert es_fin_semana is True

    def test_trimestre_computed(self, pg_conn):
        for month, expected_q in [(1, 1), (4, 2), (7, 3), (10, 4)]:
            d = date(2024, month, 1)
            resolve_fecha(pg_conn, d)
            with pg_conn.cursor() as cur:
                cur.execute(
                    "SELECT trimestre FROM dim_fecha WHERE fecha_id = %s",
                    (int(d.strftime("%Y%m%d")),),
                )
                (q,) = cur.fetchone()
            assert q == expected_q
